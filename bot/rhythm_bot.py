"""RhythmFilter Trading Bot — Bitunix Futures.

Computes breathing score across N coins on 5-min candles.
Opens shorts on all non-blacklisted coins when signal fires.
Uses flat SL/TP with 7-day max hold.

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
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
APPROVED_FILE = os.path.join(config.DATA_DIR, "approved_coins_7_12.json")

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


def load_blacklist():
    if os.path.exists(BLACKLIST_FILE):
        with open(BLACKLIST_FILE) as f:
            return set(json.load(f))
    return set()


def load_approved_coins():
    """Load the approved coin list (whitelist). Only these get traded."""
    if os.path.exists(APPROVED_FILE):
        with open(APPROVED_FILE) as f:
            return set(json.load(f))
    return set()


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "open_positions": [],      # list of {symbol, entry_price, entry_time, qty}
        "signal_history": [],      # list of {time, breath_score, coins_entered}
        "last_signal_bar": -999,
        "was_green": False,
        "short_armed": False,
        "trough_val": 0.0,
        "total_pnl": 0.0,
        "total_trades": 0,
        "total_wins": 0,
        "rsi_history": {},         # coin -> list of last N closes
    }


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


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
            return [float(k[4]) for k in data]  # close prices
    except Exception as e:
        log.warning(f"Failed to get {symbol} candles: {e}")
    return []


def compute_rsi_from_closes(closes, length=14):
    """Compute RSI from a list of close prices. Returns latest RSI."""
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


def run_bot(live=False):
    """Main bot loop."""
    log.info("=" * 60)
    log.info(f"  RhythmFilter Bot {'LIVE' if live else 'DRY RUN'}")
    log.info(f"  SL={SL_PCT}% / TP={TP_PCT}% / {LEVERAGE}x leverage")
    log.info("=" * 60)

    client = BitunixClient()
    blacklist = load_blacklist()
    approved = load_approved_coins()
    state = load_state()

    log.info(f"Approved coins: {len(approved)}")
    log.info(f"Blacklist: {len(blacklist)} coins")
    log.info(f"Open positions: {len(state['open_positions'])}")

    # EMA state for breathing score
    ema_value = None
    prev_scores = [None, None]  # [prev, prev2]
    bar_count = state.get("bar_count", 0)

    while True:
        try:
            now = datetime.now(timezone.utc)
            log.info(f"\n--- Tick {now:%H:%M:%S UTC} ---")

            # Get tradeable coins from Bitunix
            bitunix_coins = get_tradeable_coins(client)
            if not bitunix_coins:
                log.warning("No trading pairs available, sleeping...")
                time.sleep(60)
                continue

            # Filter to approved coins only (whitelist approach)
            # Use ALL coins for breathing score, but only trade approved ones
            all_symbols = [c["symbol"] for c in bitunix_coins
                           if c["symbol"].lower() not in blacklist]
            trade_symbols = [s for s in all_symbols
                             if s.lower().replace("usdt", "usdt")
                             in approved] if approved else all_symbols
            n_coins = len(all_symbols)
            log.info(f"{n_coins} coins for breathing score, "
                     f"{len(trade_symbols)} approved for trading")

            # Compute breathing score
            raw_score = compute_breathing_score(all_symbols, n_coins)

            # EMA smooth
            if ema_value is None:
                ema_value = raw_score
            else:
                m = 2.0 / (EMA_SMOOTH + 1)
                ema_value = raw_score * m + ema_value * (1 - m)

            score = ema_value
            bar_count += 1

            log.info(f"Breathing: raw={raw_score:.2f} ema={score:.2f}")

            # Signal detection
            bars_since_signal = bar_count - state["last_signal_bar"]

            if score > 0:
                state["was_green"] = True

            if score < SHORT_THRESHOLD and state["was_green"]:
                state["short_armed"] = True
                if score < state["trough_val"]:
                    state["trough_val"] = score

            signal_fired = False

            if (state["short_armed"]
                    and prev_scores[0] is not None
                    and prev_scores[1] is not None
                    and score > prev_scores[0]
                    and prev_scores[0] <= prev_scores[1]
                    and score < 0
                    and bars_since_signal >= SHORT_COOLDOWN):

                signal_fired = True
                state["last_signal_bar"] = bar_count
                state["short_armed"] = False
                state["trough_val"] = 0.0
                state["was_green"] = False

                log.info(f"*** SHORT SIGNAL *** score={score:.2f} "
                         f"trough={state['trough_val']:.2f}")

            if state["short_armed"] and score > 0:
                state["short_armed"] = False
                state["trough_val"] = 0.0
                state["was_green"] = False

            # Update prev scores
            prev_scores[1] = prev_scores[0]
            prev_scores[0] = score

            # === EXECUTE TRADES ===
            if signal_fired:
                # Get current prices
                tickers = client.get_tickers()
                price_map = {}
                if tickers.get("code") == 0:
                    for t in tickers["data"]:
                        price_map[t["symbol"]] = float(t["lastPrice"])

                coins_entered = 0
                for coin_info in bitunix_coins:
                    sym = coin_info["symbol"]
                    if sym.lower() in blacklist:
                        continue
                    # Only trade approved coins
                    if approved and sym.lower() not in approved:
                        continue
                    if sym not in price_map:
                        continue

                    price = price_map[sym]
                    if price == 0:
                        continue

                    # Calculate SL/TP prices
                    sl_price = round(price * (1 + SL_PCT / 100), 8)
                    tp_price = round(price * (1 - TP_PCT / 100), 8)

                    # Calculate qty (minimum order or % of account)
                    qty = coin_info["min_qty"]

                    if live:
                        # Set leverage
                        lev = min(LEVERAGE, coin_info["max_leverage"])
                        client.change_leverage(sym, lev)

                        # Open short
                        result = client.open_short(
                            symbol=sym,
                            qty=qty,
                            tp_price=tp_price,
                            sl_price=sl_price,
                        )
                        if result.get("code") == 0:
                            log.info(f"  OPENED SHORT {sym} "
                                     f"@ {price} SL={sl_price} TP={tp_price}")
                            coins_entered += 1
                        else:
                            log.warning(f"  FAILED {sym}: {result}")
                    else:
                        log.info(f"  [DRY] SHORT {sym} "
                                 f"@ {price} SL={sl_price} TP={tp_price}")
                        coins_entered += 1

                    # Track position
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

                log.info(f"Entered {coins_entered} shorts")

            # Save state
            state["bar_count"] = bar_count
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
    parser = argparse.ArgumentParser(description="RhythmFilter Trading Bot")
    parser.add_argument("--live", action="store_true",
                        help="Enable live trading (default: dry run)")
    args = parser.parse_args()
    run_bot(live=args.live)
