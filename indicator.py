"""RSI Trendline Breakout v8 — adapted for backtesting.

Ported from DexBot/bot/indicator.py.
Key difference: collects ALL signals over the full history
instead of returning after the first one.
"""

import numpy as np
import config


def calc_rsi(closes, length=14):
    """Wilder's smoothing RSI (matches TradingView)."""
    closes = np.array(closes, dtype=float)
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    rsi = np.full(len(closes), np.nan)
    if len(gains) < length:
        return rsi

    avg_gain = np.mean(gains[:length])
    avg_loss = np.mean(losses[:length])

    if avg_loss == 0:
        rsi[length] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi[length] = 100.0 - (100.0 / (1.0 + rs))

    for i in range(length, len(gains)):
        avg_gain = (avg_gain * (length - 1) + gains[i]) / length
        avg_loss = (avg_loss * (length - 1) + losses[i]) / length
        if avg_loss == 0:
            rsi[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i + 1] = 100.0 - (100.0 / (1.0 + rs))

    return rsi


def calc_macd(closes, fast=12, slow=26, signal=9):
    """MACD histogram."""
    closes = np.array(closes, dtype=float)

    def ema(data, period):
        result = np.full(len(data), np.nan)
        if len(data) < period:
            return result
        result[period - 1] = np.mean(data[:period])
        multiplier = 2.0 / (period + 1)
        for i in range(period, len(data)):
            result[i] = ((data[i] - result[i - 1]) * multiplier
                         + result[i - 1])
        return result

    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = ema_fast - ema_slow

    signal_line = np.full(len(closes), np.nan)
    valid_start = slow - 1
    if valid_start + signal <= len(closes):
        macd_valid = macd_line[valid_start:]
        sig = ema(macd_valid, signal)
        signal_line[valid_start:] = sig

    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calc_volume_ma(volumes, length=20):
    """Simple moving average of volume."""
    volumes = np.array(volumes, dtype=float)
    result = np.full(len(volumes), np.nan)
    for i in range(length - 1, len(volumes)):
        result[i] = np.mean(volumes[i - length + 1:i + 1])
    return result


def find_pivot_highs(rsi_values, strength=3):
    """Find pivot highs in RSI.
    Returns list of (bar_index, rsi_value) tuples.
    """
    pivots = []
    for i in range(strength, len(rsi_values) - strength):
        if np.isnan(rsi_values[i]):
            continue
        window = rsi_values[i - strength:i + strength + 1]
        if np.any(np.isnan(window)):
            continue
        if (rsi_values[i] == np.max(window) and
                rsi_values[i] > rsi_values[i - 1] and
                rsi_values[i] > rsi_values[i + 1]):
            pivots.append((i, rsi_values[i]))
    return pivots


class TrendlineState:
    """Trendline state tracker."""

    def __init__(self):
        self.anchor1_rsi = None
        self.anchor1_bar = None
        self.anchor2_rsi = None
        self.anchor2_bar = None
        self.touch_count = 0
        self.trendline_active = False
        self.signal_count = 0
        self.bars_since_cross = 100


def calc_trendline_value(state, bar):
    """Expected RSI value at a given bar on the trendline."""
    if (state.anchor1_bar is None or state.anchor2_bar is None
            or state.anchor1_bar == state.anchor2_bar):
        return None
    slope = ((state.anchor2_rsi - state.anchor1_rsi)
             / (state.anchor2_bar - state.anchor1_bar))
    return state.anchor1_rsi + slope * (bar - state.anchor1_bar)


