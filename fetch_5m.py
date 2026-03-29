"""Fetch 5-minute candles from Binance API (free, no auth)."""

import json
import os
import time
from datetime import datetime

import requests

import config

BINANCE_URL = "https://api.binance.us/api/v3/klines"

COINS = [
    "BTCUSDT", "ETHUSDT", "XRPUSDT", "BNBUSDT", "SOLUSDT",
    "DOGEUSDT", "ADAUSDT", "TRXUSDT", "AVAXUSDT", "SHIBUSDT",
    "TONUSDT", "LINKUSDT", "SUIUSDT", "DOTUSDT", "NEARUSDT",
    "UNIUSDT", "APTUSDT", "POLUSDT", "ARBUSDT", "OPUSDT",
    "ICPUSDT", "HBARUSDT", "FILUSDT", "ATOMUSDT", "IMXUSDT",
    "INJUSDT", "STXUSDT", "SUSDT", "GRTUSDT", "THETAUSDT",
    "ALGOUSDT", "LDOUSDT", "AAVEUSDT", "SKYUSDT", "SNXUSDT",
    "VETUSDT", "XLMUSDT", "PEPEUSDT", "FETUSDT", "WLDUSDT",
]


def fetch_5m(symbol, days=730):
    """Fetch 5min candles from Binance. Returns list of candle dicts."""
    all_candles = []
    end_time = int(time.time() * 1000)
    cutoff = end_time - (days * 86400 * 1000)
    batch = 0

    print(f"  {symbol}: fetching...", end="", flush=True)

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
            print(f" error: {e}", end="", flush=True)
            time.sleep(2)
            continue

        if not isinstance(data, list) or len(data) == 0:
            break

        for k in data:
            candle = {
                "time": k[0] // 1000,  # ms -> seconds
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            }
            if candle["time"] * 1000 >= cutoff:
                all_candles.append(candle)

        # Move backward
        earliest_ms = data[0][0]
        if earliest_ms <= cutoff or earliest_ms >= end_time:
            break
        end_time = earliest_ms - 1

        batch += 1
        if batch % 50 == 0:
            dt = datetime.utcfromtimestamp(earliest_ms / 1000)
            print(f" {dt:%Y-%m}", end="", flush=True)

        time.sleep(0.1)  # Stay under rate limit

    # Deduplicate and sort
    seen = set()
    unique = []
    for c in all_candles:
        if c["time"] not in seen:
            seen.add(c["time"])
            unique.append(c)
    unique.sort(key=lambda c: c["time"])

    print(f" {len(unique)} candles")
    return unique


def fetch_all(days=730):
    """Fetch 5min data for all 40 coins."""
    data_dir = os.path.join(config.DATA_DIR, "5m")
    os.makedirs(data_dir, exist_ok=True)

    print(f"Fetching {len(COINS)} coins, {days} days of 5min data\n")

    for coin in COINS:
        outfile = os.path.join(data_dir, f"{coin.lower()}_5m.json")

        # Skip if already fetched recently
        if os.path.exists(outfile):
            age_hours = (time.time() - os.path.getmtime(outfile)) / 3600
            if age_hours < 12:
                print(f"  {coin}: cached ({age_hours:.0f}h old)")
                continue

        candles = fetch_5m(coin, days)
        if candles:
            with open(outfile, "w") as f:
                json.dump(candles, f)

            first = datetime.utcfromtimestamp(candles[0]["time"])
            last = datetime.utcfromtimestamp(candles[-1]["time"])
            print(f"    {first:%Y-%m-%d} -> {last:%Y-%m-%d}")

        time.sleep(0.5)

    print("\nDone!")


if __name__ == "__main__":
    fetch_all(days=730)
