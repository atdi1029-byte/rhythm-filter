"""Baseline Grid: Unfiltered Rhythm Filter — same SL/TP combos as combined grid.

No RSI sell filter. Shorts ALL coins on every breathing signal.
This is the control to compare against the combined filter.
"""

import json
import os
from datetime import datetime

import config

COINS = [
    "btcusdt", "ethusdt", "xrpusdt", "bnbusdt", "solusdt",
    "dogeusdt", "adausdt", "trxusdt", "avaxusdt", "shibusdt",
    "tonusdt", "linkusdt", "suiusdt", "dotusdt", "nearusdt",
    "uniusdt", "aptusdt", "polusdt", "arbusdt", "opusdt",
    "icpusdt", "hbarusdt", "filusdt", "atomusdt", "imxusdt",
    "injusdt", "stxusdt", "susdt", "grtusdt", "thetausdt",
    "algousdt", "ldousdt", "aaveusdt", "skyusdt", "snxusdt",
    "vetusdt", "xlmusdt", "pepeusdt", "fetusdt", "wldusdt",
]

RSI_LEN = 14
BUY_ZONE = 40.0
SELL_ZONE = 60.0
EMA_SMOOTH = 9
TOTAL_COINS = 40
SHORT_THRESHOLD = -5.0
SHORT_COOLDOWN = 12
MAX_HOLD_BARS = 2016

SL_RANGE = [2.0, 3.0, 4.0, 5.0, 6.0, 8.0]
TP_RANGE = [3.0, 5.0, 7.0, 10.0, 12.0, 15.0]


def load_5m(symbol):
    filepath = os.path.join(config.DATA_DIR, "5m", f"{symbol}_5m.json")
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


def get_breathing_signals(breath_scores, n_bars):
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
            signals.append(i)
            short_armed = False
            last_short_bar = i
            trough_val = 0.0
            was_green = False
        if short_armed and score > 0:
            short_armed = False
            trough_val = 0.0
            was_green = False
    return signals


def sim_trade(signal_bar, coin_lookup, btc_times, n_bars, sl_pct, tp_pct):
    ts = btc_times[signal_bar]
    if ts not in coin_lookup:
        return None, None
    entry = coin_lookup[ts]["close"]
    if entry == 0:
        return None, None
    sl_price = entry * (1 + sl_pct / 100)
    tp_price = entry * (1 - tp_pct / 100)
    end_bar = min(signal_bar + MAX_HOLD_BARS, n_bars)
    for j in range(signal_bar + 1, end_bar):
        ts_j = btc_times[j]
        if ts_j not in coin_lookup:
            continue
        candle = coin_lookup[ts_j]
        if candle["high"] >= sl_price:
            return -sl_pct, "SL"
        if candle["low"] <= tp_price:
            return tp_pct, "TP"
    ts_end = btc_times[end_bar - 1]
    if ts_end in coin_lookup:
        close_price = coin_lookup[ts_end]["close"]
        pnl = (entry - close_price) / entry * 100
        return pnl, "TIMEOUT"
    return 0, "TIMEOUT"


def run_backtest():
    print("Loading 5min data...")
    all_candles = {}
    for coin in COINS:
        candles = load_5m(coin)
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
          f"{first:%Y-%m-%d} -> {last:%Y-%m-%d}")

    # Breathing score
    print("Computing breathing score...")
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
    signal_bars = get_breathing_signals(breath_scores, n_bars)
    print(f"{len(signal_bars)} breathing SHORT signals")

    # === GRID TEST ===
    print(f"\n{'='*85}")
    print(f"  BASELINE GRID (NO FILTER) — Rhythm Filter only")
    print(f"  {first:%Y-%m-%d} -> {last:%Y-%m-%d} | "
          f"{len(signal_bars)} signals x {len(all_candles)} coins")
    print(f"{'='*85}")

    all_results = []

    print(f"\n  {'SL%':>4s}  {'TP%':>4s}  {'Trades':>6s}  "
          f"{'Wins':>5s}  {'Losses':>6s}  {'WR%':>6s}  "
          f"{'Total P&L':>10s}  {'Avg/Trade':>9s}  "
          f"{'Profit$10K':>10s}")
    print(f"  {'─'*75}")

    for sl in SL_RANGE:
        for tp in TP_RANGE:
            wins = 0
            losses = 0
            total_pnl = 0.0

            for coin in all_candles:
                lookup = coin_lookups[coin]
                for bar in signal_bars:
                    pnl, outcome = sim_trade(
                        bar, lookup, btc_times, n_bars, sl, tp)
                    if pnl is None:
                        continue
                    total_pnl += pnl
                    if pnl > 0:
                        wins += 1
                    else:
                        losses += 1

            trades = wins + losses
            if trades == 0:
                continue

            wr = wins / trades * 100
            avg = total_pnl / trades
            profit_10k = total_pnl / 100 * 10000

            all_results.append({
                "sl": sl, "tp": tp,
                "trades": trades, "wins": wins,
                "losses": losses, "wr": wr,
                "pnl": total_pnl, "avg": avg,
                "profit_10k": profit_10k,
            })

            pnl_mark = "+" if total_pnl >= 0 else ""
            print(f"  {sl:>4.0f}  {tp:>4.0f}  {trades:>6d}  "
                  f"{wins:>5d}  {losses:>6d}  {wr:>5.1f}%  "
                  f"{pnl_mark}{total_pnl:>9.0f}%  "
                  f"{avg:>+7.3f}%  "
                  f"${profit_10k:>+9,.0f}")

    # Top 10 by total P&L
    print(f"\n{'='*85}")
    print(f"  TOP 10 BY TOTAL P&L")
    print(f"{'='*85}")
    top = sorted(all_results, key=lambda r: r["pnl"], reverse=True)[:10]
    print(f"\n  {'#':>3s}  {'SL%':>4s}  {'TP%':>4s}  {'Trades':>6s}  "
          f"{'WR%':>6s}  {'Avg/Trade':>9s}  {'Total P&L':>10s}  "
          f"{'$10K Profit':>11s}")
    print(f"  {'─'*65}")
    for i, r in enumerate(top):
        pnl_mark = "+" if r["pnl"] >= 0 else ""
        print(f"  {i+1:>3d}  {r['sl']:>4.0f}  {r['tp']:>4.0f}  "
              f"{r['trades']:>6d}  {r['wr']:>5.1f}%  "
              f"{r['avg']:>+7.3f}%  "
              f"{pnl_mark}{r['pnl']:>9.0f}%  "
              f"${r['profit_10k']:>+10,.0f}")

    print(f"\n{'='*85}")


if __name__ == "__main__":
    run_backtest()
