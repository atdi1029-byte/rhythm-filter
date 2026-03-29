"""Classify all 40 coins as 2x-safe vs 1x-only based on compound backtest."""

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
SL_PCT = 7.0
TP_PCT = 12.0
MAX_HOLD_BARS = 2016


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


def sim_trade(signal_bar, coin_lookup, btc_times, n_bars):
    ts = btc_times[signal_bar]
    if ts not in coin_lookup:
        return None, None
    entry = coin_lookup[ts]["close"]
    if entry == 0:
        return None, None
    sl_price = entry * (1 + SL_PCT / 100)
    tp_price = entry * (1 - TP_PCT / 100)
    end_bar = min(signal_bar + MAX_HOLD_BARS, n_bars)
    for j in range(signal_bar + 1, end_bar):
        ts_j = btc_times[j]
        if ts_j not in coin_lookup:
            continue
        candle = coin_lookup[ts_j]
        if candle["high"] >= sl_price:
            return -SL_PCT, "SL"
        if candle["low"] <= tp_price:
            return TP_PCT, "TP"
    ts_end = btc_times[end_bar - 1]
    if ts_end in coin_lookup:
        close_price = coin_lookup[ts_end]["close"]
        pnl = (entry - close_price) / entry * 100
        return pnl, "TIMEOUT"
    return 0, "TIMEOUT"


def longest_losing_streak(trades):
    max_streak = 0
    current = 0
    for pnl, outcome in trades:
        if pnl <= 0:
            current += 1
            if current > max_streak:
                max_streak = current
        else:
            current = 0
    return max_streak


def compound(trades, leverage):
    balance = 1.0
    peak = 1.0
    max_dd = 0.0
    for pnl_pct, outcome in trades:
        lev_pnl = pnl_pct * leverage / 100
        balance *= (1 + lev_pnl)
        if balance <= 0:
            return 0.0, 100.0
        if balance > peak:
            peak = balance
        dd = (peak - balance) / peak * 100
        if dd > max_dd:
            max_dd = dd
    return balance, max_dd


def fmt(val):
    if val >= 1_000_000_000:
        return f"${val/1_000_000_000:.1f}B"
    if val >= 1_000_000:
        return f"${val/1_000_000:.1f}M"
    if val >= 1_000:
        return f"${val/1_000:.1f}K"
    if val >= 1:
        return f"${val:.2f}"
    if val >= 0.01:
        return f"${val:.3f}"
    return f"${val:.6f}"


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

    # Classify each coin
    results = []
    for coin in ALL_COINS:
        if coin not in coin_lookups:
            continue
        lookup = coin_lookups[coin]
        trades = []
        for bar in signal_bars:
            pnl, outcome = sim_trade(bar, lookup, btc_times, n_bars)
            if pnl is not None:
                trades.append((pnl, outcome))

        if not trades:
            continue

        wins = sum(1 for p, o in trades if p > 0)
        total = len(trades)
        wr = wins / total * 100
        max_loss_streak = longest_losing_streak(trades)

        bal_1x, dd_1x = compound(trades, 1)
        bal_2x, dd_2x = compound(trades, 2)

        # 2x safe = 2x balance > 1x balance (leverage actually helps)
        # 2x risky = 2x balance < $0.10 (leverage destroys it)
        if bal_2x > bal_1x:
            tier = "2x SAFE"
        elif bal_2x >= 0.10:
            tier = "2x OK"
        elif bal_1x >= 1.0:
            tier = "1x ONLY"
        else:
            tier = "AVOID"

        results.append({
            "coin": coin,
            "trades": total,
            "wr": wr,
            "streak": max_loss_streak,
            "bal_1x": bal_1x,
            "bal_2x": bal_2x,
            "dd_1x": dd_1x,
            "dd_2x": dd_2x,
            "tier": tier,
        })

    # Sort by 1x balance descending
    results.sort(key=lambda r: r["bal_1x"], reverse=True)

    print(f"{'='*110}")
    print(f"  LEVERAGE CLASSIFICATION -- 40 coins | 7% SL / 12% TP | compound from $1")
    print(f"{'='*110}")
    print(f"\n  {'Coin':>10s}  {'WR%':>5s}  {'MaxLoss':>7s}  "
          f"{'$1@1x':>12s}  {'DD@1x':>6s}  "
          f"{'$1@2x':>12s}  {'DD@2x':>6s}  {'Tier':>10s}")
    print(f"  {'─'*100}")

    safe_2x = []
    ok_2x = []
    only_1x = []
    avoid = []

    for r in results:
        name = r["coin"].replace("usdt", "").upper()
        print(f"  {name:>10s}  {r['wr']:>4.1f}%  {r['streak']:>5d}x  "
              f"{fmt(r['bal_1x']):>12s}  {r['dd_1x']:>5.0f}%  "
              f"{fmt(r['bal_2x']):>12s}  {r['dd_2x']:>5.0f}%  "
              f"{r['tier']:>10s}")

        name_clean = r["coin"].replace("usdt", "").upper()
        if r["tier"] == "2x SAFE":
            safe_2x.append(name_clean)
        elif r["tier"] == "2x OK":
            ok_2x.append(name_clean)
        elif r["tier"] == "1x ONLY":
            only_1x.append(name_clean)
        else:
            avoid.append(name_clean)

    print(f"\n{'='*110}")
    print(f"  SUMMARY")
    print(f"{'='*110}")
    print(f"\n  2x SAFE ({len(safe_2x)} coins) -- leverage makes them MORE profitable:")
    print(f"    {', '.join(safe_2x)}")
    print(f"\n  2x OK ({len(ok_2x)} coins) -- survive 2x but not clearly better:")
    print(f"    {', '.join(ok_2x)}")
    print(f"\n  1x ONLY ({len(only_1x)} coins) -- profitable at 1x, destroyed at 2x:")
    print(f"    {', '.join(only_1x)}")
    print(f"\n  AVOID ({len(avoid)} coins) -- not profitable even at 1x:")
    print(f"    {', '.join(avoid)}")
    print(f"\n{'='*110}")


if __name__ == "__main__":
    run()
