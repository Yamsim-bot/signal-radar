"""Sentiment Analysis — multi-source news via RSS, Finviz, FXStreet, TradingView,
CME, ForexFactory, OPEC, myFXbook with VADER scoring and cross-reference accuracy."""

from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Optional
from collections import Counter
import re


@dataclass
class NewsHeadline:
    source: str
    title: str
    url: str
    published: str
    sentiment_score: float      # -1.0 (very negative) to +1.0 (very positive)
    keywords: list[str]
    relevance: float            # 0-1 how relevant to trading


@dataclass
class SourceAccuracy:
    """Tracks how reliable a source's sentiment signals have been."""
    source: str
    articles_scraped: int
    avg_relevance: float
    unique_topics: int
    credibility_score: float    # 0-1 based on specificity & relevance


@dataclass
class CrossReferenceEntry:
    topic: str
    source_sentiments: dict[str, float]   # source_name -> sentiment_score
    consensus: str                       # 'bullish', 'bearish', 'neutral', 'mixed'
    agreement_level: float               # 0.0 (total disagreement) to 1.0 (total agreement)
    discrepancy_flag: bool               # True when sources strongly disagree


class SentimentResult:
    """Result of multi-source sentiment analysis."""
    def __init__(self,
                 overall_score: float = 0.0,
                 headlines: list[NewsHeadline] = None,
                 trending_topics: list[str] = None,
                 dovish_count: int = 0,
                 hawkish_count: int = 0,
                 risk_on_count: int = 0,
                 risk_off_count: int = 0,
                 source_breakdown: dict[str, float] = None,
                 cross_references: list[CrossReferenceEntry] = None,
                 source_accuracy: list[SourceAccuracy] = None,
                 ai_analysis: 'Optional[ConsensusResult]' = None):
        self.overall_score = overall_score
        self.headlines = headlines or []
        self.trending_topics = trending_topics or []
        self.dovish_count = dovish_count
        self.hawkish_count = hawkish_count
        self.risk_on_count = risk_on_count
        self.risk_off_count = risk_off_count
        self.source_breakdown = source_breakdown or {}
        self.cross_references = cross_references or []
        self.source_accuracy = source_accuracy or []
        self.ai_analysis = ai_analysis


# ─── RSS / Scraped Sources ──────────────────────────────────────────────

RSS_FEEDS = [
    ('DailyFX', 'https://www.dailyfx.com/feeds/rss/'),
    ('FXStreet', 'https://www.fxstreet.com/rss/news'),
    ('Investing.com', 'https://www.investing.com/rss/news.rss'),
]

# Web-scraped sources (approaches that need requests/bs4)
SCRAPED_SOURCES = {
    'Finviz': 'https://finviz.com/news.ashx',
    'myFXbook': 'https://www.myfxbook.com/community/outlook',
    'TradingView': 'https://www.tradingview.com/news/',
}

# ─── Keyword categories ─────────────────────────────────────────────────

DOVISH_KEYWORDS = [
    'dovish', 'rate cut', 'easing', 'stimulus', 'lower rates',
    'accommodative', 'quantitative easing', 'loose policy',
    'soft landing', 'recession fears', 'slowdown', 'inflation peak',
    'bearish', 'selloff', 'risk-off', 'safe haven', 'decline',
]

HAWKISH_KEYWORDS = [
    'hawkish', 'rate hike', 'tightening', 'taper', 'higher rates',
    'restrictive', 'quantitative tightening', 'firm policy',
    'overheating', 'inflation concern', 'supply chain',
    'bullish', 'rally', 'risk-on', 'growth', 'expansion',
]

RISK_ON_KEYWORDS = [
    'risk-on', 'rally', 'bull market', 'all-time high', 'record high',
    'optimism', 'recovery', 'expansion', 'boom', 'upgrade',
    'outperform', 'buyback', 'dividend increase',
]

RISK_OFF_KEYWORDS = [
    'risk-off', 'crash', 'correction', 'bear market', 'recession',
    'pessimism', 'contraction', 'slowdown', 'default', 'downgrade',
    'volatility', 'uncertainty', 'crisis', 'emergency',
]

# Commodity / Oil keywords
COMMODITY_BULLISH_KEYWORDS = [
    'oil rally', 'crude surge', 'supply cut', 'production cut',
    'opec+ cut', 'supply constraint', 'inventory draw', 'bullish crude',
    'gold rally', 'gold surge', 'precious metals', 'safe haven bid',
]

COMMODITY_BEARISH_KEYWORDS = [
    'oil crash', 'crude drop', 'supply glut', 'oversupply',
    'demand concern', 'economic slowdown', 'inventory build',
    'gold decline', 'silver drop', 'precious metals selloff',
]

# ─── Source credibility weights (0-1) ───────────────────────────────────
# Based on: specificity, timeliness, editorial quality
SOURCE_CREDIBILITY = {
    'DailyFX': 0.95,
    'FXStreet': 0.90,
    'Investing.com': 0.85,
    'Finviz': 0.85,
    'myFXbook': 0.75,
    'TradingView': 0.90,
}


