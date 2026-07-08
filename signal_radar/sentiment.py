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
                 source_accuracy: list[SourceAccuracy] = None):
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


# ─── RSS / Scraped Sources ──────────────────────────────────────────────

RSS_FEEDS = [
    ('ForexLive', 'https://www.forexlive.com/feed/news/'),
    ('FXStreet', 'https://www.fxstreet.com/rss/news'),
    ('Investing.com', 'https://www.investing.com/rss/news.rss'),
]

# Web-scraped sources (approaches that need requests/bs4)
SCRAPED_SOURCES = {
    'Finviz': 'https://finviz.com/news.ashx',
    'myFXbook': 'https://www.myfxbook.com/community/outlook',
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
    'ForexLive': 0.90,
    'FXStreet': 0.90,
    'Investing.com': 0.85,
    'Finviz': 0.85,
    'myFXbook': 0.75,
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

    # Score each headline with VADER
    scores = _vader_scores([h.title for h in headlines])
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


# ─── VADER Scoring ──────────────────────────────────────────────────────


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
        # ForexLive — trusted forex news
        ('ForexLive', 'Fed signals patience on rate cuts as inflation remains sticky'),
        ('ForexLive', 'EURUSD extends decline on stronger US data'),
        ('ForexLive', 'Gold hits new all-time high above $2,400 on geopolitical tensions'),
        ('ForexLive', 'AUDUSD rises on RBA hawkish hold, iron ore rebound'),
        ('ForexLive', 'USDJPY tests 152 as BoJ holds steady'),
        ('ForexLive', 'GBPUSD steady ahead of UK GDP data'),
        ('ForexLive', 'Crude oil extends losses on demand concerns'),
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
    ]
    return [
        NewsHeadline(source=s, title=t, url='', published=now,
                     sentiment_score=0.0, keywords=[], relevance=_calc_relevance(t))
        for s, t in samples
    ]
