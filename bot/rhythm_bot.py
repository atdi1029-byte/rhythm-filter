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
SIGNAL_COOLDOWN = 300  # 5 min cooldown (just enough to skip duplicate burst)
MIN_EXHALE_SCORE = -3.0  # only trade when score <= this (EXHALE)
MAX_SHORTS_PER_COIN = 3  # max open shorts per crypto

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

def _post_to_cloud(action, data):
    """POST JSON to Apps Script. Handles GAS redirects."""
    if not APPS_SCRIPT_URL:
        return None
    payload = json.dumps(
        {"action": action, "data": data},
        separators=(",", ":"))
    headers = {"Content-Type": "application/json"}
    r = requests.post(
        APPS_SCRIPT_URL, data=payload,
        headers=headers, timeout=15,
        allow_redirects=False)
    # GAS redirects POST via 302
    if r.status_code in (301, 302):
        loc = r.headers.get("Location")
        if loc:
            r = requests.post(
                loc, data=payload,
                headers=headers, timeout=15)
    return r


def sync_to_cloud(state):
    """Push state to Apps Script via POST."""
    if not APPS_SCRIPT_URL:
        return
    try:
        # Strip signal_history — dashboard doesn't use it
        cloud = {k: v for k, v in state.items()
                 if k != "signal_history"}
        r = _post_to_cloud("save_state", cloud)
        if r and r.status_code == 200:
            log.debug("Synced to cloud")
        elif r:
            log.warning(
                f"Cloud sync failed: {r.status_code}")
    except Exception as e:
        log.warning(f"Cloud sync error: {e}")


def log_trade_to_cloud(trade_data):
    """Log a closed trade to Apps Script via POST."""
    if not APPS_SCRIPT_URL:
        return
    try:
        r = _post_to_cloud("log_trade", trade_data)
        if r and r.status_code == 200:
            log.debug("Trade logged to cloud")
        elif r:
            log.warning(
                f"Trade log failed: {r.status_code}")
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


def poll_for_commands():
    """Check Apps Script for pending commands."""
    if not APPS_SCRIPT_URL:
        return []
    try:
        r = requests.get(APPS_SCRIPT_URL, params={
            "action": "get_commands",
        }, timeout=10)
        data = r.json()
        if data.get("status") == "success":
            return data.get("commands", [])
    except Exception as e:
        log.warning(f"Command poll error: {e}")
    return []


def ack_command(row, result="done"):
    """Mark a command as completed."""
    if not APPS_SCRIPT_URL:
        return
    try:
        requests.get(APPS_SCRIPT_URL, params={
            "action": "ack_command",
            "row": str(row),
            "result": result,
        }, timeout=10)
    except Exception as e:
        log.warning(f"Command ack error: {e}")


def close_all_green(client, state):
    """Close all open positions that are in profit."""
    if not state["open_positions"]:
        log.info("No open positions to close")
        return "No open positions"

    # Fetch live positions with unrealized PnL
    try:
        result = client.get_positions()
        if result.get("code") != 0:
            log.error(f"Cannot get positions: {result}")
            return "Failed to get positions"
        live_positions = result.get("data", [])
    except Exception as e:
        log.error(f"Position fetch error: {e}")
        return f"Error: {e}"

    # Build map of positionId → live data
    live_map = {}
    for lp in live_positions:
        pid = lp.get("positionId")
        if pid:
            live_map[pid] = lp

    closed_count = 0
    closed_syms = []
    skipped = []

    for pos in list(state["open_positions"]):
        pid = pos.get("position_id")
        if not pid or pid not in live_map:
            skipped.append(pos["symbol"] + "(no match)")
            continue

        lp = live_map[pid]
        pnl = float(lp.get("unrealizedPNL", 0))

        if pnl <= 0:
            skipped.append(
                f"{pos['symbol']}(${pnl:.4f})")
            continue

        # Close this position
        qty = pos.get("qty", 0)
        if qty <= 0:
            continue

        log.info(
            f"  CLOSE GREEN: {pos['symbol']} "
            f"pnl=${pnl:.4f} qty={qty}")

        try:
            res = client.close_short(
                pos["symbol"], qty,
                position_id=pid)
            if res.get("code") == 0:
                closed_count += 1
                closed_syms.append(pos["symbol"])
                log.info(f"    Closed OK")
            else:
                log.warning(f"    Close failed: {res}")
                skipped.append(
                    f"{pos['symbol']}(api error)")
        except Exception as e:
            log.warning(
                f"    Close error {pos['symbol']}: {e}")
            skipped.append(
                f"{pos['symbol']}(exception)")

    # Process closed positions on next tick
    msg = f"Closed {closed_count}: {', '.join(closed_syms)}"
    if skipped:
        msg += f" | Skipped: {', '.join(skipped)}"
    log.info(f"Close All Green result: {msg}")
    return msg


