"""Leverage comparison: compound $1 per coin at different leverage levels.

Uses 5-min breathing score signals with 7% SL / 12% TP.
Shows how leverage affects compounding for each of the 8 chosen coins.
"""

import json
import os
from datetime import datetime

import config

# All 40 coins for breathing score computation
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

# Our 8 chosen coins
TARGET_COINS = [
    "ethusdt", "xrpusdt", "solusdt", "dogeusdt",
    "uniusdt", "atomusdt", "hbarusdt", "fetusdt",
]

LEVERAGE_LEVELS = [1, 2, 3, 5, 7, 10]

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
    """Returns (pnl_pct, outcome) based on price SL/TP (not leveraged)."""
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


def fmt(val):
    if val >= 1_000_000_000:
        return f"${val/1_000_000_000:.1f}B"
    if val >= 1_000_000:
        return f"${val/1_000_000:.1f}M"
    if val >= 1_000:
        return f"${val/1_000:.1f}K"
    return f"${val:.2f}"


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

    first = datetime.utcfromtimestamp(btc_times[0])
    last = datetime.utcfromtimestamp(btc_times[-1])

    # Compute breathing score
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

    print(f"\n{len(signal_bars)} signals | {first:%Y-%m-%d} to {last:%Y-%m-%d}")
    print(f"SL={SL_PCT}% / TP={TP_PCT}% | Leverage: {LEVERAGE_LEVELS}")

    # Pre-compute trade outcomes for each target coin (price-based, no leverage)
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

    # Run compound sim at each leverage level
    print(f"\n{'='*80}")
    print(f"  LEVERAGE COMPARISON -- $1 start per coin, compounding all trades")
    print(f"  {len(signal_bars)} breathing score signals | {SL_PCT}% SL / {TP_PCT}% TP")
    print(f"{'='*80}")

    # Header
    lev_headers = "  ".join(f"{'%dx' % lv:>10s}" for lv in LEVERAGE_LEVELS)
    print(f"\n  {'Coin':>10s}  {'Trades':>6s}  {'WR%':>5s}  {lev_headers}")
    print(f"  {'─'*90}")

    # Per-coin results at each leverage
    portfolio_totals = {lv: 0.0 for lv in LEVERAGE_LEVELS}
    liquidation_counts = {lv: 0 for lv in LEVERAGE_LEVELS}

    for coin in TARGET_COINS:
        if coin not in coin_trades:
            continue
        trades = coin_trades[coin]
        wins = sum(1 for p, o in trades if p > 0)
        total = len(trades)
        wr = wins / total * 100 if total > 0 else 0

        results = []
        for lv in LEVERAGE_LEVELS:
            balance = 1.0
            liquidated = False
            for pnl_pct, outcome in trades:
                # Leveraged P&L on capital
                lev_pnl = pnl_pct * lv / 100
                balance *= (1 + lev_pnl)
                if balance <= 0:
                    balance = 0
                    liquidated = True
                    break

            if liquidated:
                results.append("  LIQD")
                liquidation_counts[lv] += 1
            else:
                results.append(fmt(balance))
                portfolio_totals[lv] += balance

        result_str = "  ".join(f"{r:>10s}" for r in results)
        name = coin.replace("usdt", "").upper()
        print(f"  {name:>10s}  {total:>6d}  {wr:>4.0f}%  {result_str}")

    # Portfolio total row
    print(f"  {'─'*90}")
    total_results = []
    for lv in LEVERAGE_LEVELS:
        total_results.append(fmt(portfolio_totals[lv]))
    total_str = "  ".join(f"{r:>10s}" for r in total_results)
    print(f"  {'TOTAL':>10s}  {'':>6s}  {'':>5s}  {total_str}")

    # Liquidation warnings
    print(f"\n  Liquidation risk (7% SL):")
    for lv in LEVERAGE_LEVELS:
        max_loss = SL_PCT * lv
        if max_loss >= 100:
            print(f"    {lv}x: {max_loss:.0f}% loss on SL = GUARANTEED LIQUIDATION")
        else:
            print(f"    {lv}x: {max_loss:.0f}% loss on SL hit"
                  f" ({liquidation_counts[lv]} coins wiped)")

    # Combined portfolio: $1 per coin, 8 coins = $8 start
    print(f"\n{'='*80}")
    print(f"  COMBINED PORTFOLIO: $1 per coin x 8 coins = $8 start")
    print(f"{'='*80}")

    for lv in LEVERAGE_LEVELS:
        total_start = len(TARGET_COINS)  # $1 each
        total_end = portfolio_totals[lv]
        roi = (total_end - total_start) / total_start * 100
        liq = liquidation_counts[lv]
        surviving = len(TARGET_COINS) - liq
        print(f"  {lv}x leverage:  ${total_start} -> {fmt(total_end)}"
              f"  ({roi:+.0f}% ROI)  "
              f"[{surviving}/8 coins survived]")

    print(f"\n{'='*80}")


if __name__ == "__main__":
    run()
