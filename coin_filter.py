"""Coin Filter: Test 6% SL / 10% TP on every coin individually.

Outputs a ranked list of coins by P&L — only keep winners for the bot.
Also tests 7/12 for comparison.
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
MAX_HOLD_BARS = 2016  # 7 days on 5min
MIN_CANDLES = 50000


def load_all_5m():
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


def test_coin(coin, lookup, signal_bars, btc_times, n_bars, sl, tp):
    """Test one coin at given SL/TP. Returns stats dict."""
    wins = 0
    losses = 0
    pnl = 0.0
    trades = 0
    streak = 0
    max_loss_streak = 0

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
        trades += 1

        hit = False
        for j in range(bar + 1, end_bar):
            ts_j = btc_times[j]
            if ts_j not in lookup:
                continue
            c = lookup[ts_j]
            if c["high"] >= sl_price:
                pnl -= sl
                losses += 1
                streak = min(streak - 1, -1)
                max_loss_streak = min(max_loss_streak, streak)
                hit = True
                break
            if c["low"] <= tp_price:
                pnl += tp
                wins += 1
                streak = max(streak + 1, 1)
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
                    streak = max(streak + 1, 1)
                else:
                    losses += 1
                    streak = min(streak - 1, -1)
                    max_loss_streak = min(max_loss_streak, streak)

    if trades == 0:
        return None

    wr = wins / trades * 100
    avg = pnl / trades

    # Kelly criterion: f* = (bp - q) / b
    # b = tp/sl (reward/risk ratio), p = win rate, q = 1-p
    b = tp / sl
    p = wins / trades
    q = 1 - p
    kelly = (b * p - q) / b if b > 0 else 0
    kelly = max(kelly, 0)  # never negative

    return {
        "coin": coin,
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "wr": wr,
        "pnl": pnl,
        "avg_pnl": avg,
        "kelly": kelly,
        "max_loss_streak": abs(max_loss_streak),
    }


def run():
    print("Loading all 5min data...")
    all_candles = load_all_5m()

    btc_candles = all_candles["btcusdt"]
    btc_times = [c["time"] for c in btc_candles]
    n_bars = len(btc_times)

    coin_lookups = {}
    for coin, candles in all_candles.items():
        coin_lookups[coin] = {c["time"]: c for c in candles}

    n_coins = len(all_candles)
    first = datetime.utcfromtimestamp(btc_times[0])
    last = datetime.utcfromtimestamp(btc_times[-1])
    print(f"{n_coins} coins | {n_bars} bars | "
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
        raw_scores.append(score * 20.0 / n_coins if valid > 0 else 0.0)

    breath_scores = compute_ema(raw_scores, EMA_SMOOTH)
    signal_bars = get_signals(breath_scores, n_bars)
    print(f"{len(signal_bars)} signals\n")

    # Test both SL/TP settings
    for sl, tp in [(6, 10), (7, 12)]:
        print(f"{'='*78}")
        print(f"  COIN FILTER: {sl}% SL / {tp}% TP")
        print(f"  {len(signal_bars)} signals x {n_coins} coins")
        print(f"{'='*78}\n")

        results = []
        for coin in all_candles:
            r = test_coin(coin, coin_lookups[coin], signal_bars,
                          btc_times, n_bars, sl, tp)
            if r:
                results.append(r)

        results.sort(key=lambda r: r["pnl"], reverse=True)

        winners = [r for r in results if r["pnl"] > 0]
        losers = [r for r in results if r["pnl"] <= 0]

        # Print winners
        print(f"  WINNERS ({len(winners)} coins):")
        print(f"  {'Coin':>14s}  {'WR%':>4s}  {'P&L':>9s}  "
              f"{'Avg':>7s}  {'Kelly':>6s}  {'MaxLS':>5s}  {'Trades':>6s}")
        print(f"  {'─'*62}")

        total_winner_pnl = 0
        total_winner_trades = 0
        total_winner_wins = 0

        for r in winners:
            total_winner_pnl += r["pnl"]
            total_winner_trades += r["trades"]
            total_winner_wins += r["wins"]
            print(f"  {r['coin']:>14s}  {r['wr']:>4.0f}%  "
                  f"{r['pnl']:>+8.1f}%  {r['avg_pnl']:>+6.3f}%  "
                  f"{r['kelly']:>5.1f}%  {r['max_loss_streak']:>5d}  "
                  f"{r['trades']:>6d}")

        print(f"\n  LOSERS ({len(losers)} coins):")
        print(f"  {'─'*62}")
        for r in losers:
            print(f"  {r['coin']:>14s}  {r['wr']:>4.0f}%  "
                  f"{r['pnl']:>+8.1f}%  {r['avg_pnl']:>+6.3f}%  "
                  f"{r['kelly']:>5.1f}%  {r['max_loss_streak']:>5d}  "
                  f"{r['trades']:>6d}")

        # Summary
        winner_wr = (total_winner_wins / total_winner_trades * 100
                     if total_winner_trades > 0 else 0)
        winner_avg = (total_winner_pnl / total_winner_trades
                      if total_winner_trades > 0 else 0)

        print(f"\n  {'='*62}")
        print(f"  SUMMARY ({sl}% SL / {tp}% TP):")
        print(f"  Winners: {len(winners)} coins")
        print(f"  Losers:  {len(losers)} coins")
        print(f"  Winners-only total P&L:  {total_winner_pnl:+.1f}%")
        print(f"  Winners-only avg/trade:  {winner_avg:+.3f}%")
        print(f"  Winners-only WR:         {winner_wr:.1f}%")
        print(f"  Winners-only trades:     {total_winner_trades}")

        # Save winners list
        coin_list = [r["coin"] for r in winners]
        outfile = os.path.join(config.DATA_DIR,
                               f"approved_coins_{sl}_{tp}.json")
        with open(outfile, "w") as f:
            json.dump(coin_list, f, indent=2)
        print(f"\n  Approved list saved: {outfile}")

        # Save full results
        detail_file = os.path.join(config.DATA_DIR,
                                   f"coin_results_{sl}_{tp}.json")
        with open(detail_file, "w") as f:
            json.dump(results, f, indent=2)
        print(f"  Full results saved: {detail_file}")

        print()


if __name__ == "__main__":
    run()
