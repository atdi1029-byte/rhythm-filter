"""Combined Backtest: Rhythm Filter + RSI Trendline Sell confirmation.

Hypothesis: Only short coins where BOTH signals agree:
  1. Rhythm Filter breathing score fires SHORT (market-wide)
  2. RSI Trendline Sell indicator fired on that coin recently (per-coin)

Tests multiple confirmation windows to find the sweet spot.
RSI sell indicator runs on 4H-resampled data (matching TradingView usage),
trades are simulated on 5-min candles for precision.
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

# Breathing score params (same as backtest_5m.py)
RSI_LEN = 14
BUY_ZONE = 40.0
SELL_ZONE = 60.0
EMA_SMOOTH = 9
TOTAL_COINS = 40
SHORT_THRESHOLD = -5.0
SHORT_COOLDOWN = 12

# Trade params
SL_PCT = 3.0
TP_PCT = 5.0
MAX_HOLD_BARS = 2016  # 7 days on 5min

# Confirmation windows to test (in 5-min bars)
# 48 = 4h, 96 = 8h, 144 = 12h, 288 = 24h, 576 = 48h
WINDOWS = [48, 96, 144, 288, 576]


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
    """Detect SHORT signals from breathing score."""
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


def resample_to_4h(candles_5m):
    """Resample 5-min candles to 4H candles.
    Groups by 48 bars (48 x 5min = 240min = 4H).
    Returns list of 4H candles with time mapped to first 5m bar's time.
    """
    candles_4h = []
    for i in range(0, len(candles_5m) - 47, 48):
        chunk = candles_5m[i:i + 48]
        candle = {
            "time": chunk[0]["time"],
            "open": chunk[0]["open"],
            "high": max(c["high"] for c in chunk),
            "low": min(c["low"] for c in chunk),
            "close": chunk[-1]["close"],
            "volume": sum(c["volume"] for c in chunk),
        }
        candles_4h.append(candle)
    return candles_4h


def sim_trade(signal_bar, coin_lookup, btc_times, n_bars,
              sl_pct, tp_pct):
    """Simulate a SHORT trade. Returns (pnl%, outcome)."""
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

    # Timeout
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
            print(f"  {coin}: {len(candles)} candles")
        else:
            print(f"  {coin}: NO DATA")

    if "btcusdt" not in all_candles:
        print("ERROR: No BTC data!")
        return

    # Master timeline from BTC
    btc_candles = all_candles["btcusdt"]
    btc_times = [c["time"] for c in btc_candles]
    n_bars = len(btc_times)

    coin_lookups = {}
    for coin, candles in all_candles.items():
        coin_lookups[coin] = {c["time"]: c for c in candles}

    first = datetime.utcfromtimestamp(btc_times[0])
    last = datetime.utcfromtimestamp(btc_times[-1])
    print(f"\n{len(all_candles)} coins | {n_bars} bars | "
          f"{first:%Y-%m-%d} -> {last:%Y-%m-%d}")

    # === STEP 1: Compute breathing score + signals ===
    print("\nComputing breathing score...")
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
    print(f"{len(signal_bars)} breathing SHORT signals found")

    # === STEP 2: Run RSI Trendline Sell on 4H resampled data ===
    print("\nRunning RSI Trendline Sell indicator on 4H data...")
    # For each coin, get sell signal timestamps
    coin_sell_times = {}  # coin -> set of 5m timestamps near sell signals
    coin_sell_count = {}

    for coin in all_candles:
        candles_5m = all_candles[coin]
        candles_4h = resample_to_4h(candles_5m)

        if len(candles_4h) < 50:
            coin_sell_times[coin] = set()
            coin_sell_count[coin] = 0
            continue

        sell_signals = scan_all_sell_signals(candles_4h)
        coin_sell_count[coin] = len(sell_signals)

        # Map each 4H sell signal time back to the 5m timeline
        # Store the 5m timestamp of the 4H bar that fired
        sell_ts = set()
        for sig in sell_signals:
            sell_ts.add(sig["time"])
        coin_sell_times[coin] = sell_ts

    total_sell_sigs = sum(coin_sell_count.values())
    coins_with_sigs = sum(1 for v in coin_sell_count.values() if v > 0)
    print(f"{total_sell_sigs} total RSI sell signals across "
          f"{coins_with_sigs} coins")
    for coin in sorted(coin_sell_count.keys()):
        if coin_sell_count[coin] > 0:
            print(f"  {coin}: {coin_sell_count[coin]} sell signals")

    # Build a lookup: for each coin, for each 5m bar index,
    # how many bars ago was the last RSI sell signal?
    # This is more efficient than scanning on every trade check.
    print("\nBuilding sell signal proximity maps...")
    coin_sell_proximity = {}  # coin -> list[int] bars since last sell

    for coin in all_candles:
        sell_times = coin_sell_times[coin]
        candles_5m = all_candles[coin]

        # Map sell signal timestamps to 5m bar indices
        sell_5m_bars = set()
        for idx, c in enumerate(candles_5m):
            if c["time"] in sell_times:
                sell_5m_bars.add(idx)

        # Also map via btc_times index
        proximity = [999999] * n_bars
        last_sell = -999999
        lookup = coin_lookups[coin]

        for i in range(n_bars):
            ts = btc_times[i]
            if ts in lookup:
                # Check if this timestamp matches a sell signal
                if ts in sell_times:
                    last_sell = i
            proximity[i] = i - last_sell

        coin_sell_proximity[coin] = proximity

    # === STEP 3: Run baseline (unfiltered) + filtered backtests ===
    print(f"\n{'='*75}")
    print(f"  COMBINED BACKTEST — {SL_PCT}% SL / {TP_PCT}% TP")
    print(f"  {first:%Y-%m-%d} -> {last:%Y-%m-%d} | "
          f"{len(signal_bars)} breathing signals")
    print(f"{'='*75}")

    # Baseline: no filter (same as backtest_5m.py)
    results = {}  # window_name -> {trades, wins, losses, pnl, coin_data}

    test_configs = [("BASELINE (no filter)", None)] + [
        (f"RSI Sell within {w // 48}x4H ({w} bars)", w)
        for w in WINDOWS
    ]

    for name, window in test_configs:
        wins = 0
        losses = 0
        total_pnl = 0.0
        total_trades = 0
        coin_data = {}

        for coin in sorted(all_candles.keys()):
            lookup = coin_lookups[coin]
            c_wins = 0
            c_losses = 0
            c_pnl = 0.0

            proximity = coin_sell_proximity.get(coin)

            for bar in signal_bars:
                # Apply confirmation filter
                if window is not None and proximity is not None:
                    if proximity[bar] > window:
                        continue  # Skip: no recent RSI sell signal

                pnl, outcome = sim_trade(
                    bar, lookup, btc_times, n_bars,
                    SL_PCT, TP_PCT)
                if pnl is None:
                    continue
                c_pnl += pnl
                if pnl > 0:
                    c_wins += 1
                else:
                    c_losses += 1

            c_trades = c_wins + c_losses
            if c_trades > 0:
                coin_data[coin] = {
                    "trades": c_trades, "wins": c_wins,
                    "losses": c_losses, "pnl": c_pnl,
                    "wr": c_wins / c_trades * 100,
                    "avg": c_pnl / c_trades,
                }
            wins += c_wins
            losses += c_losses
            total_pnl += c_pnl
            total_trades += c_trades

        results[name] = {
            "trades": total_trades, "wins": wins,
            "losses": losses, "pnl": total_pnl,
            "coin_data": coin_data,
        }

    # === STEP 4: Print comparison ===
    print(f"\n{'='*75}")
    print(f"  RESULTS COMPARISON")
    print(f"{'='*75}")
    print(f"\n  {'Config':<35s}  {'Trades':>6s}  {'Wins':>5s}  "
          f"{'Losses':>6s}  {'WR%':>6s}  {'Total P&L':>10s}  "
          f"{'Avg/Trade':>9s}")
    print(f"  {'─'*80}")

    baseline_pnl = None
    for name, data in results.items():
        trades = data["trades"]
        wins = data["wins"]
        losses = data["losses"]
        pnl = data["pnl"]

        if trades == 0:
            print(f"  {name:<35s}  {'N/A':>6s}")
            continue

        wr = wins / trades * 100
        avg = pnl / trades

        if baseline_pnl is None:
            baseline_pnl = pnl
            delta = ""
        else:
            diff = pnl - baseline_pnl
            delta = f"  ({diff:+.0f}%)"

        pnl_mark = "+" if pnl >= 0 else ""
        print(f"  {name:<35s}  {trades:>6d}  {wins:>5d}  "
              f"{losses:>6d}  {wr:>5.1f}%  "
              f"{pnl_mark}{pnl:>9.0f}%  "
              f"{avg:>+7.3f}%{delta}")

    # === STEP 5: Per-coin detail for best filter ===
    # Find the filter with highest avg P&L per trade
    best_name = None
    best_avg = -999
    for name, data in results.items():
        if "BASELINE" in name:
            continue
        if data["trades"] > 0:
            avg = data["pnl"] / data["trades"]
            if avg > best_avg:
                best_avg = avg
                best_name = name

    if best_name:
        print(f"\n{'='*75}")
        print(f"  BEST FILTER: {best_name}")
        print(f"{'='*75}")
        coin_data = results[best_name]["coin_data"]
        sorted_coins = sorted(coin_data.items(),
                              key=lambda x: x[1]["pnl"], reverse=True)

        print(f"\n  {'Coin':>12s}  {'Trades':>6s}  {'Wins':>5s}  "
              f"{'Losses':>6s}  {'WR%':>5s}  {'TotalP&L':>9s}  "
              f"{'AvgP&L':>7s}")
        print(f"  {'─'*60}")

        for coin, d in sorted_coins:
            pnl_mark = "+" if d["pnl"] >= 0 else ""
            print(f"  {coin:>12s}  {d['trades']:>6d}  {d['wins']:>5d}  "
                  f"{d['losses']:>6d}  {d['wr']:>4.0f}%  "
                  f"{pnl_mark}{d['pnl']:>8.1f}%  "
                  f"{d['avg']:>+6.2f}%")

        pos = sum(1 for _, d in sorted_coins if d["pnl"] > 0)
        neg = sum(1 for _, d in sorted_coins if d["pnl"] <= 0)
        print(f"\n  Profitable coins: {pos}/{len(sorted_coins)}")
        print(f"  Negative coins:   {neg}/{len(sorted_coins)}")

    # Also show baseline per-coin for comparison
    print(f"\n{'='*75}")
    print(f"  BASELINE PER-COIN (for comparison)")
    print(f"{'='*75}")
    baseline_coins = results["BASELINE (no filter)"]["coin_data"]
    sorted_baseline = sorted(baseline_coins.items(),
                             key=lambda x: x[1]["pnl"], reverse=True)
    print(f"\n  {'Coin':>12s}  {'Trades':>6s}  {'WR%':>5s}  "
          f"{'TotalP&L':>9s}  {'AvgP&L':>7s}")
    print(f"  {'─'*50}")
    for coin, d in sorted_baseline[:10]:
        pnl_mark = "+" if d["pnl"] >= 0 else ""
        print(f"  {coin:>12s}  {d['trades']:>6d}  {d['wr']:>4.0f}%  "
              f"{pnl_mark}{d['pnl']:>8.1f}%  {d['avg']:>+6.2f}%")
    print(f"  ...")
    for coin, d in sorted_baseline[-5:]:
        pnl_mark = "+" if d["pnl"] >= 0 else ""
        print(f"  {coin:>12s}  {d['trades']:>6d}  {d['wr']:>4.0f}%  "
              f"{pnl_mark}{d['pnl']:>8.1f}%  {d['avg']:>+6.2f}%")

    pos = sum(1 for _, d in sorted_baseline if d["pnl"] > 0)
    neg = sum(1 for _, d in sorted_baseline if d["pnl"] <= 0)
    print(f"\n  Profitable coins: {pos}/{len(sorted_baseline)}")

    print(f"\n{'='*75}")


if __name__ == "__main__":
    run_backtest()
