"""Economic Calendar — ForexFactory-style events, central bank schedule."""

from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Optional
import re


@dataclass
class CalendarEvent:
    time: str
    currency: str
    event: str
    impact: str          # 'High', 'Medium', 'Low'
    actual: Optional[str]
    forecast: Optional[str]
    previous: Optional[str]
    is_past: bool


@dataclass
class CalendarResult:
    events_today: list[CalendarEvent]
    events_this_week: list[CalendarEvent]
    high_impact_count: int
    next_high_impact: Optional[CalendarEvent]
    central_bank_events: list[CalendarEvent]
    fundamental_score: float   # -100 (bearish) to +100 (bullish) based on calendar


# Central bank meeting schedule (approximate)
CENTRAL_BANK_SCHEDULE = [
    # (month, week_of_month, currency, bank, description)
    (1, 'weekly', 'USD', 'Fed', 'FOMC Rate Decision'),
    (1, 'weekly', 'EUR', 'ECB', 'ECB Rate Decision'),
    (1, 'weekly', 'GBP', 'BOE', 'BOE Rate Decision'),
    (1, 'weekly', 'JPY', 'BOJ', 'BOJ Rate Decision'),
    (1, 'weekly', 'AUD', 'RBA', 'RBA Rate Decision'),
    (1, 'weekly', 'NZD', 'RBNZ', 'RBNZ Rate Decision'),
    (1, 'weekly', 'CAD', 'BOC', 'BOC Rate Decision'),
    (1, 'weekly', 'CHF', 'SNB', 'SNB Rate Decision'),
]


def analyze() -> CalendarResult:
    """Analyze economic calendar and produce a fundamental score."""
    now = datetime.now(timezone.utc)
    today = now.strftime('%Y-%m-%d')
    now_naive = now.replace(tzinfo=None)

    # Try to fetch real events from ForexFactory via WebFetch
    events = _fetch_events()
    if not events:
        events = _generate_sample_events()

    today_events = [e for e in events if e.time[:10] == today[:10]]
    week_events = [e for e in events if e.time[:10] >= today[:10] and (datetime.fromisoformat(e.time[:19]) - now_naive).days < 7]
    high_count = sum(1 for e in week_events if e.impact == 'High')
    next_high = next((e for e in week_events if e.impact == 'High' and not e.is_past), None)

    # CB events
    cb_events = _get_cb_events()

    # Score
    score = _score_calendar(events, now_naive)

    return CalendarResult(
        events_today=today_events,
        events_this_week=week_events,
        high_impact_count=high_count,
        next_high_impact=next_high,
        central_bank_events=cb_events,
        fundamental_score=round(score, 1),
    )


def _fetch_events() -> list[CalendarEvent]:
    """Attempt to fetch real calendar events. Returns empty list on failure."""
    try:
        import requests
        from requests.exceptions import RequestException
        # Try ForexFactory RSS
        resp = requests.get('https://www.forexfactory.com/calendar?day=today', timeout=5)
        if resp.status_code == 200:
            return _parse_forexfactory(resp.text)
    except Exception:
        pass
    return []


def _parse_forexfactory(html: str) -> list[CalendarEvent]:
    """Parse ForexFactory calendar HTML."""
    events = []
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        rows = soup.select('tr.calendar_row')
        for row in rows[:30]:
            cells = row.find_all('td')
            if len(cells) >= 6:
                time_str = cells[0].get_text(strip=True)
                currency = cells[1].get_text(strip=True)
                event_name = cells[2].get_text(strip=True)
                impact = cells[3].get('class', ['Low'])[0] if cells[3].get('class') else 'Low'
                actual = cells[4].get_text(strip=True) or None
                forecast = cells[5].get_text(strip=True) or None
                prev = cells[6].get_text(strip=True) if len(cells) > 6 else None

                if 'High' in str(impact):
                    impact_str = 'High'
                elif 'Medium' in str(impact) or 'Moderate' in str(impact):
                    impact_str = 'Medium'
                else:
                    impact_str = 'Low'

                events.append(CalendarEvent(
                    time=time_str, currency=currency, event=event_name,
                    impact=impact_str, actual=actual,
                    forecast=forecast, previous=prev,
                    is_past=bool(actual),
                ))
    except Exception:
        pass
    return events


