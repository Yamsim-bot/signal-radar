"""Sentiment Analysis — multi-source news via RSS, Finviz, VADER scoring."""

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
class SentimentResult:
    overall_score: float                 # -100 (extremely bearish) to +100 (extremely bullish)
    headlines: list[NewsHeadline]
    trending_topics: list[str]
    dovish_count: int
    hawkish_count: int
    risk_on_count: int
    risk_off_count: int
    source_breakdown: dict[str, float]   # per-source average sentiment


# RSS feed sources
RSS_FEEDS = [
    ('ForexLive', 'https://www.forexlive.com/feed/news/'),
    ('ForexLive', 'https://www.forexlive.com/feed/technical-analysis/'),
    ('Investing.com', 'https://www.investing.com/rss/news.rss'),
    ('DailyFX', 'https://www.dailyfx.com/feeds/rss/news'),
    ('Bloomberg', 'https://www.bloomberg.com/feed/podcast/etsy-marketplace.xml'),
]

# Keyword categories for trading sentiment
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


def _generate_sample_headlines() -> list[NewsHeadline]:
    """Generate sample news headlines for development."""
    now = datetime.now(timezone.utc).isoformat()
    samples = [
        ('ForexLive', 'Fed signals patience on rate cuts as inflation remains sticky'),
        ('ForexLive', 'EURUSD extends decline on stronger US data'),
        ('ForexLive', 'Gold hits new all-time high above $2,400 on geopolitical tensions'),
        ('DailyFX', 'GBPUSD technical setup suggests further upside toward 1.30'),
        ('DailyFX', 'BoJ intervention fears cap USDJPY at 152 level'),
        ('DailyFX', 'Crude oil slides on demand concerns, OPEC+ supply outlook'),
        ('Investing.com', 'Wall Street rallies on tech earnings, S&P 500 hits record'),
        ('Investing.com', 'US Dollar Index holds steady ahead of CPI release'),
        ('Investing.com', 'Treasury yields dip as market prices in September rate cut'),
        ('Finviz', 'NFP expectations: 200K job additions forecast for May'),
        ('Finviz', 'AAPL upgrades target on AI product cycle optimism'),
        ('Finviz', 'Bitcoin volatility ahead of halving event'),
        ('Bloomberg', 'China stimulus measures boost commodity demand outlook'),
        ('Bloomberg', 'ECB officials push back against rapid rate cut expectations'),
        ('ForexLive', 'AUDUSD rises on RBA hawkish hold, iron ore rebound'),
    ]
    return [
        NewsHeadline(source=s, title=t, url='', published=now,
                     sentiment_score=0.0, keywords=[], relevance=_calc_relevance(t))
        for s, t in samples
    ]


def analyze() -> SentimentResult:
    """Multi-source sentiment analysis."""
    headlines = _fetch_headlines()
    if not headlines:
        headlines = _generate_sample_headlines()

    # Score each headline with VADER
    scores = _vader_scores([h.title for h in headlines])
    for i, h in enumerate(headlines):
        h.sentiment_score = scores[i] if i < len(scores) else 0.0
        h.keywords = _extract_keywords(h.title)

    # Aggregation
    overall = _aggregate_sentiment(headlines)
    trending = _trending_topics(headlines)
    dovish = sum(1 for h in headlines if any(k in h.title.lower() for k in DOVISH_KEYWORDS))
    hawkish = sum(1 for h in headlines if any(k in h.title.lower() for k in HAWKISH_KEYWORDS))
    risk_on = sum(1 for h in headlines if any(k in h.title.lower() for k in RISK_ON_KEYWORDS))
    risk_off = sum(1 for h in headlines if any(k in h.title.lower() for k in RISK_OFF_KEYWORDS))

    # Source breakdown
    src_brk = {}
    for h in headlines:
        if h.source not in src_brk:
            src_brk[h.source] = []
        src_brk[h.source].append(h.sentiment_score)
    src_avg = {s: round(sum(v)/len(v), 2) for s, v in src_brk.items()}

    return SentimentResult(
        overall_score=round(overall, 1),
        headlines=headlines[:50],  # keep top 50
        trending_topics=trending,
        dovish_count=dovish,
        hawkish_count=hawkish,
        risk_on_count=risk_on,
        risk_off_count=risk_off,
        source_breakdown=src_avg,
    )


def _fetch_headlines() -> list[NewsHeadline]:
    """Fetch news headlines from RSS feeds. Returns empty list on failure."""
    headlines = []
    try:
        import feedparser
        for source, url in RSS_FEEDS:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:10]:
                    published = entry.get('published', '') or entry.get('updated', '') or ''
                    headlines.append(NewsHeadline(
                        source=source,
                        title=entry.get('title', ''),
                        url=entry.get('link', ''),
                        published=published,
                        sentiment_score=0.0,
                        keywords=[],
                        relevance=_calc_relevance(entry.get('title', '')),
                    ))
            except Exception:
                continue
    except Exception:
        pass

    # Also try Finviz news
    try:
        finviz = _fetch_finviz()
        headlines.extend(finviz)
    except Exception:
        pass

    return headlines


def _fetch_finviz() -> list[NewsHeadline]:
    """Scrape Finviz news for broader market sentiment."""
    headlines = []
    try:
        import requests
        from bs4 import BeautifulSoup
        resp = requests.get('https://finviz.com/news.ashx', timeout=5,
                            headers={'User-Agent': 'Mozilla/5.0'})
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            rows = soup.select('tr.nn tr')
            for row in rows[:20]:
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
        # Fallback: simple word-level polarity
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
    trading_keywords = ['forex', 'fx', 'eur', 'usd', 'gbp', 'jpy', 'aud', 'nzd',
                        'cad', 'chf', 'cpi', 'gdp', 'nfp', 'fed', 'ecb', 'boe',
                        'boj', 'rba', 'rbnz', 'stock', 'index', 'commodity',
                        'oil', 'gold', 'bond', 'yield', 'rate', 'inflation',
                        'market', 'trading', 'bull', 'bear', 'rally', 'crash']
    title_lower = title.lower()
    matches = sum(1 for kw in trading_keywords if kw in title_lower)
    # Scale: 3+ keywords = very relevant, 0 = low relevance
    return min(1.0, matches / 5)


def _aggregate_sentiment(headlines: list[NewsHeadline]) -> float:
    """Aggregate individual headline scores into an overall -100 to +100 score."""
    if not headlines:
        return 0.0

    # Weighted average by relevance
    total_weight = 0.0
    weighted_sum = 0.0
    for h in headlines:
        w = h.relevance
        weighted_sum += h.sentiment_score * w
        total_weight += w

    if total_weight == 0:
        return 0.0

    avg = weighted_sum / total_weight
    return float(avg * 100)  # scale to -100 / +100


def _trending_topics(headlines: list[NewsHeadline]) -> list[str]:
    """Identify trending topics by keyword frequency."""
    all_words = []
    for h in headlines:
        all_words.extend(h.keywords)
    freq = Counter(all_words)
    # Return top 10 trending words/phrases
    return [word for word, _ in freq.most_common(10)]
