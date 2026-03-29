"""Fetch 5-minute candles for top 600 USDT pairs from Binance."""

import json
import os
import time
from datetime import datetime

import requests

import config

BINANCE_URL = "https://api.binance.us/api/v3/klines"
EXCHANGE_INFO_URL = "https://api.binance.us/api/v3/ticker/24hr"


def get_top_usdt_pairs(limit=600):
    """Get top USDT trading pairs by 24h volume from Binance."""
    print(f"Fetching top {limit} USDT pairs by volume...")
    try:
        r = requests.get(EXCHANGE_INFO_URL, timeout=30)
        tickers = r.json()
        if not isinstance(tickers, list):
            print(f"Unexpected response: {str(tickers)[:200]}")
            # Fallback: try binance.com
            print("Trying binance.com...")
            r = requests.get(
                "https://api.binance.com/api/v3/ticker/24hr",
                timeout=30)
            tickers = r.json()
            if not isinstance(tickers, list):
                print(f"Also failed: {str(tickers)[:200]}")
                return []
    except Exception as e:
        print(f"Error fetching ticker data: {e}")
        return []

    # Filter USDT pairs, exclude stablecoins and leveraged tokens
    skip = {
        "USDCUSDT", "BUSDUSDT", "TUSDUSDT", "DAIUSDT", "FDUSDUSDT",
        "USDPUSDT", "EURUSDT", "GBPUSDT", "AUDUSDT", "TRYUSDT",
    }
    usdt_pairs = []
    for t in tickers:
        sym = t["symbol"]
        if not sym.endswith("USDT"):
            continue
        if sym in skip:
            continue
        # Skip leveraged tokens (3L, 3S, UP, DOWN, BULL, BEAR)
        base = sym.replace("USDT", "")
        if any(base.endswith(x) for x in
               ["3L", "3S", "UP", "DOWN", "BULL", "BEAR", "2L", "2S",
                "4L", "4S", "5L", "5S"]):
            continue
        try:
            vol = float(t["quoteVolume"])  # volume in USDT
        except (KeyError, ValueError):
            continue
        usdt_pairs.append({"symbol": sym, "volume": vol})

    usdt_pairs.sort(key=lambda x: x["volume"], reverse=True)
    top = usdt_pairs[:limit]

    print(f"Found {len(usdt_pairs)} USDT pairs, taking top {len(top)}")
    if top:
        print(f"  #1:   {top[0]['symbol']} (${top[0]['volume']:,.0f} vol)")
        print(f"  #50:  {top[49]['symbol']} (${top[49]['volume']:,.0f} vol)"
              if len(top) > 49 else "")
        print(f"  #{len(top)}: {top[-1]['symbol']} "
              f"(${top[-1]['volume']:,.0f} vol)")

    return [t["symbol"] for t in top]


def fetch_5m(symbol, days=730):
    """Fetch 5min candles from Binance."""
    all_candles = []
    end_time = int(time.time() * 1000)
    cutoff = end_time - (days * 86400 * 1000)
    batch = 0

    while end_time > cutoff:
        try:
            r = requests.get(BINANCE_URL, params={
                "symbol": symbol,
                "interval": "5m",
                "endTime": end_time,
                "limit": 1000,
            }, timeout=15)
            data = r.json()
        except Exception as e:
            time.sleep(2)
            continue

        if not isinstance(data, list) or len(data) == 0:
            break

        for k in data:
            candle = {
                "time": k[0] // 1000,
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            }
            if candle["time"] * 1000 >= cutoff:
                all_candles.append(candle)

        earliest_ms = data[0][0]
        if earliest_ms <= cutoff or earliest_ms >= end_time:
            break
        end_time = earliest_ms - 1

        batch += 1
        time.sleep(0.1)

    # Deduplicate and sort
    seen = set()
    unique = []
    for c in all_candles:
        if c["time"] not in seen:
            seen.add(c["time"])
            unique.append(c)
    unique.sort(key=lambda c: c["time"])

    return unique


def fetch_all(days=730, limit=600):
    """Fetch 5min data for top N coins."""
    coins = get_top_usdt_pairs(limit)
    if not coins:
        print("No coins found!")
        return

    # Save coin list
    list_file = os.path.join(config.DATA_DIR, "5m", "coin_list_600.json")
    os.makedirs(os.path.dirname(list_file), exist_ok=True)
    with open(list_file, "w") as f:
        json.dump(coins, f, indent=2)

    data_dir = os.path.join(config.DATA_DIR, "5m")
    os.makedirs(data_dir, exist_ok=True)

    print(f"\nFetching {len(coins)} coins, {days} days of 5min data\n")

    fetched = 0
    cached = 0
    failed = 0

    for i, coin in enumerate(coins):
        outfile = os.path.join(data_dir, f"{coin.lower()}_5m.json")

        # Skip if already fetched recently
        if os.path.exists(outfile):
            age_hours = (time.time() - os.path.getmtime(outfile)) / 3600
            if age_hours < 12:
                cached += 1
                if (i + 1) % 50 == 0:
                    print(f"  [{i+1}/{len(coins)}] ... {cached} cached")
                continue

        print(f"  [{i+1}/{len(coins)}] {coin}: ", end="", flush=True)

        candles = fetch_5m(coin, days)
        if candles and len(candles) > 1000:
            with open(outfile, "w") as f:
                json.dump(candles, f)

            first = datetime.utcfromtimestamp(candles[0]["time"])
            last = datetime.utcfromtimestamp(candles[-1]["time"])
            print(f"{len(candles)} candles "
                  f"({first:%Y-%m-%d} -> {last:%Y-%m-%d})")
            fetched += 1
        else:
            n = len(candles) if candles else 0
            print(f"skipped ({n} candles)")
            failed += 1

        time.sleep(0.3)

    print(f"\nDone! {fetched} fetched, {cached} cached, {failed} skipped")
    print(f"Total coins with data: {fetched + cached}")


if __name__ == "__main__":
    import sys
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 600
    fetch_all(days=730, limit=limit)
