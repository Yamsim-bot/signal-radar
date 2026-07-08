"""Market Confluence — external technical/fundamental data from trusted sources.

Fetches live trading sentiment, pivot points, and technical signals from:
  - myFXbook: community long/short ratio per forex pair
  - FXStreet: sentiment polling + pivot points
  - Finviz: technical signal summary (stocks/indices)

All fetchers are non-blocking (ThreadPoolExecutor), have short timeouts,
and gracefully fall back to None on failure — they never block the radar scan.
"""

from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional
import threading

from .instruments import INSTRUMENTS

# ─── Public Data Structures ───────────────────────────────────────────────


@dataclass
class MyfxbookOutlook:
    """Retail trader positioning from myFXbook community."""
    symbol: str
    long_pct: float          # 0-100 %
    short_pct: float         # 0-100 %
    total_positions: int
    signal: str              # 'bullish', 'bearish', 'neutral' (contrarian)
    signal_strength: int     # 0-5, how extreme the positioning is


@dataclass
class FxstreetSentiment:
    """FXStreet trader sentiment poll."""
    symbol: str
    long_pct: float
    short_pct: float
    signal: str
    signal_strength: int


@dataclass
class FxstreetPivot:
    """Pivot point levels for a symbol from FXStreet."""
    symbol: str
    pivot: float
    r1: float
    r2: float
    r3: float
    s1: float
    s2: float
    s3: float


@dataclass
class FinvizTechnicals:
    """Technical signal summary from Finviz (stocks/indices)."""
    symbol: str
    signals: dict[str, str]   # e.g. {'RSI': 'Bullish', 'MACD': 'Bearish'}
    bullish_count: int
    bearish_count: int
    overall: str              # 'bullish', 'bearish', 'neutral'


@dataclass
class ConfluenceResult:
    """Per-symbol confluence data from all external sources."""
    symbol: str
    myfxbook: Optional[MyfxbookOutlook] = None
    fxstreet_sentiment: Optional[FxstreetSentiment] = None
    fxstreet_pivot: Optional[FxstreetPivot] = None
    finviz: Optional[FinvizTechnicals] = None

    def contrarian_score(self) -> float:
        """Combine retail sentiment into a -100 to +100 score.

        Extreme retail positioning → stronger contrarian signal.
        myFXbook + FXStreet sentiment averaged.
        """
        scores = []
        for src in [self.myfxbook, self.fxstreet_sentiment]:
            if src is not None:
                if src.signal == 'bullish':
                    scores.append(-src.signal_strength * 12)  # -12 to -60
                elif src.signal == 'bearish':
                    scores.append(+src.signal_strength * 12)  # +12 to +60
        if scores:
            return sum(scores) / len(scores)
        return 0.0

    def pivot_score(self, price: float) -> float:
        """Score based on price relative to pivot levels.

        Price near R1/R2 = bullish pressure, near S1/S2 = bearish.
        Returns -100 to +100.
        """
        pivot = self.fxstreet_pivot
        if pivot is None or pivot.pivot == 0:
            return 0.0
        # How far price is from pivot as % of R1-S1 range
        spread = max(pivot.r1 - pivot.s1, 0.0001)
        pos = (price - pivot.pivot) / spread * 100  # -50 to +50 normal
        return max(-50, min(50, pos))


# ─── Symbol mapping ───────────────────────────────────────────────────────

