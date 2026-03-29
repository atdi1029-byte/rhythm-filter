"""Backtest: Rhythm Filter on 5-min — Half-TP grid search.

Strategy per trade:
  1. SHORT entry on breathing score signal
  2. If price drops to half-TP: close half, move SL to breakeven
  3. Runner rides to full TP or trailing stop
  4. If SL hit before half-TP: full loss

Tests grid across all coins.
"""

import json
import os
from datetime import datetime

import config

COINS = [
    "btcusdt", "ethusdt", "xrpusdt", "bnbusdt", "solusdt",
    "dogeusdt", "adausdt", "trxusdt", "avaxusdt", "shibusdt",
    "linkusdt", "suiusdt", "dotusdt", "nearusdt",
    "uniusdt", "aptusdt", "polusdt", "arbusdt", "opusdt",
    "icpusdt", "hbarusdt", "filusdt", "atomusdt", "imxusdt",
    "susdt", "grtusdt", "thetausdt",
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

# Grid
SL_LEVELS = [4, 5, 6]
HALF_TP_LEVELS = [3, 4, 5]
FULL_TP_LEVELS = [8, 10, 12]
TRAIL_LEVELS = [3, 4, 5]
MAX_HOLD_BARS = 2016  # 7 days on 5min


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


def run_backtest():
    print("Loading 5min data...")
    all_candles = {}
    for coin in COINS:
        candles = load_5m(coin)
        if candles:
            all_candles[coin] = candles

    if "btcusdt" not in all_candles:
        print("ERROR: No BTC data! Run fetch_5m.py first.")
        return

    print(f"Loaded {len(all_candles)} coins")

    btc_candles = all_candles["btcusdt"]
    btc_times = [c["time"] for c in btc_candles]
    n_bars = len(btc_times)

    coin_lookups = {}
    for coin, candles in all_candles.items():
        coin_lookups[coin] = {c["time"]: c for c in candles}

    first = datetime.utcfromtimestamp(btc_times[0])
    last = datetime.utcfromtimestamp(btc_times[-1])
    print(f"{n_bars} bars | {first:%Y-%m-%d} -> {last:%Y-%m-%d}")

    # Compute breathing score
    print("Computing breathing score on 5min...")
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

    signal_bars = get_signals(breath_scores, n_bars)
    print(f"{len(signal_bars)} SHORT signals found\n")

    # === HALF-TP GRID ===
    combos = len(SL_LEVELS) * len(HALF_TP_LEVELS) * len(FULL_TP_LEVELS) * len(TRAIL_LEVELS)
    print(f"{'='*78}")
    print(f"  5-MIN HALF-TP GRID — ALL COINS")
    print(f"  {first:%Y-%m-%d} -> {last:%Y-%m-%d}")
    print(f"  {len(signal_bars)} signals x {len(all_candles)} coins")
    print(f"  {combos} SL/HTP/TP/Trail combos to test")
    print(f"{'='*78}\n")

    # Also run flat TP comparison (best from grid: 6% SL / 10% TP)
    flat_pnl = 0.0
    flat_trades = 0
    flat_wins = 0
    for coin in all_candles:
        lookup = coin_lookups[coin]
        for bar in signal_bars:
            ts = btc_times[bar]
            if ts not in lookup:
                continue
            entry = lookup[ts]["close"]
            if entry == 0:
                continue
            sl_price = entry * 1.06
            tp_price = entry * 0.90
            end_bar = min(bar + MAX_HOLD_BARS, n_bars)
            hit = False
            for j in range(bar + 1, end_bar):
                ts_j = btc_times[j]
                if ts_j not in lookup:
                    continue
                c = lookup[ts_j]
                if c["high"] >= sl_price:
                    flat_pnl -= 6
                    flat_trades += 1
                    hit = True
                    break
                if c["low"] <= tp_price:
                    flat_pnl += 10
                    flat_trades += 1
                    flat_wins += 1
                    hit = True
                    break
            if not hit:
                ts_end = btc_times[end_bar - 1]
                if ts_end in lookup:
                    cp = lookup[ts_end]["close"]
                    p = (entry - cp) / entry * 100
                    flat_pnl += p
                    flat_trades += 1
                    if p > 0:
                        flat_wins += 1

    flat_wr = flat_wins / flat_trades * 100 if flat_trades > 0 else 0
    flat_avg = flat_pnl / flat_trades if flat_trades > 0 else 0
    print(f"  BASELINE (flat 6% SL / 10% TP):")
    print(f"  WR={flat_wr:.0f}%  Total={flat_pnl:+.1f}%  "
          f"Avg={flat_avg:+.3f}%  ({flat_trades} trades)\n")

    results = []

    for sl in SL_LEVELS:
        for htp in HALF_TP_LEVELS:
            for ftp in FULL_TP_LEVELS:
                if ftp <= htp:
                    continue  # full TP must be wider than half TP
                for trail in TRAIL_LEVELS:
                    total_wins = 0
                    total_losses = 0
                    total_pnl = 0.0

                    for coin in all_candles:
                        lookup = coin_lookups[coin]
                        for bar in signal_bars:
                            ts = btc_times[bar]
                            if ts not in lookup:
                                continue
                            entry = lookup[ts]["close"]
                            if entry == 0:
                                continue

                            sl_price = entry * (1 + sl / 100)
                            htp_price = entry * (1 - htp / 100)
                            ftp_price = entry * (1 - ftp / 100)
                            end_bar = min(bar + MAX_HOLD_BARS, n_bars)

                            half_taken = False
                            lowest = entry

                            hit = False
                            for j in range(bar + 1, end_bar):
                                ts_j = btc_times[j]
                                if ts_j not in lookup:
                                    continue
                                c = lookup[ts_j]

                                if not half_taken:
                                    # Phase 1: SL or half-TP
                                    if c["high"] >= sl_price:
                                        total_pnl -= sl
                                        total_losses += 1
                                        hit = True
                                        break
                                    if c["low"] <= htp_price:
                                        half_taken = True
                                        lowest = c["low"]
                                        continue
                                else:
                                    # Phase 2: runner
                                    if c["low"] < lowest:
                                        lowest = c["low"]

                                    # Full TP hit
                                    if c["low"] <= ftp_price:
                                        pnl = (htp + ftp) / 2
                                        total_pnl += pnl
                                        total_wins += 1
                                        hit = True
                                        break

                                    # Trailing stop or breakeven
                                    trail_stop = lowest * (1 + trail / 100)
                                    be_stop = entry
                                    eff_stop = min(trail_stop, be_stop)

                                    if c["high"] >= eff_stop:
                                        runner_pnl = (entry - eff_stop) / entry * 100
                                        pnl = (htp + runner_pnl) / 2
                                        total_pnl += pnl
                                        if pnl > 0:
                                            total_wins += 1
                                        else:
                                            total_losses += 1
                                        hit = True
                                        break

                            if not hit:
                                if half_taken:
                                    ts_end = btc_times[end_bar - 1]
                                    if ts_end in lookup:
                                        cp = lookup[ts_end]["close"]
                                        runner_pnl = (entry - cp) / entry * 100
                                        pnl = (htp + runner_pnl) / 2
                                        total_pnl += pnl
                                        if pnl > 0:
                                            total_wins += 1
                                        else:
                                            total_losses += 1
                                else:
                                    ts_end = btc_times[end_bar - 1]
                                    if ts_end in lookup:
                                        cp = lookup[ts_end]["close"]
                                        pnl = (entry - cp) / entry * 100
                                        total_pnl += pnl
                                        if pnl > 0:
                                            total_wins += 1
                                        else:
                                            total_losses += 1

                    trades = total_wins + total_losses
                    if trades == 0:
                        continue
                    wr = total_wins / trades * 100
                    avg = total_pnl / trades

                    results.append({
                        "sl": sl, "htp": htp, "ftp": ftp, "trail": trail,
                        "trades": trades, "wins": total_wins,
                        "losses": total_losses, "wr": wr,
                        "total_pnl": total_pnl, "avg_pnl": avg,
                    })

    results.sort(key=lambda r: r["total_pnl"], reverse=True)

    print(f"\n{'='*78}")
    print(f"  TOP 20 HALF-TP COMBOS BY TOTAL P&L")
    print(f"{'='*78}")
    print(f"\n  {'SL':>3s}  {'HTP':>3s}  {'FTP':>3s}  {'Trail':>5s}  "
          f"{'WR%':>4s}  {'AvgP&L':>8s}  {'TotalP&L':>10s}  {'Trades':>6s}")
    print(f"  {'─'*62}")

    for r in results[:20]:
        beat = ">>>" if r["total_pnl"] > flat_pnl else "   "
        print(f"  {r['sl']:>2d}%  {r['htp']:>2d}%  {r['ftp']:>2d}%  "
              f"{r['trail']:>4d}%  "
              f"{r['wr']:>4.0f}%  {r['avg_pnl']:>+7.3f}%  "
              f"{r['total_pnl']:>+9.1f}%  {r['trades']:>6d} {beat}")

    if results:
        best = results[0]
        print(f"\n{'='*78}")
        print(f"  BEST HALF-TP: {best['sl']}% SL / {best['htp']}% HTP / "
              f"{best['ftp']}% FTP / {best['trail']}% Trail")
        print(f"{'='*78}")
        print(f"  Win Rate:       {best['wr']:.1f}%")
        print(f"  Total P&L:      {best['total_pnl']:+.1f}% "
              f"across {best['trades']} trades")
        print(f"  Avg per trade:  {best['avg_pnl']:+.3f}%")

        diff = best["total_pnl"] - flat_pnl
        pct = diff / abs(flat_pnl) * 100 if flat_pnl != 0 else 0
        if diff > 0:
            print(f"\n  vs FLAT 6/10:   +{diff:.1f}% better ({pct:+.1f}%)")
        else:
            print(f"\n  vs FLAT 6/10:   {diff:.1f}% worse ({pct:+.1f}%)")
            print(f"  >>> FLAT 6% SL / 10% TP wins. Keep it simple.")

    print(f"\n{'='*78}")


if __name__ == "__main__":
    run_backtest()