def analyze(quick: bool = False) -> SentimentResult:
    """Multi-source sentiment analysis across all news feeds.

    Args:
        quick: If True, skip live fetching and use sample headlines (instant).
               Use False for the News tab where accuracy matters more than speed.
    """
    if quick:
        # Instant path: use pre-generated sample headlines, no network calls
        headlines = _generate_sample_headlines()
    else:
        # Full path: fetch from live sources (RSS + scrapers, ~15-20s)
        headlines = _fetch_headlines()
        if not headlines:
            headlines = _generate_sample_headlines()

    # Score each headline with VADER + financial lexicon blend
    scores = _blended_sentiment([h.title for h in headlines])
    for i, h in enumerate(headlines):
        h.sentiment_score = scores[i] if i < len(scores) else 0.0
        h.keywords = _extract_keywords(h.title)

    # ── Aggregation ──
    overall = _aggregate_sentiment(headlines)
    trending = _trending_topics(headlines)
    dovish = sum(1 for h in headlines if any(k in h.title.lower() for k in DOVISH_KEYWORDS))
    hawkish = sum(1 for h in headlines if any(k in h.title.lower() for k in HAWKISH_KEYWORDS))
    risk_on = sum(1 for h in headlines if any(k in h.title.lower() for k in RISK_ON_KEYWORDS))
    risk_off = sum(1 for h in headlines if any(k in h.title.lower() for k in RISK_OFF_KEYWORDS))

    # Source breakdown
    src_brk = _build_source_breakdown(headlines)

    # Cross-reference: detect when sources disagree on same topic
    cross_refs = _cross_reference_sources(headlines)

    # Source accuracy / credibility assessment
    src_accuracy = _assess_source_accuracy(headlines)

    return SentimentResult(
        overall_score=round(overall, 1),
        headlines=headlines[:50],
        trending_topics=trending,
        dovish_count=dovish,
        hawkish_count=hawkish,
        risk_on_count=risk_on,
        risk_off_count=risk_off,
        source_breakdown=src_brk,
        cross_references=cross_refs,
        source_accuracy=src_accuracy,
    )


def _fetch_headlines() -> list[NewsHeadline]:
    """Fetch news headlines from ALL sources — RSS + scraped."""
    headlines = []

    # ── RSS feeds (feedparser) with parallel fetching ──
    try:
        import feedparser
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading
        rss_lock = threading.Lock()

        def _parse_rss(source, url):
            """Parse one RSS feed with timeout."""
            try:
                # Set socket timeout so feedparser doesn't hang
                import socket
                old_to = socket.getdefaulttimeout()
                socket.setdefaulttimeout(5)
                try:
                    feed = feedparser.parse(url)
                finally:
                    socket.setdefaulttimeout(old_to)
                local_hl = []
                for entry in feed.entries[:10]:
                    published = (entry.get('published', '')
                                 or entry.get('updated', '') or '')
                    title = entry.get('title', '')
                    if not title:
                        continue
                    local_hl.append(NewsHeadline(
                        source=source,
                        title=title,
                        url=entry.get('link', ''),
                        published=published,
                        sentiment_score=0.0,
                        keywords=[],
                        relevance=_calc_relevance(title),
                    ))
                if local_hl:
                    with rss_lock:
                        headlines.extend(local_hl)
            except Exception:
                pass

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(_parse_rss, source, url) for source, url in RSS_FEEDS]
            for f in as_completed(futures, timeout=15):
                try:
                    f.result()
                except Exception:
                    pass
    except Exception:
        pass

    # ── Finviz + Scraped sources (concurrent, short timeouts) ──
    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading
        scrape_lock = threading.Lock()

        def _scrape_source(name):
            """Run the appropriate scraper for a source name."""
            try:
                scrapers = {
                    'Finviz': _fetch_finviz,
                    'FXStreet': _fetch_fxstreet,
                    'CME Group': _fetch_cme,
                    'ForexFactory': _fetch_forexfactory_news,
                    'OPEC': _fetch_opec,
                    'myFXbook': _fetch_myfxbook,
                    'TradingView': _fetch_tradingview,
                }
                scraper = scrapers.get(name)
                if scraper:
                    result = scraper()
                    if result:
                        with scrape_lock:
                            headlines.extend(result)
            except Exception:
                pass

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_scrape_source, name)
                       for name in ['Finviz', 'FXStreet']]  # fastest first
            for f in as_completed(futures, timeout=15):
                try:
                    f.result()
                except Exception:
                    pass
            # Optional slower sources in background
            futures2 = [pool.submit(_scrape_source, name)
                        for name in ['CME Group', 'ForexFactory', 'OPEC', 'myFXbook']]
            for f in as_completed(futures2, timeout=10):
                try:
                    f.result()
                except Exception:
                    pass
    except Exception:
        pass

    return headlines


# ─── Individual Source Fetchers ─────────────────────────────────────────


def _fetch_finviz() -> list[NewsHeadline]:
    """Scrape Finviz news for broader market sentiment."""
    headlines = []
    try:
        import requests
        from bs4 import BeautifulSoup
        resp = requests.get(
            'https://finviz.com/news.ashx',
            timeout=5,
            headers={
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/125.0.0.0 Safari/537.36'
                ),
            },
        )
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            rows = soup.select('tr.nn tr')
            for row in rows[:25]:
                cells = row.find_all('td')
                if len(cells) >= 2:
                    title = cells[1].get_text(strip=True)
                    if title:
                        headlines.append(NewsHeadline(
                            source='Finviz',
                            title=title,
                            url='https://finviz.com/news.ashx',
                            published=datetime.now(timezone.utc).isoformat(),
                            sentiment_score=0.0,
                            keywords=[],
                            relevance=_calc_relevance(title),
                        ))
    except Exception:
        pass
    return headlines


