"""Run sell indicator over BTC history and log signals."""

import json
import os
from datetime import datetime

import config
from indicator_sell import scan_all_sell_signals


def load_candles(filepath):
    with open(filepath) as f:
        return json.load(f)


def enrich_sell_signals(signals, candles):
    """Add post-signal price action (for shorts, gain = price drop)."""
    for sig in signals:
        bar = sig["bar_index"]
        entry_price = sig["price"]

        for label, offset in config.OUTCOME_BARS.items():
            future_bar = bar + offset
            if future_bar < len(candles):
                future_price = candles[future_bar]["close"]
                sig[f"price_{label}"] = future_price
                # For shorts: profit when price goes DOWN
                sig[f"return_{label}"] = (
                    (entry_price - future_price) / entry_price
                )
            else:
                sig[f"price_{label}"] = None
                sig[f"return_{label}"] = None

        end_bar = min(bar + config.OUTCOME_BARS["14d"], len(candles))
        if bar + 1 < end_bar:
            future_lows = [c["low"] for c in candles[bar + 1:end_bar]]
            future_highs = [c["high"] for c in candles[bar + 1:end_bar]]
            # For shorts: gain = how much price dropped
            sig["max_gain"] = (
                (entry_price - min(future_lows)) / entry_price
            )
            # For shorts: drawdown = how much price went UP against you
            sig["max_drawdown"] = (
                (entry_price - max(future_highs)) / entry_price
            )
        else:
            sig["max_gain"] = None
            sig["max_drawdown"] = None

        sig["win"] = (
            sig["max_gain"] is not None
            and sig["max_gain"] >= config.WIN_THRESHOLD
        )

    return signals


def add_gap_data(signals):
    for i, sig in enumerate(signals):
        if i == 0:
            sig["gap_hours"] = None
            sig["gap_bars"] = None
        else:
            prev = signals[i - 1]
            sig["gap_hours"] = (sig["time"] - prev["time"]) / 3600
            sig["gap_bars"] = sig["bar_index"] - prev["bar_index"]
    return signals


def run_sell_backtest(symbol="BTCUSDT", interval="4h"):
    candle_file = os.path.join(
        config.DATA_DIR,
        f"{symbol.lower()}_{interval}.json"
    )

    if not os.path.exists(candle_file):
        print(f"No candle data at {candle_file}")
        return None

    print(f"Loading candles from {candle_file}...")
    candles = load_candles(candle_file)
    print(f"Loaded {len(candles)} candles")

    first = datetime.utcfromtimestamp(candles[0]["time"])
    last = datetime.utcfromtimestamp(candles[-1]["time"])
    print(f"Date range: {first} -> {last}")

    print("\nScanning for SELL signals...")
    signals = scan_all_sell_signals(candles)
    print(f"Found {len(signals)} sell signals")

    if not signals:
        print("No sell signals found!")
        return None

    print("Enriching with post-signal price action (short perspective)...")
    signals = enrich_sell_signals(signals, candles)
    signals = add_gap_data(signals)

    output_file = os.path.join(
        config.DATA_DIR,
        f"{symbol.lower()}_sell_signals.json"
    )
    with open(output_file, "w") as f:
        json.dump(signals, f, indent=2)
    print(f"Saved {len(signals)} sell signals to {output_file}")

    # Summary
    print(f"\n{'='*60}")
    print(f"  {symbol} SELL SIGNAL BACKTEST")
    print(f"{'='*60}")

    total = len(signals)
    with_outcomes = [s for s in signals if s["max_gain"] is not None]
    wins = sum(1 for s in with_outcomes if s["win"])

    if with_outcomes:
        win_rate = wins / len(with_outcomes) * 100
    else:
        win_rate = 0

    print(f"\n  Total sell signals: {total}")
    print(f"  With outcomes:      {len(with_outcomes)}")
    print(f"  Wins (>{config.WIN_THRESHOLD*100:.0f}% drop): {wins}")
    print(f"  Win rate:           {win_rate:.1f}%")

    if with_outcomes:
        avg_gain = sum(s["max_gain"] for s in with_outcomes) / len(with_outcomes)
        avg_dd = sum(s["max_drawdown"] for s in with_outcomes) / len(with_outcomes)
        print(f"\n  Avg max gain (14d):     {avg_gain*100:.1f}%")
        print(f"  Avg max drawdown (14d): {avg_dd*100:.1f}%")

        for label in config.OUTCOME_BARS:
            returns = [s[f"return_{label}"] for s in with_outcomes
                       if s[f"return_{label}"] is not None]
            if returns:
                avg_ret = sum(returns) / len(returns)
                print(f"  Avg return ({label:>3s}):      {avg_ret*100:.1f}%")

    gaps = [s["gap_hours"] for s in signals if s["gap_hours"] is not None]
    if gaps:
        print(f"\n  Avg gap:   {sum(gaps)/len(gaps):.0f} hours "
              f"({sum(gaps)/len(gaps)/24:.1f} days)")

    print(f"\n  --- Sell Signal Timeline ---")
    for i, sig in enumerate(signals):
        dt = datetime.utcfromtimestamp(sig["time"])
        result = ("WIN" if sig["win"] else
                  "LOSS" if sig["max_gain"] is not None else
                  "pending")
        gap_str = (f"(gap: {sig['gap_hours']:.0f}h)"
                   if sig["gap_hours"] else "(first)")
        print(f"  {i+1:3d}. {dt.strftime('%Y-%m-%d %H:%M')} "
              f"${sig['price']:>10,.2f}  RSI={sig['rsi']:.1f}  "
              f"{result:>7s}  {gap_str}")

    print(f"\n{'='*60}")
    return signals


if __name__ == "__main__":
    run_sell_backtest("BTCUSDT", "4h")
