"""Backtest v2: test multiple EXHALE short strategies.

Tests 4 entry strategies:
A) Short when score first crosses below -5 (EXHALE start)
B) Short when score first crosses below -10 (deep EXHALE)
C) Short when score peaks GREEN and rolls over below +5 (inhale ending)
D) Short at deep red rollover (v1 approach, refined)

Also tests SL/TP combinations.
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

# SL/TP combos to test
SL_TP_COMBOS = [
    (3, 2),
    (5, 3),
    (5, 5),
    (7, 5),
    (10, 7),
    (10, 10),
    (15, 10),
    (20, 13),
]

# Cooldown: minimum bars between signals
COOLDOWN_BARS = 12  # 2 days on 4H


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


def simulate_trade(entry_price, bar, btc_times, btc_lookup,
                   n_bars, sl_pct, tp_pct):
    """Simulate a short trade with SL/TP.

    Returns (result, exit_price, exit_bar, pnl_pct)
    """
    sl_price = entry_price * (1 + sl_pct / 100)
    tp_price = entry_price * (1 - tp_pct / 100)

    for j in range(bar + 1, min(bar + 504, n_bars)):  # max 84 days
        ts = btc_times[j]
        if ts not in btc_lookup:
            continue
        candle = btc_lookup[ts]
        high = candle["high"]
        low = candle["low"]

        # Check SL first (worst case)
        if high >= sl_price:
            pnl = -sl_pct
            return ("SL", sl_price, j, pnl)

        # Check TP
        if low <= tp_price:
            pnl = tp_pct
            return ("TP", tp_price, j, pnl)

    # Still open after max bars — close at last price
    ts = btc_times[min(bar + 503, n_bars - 1)]
    if ts in btc_lookup:
        exit_price = btc_lookup[ts]["close"]
        pnl = (entry_price - exit_price) / entry_price * 100
        return ("TIMEOUT", exit_price, min(bar + 503, n_bars - 1), pnl)
    return ("TIMEOUT", entry_price, bar, 0)


def run():
    # === LOAD DATA ===
    print("Loading candle data...")
    all_candles = {}
    for coin in COINS:
        candles = load_candles(coin)
        if candles:
            all_candles[coin] = candles

    print(f"Loaded {len(all_candles)}/{TOTAL_COINS} coins")

    btc_candles = all_candles["btcusdt"]
    btc_times = [c["time"] for c in btc_candles]
    n_bars = len(btc_times)

    coin_lookups = {}
    for coin, candles in all_candles.items():
        coin_lookups[coin] = {c["time"]: c for c in candles}

    btc_lookup = coin_lookups["btcusdt"]

    first = datetime.utcfromtimestamp(btc_times[0])
    last = datetime.utcfromtimestamp(btc_times[-1])
    print(f"Range: {first.strftime('%Y-%m-%d')} → "
          f"{last.strftime('%Y-%m-%d')} ({n_bars} bars)\n")

    # === COMPUTE BREATHING SCORE ===
    print("Computing breathing score...")
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

    raw_scores = []
    for i in range(n_bars):
        score = 0
        for coin in coin_rsi:
            rsi_val = coin_rsi[coin][i]
            if rsi_val is not None:
                if rsi_val < BUY_ZONE:
                    score += 1
                elif rsi_val > SELL_ZONE:
                    score -= 1
        raw_scores.append(score * 20.0 / TOTAL_COINS)

    breath = compute_ema(raw_scores, EMA_SMOOTH)

    # === GENERATE SIGNALS FOR EACH STRATEGY ===
    strategies = {}

    # --- Strategy A: Cross below -5 (EXHALE start) ---
    sigs_a = []
    last_signal_bar = -999
    for i in range(1, n_bars):
        if breath[i] is None or breath[i-1] is None:
            continue
        if (breath[i] < -5 and breath[i-1] >= -5
                and i - last_signal_bar >= COOLDOWN_BARS):
            ts = btc_times[i]
            if ts in btc_lookup:
                sigs_a.append({
                    "bar": i, "time": ts,
                    "entry_price": btc_lookup[ts]["close"],
                    "score": round(breath[i], 2),
                })
                last_signal_bar = i
    strategies["A: Cross below -5"] = sigs_a

    # --- Strategy B: Cross below -10 (deep EXHALE) ---
    sigs_b = []
    last_signal_bar = -999
    for i in range(1, n_bars):
        if breath[i] is None or breath[i-1] is None:
            continue
        if (breath[i] < -10 and breath[i-1] >= -10
                and i - last_signal_bar >= COOLDOWN_BARS):
            ts = btc_times[i]
            if ts in btc_lookup:
                sigs_b.append({
                    "bar": i, "time": ts,
                    "entry_price": btc_lookup[ts]["close"],
                    "score": round(breath[i], 2),
                })
                last_signal_bar = i
    strategies["B: Cross below -10"] = sigs_b

    # --- Strategy C: Inhale peak rollover (green → fading) ---
    # Score was above +8, now crosses below +5
    sigs_c = []
    last_signal_bar = -999
    was_high = False
    for i in range(1, n_bars):
        if breath[i] is None:
            continue
        if breath[i] >= 8:
            was_high = True
        if (was_high and breath[i] < 5 and breath[i-1] >= 5
                and i - last_signal_bar >= COOLDOWN_BARS):
            ts = btc_times[i]
            if ts in btc_lookup:
                sigs_c.append({
                    "bar": i, "time": ts,
                    "entry_price": btc_lookup[ts]["close"],
                    "score": round(breath[i], 2),
                })
                last_signal_bar = i
                was_high = False
    strategies["C: Inhale peak rollover"] = sigs_c

    # --- Strategy D: Score declining — was positive, now negative ---
    # Cross below 0 (neutral → exhale transition)
    sigs_d = []
    last_signal_bar = -999
    for i in range(1, n_bars):
        if breath[i] is None or breath[i-1] is None:
            continue
        if (breath[i] < 0 and breath[i-1] >= 0
                and i - last_signal_bar >= COOLDOWN_BARS):
            ts = btc_times[i]
            if ts in btc_lookup:
                sigs_d.append({
                    "bar": i, "time": ts,
                    "entry_price": btc_lookup[ts]["close"],
                    "score": round(breath[i], 2),
                })
                last_signal_bar = i
    strategies["D: Cross below 0"] = sigs_d

    # --- Strategy E: Score was above +5, drops 5+ points in 6 bars ---
    sigs_e = []
    last_signal_bar = -999
    for i in range(6, n_bars):
        if breath[i] is None or breath[i-6] is None:
            continue
        drop = breath[i-6] - breath[i]
        if (breath[i-6] >= 5 and drop >= 5
                and breath[i] < breath[i-1]
                and i - last_signal_bar >= COOLDOWN_BARS):
            ts = btc_times[i]
            if ts in btc_lookup:
                sigs_e.append({
                    "bar": i, "time": ts,
                    "entry_price": btc_lookup[ts]["close"],
                    "score": round(breath[i], 2),
                    "drop": round(drop, 2),
                })
                last_signal_bar = i
    strategies["E: Fast drop from green"] = sigs_e

    # === RUN SL/TP SIMULATION FOR EACH STRATEGY ===
    print(f"\n{'='*90}")
    print(f"  STRATEGY COMPARISON — EXHALE SHORTS ON BTC (4H)")
    print(f"{'='*90}")

    for name, sigs in strategies.items():
        print(f"\n  ┌{'─'*86}┐")
        print(f"  │ {name:<84s} │")
        print(f"  │ Signals: {len(sigs):<75d} │")
        print(f"  └{'─'*86}┘")

        if not sigs:
            print(f"    No signals!")
            continue

        # Show dates of first/last few signals
        print(f"    First: "
              f"{datetime.utcfromtimestamp(sigs[0]['time']).strftime('%Y-%m-%d')}"
              f"  Last: "
              f"{datetime.utcfromtimestamp(sigs[-1]['time']).strftime('%Y-%m-%d')}")

        print(f"\n    {'SL%':>4s} {'TP%':>4s}  "
              f"{'Wins':>5s} {'Loss':>5s} {'TO':>4s}  "
              f"{'WR%':>5s}  {'AvgPnL':>7s}  "
              f"{'TotalPnL':>9s}  {'PF':>5s}  "
              f"{'AvgWin':>7s}  {'AvgLoss':>8s}")
        print(f"    {'─'*4} {'─'*4}  "
              f"{'─'*5} {'─'*5} {'─'*4}  "
              f"{'─'*5}  {'─'*7}  "
              f"{'─'*9}  {'─'*5}  "
              f"{'─'*7}  {'─'*8}")

        for sl_pct, tp_pct in SL_TP_COMBOS:
            wins = 0
            losses = 0
            timeouts = 0
            total_pnl = 0
            win_pnls = []
            loss_pnls = []

            for sig in sigs:
                result, _, _, pnl = simulate_trade(
                    sig["entry_price"], sig["bar"],
                    btc_times, btc_lookup, n_bars,
                    sl_pct, tp_pct
                )
                total_pnl += pnl
                if result == "TP":
                    wins += 1
                    win_pnls.append(pnl)
                elif result == "SL":
                    losses += 1
                    loss_pnls.append(pnl)
                else:
                    timeouts += 1
                    if pnl > 0:
                        win_pnls.append(pnl)
                    else:
                        loss_pnls.append(pnl)

            total = wins + losses + timeouts
            wr = wins / total * 100 if total > 0 else 0
            avg_pnl = total_pnl / total if total > 0 else 0
            avg_win = (sum(win_pnls) / len(win_pnls)
                       if win_pnls else 0)
            avg_loss = (sum(loss_pnls) / len(loss_pnls)
                        if loss_pnls else 0)

            gross_wins = sum(win_pnls)
            gross_losses = abs(sum(loss_pnls))
            pf = (gross_wins / gross_losses
                  if gross_losses > 0 else 999)

            marker = " ◄" if wr >= 55 and pf >= 1.2 else ""

            print(f"    {sl_pct:>3d}% {tp_pct:>3d}%  "
                  f"{wins:>5d} {losses:>5d} {timeouts:>4d}  "
                  f"{wr:>4.0f}%  "
                  f"{avg_pnl:>+6.2f}%  "
                  f"{total_pnl:>+8.1f}%  "
                  f"{pf:>5.2f}  "
                  f"{avg_win:>+6.2f}%  "
                  f"{avg_loss:>+7.2f}%"
                  f"{marker}")

    # === BEST SIGNALS DETAIL (Strategy C — most promising) ===
    best_strat = "C: Inhale peak rollover"
    best_sigs = strategies[best_strat]
    if best_sigs:
        print(f"\n{'='*90}")
        print(f"  SIGNAL DETAIL — {best_strat}")
        print(f"{'='*90}")
        print(f"\n  {'#':>3s}  {'Date':>12s}  {'Entry':>10s}  "
              f"{'Score':>6s}  {'5%SL/3%TP':>10s}  "
              f"{'10%SL/7%TP':>11s}")
        print(f"  {'─'*70}")

        for i, sig in enumerate(best_sigs):
            dt = datetime.utcfromtimestamp(sig["time"])

            r1, _, _, pnl1 = simulate_trade(
                sig["entry_price"], sig["bar"],
                btc_times, btc_lookup, n_bars, 5, 3)
            r2, _, _, pnl2 = simulate_trade(
                sig["entry_price"], sig["bar"],
                btc_times, btc_lookup, n_bars, 10, 7)

            print(f"  {i+1:3d}  {dt.strftime('%Y-%m-%d'):>12s}  "
                  f"${sig['entry_price']:>9,.0f}  "
                  f"{sig['score']:>6.1f}  "
                  f"{r1:>3s} {pnl1:>+5.1f}%  "
                  f"{r2:>4s} {pnl2:>+5.1f}%")

    print(f"\n{'='*90}")


if __name__ == "__main__":
    run()
