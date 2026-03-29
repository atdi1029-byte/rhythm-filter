"""RhythmFilter Trading Bot — Bitunix Futures.

Receives SHORT signals from TradingView via Apps Script webhook.
Opens shorts on approved coins when signal arrives.
Uses flat SL/TP with 7-day max hold.
Syncs state to Apps Script for dashboard.

Usage:
    python rhythm_bot.py              # dry run (paper trading)
    python rhythm_bot.py --live       # real money
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

# Load .env from bot directory
load_dotenv(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '.env'))

# Add parent dir to path for config
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from bot.bitunix_api import BitunixClient

# === SETTINGS ===
SL_PCT = 7.0          # stop loss %
TP_PCT = 12.0         # take profit %
LEVERAGE = 1           # start conservative (override with --leverage)
MAX_HOLD_BARS = 4032   # 14 days on 5min
MIN_POSITION_USD = 5.0 # minimum order size in USDT
POLL_INTERVAL = 30     # check for signals every 30 seconds
POSITION_CHECK = 300   # check exits every 5 min

# Files
STATE_FILE = os.path.join(
    os.path.dirname(__file__), "bot_state.json")
LOG_FILE = os.path.join(
    os.path.dirname(__file__), "bot.log")
BLACKLIST_FILE = os.path.join(
    config.DATA_DIR, "blacklist.json")
APPROVED_FILE = os.path.join(
    config.DATA_DIR, "approved_coins_7_12.json")

# Cloud sync
APPS_SCRIPT_URL = os.environ.get(
    "RHYTHM_APPS_SCRIPT_URL", "")

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger("rhythm_bot")


# === DATA LOADING ===

def load_blacklist():
    if os.path.exists(BLACKLIST_FILE):
        with open(BLACKLIST_FILE) as f:
            return set(json.load(f))
    return set()


def load_approved_coins():
    """Load approved coin list (ordered by market cap)."""
    if os.path.exists(APPROVED_FILE):
        with open(APPROVED_FILE) as f:
            return json.load(f)
    return []


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "open_positions": [],
        "signal_history": [],
        "total_pnl": 0.0,
        "total_trades": 0,
        "total_wins": 0,
        "breathing_score": 0.0,
        "signal_state": "waiting",
    }


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    sync_to_cloud(state)


# === CLOUD SYNC ===

def sync_to_cloud(state):
    """Push state to Apps Script for dashboard."""
    if not APPS_SCRIPT_URL:
        return
    try:
        data = json.dumps(state, separators=(",", ":"))
        r = requests.get(APPS_SCRIPT_URL, params={
            "action": "save_state",
            "data": data,
        }, timeout=10)
        if r.status_code == 200:
            log.debug("Synced to cloud")
        else:
            log.warning(
                f"Cloud sync failed: {r.status_code}")
    except Exception as e:
        log.warning(f"Cloud sync error: {e}")


def log_trade_to_cloud(trade_data):
    """Log a closed trade to Apps Script."""
    if not APPS_SCRIPT_URL:
        return
    try:
        data = json.dumps(
            trade_data, separators=(",", ":"))
        requests.get(APPS_SCRIPT_URL, params={
            "action": "log_trade",
            "data": data,
        }, timeout=10)
    except Exception as e:
        log.warning(f"Trade log error: {e}")


# === SIGNAL POLLING ===

def poll_for_signal():
    """Check Apps Script for new SHORT signal from TV.

    Returns signal dict or None.
    """
    if not APPS_SCRIPT_URL:
        return None
    try:
        r = requests.get(APPS_SCRIPT_URL, params={
            "action": "get_signal",
        }, timeout=10)
        data = r.json()
        if data.get("status") == "success":
            return data.get("signal")
    except Exception as e:
        log.warning(f"Signal poll error: {e}")
    return None


def ack_signal(signal_id):
    """Mark signal as processed so we don't re-execute."""
    if not APPS_SCRIPT_URL:
        return
    try:
        requests.get(APPS_SCRIPT_URL, params={
            "action": "ack_signal",
            "id": str(signal_id),
        }, timeout=10)
    except Exception as e:
        log.warning(f"Signal ack error: {e}")


# === EXCHANGE ===

def get_tradeable_coins(client):
    """Get all USDT futures pairs from Bitunix."""
    result = client.get_trading_pairs()
    if result.get("code") != 0:
        log.error(
            f"Failed to get trading pairs: {result}")
        return []
    coins = []
    for p in result["data"]:
        if (p.get("symbolStatus") == "OPEN"
                and p["symbol"].endswith("USDT")):
            coins.append({
                "symbol": p["symbol"],
                "min_qty": float(p["minTradeVolume"]),
                "max_leverage": int(p["maxLeverage"]),
                "precision": int(p["basePrecision"]),
            })
    return coins


def get_account_balance(client):
    """Get available USDT balance."""
    try:
        result = client.get_balance()
        if result.get("code") == 0:
            data = result.get("data", {})
            return float(data.get("available", 0))
    except Exception as e:
        log.warning(f"Balance check failed: {e}")
    return 0.0


