"""Backtest: short BTC when Rhythm Filter deep red rolls over.

Replicates the Pine Script v4 breathing score across all 40 coins,
detects deep red peaks (EXHALE) rolling over, and measures what
happens to BTC price after opening a short at each rollover.
"""

import json
import os
from datetime import datetime

import config

# === COIN LIST (matches Pine Script v4) ===
# Maps to data file names
COINS = [
    "btcusdt", "ethusdt", "xrpusdt", "bnbusdt", "solusdt",
    "dogeusdt", "adausdt", "trxusdt", "avaxusdt", "shibusdt",
    "tonusdt", "linkusdt", "suiusdt", "dotusdt", "nearusdt",
    "uniusdt", "aptusdt", "maticusdt", "arbusdt", "opusdt",
    "icpusdt", "hbarusdt", "filusdt", "atomusdt", "imxusdt",
    "injusdt", "stxusdt", "ftmusdt", "grtusdt", "thetausdt",
    "algousdt", "ldousdt", "aaveusdt", "mkrusdt", "snxusdt",
    "vetusdt", "xlmusdt", "pepeusdt", "fetusdt", "eosusdt",
]

# === RSI SETTINGS (same as Pine Script) ===
RSI_LEN = 14
BUY_ZONE = 40.0
SELL_ZONE = 60.0
EMA_SMOOTH = 9
TOTAL_COINS = 40

# === SIGNAL DETECTION ===
DEEP_RED_THRESHOLD = -8.0   # breathing score must go below this
ROLLOVER_CONFIRM = 2        # bars of rising score to confirm rollover

# === OUTCOME WINDOWS ===
OUTCOME_BARS = {
    "6h": 1,     # next bar (4H)
    "12h": 3,
    "1d": 6,
    "2d": 12,
    "3d": 18,
    "5d": 30,
    "7d": 42,
    "14d": 84,
}


def load_candles(symbol):
    """Load 4H candle data for a coin."""
    filepath = os.path.join(config.DATA_DIR, f"{symbol}_4h.json")
    if not os.path.exists(filepath):
        return None
    with open(filepath) as f:
        return json.load(f)


