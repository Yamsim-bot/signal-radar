# Signal Radar — Multi-Asset Trading Bias Scanner

Scans 39 trading instruments combining **Technical Analysis (50%) + Fundamental Analysis (30%) + Sentiment (20%)** to produce directional bias per instrument.

**Live demo:** https://yamsradar.onrender.com *(after deploying)*

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/Yamsim-bot/signal-radar)

---

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run the web dashboard
python -m signal_radar.web

# Or run the CLI scanner
python -m signal_radar.cli
```

- **Web:** http://127.0.0.1:5000
- **CLI:** `python -m signal_radar.cli -s 7` (strength 7+)

---

## Features

| Tab | Description |
|-----|-------------|
| **📡 Radar** | Horizontal table of 39 instruments with bias/score/strength/confidence/price/chg%/trend/best time/entry. Click a row for factor breakdown. |
| **💬 Ask Me** | AI chatbot — ask about any pair, category, or market condition |
| **🧮 Calculator** | Position size calculator: pips, fees, risk/reward for any pair |
| **📓 Journal** | Trading journal with stats (win rate, P&L, profit factor) |
| **📰 News** | Calendar events, news headlines, central bank schedule, trending topics |

### Instrument Coverage (39)
- **7 Majors:** EURUSD, GBPUSD, USDJPY, USDCHF, USDCAD, AUDUSD, NZDUSD
- **17 Crosses:** GBPJPY, EURJPY, EURGBP, EURCHF, AUDJPY, CHFJPY, NZDJPY, GBPAUD, EURAUD, AUDNZD, NZDCAD, AUDCAD, GBPCAD, GBPCHF, EURNZD, EURCAD, CADCHF
- **6 Indices:** US30, SP500, NAS100, DAX40, FTSE100, JP225
- **4 Commodities:** XAUUSD, XAGUSD, XTIUSD, XBRUSD
- **5 Stocks:** AAPL, TSLA, GOOG, AMZN, MSFT

### CLI Filters
```bash
python -m signal_radar.cli -s 7                  # Min strength 7+
python -m signal_radar.cli -c major -s 8          # Majors only, strength 8+
python -m signal_radar.cli --max-strength 3        # Only weak signals
python -m signal_radar.cli --detail 5              # Show factor breakdown for top 5
python -m signal_radar.cli --news-only             # Only instruments with high-impact news
python -m signal_radar.cli -c stock                # Stocks only
```

---

## Deploy to Render (Free)

1. **Push to GitHub:**
```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USER/signal-radar.git
git push -u origin main
```

2. **Go to** https://dashboard.render.com → **New +** → **Web Service**
3. **Connect your GitHub repo**
4. **Settings:**
   - **Name:** `signal-radar`
   - **Environment:** `Python 3`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`
   - **Free Plan** ✓

5. **Add Environment Variable** (optional):
   - `SECRET_KEY` — random string for session security

6. **Deploy** → Your URL will be `https://signal-radar.onrender.com`

### Custom Domain (Optional)
- Buy a domain (e.g. `app.signalradar.com`)
- Add it in Render dashboard → Settings → Custom Domain
- Update your DNS with the provided CNAME record

---

## Architecture

```
PRICE DATA ──→ Technical Analysis ──→ Market Structure Score
                                              │
NEWS / RSS ──→ Sentiment Analysis ──→ Sentiment Score
                                              │
CALENDAR  ──→ Fundamental Analysis ──→ Fundamental Score
                                              │
                                         ┌────┴────┐
                                         │  RADAR  │  Weighted: 50% TA + 30% FA + 20% Sent
                                         │  ENGINE │
                                         └────┬────┘
                                              │
                                    ┌─────────┴─────────┐
                                    │  Per-Instrument   │
                                    │  BIAS + CONFIDENCE│
                                    └───────────────────┘
```

### File Structure
```
signal_radar/
├── __init__.py          # Package exports (scan, RadarResult)
├── config.py            # Weights, periods, settings
├── instruments.py       # 39-instrument database + helpers
├── data_fetcher.py      # MT5 data + sample data generator
├── indicators.py        # ADX, RSI, MACD, BB, EMA, ATR
├── market_structure.py  # Swing high/low, BOS/CHoCH, trend score
├── areas_of_value.py    # S/R zones, order blocks, Fibonacci
├── timing.py            # Session analysis, entry windows, blackout
├── calendar.py          # Economic calendar, central bank schedule
├── sentiment.py         # Multi-source RSS + VADER scoring
├── fundamental.py       # CB stances, COT, 6-factor breakdown
├── radar.py             # Weighted scoring engine
├── cli.py               # Terminal dashboard
├── web.py               # Flask web server + API
└── templates/
    └── radar.html       # Web dashboard template
app.py                   # Production gunicorn entry point
Procfile                 # Render deployment config
requirements.txt         # Python dependencies
runtime.txt              # Python version
```

---

## VPS Deployment (DigitalOcean, Linode, etc.)

```bash
# SSH into your server
git clone https://github.com/YOUR_USER/signal-radar.git
cd signal-radar
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run with gunicorn
gunicorn app:app --bind 0.0.0.0:80 --workers 4 --timeout 120
```

### With Docker
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:80", "--workers", "4"]
```

---

## Scoring Weights

| Component | Weight | Range |
|-----------|--------|-------|
| Technical Analysis | 50% | -100 to +100 |
| Fundamental Analysis | 30% | -100 to +100 |
| Sentiment | 20% | -100 to +100 |

### Bias Thresholds
| Score Range | Label |
|-------------|-------|
| +60 to +100 | Strong Buy |
| +20 to +60  | Buy |
| -20 to +20  | Neutral |
| -60 to -20  | Sell |
| -100 to -60 | Strong Sell |

---

## License
MIT
