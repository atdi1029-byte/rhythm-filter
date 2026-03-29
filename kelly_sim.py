"""Kelly Criterion simulation: find optimal bet fraction at each leverage.

Instead of betting 100% of bankroll each trade (full compound),
bet a Kelly-optimal fraction. Tests multiple fractions x leverage combos.
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

TARGET_COINS = [
    "ethusdt", "xrpusdt", "solusdt", "dogeusdt",
    "uniusdt", "atomusdt", "hbarusdt", "fetusdt",
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


def compound_with_kelly(trades, leverage, fraction):
    """Compound trades using Kelly fraction.

    fraction = what % of bankroll to risk per trade (0.0 to 1.0)
    leverage = leverage on that fraction

    Per trade:
      risk_amount = balance * fraction
      pnl_dollars = risk_amount * leverage * (pnl_pct / 100)
      balance += pnl_dollars
    """
    balance = 1.0
    max_bal = 1.0
    max_dd = 0.0

    for pnl_pct, outcome in trades:
        risk_amount = balance * fraction
        pnl_dollars = risk_amount * leverage * (pnl_pct / 100)
        balance += pnl_dollars
        if balance <= 0.001:
            balance = 0.001  # floor to avoid log(0)
            return balance, 100.0  # wiped
        if balance > max_bal:
            max_bal = balance
        dd = (max_bal - balance) / max_bal * 100
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

    # Pre-compute trades for each coin
    coin_trades = {}
    for coin in TARGET_COINS:
        if coin not in coin_lookups:
            continue
        lookup = coin_lookups[coin]
        trades = []
        for bar in signal_bars:
            pnl, outcome = sim_trade(bar, lookup, btc_times, n_bars)
            if pnl is not None:
                trades.append((pnl, outcome))
        coin_trades[coin] = trades

    # Theoretical Kelly for clean SL/TP only
    # f* = (b*p - q) / b where b = TP/SL ratio
    for coin in TARGET_COINS:
        if coin not in coin_trades:
            continue
        trades = coin_trades[coin]
        wins = sum(1 for p, o in trades if p > 0)
        total = len(trades)
        p = wins / total
        q = 1 - p
        b = TP_PCT / SL_PCT  # 12/7 = 1.714
        kelly = (b * p - q) / b
        name = coin.replace("usdt", "").upper()
        print(f"  {name}: WR={p*100:.1f}%  Theoretical Kelly={kelly*100:.1f}%")

    # Test grid: leverage x fraction
    leverages = [1, 2, 3, 5]
    fractions = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50,
                 0.60, 0.75, 1.00]

    print(f"\n{'='*100}")
    print(f"  KELLY x LEVERAGE GRID -- All 8 coins combined ($1 each = $8 start)")
    print(f"  Finding optimal fraction at each leverage level")
    print(f"{'='*100}")

    # For each leverage, find the best fraction across all coins combined
    for lv in leverages:
        print(f"\n  --- {lv}x LEVERAGE ---")
        print(f"  {'Fraction':>10s}  ", end="")
        for coin in TARGET_COINS:
            name = coin.replace("usdt", "").upper()
            print(f"  {name:>8s}", end="")
        print(f"  {'TOTAL':>10s}  {'MaxDD':>6s}")
        print(f"  {'─'*100}")

        best_total = 0
        best_frac = 0

        for frac in fractions:
            print(f"  {frac*100:>8.0f}%  ", end="")
            total = 0
            worst_dd = 0
            for coin in TARGET_COINS:
                if coin not in coin_trades:
                    continue
                bal, dd = compound_with_kelly(coin_trades[coin], lv, frac)
                total += bal
                if dd > worst_dd:
                    worst_dd = dd
                print(f"  {fmt(bal):>8s}", end="")

            print(f"  {fmt(total):>10s}  {worst_dd:>5.0f}%")

            if total > best_total:
                best_total = total
                best_frac = frac

        print(f"\n  BEST at {lv}x: {best_frac*100:.0f}% fraction -> {fmt(best_total)}")

    # Summary: best combo overall
    print(f"\n{'='*100}")
    print(f"  OPTIMAL COMBOS SUMMARY")
    print(f"{'='*100}")

    best_overall = 0
    best_combo = (1, 1.0)

    for lv in leverages:
        for frac in fractions:
            total = 0
            for coin in TARGET_COINS:
                if coin not in coin_trades:
                    continue
                bal, dd = compound_with_kelly(coin_trades[coin], lv, frac)
                total += bal
            if total > best_overall:
                best_overall = total
                best_combo = (lv, frac)

    # Show top 10 combos
    results = []
    for lv in leverages:
        for frac in fractions:
            total = 0
            worst_dd = 0
            for coin in TARGET_COINS:
                if coin not in coin_trades:
                    continue
                bal, dd = compound_with_kelly(coin_trades[coin], lv, frac)
                total += bal
                if dd > worst_dd:
                    worst_dd = dd
            results.append((lv, frac, total, worst_dd))

    results.sort(key=lambda r: r[2], reverse=True)

    print(f"\n  {'Rank':>4s}  {'Leverage':>8s}  {'Fraction':>8s}  "
          f"{'Total':>12s}  {'MaxDD':>6s}")
    print(f"  {'─'*50}")
    for i, (lv, frac, total, dd) in enumerate(results[:15]):
        print(f"  {i+1:>4d}  {lv:>7d}x  {frac*100:>7.0f}%  "
              f"{fmt(total):>12s}  {dd:>5.0f}%")

    # Compare: full compound 1x vs best Kelly
    print(f"\n  COMPARISON:")
    total_1x_full = 0
    for coin in TARGET_COINS:
        if coin not in coin_trades:
            continue
        bal, dd = compound_with_kelly(coin_trades[coin], 1, 1.0)
        total_1x_full += bal
    lv, frac = best_combo
    print(f"    1x full compound (current):  {fmt(total_1x_full)}")
    print(f"    Best Kelly ({lv}x @ {frac*100:.0f}%):     {fmt(best_overall)}")


if __name__ == "__main__":
    run()
