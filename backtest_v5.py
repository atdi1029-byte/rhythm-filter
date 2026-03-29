"""Backtest: Rhythm Filter v5 SHORT signals on BTC.

Replicates the exact v5 Pine Script signal logic:
  1. wasGreen: breathScore > 0
  2. Arm: breathScore < -5 and wasGreen
  3. Fire: curl-up (score > prev and prev <= prev2) while score < 0
  4. wasGreen resets on fire (one signal per red zone)
  5. Cooldown: 12 bars

Tests a grid of SL/TP combos to find optimal risk management.
"""

import json
import os
from datetime import datetime

import config

# === COIN LIST (matches Pine Script v5) ===
COINS = [
    "btcusdt", "ethusdt", "xrpusdt", "bnbusdt", "solusdt",
    "dogeusdt", "adausdt", "trxusdt", "avaxusdt", "shibusdt",
    "tonusdt", "linkusdt", "suiusdt", "dotusdt", "nearusdt",
    "uniusdt", "aptusdt", "maticusdt", "arbusdt", "opusdt",
    "icpusdt", "hbarusdt", "filusdt", "atomusdt", "imxusdt",
    "injusdt", "stxusdt", "ftmusdt", "grtusdt", "thetausdt",
    "algousdt", "ldousdt", "aaveusdt", "mkrusdt", "snxusdt",
    "vetusdt", "xlmusdt", "pepeusdt", "fetusdt", "eosusdt",
]

# === SETTINGS (match Pine Script v5) ===
RSI_LEN = 14
BUY_ZONE = 40.0
SELL_ZONE = 60.0
EMA_SMOOTH = 9
TOTAL_COINS = 40
SHORT_THRESHOLD = -5.0
SHORT_COOLDOWN = 12

# === SL/TP GRID TO TEST ===
SL_LEVELS = [1, 2, 3, 4, 5, 7, 10]          # stop loss %
TP_LEVELS = [1, 2, 3, 4, 5, 7, 10]          # take profit %
MAX_HOLD_BARS = 84  # 14 days max hold


def load_candles(symbol):
    filepath = os.path.join(config.DATA_DIR, f"{symbol}_4h.json")
    if not os.path.exists(filepath):
        return None
    with open(filepath) as f:
        return json.load(f)


