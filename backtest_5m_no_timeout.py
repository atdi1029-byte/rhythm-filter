"""Backtest: No timeout vs 7-day timeout comparison.

What happens if trades stay open until SL or TP hit?
No more partial exits — every trade is a clean win or loss.
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
SL_PCT = 7.0
TP_PCT = 12.0


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


def sim_trade(signal_bar, coin_lookup, btc_times, n_bars, use_timeout):
    """Simulate SHORT trade. If use_timeout=False, trade stays open
    until SL or TP (or end of data)."""
    ts = btc_times[signal_bar]
    if ts not in coin_lookup:
        return None, None
    entry = coin_lookup[ts]["close"]
    if entry == 0:
        return None, None

    sl_price = entry * (1 + SL_PCT / 100)
    tp_price = entry * (1 - TP_PCT / 100)

    if use_timeout:
        end_bar = min(signal_bar + 2016, n_bars)  # 7 days
    else:
        end_bar = n_bars  # no timeout

    for j in range(signal_bar + 1, end_bar):
        ts_j = btc_times[j]
        if ts_j not in coin_lookup:
            continue
        candle = coin_lookup[ts_j]
        if candle["high"] >= sl_price:
            return -SL_PCT, "SL"
        if candle["low"] <= tp_price:
            return TP_PCT, "TP"

    # End of data or timeout
    ts_end = btc_times[end_bar - 1]
    if ts_end in coin_lookup:
        close_price = coin_lookup[ts_end]["close"]
        pnl = (entry - close_price) / entry * 100
        return pnl, "TIMEOUT"
    return 0, "TIMEOUT"


def compound(trades):
    balance = 1.0
    for pnl_pct, outcome in trades:
        balance *= (1 + pnl_pct / 100)
        if balance <= 0:
            return 0.0
    return balance


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

    print(f"{len(signal_bars)} signals | {first:%Y-%m-%d} to {last:%Y-%m-%d}")
    print(f"SL={SL_PCT}% / TP={TP_PCT}%\n")

    # Run both modes for each coin
    print(f"{'='*100}")
    print(f"  TIMEOUT vs NO-TIMEOUT COMPARISON")
    print(f"{'='*100}")

    print(f"\n  {'Coin':>10s}  {'WR(TO)':>6s}  {'WR(no)':>6s}  "
          f"{'TO%':>5s}  {'TO->TP':>6s}  {'TO->SL':>6s}  "
          f"{'$1(TO)':>12s}  {'$1(no)':>12s}  {'Better':>8s}")
    print(f"  {'─'*95}")

    total_to_wins = 0
    total_to_losses = 0
    total_no_wins = 0
    total_no_losses = 0
    total_timeouts = 0
    total_to_became_tp = 0
    total_to_became_sl = 0

    coin_results = []

    for coin in sorted(all_candles.keys()):
        lookup = coin_lookups[coin]

        trades_to = []  # with timeout
        trades_no = []  # no timeout

        timeouts = 0
        to_became_tp = 0
        to_became_sl = 0

        for bar in signal_bars:
            pnl_to, out_to = sim_trade(bar, lookup, btc_times, n_bars, True)
            pnl_no, out_no = sim_trade(bar, lookup, btc_times, n_bars, False)

            if pnl_to is None:
                continue

            trades_to.append((pnl_to, out_to))
            trades_no.append((pnl_no, out_no))

            if out_to == "TIMEOUT":
                timeouts += 1
                if out_no == "TP":
                    to_became_tp += 1
                elif out_no == "SL":
                    to_became_sl += 1

        if not trades_to:
            continue

        total = len(trades_to)
        wins_to = sum(1 for p, o in trades_to if p > 0)
        wins_no = sum(1 for p, o in trades_no if p > 0)
        wr_to = wins_to / total * 100
        wr_no = wins_no / total * 100
        to_pct = timeouts / total * 100

        bal_to = compound(trades_to)
        bal_no = compound(trades_no)

        better = "NO-TO" if bal_no > bal_to else "TO"

        total_to_wins += wins_to
        total_to_losses += total - wins_to
        total_no_wins += wins_no
        total_no_losses += total - wins_no
        total_timeouts += timeouts
        total_to_became_tp += to_became_tp
        total_to_became_sl += to_became_sl

        name = coin.replace("usdt", "").upper()
        coin_results.append({
            "coin": name, "wr_to": wr_to, "wr_no": wr_no,
            "to_pct": to_pct, "to_tp": to_became_tp, "to_sl": to_became_sl,
            "bal_to": bal_to, "bal_no": bal_no, "better": better,
        })

        print(f"  {name:>10s}  {wr_to:>5.1f}%  {wr_no:>5.1f}%  "
              f"{to_pct:>4.0f}%  {to_became_tp:>5d}   {to_became_sl:>5d}   "
              f"{fmt(bal_to):>12s}  {fmt(bal_no):>12s}  {better:>8s}")

    # Summary
    total_trades = total_to_wins + total_to_losses
    print(f"\n{'='*100}")
    print(f"  SUMMARY")
    print(f"{'='*100}")
    print(f"  Total trades:     {total_trades}")
    print(f"  Timeouts (7-day): {total_timeouts} "
          f"({total_timeouts/total_trades*100:.1f}%)")
    print(f"  Timeouts -> TP:   {total_to_became_tp} "
          f"({total_to_became_tp/total_timeouts*100:.1f}% of timeouts)")
    print(f"  Timeouts -> SL:   {total_to_became_sl} "
          f"({total_to_became_sl/total_timeouts*100:.1f}% of timeouts)")
    still_open = total_timeouts - total_to_became_tp - total_to_became_sl
    print(f"  Still open (EOD): {still_open} "
          f"({still_open/total_timeouts*100:.1f}% of timeouts)")

    print(f"\n  WITH timeout:    WR={total_to_wins/total_trades*100:.1f}%")
    print(f"  WITHOUT timeout: WR={total_no_wins/total_trades*100:.1f}%")

    # Count which mode wins per coin
    to_better = sum(1 for r in coin_results if r["better"] == "TO")
    no_better = sum(1 for r in coin_results if r["better"] == "NO-TO")
    print(f"\n  Timeout better:    {to_better} coins")
    print(f"  No-timeout better: {no_better} coins")

    # Total portfolio compound
    total_bal_to = sum(r["bal_to"] for r in coin_results)
    total_bal_no = sum(r["bal_no"] for r in coin_results)
    print(f"\n  Portfolio $1/coin compound:")
    print(f"    WITH timeout:    {fmt(total_bal_to)}")
    print(f"    WITHOUT timeout: {fmt(total_bal_no)}")

    print(f"\n{'='*100}")


if __name__ == "__main__":
    run()