# Map radar symbols to myFXbook instrument names
MYFXBOOK_SYMBOLS = {
    # Forex majors
    'EURUSD': 'EURUSD', 'GBPUSD': 'GBPUSD', 'USDJPY': 'USDJPY',
    'USDCHF': 'USDCHF', 'USDCAD': 'USDCAD', 'AUDUSD': 'AUDUSD',
    'NZDUSD': 'NZDUSD',
    # Forex crosses
    'EURGBP': 'EURGBP', 'EURJPY': 'EURJPY', 'EURCHF': 'EURCHF',
    'EURAUD': 'EURAUD', 'EURNZD': 'EURNZD', 'EURCAD': 'EURCAD',
    'GBPJPY': 'GBPJPY', 'GBPCHF': 'GBPCHF', 'GBPAUD': 'GBPAUD',
    'GBPNZD': 'GBPNZD', 'GBPCAD': 'GBPCAD',
    'AUDJPY': 'AUDJPY', 'AUDCHF': 'AUDCHF', 'AUDCAD': 'AUDCAD',
    'AUDNZD': 'AUDNZD',
    'NZDJPY': 'NZDJPY', 'NZDCHF': 'NZDCHF', 'NZDCAD': 'NZDCAD',
    'CADJPY': 'CADJPY', 'CADCHF': 'CADCHF',
    'CHFJPY': 'CHFJPY',
    # Crypto
    'BTCUSD': 'BTCUSD', 'ETHUSD': 'ETHUSD',
    # Indices
    'SP500': 'SP500', 'NAS100': 'NAS100', 'DJ30': 'DJ30',
    'DAX40': 'DAX40', 'FTSE100': 'FTSE100',
    # Commodities
    'XAUUSD': 'XAUUSD', 'XAGUSD': 'XAGUSD', 'USOIL': 'USOIL',
    'UKOIL': 'UKOIL',
}

# FXStreet URL path mapping
FXSTREET_SYMBOLS = {
    'EURUSD': 'eur-usd', 'GBPUSD': 'gbp-usd', 'USDJPY': 'usd-jpy',
    'USDCHF': 'usd-chf', 'USDCAD': 'usd-cad', 'AUDUSD': 'aud-usd',
    'NZDUSD': 'nzd-usd',
    'EURGBP': 'eur-gbp', 'EURJPY': 'eur-jpy', 'EURCHF': 'eur-chf',
    'EURAUD': 'eur-aud', 'EURNZD': 'eur-nzd', 'EURCAD': 'eur-cad',
    'GBPJPY': 'gbp-jpy', 'GBPCHF': 'gbp-chf', 'GBPAUD': 'gbp-aud',
    'GBPNZD': 'gbp-nzd', 'GBPCAD': 'gbp-cad',
    'AUDJPY': 'aud-jpy', 'AUDCHF': 'aud-chf', 'AUDCAD': 'aud-cad',
    'AUDNZD': 'aud-nzd',
    'NZDJPY': 'nzd-jpy', 'NZDCHF': 'nzd-chf', 'NZDCAD': 'nzd-cad',
    'CADJPY': 'cad-jpy', 'CADCHF': 'cad-chf', 'CHFJPY': 'chf-jpy',
    'XAUUSD': 'xau-usd', 'XAGUSD': 'xag-usd',
    'BTCUSD': 'btc-usd', 'ETHUSD': 'eth-usd',
}


# ─── Fetchers ─────────────────────────────────────────────────────────────

_fetch_lock = threading.Lock()
_confluence_cache: dict[str, dict[str, ConfluenceResult]] = {}
_cache_time: float = 0
_CACHE_TTL = 60.0  # seconds — short enough for fresh data, long enough for scan speed


def fetch_all(symbols: list[str]) -> dict[str, ConfluenceResult]:
    """Fetch confluence data for all requested symbols.

    Returns dict of symbol → ConfluenceResult.
    Thread-safe, cached, never blocks more than ~8s total.
    """
    global _confluence_cache, _cache_time
    now = datetime.now(timezone.utc).timestamp()

    with _fetch_lock:
        if now - _cache_time < _CACHE_TTL and _confluence_cache:
            # Return only requested symbols from cache
            return {s: _confluence_cache.get(s) for s in symbols if s in _confluence_cache}

    # Determine which symbols to fetch for
    fetch_set = symbols

    # Run fetchers in parallel
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: dict[str, ConfluenceResult] = {}

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(_fetch_myfxbook_outlook, fetch_set): 'myfxbook',
            pool.submit(_fetch_fxstreet_sentiment, fetch_set): 'fxstreet_sentiment',
            pool.submit(_fetch_finviz_technicals, fetch_set): 'finviz',
        }
        for f in as_completed(futures, timeout=8):
            source = futures[f]
            try:
                data = f.result()
                if data:
                    for sym, entry in data.items():
                        if sym not in results:
                            results[sym] = ConfluenceResult(symbol=sym)
                        if source == 'myfxbook':
                            results[sym].myfxbook = entry
                        elif source == 'fxstreet_sentiment':
                            results[sym].fxstreet_sentiment = entry
                        elif source == 'finviz':
                            results[sym].finviz = entry
            except Exception:
                pass

    # Cache
    with _fetch_lock:
        _confluence_cache = results
        _cache_time = now

    return {s: results.get(s) for s in symbols if s in results}