def _fetch_fxstreet() -> list[NewsHeadline]:
    """Fetch FXStreet forex news (scraped fallback)."""
    headlines = []
    try:
        import requests
        from bs4 import BeautifulSoup
        resp = requests.get(
            'https://www.fxstreet.com/rss/news',
            timeout=5,
            headers={'User-Agent': 'Mozilla/5.0'},
        )
        if resp.status_code == 200:
            import feedparser as _fp
            feed = _fp.parse(resp.text)
            for entry in feed.entries[:15]:
                title = entry.get('title', '')
                if not title:
                    continue
                headlines.append(NewsHeadline(
                    source='FXStreet',
                    title=title,
                    url=entry.get('link', ''),
                    published=entry.get('published', ''),
                    sentiment_score=0.0,
                    keywords=[],
                    relevance=_calc_relevance(title),
                ))
    except Exception:
        pass
    return headlines


def _fetch_cme() -> list[NewsHeadline]:
    """Fetch CME Group market news (futures, rates, commodities)."""
    headlines = []
    urls = [
        'https://www.cmegroup.com/content/cme-group/feeds/market-news/rss.xml',
        'https://www.cmegroup.com/feed/rss/command-center',
    ]
    try:
        import requests
        for url in urls:
            try:
                resp = requests.get(
                    url,
                    timeout=5,
                    headers={'User-Agent': 'Mozilla/5.0'},
                )
                if resp.status_code == 200:
                    import feedparser as _fp
                    feed = _fp.parse(resp.text)
                    for entry in feed.entries[:8]:
                        title = entry.get('title', '')
                        if not title:
                            continue
                        headlines.append(NewsHeadline(
                            source='CME Group',
                            title=title,
                            url=entry.get('link', ''),
                            published=entry.get('published', ''),
                            sentiment_score=0.0,
                            keywords=[],
                            relevance=_calc_relevance(title),
                        ))
                    if headlines:
                        break  # got data, stop trying other URLs
            except Exception:
                continue
    except Exception:
        pass
    return headlines


def _fetch_forexfactory_news() -> list[NewsHeadline]:
    """Scrape ForexFactory news headlines."""
    headlines = []
    try:
        import requests
        from bs4 import BeautifulSoup
        resp = requests.get(
            'https://www.forexfactory.com/',
            timeout=5,
            headers={
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/125.0.0.0 Safari/537.36'
                ),
            },
        )
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            # Look for news items — Forex Factory uses various structures
            for tag in soup.select('a'):
                text = tag.get_text(strip=True)
                if text and len(text) > 30 and any(
                    kw in text.lower() for kw in ['forex', 'eur', 'usd', 'gbp',
                                                  'jpy', 'gold', 'oil', 'cpi',
                                                  'nfp', 'fed', 'ecb', 'boe']
                ):
                    headlines.append(NewsHeadline(
                        source='ForexFactory',
                        title=text,
                        url=tag.get('href', ''),
                        published=datetime.now(timezone.utc).isoformat(),
                        sentiment_score=0.0,
                        keywords=[],
                        relevance=_calc_relevance(text),
                    ))
    except Exception:
        pass
    return headlines


def _fetch_opec() -> list[NewsHeadline]:
    """Scrape OPEC press releases for oil market signals."""
    headlines = []
    urls = [
        'https://www.opec.org/opec_web/en/press_releases/',
        'https://www.opec.org/opec_web/en/rss.xml',
    ]
    try:
        import requests
        from bs4 import BeautifulSoup
        for url in urls:
            try:
                resp = requests.get(
                    url,
                    timeout=5,
                    headers={'User-Agent': 'Mozilla/5.0'},
                )
                if resp.status_code == 200:
                    # Try RSS feed first
                    if 'xml' in resp.headers.get('Content-Type', ''):
                        import feedparser as _fp
                        feed = _fp.parse(resp.text)
                        for entry in feed.entries[:10]:
                            title = entry.get('title', '')
                            if not title:
                                continue
                            headlines.append(NewsHeadline(
                                source='OPEC',
                                title=title,
                                url=entry.get('link', ''),
                                published=entry.get('published', ''),
                                sentiment_score=0.0,
                                keywords=[],
                                relevance=_calc_relevance(title),
                            ))
                    else:
                        # HTML page — scrape press release list
                        soup = BeautifulSoup(resp.text, 'html.parser')
                        for link in soup.select('a[href*="press_release"]'):
                            text = link.get_text(strip=True)
                            if text and len(text) > 10:
                                headlines.append(NewsHeadline(
                                    source='OPEC',
                                    title=text,
                                    url=url.rstrip('/') + '/' + link.get('href', '').lstrip('/'),
                                    published=datetime.now(timezone.utc).isoformat(),
                                    sentiment_score=0.0,
                                    keywords=[],
                                    relevance=_calc_relevance(text),
                                ))
                    if headlines:
                        break
            except Exception:
                continue
    except Exception:
        pass
    return headlines


