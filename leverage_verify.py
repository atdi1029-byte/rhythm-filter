"""Verify leverage math by printing trade-by-trade for ETH."""

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
        return None, None, None
    entry = coin_lookup[ts]["close"]
    if entry == 0:
        return None, None, None
    sl_price = entry * (1 + SL_PCT / 100)
    tp_price = entry * (1 - TP_PCT / 100)
    end_bar = min(signal_bar + MAX_HOLD_BARS, n_bars)
    for j in range(signal_bar + 1, end_bar):
        ts_j = btc_times[j]
        if ts_j not in coin_lookup:
            continue
        candle = coin_lookup[ts_j]
        if candle["high"] >= sl_price:
            return -SL_PCT, "SL", entry
        if candle["low"] <= tp_price:
            return TP_PCT, "TP", entry
    ts_end = btc_times[end_bar - 1]
    if ts_end in coin_lookup:
        close_price = coin_lookup[ts_end]["close"]
        pnl = (entry - close_price) / entry * 100
        return pnl, "TIMEOUT", entry
    return 0, "TIMEOUT", entry


def run():
    print("Loading data...")
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

    # ETH trades
    eth_lookup = coin_lookups["ethusdt"]
    trades = []
    for bar in signal_bars:
        pnl, outcome, entry = sim_trade(bar, eth_lookup, btc_times, n_bars)
        if pnl is not None:
            ts = btc_times[bar]
            trades.append((pnl, outcome, entry, ts))

    # Print first 50 trades with running balance at 1x and 2x
    print(f"\nETH: {len(trades)} trades total")
    wins = sum(1 for p, o, e, t in trades if p > 0)
    losses = sum(1 for p, o, e, t in trades if p <= 0)
    timeouts = sum(1 for p, o, e, t in trades if o == "TIMEOUT")
    print(f"Wins: {wins}, Losses: {losses}, Timeouts: {timeouts}")
    print(f"WR: {wins/len(trades)*100:.1f}%")

    print(f"\n{'#':>4s}  {'Date':>12s}  {'Entry':>8s}  {'P&L%':>7s}  "
          f"{'Type':>4s}  {'Bal@1x':>12s}  {'Bal@2x':>12s}  "
          f"{'Bal@3x':>12s}")
    print(f"  {'─'*85}")

    bal_1x = 1.0
    bal_2x = 1.0
    bal_3x = 1.0

    for i, (pnl, outcome, entry, ts) in enumerate(trades):
        dt = datetime.utcfromtimestamp(ts)

        bal_1x *= (1 + pnl * 1 / 100)
        bal_2x *= (1 + pnl * 2 / 100)
        bal_3x *= (1 + pnl * 3 / 100)

        # Print first 50, then every 100th, then last 10
        if i < 50 or i % 100 == 0 or i >= len(trades) - 10:
            print(f"  {i+1:>4d}  {dt:%Y-%m-%d}  ${entry:>7.1f}  "
                  f"{pnl:>+6.2f}%  {outcome:>4s}  "
                  f"${bal_1x:>11.6f}  ${bal_2x:>11.6f}  "
                  f"${bal_3x:>11.6f}")

    print(f"\n  FINAL:")
    print(f"    1x: ${bal_1x:.6f}")
    print(f"    2x: ${bal_2x:.6f}")
    print(f"    3x: ${bal_3x:.6f}")

    # Also show the math check
    print(f"\n  MATH CHECK:")
    print(f"    1x win:  $1 * (1 + 12/100) = ${1*1.12:.2f}")
    print(f"    1x loss: $1 * (1 - 7/100)  = ${1*0.93:.2f}")
    print(f"    2x win:  $1 * (1 + 24/100) = ${1*1.24:.2f}")
    print(f"    2x loss: $1 * (1 - 14/100) = ${1*0.86:.2f}")
    print(f"    3x win:  $1 * (1 + 36/100) = ${1*1.36:.2f}")
    print(f"    3x loss: $1 * (1 - 21/100) = ${1*0.79:.2f}")


if __name__ == "__main__":
    run()
