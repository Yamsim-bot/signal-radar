# XAUUSD Cent Raw ECN — Realistic Scalper

## Target: ~$25–$50/month ($12–$25 bi-weekly)

> **Why not $90–$120?** Same math as GBPJPY — you'd need 18-24% every 2 weeks.
> On $500 that means risking 5%+ per trade. Two bad gold news spikes and the account is halved.

## Cost structure (Vantage Cent Raw ECN)

| Item | Cost |
|---|---|
| Spread (XAUUSD) | ~1.0–2.5 pips liquid hours |
| Commission | $0.06 round-turn per cent lot |
| Pip value (1 cent lot) | ~$0.10 |
| Typical ATR (M5) | $3–$8 |

## Strategy logic

- **Timeframe:** M5 entry, M15 trend
- **Session:** NY session (12:00–20:00 GMT) — gold's most volatile hours
- **Trend:** EMA 20/50 crossover + EMA 200 bias
- **Entry:** ADX ≥ 20 + DI filter + pillars confirmation
- **Stop:** ATR × fib_mult (stealth levels, non-round numbers)
- **Partial TP:** 50% at fib-based partial
- **Trail:** ATR-based trailing, activates after 0.4 ATR in profit
- **News:** Blocks 45 min before US high-impact data (NFP, CPI, GDP)
- **Risk:** 1% per trade

## Circuit breakers

| # | Breaker | Limit | Action |
|---|---|---|---|
| 1 | Consecutive losses | 2 | Locked for day |
| 2 | Daily losses | 2 | Locked for day |
| 3 | Daily trades | 8 | Locked for day |
| 4 | Drawdown | 20% | Permanent stop |
| 5 | News | Before US data | Blocks 45 min before |

## Files

| File | Purpose |
|---|---|
| `mate_scalper_pro.py` | Original full scalper (60KB — ADX/DI/stealth/backtest) |
| `deploy_xauusd_cent.py` | **Main bot** — patches for cent account, auto-detects symbol, verbose logging |
| `xauusd_cent_raw_ecn.pine` | TradingView Pine Script v5 for XAUUSD |

## How to use

### 1. Check your setup first
```bash
python deploy_xauusd_cent.py --status
```

### 2. Find the right symbol
```bash
python deploy_xauusd_cent.py --find-symbol
```

### 3. Run the bot
```bash
python deploy_xauusd_cent.py                    # 24/7
python deploy_xauusd_cent.py --hours 8          # 8-hour session
```

### 4. Check logs
```bash
cat logs/cent_scalper.log | tail -20
```

## What was fixed vs the original

| Issue | Original | Fixed |
|---|---|---|
| Symbol | Hardcoded `XAUUSD+` | Auto-detects (tries 7 variants) |
| Daily losses | Hardcoded `>= 2` | Uses `config.max_consecutive_losses` |
| Logging | Silent skip | Logs WHY each signal was rejected |
| Commission | $6/standard lot RT | $0.06/cent lot RT |
| Pip value | $10/pip/standard lot | $0.10/pip/cent lot |
| Capital | $10,000 demo | $500 cent account |
