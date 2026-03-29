"""Backtest: Rhythm Filter v5 — Half-TP strategy.

Strategy:
  1. SHORT entry on v5 signal
  2. If price drops to half-TP: close half, move SL to breakeven
  3. Runner rides with trailing stop from lowest point
  4. If SL hit before half-TP: full loss

Tests grid of SL / half-TP / trailing stop combos.
"""

import json
import os
from datetime import datetime

import config

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

RSI_LEN = 14
BUY_ZONE = 40.0
SELL_ZONE = 60.0
EMA_SMOOTH = 9
TOTAL_COINS = 40
SHORT_THRESHOLD = -5.0
SHORT_COOLDOWN = 12

# === GRID ===
SL_LEVELS = [3, 5, 7, 10]
HALF_TP_LEVELS = [1, 2, 3, 4, 5]
TRAIL_LEVELS = [2, 3, 5, 7]
MAX_HOLD_BARS = 168  # 28 days — let runners run


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
    gains, losses = [], []
    for i in range(1, length + 1):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains) / length
    al = sum(losses) / length
    if al == 0:
        rsi[length] = 100.0
    else:
        rsi[length] = 100 - 100 / (1 + ag / al)
    for i in range(length + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (length - 1) + max(d, 0)) / length
        al = (al * (length - 1) + max(-d, 0)) / length
        if al == 0:
            rsi[i] = 100.0
        else:
            rsi[i] = 100 - 100 / (1 + ag / al)
    return rsi


def compute_ema(values, length):
    ema = [None] * len(values)
    m = 2.0 / (length + 1)
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
            ema[i] = values[i] * m + ema[i - 1] * (1 - m)
        else:
            ema[i] = ema[i - 1]
    return ema


def get_signals(breath_scores, btc_times, coin_lookups, n_bars):
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

        if score > 0:
            was_green = True
        if score < SHORT_THRESHOLD and was_green:
            short_armed = True
            if score < trough_val:
                trough_val = score

        if (short_armed and score > prev and prev <= prev2
                and score < 0
                and (i - last_short_bar) >= SHORT_COOLDOWN):
            ts = btc_times[i]
            btc_lookup = coin_lookups["btcusdt"]
            if ts in btc_lookup:
                signals.append({
                    "bar": i, "time": ts,
                    "entry_price": btc_lookup[ts]["close"],
                    "breath_score": round(score, 2),
                    "trough_score": round(trough_val, 2),
                })
            short_armed = False
            last_short_bar = i
            trough_val = 0.0
            was_green = False

        if short_armed and score > 0:
            short_armed = False
            trough_val = 0.0
            was_green = False

    return signals


def sim_half_tp(sig, sl_pct, half_tp_pct, trail_pct,
                btc_times, btc_lookup, n_bars):
    """Simulate a half-TP trade. Returns total P&L as % of position."""
    bar = sig["bar"]
    entry = sig["entry_price"]
    sl_price = entry * (1 + sl_pct / 100)
    half_tp_price = entry * (1 - half_tp_pct / 100)

    half_taken = False
    lowest_since_half = entry
    end_bar = min(bar + MAX_HOLD_BARS, n_bars)

    for j in range(bar + 1, end_bar):
        ts = btc_times[j]
        if ts not in btc_lookup:
            continue
        candle = btc_lookup[ts]
        high = candle["high"]
        low = candle["low"]

        if not half_taken:
            # Phase 1: waiting for half-TP or SL
            if high >= sl_price:
                # Full stop loss on entire position
                return -sl_pct, "FULL SL"
            if low <= half_tp_price:
                # Half-TP hit! Take half profit
                half_taken = True
                lowest_since_half = low
                # SL now moves to breakeven for remaining half
                continue
        else:
            # Phase 2: runner with trailing stop
            # Track lowest point
            if low < lowest_since_half:
                lowest_since_half = low

            # Trailing stop: if price rises trail_pct from lowest
            trail_stop = lowest_since_half * (1 + trail_pct / 100)
            # Also breakeven stop
            be_stop = entry

            effective_stop = min(trail_stop, be_stop)

            if high >= effective_stop:
                # Runner stopped — close at effective stop
                runner_pnl = (entry - effective_stop) / entry * 100
                # Total = half at half_tp + half at runner exit
                total = (half_tp_pct + runner_pnl) / 2
                if runner_pnl >= 0:
                    return total, "HALF+BE"
                else:
                    return total, "HALF+TRAIL"

    # Timeout — close runner at last bar
    if half_taken:
        ts = btc_times[end_bar - 1]
        if ts in btc_lookup:
            close_price = btc_lookup[ts]["close"]
            runner_pnl = (entry - close_price) / entry * 100
            total = (half_tp_pct + runner_pnl) / 2
            return total, "HALF+TIME"
        return half_tp_pct / 2, "HALF+TIME"
    else:
        # Never hit half-TP, close at last bar
        ts = btc_times[end_bar - 1]
        if ts in btc_lookup:
            close_price = btc_lookup[ts]["close"]
            pnl = (entry - close_price) / entry * 100
            return pnl, "TIMEOUT"
        return 0, "TIMEOUT"