def _score_retail_signal(long_pct: float, short_pct: float) -> tuple[str, int]:
    """Determine contrarian signal from retail long/short ratio.

    Extreme positioning (75%+ one way) = strong contrarian signal.
    Moderate (60-75%) = moderate signal.
    Balanced (<60%) = neutral.
    """
    if long_pct > short_pct:
        extreme = max(long_pct, short_pct)
        if extreme >= 85:
            return 'bearish', 5   # extremely overbought retail
        elif extreme >= 75:
            return 'bearish', 4
        elif extreme >= 65:
            return 'bearish', 3
        elif extreme >= 60:
            return 'bearish', 2
        else:
            return 'bearish', 1
    elif short_pct > long_pct:
        extreme = max(long_pct, short_pct)
        if extreme >= 85:
            return 'bullish', 5   # extremely oversold retail
        elif extreme >= 75:
            return 'bullish', 4
        elif extreme >= 65:
            return 'bullish', 3
        elif extreme >= 60:
            return 'bullish', 2
        else:
            return 'bullish', 1
    return 'neutral', 0


# ─── myFXbook Community Outlook ───────────────────────────────────────────


def _fetch_myfxbook_outlook(symbols: list[str]) -> dict[str, MyfxbookOutlook]:
    """Fetch retail long/short positioning from myFXbook community outlook."""
    result = {}
    try:
        import requests
        from bs4 import BeautifulSoup

        resp = requests.get(
            'https://www.myfxbook.com/community/outlook',
            timeout=6,
            headers={
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/125.0.0.0 Safari/537.36'
                ),
                'Accept': 'text/html,application/xhtml+xml',
            },
        )
        if resp.status_code != 200:
            return result

        soup = BeautifulSoup(resp.text, 'html.parser')

        # myFXbook outlook table
        rows = soup.select('table.table tbody tr')
        if not rows:
            # Try alternative selectors
            rows = soup.select('tr.outlook-row') or soup.select('tr[class*="outlook"]')
        if not rows:
            # Try the most generic approach — find tables with long/short columns
            for table in soup.select('table'):
                headers = table.find_all('th')
                header_texts = [h.get_text(strip=True).lower() for h in headers]
                if 'long' in header_texts and 'short' in header_texts:
                    rows = table.find_all('tr')[1:]
                    break

        for row in rows:
            cells = row.find_all('td')
            if len(cells) < 4:
                continue

            instr_cell = cells[0].get_text(strip=True).upper()
            # Match our symbol names
            sym = None
            for radar_sym, myfx_name in MYFXBOOK_SYMBOLS.items():
                if myfx_name == instr_cell or myfx_name in instr_cell:
                    sym = radar_sym
                    break
            if sym not in symbols or sym is None:
                continue

            # Parse long/short percentages
            try:
                long_text = cells[1].get_text(strip=True).replace('%', '')
                short_text = cells[2].get_text(strip=True).replace('%', '')
                long_pct = float(long_text)
                short_pct = float(short_text)

                pos_text = cells[3].get_text(strip=True).replace(',', '')
                total_pos = int(float(pos_text)) if pos_text.replace('.', '').isdigit() else 0

                signal, strength = _score_retail_signal(long_pct, short_pct)

                result[sym] = MyfxbookOutlook(
                    symbol=sym,
                    long_pct=round(long_pct, 1),
                    short_pct=round(short_pct, 1),
                    total_positions=total_pos,
                    signal=signal,
                    signal_strength=strength,
                )
            except (ValueError, IndexError):
                continue
    except Exception:
        pass
    return result