def _fetch_myfxbook() -> list[NewsHeadline]:
    """Scrape myFXbook community outlook / analysis."""
    headlines = []
    urls = [
        'https://www.myfxbook.com/community/outlook',
        'https://www.myfxbook.com/community/news',
        'https://blog.myfxbook.com/feed/',
    ]
    try:
        import requests
        from bs4 import BeautifulSoup
        for url in urls:
            try:
                resp = requests.get(
                    url,
                    timeout=5,
                    headers={'User-Agent': 'Mozilla/5.0'},
                )
                if resp.status_code == 200:
                    # Try RSS/XML
                    ct = resp.headers.get('Content-Type', '')
                    if 'xml' in ct or 'rss' in ct or 'atom' in ct:
                        import feedparser as _fp
                        feed = _fp.parse(resp.text)
                        for entry in feed.entries[:10]:
                            title = entry.get('title', '')
                            if not title:
                                continue
                            headlines.append(NewsHeadline(
                                source='myFXbook',
                                title=title,
                                url=entry.get('link', ''),
                                published=entry.get('published', ''),
                                sentiment_score=0.0,
                                keywords=[],
                                relevance=_calc_relevance(title),
                            ))
                    else:
                        soup = BeautifulSoup(resp.text, 'html.parser')
                        # Try common article containers
                        for sel in ['h2 a', 'h3 a', '.article-title a',
                                    '.outlook-item a', '.news-item a']:
                            for link in soup.select(sel):
                                text = link.get_text(strip=True)
                                if text and len(text) > 15:
                                    headlines.append(NewsHeadline(
                                        source='myFXbook',
                                        title=text,
                                        url=link.get('href', ''),
                                        published=datetime.now(timezone.utc).isoformat(),
                                        sentiment_score=0.0,
                                        keywords=[],
                                        relevance=_calc_relevance(text),
                                    ))
                            if headlines:
                                break
                    if headlines:
                        break
            except Exception:
                continue
    except Exception:
        pass
    return headlines


def _fetch_tradingview() -> list[NewsHeadline]:
    """Scrape TradingView news page for market headlines."""
    headlines = []
    try:
        import requests
        from bs4 import BeautifulSoup
        import re

        resp = requests.get(
            'https://www.tradingview.com/news/',
            timeout=8,
            headers={
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/125.0.0.0 Safari/537.36'
                ),
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
            },
        )
        if resp.status_code != 200:
            return headlines

        soup = BeautifulSoup(resp.text, 'html.parser')

        # Try multiple selector strategies
        found = []

        # Strategy 1: TV news card items
        for item in soup.select('[class*="news"] [class*="item"], '
                                '[class*="card"] a, '
                                '.tv-widget-news__item a, '
                                'a[class*="news"]'):
            text = item.get_text(strip=True)
            href = item.get('href', '')
            if text and len(text) > 15 and not text.startswith('http'):
                found.append((text, href))

        # Strategy 2: Look for JSON-LD structured data
        if not found:
            for script in soup.select('script[type="application/ld+json"]'):
                try:
                    import json as _json
                    data = _json.loads(script.string or '{}')
                    articles = data if isinstance(data, list) else data.get('itemListElement', [])
                    for art in articles:
                        a = art if isinstance(art, dict) else {}
                        title = a.get('name', a.get('headline', ''))
                        if title and len(str(title)) > 15:
                            found.append((str(title), a.get('url', '')))
                except Exception:
                    continue

        # Strategy 3: Extract from raw HTML using regex for TV headlines
        if not found:
            for match in re.finditer(
                r'<[^>]*class="[^"]*tv-widget-headline[^"]*"[^>]*>([^<]+)</',
                resp.text
            ):
                text = match.group(1).strip()
                if text and len(text) > 15:
                    found.append((text, ''))

        # Strategy 4: Article headline links
        if not found:
            for tag in soup.select('article a, [class*="headline"] a, h2 a, h3 a'):
                text = tag.get_text(strip=True)
                href = tag.get('href', '')
                if text and len(text) > 15:
                    found.append((text, href))

        # Deduplicate by title
        seen = set()
        for title, url in found:
            norm = title.lower().strip()
            if norm in seen or len(norm) < 20:
                continue
            seen.add(norm)

            # Build absolute URL if relative
            if url and not url.startswith('http'):
                url = 'https://www.tradingview.com' + url

            headlines.append(NewsHeadline(
                source='TradingView',
                title=title,
                url=url,
                published=datetime.now(timezone.utc).isoformat(),
                sentiment_score=0.0,
                keywords=[],
                relevance=_calc_relevance(title),
            ))
            if len(headlines) >= 10:
                break

    except Exception:
        pass
    return headlines


# ─── Financial Sentiment Scoring ─────────────────────────────────────────
#
# VADER alone doesn't understand financial language ("dovish", "hawkish",
# "rate hold", "inflation tick up"). We layer a financial-market lexicon
# on top and blend the two scores.
#
# Each phrase/word has a (direction, weight) pair. Direction = +1 (bullish
# for the asset/category) or -1 (bearish). Weight = strength of the signal.
# We also track which asset class the term relates to.

