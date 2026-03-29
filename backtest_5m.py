"""Backtest: Rhythm Filter v5 on 5-min candles.

Tests SHORT signals across all 40 coins individually.
Breathing score is computed from all 40 coins (market-wide signal).
Each coin is traded independently with its own SL/TP.
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
SHORT_THRESHOLD = -3.0
SHORT_COOLDOWN = 12

SL_PCT = 3.0
HALF_TP_PCT = 5.0
MAX_HOLD_BARS = 2016  # 7 days on 5min (7*24*12*60/5... 7*288=2016)


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


def get_signals(breath_scores, n_bars):
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


def sim_trade(signal_bar, coin_lookup, btc_times, n_bars,
              sl_pct, tp_pct):
    """Simulate a SHORT trade on a specific coin. Returns (pnl%, outcome)."""
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

    # Use BTC timestamps as master timeline
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

    # Compute breathing score
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

    # Get signals
    signal_bars = get_signals(breath_scores, n_bars)
    print(f"\n{len(signal_bars)} SHORT signals found")

    # Test each coin
    print(f"\n{'='*70}")
    print(f"  5-MIN BACKTEST — {SL_PCT}% SL / {HALF_TP_PCT}% TP")
    print(f"  {first:%Y-%m-%d} -> {last:%Y-%m-%d} | "
          f"{len(signal_bars)} signals")
    print(f"{'='*70}")

    print(f"\n  {'Coin':>12s}  {'Trades':>6s}  {'Wins':>5s}  "
          f"{'Losses':>6s}  {'WR%':>5s}  {'TotalP&L':>9s}  "
          f"{'AvgP&L':>7s}")
    print(f"  {'─'*60}")

    coin_results = []
    grand_wins = 0
    grand_losses = 0
    grand_pnl = 0.0
    grand_trades = 0

    for coin in sorted(all_candles.keys()):
        lookup = coin_lookups[coin]
        wins = 0
        losses = 0
        total_pnl = 0.0

        for bar in signal_bars:
            pnl, outcome = sim_trade(
                bar, lookup, btc_times, n_bars,
                SL_PCT, HALF_TP_PCT)
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

        coin_results.append({
            "coin": coin, "trades": trades,
            "wins": wins, "losses": losses,
            "wr": wr, "total_pnl": total_pnl, "avg_pnl": avg,
        })

        grand_wins += wins
        grand_losses += losses
        grand_pnl += total_pnl
        grand_trades += trades

        pnl_mark = "+" if total_pnl >= 0 else ""
        print(f"  {coin:>12s}  {trades:>6d}  {wins:>5d}  "
              f"{losses:>6d}  {wr:>4.0f}%  "
              f"{pnl_mark}{total_pnl:>8.1f}%  "
              f"{avg:>+6.2f}%")

    # Sort by P&L
    coin_results.sort(key=lambda r: r["total_pnl"], reverse=True)

    # Summary
    print(f"\n{'='*70}")
    print(f"  GRAND TOTAL")
    print(f"{'='*70}")
    if grand_trades > 0:
        grand_wr = grand_wins / grand_trades * 100
        grand_avg = grand_pnl / grand_trades
        print(f"  Total trades:  {grand_trades}")
        print(f"  Wins:          {grand_wins}")
        print(f"  Losses:        {grand_losses}")
        print(f"  Win Rate:      {grand_wr:.1f}%")
        print(f"  Total P&L:     {grand_pnl:+.1f}%")
        print(f"  Avg per trade: {grand_avg:+.3f}%")

    # Top 10 and bottom 10
    print(f"\n  TOP 10 COINS:")
    for r in coin_results[:10]:
        print(f"    {r['coin']:>12s}  {r['total_pnl']:>+8.1f}%  "
              f"WR={r['wr']:.0f}%  ({r['trades']} trades)")

    print(f"\n  BOTTOM 10 COINS:")
    for r in coin_results[-10:]:
        print(f"    {r['coin']:>12s}  {r['total_pnl']:>+8.1f}%  "
              f"WR={r['wr']:.0f}%  ({r['trades']} trades)")

    # Positive vs negative coins
    pos = [r for r in coin_results if r["total_pnl"] > 0]
    neg = [r for r in coin_results if r["total_pnl"] <= 0]
    print(f"\n  Profitable coins: {len(pos)}/{len(coin_results)}")
    print(f"  Negative coins:   {len(neg)}/{len(coin_results)}")

    print(f"\n{'='*70}")


if __name__ == "__main__":
    run_backtest()
