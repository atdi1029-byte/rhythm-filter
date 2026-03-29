"""Backtest: Rhythm Filter v5 on 5-min — expanded coin set.

Loads ALL available 5-min data files and runs the SL/TP grid.
Tests whether the edge holds with more coins.
Also tracks per-coin P&L for blacklisting losers.
"""

import json
import os
from datetime import datetime

import config

RSI_LEN = 14
BUY_ZONE = 40.0
SELL_ZONE = 60.0
EMA_SMOOTH = 9
SHORT_THRESHOLD = -5.0
SHORT_COOLDOWN = 12

# Grid — wide sweep
SL_LEVELS = [3, 4, 5, 6, 7, 8]
TP_LEVELS = [5, 7, 8, 9, 10, 12, 15]
MAX_HOLD_BARS = 2016  # 7 days on 5min
MIN_CANDLES = 50000   # skip coins with less than ~2 months of data


def load_all_5m():
    """Load all available 5-min data files."""
    data_dir = os.path.join(config.DATA_DIR, "5m")
    all_candles = {}
    for f in sorted(os.listdir(data_dir)):
        if not f.endswith("_5m.json"):
            continue
        coin = f.replace("_5m.json", "")
        filepath = os.path.join(data_dir, f)
        with open(filepath) as fh:
            candles = json.load(fh)
        if len(candles) >= MIN_CANDLES:
            all_candles[coin] = candles
    return all_candles


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
    print("Loading ALL 5min data files...")
    all_candles = load_all_5m()

    if "btcusdt" not in all_candles:
        print("ERROR: No BTC data!")
        return

    n_coins = len(all_candles)
    print(f"Loaded {n_coins} coins (min {MIN_CANDLES} candles each)")

    btc_candles = all_candles["btcusdt"]
    btc_times = [c["time"] for c in btc_candles]
    n_bars = len(btc_times)

    coin_lookups = {}
    for coin, candles in all_candles.items():
        coin_lookups[coin] = {c["time"]: c for c in candles}

    first = datetime.utcfromtimestamp(btc_times[0])
    last = datetime.utcfromtimestamp(btc_times[-1])
    print(f"{n_bars} bars | {first:%Y-%m-%d} -> {last:%Y-%m-%d}")

    # Compute breathing score using ALL coins
    print(f"Computing breathing score on 5min ({n_coins} coins)...")
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
        raw_scores.append(score * 20.0 / n_coins if valid > 0 else 0.0)

    breath_scores = compute_ema(raw_scores, EMA_SMOOTH)

    signal_bars = get_signals(breath_scores, n_bars)
    print(f"{len(signal_bars)} SHORT signals found\n")

    # === GRID TEST ===
    print(f"{'='*78}")
    print(f"  5-MIN SL/TP GRID — {n_coins} COINS")
    print(f"  {first:%Y-%m-%d} -> {last:%Y-%m-%d}")
    print(f"  {len(signal_bars)} signals x {n_coins} coins")
    print(f"{'='*78}")

    results = []

    for sl in SL_LEVELS:
        for tp in TP_LEVELS:
            total_wins = 0
            total_losses = 0
            total_pnl = 0.0
            coins_positive = 0
            coins_negative = 0

            for coin in all_candles:
                lookup = coin_lookups[coin]
                coin_pnl = 0.0

                for bar in signal_bars:
                    ts = btc_times[bar]
                    if ts not in lookup:
                        continue
                    entry = lookup[ts]["close"]
                    if entry == 0:
                        continue

                    sl_price = entry * (1 + sl / 100)
                    tp_price = entry * (1 - tp / 100)
                    end_bar = min(bar + MAX_HOLD_BARS, n_bars)

                    hit = False
                    for j in range(bar + 1, end_bar):
                        ts_j = btc_times[j]
                        if ts_j not in lookup:
                            continue
                        c = lookup[ts_j]
                        if c["high"] >= sl_price:
                            total_pnl -= sl
                            coin_pnl -= sl
                            total_losses += 1
                            hit = True
                            break
                        if c["low"] <= tp_price:
                            total_pnl += tp
                            coin_pnl += tp
                            total_wins += 1
                            hit = True
                            break

                    if not hit:
                        ts_end = btc_times[end_bar - 1]
                        if ts_end in lookup:
                            cp = lookup[ts_end]["close"]
                            pnl = (entry - cp) / entry * 100
                            total_pnl += pnl
                            coin_pnl += pnl
                            if pnl > 0:
                                total_wins += 1
                            else:
                                total_losses += 1

                if coin_pnl > 0:
                    coins_positive += 1
                else:
                    coins_negative += 1

            trades = total_wins + total_losses
            if trades == 0:
                continue
            wr = total_wins / trades * 100
            avg = total_pnl / trades

            results.append({
                "sl": sl, "tp": tp,
                "trades": trades, "wins": total_wins,
                "losses": total_losses, "wr": wr,
                "total_pnl": total_pnl, "avg_pnl": avg,
                "coins_pos": coins_positive,
                "coins_neg": coins_negative,
            })

            print(f"  SL={sl}% TP={tp}%  "
                  f"WR={wr:.0f}%  "
                  f"Total={total_pnl:+.1f}%  "
                  f"Avg={avg:+.3f}%  "
                  f"Coins +{coins_positive}/-{coins_negative}")

    # Sort by total P&L
    results.sort(key=lambda r: r["total_pnl"], reverse=True)

    print(f"\n{'='*78}")
    print(f"  TOP 10 COMBOS BY TOTAL P&L")
    print(f"{'='*78}")
    print(f"\n  {'SL':>3s}  {'TP':>3s}  {'Trades':>6s}  "
          f"{'WR%':>4s}  {'TotalP&L':>10s}  {'AvgP&L':>8s}  "
          f"{'Coins+':>6s}  {'Coins-':>6s}")
    print(f"  {'─'*58}")

    for r in results[:10]:
        print(f"  {r['sl']:>2d}%  {r['tp']:>2d}%  {r['trades']:>6d}  "
              f"{r['wr']:>4.0f}%  {r['total_pnl']:>+9.1f}%  "
              f"{r['avg_pnl']:>+7.3f}%  "
              f"{r['coins_pos']:>6d}  {r['coins_neg']:>6d}")

    # Per-coin breakdown for best combo
    if results:
        best = results[0]
        print(f"\n{'='*78}")
        print(f"  BEST: {best['sl']}% SL / {best['tp']}% TP")
        print(f"  WR={best['wr']:.1f}%  Avg={best['avg_pnl']:+.3f}%  "
              f"Total={best['total_pnl']:+.1f}%")
        print(f"{'='*78}")

        coin_detail = []
        for coin in sorted(all_candles.keys()):
            lookup = coin_lookups[coin]
            wins = 0
            losses = 0
            pnl = 0.0
            trade_count = 0

            for bar in signal_bars:
                ts = btc_times[bar]
                if ts not in lookup:
                    continue
                entry = lookup[ts]["close"]
                if entry == 0:
                    continue

                sl_price = entry * (1 + best["sl"] / 100)
                tp_price = entry * (1 - best["tp"] / 100)
                end_bar = min(bar + MAX_HOLD_BARS, n_bars)

                hit = False
                for j in range(bar + 1, end_bar):
                    ts_j = btc_times[j]
                    if ts_j not in lookup:
                        continue
                    c = lookup[ts_j]
                    if c["high"] >= sl_price:
                        pnl -= best["sl"]
                        losses += 1
                        hit = True
                        break
                    if c["low"] <= tp_price:
                        pnl += best["tp"]
                        wins += 1
                        hit = True
                        break
                if not hit:
                    ts_end = btc_times[end_bar - 1]
                    if ts_end in lookup:
                        cp = lookup[ts_end]["close"]
                        p = (entry - cp) / entry * 100
                        pnl += p
                        if p > 0:
                            wins += 1
                        else:
                            losses += 1

                trade_count += 1

            if trade_count > 0:
                coin_detail.append({
                    "coin": coin, "trades": trade_count,
                    "wins": wins, "losses": losses,
                    "wr": wins / (wins + losses) * 100 if (wins + losses) > 0 else 0,
                    "pnl": pnl,
                })

        coin_detail.sort(key=lambda r: r["pnl"], reverse=True)

        # Print all coins
        print(f"\n  PER-COIN BREAKDOWN:")
        print(f"  {'─'*60}")

        blacklist = []
        for r in coin_detail:
            mark = "+" if r["pnl"] > 0 else " "
            flag = " BLACKLIST" if r["pnl"] < 0 else ""
            print(f"    {r['coin']:>14s}  "
                  f"WR={r['wr']:>4.0f}%  "
                  f"P&L={mark}{r['pnl']:>8.1f}%  "
                  f"({r['wins']}W/{r['losses']}L){flag}")
            if r["pnl"] < 0:
                blacklist.append(r["coin"])

        # Summary
        total_positive = sum(1 for r in coin_detail if r["pnl"] > 0)
        total_negative = sum(1 for r in coin_detail if r["pnl"] <= 0)
        print(f"\n  SUMMARY: {total_positive} profitable / "
              f"{total_negative} negative")

        if blacklist:
            print(f"\n  BLACKLIST ({len(blacklist)} coins):")
            for coin in blacklist:
                r = next(x for x in coin_detail if x["coin"] == coin)
                print(f"    {coin}: {r['pnl']:+.1f}%")

        # Save blacklist to file
        bl_file = os.path.join(config.DATA_DIR, "blacklist.json")
        with open(bl_file, "w") as f:
            json.dump(blacklist, f, indent=2)
        print(f"\n  Blacklist saved to {bl_file}")

    print(f"\n{'='*78}")


if __name__ == "__main__":
    run_backtest()