def close_position_by_id(client, state, position_id):
    """Close a specific position by its ID."""
    pos = None
    for p in state["open_positions"]:
        if p.get("position_id") == position_id:
            pos = p
            break

    if not pos:
        return f"Position {position_id} not found in state"

    qty = pos.get("qty", 0)
    if qty <= 0:
        return f"Invalid qty for {pos['symbol']}"

    log.info(
        f"  CLOSE: {pos['symbol']} "
        f"qty={qty} pid={position_id}")

    try:
        res = client.close_short(
            pos["symbol"], qty,
            position_id=position_id)
        if res.get("code") == 0:
            log.info(f"    Closed OK")
            return f"Closed {pos['symbol']}"
        else:
            log.warning(f"    Close failed: {res}")
            return f"Failed: {res.get('msg', 'unknown')}"
    except Exception as e:
        log.warning(f"    Close error: {e}")
        return f"Error: {e}"


def drain_all_signals():
    """Ack ALL pending signals so none pile up."""
    drained = 0
    for _ in range(50):  # safety cap
        sig = poll_for_signal()
        if not sig:
            break
        ack_signal(sig["id"])
        drained += 1
    if drained > 0:
        log.info(f"  Drained {drained} queued signals")


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

def assign_position_ids(state, live_positions):
    """Match state entries to Bitunix positionIds."""
    used_ids = set()
    for pos in state["open_positions"]:
        pid = pos.get("position_id")
        if pid:
            used_ids.add(pid)

    assigned = 0
    for pos in state["open_positions"]:
        if pos.get("position_id"):
            continue
        for lp in live_positions:
            pid = lp.get("positionId")
            if pid in used_ids:
                continue
            if lp["symbol"] != pos["symbol"]:
                continue
            lp_price = float(
                lp.get("avgOpenPrice", 0))
            if (lp_price > 0
                    and pos["entry_price"] > 0
                    and abs(lp_price - pos["entry_price"])
                    < pos["entry_price"] * 0.02):
                pos["position_id"] = pid
                used_ids.add(pid)
                assigned += 1
                log.info(
                    f"  Assigned positionId {pid} "
                    f"to {pos['symbol']} "
                    f"entry={pos['entry_price']}")
                break
    return assigned