def scan_all_signals(candles):
    """Run v8 indicator over full candle history.

    Returns list of all buy signals found:
    [
        {
            "bar_index": int,
            "time": int,
            "price": float,
            "rsi": float,
            "trendline_value": float,
            "touch_count": int,
            "bars_elapsed": int,
            "macd_hist": float,
            "volume_ratio": float,
        },
        ...
    ]
    """
    if len(candles) < config.MACD_SLOW + config.MACD_SIGNAL + 5:
        print("Not enough candles for indicator warmup")
        return []

    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]

    # Calculate indicators over full history
    rsi_values = calc_rsi(closes, config.RSI_LENGTH)
    _, _, histogram = calc_macd(
        closes, config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL
    )
    vol_ma = calc_volume_ma(volumes, config.VOLUME_MA_LENGTH)
    pivots = find_pivot_highs(rsi_values, config.PIVOT_STRENGTH)

    # Build pivot lookup for fast access
    pivot_map = {pb: pv for pb, pv in pivots}

    # Process bar by bar
    state = TrendlineState()
    signals = []
    start_bar = config.MACD_SLOW + config.MACD_SIGNAL

    for bar_idx in range(start_bar, len(candles)):
        rsi = rsi_values[bar_idx]
        if np.isnan(rsi):
            continue

        # Check for confirmed pivot at bar_idx - pivot_strength
        pivot_bar = bar_idx - config.PIVOT_STRENGTH
        pivot_rsi = pivot_map.get(pivot_bar)

        # === ANCHOR 1: Pivot at/above startLevel ===
        if (pivot_rsi is not None
                and pivot_rsi >= config.START_LEVEL):
            if (state.anchor1_rsi is None
                    or pivot_rsi > state.anchor1_rsi):
                trend_at_pivot = calc_trendline_value(
                    state, pivot_bar
                )
                should_reset = (
                    not state.trendline_active
                    or trend_at_pivot is None
                    or pivot_rsi > trend_at_pivot
                )
                if should_reset:
                    state.anchor1_rsi = pivot_rsi
                    state.anchor1_bar = pivot_bar
                    state.anchor2_rsi = None
                    state.anchor2_bar = None
                    state.touch_count = 1
                    state.trendline_active = False
                    state.signal_count = 0
                    state.bars_since_cross = 100

            # Reset on new high above trendline
            if state.trendline_active:
                trend_at_pivot = calc_trendline_value(
                    state, pivot_bar
                )
                if (trend_at_pivot is None
                        or pivot_rsi > trend_at_pivot):
                    state.anchor1_rsi = pivot_rsi
                    state.anchor1_bar = pivot_bar
                    state.anchor2_rsi = None
                    state.anchor2_bar = None
                    state.touch_count = 1
                    state.trendline_active = False
                    state.signal_count = 0
                    state.bars_since_cross = 100

        # === ANCHOR 2: Lower pivot below startLevel ===
        if (state.anchor1_bar is not None
                and state.anchor2_bar is None
                and pivot_rsi is not None
                and pivot_rsi < config.START_LEVEL
                and pivot_rsi < state.anchor1_rsi):
            state.anchor2_rsi = pivot_rsi
            state.anchor2_bar = pivot_bar
            state.trendline_active = True
            state.touch_count = 2

        # === COUNT TOUCHES ===
        if (state.trendline_active
                and pivot_rsi is not None
                and pivot_rsi < config.START_LEVEL):
            expected = calc_trendline_value(state, pivot_bar)
            if expected is not None:
                if (pivot_rsi <= expected + config.TOUCH_SENSITIVITY
                        and pivot_rsi >= expected
                        - config.TOUCH_SENSITIVITY):
                    state.touch_count += 1
                elif pivot_rsi < state.anchor2_rsi:
                    state.anchor2_rsi = pivot_rsi
                    state.anchor2_bar = pivot_bar
                    state.touch_count += 1

        # === BREAKOUT DETECTION ===
        current_trend = calc_trendline_value(state, bar_idx)
        prev_trend = calc_trendline_value(state, bar_idx - 1)

        prev_rsi = (rsi_values[bar_idx - 1]
                    if bar_idx > 0 else np.nan)
        if (current_trend is not None
                and prev_trend is not None
                and not np.isnan(prev_rsi)):
            just_crossed = (rsi > current_trend
                            and prev_rsi <= prev_trend)
            if just_crossed:
                state.bars_since_cross = 0
            else:
                state.bars_since_cross += 1
        else:
            state.bars_since_cross += 1

        crossed_above = (
            state.bars_since_cross <= 5
            and current_trend is not None
            and rsi > current_trend
        )

        bars_elapsed = (bar_idx - state.anchor1_bar
                        if state.anchor1_bar is not None else 0)
        valid_time = bars_elapsed >= config.MIN_BARS

        # Was RSI oversold recently?
        lb_start = max(0, bar_idx - config.LOOKBACK_BARS + 1)
        recent_rsi = rsi_values[lb_start:bar_idx + 1]
        recent_clean = recent_rsi[~np.isnan(recent_rsi)]
        was_oversold = (len(recent_clean) > 0
                        and np.min(recent_clean)
                        < config.OVERSOLD_LEVEL)

        # In buy zone?
        in_buy_zone = (rsi >= config.OVERSOLD_LEVEL - 2
                       and rsi < config.MAX_BREAKOUT_RSI)

        # MACD rising 3 bars
        macd_confirmed = False
        if bar_idx >= 2 and not np.isnan(histogram[bar_idx]):
            h0 = histogram[bar_idx]
            h1 = histogram[bar_idx - 1]
            h2 = histogram[bar_idx - 2]
            if not (np.isnan(h1) or np.isnan(h2)):
                macd_confirmed = h0 > h1 and h1 > h2

        # Volume confirmation
        volume_confirmed = False
        if not np.isnan(vol_ma[bar_idx]):
            volume_confirmed = (
                volumes[bar_idx]
                >= vol_ma[bar_idx] * config.VOLUME_THRESHOLD
            )

        # === BREAKOUT ===
        breakout = (
            state.trendline_active
            and crossed_above
            and valid_time
            and state.touch_count >= config.MIN_TOUCHES
            and was_oversold
            and in_buy_zone
            and state.signal_count < config.MAX_SIGNALS
            and macd_confirmed
            and volume_confirmed
        )

        if breakout:
            state.signal_count += 1
            state.bars_since_cross = 100

            signals.append({
                "bar_index": bar_idx,
                "time": candles[bar_idx]["time"],
                "price": candles[bar_idx]["close"],
                "rsi": float(rsi),
                "trendline_value": float(current_trend),
                "touch_count": state.touch_count,
                "bars_elapsed": bars_elapsed,
                "macd_hist": float(histogram[bar_idx]),
                "volume_ratio": (
                    volumes[bar_idx] / vol_ma[bar_idx]
                    if vol_ma[bar_idx] > 0 else 0
                ),
            })

    return signals
