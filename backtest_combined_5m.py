"""Combined Backtest: Rhythm Filter + RSI Sell on 5-MIN data.

Same idea but runs the RSI sell indicator directly on 5m candles
instead of resampling to 4H. This produces way more per-coin sell
signals, so more trades pass the confirmation filter.
"""

import json
import os
from datetime import datetime

import numpy as np

import config
from indicator_sell import scan_all_sell_signals

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

# Test windows in 5m bars
# 12 = 1hr, 36 = 3hr, 72 = 6hr, 144 = 12hr, 288 = 24hr, 576 = 48hr
WINDOWS = [12, 36, 72, 144, 288, 576]

# Best SL/TP combos from baseline grid
SL_TP_COMBOS = [
    (3, 5),    # original
    (6, 10),   # baseline sweet spot
    (8, 15),   # baseline best total P&L
    (8, 12),   # baseline #2
    (5, 10),   # mid range
    (6, 15),   # baseline #3
]


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

    # Run RSI sell on 5m data directly
    print("\nRunning RSI Trendline Sell on 5-MIN data (this takes a minute)...")
    coin_sell_times = {}
    coin_sell_count = {}

    for coin in all_candles:
        candles = all_candles[coin]
        print(f"  {coin}: scanning {len(candles)} candles...", end="", flush=True)
        sell_signals = scan_all_sell_signals(candles)
        coin_sell_count[coin] = len(sell_signals)
        coin_sell_times[coin] = set(s["time"] for s in sell_signals)
        print(f" {len(sell_signals)} signals")

    total_sigs = sum(coin_sell_count.values())
    print(f"\n{total_sigs} total RSI sell signals on 5m data")

    # Proximity maps
    print("Building proximity maps...")
    coin_sell_proximity = {}
    for coin in all_candles:
        sell_times = coin_sell_times[coin]
        proximity = [999999] * n_bars
        last_sell = -999999
        lookup = coin_lookups[coin]
        for i in range(n_bars):
            ts = btc_times[i]
            if ts in lookup and ts in sell_times:
                last_sell = i
            proximity[i] = i - last_sell
        coin_sell_proximity[coin] = proximity

    # === RESULTS ===
    print(f"\n{'='*90}")
    print(f"  5-MIN RSI SELL FILTER — Rhythm Filter + RSI Sell on 5m")
    print(f"  {first:%Y-%m-%d} -> {last:%Y-%m-%d} | "
          f"{len(signal_bars)} breathing signals")
    print(f"{'='*90}")

    # Run all combos
    print(f"\n  {'Window':>8s}  {'SL':>3s}  {'TP':>3s}  {'Trades':>7s}  "
          f"{'Wins':>6s}  {'Losses':>6s}  {'WR%':>6s}  "
          f"{'Total P&L':>10s}  {'Avg/Trade':>9s}  "
          f"{'$10K Profit':>11s}")
    print(f"  {'─'*85}")

    # Baseline first
    for sl, tp in SL_TP_COMBOS:
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
        pnl_mark = "+" if total_pnl >= 0 else ""
        print(f"  {'BASELINE':>8s}  {sl:>3.0f}  {tp:>3.0f}  {trades:>7d}  "
              f"{wins:>6d}  {losses:>6d}  {wr:>5.1f}%  "
              f"{pnl_mark}{total_pnl:>9.0f}%  "
              f"{avg:>+7.3f}%  "
              f"${profit_10k:>+10,.0f}")

    print(f"  {'─'*85}")

    all_results = []

    for window in WINDOWS:
        if window < 60:
            label = f"{window*5}min"
        else:
            label = f"{window*5//60}hr"

        for sl, tp in SL_TP_COMBOS:
            wins = 0
            losses = 0
            total_pnl = 0.0

            for coin in all_candles:
                lookup = coin_lookups[coin]
                proximity = coin_sell_proximity.get(coin)

                for bar in signal_bars:
                    if proximity is not None and proximity[bar] > window:
                        continue
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
                "window": label, "window_bars": window,
                "sl": sl, "tp": tp,
                "trades": trades, "wins": wins,
                "losses": losses, "wr": wr,
                "pnl": total_pnl, "avg": avg,
                "profit_10k": profit_10k,
            })

            pnl_mark = "+" if total_pnl >= 0 else ""
            print(f"  {label:>8s}  {sl:>3.0f}  {tp:>3.0f}  {trades:>7d}  "
                  f"{wins:>6d}  {losses:>6d}  {wr:>5.1f}%  "
                  f"{pnl_mark}{total_pnl:>9.0f}%  "
                  f"{avg:>+7.3f}%  "
                  f"${profit_10k:>+10,.0f}")
        if window != WINDOWS[-1]:
            print()

    # Top 15 by total P&L
    print(f"\n{'='*90}")
    print(f"  TOP 15 BY TOTAL P&L (filtered vs baseline)")
    print(f"{'='*90}")
    top = sorted(all_results, key=lambda r: r["pnl"], reverse=True)[:15]
    print(f"\n  {'#':>3s}  {'Window':>8s}  {'SL':>3s}  {'TP':>3s}  "
          f"{'Trades':>7s}  {'WR%':>6s}  {'Avg':>7s}  "
          f"{'Total P&L':>10s}  {'$10K Profit':>11s}")
    print(f"  {'─'*70}")
    for i, r in enumerate(top):
        pnl_mark = "+" if r["pnl"] >= 0 else ""
        print(f"  {i+1:>3d}  {r['window']:>8s}  {r['sl']:>3.0f}  "
              f"{r['tp']:>3.0f}  {r['trades']:>7d}  {r['wr']:>5.1f}%  "
              f"{r['avg']:>+5.3f}%  "
              f"{pnl_mark}{r['pnl']:>9.0f}%  "
              f"${r['profit_10k']:>+10,.0f}")

    print(f"\n{'='*90}")


if __name__ == "__main__":
    run_backtest()
