"""Portfolio simulation: shared pool, all coins trade together.

Models the ACTUAL system: one balance, all coins fire on same signal,
profits go back to pool. Tests different leverage levels.
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

# Top 25 tradeable coins (from tier analysis, excluding AVOID)
TOP_25 = [
    "vetusdt", "hbarusdt", "thetausdt", "algousdt", "fetusdt",
    "shibusdt", "dotusdt", "adausdt", "suiusdt", "grtusdt",
    "icpusdt", "polusdt", "atomusdt", "dogeusdt", "opusdt",
    "nearusdt", "xlmusdt", "ldousdt", "susdt", "solusdt",
    "linkusdt", "pepeusdt", "avaxusdt", "aptusdt", "uniusdt",
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
MAX_HOLD_BARS = 8640  # 30 days


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


def sim_signal_trades(signal_bar, coin_lookups, btc_times, n_bars, coins):
    """Simulate all coin trades for a single signal.
    Returns list of (pnl_pct, coin) for each trade that fires."""
    results = []
    for coin in coins:
        if coin not in coin_lookups:
            continue
        lookup = coin_lookups[coin]
        ts = btc_times[signal_bar]
        if ts not in lookup:
            continue
        entry = lookup[ts]["close"]
        if entry == 0:
            continue

        sl_price = entry * (1 + SL_PCT / 100)
        tp_price = entry * (1 - TP_PCT / 100)
        end_bar = min(signal_bar + MAX_HOLD_BARS, n_bars)

        pnl = 0
        outcome = "TIMEOUT"
        for j in range(signal_bar + 1, end_bar):
            ts_j = btc_times[j]
            if ts_j not in lookup:
                continue
            candle = lookup[ts_j]
            if candle["high"] >= sl_price:
                pnl = -SL_PCT
                outcome = "SL"
                break
            if candle["low"] <= tp_price:
                pnl = TP_PCT
                outcome = "TP"
                break

        if outcome == "TIMEOUT":
            ts_end = btc_times[end_bar - 1]
            if ts_end in lookup:
                close_price = lookup[ts_end]["close"]
                pnl = (entry - close_price) / entry * 100

        results.append((pnl, coin, outcome))

    return results


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

    # Available coins for trading
    tradeable = [c for c in TOP_25 if c in coin_lookups]
    n_coins = len(tradeable)

    print(f"\n{len(signal_bars)} signals | {first:%Y-%m-%d} to {last:%Y-%m-%d}")
    print(f"{n_coins} coins in portfolio | 30-day timeout")
    print(f"SL={SL_PCT}% / TP={TP_PCT}%")

    # Test different leverage levels
    leverages = [1, 2, 3, 5]
    starting_balance = 50.0

    print(f"\n{'='*90}")
    print(f"  PORTFOLIO SIM — ${starting_balance} start, {n_coins} coins, equal split")
    print(f"  Each signal: pool / {n_coins} = per-coin allocation")
    print(f"  All profits/losses go back to shared pool")
    print(f"{'='*90}")

    for lev in leverages:
        balance = starting_balance
        peak = starting_balance
        max_dd = 0.0
        total_wins = 0
        total_losses = 0
        total_trades = 0
        worst_signal_loss = 0.0

        # Track balance at each signal for reporting
        checkpoints = []

        for sig_idx, bar in enumerate(signal_bars):
            # Equal split: each coin gets balance / n_coins
            per_coin = balance / n_coins

            # Simulate all trades for this signal
            trades = sim_signal_trades(
                bar, coin_lookups, btc_times, n_bars, tradeable)

            signal_pnl = 0.0
            for pnl_pct, coin, outcome in trades:
                # P&L in dollars: per_coin * leverage * pnl%
                dollar_pnl = per_coin * lev * (pnl_pct / 100)
                signal_pnl += dollar_pnl
                total_trades += 1
                if pnl_pct > 0:
                    total_wins += 1
                else:
                    total_losses += 1

            balance += signal_pnl

            if signal_pnl < worst_signal_loss:
                worst_signal_loss = signal_pnl

            if balance <= 0:
                balance = 0
                break

            if balance > peak:
                peak = balance
            dd = (peak - balance) / peak * 100
            if dd > max_dd:
                max_dd = dd

            # Log every 200 signals
            if sig_idx % 200 == 0 or sig_idx == len(signal_bars) - 1:
                dt = datetime.utcfromtimestamp(btc_times[bar])
                checkpoints.append((sig_idx + 1, dt, balance))

        wr = total_wins / total_trades * 100 if total_trades > 0 else 0
        roi = (balance - starting_balance) / starting_balance * 100

        print(f"\n  --- {lev}x LEVERAGE ---")
        print(f"  ${starting_balance} -> {fmt(balance)}  "
              f"({roi:+.0f}% ROI)")
        print(f"  Trades: {total_trades}  "
              f"WR: {wr:.1f}%  "
              f"Max DD: {max_dd:.1f}%")
        print(f"  Worst single signal: {fmt(worst_signal_loss)} loss")

        print(f"\n  Growth timeline:")
        for idx, dt, bal in checkpoints:
            print(f"    Signal {idx:>5d}  {dt:%Y-%m-%d}  {fmt(bal)}")

    # Also test with just the 8 current coins
    current_8 = [
        "ethusdt", "xrpusdt", "solusdt", "dogeusdt",
        "uniusdt", "atomusdt", "hbarusdt", "fetusdt",
    ]
    current_8 = [c for c in current_8 if c in coin_lookups]

    print(f"\n{'='*90}")
    print(f"  CURRENT 8 COINS — ${starting_balance} start")
    print(f"{'='*90}")

    for lev in [1, 2, 3]:
        balance = starting_balance
        peak = starting_balance
        max_dd = 0.0
        total_trades = 0
        total_wins = 0

        for bar in signal_bars:
            per_coin = balance / len(current_8)
            trades = sim_signal_trades(
                bar, coin_lookups, btc_times, n_bars, current_8)

            for pnl_pct, coin, outcome in trades:
                dollar_pnl = per_coin * lev * (pnl_pct / 100)
                balance += dollar_pnl
                total_trades += 1
                if pnl_pct > 0:
                    total_wins += 1

            if balance <= 0:
                balance = 0
                break
            if balance > peak:
                peak = balance
            dd = (peak - balance) / peak * 100
            if dd > max_dd:
                max_dd = dd

        wr = total_wins / total_trades * 100 if total_trades > 0 else 0
        roi = (balance - starting_balance) / starting_balance * 100
        print(f"  {lev}x:  ${starting_balance} -> {fmt(balance)}  "
              f"({roi:+.0f}% ROI)  "
              f"MaxDD: {max_dd:.1f}%  WR: {wr:.1f}%")

    print(f"\n{'='*90}")


if __name__ == "__main__":
    run()