FINANCIAL_BULLISH = {
    # Central bank dovish = bullish for risk assets
    'dovish': +0.6, 'dovish hold': +0.7, 'dovish pause': +0.7,
    'rate cut': +0.8, 'rate cuts': +0.8, 'cut rates': +0.8,
    'easing': +0.6, 'loosening': +0.5, 'accommodative': +0.6,
    'stimulus': +0.7, 'expansionary': +0.5,
    'lower rates': +0.7, 'lower interest rates': +0.7,
    'hold steady': +0.2, 'on hold': +0.1, 'steady': +0.1,  # neutral-positive
    # Economic strength = bullish for currency
    'growth': +0.4, 'gdp beat': +0.6, 'gdp surprise': +0.6,
    'expansion': +0.5, 'rebound': +0.5, 'recovery': +0.5,
    'boom': +0.6, 'surge': +0.5, 'rally': +0.6, 'breakout': +0.6,
    # Employment
    'jobs growth': +0.5, 'payrolls beat': +0.6, 'unemployment falls': +0.5,
    'claims fall': +0.4, 'hiring': +0.4, 'wage growth': +0.4,
    # Market momentum
    'all-time high': +0.6, 'record high': +0.6, 'new high': +0.4,
    'bullish': +0.7, 'bull market': +0.7, 'bull run': +0.6,
    'outperform': +0.5, 'upgrade': +0.4, 'buy signal': +0.6,
    'positive': +0.3, 'strong': +0.3, 'higher': +0.3,
    'upside': +0.5, 'momentum': +0.3,
    # Commodities
    'supply crunch': +0.4, 'supply tight': +0.4,
    'production cut': +0.5, 'output cut': +0.4,
    # Specific instruments
    'risk on': +0.5, 'risk-on': +0.5,
    # Earnings
    'beat earnings': +0.5, 'profit beat': +0.5, 'revenue beat': +0.4,
    # Gold specific
    'safe haven': +0.5, 'flight to safety': +0.5,
    # Technical
    'golden cross': +0.5, 'breakout above': +0.5,
}

FINANCIAL_BEARISH = {
    # Central bank hawkish = bearish for risk assets
    'hawkish': -0.6, 'hawkish hold': -0.7, 'hawkish pause': -0.7,
    'rate hike': -0.8, 'rate hikes': -0.8, 'hike rates': -0.8,
    'tightening': -0.6, 'tighten': -0.5,
    'higher rates': -0.7, 'higher interest rates': -0.7,
    # Inflation
    'inflation': -0.4, 'inflation rises': -0.6, 'inflation ticks up': -0.6,
    'inflation sticky': -0.5, 'sticky inflation': -0.5,
    'cpi beat': -0.5, 'cpi surprise up': -0.6,
    # Economic weakness = bearish
    'recession': -0.8, 'recession fears': -0.8, 'recessionary': -0.7,
    'contraction': -0.6, 'slowdown': -0.5, 'slump': -0.5,
    'decline': -0.4, 'downside': -0.5, 'stagnation': -0.5,
    # Employment
    'jobs miss': -0.5, 'payrolls miss': -0.6, 'unemployment rises': -0.5,
    'claims spike': -0.4, 'layoffs': -0.5, 'firing': -0.4,
    'wage stagnation': -0.4,
    # Market weakness
    'bearish': -0.7, 'bear market': -0.7, 'bear run': -0.6,
    'crash': -0.9, 'plunge': -0.7, 'tumble': -0.6, 'slide': -0.4,
    'sell-off': -0.6, 'selloff': -0.6, 'dump': -0.5,
    'correction': -0.5, 'downgrade': -0.5, 'underperform': -0.5,
    'sell signal': -0.6, 'resistance': -0.2,
    'negative': -0.3, 'weak': -0.3, 'lower': -0.3,
    'worst': -0.4, 'loss': -0.4, 'drop': -0.4, 'fall': -0.3,
    'downside risk': -0.6,
    # Geopolitical / risk-off
    'risk off': -0.5, 'risk-off': -0.5, 'uncertainty': -0.4,
    'geopolitical': -0.4, 'tensions': -0.4, 'sanctions': -0.5,
    'trade war': -0.6, 'tariffs': -0.5, 'default': -0.7,
    'bankrupt': -0.8, 'insolvent': -0.7, 'bailout': -0.5,
    # Energy
    'demand concerns': -0.4, 'demand weak': -0.4, 'oversupply': -0.5,
    'supply glut': -0.5,
    # Technical
    'death cross': -0.6, 'breakdown': -0.5, 'break below': -0.4,
    'cap upside': -0.3, 'resistance holds': -0.3,
}


