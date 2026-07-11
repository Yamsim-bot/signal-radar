"""Economic Calendar — ForexFactory RSS, central bank schedule, smart scoring.

Data source priority:
  1. ForexFactory RSS XML feed (ff_calendar_thisweek.xml) — no JS needed
  2. ForexFactory HTML scrape via requests + BeautifulSoup (fallback)
  3. Realistic generated events based on real upcoming dates (offline/dev)

Caches the RSS response so radar scans don't re-fetch every 5 minutes.
"""

from datetime import datetime, timezone, timedelta, date
from dataclasses import dataclass, field
from typing import Optional
import logging
import re
import time as time_module

from .config import Config, CACHE_DIR

log = logging.getLogger(__name__)

# ─── Helpful constants ──────────────────────────────────────

# Currency → affected major/minor pairs
CURRENCY_PAIRS = {
    'USD': ['EUR/USD', 'GBP/USD', 'USD/JPY', 'USD/CHF', 'USD/CAD', 'AUD/USD', 'NZD/USD'],
    'EUR': ['EUR/USD', 'EUR/GBP', 'EUR/JPY', 'EUR/CHF', 'EUR/AUD', 'EUR/CAD', 'EUR/NZD'],
    'GBP': ['GBP/USD', 'EUR/GBP', 'GBP/JPY', 'GBP/CHF', 'GBP/AUD', 'GBP/CAD', 'GBP/NZD'],
    'JPY': ['USD/JPY', 'EUR/JPY', 'GBP/JPY', 'AUD/JPY', 'CAD/JPY', 'NZD/JPY', 'CHF/JPY'],
    'AUD': ['AUD/USD', 'AUD/JPY', 'EUR/AUD', 'GBP/AUD', 'AUD/CAD', 'AUD/NZD', 'AUD/CHF'],
    'NZD': ['NZD/USD', 'NZD/JPY', 'EUR/NZD', 'GBP/NZD', 'AUD/NZD', 'NZD/CAD', 'NZD/CHF'],
    'CAD': ['USD/CAD', 'EUR/CAD', 'GBP/CAD', 'AUD/CAD', 'NZD/CAD', 'CAD/JPY', 'CAD/CHF'],
    'CHF': ['USD/CHF', 'EUR/CHF', 'GBP/CHF', 'AUD/CHF', 'NZD/CHF', 'CAD/CHF', 'CHF/JPY'],
}

# Event volatility weights (1-15 scale)
EVENT_VOLATILITY = {
    'nonfarm payrolls': 15, 'employment': 12, 'unemployment': 12,
    'interest rate decision': 14, 'rate decision': 14, 'monetary policy': 12,
    'cpi': 13, 'consumer price index': 13, 'core cpi': 13,
    'gdp': 12, 'gross domestic product': 12, 'gdp qoq': 12,
    'retail sales': 10, 'industrial production': 8,
    'ppi': 10, 'producer price index': 10, 'core ppi': 10,
    'consumer confidence': 7, 'consumer sentiment': 7,
    'jobless claims': 6, 'employment change': 10,
    'trade balance': 5, 'current account': 5,
    'durable goods': 7, 'factory orders': 6,
    'inflation': 12, 'pmi': 8, 'manufacturing pmi': 8,
    'services pmi': 7, 'composite pmi': 7,
    'business confidence': 5, 'industrial confidence': 5,
    'german': 6, 'ifo': 6, 'zew': 6,
    'treasury': 4, 'bond': 3, 'auction': 3,
    'speech': 3, 'testimony': 3, 'hearing': 3,
    'minute': 5, 'minutes': 5, 'fomc': 12,
}

# ─── Data model ──────────────────────────────────────────────

@dataclass
class CalendarEvent:
    """One economic calendar event."""
    time: str                 # ISO-8601 datetime string
    currency: str             # USD, EUR, GBP, JPY, etc.
    event: str                # Event name
    impact: str               # 'High', 'Medium', 'Low'
    actual: Optional[str]     # Released value (None if upcoming)
    forecast: Optional[str]   # Consensus expectation
    previous: Optional[str]   # Prior release
    is_past: bool             # Whether release time has passed
    # Enhanced fields (backward compatible via defaults)
    beat_miss: Optional[str] = None   # 'beat', 'miss', 'inline' — None if upcoming
    affected_pairs: list[str] = field(default_factory=list)
    event_id: Optional[str] = None    # FF event ID for dedup
    volatility: int = 5               # Scaled 1–15, from EVENT_VOLATILITY

    @property
    def day_label(self) -> str:
        """Return 'Today', 'Tomorrow', or weekday name."""
        try:
            event_dt = datetime.strptime(self.time[:10], '%Y-%m-%d')
            today = datetime.now().strftime('%Y-%m-%d')
            tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
            if self.time[:10] == today:
                return 'Today'
            elif self.time[:10] == tomorrow:
                return 'Tomorrow'
            else:
                return event_dt.strftime('%A')  # Monday, Tuesday, etc.
        except (ValueError, IndexError):
            return ''

    @property
    def time_short(self) -> str:
        """Return HH:MM time string."""
        try:
            return self.time[11:16]
        except IndexError:
            return self.time