def _generate_sample_events() -> list[CalendarEvent]:
    """Generate sample calendar events for development."""
    now = datetime.now(timezone.utc)
    events = []
    sample = [
        (now.strftime('%Y-%m-%d') + ' 13:30', 'USD', 'Initial Jobless Claims', 'Medium'),
        (now.strftime('%Y-%m-%d') + ' 14:00', 'USD', 'Consumer Sentiment', 'Medium'),
        ((now + timedelta(days=1)).strftime('%Y-%m-%d') + ' 07:00', 'EUR', 'German Factory Orders', 'High'),
        ((now + timedelta(days=1)).strftime('%Y-%m-%d') + ' 13:30', 'USD', 'Core CPI (MoM)', 'High'),
        ((now + timedelta(days=1)).strftime('%Y-%m-%d') + ' 13:30', 'USD', 'Retail Sales (MoM)', 'High'),
        ((now + timedelta(days=2)).strftime('%Y-%m-%d') + ' 12:00', 'GBP', 'BOE Interest Rate Decision', 'High'),
        ((now + timedelta(days=2)).strftime('%Y-%m-%d') + ' 07:00', 'GBP', 'GDP (MoM)', 'Medium'),
        ((now + timedelta(days=3)).strftime('%Y-%m-%d') + ' 00:30', 'JPY', 'National CPI (YoY)', 'High'),
        ((now + timedelta(days=3)).strftime('%Y-%m-%d') + ' 22:00', 'NZD', 'RBNZ Rate Decision', 'High'),
        ((now + timedelta(days=4)).strftime('%Y-%m-%d') + ' 13:30', 'USD', 'Nonfarm Payrolls', 'High'),
        ((now + timedelta(days=4)).strftime('%Y-%m-%d') + ' 13:30', 'USD', 'Unemployment Rate', 'High'),
        ((now + timedelta(days=4)).strftime('%Y-%m-%d') + ' 07:00', 'EUR', 'ECB President Speech', 'Medium'),
    ]
    for t, cur, ev, imp in sample:
        events.append(CalendarEvent(
            time=t, currency=cur, event=ev, impact=imp,
            actual=None, forecast=None, previous=None, is_past=False,
        ))
    return events


def _get_cb_events() -> list[CalendarEvent]:
    """Get upcoming central bank meetings from schedule."""
    events = []
    now = datetime.now(timezone.utc)
    curr_month = now.month
    next_month = (curr_month % 12) + 1

    cb_map = {'USD': 'Fed', 'EUR': 'ECB', 'GBP': 'BOE', 'JPY': 'BOJ',
              'AUD': 'RBA', 'NZD': 'RBNZ', 'CAD': 'BOC', 'CHF': 'SNB'}

    for month, _, currency, bank, desc in CENTRAL_BANK_SCHEDULE:
        if month == curr_month or month == next_month:
            # Approx: 3rd week of month for most CBs
            date_str = f'2026-{month:02d}-15 {12:02d}:00'
            events.append(CalendarEvent(
                time=date_str, currency=currency, event=f'{bank} {desc}',
                impact='High', actual=None, forecast=None, previous=None, is_past=False,
            ))
    return events


def _score_calendar(events: list[CalendarEvent], now: datetime) -> float:
    """Score upcoming events for market sentiment bias."""
    if not events:
        return 0.0

    score = 0.0
    for e in events:
        if e.is_past:
            continue
        # High impact events increase uncertainty / volatility
        if e.impact == 'High':
            score -= 5
        elif e.impact == 'Medium':
            score -= 2
        # Central bank events
        if 'rate decision' in e.event.lower():
            score -= 10  # uncertainty ahead of rate decisions

    # Range bound: -50 to +50 typically
    return float(max(-100, min(100, score)))