def process_closed_position(state, client, pos):
    """Look up PnL for a closed position and log it."""
    entry = pos["entry_price"]
    qty = pos.get("qty", 0)
    pid = pos.get("position_id")
    pnl = 0.0
    exit_price = 0.0
    outcome = "unknown"

    try:
        hist = client.get_history_positions(
            symbol=pos["symbol"])
        if hist.get("code") == 0:
            raw = hist.get("data", {})
            hdata = raw.get("positionList", [])

            # Strategy 1: match by positionId (best)
            if pid:
                for h in hdata:
                    if h.get("positionId") == pid:
                        pnl = float(
                            h.get("realizedPNL", 0))
                        exit_price = float(
                            h.get("closePrice", 0))
                        outcome = (
                            "TP" if pnl > 0 else "SL")
                        log.info(
                            f"  Matched {pos['symbol']}"
                            f" by positionId={pid}"
                            f" pnl=${pnl:.4f}"
                            f" exit={exit_price}")
                        break

            # Strategy 2: entry price + time window
            if outcome == "unknown":
                entry_ts = pos.get("entry_time", "")
                for h in hdata:
                    hp = float(
                        h.get("entryPrice", 0))
                    if not (hp > 0 and entry > 0
                            and abs(hp - entry)
                            < entry * 0.02):
                        continue
                    # Check closed after entry time
                    h_mtime = h.get("mtime", 0)
                    if entry_ts and h_mtime:
                        try:
                            et = datetime.fromisoformat(
                                entry_ts)
                            et_ms = int(
                                et.timestamp() * 1000)
                            if int(h_mtime) < et_ms:
                                continue
                        except Exception:
                            pass
                    pnl = float(
                        h.get("realizedPNL", 0))
                    exit_price = float(
                        h.get("closePrice", 0))
                    outcome = (
                        "TP" if pnl > 0 else "SL")
                    log.info(
                        f"  Matched {pos['symbol']}"
                        f" by price+time"
                        f" pnl=${pnl:.4f}"
                        f" exit={exit_price}")
                    break

            if outcome == "unknown":
                log.warning(
                    f"  No PnL match for "
                    f"{pos['symbol']} "
                    f"entry={entry} pid={pid}")
                outcome = "manual"
    except Exception as e:
        log.warning(
            f"  History error "
            f"{pos['symbol']}: {e}")

    # PnL as % of position value
    position_value = entry * qty if qty else entry
    pnl_pct = (
        pnl / position_value * 100
    ) if position_value else 0

    # Only count real trades (TP/SL) in stats,
    # skip ghost trades (manual/unknown at 0%)
    if outcome not in ("manual", "unknown"):
        state["total_trades"] += 1
        state["total_pnl"] += pnl_pct
        if pnl_pct > 0:
            state["total_wins"] += 1

    log.info(
        f"  CLOSED {pos['symbol']} "
        f"entry={entry} pnl={pnl_pct:+.2f}% "
        f"({outcome}) pid={pid}")

    # Only log real trades to cloud
    if outcome not in ("manual", "unknown"):
        log_trade_to_cloud({
            "timestamp": datetime.now(
                timezone.utc).isoformat(),
            "symbol": pos["symbol"],
            "entry_price": entry,
            "exit_price": exit_price,
            "pnl_pct": round(pnl_pct, 4),
            "outcome": outcome,
            "breathing_score": state.get(
                "breathing_score", 0),
            "hold_bars": 0,
            "signal_time": pos.get(
                "entry_time", ""),
            "position_id": pid,
        })


def check_position_exits(state, client, live=False):
    """Check Bitunix for closed positions, log trades."""
    if not state["open_positions"]:
        return

    if live:
        try:
            result = client.get_positions()
            if result.get("code") != 0:
                return
            live_positions = result.get("data", [])
        except Exception as e:
            log.warning(f"Position check failed: {e}")
            return

        # Try to assign positionIds to entries that
        # don't have them yet
        assign_position_ids(state, live_positions)

        # Update unrealized PnL from Bitunix
        live_pnl_map = {}
        for lp in live_positions:
            pid = lp.get("positionId")
            if pid:
                live_pnl_map[pid] = float(
                    lp.get("unrealizedPNL", 0))
        for pos in state["open_positions"]:
            pid = pos.get("position_id")
            if pid and pid in live_pnl_map:
                pos["unrealized_pnl"] = (
                    live_pnl_map[pid])

        # Build set of live positionIds
        live_ids = set()
        for lp in live_positions:
            live_ids.add(lp.get("positionId"))

        # Max-hold force-close
        now = datetime.now(timezone.utc)
        max_hold_s = MAX_HOLD_BARS * 300
        for pos in state["open_positions"]:
            pid = pos.get("position_id")
            if not pid or pid not in live_ids:
                continue
            try:
                et = datetime.fromisoformat(
                    pos["entry_time"])
                elapsed = (now - et).total_seconds()
                if elapsed >= max_hold_s:
                    log.info(
                        f"  MAX HOLD: closing "
                        f"{pos['symbol']} "
                        f"(age={elapsed/3600:.1f}h)")
                    qty = pos.get("qty", 0)
                    if qty > 0:
                        res = client.close_short(
                            pos["symbol"], qty,
                            position_id=pid)
                        if res.get("code") == 0:
                            log.info("    Closed OK")
                        else:
                            log.warning(
                                f"    Close failed: "
                                f"{res}")
            except Exception as e:
                log.warning(
                    f"  Max hold error: {e}")

        # Categorize: closed vs still open
        closed = []
        still_open = []
        no_id = []

        for pos in state["open_positions"]:
            pid = pos.get("position_id")
            if pid and pid not in live_ids:
                closed.append(pos)
            elif pid and pid in live_ids:
                still_open.append(pos)
            else:
                no_id.append(pos)

        # Fallback for entries without positionId:
        # count per-symbol positions on Bitunix vs state
        live_count = {}
        for lp in live_positions:
            sym = lp["symbol"]
            live_count[sym] = live_count.get(sym, 0) + 1

        id_count = {}
        for p in still_open:
            sym = p["symbol"]
            id_count[sym] = id_count.get(sym, 0) + 1

        by_sym = {}
        for p in no_id:
            by_sym.setdefault(p["symbol"], []).append(p)

        for sym, entries in by_sym.items():
            bitunix_n = live_count.get(sym, 0)
            matched_n = id_count.get(sym, 0)
            unmatched = bitunix_n - matched_n
            # Sort oldest first — oldest close first
            entries.sort(
                key=lambda x: x.get("entry_time", ""))
            keep = max(0, unmatched)
            still_open.extend(entries[:keep])
            closed.extend(entries[keep:])

        # Process all closed positions
        for pos in closed:
            process_closed_position(
                state, client, pos)

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

    # Reject signals that aren't in EXHALE
    if score > MIN_EXHALE_SCORE:
        log.warning(
            f"  REJECTED: score {score} > "
            f"{MIN_EXHALE_SCORE} (not in EXHALE)")
        return 0

    # Get current prices
    tickers = client.get_tickers()
    price_map = {}
    if tickers.get("code") == 0:
        for t in tickers["data"]:
            price_map[t["symbol"]] = float(
                t["lastPrice"])

    coins_entered = 0

    for sym in trade_symbols:
        if max_coins and coins_entered >= max_coins:
            break
        if sym not in price_map:
            continue
        ci = coin_map.get(sym)
        if not ci:
            continue

        price = price_map[sym]
        if price == 0:
            continue

        # Check per-coin limit
        open_count = sum(
            1 for p in state["open_positions"]
            if p["symbol"] == sym)
        if open_count >= MAX_SHORTS_PER_COIN:
            log.info(
                f"  SKIP {sym}: already {open_count} "
                f"open (max {MAX_SHORTS_PER_COIN})")
            continue

        # SL/TP prices (short = SL above, TP below)
        sl_price = round(
            price * (1 + SL_PCT / 100), 8)
        tp_price = round(
            price * (1 - TP_PCT / 100), 8)

        # Position sizing
        if per_coin_usd:
            # Skip coins where exchange minimum > budget
            min_cost = ci["min_qty"] * price
            if min_cost > per_coin_usd:
                log.debug(
                    f"  SKIP {sym}: min ${min_cost:.2f} "
                    f"> budget ${per_coin_usd}")
                continue

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


