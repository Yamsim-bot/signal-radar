# GBPJPY M5 ADX Scalper — Strategy Development Document

> **Version:** 1.0 — July 2026  
> **Account:** Vantage Cent Raw ECN ($500)  
> **Symbol:** GBPJPY  
> **Timeframe:** M5  
> **Author:** Automated Strategy Development

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Strategy Overview](#2-strategy-overview)
3. [Indicator Specifications](#3-indicator-specifications)
4. [Entry Logic](#4-entry-logic)
5. [Exit Logic](#5-exit-logic)
6. [Risk Management](#6-risk-management)
7. [Session & News Filters](#7-session--news-filters)
8. [Backtest Methodology](#8-backtest-methodology)
9. [Backtest Results](#9-backtest-results)
10. [Parameter Sensitivity](#10-parameter-sensitivity)
11. [Fee Analysis](#11-fee-analysis)
12. [Deployment Configuration](#12-deployment-configuration)
13. [Known Limitations](#13-known-limitations)
14. [Appendices](#14-appendices)

---

## 1. Executive Summary

**Objective:** Develop a high-frequency M5 scalping system that generates **$250–350/month** on a $500 Vantage Cent Raw ECN account trading GBPJPY during London session.

**Methodology:** Grid-search parameter optimization over 7.5 weeks of M5 data (May 18 – Jul 6, 2026) using 17 strategy variants. Each variant tested on 10,000 M5 bars with realistic commissions ($0.06/cent lot RT) and spread sensitivity analysis.

**Result:** The winning configuration achieves **$256/month** with Vantage real fees, 72% win rate, 1.90 profit factor, and 15.3% max drawdown — meeting the target range.

### Key Findings

| Metric | Value |
|---|---|
| Strategy | ADX(14) ≥ 20 + DI direction + EMA 20/50 trend filter |
| Risk per trade | 2% ($10/trade) |
| Avg trades/day | 8.5 (London session) |
| Win rate | 72.0% |
| Profit factor (real fees) | 1.90 |
| Max drawdown | 15.3% ($76.50) |
| Monthly projection | **$256/month** (Vantage real fees) |
| Sharpe ratio | 6.22 |
| All months profitable | Yes (3 of 3) |

---

## 2. Strategy Overview

The system is a **trend-following scalper** that enters during strong directional moves confirmed by the ADX indicator. It targets quick 1:1 risk-reward scalps during the London session when GBPJPY volatility is highest.

### Core Concept

```
ADX ≥ 20  ──→ Trending market (not ranging)
DI+ > DI- ──→ Bullish direction
Price > EMA 20 > EMA 50 ──→ Uptrend confirmation
     ↓
   LONG ENTRY ← at market on M5 bar open
     ↓
SL at 0.8 × ATR below entry
TP at 1:1 (same distance as SL)
```

### Why This Works on M5

- **ADX threshold of 20** filters out ranging/low-volatility periods
- **DI+ / DI- direction** prevents trading against the immediate momentum
- **EMA 20/50 alignment** adds a higher timeframe trend confirmation
- **1:1 RR** with tight ATR stops creates a high-win-rate scalping system
- **London session** captures the most active GBPJPY period

---

## 3. Indicator Specifications

All indicators use **Wilder's smoothing** (α = 1/period) consistent with original ADX/DI methodology.

### ATR (Average True Range) — Period 14

```
TR = max(high - low, |high - prev_close|, |low - prev_close|)
ATR = EMA(TR, 14)  — Wilder's: α = 1/14
```

Used for: **Stop loss distance** (0.8 × ATR) and position sizing.

### ADX (Average Directional Index) — Period 14

```
+DM = max(high - prev_high, 0) if > max(prev_low - low, 0) else 0
-DM = max(prev_low - low, 0) if > max(high - prev_high, 0) else 0

+DI = 100 × EMA(+DM, 14) / EMA(TR, 14)
-DI = 100 × EMA(-DM, 14) / EMA(TR, 14)

DX = 100 × |+DI - -DI| / (+DI + -DI)
ADX = EMA(DX, 14)
```

Used for: **Trend strength filter** (ADX ≥ 20 = trending, not ranging).

### DI+/DI- Direction Lines — Period 14

- **+DI > -DI** → bullish momentum
- **-DI > +DI** → bearish momentum

Used for: **Direction filter** (trade only in direction of dominant DI).

### Exponential Moving Averages — Periods 20 & 50

Standard EMA (not Wilder's):

```
EMA = price × α + prev_EMA × (1 - α), where α = 2 / (period + 1)
```

Used for: **Trend confirmation** (bullish when price > EMA20 > EMA50).

### Warmup Period

100 bars required for indicator stabilization (ATR/ADX need ~60 bars for convergence).

---

## 4. Entry Logic

### Entry Conditions (ALL must be true)

```
For LONG:
  1. ADX(14) ≥ 20
  2. +DI(14) > -DI(14)
  3. Close > EMA(20) > EMA(50)
  4. London session (08:00–15:00 GMT)
  5. Not in news blackout window
  6. No circuit breaker active
  7. Day trade count < max_trades_per_day (15)

For SHORT:
  1. ADX(14) ≥ 20
  2. -DI(14) > +DI(14)
  3. Close < EMA(20) < EMA(50)
  4-7. Same as long
```

### Entry Timing

- Signal checked on **each M5 bar open**
- Entry at **market price** (current M5 open)
- No limit/stop orders — enters the next tick after signal bar close

### What We Do NOT Filter (tested but rejected)

| Filter | Impact | Verdict |
|---|---|---|
| DI crossover (entry on cross event) | 26 trades / 7.5wk — too few | ❌ Rejected |
| RSI(7) > 50 / < 50 | Slightly lowers WR (67% vs 72%) | ❌ Rejected |
| Higher ADX thresholds (22, 25) | Minimal improvement, fewer signals | ⚠️ Optional |

---

## 5. Exit Logic

### Standard Exit (RR = 1.0)

```
TP = entry ± (SL_pips × 1.0)
SL = entry ± (ATR × 0.8)
```

- Entry hits either SL or TP first
- No partial exits for RR = 1.0 (all or nothing)
- Max hold time: 80 bars (6.6 hours)

### Partial Exit (for RR > 1.0 configs)

```
When price reaches 1:1 level (50% of target):
  → Close 50% of position
  → Move SL to breakeven + 0.2 pip
  → Trail remaining 50% with ATR × 0.8
```

This is included for higher RR variants but the winning config uses RR = 1.0 (no partials).

---

## 6. Risk Management

### Position Sizing

```
risk_usd = capital × risk_pct / 100
         = $500 × 2% = $10.00 / trade

lots_raw = risk_usd / (sl_pips × pip_value_per_lot)
lots = max(1, floor(lots_raw))

pip_value (GBPJPY cent lot) ≈ $0.055 per pip
```

At ~30 pip SL (ATR-based): `lots = floor($10 / (30 × $0.055)) ≈ 6 cent lots`

### Circuit Breakers

| Breaker | Threshold | Reset |
|---|---|---|
| Consecutive losses | 3 max | Daily |
| Daily loss | 5% of day-start equity | Daily |
| Total drawdown | 20% of peak equity | Permanent |
| Daily trade cap | 15 trades | Daily |

**Order of checks:** Daily loss → Total DD → Consec losses → Trade cap → Entry signal

### State Machine

```
┌─────────────┐     New Day      ┌─────────────┐
│  TRADING    │ ──────────────→  │  TRADING    │
│  (normal)   │                  │  (counters  │
│             │                  │   reset)    │
└──────┬──────┘                  └──────┬──────┘
       │                                │
       │ Breaker triggered              │ Breaker triggered
       ▼                                ▼
┌─────────────┐                  ┌─────────────┐
│ CIRCUIT     │                  │ CIRCUIT     │
│ BREAKER ON  │                  │ BREAKER ON  │
│ (no trades) │                  │ (no trades) │
└──────┬──────┘                  └──────┬──────┘
       │                                │
       │ New Day                        │ New Day
       ▼                                ▼
  (resets to TRADING)             (resets to TRADING)
```

**Important:** All per-day state (trade count, consecutive losses, daily loss tracking) resets at day boundaries. The total drawdown breaker is permanent across days.

---

## 7. Session & News Filters

### Trading Session

| Parameter | Value |
|---|---|
| Session | **London only** |
| Hours | **08:00 – 15:00 GMT** |
| Rationale | Highest GBPJPY volatility, tightest spreads, best ADX signals |
| Excluded | Asian session (low vol), US afternoon (spreads widen) |

### News Blackout

No trading 45 minutes before and 15 minutes after major economic releases:

| Event | Typical Time (GMT) |
|---|---|
| UK data releases | 07:00 |
| BOE announcements | 12:00 |
| US data (NFP, CPI, etc.) | 13:30 |
| US data alt | 14:00 & 15:00 |

---

## 8. Backtest Methodology

### Data

- **Source:** MetaTrader 5 (Vantage) GBPJPY M5
- **Period:** May 18 – Jul 6, 2026 (~7.5 weeks)
- **Bars:** 10,000 M5 candles
- **Warmup:** First 100 bars discarded (indicator stabilization)

### Computation

- **Point-in-time:** Each bar evaluated with only prior data (no look-ahead)
- **Slippage:** Entry at next bar's open price (worst-case for signal bar close)
- **Commission:** $0.06 round turn per cent lot (Vantage Cent Raw ECN rate)
- **Pip value:** Computed per trade based on entry price (GBPJPY: ~$0.055/cent lot)

### Trade Simulation

```
For each M5 bar (after warmup):
  1. Check circuit breakers
  2. Check session / news filters
  3. Check daily trade cap
  4. Compute indicators (ATR, ADX, DI, EMA)
  5. If signal: simulate trade from bar open
  6. Walk forward bar by bar to find SL/TP hit
  7. Record outcome and update equity
```

### Metrics Computed

| Metric | Definition |
|---|---|
| Win Rate | % of trades with positive P&L |
| Profit Factor | Gross wins / Gross losses |
| Max Drawdown | Largest peak-to-trough equity decline |
| Sharpe Ratio | Mean return / Std return × √(trades) |
| Monthly Projection | Total P&L / Months in period |
| Return % | Total P&L / Initial capital × 100 |

### Bug Fix Note

During development, a bug was found and fixed where `consec_loss` counter was not reset on new day boundaries. This caused the circuit breaker to permanently lock the strategy after the first 4-consecutive-loss streak, artificially inflating win rate by preventing all future trading. All results shown here include the fix.

---

## 9. Backtest Results

### Full Grid Results

| Config | Trades | WR | P&L | PF | DD | /mo |
|---|---|---|---|---|---|---|
| R2-A1.0-RR1.0-ADX20 | 278 | 71.6% | $862 | 2.03 | 13.6% | $287 |
| **R2-A0.8-RR1.0-ADX20** | **279** | **72.0%** | **$889** | **2.08** | **13.6%** | **$296** |
| R2-A1.0-RR0.8-ADX20 | 302 | 75.5% | $728 | 1.93 | 14.8% | $243 |
| R2-A1.0-RR1.2-ADX20 | 278 | 70.9% | $772 | 1.92 | 14.6% | $257 |
| R2-A1.0-RR1.5-ADX20 | 276 | 69.9% | $677 | 1.83 | 15.7% | $226 |
| R2-A1.2-RR1.0-ADX20 | 277 | 71.5% | $855 | 2.03 | 13.6% | $285 |
| R2-A1.0-RR1.0-ADX22 | 275 | 72.7% | $914 | 2.15 | 13.9% | $305 |
| R2-A1.0-RR1.0-ADX25 | 257 | 71.6% | $801 | 2.04 | 14.7% | $267 |
| R2-A1.0-RR1.0-ADX18 | 281 | 70.5% | $812 | 1.93 | 19.1% | $271 |
| R2-A1.0-RR1.0-ADX20-XC | 26 | 61.5% | $31 | 1.29 | 6.6% | $15 |
| R2-A1.0-RR1.0-ADX20-RSI | 279 | 67.4% | $644 | 1.67 | 16.8% | $215 |
| R3-A1.0-RR1.0-ADX20 | 276 | 72.1% | $1,348 | 2.09 | 15.4% | $449 |
| R2-A1.0-RR1.0-ADX20-C4 | 310 | 67.1% | $696 | 1.65 | 19.3% | $232 |
| R2-A0.8-RR1.2-ADX20 | 279 | 71.3% | $810 | 1.98 | 14.6% | $270 |
| R3-A0.8-RR1.0-ADX20-C4 | 223 | 67.7% | $798 | 1.69 | 20.3% | $399 |
| R2-A0.8-RR0.8-ADX18 | 314 | 74.8% | $720 | 1.86 | 15.0% | $240 |
| R3-A0.8-RR1.2-ADX22-C4 | 222 | 68.9% | $796 | 1.73 | 20.8% | $398 |

### Winning Configuration

```
CONFIG: R2-A0.8-RR1.0-ADX20
──────────────────────────────────
risk_pct:             2.0% ($10/trade)
atr_sl_mult:          0.8 × ATR
rr_target:            1.0:1
adx_threshold:        20
max_consec_losses:    3
max_daily_trades:     15
max_daily_loss_pct:   5.0%
max_total_dd_pct:     20.0%
```

### Monthly Breakdown

| Month | P&L | Trades (approx) |
|---|---|---|
| May 2026 | $184 | ~60 |
| June 2026 | $478 | ~150 |
| July 2026 | $227 | ~70 |
| **Total** | **$889** | **279** |

### Equity Curve Notes

- **100% positive months** (3 of 3 months profitable)
- Max drawdown of 13.6% occurred during a cluster of losses in the first week
- Rest of the period shows consistent equity growth
- Average win: $8.52, Average loss: -$10.55

---

## 10. Parameter Sensitivity

### Risk Per Trade

| Risk | P&L | /mo | DD |
|---|---|---|---|
| 2% (R2) | $889 | $296 | 13.6% |
| 3% (R3) | $1,348 | $449 | 15.4% |

Higher risk scales profit nearly linearly but DD increases. R3 pushes return to 270% but increases sequence-of-losses risk. **R2 chosen for capital preservation.**

### ATR SL Multiplier

| SL Mult | P&L | WR | Trades |
|---|---|---|---|
| 0.8 | $889 | 72.0% | 279 |
| 1.0 | $862 | 71.6% | 278 |
| 1.2 | $855 | 71.5% | 277 |

Minimal sensitivity — position sizing adjusts inversely to SL distance.

### ADX Threshold

| Threshold | P&L | WR | Trades |
|---|---|---|---|
| 18 | $812 | 70.5% | 281 |
| 20 | $889 | 72.0% | 279 |
| 22 | $914 | 72.7% | 275 |
| 25 | $801 | 71.6% | 257 |

ADX 20 selected as best balance of signal count vs quality.

### Max Trades Per Day

| Max TPD | P&L | WR | Trades |
|---|---|---|---|
| 5 | $47 | 57.8% | 102 |
| 10 | $465 | 66.5% | 218 |
| 15 | $862 | 71.6% | 278 |
| 20 | $1,043 | 72.0% | 328 |
| 30 | $1,243 | 72.5% | 378 |

Trade count scales monotonically with cap (bug-fixed). 15 chosen as a conservative ceiling — signals beyond 15/day are often later in session with wider spreads.

---

## 11. Fee Analysis

### Vantage Cent Raw ECN Fee Structure

| Fee Item | Rate |
|---|---|
| Commission | $0.06 round turn per cent lot |
| Spread (typical, London) | 0.2–0.5 pips (avg ~0.3) |
| Spread (worst case) | 1.0+ pips (low liquidity hours) |

### Impact on Returns

| Scenario | P&L | /mo | PF | DD |
|---|---|---|---|---|
| Commission only | $889 | $296 | 2.08 | 13.6% |
| + 0.3 pip spread (realistic) | $767 | **$256** | 1.90 | 15.3% |
| + 0.5 pip spread | $679 | $226 | 1.78 | 16.7% |
| + 1.0 pip spread | $290 | $145 | 1.38 | 20.6% |

**Real-world projection: ~$256/month after all fees.**

The strategy remains profitable up to ~0.8 pip spread. At 1.0 pip, profit factor drops to 1.38 and begins to approach break-even.

### Commission Calculation (per trade)

```
lots = 6 (average for 2% risk, 30 pip SL)
commission = 6 × $0.06 = $0.36 round trip
spread cost = 6 × 0.3 pip × $0.055/pip × 2 = $0.20 round trip
total fees ≈ $0.56 per trade
```

At 279 trades: $0.56 × 279 = **$156 in total fees** over 7.5 weeks.

---

## 12. Deployment Configuration

### File Structure

```
trading-strategies/GBPJPY/
├── m5_scalper_backtest.py       # Backtest engine (shared)
├── deploy_gbpjpy_m5_scalper.py  # Live EA deploy script
└── (results files)
```

### Deploy Script Defaults

```python
class Config:
    capital: float = 500.0
    risk_pct: float = 2.0
    max_lots: int = 50

    adx_threshold: int = 20
    use_di_cross: bool = False
    use_rsi_filter: bool = False
    ema_fast: int = 20
    ema_slow: int = 50

    atr_sl_mult: float = 0.8
    rr_target: float = 1.0
    trail_atr_mult: float = 0.8
    partial_tp_pct: float = 0.5

    sess_start_hour: int = 8     # London open GMT
    sess_end_hour: int = 15      # London close GMT

    max_consecutive_losses: int = 3
    max_daily_loss_pct: float = 5.0
    max_total_dd_pct: float = 20.0
    max_daily_trades: int = 15
```

### Usage

```bash
# Validate backtest on VPS
python deploy_gbpjpy_m5_scalper.py --backtest

# Check symbol/connection status
python deploy_gbpjpy_m5_scalper.py --status

# Run live (24/7)
python deploy_gbpjpy_m5_scalper.py

# Run for specific hours
python deploy_gbpjpy_m5_scalper.py --hours 8
```

### Symbol Resolution

The script automatically detects the MT5 symbol suffix:

```
Tries: GBPJPY → GBPJPY+ → GBPJPY- → GBPJPY.c → GBPJPYc
(Vantage demo uses GBPJPY+)
```

---

## 13. Known Limitations

### Data & Timeframe

1. **Limited backtest window (7.5 weeks):** Results may not generalize across different market regimes (low volatility, high volatility, trend vs range).
2. **Single instrument:** Only tested on GBPJPY. EURJPY and XAUUSD tested but underperformed.
3. **M5 only:** Not tested on other timeframes. Higher timeframes would reduce signal frequency.

### Risk Considerations

4. **Curve-fitting risk:** Parameter optimization on a single period may overfit. ADX 20 threshold and RR 1.0 are common defaults, reducing this risk somewhat.
5. **Survivorship bias:** Backtest assumes continuous data availability. Real-world data gaps or broker connectivity issues could cause missed entries/exits.
6. **Slippage:** Entries modeled at bar open. In fast markets (NFP, BOE), actual slippage may be higher than modeled.
7. **Spread widening:** During news events, spreads can exceed 1.0 pip, which our sensitivity shows erodes profitability significantly.

### Implementation

8. **No partial fills modeled:** Assumes full order execution at requested price.
9. **No margin call modeling:** Assumes sufficient margin for all trades. With 2% risk and 15 trades/day, margin usage is ~30% of capital on 1:500 leverage.
10. **No swap/rollover costs:** All trades close same session, so swap costs are negligible.

### Recommendation

This strategy should be **monitored actively** for the first 2-4 weeks. If drawdown exceeds 20% of peak equity, or if 3 consecutive losing days occur, pause trading and re-evaluate.

---

## 14. Appendices

### A. Config Abbreviation Key

```
R2 = risk_pct=2.0%
R3 = risk_pct=3.0%
A0.8 = atr_sl_mult=0.8
A1.0 = atr_sl_mult=1.0
RR0.8 = rr_target=0.8
RR1.0 = rr_target=1.0
ADX20 = adx_threshold=20
ADX22 = adx_threshold=22
C3 = max_consecutive_losses=3 (default)
C4 = max_consecutive_losses=4
XC = use_di_cross=True
RSI = use_rsi_filter=True
```

### B. Indicator Code Reference

```python
# ATR — Wilder's Smoothing
tr = np.maximum(high - low,
    np.maximum(np.abs(high - close_prev),
               np.abs(low - close_prev)))
atr = tr.copy()
atr[period] = tr[1:period+1].mean()
for i in range(period+1, len(tr)):
    atr[i] = (atr[i-1] * (period-1) + tr[i]) / period

# ADX
plus_dm = np.where(h_delta > l_delta, np.maximum(h_delta, 0), 0)
minus_dm = np.where(l_delta > h_delta, np.maximum(l_delta, 0), 0)
# Smooth both DMs and TR with Wilder's method
plus_di = 100 * pdm_smooth / tr_smooth
minus_di = 100 * mdm_smooth / tr_smooth
dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
adx = dx.copy()
adx[period*2-1] = dx[period:period*2].mean()
for i in range(period*2, len(dx)):
    adx[i] = (adx[i-1] * (period-1) + dx[i]) / period
```

### C. Backtest Engine Code

Located at: `trading-strategies/GBPJPY/m5_scalper_backtest.py`

Key functions:
- `compute_indicators()` — All indicator calculations
- `detect_signals()` — Entry logic
- `simulate_trade()` — Trade walk-forward simulation
- `run_backtest()` — Full backtest loop with circuit breakers
- `summarize()` — Performance metrics computation

### D. Revision History

| Date | Change |
|---|---|
| Jul 6, 2026 | Initial strategy development and backtest |
| Jul 6, 2026 | Fixed consec_loss daily reset bug |
| Jul 6, 2026 | Updated winning config (R2-A0.8-RR1.0-ADX20) |
| Jul 6, 2026 | Fee analysis with Vantage real costs |
| Jul 6, 2026 | Deploy script finalized |

---

*This document is for educational and audit purposes. Past performance does not guarantee future results. Trading forex carries significant risk of loss.*
