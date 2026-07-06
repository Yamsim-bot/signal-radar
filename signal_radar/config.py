"""Configuration for Signal Radar system."""

from pathlib import Path
from dataclasses import dataclass, field

HERE = Path(__file__).parent.resolve()
PROJECT_ROOT = HERE.parent
CACHE_DIR = HERE / "cache"
CACHE_DIR.mkdir(exist_ok=True)

@dataclass
class Config:
    # Data fetching
    mt5_bars: int = 200         # bars to fetch per symbol (M5 = ~16h at 200)
    cache_expiry_hours: int = 2
    use_cache: bool = True

    # Scoring weights (sum = 1.0)
    weight_technical: float = 0.50
    weight_fundamental: float = 0.30
    weight_sentiment: float = 0.20

    # Technical analysis
    adx_period: int = 14
    adx_threshold: int = 20
    ema_fast: int = 20
    ema_slow: int = 50
    rsi_period: int = 14
    bb_period: int = 20
    bb_std: float = 2.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    swing_lookback: int = 20
    swing_cluster_pips: int = 5

    # News feed URLs (RSS)
    rss_feeds: list = field(default_factory=lambda: [
        ("ForexLive", "https://www.forexlive.com/feed/"),
        ("DailyFX", "https://www.dailyfx.com/feeds/rss"),
        ("Investing.com", "https://www.investing.com/rss/news.rss"),
        ("Bloomberg", "https://feeds.bloomberg.com/markets/news.rss"),
    ])

    # Finviz URL (scraped)
    finviz_url: str = "https://finviz.com/news.ashx"

    # Economic calendar sources
    forex_factory_url: str = "https://www.forexfactory.com/calendar"
    investing_calendar_url: str = "https://www.investing.com/economic-calendar/"

    # COT report URL (CFTC)
    cot_url: str = "https://www.cftc.gov/dea/futures/deacmxsf.htm"