def compute_rsi(closes, length=14):
    """Compute RSI series (matches Pine Script ta.rsi)."""
    rsi = [None] * len(closes)
    if len(closes) < length + 1:
        return rsi

    # First RSI: simple average of gains/losses
    gains = []
    losses = []
    for i in range(1, length + 1):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))

    avg_gain = sum(gains) / length
    avg_loss = sum(losses) / length

    if avg_loss == 0:
        rsi[length] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi[length] = 100.0 - (100.0 / (1.0 + rs))

    # Subsequent RSI: Wilder's smoothing (RMA)
    for i in range(length + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gain = max(delta, 0)
        loss = max(-delta, 0)

        avg_gain = (avg_gain * (length - 1) + gain) / length
        avg_loss = (avg_loss * (length - 1) + loss) / length

        if avg_loss == 0:
            rsi[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i] = 100.0 - (100.0 / (1.0 + rs))

    return rsi


def compute_ema(values, length):
    """Compute EMA series (handles None values)."""
    ema = [None] * len(values)
    multiplier = 2.0 / (length + 1)

    # Find first valid value
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
            ema[i] = values[i] * multiplier + ema[i - 1] * (1 - multiplier)
        else:
            ema[i] = ema[i - 1]

    return ema


def align_candles(all_candles):
    """Align all coins to BTC's timestamps.

    Returns dict of {timestamp: {coin: candle}} only for
    timestamps where BTC has data.
    """
    btc_candles = all_candles.get("btcusdt")
    if not btc_candles:
        raise ValueError("No BTC data!")

    btc_times = [c["time"] for c in btc_candles]

    # Build lookup for each coin
    coin_lookups = {}
    for coin, candles in all_candles.items():
        coin_lookups[coin] = {c["time"]: c for c in candles}

    return btc_times, coin_lookups


def run_backtest():
    # === LOAD ALL COIN DATA ===
    print("Loading candle data for all coins...")
    all_candles = {}
    for coin in COINS:
        candles = load_candles(coin)
        if candles:
            all_candles[coin] = candles
            print(f"  {coin}: {len(candles)} candles")
        else:
            print(f"  {coin}: NO DATA — skipping")

    active_coins = len(all_candles)
    print(f"\nLoaded {active_coins}/{TOTAL_COINS} coins")

    if "btcusdt" not in all_candles:
        print("ERROR: No BTC data!")
        return

    # === ALIGN TO BTC TIMESTAMPS ===
    btc_times, coin_lookups = align_candles(all_candles)
    n_bars = len(btc_times)
    print(f"BTC has {n_bars} bars")

    first = datetime.utcfromtimestamp(btc_times[0])
    last = datetime.utcfromtimestamp(btc_times[-1])
    print(f"Date range: {first} → {last}")

    # === COMPUTE RSI FOR EACH COIN ===
    print("\nComputing RSI for each coin...")
    coin_rsi = {}

    for coin in all_candles:
        # Build close series aligned to BTC timestamps
        closes = []
        lookup = coin_lookups[coin]
        last_close = None
        for ts in btc_times:
            if ts in lookup:
                last_close = lookup[ts]["close"]
            closes.append(last_close)

        # Fill leading Nones with first valid close
        first_valid = next((c for c in closes if c is not None), None)
        if first_valid is None:
            continue
        closes = [c if c is not None else first_valid for c in closes]

        rsi = compute_rsi(closes, RSI_LEN)
        coin_rsi[coin] = rsi

    print(f"Computed RSI for {len(coin_rsi)} coins")

    # === COMPUTE BREATHING SCORE ===
    print("Computing composite breathing score...")
    raw_scores = []

    for i in range(n_bars):
        score = 0
        valid = 0
        for coin in coin_rsi:
            rsi_val = coin_rsi[coin][i]
            if rsi_val is not None:
                valid += 1
                if rsi_val < BUY_ZONE:
                    score += 1
                elif rsi_val > SELL_ZONE:
                    score -= 1

        # Normalize to -20 to +20 (same as Pine Script)
        if valid > 0:
            normalized = score * 20.0 / TOTAL_COINS
        else:
            normalized = 0.0
        raw_scores.append(normalized)

    # Smooth with EMA
    breath_scores = compute_ema(raw_scores, EMA_SMOOTH)

    # === DETECT DEEP RED ROLLOVERS ===
    print(f"\nDetecting deep red rollovers "
          f"(threshold: {DEEP_RED_THRESHOLD})...")

    signals = []
    in_deep_red = False
    trough_val = 0
    trough_bar = 0
    rising_count = 0

    for i in range(1, n_bars):
        score = breath_scores[i]
        prev = breath_scores[i - 1]

        if score is None or prev is None:
            continue

        # Enter deep red zone
        if score <= DEEP_RED_THRESHOLD and not in_deep_red:
            in_deep_red = True
            trough_val = score
            trough_bar = i
            rising_count = 0

        # Track deepest point
        if in_deep_red and score < trough_val:
            trough_val = score
            trough_bar = i
            rising_count = 0

        # Count rising bars after trough
        if in_deep_red and score > prev:
            rising_count += 1
        elif in_deep_red and score <= prev:
            rising_count = 0

        # Rollover confirmed
        if in_deep_red and rising_count >= ROLLOVER_CONFIRM:
            # Get BTC price at this bar
            btc_lookup = coin_lookups["btcusdt"]
            ts = btc_times[i]
            if ts in btc_lookup:
                entry_price = btc_lookup[ts]["close"]

                # Count buy/sell zone coins at this moment
                buy_count = 0
                sell_count = 0
                for coin in coin_rsi:
                    rsi_val = coin_rsi[coin][i]
                    if rsi_val is not None:
                        if rsi_val < BUY_ZONE:
                            buy_count += 1
                        elif rsi_val > SELL_ZONE:
                            sell_count += 1

                signals.append({
                    "bar": i,
                    "time": ts,
                    "entry_price": entry_price,
                    "breath_score": round(score, 2),
                    "trough_score": round(trough_val, 2),
                    "buy_zone_coins": buy_count,
                    "sell_zone_coins": sell_count,
                })

            in_deep_red = False
            rising_count = 0

        # Exit deep red without rollover
        if in_deep_red and score > DEEP_RED_THRESHOLD + 3:
            in_deep_red = False
            rising_count = 0

    print(f"Found {len(signals)} rollover signals")

    if not signals:
        print("No signals found! Try adjusting DEEP_RED_THRESHOLD")
        return

    # === MEASURE OUTCOMES ===
    print("Measuring short outcomes...")
    btc_lookup = coin_lookups["btcusdt"]

    for sig in signals:
        bar = sig["bar"]
        entry = sig["entry_price"]

        for label, offset in OUTCOME_BARS.items():
            future_bar = bar + offset
            if future_bar < n_bars:
                ts = btc_times[future_bar]
                if ts in btc_lookup:
                    future_price = btc_lookup[ts]["close"]
                    # Short profit = entry - exit (price drop = profit)
                    sig[f"return_{label}"] = round(
                        (entry - future_price) / entry * 100, 2
                    )
                else:
                    sig[f"return_{label}"] = None
            else:
                sig[f"return_{label}"] = None

        # Max gain (max price drop) and max drawdown (max price rise)
        end_bar = min(bar + OUTCOME_BARS["14d"], n_bars)
        if bar + 1 < end_bar:
            lows = []
            highs = []
            for j in range(bar + 1, end_bar):
                ts = btc_times[j]
                if ts in btc_lookup:
                    lows.append(btc_lookup[ts]["low"])
                    highs.append(btc_lookup[ts]["high"])

            if lows and highs:
                sig["max_gain_pct"] = round(
                    (entry - min(lows)) / entry * 100, 2
                )
                sig["max_drawdown_pct"] = round(
                    (max(highs) - entry) / entry * 100, 2
                )
            else:
                sig["max_gain_pct"] = None
                sig["max_drawdown_pct"] = None
        else:
            sig["max_gain_pct"] = None
            sig["max_drawdown_pct"] = None

    # === PRINT RESULTS ===
    print(f"\n{'='*80}")
    print(f"  RHYTHM FILTER EXHALE SHORT BACKTEST")
    print(f"  Deep red rollover → short BTC")
    print(f"{'='*80}")

    print(f"\n  Threshold: breathScore < {DEEP_RED_THRESHOLD}")
    print(f"  Rollover confirm: {ROLLOVER_CONFIRM} rising bars")
    print(f"  Signals found: {len(signals)}")
    print(f"  Date range: {first.strftime('%Y-%m-%d')} → "
          f"{last.strftime('%Y-%m-%d')}")

    # Timeline
    print(f"\n  {'─'*76}")
    print(f"  {'#':>3s}  {'Date':>12s}  {'Entry':>10s}  "
          f"{'Score':>6s}  {'Trough':>7s}  "
          f"{'1d':>6s}  {'3d':>6s}  {'7d':>6s}  {'14d':>6s}  "
          f"{'MaxGain':>7s}")
    print(f"  {'─'*76}")

    for i, sig in enumerate(signals):
        dt = datetime.utcfromtimestamp(sig["time"])
        r1d = sig.get("return_1d")
        r3d = sig.get("return_3d")
        r7d = sig.get("return_7d")
        r14d = sig.get("return_14d")
        mg = sig.get("max_gain_pct")

        def fmt(v):
            if v is None:
                return "   —  "
            return f"{v:+6.1f}%"

        print(f"  {i+1:3d}  {dt.strftime('%Y-%m-%d'):>12s}  "
              f"${sig['entry_price']:>9,.0f}  "
              f"{sig['breath_score']:>6.1f}  "
              f"{sig['trough_score']:>7.1f}  "
              f"{fmt(r1d)}  {fmt(r3d)}  {fmt(r7d)}  {fmt(r14d)}  "
              f"{fmt(mg)}")

    # Summary stats
    print(f"\n{'='*80}")
    print(f"  SUMMARY STATISTICS")
    print(f"{'='*80}")

    for label in OUTCOME_BARS:
        returns = [sig[f"return_{label}"] for sig in signals
                   if sig.get(f"return_{label}") is not None]
        if not returns:
            continue

        wins = sum(1 for r in returns if r > 0)
        avg_ret = sum(returns) / len(returns)
        med_ret = sorted(returns)[len(returns) // 2]
        best = max(returns)
        worst = min(returns)
        wr = wins / len(returns) * 100

        print(f"\n  {label:>4s}:  WR={wr:.0f}%  "
              f"Avg={avg_ret:+.2f}%  Med={med_ret:+.2f}%  "
              f"Best={best:+.1f}%  Worst={worst:+.1f}%  "
              f"(n={len(returns)})")

    # Max gain stats
    gains = [sig["max_gain_pct"] for sig in signals
             if sig.get("max_gain_pct") is not None]
    drawdowns = [sig["max_drawdown_pct"] for sig in signals
                 if sig.get("max_drawdown_pct") is not None]

    if gains:
        print(f"\n  Max gain (14d window):")
        print(f"    Avg: {sum(gains)/len(gains):+.2f}%")
        print(f"    Med: {sorted(gains)[len(gains)//2]:+.2f}%")

    if drawdowns:
        print(f"\n  Max drawdown against you (14d window):")
        print(f"    Avg: {sum(drawdowns)/len(drawdowns):.2f}%")
        print(f"    Med: {sorted(drawdowns)[len(drawdowns)//2]:.2f}%")

    # Win rate by threshold depth
    print(f"\n{'='*80}")
    print(f"  WIN RATE BY TROUGH DEPTH")
    print(f"{'='*80}")

    for depth in [-8, -10, -12, -14]:
        deep = [s for s in signals
                if s["trough_score"] <= depth
                and s.get("return_3d") is not None]
        if deep:
            wr_3d = sum(1 for s in deep
                        if s["return_3d"] > 0) / len(deep) * 100
            avg_3d = sum(s["return_3d"] for s in deep) / len(deep)
            print(f"  Trough ≤ {depth}: {len(deep)} signals, "
                  f"3d WR={wr_3d:.0f}%, avg={avg_3d:+.2f}%")

    # Save results
    output_file = os.path.join(config.OUTPUT_DIR, "breathing_shorts.json")
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(signals, f, indent=2)
    print(f"\n  Saved to {output_file}")

    print(f"\n{'='*80}")


if __name__ == "__main__":
    run_backtest()
