"""Run v8 indicator over historical candles and log signals with outcomes."""

import json
import os
from datetime import datetime

import config
from indicator import scan_all_signals


def load_candles(filepath):
    """Load candle data from JSON file."""
    with open(filepath) as f:
        return json.load(f)


def enrich_signals(signals, candles):
    """Add post-signal price action to each signal.

    For each signal, record what happened 1d, 3d, 7d, 14d later.
    """
    for sig in signals:
        bar = sig["bar_index"]
        entry_price = sig["price"]

        for label, offset in config.OUTCOME_BARS.items():
            future_bar = bar + offset
            if future_bar < len(candles):
                sig[f"price_{label}"] = candles[future_bar]["close"]
                sig[f"return_{label}"] = (
                    (candles[future_bar]["close"] - entry_price)
                    / entry_price
                )
            else:
                sig[f"price_{label}"] = None
                sig[f"return_{label}"] = None

        # Max gain and max drawdown in the 14-day window
        end_bar = min(bar + config.OUTCOME_BARS["14d"], len(candles))
        if bar + 1 < end_bar:
            future_highs = [c["high"] for c in candles[bar + 1:end_bar]]
            future_lows = [c["low"] for c in candles[bar + 1:end_bar]]
            sig["max_gain"] = (
                (max(future_highs) - entry_price) / entry_price
            )
            sig["max_drawdown"] = (
                (min(future_lows) - entry_price) / entry_price
            )
        else:
            sig["max_gain"] = None
            sig["max_drawdown"] = None

        # Win = price went up more than threshold in 14d window
        sig["win"] = (
            sig["max_gain"] is not None
            and sig["max_gain"] >= config.WIN_THRESHOLD
        )

    return signals


def add_gap_data(signals):
    """Add time gap between consecutive signals."""
    for i, sig in enumerate(signals):
        if i == 0:
            sig["gap_hours"] = None
            sig["gap_bars"] = None
        else:
            prev = signals[i - 1]
            sig["gap_hours"] = (
                (sig["time"] - prev["time"]) / 3600
            )
            sig["gap_bars"] = sig["bar_index"] - prev["bar_index"]

    return signals


def run_backtest(symbol="BTCUSDT", interval="4h"):
    """Full backtest pipeline: load candles, find signals, enrich."""
    candle_file = os.path.join(
        config.DATA_DIR,
        f"{symbol.lower()}_{interval}.json"
    )

    if not os.path.exists(candle_file):
        print(f"No candle data found at {candle_file}")
        print("Run fetch.py first to download candle data.")
        return None

    print(f"Loading candles from {candle_file}...")
    candles = load_candles(candle_file)
    print(f"Loaded {len(candles)} candles")

    first = datetime.utcfromtimestamp(candles[0]["time"])
    last = datetime.utcfromtimestamp(candles[-1]["time"])
    print(f"Date range: {first} -> {last}")

    # Find all v8 buy signals
    print("\nScanning for v8 buy signals...")
    signals = scan_all_signals(candles)
    print(f"Found {len(signals)} buy signals")

    if not signals:
        print("No signals found!")
        return None

    # Enrich with post-signal outcomes
    print("Enriching with post-signal price action...")
    signals = enrich_signals(signals, candles)

    # Add gap data
    signals = add_gap_data(signals)

    # Save signals
    os.makedirs(config.DATA_DIR, exist_ok=True)
    output_file = os.path.join(
        config.DATA_DIR,
        f"{symbol.lower()}_signals.json"
    )
    with open(output_file, "w") as f:
        json.dump(signals, f, indent=2)
    print(f"Saved {len(signals)} signals to {output_file}")

    # Print summary
    print_summary(signals, symbol)

    return signals


def print_summary(signals, symbol):
    """Print quick stats about the signals found."""
    print(f"\n{'='*60}")
    print(f"  {symbol} v8 BACKTEST SUMMARY")
    print(f"{'='*60}")

    total = len(signals)
    wins = sum(1 for s in signals if s["win"])
    losses = total - wins

    # Some signals near the end won't have outcome data
    with_outcomes = [s for s in signals if s["max_gain"] is not None]
    if with_outcomes:
        win_rate = sum(1 for s in with_outcomes if s["win"]) / len(with_outcomes)
    else:
        win_rate = 0

    print(f"\n  Total signals:  {total}")
    print(f"  With outcomes:  {len(with_outcomes)}")
    print(f"  Wins (>{config.WIN_THRESHOLD*100:.0f}%):   {wins}")
    print(f"  Losses:         {losses}")
    print(f"  Win rate:       {win_rate*100:.1f}%")

    if with_outcomes:
        avg_gain = sum(
            s["max_gain"] for s in with_outcomes
        ) / len(with_outcomes)
        avg_dd = sum(
            s["max_drawdown"] for s in with_outcomes
        ) / len(with_outcomes)
        print(f"\n  Avg max gain (14d):     {avg_gain*100:.1f}%")
        print(f"  Avg max drawdown (14d): {avg_dd*100:.1f}%")

    # Returns by timeframe
    for label in config.OUTCOME_BARS:
        returns = [
            s[f"return_{label}"] for s in with_outcomes
            if s[f"return_{label}"] is not None
        ]
        if returns:
            avg_ret = sum(returns) / len(returns)
            print(f"  Avg return ({label:>3s}):      {avg_ret*100:.1f}%")

    # Gap analysis
    gaps = [s["gap_hours"] for s in signals if s["gap_hours"] is not None]
    if gaps:
        print(f"\n  --- Signal Gaps ---")
        print(f"  Avg gap:   {sum(gaps)/len(gaps):.0f} hours "
              f"({sum(gaps)/len(gaps)/24:.1f} days)")
        print(f"  Min gap:   {min(gaps):.0f} hours "
              f"({min(gaps)/24:.1f} days)")
        print(f"  Max gap:   {max(gaps):.0f} hours "
              f"({max(gaps)/24:.1f} days)")

    # Signal dates
    print(f"\n  --- Signal Timeline ---")
    for i, sig in enumerate(signals):
        dt = datetime.utcfromtimestamp(sig["time"])
        result = "WIN" if sig["win"] else "LOSS" if sig["max_gain"] is not None else "pending"
        gap_str = f"(gap: {sig['gap_hours']:.0f}h)" if sig["gap_hours"] else "(first)"
        print(f"  {i+1:3d}. {dt.strftime('%Y-%m-%d %H:%M')} "
              f"${sig['price']:>10,.2f}  RSI={sig['rsi']:.1f}  "
              f"{result:>7s}  {gap_str}")

    print(f"\n{'='*60}")


if __name__ == "__main__":
    run_backtest("BTCUSDT", "4h")