# ─── FXStreet Sentiment ───────────────────────────────────────────────────


def _fetch_fxstreet_sentiment(symbols: list[str]) -> dict[str, FxstreetSentiment]:
    """Fetch trader sentiment polling from FXStreet."""
    result = {}
    try:
        import requests
        from bs4 import BeautifulSoup

        # FXStreet has a sentiment overview page
        resp = requests.get(
            'https://www.fxstreet.com/sentiment',
            timeout=6,
            headers={
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/125.0.0.0 Safari/537.36'
                ),
                'Accept': 'text/html,application/xhtml+xml',
            },
        )
        if resp.status_code != 200:
            return result

        soup = BeautifulSoup(resp.text, 'html.parser')

        # FXStreet sentiment table — various possible structures
        sentiment_data = {}

        # Try to find sentiment bars/tables
        for item in soup.select('[class*="sentiment"]'):
            text = item.get_text(strip=True)
            # Look for patterns like "EURUSD 65% Long 35% Short"
            import re
            matches = re.findall(
                r'([A-Z]{6})\s*(\d{1,3}(?:\.\d)?)%?\s*(?:long|bullish)[^0-9]*(\d{1,3}(?:\.\d)?)%?\s*(?:short|bearish)',
                text, re.IGNORECASE
            )
            for m in matches:
                sym_raw = m[0]
                # Convert e.g. "EURUSD" format
                if len(sym_raw) == 6:
                    sentiment_data[sym_raw] = (float(m[1]), float(m[2]))

        # Also try the individual symbol pages for better accuracy
        # FXStreet has per-symbol sentiment at /sentiment/{symbol}
        # But that's too many requests — the overview page should suffice

        for sym in symbols:
            radar_sym = sym
            fxstreet_key = sym  # Direct mapping
            if fxstreet_key in sentiment_data:
                long_pct, short_pct = sentiment_data[fxstreet_key]
                signal, strength = _score_retail_signal(long_pct, short_pct)
                result[radar_sym] = FxstreetSentiment(
                    symbol=radar_sym,
                    long_pct=long_pct,
                    short_pct=short_pct,
                    signal=signal,
                    signal_strength=strength,
                )
    except Exception:
        pass
    return result


# ─── Finviz Technical Signals ─────────────────────────────────────────────


