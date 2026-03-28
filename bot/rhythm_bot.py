"""RhythmFilter Trading Bot — Bitunix Futures.

Computes breathing score across N coins on 5-min candles.
Opens shorts on approved coins when signal fires.
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

# Add parent dir to path for config
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from bot.bitunix_api import BitunixClient

# === SETTINGS ===
SL_PCT = 7.0          # stop loss %
TP_PCT = 12.0         # take profit %
LEVERAGE = 2           # start conservative
MAX_HOLD_BARS = 2016   # 7 days on 5min
RSI_LEN = 14
BUY_ZONE = 40.0
SELL_ZONE = 60.0
EMA_SMOOTH = 9
SHORT_THRESHOLD = -5.0
SHORT_COOLDOWN = 12    # bars between signals
LOOP_INTERVAL = 300    # 5 minutes in seconds
MIN_POSITION_USD = 5.0 # minimum order size in USDT

# Binance for market data (free, no auth)
BINANCE_KLINES = "https://api.binance.com/api/v3/klines"

# Files
STATE_FILE = os.path.join(os.path.dirname(__file__), "bot_state.json")
LOG_FILE = os.path.join(os.path.dirname(__file__), "bot.log")
BLACKLIST_FILE = os.path.join(config.DATA_DIR, "blacklist.json")
APPROVED_FILE = os.path.join(
    config.DATA_DIR, "approved_coins_7_12.json")

# Cloud sync
APPS_SCRIPT_URL = os.environ.get("RHYTHM_APPS_SCRIPT_URL", "")

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
    """Load approved coin list (whitelist). Only these get traded."""
    if os.path.exists(APPROVED_FILE):
        with open(APPROVED_FILE) as f:
            return set(json.load(f))
    return set()


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "open_positions": [],
        "signal_history": [],
        "last_signal_bar": -999,
        "was_green": False,
        "short_armed": False,
        "trough_val": 0.0,
        "total_pnl": 0.0,
        "total_trades": 0,
        "total_wins": 0,
        "bar_count": 0,
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
            log.warning(f"Cloud sync failed: {r.status_code}")
    except Exception as e:
        log.warning(f"Cloud sync error: {e}")


def log_trade_to_cloud(trade_data):
    """Log a closed trade to Apps Script."""
    if not APPS_SCRIPT_URL:
        return
    try:
        data = json.dumps(trade_data, separators=(",", ":"))
        requests.get(APPS_SCRIPT_URL, params={
            "action": "log_trade",
            "data": data,
        }, timeout=10)
    except Exception as e:
        log.warning(f"Trade log error: {e}")


# === MARKET DATA ===

def get_binance_closes(symbol, limit=50):
    """Get last N 5-min closes from Binance."""
    try:
        r = requests.get(BINANCE_KLINES, params={
            "symbol": symbol.upper(),
            "interval": "5m",
            "limit": limit,
        }, timeout=10)
        data = r.json()
        if isinstance(data, list):
            return [float(k[4]) for k in data]
    except Exception as e:
        log.warning(f"Failed to get {symbol} candles: {e}")
    return []


def compute_rsi_from_closes(closes, length=14):
    """Compute RSI from close prices. Returns latest RSI."""
    if len(closes) < length + 1:
        return None
    gains, losses = [], []
    for i in range(1, length + 1):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains) / length
    al = sum(losses) / length
    for i in range(length + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (length - 1) + max(d, 0)) / length
        al = (al * (length - 1) + max(-d, 0)) / length
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


# === EXCHANGE ===

def get_tradeable_coins(client):
    """Get all USDT futures pairs from Bitunix."""
    result = client.get_trading_pairs()
    if result.get("code") != 0:
        log.error(f"Failed to get trading pairs: {result}")
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


def compute_breathing_score(coins, n_coins):
    """Compute current breathing score from live data."""
    score = 0
    valid = 0
    for coin_symbol in coins:
        closes = get_binance_closes(coin_symbol, limit=50)
        if not closes:
            continue
        rsi = compute_rsi_from_closes(closes, RSI_LEN)
        if rsi is None:
            continue
        valid += 1
        if rsi < BUY_ZONE:
            score += 1
        elif rsi > SELL_ZONE:
            score -= 1
    if valid == 0:
        return 0.0
    return score * 20.0 / n_coins


# === POSITION MANAGEMENT ===

def check_position_exits(state, client, live=False):
    """Check Bitunix for closed positions, log trades."""
    if not state["open_positions"]:
        return

    if live:
        # Query Bitunix for actual open positions
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
            sym = pos["symbol"]
            if sym not in live_symbols:
                # Position was closed (TP or SL hit)
                closed.append(pos)
            else:
                still_open.append(pos)

        for pos in closed:
            # Determine outcome from entry vs current
            entry = pos["entry_price"]
            sl = pos["sl_price"]
            tp = pos["tp_price"]
            # We don't know exact exit price without history
            # Check history API
            pnl = 0.0
            outcome = "unknown"
            try:
                hist = client.get_history_positions(
                    symbol=pos["symbol"])
                if hist.get("code") == 0:
                    for h in hist.get("data", []):
                        if abs(float(h.get("openPrice", 0))
                               - entry) < entry * 0.001:
                            pnl = float(
                                h.get("realizedPnl", 0))
                            outcome = ("TP" if pnl > 0
                                       else "SL")
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
                "breathing_score": 0,
                "hold_bars": 0,
                "signal_time": pos.get("entry_time", ""),
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
                elapsed = (now - entry_time).total_seconds()
                max_hold_secs = MAX_HOLD_BARS * 300
                if elapsed >= max_hold_secs:
                    log.info(
                        f"  [DRY] MAX HOLD {pos['symbol']}")
                    state["total_trades"] += 1
                    continue
            except Exception:
                pass
            still_open.append(pos)
        state["open_positions"] = still_open


def fetch_live_kelly():
    """Fetch live Kelly % per coin from Apps Script.

    Returns dict of {symbol: kelly_pct} where kelly is 0-1.
    Falls back to backtest Kelly from coin_results file.
    """
    kelly_map = {}

    # Try cloud first
    if APPS_SCRIPT_URL:
        try:
            r = requests.get(APPS_SCRIPT_URL, params={
                "action": "get_coins",
            }, timeout=10)
            data = r.json()
            if isinstance(data, dict) and data.get("data"):
                for c in data["data"]:
                    coin = c.get("Coin", "").lower()
                    # Use live Kelly if enough trades,
                    # otherwise fall back to backtest
                    live_k = c.get("Live_Kelly", 0)
                    bt_k = c.get("BT_Kelly", 0)
                    live_t = c.get("Live_Trades", 0)
                    kelly_map[coin] = (
                        live_k if live_t >= 10 else bt_k)
                if kelly_map:
                    log.info(
                        f"  Loaded Kelly for "
                        f"{len(kelly_map)} coins")
                    return kelly_map
        except Exception as e:
            log.warning(f"Kelly fetch failed: {e}")

    # Fallback: load from local backtest results
    results_file = os.path.join(
        config.DATA_DIR, "coin_results_7_12.json")
    if os.path.exists(results_file):
        with open(results_file) as f:
            results = json.load(f)
        for r in results:
            kelly_map[r["coin"]] = r.get("kelly", 0)

    return kelly_map


# Kelly fraction cap (never bet more than half-Kelly
# for safety — "fractional Kelly")
KELLY_FRACTION = 0.5
KELLY_MIN = 0.005   # minimum 0.5% of account per coin
KELLY_MAX = 0.05    # maximum 5% of account per coin


def calculate_position_size(balance, n_trade_coins, price,
                            min_qty, precision,
                            kelly_pct=None):
    """Kelly-weighted position sizing.

    If kelly_pct is provided, size is proportional to
    Kelly %. Otherwise falls back to equal allocation.

    Args:
        kelly_pct: Kelly fraction 0-1 (e.g. 0.15 = 15%)
    """
    if n_trade_coins == 0 or price == 0:
        return min_qty

    if kelly_pct is not None and kelly_pct > 0:
        # Fractional Kelly: bet kelly_pct * fraction
        # of bankroll on this coin
        frac = kelly_pct * KELLY_FRACTION
        frac = max(KELLY_MIN, min(KELLY_MAX, frac))
        alloc = balance * LEVERAGE * frac
    else:
        # Equal allocation fallback
        alloc = balance * LEVERAGE / n_trade_coins

    if alloc < MIN_POSITION_USD:
        alloc = MIN_POSITION_USD
    qty = alloc / price
    if precision > 0:
        qty = round(qty, precision)
    else:
        qty = int(qty)
    return max(qty, min_qty)


# === MAIN BOT LOOP ===

def run_bot(live=False):
    """Main bot loop."""
    log.info("=" * 60)
    log.info(
        f"  RhythmFilter Bot {'LIVE' if live else 'DRY RUN'}")
    log.info(
        f"  SL={SL_PCT}% / TP={TP_PCT}% / {LEVERAGE}x leverage")
    if APPS_SCRIPT_URL:
        log.info("  Cloud sync: ENABLED")
    else:
        log.info("  Cloud sync: DISABLED (no URL)")
    log.info("=" * 60)

    client = BitunixClient()
    blacklist = load_blacklist()
    approved = load_approved_coins()
    state = load_state()

    log.info(f"Approved coins: {len(approved)}")
    log.info(f"Blacklist: {len(blacklist)} coins")
    log.info(
        f"Open positions: {len(state['open_positions'])}")

    # EMA state for breathing score
    ema_value = None
    prev_scores = [None, None]
    bar_count = state.get("bar_count", 0)

    while True:
        try:
            now = datetime.now(timezone.utc)
            log.info(f"\n--- Tick {now:%H:%M:%S UTC} ---")

            # Get tradeable coins from Bitunix
            bitunix_coins = get_tradeable_coins(client)
            if not bitunix_coins:
                log.warning(
                    "No trading pairs available, sleeping...")
                time.sleep(60)
                continue

            # Build coin map for position sizing
            coin_map = {c["symbol"]: c for c in bitunix_coins}

            # ALL coins for breathing score
            all_symbols = [
                c["symbol"] for c in bitunix_coins
                if c["symbol"].lower() not in blacklist]
            # Only approved coins for trading
            trade_symbols = [
                s for s in all_symbols
                if s.lower() in approved
            ] if approved else all_symbols
            n_coins = len(all_symbols)
            n_trade = len(trade_symbols)
            log.info(
                f"{n_coins} breathing, "
                f"{n_trade} tradeable")

            # Check for closed positions
            check_position_exits(state, client, live)

            # Get balance for position sizing
            balance = (get_account_balance(client)
                       if live else 1000.0)

            # Compute breathing score
            raw_score = compute_breathing_score(
                all_symbols, n_coins)

            # EMA smooth
            if ema_value is None:
                ema_value = raw_score
            else:
                m = 2.0 / (EMA_SMOOTH + 1)
                ema_value = (raw_score * m
                             + ema_value * (1 - m))

            score = ema_value
            bar_count += 1

            # Update state for dashboard
            state["breathing_score"] = round(score, 4)
            state["breathing_raw"] = round(raw_score, 4)

            log.info(
                f"Breathing: raw={raw_score:.2f} "
                f"ema={score:.2f}")

            # Signal detection
            bars_since = (bar_count
                          - state["last_signal_bar"])

            if score > 0:
                state["was_green"] = True
                state["signal_state"] = "waiting"

            if (score < SHORT_THRESHOLD
                    and state["was_green"]):
                state["short_armed"] = True
                state["signal_state"] = "armed"
                if score < state["trough_val"]:
                    state["trough_val"] = score

            signal_fired = False

            if (state["short_armed"]
                    and prev_scores[0] is not None
                    and prev_scores[1] is not None
                    and score > prev_scores[0]
                    and prev_scores[0] <= prev_scores[1]
                    and score < 0
                    and bars_since >= SHORT_COOLDOWN):

                signal_fired = True
                state["signal_state"] = "fired"
                state["last_signal_bar"] = bar_count
                state["short_armed"] = False
                state["trough_val"] = 0.0
                state["was_green"] = False

                log.info(
                    f"*** SHORT SIGNAL *** "
                    f"score={score:.2f}")

            if state["short_armed"] and score > 0:
                state["short_armed"] = False
                state["trough_val"] = 0.0
                state["was_green"] = False
                state["signal_state"] = "waiting"

            # Update prev scores
            prev_scores[1] = prev_scores[0]
            prev_scores[0] = score

            # === EXECUTE TRADES ===
            if signal_fired:
                tickers = client.get_tickers()
                price_map = {}
                if tickers.get("code") == 0:
                    for t in tickers["data"]:
                        price_map[t["symbol"]] = float(
                            t["lastPrice"])

                coins_entered = 0
                for sym in trade_symbols:
                    if sym not in price_map:
                        continue
                    ci = coin_map.get(sym)
                    if not ci:
                        continue

                    price = price_map[sym]
                    if price == 0:
                        continue

                    # SL/TP prices
                    sl_price = round(
                        price * (1 + SL_PCT / 100), 8)
                    tp_price = round(
                        price * (1 - TP_PCT / 100), 8)

                    # Position sizing
                    qty = calculate_position_size(
                        balance, n_trade, price,
                        ci["min_qty"], ci["precision"])

                    if live:
                        lev = min(
                            LEVERAGE, ci["max_leverage"])
                        client.change_leverage(sym, lev)

                        result = client.open_short(
                            symbol=sym,
                            qty=qty,
                            tp_price=tp_price,
                            sl_price=sl_price,
                        )
                        if result.get("code") == 0:
                            log.info(
                                f"  SHORT {sym} "
                                f"@ {price} qty={qty} "
                                f"SL={sl_price} "
                                f"TP={tp_price}")
                            coins_entered += 1
                        else:
                            log.warning(
                                f"  FAILED {sym}: "
                                f"{result}")
                            continue
                    else:
                        log.info(
                            f"  [DRY] SHORT {sym} "
                            f"@ {price} qty={qty} "
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

                # Keep only last 50 signals in state
                if len(state["signal_history"]) > 50:
                    state["signal_history"] = (
                        state["signal_history"][-50:])

                log.info(
                    f"Entered {coins_entered} shorts")

            # Save state + sync to cloud
            state["bar_count"] = bar_count
            state["last_tick"] = now.isoformat()
            save_state(state)

            # Sleep until next 5-min candle
            time.sleep(LOOP_INTERVAL)

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
    args = parser.parse_args()
    run_bot(live=args.live)