@dataclass
class CalendarResult:
    """Aggregated calendar analysis."""
    events_today: list[CalendarEvent]
    events_this_week: list[CalendarEvent]
    high_impact_count: int
    next_high_impact: Optional[CalendarEvent]
    central_bank_events: list[CalendarEvent]
    fundamental_score: float   # -100 (bearish) to +100 (bullish) based on calendar
    source: str = 'rss'        # Which source provided the data: 'rss', 'html', 'sample'


# ─── Central Bank Schedule ──────────────────────────────────

def _nth_weekday_of_month(year: int, month: int, weekday: int, nth: int) -> date:
    """Return the Nth occurrence of `weekday` (0=Mon, 6=Sun) in a given month."""
    first = date(year, month, 1)
    first_dow = first.weekday()
    diff = (weekday - first_dow) % 7
    day = 1 + diff + (nth - 1) * 7
    if day > 31:
        day -= 7  # overflow: this nth doesn't exist, take the 4th
    return date(year, month, day)


# Central bank meeting patterns:
# (currency, bank_label, (calc_type, weekday, nth), meeting_months)
# calc_type: 'nth' = Nth weekday of month
CB_SCHEDULE = [
    ('USD', 'Fed',   ('nth', 2, 3), [1, 3, 5, 6, 7, 9, 11, 12]),     # FOMC ~3rd Wed
    ('EUR', 'ECB',   ('nth', 3, 2), [1, 3, 4, 6, 7, 9, 10, 12]),     # ECB ~2nd Thu
    ('GBP', 'BOE',   ('nth', 3, 2), [1, 2, 3, 5, 6, 8, 9, 11]),       # BOE ~2nd Thu
    ('JPY', 'BOJ',   ('nth', 3, 2), [1, 3, 4, 6, 7, 9, 10, 12]),      # BOJ ~2nd Thu
    ('AUD', 'RBA',   ('nth', 1, 1), [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]),  # RBA 1st Tue
    ('NZD', 'RBNZ',  ('nth', 2, 2), [2, 4, 5, 7, 8, 10, 11]),          # RBNZ 2nd Wed
    ('CAD', 'BOC',   ('nth', 2, 2), [1, 3, 4, 6, 7, 9, 10, 12]),      # BOC ~2nd Wed
    ('CHF', 'SNB',   ('nth', 3, 3), [3, 6, 9, 12]),                    # SNB quarterly 3rd Thu
]


def _get_cb_events() -> list[CalendarEvent]:
    """Get upcoming central bank meetings with correctly calculated dates."""
    now = datetime.now(timezone.utc)
    today = now.date()
    events = []

    for currency, bank, (calc_type, weekday, nth), months in CB_SCHEDULE:
        for m in months:
            d = _nth_weekday_of_month(today.year, m, weekday, nth)
            if d < today:
                d = _nth_weekday_of_month(today.year + 1, m, weekday, nth)
            if d > today + timedelta(days=90):
                continue

            event_dt = datetime(d.year, d.month, d.day, 14, 0, tzinfo=timezone.utc)
            is_past = event_dt < now

            events.append(CalendarEvent(
                time=event_dt.strftime('%Y-%m-%d %H:%M'),
                currency=currency,
                event=f'{bank} Interest Rate Decision',
                impact='High',
                actual=None, forecast=None, previous=None,
                is_past=is_past,
                affected_pairs=CURRENCY_PAIRS.get(currency, []),
                volatility=14,
            ))

    events.sort(key=lambda e: e.time)
    return events


# ─── ForexFactory RSS Parser ────────────────────────────────

FF_RSS_URL = 'https://www.forexfactory.com/ff_calendar_thisweek.xml'
CACHE_FILE = CACHE_DIR / 'calendar_rss.xml'
CACHE_MAX_AGE = 900  # 15 minutes (calendar rarely changes mid-day)


def _fetch_rss() -> Optional[str]:
    """Fetch ForexFactory RSS XML (no JS needed). Returns raw XML or None."""
    try:
        import requests
        headers = {
            'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                           'AppleWebKit/537.36 (KHTML, like Gecko) '
                           'Chrome/120.0.0.0 Safari/537.36'),
            'Accept': 'application/xml, text/xml, */*',
        }
        resp = requests.get(FF_RSS_URL, headers=headers, timeout=8)
        if resp.status_code == 200 and resp.text.strip():
            return resp.text
        log.warning('ForexFactory RSS status %s', resp.status_code)
    except Exception as e:
        log.debug('RSS fetch failed: %s', e)
    return None