def _fetch_finviz_technicals(symbols: list[str]) -> dict[str, FinvizTechnicals]:
    """Fetch technical signal summary from Finviz for stocks/indices.

    For forex, Finviz data is limited — primarily covers US stocks and
    major indices. We try ETFs/index symbols that map to our instruments.
    """
    result = {}
    finviz_map = {
        'SP500': 'SPY', 'NAS100': 'QQQ', 'DJ30': 'DIA',
        'DAX40': 'EWG', 'FTSE100': 'EWU', 'VIX': 'VIX',
        'XAUUSD': 'GLD', 'XAGUSD': 'SLV', 'USOIL': 'USO',
        'UKOIL': 'BNO', 'BTCUSD': 'GBTC', 'ETHUSD': 'ETHE',
    }

    try:
        import requests

        # Finviz screener accepts comma-separated tickers
        tickers = []
        sym_map = {}
        for sym in symbols:
            ticker = finviz_map.get(sym)
            if ticker:
                tickers.append(ticker)
                sym_map[ticker] = sym

        if not tickers:
            return result

        resp = requests.get(
            f"https://finviz.com/screener.ashx?v=152&t={'%2C'.join(tickers)}&o=-change",
            timeout=6,
            headers={
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/125.0.0.0 Safari/537.36'
                ),
                'Accept': 'text/html,application/xhtml+xml',
            },
        )
        if resp.status_code != 200:
            return result

        # Parse the screener table
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, 'html.parser')

        # Finviz table rows
        for row in soup.select('tr[valign="top"]'):
            cells = row.find_all('td')
            if len(cells) < 2:
                continue
            ticker_cell = cells[1].get_text(strip=True).upper() if len(cells) > 1 else ''
            if ticker_cell not in sym_map:
                # Try the ticker link
                link = cells[1].find('a') if len(cells) > 1 else None
                ticker_cell = link.get_text(strip=True).upper() if link else ticker_cell

            radar_sym = sym_map.get(ticker_cell)
            if radar_sym is None:
                continue

            signals = {}
            bulls = 0
            bears = 0

            # Finviz shows technical signals in later columns
            # Typical columns: ticker, company, sector, industry, country,
            # market cap, P/E, price, change, volume, technical signals...
            for cell in cells[10:]:  # Signals typically start around column 10
                text = cell.get_text(strip=True)
                if not text:
                    continue
                if text in ('Bullish', 'Bearish'):
                    parts = cell.find_previous('td')
                    label = parts.get_text(strip=True) if parts else 'Signal'
                    signals[label] = text
                    if text == 'Bullish':
                        bulls += 1
                    else:
                        bears += 1

            if bulls > bears:
                overall = 'bullish'
            elif bears > bulls:
                overall = 'bearish'
            else:
                overall = 'neutral'

            result[radar_sym] = FinvizTechnicals(
                symbol=radar_sym,
                signals=signals,
                bullish_count=bulls,
                bearish_count=bears,
                overall=overall,
            )
    except Exception:
        pass
    return result


# ─── Pivot Points (from FXStreet) ─────────────────────────────────────────


def fetch_pivots(symbols: list[str]) -> dict[str, FxstreetPivot]:
    """Fetch pivot point levels from FXStreet for forex pairs."""
    result = {}
    try:
        import requests
        from bs4 import BeautifulSoup
        import re

        # FXStreet pivots page
        resp = requests.get(
            'https://www.fxstreet.com/technical-charts/pivots',
            timeout=6,
            headers={
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/125.0.0.0 Safari/537.36'
                ),
                'Accept': 'text/html,application/xhtml+xml',
            },
        )
        if resp.status_code != 200:
            return result

        soup = BeautifulSoup(resp.text, 'html.parser')

        # Parse pivot tables — FXStreet typically has a Classic pivot table
        tables = soup.select('table.technical-pivots-table')
        if not tables:
            tables = soup.select('table[class*="pivot"]')
        if not tables:
            # Look for any table with pivot-like data
            for table in soup.select('table'):
                if 'pivot' in table.get_text(strip=True).lower():
                    tables = [table]
                    break

        for table in tables:
            rows = table.find_all('tr')
            for row in rows:
                cells = row.find_all('td')
                if len(cells) < 8:
                    continue

                # First cell should be a pair name
                sym_text = cells[0].get_text(strip=True).upper().replace('/', '')
                # Map to our symbol format (EURUSD, GBPUSD, etc.)
                radar_sym = None
                for s in symbols:
                    if s == sym_text or s == sym_text.replace('/', ''):
                        radar_sym = s
                        break
                if radar_sym is None:
                    continue

                try:
                    vals = []
                    for i in range(1, min(9, len(cells))):
                        v = cells[i].get_text(strip=True).replace(',', '')
                        vals.append(float(v) if v else 0.0)

                    if len(vals) >= 7:
                        result[radar_sym] = FxstreetPivot(
                            symbol=radar_sym,
                            pivot=vals[0],
                            r1=vals[1], r2=vals[2], r3=vals[3],
                            s1=vals[4], s2=vals[5], s3=vals[6],
                        )
                except (ValueError, IndexError):
                    continue
    except Exception:
        pass
    return result