# === POSITION MANAGEMENT ===

def check_position_exits(state, client, live=False):
    """Check Bitunix for closed positions, log trades."""
    if not state["open_positions"]:
        return

    if live:
        try:
            result = client.get_positions()
            if result.get("code") != 0:
                return
            live_symbols = set()
            for p in result.get("data", []):
                live_symbols.add(p["symbol"])
        except Exception as e:
            log.warning(f"Position check failed: {e}")
            return

        closed = []
        still_open = []
        for pos in state["open_positions"]:
            if pos["symbol"] not in live_symbols:
                closed.append(pos)
            else:
                still_open.append(pos)

        for pos in closed:
            entry = pos["entry_price"]
            pnl = 0.0
            outcome = "unknown"
            try:
                hist = client.get_history_positions(
                    symbol=pos["symbol"])
                if hist.get("code") == 0:
                    for h in hist.get("data", []):
                        op = float(
                            h.get("openPrice", 0))
                        if abs(op - entry) < entry * 0.001:
                            pnl = float(
                                h.get("realizedPnl", 0))
                            outcome = (
                                "TP" if pnl > 0 else "SL")
                            break
            except Exception:
                pass

            pnl_pct = (pnl / entry * 100) if entry else 0
            state["total_trades"] += 1
            state["total_pnl"] += pnl_pct
            if pnl_pct > 0:
                state["total_wins"] += 1

            log.info(
                f"  CLOSED {pos['symbol']} "
                f"entry={entry} pnl={pnl_pct:+.2f}% "
                f"({outcome})")

            log_trade_to_cloud({
                "timestamp": datetime.now(
                    timezone.utc).isoformat(),
                "symbol": pos["symbol"],
                "entry_price": entry,
                "exit_price": 0,
                "pnl_pct": round(pnl_pct, 4),
                "outcome": outcome,
                "breathing_score": state.get(
                    "breathing_score", 0),
                "hold_bars": 0,
                "signal_time": pos.get(
                    "entry_time", ""),
            })

        state["open_positions"] = still_open

    else:
        # Dry run: check max hold time
        now = datetime.now(timezone.utc)
        still_open = []
        for pos in state["open_positions"]:
            try:
                entry_time = datetime.fromisoformat(
                    pos["entry_time"])
                elapsed = (
                    now - entry_time).total_seconds()
                if elapsed >= MAX_HOLD_BARS * 300:
                    log.info(
                        f"  [DRY] MAX HOLD "
                        f"{pos['symbol']}")
                    state["total_trades"] += 1
                    continue
            except Exception:
                pass
            still_open.append(pos)
        state["open_positions"] = still_open


# === TRADE EXECUTION ===

def execute_signal(state, client, signal,
                   trade_symbols, coin_map,
                   live=False, per_coin_usd=None,
                   max_coins=None):
    """Open shorts on approved coins."""
    now = datetime.now(timezone.utc)
    score = signal.get("score", 0)

    log.info(
        f"*** SHORT SIGNAL from TradingView *** "
        f"score={score}")

    # Get current prices
    tickers = client.get_tickers()
    price_map = {}
    if tickers.get("code") == 0:
        for t in tickers["data"]:
            price_map[t["symbol"]] = float(
                t["lastPrice"])

    coins_entered = 0
    coins_to_trade = trade_symbols
    if max_coins:
        coins_to_trade = trade_symbols[:max_coins]

    for sym in coins_to_trade:
        if sym not in price_map:
            continue
        ci = coin_map.get(sym)
        if not ci:
            continue

        price = price_map[sym]
        if price == 0:
            continue

        # SL/TP prices (short = SL above, TP below)
        sl_price = round(
            price * (1 + SL_PCT / 100), 8)
        tp_price = round(
            price * (1 - TP_PCT / 100), 8)

        # Position sizing
        if per_coin_usd:
            notional = per_coin_usd * LEVERAGE
            qty = notional / price
            if ci["precision"] > 0:
                qty = round(qty, ci["precision"])
            else:
                qty = int(qty)
            qty = max(qty, ci["min_qty"])
        else:
            # Equal allocation fallback
            qty = ci["min_qty"]

        if live:
            lev = min(LEVERAGE, ci["max_leverage"])
            client.change_leverage(sym, lev)

            result = client.open_short(
                symbol=sym,
                qty=qty,
                tp_price=tp_price,
                sl_price=sl_price,
            )
            if result.get("code") == 0:
                log.info(
                    f"  SHORT {sym} @ {price} "
                    f"qty={qty} "
                    f"SL={sl_price} TP={tp_price}")
                coins_entered += 1
            else:
                log.warning(
                    f"  FAILED {sym}: {result}")
                continue
        else:
            log.info(
                f"  [DRY] SHORT {sym} @ {price} "
                f"qty={qty} "
                f"SL={sl_price} TP={tp_price}")
            coins_entered += 1

        state["open_positions"].append({
            "symbol": sym,
            "entry_price": price,
            "entry_time": now.isoformat(),
            "qty": qty,
            "sl_price": sl_price,
            "tp_price": tp_price,
        })

    state["signal_history"].append({
        "time": now.isoformat(),
        "breath_score": round(score, 3),
        "coins_entered": coins_entered,
    })

    # Keep only last 50 signals
    if len(state["signal_history"]) > 50:
        state["signal_history"] = (
            state["signal_history"][-50:])

    log.info(f"Entered {coins_entered} shorts")
    return coins_entered