def compute_rsi(closes, length=14):
    rsi = [None] * len(closes)
    if len(closes) < length + 1:
        return rsi

    gains = []
    losses = []
    for i in range(1, length + 1):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))

    avg_gain = sum(gains) / length
    avg_loss = sum(losses) / length

    if avg_loss == 0:
        rsi[length] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi[length] = 100.0 - (100.0 / (1.0 + rs))

    for i in range(length + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gain = max(delta, 0)
        loss = max(-delta, 0)
        avg_gain = (avg_gain * (length - 1) + gain) / length
        avg_loss = (avg_loss * (length - 1) + loss) / length
        if avg_loss == 0:
            rsi[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i] = 100.0 - (100.0 / (1.0 + rs))

    return rsi


def compute_ema(values, length):
    ema = [None] * len(values)
    multiplier = 2.0 / (length + 1)

    start = None
    for i, v in enumerate(values):
        if v is not None:
            start = i
            ema[i] = v
            break

    if start is None:
        return ema

    for i in range(start + 1, len(values)):
        if values[i] is not None and ema[i - 1] is not None:
            ema[i] = values[i] * multiplier + ema[i - 1] * (1 - multiplier)
        else:
            ema[i] = ema[i - 1]

    return ema


def run_backtest():
    # === LOAD DATA ===
    print("Loading candle data...")
    all_candles = {}
    for coin in COINS:
        candles = load_candles(coin)
        if candles:
            all_candles[coin] = candles

    if "btcusdt" not in all_candles:
        print("ERROR: No BTC data!")
        return

    # Align to BTC timestamps
    btc_candles = all_candles["btcusdt"]
    btc_times = [c["time"] for c in btc_candles]
    n_bars = len(btc_times)

    coin_lookups = {}
    for coin, candles in all_candles.items():
        coin_lookups[coin] = {c["time"]: c for c in candles}

    first = datetime.utcfromtimestamp(btc_times[0])
    last = datetime.utcfromtimestamp(btc_times[-1])
    print(f"Loaded {len(all_candles)}/{TOTAL_COINS} coins")
    print(f"BTC: {n_bars} bars | {first:%Y-%m-%d} → {last:%Y-%m-%d}")

    # === COMPUTE RSI FOR EACH COIN ===
    coin_rsi = {}
    for coin in all_candles:
        closes = []
        lookup = coin_lookups[coin]
        last_close = None
        for ts in btc_times:
            if ts in lookup:
                last_close = lookup[ts]["close"]
            closes.append(last_close)

        first_valid = next((c for c in closes if c is not None), None)
        if first_valid is None:
            continue
        closes = [c if c is not None else first_valid for c in closes]
        coin_rsi[coin] = compute_rsi(closes, RSI_LEN)

    # === COMPUTE BREATHING SCORE ===
    raw_scores = []
    for i in range(n_bars):
        score = 0
        valid = 0
        for coin in coin_rsi:
            rsi_val = coin_rsi[coin][i]
            if rsi_val is not None:
                valid += 1
                if rsi_val < BUY_ZONE:
                    score += 1
                elif rsi_val > SELL_ZONE:
                    score -= 1
        if valid > 0:
            normalized = score * 20.0 / TOTAL_COINS
        else:
            normalized = 0.0
        raw_scores.append(normalized)

    breath_scores = compute_ema(raw_scores, EMA_SMOOTH)

    # === DETECT SIGNALS (exact v5 logic) ===
    print(f"\nDetecting v5 SHORT signals (threshold={SHORT_THRESHOLD})...")

    signals = []
    was_green = False
    short_armed = False
    trough_val = 0.0
    last_short_bar = -999

    for i in range(2, n_bars):
        score = breath_scores[i]
        prev = breath_scores[i - 1]
        prev2 = breath_scores[i - 2]

        if score is None or prev is None or prev2 is None:
            continue

        # Track green
        if score > 0:
            was_green = True

        # Arm
        if score < SHORT_THRESHOLD and was_green:
            short_armed = True
            if score < trough_val:
                trough_val = score

        # Detect curl-up while armed
        if (short_armed
                and score > prev
                and prev <= prev2
                and score < 0
                and (i - last_short_bar) >= SHORT_COOLDOWN):

            # Get BTC price
            ts = btc_times[i]
            btc_lookup = coin_lookups["btcusdt"]
            if ts in btc_lookup:
                entry_price = btc_lookup[ts]["close"]
                signals.append({
                    "bar": i,
                    "time": ts,
                    "entry_price": entry_price,
                    "breath_score": round(score, 2),
                    "trough_score": round(trough_val, 2),
                })

            # Reset (v5: wasGreen resets on fire)
            short_armed = False
            last_short_bar = i
            trough_val = 0.0
            was_green = False

        # Disarm if score goes above 0
        if short_armed and score > 0:
            short_armed = False
            trough_val = 0.0
            was_green = False

    print(f"Found {len(signals)} signals")

    if not signals:
        print("No signals found!")
        return

    # === PRINT SIGNAL TIMELINE ===
    btc_lookup = coin_lookups["btcusdt"]

    print(f"\n{'='*70}")
    print(f"  SIGNAL TIMELINE")
    print(f"{'='*70}")
    print(f"  {'#':>3s}  {'Date':>12s}  {'Entry':>10s}  {'Score':>6s}  "
          f"{'Trough':>7s}  {'7d Move':>8s}  {'14d Move':>8s}")
    print(f"  {'─'*66}")

    for idx, sig in enumerate(signals):
        dt = datetime.utcfromtimestamp(sig["time"])
        bar = sig["bar"]

        # 7d and 14d price moves
        r7d = None
        r14d = None
        for label, offset in [("7d", 42), ("14d", 84)]:
            fb = bar + offset
            if fb < n_bars:
                ts = btc_times[fb]
                if ts in btc_lookup:
                    fp = btc_lookup[ts]["close"]
                    ret = (sig["entry_price"] - fp) / sig["entry_price"] * 100
                    if label == "7d":
                        r7d = ret
                    else:
                        r14d = ret

        def fmt(v):
            return f"{v:+7.1f}%" if v is not None else "     —  "

        print(f"  {idx+1:3d}  {dt:%Y-%m-%d}  "
              f"${sig['entry_price']:>9,.0f}  "
              f"{sig['breath_score']:>6.1f}  "
              f"{sig['trough_score']:>7.1f}  "
              f"{fmt(r7d)}  {fmt(r14d)}")

    # === TEST SL/TP GRID ===
    print(f"\n{'='*70}")
    print(f"  SL/TP GRID RESULTS")
    print(f"{'='*70}")
    print(f"\n  Testing {len(SL_LEVELS)} SL x {len(TP_LEVELS)} TP "
          f"= {len(SL_LEVELS)*len(TP_LEVELS)} combos...\n")

    best_pnl = -999
    best_combo = None
    results = []

    for sl in SL_LEVELS:
        for tp in TP_LEVELS:
            wins = 0
            losses = 0
            total_pnl = 0.0
            trades = 0
            timeouts = 0

            for sig in signals:
                bar = sig["bar"]
                entry = sig["entry_price"]
                sl_price = entry * (1 + sl / 100.0)   # short SL = price UP
                tp_price = entry * (1 - tp / 100.0)   # short TP = price DOWN

                outcome = None
                end_bar = min(bar + MAX_HOLD_BARS, n_bars)

                for j in range(bar + 1, end_bar):
                    ts = btc_times[j]
                    if ts not in btc_lookup:
                        continue
                    candle = btc_lookup[ts]
                    high = candle["high"]
                    low = candle["low"]

                    # Check SL first (worst case)
                    if high >= sl_price:
                        outcome = -sl
                        break
                    # Check TP
                    if low <= tp_price:
                        outcome = tp
                        break

                if outcome is None:
                    # Timeout — close at last bar's close
                    if bar + 1 < end_bar:
                        ts = btc_times[end_bar - 1]
                        if ts in btc_lookup:
                            close_price = btc_lookup[ts]["close"]
                            outcome = (entry - close_price) / entry * 100
                        else:
                            outcome = 0
                    else:
                        outcome = 0
                    timeouts += 1

                trades += 1
                total_pnl += outcome
                if outcome > 0:
                    wins += 1
                else:
                    losses += 1

            if trades == 0:
                continue

            wr = wins / trades * 100
            avg_pnl = total_pnl / trades
            result = {
                "sl": sl, "tp": tp,
                "trades": trades, "wins": wins, "losses": losses,
                "wr": wr, "total_pnl": total_pnl, "avg_pnl": avg_pnl,
                "timeouts": timeouts,
            }
            results.append(result)

            if total_pnl > best_pnl:
                best_pnl = total_pnl
                best_combo = result

    # Print grid as table
    print(f"  {'SL%':>4s}  {'TP%':>4s}  {'Trades':>6s}  {'WR%':>5s}  "
          f"{'AvgP&L':>7s}  {'TotalP&L':>9s}  {'W':>3s}  {'L':>3s}  "
          f"{'TO':>3s}")
    print(f"  {'─'*58}")

    # Sort by total P&L
    results.sort(key=lambda r: r["total_pnl"], reverse=True)

    for r in results:
        marker = " ★" if r == best_combo else ""
        print(f"  {r['sl']:>3d}%  {r['tp']:>3d}%  {r['trades']:>6d}  "
              f"{r['wr']:>4.0f}%  {r['avg_pnl']:>+6.2f}%  "
              f"{r['total_pnl']:>+8.1f}%  "
              f"{r['wins']:>3d}  {r['losses']:>3d}  "
              f"{r['timeouts']:>3d}{marker}")

    # Best combo details
    if best_combo:
        print(f"\n{'='*70}")
        print(f"  BEST COMBO: {best_combo['sl']}% SL / "
              f"{best_combo['tp']}% TP")
        print(f"{'='*70}")
        print(f"  Win Rate:  {best_combo['wr']:.1f}%")
        print(f"  Avg P&L:   {best_combo['avg_pnl']:+.2f}% per trade")
        print(f"  Total P&L: {best_combo['total_pnl']:+.1f}% "
              f"across {best_combo['trades']} trades")
        print(f"  Wins: {best_combo['wins']}  "
              f"Losses: {best_combo['losses']}  "
              f"Timeouts: {best_combo['timeouts']}")

        # Kelly criterion
        if best_combo['wr'] > 0 and best_combo['losses'] > 0:
            p = best_combo['wr'] / 100
            avg_win = best_combo['tp']
            avg_loss = best_combo['sl']
            b = avg_win / avg_loss
            kelly = p - (1 - p) / b
            print(f"\n  Kelly Criterion: {kelly*100:.1f}% of bankroll")
            print(f"  Half-Kelly (safer): {kelly*50:.1f}%")

    # === INDIVIDUAL TRADE RESULTS FOR BEST COMBO ===
    if best_combo:
        sl = best_combo["sl"]
        tp = best_combo["tp"]
        print(f"\n{'='*70}")
        print(f"  INDIVIDUAL TRADES ({sl}% SL / {tp}% TP)")
        print(f"{'='*70}")
        print(f"  {'#':>3s}  {'Date':>12s}  {'Entry':>10s}  "
              f"{'Result':>8s}  {'P&L':>7s}")
        print(f"  {'─'*50}")

        for idx, sig in enumerate(signals):
            bar = sig["bar"]
            entry = sig["entry_price"]
            sl_price = entry * (1 + sl / 100.0)
            tp_price = entry * (1 - tp / 100.0)
            dt = datetime.utcfromtimestamp(sig["time"])

            outcome = None
            result_str = ""
            end_bar = min(bar + MAX_HOLD_BARS, n_bars)

            for j in range(bar + 1, end_bar):
                ts = btc_times[j]
                if ts not in btc_lookup:
                    continue
                candle = btc_lookup[ts]
                if candle["high"] >= sl_price:
                    outcome = -sl
                    result_str = "STOPPED"
                    break
                if candle["low"] <= tp_price:
                    outcome = tp
                    result_str = "TP HIT"
                    break

            if outcome is None:
                if bar + 1 < end_bar:
                    ts = btc_times[end_bar - 1]
                    if ts in btc_lookup:
                        close_price = btc_lookup[ts]["close"]
                        outcome = (entry - close_price) / entry * 100
                    else:
                        outcome = 0
                else:
                    outcome = 0
                result_str = "TIMEOUT"

            emoji = "✓" if outcome > 0 else "✗"
            print(f"  {idx+1:3d}  {dt:%Y-%m-%d}  "
                  f"${entry:>9,.0f}  "
                  f"{result_str:>8s}  "
                  f"{outcome:>+6.1f}% {emoji}")

    print(f"\n{'='*70}")


if __name__ == "__main__":
    run_backtest()
