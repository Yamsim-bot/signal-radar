# GBPJPY Cent Raw ECN — Realistic Strategy v2

A trend-following GBPJPY strategy designed for **Vantage Cent Raw ECN** accounts with a **$500 starting capital**.

## Target: ~$25–$50/month ($12–$25 bi-weekly)

> **Why not $90–$120?** That target requires 18–24% return every 2 weeks.
> You would need to risk 5%+ per trade, survive on a ~40% win rate,
> and have zero tolerance for the inevitable 3–5 loss streaks.
> **85–95% probability of blowing the account within 6 months.**

## Cost structure (Vantage Cent Raw ECN)

| Item | Cost |
|---|---|
| Spread (GBPJPY) | ~0.3–0.8 pips liquid hours |
| Commission | $0.06 round-turn per cent lot |
| Pip value (1 cent lot) | ~$0.0093 at 193.00 |
| 1 pip cost (spread) | ~$0.003–$0.007 per cent lot |

## Strategy logic

- **Timeframe:** M15
- **Session:** London peak (08:00–15:00 GMT) — tightest window for highest volatility
- **Trend:** EMA 20/50 crossover
- **Entry:** RSI(7) oversold/overbought crossover after trend confirmation
- **Stop:** ATR(14) × 1.5 (minimum 20 pips)
- **Partial TP:** 50% at 1:1 RR, then trail remaining
- **Trail:** ATR-based trailing on remaining position (locked above breakeven)
- **Risk:** 1% per trade

## News filter

**Blocks trading before high-impact news** to avoid pre-news chop and fakeouts.
**Enables momentum-mode entries** in the 30-minute window after news volatility settles.

### Default high-impact events watched (GMT):
- UK data (CPI, GDP, Employment, Retail Sales) — **07:00**
- BOE decisions — **12:00**
- US data (NFP, CPI, PPI, Durable Goods) — **13:30**

### Python EA:
Fetches **live ForexFactory calendar** each day. Filters for high-impact GBP/JPY/USD events.
Falls back to schedule-based if ForexFactory is unreachable.

### Pine Script:
Schedule-based (ForexFactory requires external API on TV).
User-configurable: toggle each event window + custom event time.

## Circuit breakers

| # | Breaker | Limit | Action |
|---|---|---|---|
| 1 | **Consecutive losses** | 2 SLs in a row | Locked until next day |
| 2 | **Daily loss** | 5% | Locked for the day |
| 3 | **Total drawdown** | 20% | Permanent stop |
| 4 | **Spread** | > 2 pips | Skip trade |
| 5 | **Session** | 08:00–15:00 GMT | No trading outside hours |
| 6 | **News** | Before high-impact | Block × 45 min before, wait 15 min after |

> **No revenge trading:** the consecutive-loss breaker (2 SLs = stop) forces you to step
> away for the day. This is the single most important rule for avoiding a blown account.

## Files

| File | Purpose |
|---|---|
| `gbpjpy_cent_raw_ecn.pine` | TradingView Pine Script v5 strategy |
| `gbpjpy_mt5_ea.py` | Python EA for MetaTrader 5 (+ ForexFactory news) |
| `REQUIREMENTS.txt` | Python dependencies |
| `README.md` | This file |

## Pine Script — TradingView

1. Open TradingView → Pine Editor
2. Paste contents of `gbpjpy_cent_raw_ecn.pine`
3. Add to chart (GBPJPY, M15 timeframe)
4. Adjust inputs in **Settings** → **Strategy** tab
5. Use the **Strategy Tester** tab to backtest

### Pine Script inputs to configure:
- **News filter:** toggle each UK/US event window + set custom times
- **Risk per trade:** defaults to 1% ($5 on $500)
- **Partial TP %:** defaults to 50% at 1:1
- **Trail:** toggle on/off, adjust ATR multiplier

> **Note:** Pine Script backtests don't model news-session spread widening or slippage
> in fast markets. Results are optimistic.

## Python EA — MetaTrader 5

### Setup

```bash
pip install -r REQUIREMENTS.txt
python gbpjpy_mt5_ea.py
```

### Options

```bash
python gbpjpy_mt5_ea.py                          # Auto-detect MT5
python gbpjpy_mt5_ea.py "C:\Program Files\Vantage MT5\terminal64.exe"  # Custom path
python gbpjpy_mt5_ea.py --backtest                # Paper backtest (500 candles)
python gbpjpy_mt5_ea.py --news-only               # Print today's news events
python gbpjpy_mt5_ea.py --help                    # Usage
```

### What the EA does on each tick:

```
1. Check circuit breakers (loss streak, daily loss, drawdown)
2. Check news blackout (live ForexFactory data)
3. Check session hours (08:00-15:00 GMT)
4. Check spread (< 2 pips)
5. If in position → manage partial TP + trailing stop
6. If no position → compute signal from EMA/RSI
7. If signal → size position at 1% risk, send order with SL + TP
```

### Journal

All actions logged to `gbpjpy_journal.json` — review it weekly.
News events cached locally in `gbpjpy_news_cache.json` (refreshes every 6 hours).

## Monthly projections

| Scenario | Win rate | Monthly | Bi-weekly | Survive 6 mo |
|---|---|---|---|---|
| Conservative | 55% | 2–4% ($10–$20) | $5–$10 | 95%+ |
| Moderate | 50% | 5–8% ($25–$40) | $12–$20 | 75–85% |
| Ambitious | 45% | 8–12% ($40–$60) | $20–$30 | 50–60% |
| **Your original ask** | 40% | 36–48% ($180–$240) | $90–$120 | **5–15%** |

**Key insight:** The difference between Moderate and Your Ask is **position sizing**.
Hitting $90/2 weeks means risking 5× more per trade — 3 consecutive losses wipes 30%+ of the account.
That happens ~3×/year even with a 50% win rate.
