"""Bitunix Futures API client.

Handles authentication, order placement, and account management.
Docs: https://www.bitunix.com/api-docs/futures/
"""

import hashlib
import json
import logging
import os
import time
import uuid

import requests

BASE_URL = "https://fapi.bitunix.com"
RETRY_DELAYS = [2, 4, 8]

log = logging.getLogger("rhythm_bot")


class BitunixClient:
    def __init__(self, api_key=None, secret_key=None):
        self.api_key = api_key or os.environ.get("BITUNIX_API_KEY", "")
        self.secret_key = secret_key or os.environ.get("BITUNIX_API_SECRET", "")
        self.session = requests.Session()

    def _retry(self, fn, max_retries=3):
        """Retry a request with exponential backoff."""
        for attempt in range(max_retries + 1):
            try:
                return fn()
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                if attempt >= max_retries:
                    raise
                delay = RETRY_DELAYS[min(
                    attempt, len(RETRY_DELAYS) - 1)]
                log.warning(
                    f"Bitunix request failed "
                    f"(attempt {attempt + 1}/{max_retries})"
                    f", retry in {delay}s: {e}")
                time.sleep(delay)
                self.session = requests.Session()

    def _sha256(self, s):
        return hashlib.sha256(s.encode("utf-8")).hexdigest()

    def _sign(self, query_params="", body=""):
        """Create Bitunix double-SHA256 signature."""
        nonce = uuid.uuid4().hex
        timestamp = str(int(time.time() * 1000))

        # Step 1: digest = sha256(nonce + timestamp + apiKey + params + body)
        digest_input = nonce + timestamp + self.api_key + query_params + body
        digest = self._sha256(digest_input)

        # Step 2: sign = sha256(digest + secretKey)
        sign = self._sha256(digest + self.secret_key)

        return {
            "api-key": self.api_key,
            "sign": sign,
            "nonce": nonce,
            "timestamp": timestamp,
            "language": "en-US",
            "Content-Type": "application/json",
        }

    def _get(self, path, params=None):
        """Authenticated GET request with retry."""
        def do_request():
            query_str = ""
            if params:
                sorted_params = sorted(params.items())
                query_str = "".join(
                    f"{k}{v}" for k, v in sorted_params)
            headers = self._sign(query_params=query_str)
            url = BASE_URL + path
            r = self.session.get(
                url, params=params,
                headers=headers, timeout=30)
            return r.json()
        return self._retry(do_request)

    def _post(self, path, data=None):
        """Authenticated POST request with retry."""
        def do_request():
            body = json.dumps(
                data, separators=(",", ":")) if data else ""
            headers = self._sign(body=body)
            url = BASE_URL + path
            r = self.session.post(
                url, data=body,
                headers=headers, timeout=30)
            return r.json()
        return self._retry(do_request)

    # === MARKET DATA (public, no auth needed) ===

    def get_tickers(self, symbols=None):
        """Get current prices for trading pairs."""
        def do_request():
            params = {}
            if symbols:
                params["symbols"] = ",".join(symbols)
            url = BASE_URL + "/api/v1/futures/market/tickers"
            r = self.session.get(
                url, params=params, timeout=30)
            return r.json()
        return self._retry(do_request)

    def get_trading_pairs(self, symbols=None):
        """Get available trading pairs."""
        def do_request():
            params = {}
            if symbols:
                params["symbols"] = ",".join(symbols)
            url = (BASE_URL
                   + "/api/v1/futures/market/trading_pairs")
            r = self.session.get(
                url, params=params, timeout=30)
            return r.json()
        return self._retry(do_request)

    # === ACCOUNT ===

    def change_leverage(self, symbol, leverage):
        """Set leverage for a trading pair."""
        return self._post("/api/v1/futures/account/change_leverage", {
            "symbol": symbol,
            "leverage": leverage,
            "marginCoin": "USDT",
        })

    def change_margin_mode(self, symbol, mode="ISOLATION"):
        """Set margin mode: ISOLATION or CROSS."""
        return self._post("/api/v1/futures/account/change_margin_mode", {
            "symbol": symbol,
            "marginMode": mode,
            "marginCoin": "USDT",
        })

    def get_balance(self):
        """Get account balance (USDT available)."""
        return self._get("/api/v1/futures/account/balance",
                         {"marginCoin": "USDT"})

    def get_positions(self, symbol=None):
        """Get open positions."""
        params = {}
        if symbol:
            params["symbol"] = symbol
        return self._get("/api/v1/futures/position/get_pending_positions",
                         params)

    def get_history_positions(self, symbol=None, page=1, page_size=50):
        """Get closed position history."""
        params = {"page": str(page), "pageSize": str(page_size)}
        if symbol:
            params["symbol"] = symbol
        return self._get(
            "/api/v1/futures/position/get_history_positions", params)

    # === TRADING ===

    def place_order(self, symbol, side, qty, order_type="MARKET",
                    price=None, tp_price=None, sl_price=None,
                    trade_side="OPEN", client_id=None):
        """Place a futures order with optional TP/SL.

        Args:
            symbol: e.g. "BTCUSDT"
            side: "BUY" or "SELL" (SELL to open short)
            qty: amount in base coin (string)
            order_type: "MARKET" or "LIMIT"
            price: limit price (required for LIMIT)
            tp_price: take profit trigger price
            sl_price: stop loss trigger price
            trade_side: "OPEN" or "CLOSE"
            client_id: custom order ID
        """
        data = {
            "symbol": symbol,
            "side": side,
            "qty": str(qty),
            "orderType": order_type,
            "tradeSide": trade_side,
        }

        if price:
            data["price"] = str(price)
        if tp_price:
            data["tpPrice"] = str(tp_price)
            data["tpStopType"] = "LAST_PRICE"
            data["tpOrderType"] = "MARKET"
        if sl_price:
            data["slPrice"] = str(sl_price)
            data["slStopType"] = "LAST_PRICE"
            data["slOrderType"] = "MARKET"
        if client_id:
            data["clientId"] = client_id

        return self._post("/api/v1/futures/trade/place_order", data)

    def open_short(self, symbol, qty, tp_price=None, sl_price=None):
        """Open a short position (convenience method)."""
        return self.place_order(
            symbol=symbol,
            side="SELL",
            qty=qty,
            tp_price=tp_price,
            sl_price=sl_price,
            trade_side="OPEN",
        )

    def close_short(self, symbol, qty, position_id=None):
        """Close a short position (convenience method)."""
        data = {
            "symbol": symbol,
            "side": "BUY",
            "qty": str(qty),
            "orderType": "MARKET",
            "tradeSide": "CLOSE",
        }
        if position_id:
            data["positionId"] = position_id
        return self._post("/api/v1/futures/trade/place_order", data)


# Quick test
if __name__ == "__main__":
    client = BitunixClient()

    # Test public endpoint
    print("Getting trading pairs...")
    pairs = client.get_trading_pairs(["BTCUSDT", "ETHUSDT"])
    if pairs.get("code") == 0:
        for p in pairs["data"]:
            print(f"  {p['symbol']}: "
                  f"leverage {p['minLeverage']}-{p['maxLeverage']}x, "
                  f"min qty {p['minTradeVolume']}")
    else:
        print(f"  Error: {pairs}")

    print("\nGetting tickers...")
    tickers = client.get_tickers(["BTCUSDT", "ETHUSDT"])
    if tickers.get("code") == 0:
        for t in tickers["data"]:
            print(f"  {t['symbol']}: ${t['lastPrice']}")
    else:
        print(f"  Error: {tickers}")
