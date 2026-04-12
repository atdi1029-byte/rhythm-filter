"""Rhythm Filter Autobot — Standalone SHORT bot.

Computes breathing score from CryptoCompare data.
Opens shorts on Bitunix when signal fires.
No SL, no auto-close. Max 3 shorts per coin.
You close manually when green.

Usage:
    python autobot.py              # dry run (paper)
    python autobot.py --live       # real money
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

# Load .env from autobot directory
load_dotenv(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '.env'))

from bitunix_api import BitunixClient

# === SETTINGS ===
LEVERAGE = 1
POSITION_USD = 3.0        # $ per short position
MAX_SHORTS_PER_COIN = 3
POLL_INTERVAL = 300        # check every 5 min (one candle)
SIGNAL_COOLDOWN = 300      # 5 min between signal checks
WARMUP_PAGES = 6           # ~40 days of warmup data

# Breathing score params
RSI_LEN = 14
BUY_ZONE = 40.0
SELL_ZONE = 60.0
EMA_LEN = 9
TOTAL_COINS = 40
SHORT_THRESHOLD = -5.0
SHORT_COOLDOWN_BARS = 12

# 40 coins for breathing score
SCORE_COINS = [
    "BTC", "ETH", "XRP", "BNB", "SOL",
    "DOGE", "ADA", "TRX", "AVAX", "SHIB",
    "TON", "LINK", "SUI", "DOT", "NEAR",
    "UNI", "APT", "POL", "ARB", "OP",
    "ICP", "HBAR", "FIL", "ATOM", "IMX",
    "INJ", "STX", "S", "GRT", "THETA",
    "ALGO", "LDO", "AAVE", "SKY", "SNX",
    "VET", "XLM", "PEPE", "FET", "WLD",
]

# Top 9 coins to trade (best performers from live bot)
TRADE_COINS = [
    "APTUSDT", "FETUSDT", "THETAUSDT", "SNXUSDT",
    "GRTUSDT", "OPUSDT", "WIFUSDT", "ENJUSDT",
    "HBARUSDT",
]

BLACKLIST = set()  # none for now

# Files
BOT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BOT_DIR, "state.json")
CACHE_FILE = os.path.join(BOT_DIR, "candle_cache.json")
LOG_FILE = os.path.join(BOT_DIR, "autobot.log")

# Logging — file only (no StreamHandler duplication)
log = logging.getLogger("autobot")
log.setLevel(logging.INFO)
_fh = logging.FileHandler(LOG_FILE)
_fh.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(_fh)


# ==========================================
# DATA FETCHING
# ==========================================

CC_API_KEY = os.environ.get("CRYPTOCOMPARE_API_KEY", "")


def fetch_candles_binance_us(symbol, limit=1000,
                              end_time=None):
    """Fetch 5m candles from Binance US (public)."""
    url = "https://api.binance.us/api/v3/klines"
    params = {
        "symbol": symbol + "USDT",
        "interval": "5m",
        "limit": min(limit, 1000),
    }
    if end_time:
        params["endTime"] = end_time
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return [{"time": int(k[0]), "close": float(k[4])}
            for k in r.json()]


def fetch_candles_cc(symbol, limit=2000, to_ts=None):
    """Fetch 5m candles — try CryptoCompare first,
    fall back to Binance US."""
    # Try CryptoCompare
    try:
        url = ("https://min-api.cryptocompare.com"
               "/data/v2/histominute")
        params = {
            "fsym": symbol, "tsym": "USDT",
            "limit": limit, "aggregate": 5,
            "e": "Binance",
        }
        if to_ts:
            params["toTs"] = to_ts
        if CC_API_KEY:
            params["api_key"] = CC_API_KEY
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data.get("Response") == "Success":
            return [
                {"time": b["time"] * 1000,
                 "close": b["close"]}
                for b in data["Data"]["Data"]]
        # Rate limited — fall through to Binance US
    except Exception:
        pass

    # Fallback: Binance US (max 1000 per request)
    end_ms = (to_ts * 1000) if to_ts else None
    return fetch_candles_binance_us(
        symbol, min(limit, 1000), end_ms)


def fetch_extended(symbol, pages=6):
    """Fetch multiple pages for warmup data."""
    all_candles = []
    to_ts = None
    for _ in range(pages):
        candles = fetch_candles_cc(symbol, 2000, to_ts)
        if not candles:
            break
        all_candles = candles + all_candles
        to_ts = candles[0]["time"] // 1000 - 1
        time.sleep(2)
    # Deduplicate
    seen = set()
    deduped = []
    for c in all_candles:
        if c["time"] not in seen:
            seen.add(c["time"])
            deduped.append(c)
    deduped.sort(key=lambda x: x["time"])
    return deduped


def fetch_latest_candle(symbol):
    """Fetch the most recent 5m candle."""
    candles = fetch_candles_cc(symbol, limit=2)
    if candles:
        return candles[-1]
    return None


# ==========================================
# INDICATORS
# ==========================================

def compute_rsi(closes, length=14):
    """Wilder's RSI — matches Pine Script ta.rsi()."""
    rsi = [None] * len(closes)
    if len(closes) < length + 1:
        return rsi
    gains, losses = [], []
    for i in range(1, length + 1):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains) / length
    al = sum(losses) / length
    if al == 0:
        rsi[length] = 100.0
    else:
        rsi[length] = 100.0 - 100.0 / (1.0 + ag / al)
    for i in range(length + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (length - 1) + max(d, 0)) / length
        al = (al * (length - 1) + max(-d, 0)) / length
        if al == 0:
            rsi[i] = 100.0
        else:
            rsi[i] = 100.0 - 100.0 / (1.0 + ag / al)
    return rsi


