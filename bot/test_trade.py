"""
Quick test: open a tiny ETH short and close it immediately.
Verifies Bitunix API connectivity, authentication, and order flow.
Uses 3x leverage, minimum order size (0.003 ETH ≈ $6 notional, ~$2 margin).
"""
import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from bitunix_api import BitunixClient

api = BitunixClient(
    os.environ.get("BITUNIX_API_KEY"),
    os.environ.get("BITUNIX_API_SECRET")
)

print("=== RhythmFilter API Test ===\n")

# 1. Test balance
print("1. Fetching balance...")
bal = api.get_balance()
print(f"   Response: {json.dumps(bal, indent=2)[:500]}")

# 2. Set 3x leverage + isolated margin for ETHUSDT
print("\n2. Setting 3x leverage (isolated)...")
lev = api.change_leverage("ETHUSDT", "3")
print(f"   Leverage: {json.dumps(lev, indent=2)[:300]}")
margin = api.change_margin_mode("ETHUSDT", "ISOLATION")
print(f"   Margin mode: {json.dumps(margin, indent=2)[:300]}")

# 3. Test get positions
print("\n3. Fetching open positions...")
pos = api.get_positions()
print(f"   Response: {json.dumps(pos, indent=2)[:500]}")

# 4. Open minimum short: 0.003 ETH at 3x
print("\n4. Opening tiny ETHUSDT short (market order)...")
print("   Symbol: ETHUSDT, Side: SELL, Qty: 0.003 ETH, Leverage: 3x")
order = api.place_order(
    symbol="ETHUSDT",
    side="SELL",
    qty="0.003",
    order_type="MARKET",
    trade_side="OPEN"
)
print(f"   Order response: {json.dumps(order, indent=2)[:500]}")

if order.get("code") == 0 or order.get("msg") == "Success":
    print("\n   Order placed! Waiting 3 seconds...")
    time.sleep(3)

    # 5. Check position
    print("\n5. Checking position...")
    pos2 = api.get_positions("ETHUSDT")
    print(f"   Positions: {json.dumps(pos2, indent=2)[:500]}")

    # 6. Close it
    print("\n6. Closing position (market buy)...")
    close = api.place_order(
        symbol="ETHUSDT",
        side="BUY",
        qty="0.003",
        order_type="MARKET",
        trade_side="CLOSE"
    )
    print(f"   Close response: {json.dumps(close, indent=2)[:500]}")
else:
    print(f"\n   Order FAILED. Check API keys and balance.")
    print(f"   You need USDT in your futures wallet to trade.")

print("\n=== Test Complete ===")
