# RhythmFilter Backtest Results

## Summary (March 28, 2026)

### Strategy
- **Breathing Score**: Composite RSI across all coins, EMA-smoothed
- **Signal**: Short when breathing score troughs and turns up (still negative)
- **Entry**: Short all coins simultaneously on each signal
- **Exit**: Flat SL/TP, 7-day max hold

### Results by Coin Count

| Coins | Best SL/TP | Signals | Total P&L | Avg/Trade | Profitable Coins |
|-------|-----------|---------|-----------|-----------|-----------------|
| 36    | 6%/10%    | 1,666   | +43,844%  | +0.794%   | 35/36 (97%)     |
| 60    | 7%/12%    | 1,516   | +63,345%  | +0.791%   | 55/60 (92%)     |
| 100   | 8%/15%    | 1,177   | +63,720%  | +0.636%   | 81/100 (81%)    |
| 176   | 3%/15%    | 704     | +69,076%  | +0.660%   | 158/176 (90%)   |

### Key Findings

1. **Half-TP loses to flat**: Moving SL to breakeven after half-TP caps upside.
   Best half-TP was -22% worse than flat 6/10.

2. **More coins = fewer, better signals**: The breathing score gets more precise
   with more data points. 176 coins produced only 704 signals (vs 1,666 with 36).

3. **Optimal SL/TP shifts with coin count**: More volatile alts need wider TP.
   With 176 coins, 3% SL / 15% TP was optimal.

4. **BTC consistently negative**: Too stable for this strategy. Always blacklisted.

5. **Consistent losers (blacklist)**: BTC, LTC, PAXG, TFUEL, ONT, DASH, BAT

### Blacklist (18 coins at 176-coin level)
aaveusdt, wldusdt, roseusdt, dgbusdt, hypeusdt, 1inchusdt, ethusdt,
vthousdt, neousdt, btcusdt, zilusdt, qtumusdt, dashusdt, batusdt,
ltcusdt, ontusdt, tfuelusdt, paxgusdt

### Top Performers (176 coins, 3%/15%)
1. BOSON: +1,746%
2. OGN: +1,569%
3. LAZIO: +1,442%
4. LTO: +1,420%
5. PORTO: +1,405%

### Next Steps
- [ ] Expand to 500+ coins via binance.com API
- [ ] Build Bitunix trading bot
- [ ] Build RhythmFilter dashboard app
- [ ] Add Kelly criterion position sizing
- [ ] Add compound growth projector
- [ ] Add Pokemon level system