def compute_ema(values, length):
    """EMA with SMA seed — matches Pine Script ta.ema()."""
    ema = [None] * len(values)
    m = 2.0 / (length + 1)
    buf = []
    seed_idx = None
    for i, v in enumerate(values):
        if v is not None:
            buf.append(v)
            if len(buf) == length:
                seed_idx = i
                break
    if seed_idx is None:
        return ema
    ema[seed_idx] = sum(buf) / length
    for i in range(seed_idx + 1, len(values)):
        if values[i] is not None and ema[i - 1] is not None:
            ema[i] = values[i] * m + ema[i - 1] * (1 - m)
        else:
            ema[i] = ema[i - 1]
    return ema


def score_rsi(rsi_val):
    if rsi_val is None:
        return 0
    if rsi_val < BUY_ZONE:
        return 1
    if rsi_val > SELL_ZONE:
        return -1
    return 0


# ==========================================
# BREATHING ENGINE
# ==========================================

class BreathingEngine:
    """Maintains breathing score state across updates."""

    def __init__(self):
        self.coin_candles = {}   # sym -> list of candles
        self.ref_times = []
        self.breath_scores = []
        self.raw_scores = []
        self.coin_rsis = {}

        # Signal state
        self.was_green = False
        self.short_armed = False
        self.trough_val = 0.0
        self.last_short_bar = -999
        self.n_bars = 0

        # Track signals on new bars during replay
        self._replay_cutoff = 0
        self._new_signal = False

    def _save_cache(self):
        """Save candle data to disk for fast restarts."""
        try:
            with open(CACHE_FILE, "w") as f:
                json.dump(self.coin_candles, f,
                          separators=(",", ":"))
            log.info("Candle cache saved to disk")
        except Exception as e:
            log.warning(f"Cache save failed: {e}")

    def _load_cache(self):
        """Load cached candle data from disk.

        Returns True if cache was loaded successfully.
        """
        if not os.path.exists(CACHE_FILE):
            return False
        try:
            age_hr = ((time.time()
                       - os.path.getmtime(CACHE_FILE))
                      / 3600)
            if age_hr > 48:
                log.info("Cache too old (>48h), "
                         "doing full warmup")
                return False

            with open(CACHE_FILE) as f:
                self.coin_candles = json.load(f)

            if "BTC" not in self.coin_candles:
                return False

            n_coins = len(self.coin_candles)
            last_t = self.coin_candles["BTC"][-1]["time"]
            age_min = ((time.time() * 1000 - last_t)
                       / 60000)
            log.info(f"Loaded cache: {n_coins} coins, "
                     f"{age_min:.0f}m old")
            return True
        except Exception as e:
            log.warning(f"Cache load failed: {e}")
            return False

    def load_warmup(self):
        """Load warmup data — from cache if fresh,
        otherwise fetch from CryptoCompare."""

        # Try cache first
        if self._load_cache():
            log.info("Backfilling from cache...")
            self._backfill_gaps()
            self._replay_cutoff = 999999  # suppress signals during warmup
            self._compute_full()
            self._save_cache()
            log.info(
                f"Warmup from cache: {self.n_bars} bars, "
                f"score={self.breath_scores[-1]:+.1f}")
            return

        # Full fetch
        log.info("Loading warmup data "
                 f"({WARMUP_PAGES} pages per coin)...")
        failed = []
        for i, sym in enumerate(SCORE_COINS):
            try:
                candles = fetch_extended(sym, WARMUP_PAGES)
                self.coin_candles[sym] = candles
                if (i + 1) % 10 == 0:
                    log.info(f"  {i+1}/40 loaded")
                time.sleep(1)
            except Exception as e:
                log.warning(f"  {sym}: FAILED ({e})")
                failed.append(sym)
                time.sleep(5)

        if "BTC" not in self.coin_candles:
            raise RuntimeError("BTC data required!")

        # Retry failed coins after cooldown
        if failed:
            log.info(f"Retrying {len(failed)} failed "
                     "coins after cooldown...")
            time.sleep(10)
            still_failed = []
            for sym in failed:
                try:
                    candles = fetch_extended(
                        sym, WARMUP_PAGES)
                    self.coin_candles[sym] = candles
                    log.info(f"  {sym}: OK on retry")
                    time.sleep(1)
                except Exception as e:
                    still_failed.append(sym)
                    time.sleep(2)
            if still_failed:
                log.warning(
                    "Still missing: "
                    f"{', '.join(still_failed)}")

        self._replay_cutoff = 999999  # suppress signals during warmup
        self._compute_full()
        self._save_cache()
        log.info(f"Warmup complete: {self.n_bars} bars, "
                 f"score={self.breath_scores[-1]:+.1f}")

    def _compute_full(self):
        """Full recompute from all candle data."""
        self.ref_times = [
            c["time"] for c in self.coin_candles["BTC"]]
        self.n_bars = len(self.ref_times)

        # RSI per coin
        self.coin_rsis = {}
        for sym in SCORE_COINS:
            if sym not in self.coin_candles:
                self.coin_rsis[sym] = [None] * self.n_bars
                continue
            lookup = {c["time"]: c["close"]
                      for c in self.coin_candles[sym]}
            closes = []
            for t in self.ref_times:
                if t in lookup:
                    closes.append(lookup[t])
                elif closes:
                    closes.append(closes[-1])
                else:
                    closes.append(None)
            valid = []
            first_valid = None
            for j, c in enumerate(closes):
                if c is not None:
                    if first_valid is None:
                        first_valid = j
                    valid.append(c)
            if (first_valid is None
                    or len(valid) < RSI_LEN + 1):
                self.coin_rsis[sym] = [None] * self.n_bars
                continue
            rsi_vals = compute_rsi(valid, RSI_LEN)
            full_rsi = [None] * self.n_bars
            for j, rv in enumerate(rsi_vals):
                full_rsi[first_valid + j] = rv
            self.coin_rsis[sym] = full_rsi

        # Raw breathing score
        self.raw_scores = [None] * self.n_bars
        for i in range(self.n_bars):
            total = 0
            count = 0
            for sym in SCORE_COINS:
                r = self.coin_rsis[sym][i]
                if r is not None:
                    total += score_rsi(r)
                    count += 1
            if count > 0:
                self.raw_scores[i] = (
                    total * 20.0 / TOTAL_COINS)

        # EMA smooth
        self.breath_scores = compute_ema(
            self.raw_scores, EMA_LEN)

        # Replay signal state to current bar
        self.was_green = False
        self.short_armed = False
        self.trough_val = 0.0
        self.last_short_bar = -999
        self._new_signal = False

        for i in range(2, self.n_bars):
            s = self.breath_scores[i]
            p = self.breath_scores[i - 1]
            p2 = self.breath_scores[i - 2]
            if s is None or p is None or p2 is None:
                continue
            if s > 0:
                self.was_green = True
            if s < SHORT_THRESHOLD and self.was_green:
                self.short_armed = True
                if s < self.trough_val:
                    self.trough_val = s
            if (self.short_armed and s > p and p <= p2
                    and s < 0
                    and (i - self.last_short_bar)
                    >= SHORT_COOLDOWN_BARS):
                # Signal fires on this bar
                self.short_armed = False
                self.last_short_bar = i
                self.trough_val = 0.0
                self.was_green = False
                # Track if this is a NEW bar (not old history)
                if i >= self._replay_cutoff:
                    self._new_signal = True
                    log.info(
                        f"Signal fired on bar {i} "
                        f"(cutoff={self._replay_cutoff}) "
                        f"score={s:+.1f}")
            if self.short_armed and s > 0:
                self.short_armed = False
                self.trough_val = 0.0
                self.was_green = False

    def _backfill_gaps(self):
        """Detect and fill candle gaps (e.g. after sleep).

        If BTC's last candle is more than 10 min old,
        fetch enough pages to cover the gap.
        """
        if "BTC" not in self.coin_candles:
            return
        last_t = self.coin_candles["BTC"][-1]["time"]
        now_ms = int(time.time() * 1000)
        gap_min = (now_ms - last_t) / 60000
        if gap_min <= 10:
            return  # no gap

        # Each candle = 5 min, each page = 2000 candles
        missed = int(gap_min / 5) + 10  # buffer
        pages = max(1, (missed // 2000) + 1)
        pages = min(pages, 3)  # cap at 3 pages

        log.info(f"Gap detected: {gap_min:.0f} min, "
                 f"backfilling {pages} page(s)...")

        for sym in SCORE_COINS:
            try:
                candles = fetch_extended(sym, pages)
                if candles and sym in self.coin_candles:
                    existing = {
                        c["time"] for c in
                        self.coin_candles[sym]}
                    added = 0
                    for c in candles:
                        if c["time"] not in existing:
                            self.coin_candles[sym].append(c)
                            existing.add(c["time"])
                            added += 1
                    self.coin_candles[sym].sort(
                        key=lambda x: x["time"])
                    if added > 0:
                        log.debug(f"  {sym}: +{added} candles")
            except Exception as e:
                log.debug(f"  Backfill {sym} failed: {e}")
            time.sleep(0.1)

        log.info("Backfill complete")

    def update(self):
        """Fetch latest candles and check for signal.

        Returns True if SHORT signal fires.
        """
        # Backfill any gaps from sleep/downtime
        self._backfill_gaps()

        # Fetch latest candle for each coin
        for sym in SCORE_COINS:
            try:
                c = fetch_latest_candle(sym)
                if c and sym in self.coin_candles:
                    last = self.coin_candles[sym][-1]
                    if c["time"] > last["time"]:
                        self.coin_candles[sym].append(c)
                    elif c["time"] == last["time"]:
                        self.coin_candles[sym][-1] = c
            except Exception as e:
                log.debug(f"Update {sym} failed: {e}")
            time.sleep(0.05)

        # Set cutoff so _compute_full knows which bars are new
        self._replay_cutoff = max(self.n_bars - 1, 0)
        self._compute_full()

        if self.n_bars < 3:
            return False

        # Signal already fired on a new bar during replay
        # (e.g. backfill added intermediate candles where
        # the peak-turn happened)
        if self._new_signal:
            s = self.breath_scores[self.last_short_bar]
            log.info(f"SHORT SIGNAL (from replay)! "
                     f"Score={s:+.1f}")
            return True

        i = self.n_bars - 1
        s = self.breath_scores[i]
        p = self.breath_scores[i - 1]
        p2 = self.breath_scores[i - 2]

        if s is None or p is None or p2 is None:
            return False

        # Check signal on latest bar
        signal = (self.short_armed and s > p and p <= p2
                  and s < 0
                  and (i - self.last_short_bar)
                  >= SHORT_COOLDOWN_BARS)

        if signal:
            self.short_armed = False
            self.last_short_bar = i
            self.trough_val = 0.0
            self.was_green = False
            log.info(f"SHORT SIGNAL! Score={s:+.1f} "
                     f"Raw={self.raw_scores[i]:+.1f}")

        return signal

    def get_score(self):
        if self.breath_scores and self.breath_scores[-1]:
            return self.breath_scores[-1]
        return 0.0

    def get_phase(self):
        s = self.get_score()
        if s >= 5:
            return "INHALE"
        if s <= -5:
            return "EXHALE"
        return "BETWEEN"

    def get_buy_sell_counts(self):
        buy = sell = 0
        for sym in SCORE_COINS:
            r = self.coin_rsis[sym][-1] if self.coin_rsis.get(sym) else None
            if r is not None:
                if r < BUY_ZONE:
                    buy += 1
                if r > SELL_ZONE:
                    sell += 1
        return buy, sell

    def health_check(self):
        """Check data freshness and report issues."""
        issues = []
        now_ms = int(time.time() * 1000)

        # Check BTC candle freshness
        if "BTC" in self.coin_candles:
            last_t = self.coin_candles["BTC"][-1]["time"]
            age_min = (now_ms - last_t) / 60000
            if age_min > 15:
                issues.append(
                    f"BTC data stale ({age_min:.0f}m old)")
        else:
            issues.append("BTC data missing!")

        # Check how many coins have data
        coins_ok = sum(
            1 for s in SCORE_COINS
            if s in self.coin_candles
            and len(self.coin_candles[s]) > 0)
        if coins_ok < 35:
            issues.append(
                f"Only {coins_ok}/40 coins have data")

        # Check score is computing
        if (not self.breath_scores
                or self.breath_scores[-1] is None):
            issues.append("Score not computing")

        if issues:
            log.warning(
                f"HEALTH: {' | '.join(issues)}")
        else:
            log.debug("Health check OK")


# ==========================================
# STATE MANAGEMENT
# ==========================================

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "open_positions": [],
        "total_signals": 0,
        "total_shorts_opened": 0,
    }


APPS_SCRIPT_URL = os.environ.get(
    "RHYTHM_APPS_SCRIPT_URL", "")


def sync_to_cloud(state, engine=None):
    """Push state to Apps Script for dashboard (GET)."""
    if not APPS_SCRIPT_URL:
        return
    try:
        cloud = {
            "open_positions": state["open_positions"],
            "total_signals": state["total_signals"],
            "total_shorts_opened": state["total_shorts_opened"],
            "breathing_score": (
                engine.get_score() if engine else 0),
            "signal_state": (
                "armed" if (engine and engine.short_armed)
                else "waiting"),
        }
        r = requests.get(APPS_SCRIPT_URL, params={
            "action": "save_state",
            "data": json.dumps(cloud, separators=(",", ":")),
        }, timeout=15)
        if r.status_code == 200:
            log.debug("Synced to cloud")
    except Exception as e:
        log.warning(f"Cloud sync error: {e}")


def save_state(state, engine=None):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    sync_to_cloud(state, engine)


def count_shorts_for_coin(state, symbol):
    """Count open shorts for a specific coin."""
    return sum(1 for p in state["open_positions"]
               if p["symbol"] == symbol)


# ==========================================
# TRADING
# ==========================================

def get_coin_info(client):
    """Get min qty and precision for all trade coins."""
    result = client.get_trading_pairs()
    if result.get("code") != 0:
        log.error(f"Failed to get trading pairs: {result}")
        return {}
    info = {}
    for p in result.get("data", []):
        sym = p["symbol"]
        if sym in TRADE_COINS or sym in BLACKLIST:
            info[sym] = {
                "min_qty": float(p["minTradeVolume"]),
                "precision": int(p["basePrecision"]),
                "max_leverage": int(p["maxLeverage"]),
            }
    return info


def get_prices(client, symbols):
    """Get current prices for symbols."""
    result = client.get_tickers(symbols)
    if result.get("code") != 0:
        return {}
    prices = {}
    for t in result.get("data", []):
        prices[t["symbol"]] = float(t["lastPrice"])
    return prices


def open_shorts(client, state, coin_info, dry_run=True):
    """Open shorts on all eligible coins."""
    active_coins = [c for c in TRADE_COINS
                    if c not in BLACKLIST]

    prices = get_prices(client, active_coins)
    if not prices:
        log.warning("No prices available")
        return

    opened = 0
    skipped_max = 0
    skipped_info = 0
    skipped_size = 0

    for sym in active_coins:
        # Check max shorts
        n = count_shorts_for_coin(state, sym)
        if n >= MAX_SHORTS_PER_COIN:
            skipped_max += 1
            continue

        if sym not in coin_info:
            skipped_info += 1
            continue

        price = prices.get(sym, 0)
        if price <= 0:
            continue

        info = coin_info[sym]
        qty = POSITION_USD / price
        precision = info["precision"]
        qty = round(qty, precision)

        if qty < info["min_qty"]:
            skipped_size += 1
            continue

        if dry_run:
            log.info(f"  [DRY] SHORT {sym} "
                     f"qty={qty} @ ${price}")
        else:
            try:
                result = client.open_short(sym, qty)
                if result.get("code") == 0:
                    log.info(f"  SHORT {sym} "
                             f"qty={qty} @ ${price}")
                    state["open_positions"].append({
                        "symbol": sym,
                        "qty": qty,
                        "entry_price": price,
                        "time": datetime.now(
                            timezone.utc).isoformat(),
                    })
                else:
                    log.warning(f"  FAIL {sym}: "
                                f"{result.get('msg')}")
                    continue
            except Exception as e:
                log.warning(f"  ERROR {sym}: {e}")
                continue

        opened += 1

    state["total_shorts_opened"] += opened
    save_state(state)

    log.info(f"Opened {opened} shorts | "
             f"Skipped: {skipped_max} at max, "
             f"{skipped_info} no info, "
             f"{skipped_size} too small")


def setup_leverage(client, coin_info):
    """Set isolated margin + leverage 1x for all coins."""
    log.info("Setting isolated margin + 1x leverage...")
    for sym in TRADE_COINS:
        if sym in BLACKLIST or sym not in coin_info:
            continue
        try:
            client.change_margin_mode(sym, "ISOLATION")
        except Exception:
            pass
        try:
            client.change_leverage(sym, LEVERAGE)
        except Exception:
            pass
        time.sleep(0.05)
    log.info("All coins set to isolated 1x.")


# ==========================================
# MAIN LOOP
# ==========================================

LOCK_FILE = os.path.join(BOT_DIR, "autobot.pid")


def check_single_instance():
    """Ensure only one instance runs at a time."""
    if os.path.exists(LOCK_FILE):
        with open(LOCK_FILE) as f:
            old_pid = f.read().strip()
        # Check if old process is still alive
        try:
            os.kill(int(old_pid), 0)
            log.error(f"Already running (PID {old_pid})")
            sys.exit(1)
        except (OSError, ValueError):
            pass  # old process is dead
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    import atexit
    atexit.register(
        lambda: os.remove(LOCK_FILE)
        if os.path.exists(LOCK_FILE) else None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true",
                        help="Real money mode")
    args = parser.parse_args()

    check_single_instance()

    dry_run = not args.live
    mode = "LIVE" if args.live else "DRY RUN"

    log.info(f"{'='*50}")
    log.info(f"Rhythm Filter Autobot — {mode}")
    log.info(f"Threshold: {SHORT_THRESHOLD}")
    log.info(f"Max shorts/coin: {MAX_SHORTS_PER_COIN}")
    log.info(f"Position size: ${POSITION_USD}")
    log.info(f"Leverage: {LEVERAGE}x")
    log.info(f"{'='*50}")

    # Init exchange client
    client = BitunixClient()
    state = load_state()

    # Get coin info
    log.info("Fetching coin info...")
    coin_info = get_coin_info(client)
    log.info(f"Got info for {len(coin_info)} coins")

    # Set leverage
    if not dry_run:
        setup_leverage(client, coin_info)

    # Test balance (endpoint may be broken on Bitunix)
    try:
        bal = client.get_balance()
        if bal.get("code") == 0:
            avail = bal.get("data", {}).get("available", 0)
            log.info(f"Balance: ${float(avail):.2f} USDT")
        else:
            log.info("Balance endpoint unavailable "
                     "(Bitunix API issue, trading OK)")
    except Exception as e:
        log.info(f"Balance check skipped: {e}")

    # Verify auth works (positions endpoint)
    try:
        pos = client.get_positions()
        if pos.get("code") == 0:
            log.info("API auth verified (positions OK)")
        else:
            log.error(f"API AUTH FAILED: {pos.get('msg')} "
                      "-- trades will not work!")
    except Exception as e:
        log.error(f"API auth test failed: {e}")

    # Init breathing engine
    engine = BreathingEngine()
    engine.load_warmup()

    score = engine.get_score()
    phase = engine.get_phase()
    buy_c, sell_c = engine.get_buy_sell_counts()
    log.info(f"Score: {score:+.1f} | Phase: {phase} | "
             f"Buy: {buy_c}/40 | Sell: {sell_c}/40")
    log.info(f"Armed: {engine.short_armed} | "
             f"WasGreen: {engine.was_green}")
    log.info(f"Open positions: "
             f"{len(state['open_positions'])}")
    log.info(f"Polling every {POLL_INTERVAL}s...")

    # Initial cloud sync
    save_state(state, engine)

    # Main loop
    tick_count = 0
    while True:
        try:
            time.sleep(POLL_INTERVAL)
            tick_count += 1

            signal = engine.update()
            score = engine.get_score()
            phase = engine.get_phase()
            buy_c, sell_c = engine.get_buy_sell_counts()

            log.info(f"Score: {score:+.1f} | "
                     f"Phase: {phase} | "
                     f"Buy: {buy_c}/40 | "
                     f"Sell: {sell_c}/40 | "
                     f"Armed: {engine.short_armed}")

            # Health check + cache save every ~1 hour
            if tick_count % 12 == 0:
                engine.health_check()
                engine._save_cache()

            # Sync to dashboard every tick
            save_state(state, engine)

            if signal:
                state["total_signals"] += 1
                log.info(
                    f"=== SIGNAL #{state['total_signals']}"
                    f" — OPENING SHORTS ===")
                open_shorts(client, state, coin_info,
                            dry_run=dry_run)
                save_state(state, engine)

        except KeyboardInterrupt:
            log.info("Shutting down...")
            save_state(state, engine)
            break
        except Exception as e:
            log.error(f"Loop error: {e}", exc_info=True)
            time.sleep(60)


if __name__ == "__main__":
    main()
