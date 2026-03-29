"""Per-coin analysis for tier classification.

For each coin:
  1. Grid search SL/TP to find optimal settings
  2. Compound sim at 1x and 2x (at 7/12)
  3. Compound sim at optimal SL/TP
  4. WR%, max losing streak, timeout rate

Outputs data/tier_data.json for dashboard.
"""

import json
import os
from datetime import datetime

import config

ALL_COINS = [
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

SL_GRID = [3, 4, 5, 6, 7, 8]
TP_GRID = [5, 6, 7, 8, 9, 10, 12, 15]


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


def sim_trades(signal_bars, coin_lookup, btc_times, n_bars, sl_pct, tp_pct):
    """Return list of (pnl_pct, outcome) for all signals."""
    trades = []
    for bar in signal_bars:
        ts = btc_times[bar]
        if ts not in coin_lookup:
            continue
        entry = coin_lookup[ts]["close"]
        if entry == 0:
            continue

        sl_price = entry * (1 + sl_pct / 100)
        tp_price = entry * (1 - tp_pct / 100)
        end_bar = min(bar + MAX_HOLD_BARS, n_bars)

        hit = False
        for j in range(bar + 1, end_bar):
            ts_j = btc_times[j]
            if ts_j not in coin_lookup:
                continue
            candle = coin_lookup[ts_j]
            if candle["high"] >= sl_price:
                trades.append((-sl_pct, "SL"))
                hit = True
                break
            if candle["low"] <= tp_price:
                trades.append((tp_pct, "TP"))
                hit = True
                break

        if not hit:
            ts_end = btc_times[end_bar - 1]
            if ts_end in coin_lookup:
                close_price = coin_lookup[ts_end]["close"]
                pnl = (entry - close_price) / entry * 100
                trades.append((pnl, "TIMEOUT"))
            else:
                trades.append((0, "TIMEOUT"))

    return trades


def compound(trades, leverage=1):
    balance = 1.0
    for pnl_pct, outcome in trades:
        lev_pnl = pnl_pct * leverage / 100
        balance *= (1 + lev_pnl)
        if balance <= 0:
            return 0.0
    return balance


def max_losing_streak(trades):
    streak = 0
    max_s = 0
    for pnl, _ in trades:
        if pnl <= 0:
            streak += 1
            if streak > max_s:
                max_s = streak
        else:
            streak = 0
    return max_s


def fmt(val):
    if val >= 1_000_000_000:
        return f"${val/1_000_000_000:.1f}B"
    if val >= 1_000_000:
        return f"${val/1_000_000:.1f}M"
    if val >= 1_000:
        return f"${val/1_000:.1f}K"
    if val >= 1:
        return f"${val:.2f}"
    return f"${val:.4f}"


def run():
    print("Loading 5min data...")
    all_candles = {}
    for coin in ALL_COINS:
        candles = load_5m(coin)
        if candles:
            all_candles[coin] = candles

    btc_candles = all_candles["btcusdt"]
    btc_times = [c["time"] for c in btc_candles]
    n_bars = len(btc_times)
    coin_lookups = {}
    for coin, candles in all_candles.items():
        coin_lookups[coin] = {c["time"]: c for c in candles}

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
    signal_bars = get_signals(breath_scores, n_bars)
    print(f"{len(signal_bars)} signals\n")

    # Analyze each coin
    results = []
    for coin in ALL_COINS:
        if coin not in coin_lookups:
            continue
        lookup = coin_lookups[coin]
        name = coin.replace("usdt", "").upper()
        print(f"  Analyzing {name}...", end="", flush=True)

        # Default 7/12 trades
        trades_712 = sim_trades(signal_bars, lookup, btc_times, n_bars, 7.0, 12.0)
        if not trades_712:
            print(" no trades")
            continue

        total = len(trades_712)
        wins_712 = sum(1 for p, o in trades_712 if p > 0)
        wr_712 = wins_712 / total * 100
        timeouts_712 = sum(1 for p, o in trades_712 if o == "TIMEOUT")
        to_rate = timeouts_712 / total * 100
        streak = max_losing_streak(trades_712)
        bal_1x = compound(trades_712, 1)
        bal_2x = compound(trades_712, 2)

        # Grid search for optimal SL/TP (by compound growth at 1x)
        best_sl = 7
        best_tp = 12
        best_bal = bal_1x

        for sl in SL_GRID:
            for tp in TP_GRID:
                if tp <= sl:
                    continue
                trades = sim_trades(signal_bars, lookup, btc_times, n_bars, sl, tp)
                bal = compound(trades, 1)
                if bal > best_bal:
                    best_bal = bal
                    best_sl = sl
                    best_tp = tp

        # Get stats at optimal settings
        if best_sl != 7 or best_tp != 12:
            trades_opt = sim_trades(signal_bars, lookup, btc_times, n_bars, best_sl, best_tp)
            wins_opt = sum(1 for p, o in trades_opt if p > 0)
            wr_opt = wins_opt / len(trades_opt) * 100
            bal_opt = compound(trades_opt, 1)
        else:
            wr_opt = wr_712
            bal_opt = bal_1x

        # Leverage classification
        if bal_2x > bal_1x:
            lev_tier = "2x_safe"
        elif bal_2x >= 0.10:
            lev_tier = "2x_ok"
        elif bal_1x >= 1.0:
            lev_tier = "1x_only"
        else:
            lev_tier = "avoid"

        entry = {
            "coin": coin,
            "name": name,
            "trades": total,
            "wr_712": round(wr_712, 1),
            "timeout_rate": round(to_rate, 1),
            "max_streak": streak,
            "bal_1x_712": bal_1x,
            "bal_2x_712": bal_2x,
            "optimal_sl": best_sl,
            "optimal_tp": best_tp,
            "wr_optimal": round(wr_opt, 1),
            "bal_1x_optimal": bal_opt,
            "lev_tier": lev_tier,
        }
        results.append(entry)

        print(f"  7/12: {fmt(bal_1x)} | best: {best_sl}/{best_tp} = {fmt(bal_opt)} | {lev_tier}")

    # Sort by 1x compound balance at 7/12
    results.sort(key=lambda r: r["bal_1x_712"], reverse=True)

    # Take top 25
    top25 = results[:25]

    # Print summary table
    print(f"\n{'='*110}")
    print(f"  TOP 25 COINS — TIER ANALYSIS")
    print(f"{'='*110}")
    print(f"\n  {'#':>2s}  {'Coin':>6s}  {'WR%':>5s}  {'TO%':>4s}  "
          f"{'Streak':>6s}  {'$1@1x':>10s}  {'$1@2x':>10s}  "
          f"{'BestSL/TP':>9s}  {'$1@opt':>10s}  {'Lev':>8s}")
    print(f"  {'─'*95}")

    for i, r in enumerate(top25):
        print(f"  {i+1:>2d}  {r['name']:>6s}  {r['wr_712']:>4.1f}%  "
              f"{r['timeout_rate']:>3.0f}%  {r['max_streak']:>5d}x  "
              f"{fmt(r['bal_1x_712']):>10s}  {fmt(r['bal_2x_712']):>10s}  "
              f"{r['optimal_sl']:>2d}/{r['optimal_tp']:>2d}%     "
              f"{fmt(r['bal_1x_optimal']):>10s}  {r['lev_tier']:>8s}")

    # Save JSON
    out_path = os.path.join(config.DATA_DIR, "tier_data.json")
    # Clean up floats for JSON (avoid huge numbers)
    for r in top25:
        for key in ["bal_1x_712", "bal_2x_712", "bal_1x_optimal"]:
            if r[key] > 1e15:
                r[key] = round(r[key] / 1e9, 2)
                r[key + "_unit"] = "B"
            elif r[key] > 1e12:
                r[key] = round(r[key] / 1e9, 2)
                r[key + "_unit"] = "B"
            elif r[key] > 1e9:
                r[key] = round(r[key] / 1e9, 2)
                r[key + "_unit"] = "B"
            elif r[key] > 1e6:
                r[key] = round(r[key] / 1e6, 2)
                r[key + "_unit"] = "M"
            elif r[key] > 1e3:
                r[key] = round(r[key] / 1e3, 2)
                r[key + "_unit"] = "K"
            else:
                r[key] = round(r[key], 2)
                r[key + "_unit"] = ""

    with open(out_path, "w") as f:
        json.dump(top25, f, indent=2)

    print(f"\n  Saved to {out_path}")
    print(f"\n  Tier distribution:")
    for tier in ["2x_safe", "2x_ok", "1x_only", "avoid"]:
        count = sum(1 for r in top25 if r["lev_tier"] == tier)
        names = [r["name"] for r in top25 if r["lev_tier"] == tier]
        print(f"    {tier:>8s}: {count} — {', '.join(names)}")


if __name__ == "__main__":
    run()
