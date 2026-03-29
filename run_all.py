"""Fetch + backtest all major cryptos for composite rhythm analysis."""

import sys
import os
import time

from fetch import fetch_candles
from backtest import run_backtest
from backtest_sell import run_sell_backtest
import config


SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "DOGEUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "DOTUSDT",
    "MATICUSDT",
]


def run_all():
    results = {}

    for symbol in SYMBOLS:
        candle_file = os.path.join(
            config.DATA_DIR,
            f"{symbol.lower()}_4h.json"
        )
        signal_file = os.path.join(
            config.DATA_DIR,
            f"{symbol.lower()}_signals.json"
        )
        sell_file = os.path.join(
            config.DATA_DIR,
            f"{symbol.lower()}_sell_signals.json"
        )

        # Skip if already done
        if (os.path.exists(signal_file)
                and os.path.exists(sell_file)):
            print(f"\n{'='*60}")
            print(f"  {symbol} — already done, skipping")
            print(f"{'='*60}")
            results[symbol] = "skipped (exists)"
            continue

        print(f"\n{'#'*60}")
        print(f"  PROCESSING: {symbol}")
        print(f"{'#'*60}")

        # Fetch candles if needed
        if not os.path.exists(candle_file):
            print(f"\n--- Fetching {symbol} candles ---")
            try:
                candles = fetch_candles(symbol, "4h", days=2200)
                if not candles:
                    print(f"  FAILED to fetch {symbol}")
                    results[symbol] = "FETCH FAILED"
                    continue
            except Exception as e:
                print(f"  ERROR fetching {symbol}: {e}")
                results[symbol] = f"FETCH ERROR: {e}"
                continue
            time.sleep(2)

        # Run buy backtest
        if not os.path.exists(signal_file):
            print(f"\n--- Buy backtest {symbol} ---")
            try:
                sigs = run_backtest(symbol, "4h")
                if sigs is None:
                    results[symbol] = "NO BUY SIGNALS"
                    continue
            except Exception as e:
                print(f"  ERROR in buy backtest: {e}")
                results[symbol] = f"BUY ERROR: {e}"
                continue

        # Run sell backtest
        if not os.path.exists(sell_file):
            print(f"\n--- Sell backtest {symbol} ---")
            try:
                sigs = run_sell_backtest(symbol, "4h")
                if sigs is None:
                    results[symbol] = "NO SELL SIGNALS"
                    continue
            except Exception as e:
                print(f"  ERROR in sell backtest: {e}")
                results[symbol] = f"SELL ERROR: {e}"
                continue

        results[symbol] = "OK"

    # Summary
    print(f"\n\n{'='*60}")
    print(f"  ALL SYMBOLS — STATUS")
    print(f"{'='*60}\n")

    for sym, status in results.items():
        print(f"  {sym:<12s}  {status}")


if __name__ == "__main__":
    run_all()