def _financial_sentiment(text: str) -> float:
    """Score a headline using the financial-market lexicon."""
    text_lower = text.lower()
    score = 0.0
    count = 0

    # Multi-word phrases (check first)
    for phrase, weight in {**FINANCIAL_BULLISH, **FINANCIAL_BEARISH}.items():
        if ' ' in phrase and phrase in text_lower:
            score += weight
            count += 1

    # Single-word matches (avoid double-counting phrases already matched)
    words = text_lower.split()
    for word in words:
        word = word.strip('.,!?()[]{}"\':;')
        if word in FINANCIAL_BULLISH and word not in [w for w in FINANCIAL_BULLISH if ' ' in w]:
            score += FINANCIAL_BULLISH[word]
            count += 1
        elif word in FINANCIAL_BEARISH and word not in [w for w in FINANCIAL_BEARISH if ' ' in w]:
            score += FINANCIAL_BEARISH[word]
            count += 1

    if count == 0:
        return 0.0
    # Normalise to roughly [-1, 1] and clamp
    avg = score / max(count, 1)
    return max(-1.0, min(1.0, avg * 1.5))  # scale up to saturate


def _blended_sentiment(titles: list[str]) -> list[float]:
    """Score headlines using VADER + financial lexicon blend.

    VADER handles general language. The financial lexicon catches
    domain-specific terms VADER would score as neutral.
    Final score = 0.4 * VADER + 0.6 * financial
    """
    vader_scores = _vader_scores(titles)
    blended = []
    for i, t in enumerate(titles):
        fin = _financial_sentiment(t)
        # Blend: VADER for general, financial for domain
        # Use whichever has stronger signal (further from zero)
        if abs(fin) > abs(vader_scores[i]) * 2:
            blended.append(fin)
        elif abs(vader_scores[i]) > abs(fin) * 2:
            blended.append(vader_scores[i])
        else:
            blended.append(0.4 * vader_scores[i] + 0.6 * fin)
    return blended


def _vader_scores(titles: list[str]) -> list[float]:
    """Score headlines using VADER sentiment. Falls back to simple polarity."""
    try:
        from nltk.sentiment import SentimentIntensityAnalyzer
        try:
            sia = SentimentIntensityAnalyzer()
        except LookupError:
            import nltk
            nltk.download('vader_lexicon', quiet=True)
            sia = SentimentIntensityAnalyzer()

        scores = []
        for t in titles:
            vs = sia.polarity_scores(t)
            scores.append(vs['compound'])
        return scores
    except Exception:
        return [_simple_polarity(t) for t in titles]


def _simple_polarity(text: str) -> float:
    """Simple polarity fallback when VADER unavailable."""
    positive = ['up', 'gain', 'rise', 'rally', 'bullish', 'strong', 'growth',
                'positive', 'surge', 'breakout', 'higher', 'upgrade', 'outperform']
    negative = ['down', 'loss', 'fall', 'decline', 'bearish', 'weak', 'slump',
                'negative', 'drop', 'crash', 'lower', 'downgrade', 'underperform']
    text_lower = text.lower()
    pos_count = sum(1 for w in positive if w in text_lower)
    neg_count = sum(1 for w in negative if w in text_lower)
    total = pos_count + neg_count
    if total == 0:
        return 0.0
    return (pos_count - neg_count) / total


# ─── Keyword / Relevance ────────────────────────────────────────────────


def _extract_keywords(text: str) -> list[str]:
    """Extract trading-relevant keywords from headline."""
    keywords = []
    text_lower = text.lower()
    for word in text_lower.split():
        word = word.strip('.,!?()[]{}"\':;')
        if len(word) > 3 and word not in ('the', 'this', 'that', 'with', 'from',
                                            'have', 'been', 'will', 'were', 'what',
                                            'when', 'where', 'which', 'their'):
            keywords.append(word)
    return keywords[:10]


def _calc_relevance(title: str) -> float:
    """Estimate relevance of a headline to trading (0-1)."""
    trading_keywords = [
        'forex', 'fx', 'eur', 'usd', 'gbp', 'jpy', 'aud', 'nzd',
        'cad', 'chf', 'cpi', 'gdp', 'nfp', 'fed', 'ecb', 'boe',
        'boj', 'rba', 'rbnz', 'stock', 'index', 'commodity',
        'oil', 'gold', 'bond', 'yield', 'rate', 'inflation',
        'market', 'trading', 'bull', 'bear', 'rally', 'crash',
        'opec', 'cme', 'futures', 'fomc', 'treasury', 'spx',
        # FXStreet / TradingView specific
        'technical', 'analysis', 'fibonacci', 'support', 'resistance',
        'breakout', 'retracement', 'divergence', 'candle', 'pattern',
        'entry', 'target', 'stop loss', 'take profit', 'signal',
    ]
    title_lower = title.lower()
    matches = sum(1 for kw in trading_keywords if kw in title_lower)
    return min(1.0, matches / 5)


def _build_source_breakdown(headlines: list[NewsHeadline]) -> dict[str, float]:
    """Compute per-source average sentiment."""
    src_brk = {}
    for h in headlines:
        if h.source not in src_brk:
            src_brk[h.source] = []
        src_brk[h.source].append(h.sentiment_score)
    return {s: round(sum(v) / len(v), 2) for s, v in src_brk.items()}


# ─── Cross-Reference Logic ──────────────────────────────────────────────