# === MAIN BOT LOOP ===

def run_bot(live=False, override_balance=None,
            per_coin_usd=None, max_coins=None):
    """Main bot loop — polls for TV signals."""
    log.info("=" * 60)
    log.info(
        f"  RhythmFilter Bot "
        f"{'LIVE' if live else 'DRY RUN'}")
    log.info(
        f"  SL={SL_PCT}% / TP={TP_PCT}% "
        f"/ {LEVERAGE}x leverage")
    log.info(
        f"  Signal source: TradingView webhook")
    if per_coin_usd:
        log.info(f"  Fixed ${per_coin_usd} per coin")
    if max_coins:
        log.info(
            f"  Max {max_coins} coins per signal")
    if APPS_SCRIPT_URL:
        log.info("  Cloud sync: ENABLED")
    else:
        log.info(
            "  Cloud sync: DISABLED (no URL)")
    log.info("=" * 60)

    client = BitunixClient()
    blacklist = load_blacklist()
    approved_list = load_approved_coins()
    state = load_state()

    log.info(f"Approved coins: {len(approved_list)}")
    log.info(f"Blacklist: {len(blacklist)} coins")
    log.info(
        f"Open positions: "
        f"{len(state['open_positions'])}")

    # Load tradeable coins once (refresh every hour)
    coin_map = {}
    trade_symbols = []
    last_pair_refresh = 0

    last_position_check = 0
    poll_count = 0

    while True:
        try:
            now = datetime.now(timezone.utc)
            now_ts = time.time()

            # Refresh trading pairs every hour
            if now_ts - last_pair_refresh > 3600:
                log.info("Refreshing trading pairs...")
                bitunix_coins = get_tradeable_coins(
                    client)
                if bitunix_coins:
                    coin_map = {
                        c["symbol"]: c
                        for c in bitunix_coins}
                    bset = set(
                        s.lower()
                        for s in coin_map.keys())
                    trade_symbols = [
                        s.upper()
                        for s in approved_list
                        if s.lower() in bset
                        and s.lower() not in blacklist
                    ]
                    log.info(
                        f"  {len(trade_symbols)} "
                        f"tradeable coins on Bitunix")
                    last_pair_refresh = now_ts

            # Poll for signal from TradingView
            signal = poll_for_signal()

            if signal:
                log.info(
                    f"\n=== SIGNAL RECEIVED "
                    f"{now:%H:%M:%S UTC} ===")

                # Update dashboard score
                state["breathing_score"] = signal.get(
                    "score", 0)
                state["signal_state"] = "fired"

                # Execute trades
                execute_signal(
                    state, client, signal,
                    trade_symbols, coin_map,
                    live=live,
                    per_coin_usd=per_coin_usd,
                    max_coins=max_coins,
                )

                # Acknowledge signal
                ack_signal(signal["id"])

                # Save immediately
                state["last_tick"] = now.isoformat()
                save_state(state)
            else:
                # Periodic position check
                if now_ts - last_position_check > POSITION_CHECK:
                    check_position_exits(
                        state, client, live)
                    state["last_tick"] = now.isoformat()
                    save_state(state)
                    last_position_check = now_ts

                # Heartbeat log every 10 polls (~5 min)
                poll_count += 1
                if poll_count % 10 == 0:
                    n_pos = len(state["open_positions"])
                    log.info(
                        f"Listening... "
                        f"{n_pos} open positions")

            # Sleep before next poll
            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            log.info("Bot stopped by user")
            save_state(state)
            break
        except Exception as e:
            log.error(f"Error: {e}", exc_info=True)
            time.sleep(30)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="RhythmFilter Trading Bot")
    parser.add_argument(
        "--live", action="store_true",
        help="Enable live trading (default: dry run)")
    parser.add_argument(
        "--balance", type=float, default=None,
        help="Override starting balance")
    parser.add_argument(
        "--leverage", type=int, default=None,
        help="Override leverage (default: 1)")
    parser.add_argument(
        "--per-coin", type=float, default=None,
        help="Fixed $ per coin (overrides equal alloc)")
    parser.add_argument(
        "--max-coins", type=int, default=None,
        help="Max coins to trade per signal")
    args = parser.parse_args()

    if args.leverage is not None:
        LEVERAGE = args.leverage
    run_bot(
        live=args.live,
        override_balance=args.balance,
        per_coin_usd=args.per_coin,
        max_coins=args.max_coins,
    )