def run_backtest():
    print("Loading data...")
    all_candles = {}
    for coin in COINS:
        candles = load_candles(coin)
        if candles:
            all_candles[coin] = candles

    if "btcusdt" not in all_candles:
        print("ERROR: No BTC data!")
        return

    btc_candles = all_candles["btcusdt"]
    btc_times = [c["time"] for c in btc_candles]
    n_bars = len(btc_times)
    coin_lookups = {}
    for coin, candles in all_candles.items():
        coin_lookups[coin] = {c["time"]: c for c in candles}

    first = datetime.utcfromtimestamp(btc_times[0])
    last = datetime.utcfromtimestamp(btc_times[-1])
    print(f"{len(all_candles)} coins | {n_bars} bars | "
          f"{first:%Y-%m-%d} → {last:%Y-%m-%d}")

    # Compute breathing score
    coin_rsi = {}
    for coin in all_candles:
        closes = []
        lookup = coin_lookups[coin]
        lc = None
        for ts in btc_times:
            if ts in lookup:
                lc = lookup[ts]["close"]
            closes.append(lc)
        fv = next((c for c in closes if c is not None), None)
        if fv is None:
            continue
        closes = [c if c is not None else fv for c in closes]
        coin_rsi[coin] = compute_rsi(closes, RSI_LEN)

    raw_scores = []
    for i in range(n_bars):
        score = 0
        valid = 0
        for coin in coin_rsi:
            rv = coin_rsi[coin][i]
            if rv is not None:
                valid += 1
                if rv < BUY_ZONE:
                    score += 1
                elif rv > SELL_ZONE:
                    score -= 1
        raw_scores.append(score * 20.0 / TOTAL_COINS if valid > 0 else 0.0)

    breath_scores = compute_ema(raw_scores, EMA_SMOOTH)

    # Get signals
    signals = get_signals(breath_scores, btc_times, coin_lookups, n_bars)
    print(f"\n{len(signals)} SHORT signals found\n")

    btc_lookup = coin_lookups["btcusdt"]

    # === TEST GRID ===
    print(f"{'='*78}")
    print(f"  HALF-TP STRATEGY GRID")
    print(f"  Half position at TP1, move SL to breakeven, "
          f"trail remaining half")
    print(f"{'='*78}")

    results = []

    for sl in SL_LEVELS:
        for htp in HALF_TP_LEVELS:
            for trail in TRAIL_LEVELS:
                wins = 0
                losses = 0
                total_pnl = 0.0
                full_sl_count = 0
                half_be_count = 0
                runner_count = 0

                for sig in signals:
                    pnl, outcome = sim_half_tp(
                        sig, sl, htp, trail,
                        btc_times, btc_lookup, n_bars)
                    total_pnl += pnl
                    if pnl > 0:
                        wins += 1
                    else:
                        losses += 1
                    if outcome == "FULL SL":
                        full_sl_count += 1
                    elif "HALF" in outcome:
                        if "BE" in outcome:
                            half_be_count += 1
                        else:
                            runner_count += 1

                trades = wins + losses
                if trades == 0:
                    continue
                wr = wins / trades * 100
                avg = total_pnl / trades

                results.append({
                    "sl": sl, "htp": htp, "trail": trail,
                    "trades": trades, "wins": wins, "losses": losses,
                    "wr": wr, "total_pnl": total_pnl, "avg_pnl": avg,
                    "full_sl": full_sl_count,
                    "half_be": half_be_count,
                    "runners": runner_count,
                })

    # Sort by total P&L
    results.sort(key=lambda r: r["total_pnl"], reverse=True)

    # Print top 30
    print(f"\n  {'SL':>3s}  {'HTP':>3s}  {'Trail':>5s}  "
          f"{'WR%':>4s}  {'AvgP&L':>7s}  {'TotalP&L':>9s}  "
          f"{'FullSL':>6s}  {'Half+BE':>7s}  {'Runners':>7s}")
    print(f"  {'─'*68}")

    for r in results[:30]:
        print(f"  {r['sl']:>2d}%  {r['htp']:>2d}%  {r['trail']:>4d}%  "
              f"{r['wr']:>4.0f}%  {r['avg_pnl']:>+6.2f}%  "
              f"{r['total_pnl']:>+8.1f}%  "
              f"{r['full_sl']:>6d}  {r['half_be']:>7d}  "
              f"{r['runners']:>7d}")

    # Best combo details
    if results:
        best = results[0]
        print(f"\n{'='*78}")
        print(f"  BEST: {best['sl']}% SL / {best['htp']}% Half-TP / "
              f"{best['trail']}% Trail")
        print(f"{'='*78}")
        print(f"  Win Rate:    {best['wr']:.1f}%")
        print(f"  Avg P&L:     {best['avg_pnl']:+.2f}% per trade")
        print(f"  Total P&L:   {best['total_pnl']:+.1f}% across "
              f"{best['trades']} trades")
        print(f"  Full SL:     {best['full_sl']} trades")
        print(f"  Half + BE:   {best['half_be']} trades")
        print(f"  Runners:     {best['runners']} trades")

        # Show individual trades for best combo
        print(f"\n{'='*78}")
        print(f"  INDIVIDUAL TRADES")
        print(f"{'='*78}")
        print(f"  {'#':>3s}  {'Date':>12s}  {'Entry':>10s}  "
              f"{'Result':>10s}  {'P&L':>7s}")
        print(f"  {'─'*52}")

        for idx, sig in enumerate(signals):
            pnl, outcome = sim_half_tp(
                sig, best["sl"], best["htp"], best["trail"],
                btc_times, btc_lookup, n_bars)
            dt = datetime.utcfromtimestamp(sig["time"])
            marker = "✓" if pnl > 0 else "✗"
            print(f"  {idx+1:3d}  {dt:%Y-%m-%d}  "
                  f"${sig['entry_price']:>9,.0f}  "
                  f"{outcome:>10s}  {pnl:>+6.1f}% {marker}")

    print(f"\n{'='*78}")


if __name__ == "__main__":
    run_backtest()