def _cross_reference_sources(headlines: list[NewsHeadline]) -> list[CrossReferenceEntry]:
    """Detect same topics across multiple sources and flag discrepancies.

    Groups headlines by topic (common keywords), compares sentiment
    per source, and flags when sources strongly disagree.
    """
    if not headlines:
        return []

    # Build keyword → list of (source, sentiment) mappings
    topic_map: dict[str, list[tuple[str, float]]] = {}
    for h in headlines:
        for kw in h.keywords:
            kw_lower = kw.lower()
            if kw_lower not in topic_map:
                topic_map[kw_lower] = []
            topic_map[kw_lower].append((h.source, h.sentiment_score))

    # Only care about topics mentioned by 2+ different sources
    entries = []
    for topic, pairs in topic_map.items():
        unique_sources = set(s for s, _ in pairs)
        if len(unique_sources) < 2:
            continue

        # Per-source average sentiment for this topic
        src_sent: dict[str, float] = {}
        for s, score in pairs:
            if s not in src_sent:
                src_sent[s] = []
            src_sent[s].append(score)
        src_avg = {s: sum(v) / len(v) for s, v in src_sent.items()}

        # Consensus: bullish, bearish, or mixed
        vals = list(src_avg.values())
        pos = sum(1 for v in vals if v > 0.1)
        neg = sum(1 for v in vals if v < -0.1)
        neu = sum(1 for v in vals if -0.1 <= v <= 0.1)

        if pos > neg and pos >= len(vals) * 0.6:
            consensus = 'bullish'
        elif neg > pos and neg >= len(vals) * 0.6:
            consensus = 'bearish'
        elif neu >= len(vals) * 0.6:
            consensus = 'neutral'
        else:
            consensus = 'mixed'

        # Agreement level: standard deviation of sentiments (lower = more agreement)
        if len(vals) >= 2:
            mean_v = sum(vals) / len(vals)
            variance = sum((v - mean_v) ** 2 for v in vals) / len(vals)
            std_dev = variance ** 0.5
            agreement = max(0.0, min(1.0, 1.0 - std_dev))
        else:
            agreement = 1.0

        # Flag discrepancy: two or more sources disagree in sign
        pos_sources = sum(1 for v in vals if v > 0.1)
        neg_sources = sum(1 for v in vals if v < -0.1)
        discrepancy = (pos_sources >= 1 and neg_sources >= 1)

        if discrepancy or agreement < 0.5:
            entries.append(CrossReferenceEntry(
                topic=topic,
                source_sentiments=src_avg,
                consensus=consensus,
                agreement_level=round(agreement, 2),
                discrepancy_flag=discrepancy,
            ))

    # Return top 15 most interesting discrepancies first
    entries.sort(key=lambda e: (-e.discrepancy_flag, e.agreement_level))
    return entries[:15]


def _assess_source_accuracy(headlines: list[NewsHeadline]) -> list[SourceAccuracy]:
    """Assess source quality metrics based on scraped data."""
    source_data: dict[str, dict] = {}

    for h in headlines:
        if h.source not in source_data:
            source_data[h.source] = {
                'articles': 0,
                'relevance_sum': 0.0,
                'topics': set(),
            }
        source_data[h.source]['articles'] += 1
        source_data[h.source]['relevance_sum'] += h.relevance
        source_data[h.source]['topics'].update(h.keywords)

    results = []
    for source, data in source_data.items():
        n = data['articles']
        avg_rel = data['relevance_sum'] / n if n > 0 else 0.0
        n_unique = len(data['topics'])

        # Credibility = weighted blend of source credibility + observed relevance
        base_cred = SOURCE_CREDIBILITY.get(source, 0.5)
        relevance_bonus = avg_rel * 0.15  # +0.15 if all headlines are relevant
        credibility = min(1.0, base_cred + relevance_bonus)

        results.append(SourceAccuracy(
            source=source,
            articles_scraped=n,
            avg_relevance=round(avg_rel, 2),
            unique_topics=n_unique,
            credibility_score=round(credibility, 2),
        ))

    results.sort(key=lambda r: -r.credibility_score)
    return results


# ─── Aggregation ────────────────────────────────────────────────────────


def _aggregate_sentiment(headlines: list[NewsHeadline]) -> float:
    """Aggregate individual headline scores into an overall -100 to +100 score."""
    if not headlines:
        return 0.0

    total_weight = 0.0
    weighted_sum = 0.0
    for h in headlines:
        # Blend: relevance + source credibility as weight
        base_weight = SOURCE_CREDIBILITY.get(h.source, 0.5)
        w = h.relevance * base_weight
        weighted_sum += h.sentiment_score * w
        total_weight += w

    if total_weight == 0:
        return 0.0

    avg = weighted_sum / total_weight
    return float(avg * 100)


def _trending_topics(headlines: list[NewsHeadline]) -> list[str]:
    """Identify trending topics by keyword frequency."""
    all_words = []
    for h in headlines:
        all_words.extend(h.keywords)
    freq = Counter(all_words)
    return [word for word, _ in freq.most_common(10)]


# ─── Sample Data ────────────────────────────────────────────────────────