# === RECONCILE ===

def reconcile_state(client):
    """One-time sync: align state with Bitunix."""
    state = load_state()
    n = len(state["open_positions"])
    log.info("=== RECONCILE MODE ===")
    log.info(f"State has {n} open positions")

    try:
        result = client.get_positions()
        if result.get("code") != 0:
            log.error(f"Cannot get positions: {result}")
            return
        live_positions = result.get("data", [])
    except Exception as e:
        log.error(f"API error: {e}")
        return

    log.info(
        f"Bitunix has {len(live_positions)} "
        f"live positions")

    # Show what's on Bitunix
    live_by_sym = {}
    for lp in live_positions:
        sym = lp["symbol"]
        live_by_sym.setdefault(sym, []).append(lp)
    for sym, plist in sorted(live_by_sym.items()):
        for p in plist:
            log.info(
                f"  Bitunix: {sym} "
                f"id={p.get('positionId')} "
                f"price={p.get('avgOpenPrice')} "
                f"qty={p.get('qty')}")

    # Show what's in state
    state_by_sym = {}
    for pos in state["open_positions"]:
        sym = pos["symbol"]
        state_by_sym.setdefault(sym, []).append(pos)
    for sym, entries in sorted(state_by_sym.items()):
        log.info(f"  State: {sym} x{len(entries)}")

    # Assign positionIds where possible
    assigned = assign_position_ids(
        state, live_positions)
    log.info(f"Assigned {assigned} positionIds")

    # Build set of live IDs
    live_ids = set()
    for lp in live_positions:
        live_ids.add(lp.get("positionId"))

    # Categorize
    kept = []
    removed = []

    # Entries with positionId: definitive check
    no_id = []
    for pos in state["open_positions"]:
        pid = pos.get("position_id")
        if pid and pid in live_ids:
            kept.append(pos)
        elif pid and pid not in live_ids:
            removed.append(pos)
        else:
            no_id.append(pos)

    # Entries without ID: check if symbol has
    # unmatched live positions
    id_count = {}
    for p in kept:
        sym = p["symbol"]
        id_count[sym] = id_count.get(sym, 0) + 1

    live_count = {}
    for lp in live_positions:
        sym = lp["symbol"]
        live_count[sym] = live_count.get(sym, 0) + 1

    by_sym = {}
    for p in no_id:
        by_sym.setdefault(p["symbol"], []).append(p)

    for sym, entries in by_sym.items():
        bitunix_n = live_count.get(sym, 0)
        matched_n = id_count.get(sym, 0)
        unmatched = bitunix_n - matched_n
        entries.sort(
            key=lambda x: x.get("entry_time", ""))
        keep_n = max(0, unmatched)
        kept.extend(entries[:keep_n])
        removed.extend(entries[keep_n:])

    log.info(
        f"\nKept: {len(kept)}, "
        f"Removed: {len(removed)}")
    for pos in removed:
        log.info(
            f"  REMOVED: {pos['symbol']} "
            f"entry={pos['entry_price']} "
            f"time={pos.get('entry_time', '?')}")

    state["open_positions"] = kept
    save_state(state)
    log.info("State reconciled and saved.")


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
    last_command_check = 0
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

            # Check for dashboard commands
            if now_ts - last_command_check > 30:
                commands = poll_for_commands()
                for cmd in commands:
                    cmd_name = cmd.get("command", "")
                    cmd_row = cmd.get("row")
                    log.info(
                        f"=== COMMAND: {cmd_name} ===")

                    if cmd_name == "close_all_green":
                        result = close_all_green(
                            client, state)
                        ack_command(cmd_row, result)
                    elif cmd_name == "close_position":
                        pid = cmd.get("params", "")
                        result = close_position_by_id(
                            client, state, pid)
                        ack_command(cmd_row, result)
                    else:
                        ack_command(
                            cmd_row,
                            f"Unknown: {cmd_name}")

                    # Immediate position check after close
                    time.sleep(2)
                    check_position_exits(
                        state, client, live)
                    state["last_tick"] = (
                        now.isoformat())
                    save_state(state)

                last_command_check = now_ts

            # Poll for signal from TradingView
            signal = poll_for_signal()

            if signal:
                # Check cooldown — skip if too soon
                last_sig = state.get("last_signal_time")
                if last_sig:
                    try:
                        elapsed = (
                            now - datetime.fromisoformat(
                                last_sig)
                        ).total_seconds()
                        if elapsed < SIGNAL_COOLDOWN:
                            remaining = int(
                                SIGNAL_COOLDOWN - elapsed)
                            log.info(
                                f"Signal ignored "
                                f"(cooldown {remaining}s "
                                f"remaining)")
                            drain_all_signals()
                            time.sleep(POLL_INTERVAL)
                            continue
                    except Exception:
                        pass

                log.info(
                    f"\n=== SIGNAL RECEIVED "
                    f"{now:%H:%M:%S UTC} ===")

                # Update dashboard score
                state["breathing_score"] = signal.get(
                    "score", 0)
                state["signal_state"] = "fired"

                # Execute trades
                coins = execute_signal(
                    state, client, signal,
                    trade_symbols, coin_map,
                    live=live,
                    per_coin_usd=per_coin_usd,
                    max_coins=max_coins,
                )

                # Capture positionIds from Bitunix
                if coins > 0 and live:
                    time.sleep(3)  # wait for fills
                    try:
                        pres = client.get_positions()
                        if pres.get("code") == 0:
                            n = assign_position_ids(
                                state,
                                pres.get("data", []))
                            if n:
                                log.info(
                                    f"  Assigned {n} "
                                    f"positionIds")
                    except Exception as e:
                        log.warning(
                            f"  positionId fetch: {e}")

                # Record cooldown timestamp
                if coins > 0:
                    state["last_signal_time"] = (
                        now.isoformat())

                # Ack this signal + drain ALL queued ones
                ack_signal(signal["id"])
                drain_all_signals()

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
    parser.add_argument(
        "--reconcile", action="store_true",
        help="Reconcile state with Bitunix and exit")
    args = parser.parse_args()

    if args.leverage is not None:
        LEVERAGE = args.leverage

    if args.reconcile:
        client = BitunixClient()
        reconcile_state(client)
        sys.exit(0)

    run_bot(
        live=args.live,
        override_balance=args.balance,
        per_coin_usd=args.per_coin,
        max_coins=args.max_coins,
    )
