"""RhythmFilter configuration — v8 RSI Trendline Breakout params."""

import os

# === INDICATOR PARAMETERS (Crypto v8) ===
RSI_LENGTH = 14
PIVOT_STRENGTH = 3
TOUCH_SENSITIVITY = 4.0
MIN_TOUCHES = 2
MIN_BARS = 15
START_LEVEL = 60
MAX_SIGNALS = 2
OVERSOLD_LEVEL = 30
MAX_BREAKOUT_RSI = 50
LOOKBACK_BARS = 20

# MACD
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# Volume
VOLUME_MA_LENGTH = 20
VOLUME_THRESHOLD = 0.7

# DMI
USE_DMI_FILTER = False
DMI_LENGTH = 14
ADX_SMOOTHING = 14
DMI_THRESHOLD = 35

# === BACKTEST SETTINGS ===
WIN_THRESHOLD = 0.05       # 5% gain = "win"
OUTCOME_BARS = {
    "1d": 6,               # 6 x 4H = 24 hours
    "3d": 18,
    "7d": 42,
    "14d": 84,
}

# === PATHS ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