def _generate_sample_headlines() -> list[NewsHeadline]:
    """Generate sample news headlines with multi-source coverage of trusted sources."""
    now = datetime.now(timezone.utc).isoformat()
    samples = [
        # DailyFX — trusted forex news & analysis (replaces ForexLive)
        ('DailyFX', 'Fed signals patience on rate cuts as inflation remains sticky'),
        ('DailyFX', 'EURUSD extends decline on stronger US data'),
        ('DailyFX', 'Gold holds near record highs on geopolitical tensions'),
        ('DailyFX', 'AUDUSD rises on RBA hawkish hold, iron ore rebound'),
        ('DailyFX', 'USDJPY tests key resistance as BoJ holds steady'),
        ('DailyFX', 'GBPUSD steady ahead of UK GDP data'),
        ('DailyFX', 'Crude oil extends losses on demand concerns'),
        # FXStreet — trusted forex technical/fundamental analysis
        ('FXStreet', 'EURUSD technical: key support at 1.1050 holds, bounce expected'),
        ('FXStreet', 'GBPUSD finds resistance at 1.2900 ahead of BOE testimony'),
        ('FXStreet', 'Gold technical analysis: bulls eye $2,420 breakout'),
        ('FXStreet', 'USDJPY technical: BoJ intervention risks cap upside'),
        ('FXStreet', 'AUDUSD: RBA minutes support further upside'),
        ('FXStreet', 'NZDUSD bears target 0.6200 on weak NZ data'),
        ('FXStreet', 'GBPJPY bullish flag pattern suggests breakout to 194'),
        ('FXStreet', 'Copper prices extend rally on China stimulus hopes'),
        # Investing.com — trusted economic news
        ('Investing.com', 'Wall Street rallies on tech earnings, S&P 500 holds near highs'),
        ('Investing.com', 'US Dollar Index holds steady ahead of CPI release'),
        ('Investing.com', 'Treasury yields dip as market prices in September rate cut'),
        ('Investing.com', 'Fed officials push back on rapid rate cut expectations'),
        ('Investing.com', 'European equities mixed amid growth concerns'),
        # Finviz — trusted market data
        ('Finviz', 'NFP expectations signal steady labor market'),
        ('Finviz', 'AAPL upgrades target on AI product cycle optimism'),
        ('Finviz', 'Crude oil supply concerns persist on geopolitical risks'),
        ('Finviz', 'SP500 momentum remains positive on rate cut hopes'),
        # myFXbook — community outlook data
        ('myFXbook', 'EURUSD retail positioning shifts bullish above 1.1200'),
        ('myFXbook', 'GBPUSD retail traders heavily short near 1.2900 resistance'),
        ('myFXbook', 'Gold longs dominate myFXbook community outlook'),
        # TradingView — professional chart analysis & market coverage
        ('TradingView', 'Nasdaq 100 breaks resistance as tech leads market rally'),
        ('TradingView', 'Brent crude consolidates above $85 as OPEC maintains outlook'),
        ('TradingView', 'GBPUSD: key technical levels ahead of BOE decision'),
        ('TradingView', 'S&P 500 extends winning streak on rate cut optimism'),
    ]
    return [
        NewsHeadline(source=s, title=t, url='', published=now,
                     sentiment_score=0.0, keywords=[], relevance=_calc_relevance(t))
        for s, t in samples
    ]


# ─── AI Enhancement ───────────────────────────────────────────────────────
#
# Optional: replace VADER keyword scoring with multi-LLM consensus
# (Claude + Gemini + DeepSeek + Grok) for much smarter analysis.
# Falls back gracefully if no API keys are configured.


def enhance_with_ai(sent_result: SentimentResult,
                    live_prices: Optional[dict] = None,
                    calendar_events: Optional[list] = None,
                    cfg: 'Optional[Config]' = None) -> SentimentResult:
    """Enhance a SentimentResult with multi-LLM consensus analysis.

    Calls all available AI models in parallel with the collected headlines,
    prices, and calendar context. The AI consensus score replaces the VADER-
    derived overall_score when successful.

    Args:
        sent_result: Existing SentimentResult from analyze()
        live_prices: Dict of {symbol: price} from fetch_live_prices
        calendar_events: List of CalendarEvent from eco_calendar.analyze
        cfg: Config object (to check use_ai_sentiment flag)

    Returns:
        The same SentimentResult (mutated in-place) with AI enhancements attached.
        If AI is unavailable or disabled, returns unchanged.
    """
    # Check if AI is enabled in config
    if cfg is not None and not getattr(cfg, 'use_ai_sentiment', True):
        return sent_result

    # Lazy import — AI analyst is optional
    try:
        from .ai_analyst import run_ai_consensus, ConsensusResult
    except ImportError:
        return sent_result  # ai_analyst module not available

    if live_prices is None:
        live_prices = {}
    if calendar_events is None:
        calendar_events = []

    # Need at least some headlines to analyse
    if not sent_result.headlines:
        return sent_result

    consensus = run_ai_consensus(
        headlines=sent_result.headlines,
        live_prices=live_prices,
        calendar_events=calendar_events,
    )

    if consensus is not None:
        # Override the VADER score with AI consensus
        sent_result.overall_score = consensus.overall_score
        sent_result.ai_analysis = consensus

        # Also update the risk/dovish/hawkish counts from AI output
        if consensus.risk_appetite == 'risk_on':
            sent_result.risk_on_count = max(sent_result.risk_on_count, 3)
        elif consensus.risk_appetite == 'risk_off':
            sent_result.risk_off_count = max(sent_result.risk_off_count, 3)

    return sent_result