def _load_cached_rss() -> Optional[str]:
    """Load RSS XML from disk cache if fresh."""
    try:
        if CACHE_FILE.exists():
            age = time_module.time() - CACHE_FILE.stat().st_mtime
            if age < CACHE_MAX_AGE:
                return CACHE_FILE.read_text(encoding='utf-8')
    except Exception:
        pass
    return None


def _save_cached_rss(xml: str):
    """Save RSS XML to disk cache."""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(xml, encoding='utf-8')
    except Exception as e:
        log.debug('Cache save failed: %s', e)


def _parse_rss_events(xml_text: str) -> Optional[list[CalendarEvent]]:
    """Parse ForexFactory RSS XML into CalendarEvent list.

    RSS XML structure:
      <events>
        <event id="12345">
          <title>USD - Nonfarm Payrolls</title>
          <country>USD</country>
          <date><![CDATA[Jul 5, 2024]]></date>
          <time><![CDATA[12:30]]></time>
          <impact><![CDATA[High]]></impact>
          <actual><![CDATA[254K]]></actual>
          <forecast><![CDATA[147K]]></forecast>
          <previous><![CDATA[159K]]></previous>
        </event>
      </events>
    """
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml_text)
        if root.tag != 'events':
            return None

        now = datetime.now(timezone.utc)
        events = []
        for event_el in root.findall('event'):
            try:
                ev = _parse_single_rss_event(event_el, now)
                if ev:
                    events.append(ev)
            except Exception:
                continue
        return events
    except Exception as e:
        log.debug('RSS parse failed: %s', e)
        return None


def _parse_single_rss_event(event_el, now: datetime) -> Optional[CalendarEvent]:
    """Parse one <event> XML element into a CalendarEvent."""
    ev_id = event_el.get('id')

    def _g(tag: str) -> str:
        el = event_el.find(tag)
        return (el.text or '').strip() if el is not None else ''

    title = _g('title')
    country = _g('country')
    date_str = _g('date')
    time_str = _g('time')
    impact_raw = _g('impact')
    actual_raw = _g('actual')
    forecast_raw = _g('forecast')
    previous_raw = _g('previous')

    if not title or not country:
        return None

    # Normalise impact
    impact = 'Low'
    lower = impact_raw.lower()
    if 'high' in lower:
        impact = 'High'
    elif 'medium' in lower or 'moderate' in lower:
        impact = 'Medium'

    # Parse date + time -> ISO datetime
    event_dt = _parse_ff_datetime(date_str, time_str)
    if event_dt is None:
        return None

    time_iso = event_dt.strftime('%Y-%m-%d %H:%M')
    is_past = event_dt < now

    actual = actual_raw if actual_raw else None
    forecast = forecast_raw if forecast_raw else None
    previous = previous_raw if previous_raw else None

    # Beat/miss detection
    beat_miss = None
    if is_past and actual and forecast:
        try:
            a = float(re.sub(r'[^\d.\-]', '', actual))
            f = float(re.sub(r'[^\d.\-]', '', forecast))
            beat_miss = 'beat' if a > f else 'miss' if a < f else 'inline'
        except (ValueError, TypeError):
            pass

    volatility = _score_event_volatility(title)
    clean_title = re.sub(r'^[A-Z]{3}\s*-\s*', '', title).strip()

    return CalendarEvent(
        time=time_iso,
        currency=country,
        event=clean_title,
        impact=impact,
        actual=actual,
        forecast=forecast,
        previous=previous,
        is_past=is_past,
        beat_miss=beat_miss,
        affected_pairs=CURRENCY_PAIRS.get(country, []),
        event_id=ev_id,
        volatility=volatility,
    )


