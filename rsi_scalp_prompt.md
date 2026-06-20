# RSI Trendline Scalp — Build Prompt

Build a Pine Script v6 indicator called "RSI Trendline Scalp 15m" that runs on a 5m chart but computes all RSI trendline logic on the 15m timeframe via `request.security`.

## Source
Base the logic on `/Users/alexbarnett/Documents/Code/Claude/RSI_Trendline_Buy/bot/pinescript/rsi_trendline_v8_analyzer.txt` — the core trendline + breakout mechanism. Strip out DCA, double tap, Fib/webhook, and divergence. Keep it clean.

## Core Mechanism (all computed on 15m)
1. RSI(14) on 15m
2. Pivot detection (strength=3) finds RSI pivot highs
3. Anchor 1: pivot high at/above 60 (startLevel)
4. Anchor 2: lower pivot high → draw descending trendline
5. Count touches (sensitivity=4)
6. BUY: RSI breaks above trendline + was recently oversold (<30) + RSI still below 50 + min 2 touches + min 15 bars elapsed
7. SELL (failed breakout): RSI crossed above trendline but crosses back below within failure window (15 bars)

## Filters (all toggle-able, default ON)
- MACD confirmation (2 consecutive bars histogram rising for buy, falling for sell) — computed on 15m
- Volume above 0.7x average — computed on 15m

## Display (on 5m chart)
- Plot 15m RSI + RSI MA
- Draw the descending trendline on the RSI panel
- BUY = green triangle up at bottom
- SELL = red triangle down at top
- OB/OS levels (70/30) as dashed lines
- Info table (top right): current RSI, touch count, trendline status, signal status

## Alerts
- `alert()` calls for real-time push (include RSI value, touch count)
- `alertcondition()` for each signal type

## Colors
- RSI line: teal (#009688)
- RSI MA: light cyan (#80CBC4)
- Trendline: gray (#757575)
- Same dark theme as DSS Scalp

## Key Differences from v8
- NO multi-timeframe confirmation (already on 15m, displayed on 5m)
- NO Fibonacci SL/TP calculation
- NO DCA / double tap
- NO divergence detection
- NO webhook JSON formatting
- Just clean signals on a 5m chart

## File
Save to: `/Users/alexbarnett/Documents/Code/Claude/RhythmFilter/rsi_scalp_15m.pine`
