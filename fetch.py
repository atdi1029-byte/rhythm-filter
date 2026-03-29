"""Fetch historical candles from CryptoCompare (free, no auth).

Fetches 1H candles and aggregates to 4H for the v8 indicator.
"""

import json
import os
import time
from datetime import datetime

import requests

import config

CRYPTOCOMPARE_BASE = "https://min-api.cryptocompare.com/data/v2/histohour"


def fetch_1h_candles(fsym="BTC", tsym="USD", days=2200):
    """Fetch hourly candles from CryptoCompare.

    Uses toTs pagination to walk backwards in time.
    Max 2000 candles per request.
    """
    all_candles = []
    to_ts = int(time.time())
    cutoff = to_ts - (days * 86400)
    batch = 0

    print(f"Fetching {fsym}/{tsym} 1H candles "
          f"(~{days} days of history)...")

    while to_ts > cutoff:
        try:
            r = requests.get(CRYPTOCOMPARE_BASE, params={
                "fsym": fsym,
                "tsym": tsym,
                "limit": 2000,
                "toTs": to_ts,
            }, timeout=15)
            data = r.json()
        except Exception as e:
            print(f"  Error batch {batch}: {e}")
            time.sleep(3)
            continue

        if data.get("Response") != "Success":
            print(f"  API error: {data.get('Message', 'unknown')}")
            break

        candles = data["Data"]["Data"]
        if not candles:
            break

        # Filter out zero-volume candles at the very start
        valid = [c for c in candles
                 if c["open"] > 0 and c["close"] > 0]

        all_candles.extend(valid)

        # Move backward
        earliest = candles[0]["time"]
        if earliest <= cutoff or earliest >= to_ts:
            break
        to_ts = earliest - 1

        batch += 1
        if batch % 5 == 0:
            dt = datetime.utcfromtimestamp(earliest)
            print(f"  Batch {batch}: {len(all_candles)} "
                  f"1H candles, back to {dt.strftime('%Y-%m-%d')}")

        time.sleep(0.5)

    # Deduplicate by timestamp
    seen = set()
    unique = []
    for c in all_candles:
        if c["time"] not in seen:
            seen.add(c["time"])
            unique.append(c)

    unique.sort(key=lambda c: c["time"])
    print(f"  Total 1H candles: {len(unique)}")
    return unique


def aggregate_to_4h(hourly_candles):
    """Aggregate 1H candles into 4H candles.

    Groups by 4-hour windows (0:00, 4:00, 8:00, 12:00, 16:00, 20:00).
    """
    # Group candles into 4H buckets
    buckets = {}
    for c in hourly_candles:
        # Round down to nearest 4-hour boundary
        bucket_ts = c["time"] - (c["time"] % (4 * 3600))
        if bucket_ts not in buckets:
            buckets[bucket_ts] = []
        buckets[bucket_ts].append(c)

    # Aggregate each bucket
    candles_4h = []
    for bucket_ts in sorted(buckets.keys()):
        group = buckets[bucket_ts]
        if len(group) < 3:  # Need at least 3 of 4 hours
            continue

        # Sort by time to get proper OHLC
        group.sort(key=lambda c: c["time"])

        candle = {
            "time": bucket_ts,
            "open": group[0]["open"],
            "high": max(c["high"] for c in group),
            "low": min(c["low"] for c in group),
            "close": group[-1]["close"],
            "volume": sum(c["volumeto"] for c in group),
        }
        candles_4h.append(candle)

    return candles_4h


def fetch_candles(symbol="BTCUSDT", interval="4h",
                  days=2200, output_file=None):
    """Main entry: fetch and save 4H candles.

    Args:
        symbol: pair name (BTCUSDT -> fsym=BTC, tsym=USD)
        interval: only "4h" supported
        days: days of history (default ~6 years, back to 2020)
        output_file: where to save
    """
    # Parse symbol
    fsym = symbol.replace("USDT", "").replace("USD", "")
    tsym = "USD"

    if output_file is None:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        output_file = os.path.join(
            config.DATA_DIR,
            f"{symbol.lower()}_{interval}.json"
        )

    # Fetch 1H candles
    hourly = fetch_1h_candles(fsym, tsym, days)

    if not hourly:
        print("No candle data received!")
        return []

    # Aggregate to 4H
    print("Aggregating 1H -> 4H...")
    candles = aggregate_to_4h(hourly)

    # Save
    with open(output_file, "w") as f:
        json.dump(candles, f)

    first = datetime.utcfromtimestamp(candles[0]["time"])
    last = datetime.utcfromtimestamp(candles[-1]["time"])
    print(f"\nDone: {len(candles)} 4H candles saved")
    print(f"Date range: {first} -> {last}")
    print(f"Saved to: {output_file}")

    return candles


if __name__ == "__main__":
    candles = fetch_candles("BTCUSDT", "4h", days=2200)
    if candles:
        print(f"\nFirst: {candles[0]}")
        print(f"Last:  {candles[-1]}")