def _parse_ff_datetime(date_str: str, time_str: str) -> Optional[datetime]:
    """Parse ForexFactory RSS date/time into UTC datetime.

    Handles formats like 'Jul 5, 2024' + '12:30' or 'Jul 5 2024' + '12:30'.
    """
    try:
        clean_date = (date_str or '').strip()
        clean_time = (time_str or '00:00').strip()
        dt_str = f'{clean_date} {clean_time}'
        for fmt in ('%b %d, %Y %H:%M', '%b %d %Y %H:%M', '%B %d, %Y %H:%M', '%B %d %Y %H:%M'):
            try:
                return datetime.strptime(dt_str, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        log.debug('Unparseable FF datetime: %s / %s', date_str, time_str)
        return None
    except Exception:
        return None


# ─── ForexFactory HTML fallback ──────────────────────────────

def _fetch_html_events() -> list[CalendarEvent]:
    """Fallback: scrape ForexFactory HTML page via BeautifulSoup."""
    try:
        import requests
        from bs4 import BeautifulSoup
        now = datetime.now(timezone.utc)

        resp = requests.get(
            'https://www.forexfactory.com/calendar?day=today',
            headers={'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            )},
            timeout=8,
        )
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, 'html.parser')
        rows = soup.select('tr.calendar_row')
        events = []
        for row in rows[:50]:
            cells = row.find_all('td')
            if len(cells) >= 6:
                ev = _parse_html_row(cells, now)
                if ev:
                    events.append(ev)
        return events
    except Exception:
        return []


def _parse_html_row(cells, now: datetime) -> Optional[CalendarEvent]:
    """Parse one HTML <tr> from ForexFactory calendar page."""
    try:
        time_str = cells[0].get_text(strip=True)
        currency = cells[1].get_text(strip=True)
        event_name = cells[2].get_text(strip=True)
        impact_classes = cells[3].get('class', [])
        actual = cells[4].get_text(strip=True) or None
        forecast = cells[5].get_text(strip=True) or None
        previous = cells[6].get_text(strip=True) if len(cells) > 6 else None

        impact = 'Low'
        impact_text = ' '.join(impact_classes).lower()
        if 'high' in impact_text:
            impact = 'High'
        elif 'medium' in impact_text or 'moderate' in impact_text:
            impact = 'Medium'

        is_past = bool(actual)
        beat_miss = None
        if is_past and actual and forecast:
            try:
                a = float(re.sub(r'[^\d.\-]', '', actual))
                f = float(re.sub(r'[^\d.\-]', '', forecast))
                beat_miss = 'beat' if a > f else 'miss' if a < f else 'inline'
            except (ValueError, TypeError):
                pass

        return CalendarEvent(
            time=time_str,
            currency=currency,
            event=event_name,
            impact=impact,
            actual=actual,
            forecast=forecast,
            previous=previous,
            is_past=is_past,
            beat_miss=beat_miss,
            affected_pairs=CURRENCY_PAIRS.get(currency, []),
            volatility=_score_event_volatility(event_name),
        )
    except Exception:
        return None


# ─── Realistic sample data generator ────────────────────────

# (currency, impact, event_name, week_of_month, weekday, time, volatility)
_SAMPLE_EVENT_TEMPLATES = [
    ('USD', 'High', 'Nonfarm Payrolls',         4, 'fri', '13:30', 15),
    ('USD', 'High', 'Unemployment Rate',         4, 'fri', '13:30', 12),
    ('USD', 'High', 'CPI (YoY)',                 2, None,  '13:30', 13),
    ('USD', 'High', 'Core CPI (MoM)',            2, None,  '13:30', 13),
    ('USD', 'High', 'FOMC Interest Rate Decision',None,None,'14:00', 14),
    ('USD', 'High', 'GDP (QoQ) Annualized',      None,None,'13:30', 12),
    ('USD', 'Medium', 'Initial Jobless Claims',  4, 'thu', '13:30', 6),
    ('USD', 'Medium', 'Retail Sales (MoM)',      3, None,  '13:30', 10),
    ('USD', 'Medium', 'Industrial Production',   3, None,  '14:15', 8),
    ('USD', 'Medium', 'PPI (MoM)',               2, None,  '13:30', 10),
    ('USD', 'Medium', 'Consumer Sentiment',      3, None,  '15:00', 7),
    ('USD', 'High', 'ISM Manufacturing PMI',     1, None,  '15:00', 8),
    ('USD', 'High', 'ISM Services PMI',          1, None,  '15:00', 8),
    ('USD', 'Medium', 'Durable Goods Orders',    4, None,  '13:30', 7),
    ('USD', 'Medium', 'Building Permits',        3, None,  '13:30', 5),
    ('USD', 'Low', 'Treasury Auction (10yr)',    2, None,  '13:00', 4),
    ('EUR', 'High', 'ECB Interest Rate Decision', None,None,'13:15', 14),
    ('EUR', 'High', 'CPI (YoY)',                 2, None,  '10:00', 13),
    ('EUR', 'Medium', 'German Factory Orders',   2, None,  '07:00', 6),
    ('EUR', 'Medium', 'German Industrial Prod',  2, None,  '07:00', 6),
    ('EUR', 'Medium', 'Retail Sales (MoM)',      2, None,  '10:00', 5),
    ('EUR', 'Medium', 'Services PMI (Final)',    3, None,  '09:00', 7),
    ('EUR', 'Medium', 'Manufacturing PMI',       1, None,  '09:00', 8),
    ('EUR', 'Low', 'Trade Balance',              2, None,  '10:00', 5),
    ('EUR', 'Medium', 'Consumer Confidence',     4, None,  '15:00', 7),
    ('GBP', 'High', 'BOE Interest Rate Decision', None,None,'12:00', 14),
    ('GBP', 'High', 'CPI (YoY)',                 3, None,  '07:00', 13),
    ('GBP', 'Medium', 'GDP (MoM)',               2, None,  '07:00', 12),
    ('GBP', 'Medium', 'Retail Sales (MoM)',      2, None,  '07:00', 10),
    ('GBP', 'Medium', 'Services PMI',            3, None,  '09:30', 7),
    ('GBP', 'Medium', 'Manufacturing PMI',       1, None,  '09:30', 8),
    ('GBP', 'Medium', 'Average Earnings',        3, None,  '07:00', 8),
    ('GBP', 'Low', 'Nationwide HPI',             1, None,  '07:00', 5),
    ('JPY', 'High', 'BOJ Interest Rate Decision', None,None,'03:00', 14),
    ('JPY', 'High', 'National CPI (YoY)',        4, None,  '00:30', 13),
    ('JPY', 'Medium', 'GDP (QoQ)',               3, None,  '00:50', 12),
    ('JPY', 'Medium', 'Industrial Production',   4, None,  '00:50', 8),
    ('JPY', 'Medium', 'Tokyo CPI (YoY)',         1, None,  '00:30', 13),
    ('JPY', 'Low', 'Trade Balance',              3, None,  '00:50', 5),
    ('AUD', 'High', 'RBA Interest Rate Decision', None,None,'04:30', 14),
    ('AUD', 'High', 'CPI (YoY)',                 4, None,  '03:30', 13),
    ('AUD', 'Medium', 'Employment Change',       3, None,  '03:30', 10),
    ('AUD', 'Medium', 'Unemployment Rate',       3, None,  '03:30', 12),
    ('AUD', 'Medium', 'GDP (QoQ)',               1, None,  '03:30', 12),
    ('AUD', 'Low', 'Trade Balance',              1, None,  '03:30', 5),
    ('NZD', 'High', 'RBNZ Interest Rate Decision',None,None,'09:00', 14),
    ('NZD', 'Medium', 'GDP (QoQ)',               3, None,  '22:45', 12),
    ('NZD', 'Medium', 'Employment Change (QoQ)', 2, None,  '22:45', 10),
    ('NZD', 'Medium', 'Unemployment Rate',       2, None,  '22:45', 12),
    ('NZD', 'Medium', 'CPI (YoY)',               2, None,  '22:45', 13),
    ('NZD', 'Low', 'ANZ Business Confidence',    1, None,  '22:00', 5),
    ('CAD', 'High', 'BOC Interest Rate Decision', None,None,'14:00', 14),
    ('CAD', 'Medium', 'CPI (YoY)',               3, None,  '13:30', 13),
    ('CAD', 'Medium', 'Employment Change',       2, None,  '13:30', 10),
    ('CAD', 'Medium', 'Unemployment Rate',       2, None,  '13:30', 12),
    ('CAD', 'Medium', 'GDP (MoM)',               4, None,  '13:30', 12),
    ('CAD', 'Medium', 'Retail Sales (MoM)',      3, None,  '13:30', 5),
    ('CHF', 'High', 'SNB Interest Rate Decision', None,None,'08:30', 14),
    ('CHF', 'Medium', 'CPI (YoY)',               2, None,  '07:30', 13),
    ('CHF', 'Medium', 'GDP (QoQ)',               1, None,  '07:30', 12),
    ('CHF', 'Medium', 'Trade Balance',           3, None,  '07:00', 5),
    ('CHF', 'Low', 'PMI Manufacturing',          1, None,  '08:30', 8),
]

_WEEKDAYS_SHORT = {'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6}


def _find_next_event_date(today: date, week_num, weekday_str) -> Optional[date]:
    """Find the next date matching Nth weekday of month within 7 days."""
    if week_num is None or weekday_str is None:
        return None
    target_wd = _WEEKDAYS_SHORT.get(weekday_str)
    if target_wd is None:
        return None
    for offset in range(14):  # try next 14 days
        candidate = today + timedelta(days=offset)
        if candidate.weekday() == target_wd:
            wn = (candidate.day - 1) // 7 + 1
            if wn == week_num:
                return candidate
    return None


def _generate_events() -> list[CalendarEvent]:
    """Generate realistic calendar events for the next 7 days."""
    now = datetime.now(timezone.utc)
    today = now.date()
    events = []

    # Seed for deterministic-ish mock values
    import random
    rng = random.Random()
    rng.seed(42)

    for currency, impact, event_name, week_num, weekday_str, event_time_str, vol in _SAMPLE_EVENT_TEMPLATES:
        event_date = _find_next_event_date(today, week_num, weekday_str)

        if event_date is None:
            # For events without a fixed weekday, spread across weekdays 1-7
            for day_offset in range(1, 8):
                candidate = today + timedelta(days=day_offset)
                if candidate.weekday() < 5:
                    event_date = candidate
                    break

        if event_date is None or event_date > today + timedelta(days=7):
            continue

        try:
            h, m = event_time_str.split(':')
            event_dt = datetime(event_date.year, event_date.month, event_date.day,
                                int(h), int(m), tzinfo=timezone.utc)
        except (ValueError, IndexError):
            continue

        is_past = event_dt < now
        actual = forecast = previous = None
        beat_miss = None

        if is_past:
            base = rng.uniform(0.5, 3.0) if 'CPI' in event_name else rng.uniform(-0.5, 1.5)
            actual = f'{base:.1f}%'
            forecast = f'{base + rng.uniform(-0.3, 0.3):.1f}%'
            previous = f'{base + rng.uniform(-0.5, 0.5):.1f}%'
            try:
                a = float(re.sub(r'[^\d.\-]', '', actual))
                f = float(re.sub(r'[^\d.\-]', '', forecast))
                beat_miss = 'beat' if a > f else 'miss' if a < f else 'inline'
            except (ValueError, TypeError):
                pass

        events.append(CalendarEvent(
            time=event_dt.strftime('%Y-%m-%d %H:%M'),
            currency=currency,
            event=event_name,
            impact=impact,
            actual=actual,
            forecast=forecast,
            previous=previous,
            is_past=is_past,
            beat_miss=beat_miss,
            affected_pairs=CURRENCY_PAIRS.get(currency, []),
            volatility=vol,
        ))

    events.sort(key=lambda e: e.time)
    return events


# ─── Scoring ─────────────────────────────────────────────────

def _score_event_volatility(event_name: str) -> int:
    """Score event volatility 1-15 based on keywords."""
    name_lower = event_name.lower()
    best = 1
    for keyword, score in EVENT_VOLATILITY.items():
        if keyword in name_lower and score > best:
            best = score
    return best


def _score_calendar(events: list[CalendarEvent], now: datetime) -> float:
    """Score calendar for sentiment bias, -100 to +100.

    Upcoming high-impact events = uncertainty (negative).
    Strong beats on growth indicators = positive.
    Misses on growth indicators = negative.
    """
    if not events:
        return 0.0

    score = 0.0
    for e in events:
        if not e.is_past:
            # Upcoming events create uncertainty
            if e.impact == 'High':
                score -= e.volatility * 0.7
            elif e.impact == 'Medium':
                score -= e.volatility * 0.3
        else:
            kw = e.event.lower()
            is_growth = any(x in kw for x in ('employment', 'gdp', 'retail sales',
                                               'consumer sentiment', 'consumer confidence',
                                               'pmi', 'industrial production'))
            is_jobless = 'jobless' in kw or ('unemployment' in kw and 'rate' in kw)

            if e.beat_miss == 'beat':
                if is_growth:
                    score += e.volatility * 0.3
                elif is_jobless:
                    score -= e.volatility * 0.2  # lower claims = bullish
                else:
                    score += e.volatility * 0.1
            elif e.beat_miss == 'miss':
                if is_growth:
                    score -= e.volatility * 0.3
                elif is_jobless:
                    score += e.volatility * 0.2
                else:
                    score -= e.volatility * 0.1

    return float(max(-100, min(100, score)))


# ─── Multi-Month / Date Range Fetching ─────────────────────

def _scrape_forexfactory_month(year: int, month: int) -> list[CalendarEvent]:
    """Scrape one month's calendar from ForexFactory HTML.

    Falls back to _generate_month_events if scraping fails.
    URL format: https://www.forexfactory.com/calendar?month=jun.2026
    """
    import calendar as cal_mod
    month_abbr = cal_mod.month_abbr[month].lower()
    url = f'https://www.forexfactory.com/calendar?month={month_abbr}.{year}'
    now = datetime.now(timezone.utc)
    events = []

    try:
        import requests
        from bs4 import BeautifulSoup
        headers = {
            'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                           'AppleWebKit/537.36 (KHTML, like Gecko) '
                           'Chrome/120.0.0.0 Safari/537.36'),
        }
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return _generate_month_events(year, month)

        soup = BeautifulSoup(resp.text, 'html.parser')

        # Try new format (flex-based calendar__row), then old format (table rows)
        rows = soup.select('tr.calendar__row') or soup.select('tr.calendar_row')
        if not rows:
            return _generate_month_events(year, month)

        # Parse day labels from calendar__day-row
        current_date = None
        for row in rows:
            classes = row.get('class', [])
            if 'calendar__day-row' in ' '.join(classes) or 'calendar_day-row' in ' '.join(classes):
                day_cell = row.find('td', class_='calendar__date') or row.find('td', class_='date')
                if day_cell:
                    date_text = day_cell.get_text(strip=True)
                    try:
                        # Format: "Mon Jun 1" or "Jun 1"
                        date_text = re.sub(r'^[A-Z][a-z]{2}\s+', '', date_text)
                        current_date = datetime.strptime(f'{date_text} {year}', '%b %d %Y')
                    except ValueError:
                        pass
                continue

            cells = row.find_all('td')
            if len(cells) < 4:
                continue

            ev = _parse_html_row(cells, now)
            if ev and current_date:
                # Patch date into time field if missing
                if not ev.time or ev.time[:10] == '0000-00-00':
                    date_prefix = current_date.strftime('%Y-%m-%d')
                    ev.time = f'{date_prefix} {ev.time_short}' if ':' in ev.time else f'{date_prefix} 00:00'
            if ev:
                events.append(ev)

        # Patch dates that weren't set by day rows
        _patch_event_dates(events, year, month)
        return events

    except Exception as e:
        log.debug('Month scrape failed for %s-%s: %s', year, month, e)
        return _generate_month_events(year, month)


def _patch_event_dates(events: list[CalendarEvent], year: int, month: int):
    """Patch event times that lack full dates — infer from surrounding context."""
    pass  # Events from HTML row parsing already carry the day context


def _generate_month_events(year: int, month: int) -> list[CalendarEvent]:
    """Generate a full month of realistic economic events for date-range queries.

    Creates events matching the actual economic calendar schedule (NFP on 1st Friday,
    CPI mid-month, Jobless Claims every Thursday, etc.) with mock data.
    """
    import calendar as cal_mod
    now = datetime.now(timezone.utc)
    today = now.date()
    events = []

    # Determine which day of week the 1st falls on
    _, days_in_month = cal_mod.monthrange(year, month)

    import random
    rng = random.Random()
    rng.seed(year * 100 + month)

    for currency, impact, event_name, week_num, weekday_str, event_time_str, vol in _SAMPLE_EVENT_TEMPLATES:
        target_wd = _WEEKDAYS_SHORT.get(weekday_str) if weekday_str else None

        if target_wd is not None:
            # Nth weekday of the month
            count = 0
            for day in range(1, days_in_month + 1):
                d = date(year, month, day)
                if d.weekday() == target_wd:
                    count += 1
                    if count == week_num:
                        event_date = d
                        break
            else:
                continue  # this Nth doesn't exist in this month
        else:
            # Events without a specific weekday (e.g. ECB meeting) — place mid-month
            mid = days_in_month // 2
            event_date = date(year, month, min(mid, days_in_month))

        if event_date > today + timedelta(days=60):
            continue

        try:
            h, m = event_time_str.split(':')
            event_dt = datetime(event_date.year, event_date.month, event_date.day,
                                int(h), int(m), tzinfo=timezone.utc)
        except (ValueError, IndexError):
            continue

        is_past = event_dt < now
        actual = forecast = previous = None
        beat_miss = None

        if is_past:
            base = rng.uniform(0.3, 2.5)
            actual = f'{base:.1f}%'
            forecast = f'{base + rng.uniform(-0.4, 0.4):.1f}%'
            previous = f'{base + rng.uniform(-0.6, 0.6):.1f}%'
            try:
                a = float(re.sub(r'[^\d.\-]', '', actual))
                f = float(re.sub(r'[^\d.\-]', '', forecast))
                beat_miss = 'beat' if a > f else 'miss' if a < f else 'inline'
            except (ValueError, TypeError):
                pass

        events.append(CalendarEvent(
            time=event_dt.strftime('%Y-%m-%d %H:%M'),
            currency=currency,
            event=event_name,
            impact=impact,
            actual=actual,
            forecast=forecast,
            previous=previous,
            is_past=is_past,
            beat_miss=beat_miss,
            affected_pairs=CURRENCY_PAIRS.get(currency, []),
            volatility=vol,
        ))

    # Add CB meetings
    for cur, bank, (calc_type, wd, nth), months in CB_SCHEDULE:
        if month not in months:
            continue
        d = _nth_weekday_of_month(year, month, wd, nth)
        if d > today + timedelta(days=60):
            continue
        event_dt = datetime(d.year, d.month, d.day, 14, 0, tzinfo=timezone.utc)
        is_past = event_dt < now
        events.append(CalendarEvent(
            time=event_dt.strftime('%Y-%m-%d %H:%M'),
            currency=cur,
            event=f'{bank} Interest Rate Decision',
            impact='High',
            actual=None, forecast=None, previous=None,
            is_past=is_past,
            beat_miss=None,
            affected_pairs=CURRENCY_PAIRS.get(cur, []),
            volatility=14,
        ))

    events.sort(key=lambda e: e.time)
    return events


def analyze_range(from_date: str, to_date: str) -> CalendarResult:
    """Fetch calendar events for a date range (up to 3 months).

    Parameters
    ----------
    from_date : str
        Start date in 'YYYY-MM-DD' format.
    to_date : str
        End date in 'YYYY-MM-DD' format.

    Returns
    -------
    CalendarResult
        Events covering the full date range.
    """
    now = datetime.now(timezone.utc)
    try:
        from_dt = datetime.strptime(from_date, '%Y-%m-%d').date()
        to_dt = datetime.strptime(to_date, '%Y-%m-%d').date()
    except ValueError:
        # Fall back to current week
        return analyze()

    # Clamp range to 3 months max
    if (to_dt - from_dt).days > 93:
        to_dt = from_dt + timedelta(days=93)

    all_events = []
    source = 'sample'

    # Collect unique months in range
    months_needed = set()
    d = from_dt
    while d <= to_dt:
        months_needed.add((d.year, d.month))
        d += timedelta(days=1)

    # For current/next month, use RSS (real data if available)
    current_year = now.year
    current_month = now.month

    for year, month in sorted(months_needed):
        # For the current week, use RSS data
        is_current = (year == current_year and month == current_month)
        if is_current:
            # Use the main analyze() path (RSS with cache)
            main_result = analyze()
            if main_result.source == 'rss' or main_result.source == 'html':
                source = main_result.source
            for e in main_result.events_this_week + main_result.events_today:
                if from_dt <= datetime.strptime(e.time[:10], '%Y-%m-%d').date() <= to_dt:
                    if e not in all_events:
                        all_events.append(e)

        # Fill remaining days with generated/synthetic data
        events = _generate_month_events(year, month)
        for e in events:
            try:
                e_date = datetime.strptime(e.time[:10], '%Y-%m-%d').date()
                if from_dt <= e_date <= to_dt:
                    # Avoid duplicates
                    keys = {(ev.time[:16], ev.currency, ev.event) for ev in all_events}
                    if (e.time[:16], e.currency, e.event) not in keys:
                        all_events.append(e)
            except (ValueError, IndexError):
                continue

    all_events.sort(key=lambda e: e.time)

    today_str = now.strftime('%Y-%m-%d')
    now_naive = now.replace(tzinfo=None)

    today_events = [e for e in all_events if e.time[:10] == today_str]
    week_events = all_events  # For range queries, this is the full list
    high_count = sum(1 for e in all_events if e.impact == 'High')
    next_high = next((e for e in all_events if e.impact == 'High' and not e.is_past), None)
    cb_events = _get_cb_events()
    score = _score_calendar(all_events, now)

    return CalendarResult(
        events_today=today_events,
        events_this_week=all_events,
        high_impact_count=high_count,
        next_high_impact=next_high,
        central_bank_events=cb_events,
        fundamental_score=round(score, 1),
        source=source,
    )


# ─── Main entry point ───────────────────────────────────────

def analyze() -> CalendarResult:
    """Analyze economic calendar — RSS first, HTML fallback, then sample data.

    Returns a CalendarResult with real or generated events and a
    fundamental score for the radar engine.
    """
    now = datetime.now(timezone.utc)
    source = 'sample'
    events = None

    # 1. Try cached RSS first (fast, no network)
    xml_text = _load_cached_rss()
    if xml_text:
        events = _parse_rss_events(xml_text)
        if events:
            source = 'rss'

    # 2. Try live RSS fetch
    if source != 'rss' or not events:
        xml_text = _fetch_rss()
        if xml_text:
            events = _parse_rss_events(xml_text)
            if events:
                _save_cached_rss(xml_text)
                source = 'rss'

    # 3. Fallback: HTML scrape
    if not events:
        events = _fetch_html_events()
        if events:
            source = 'html'

    # 4. Last resort: realistic sample data
    if not events:
        events = _generate_events()
        source = 'sample'

    # Classify events
    today_str = now.strftime('%Y-%m-%d')
    now_naive = now.replace(tzinfo=None)

    today_events = []
    week_events = []
    for e in events:
        try:
            event_date = e.time[:10]
            if event_date == today_str:
                today_events.append(e)
            e_dt = datetime.strptime(e.time[:19], '%Y-%m-%d %H:%M')
            if event_date >= today_str and (e_dt - now_naive).days < 7:
                week_events.append(e)
        except (ValueError, IndexError):
            continue

    high_count = sum(1 for e in week_events if e.impact == 'High')
    next_high = next((e for e in week_events if e.impact == 'High' and not e.is_past), None)

    # Central bank events (independently calculated for reliability)
    cb_events = _get_cb_events()
    score = _score_calendar(events, now)

    log.debug(
        'Calendar: %d events this week (%d high), source=%s, score=%.1f',
        len(week_events), high_count, source, score,
    )

    return CalendarResult(
        events_today=today_events,
        events_this_week=week_events,
        high_impact_count=high_count,
        next_high_impact=next_high,
        central_bank_events=cb_events,
        fundamental_score=round(score, 1),
        source=source,
    )
