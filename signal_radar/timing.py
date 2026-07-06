"""Timing of Entry — session analysis, optimal windows, news blackout."""

from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Optional

from .instruments import INSTRUMENTS


@dataclass
class TimingResult:
    current_session: str               # 'London', 'NY', 'Tokyo', 'Sydney', 'Asian', 'Pacific', 'off_hours'
    session_quality: str               # 'prime', 'active', 'quiet', 'closed'
    hours_to_next_session: float
    entry_timing: str                  # 'now', 'soon', 'wait', 'avoid'
    timing_score: int                  # 0-100
    next_news_blackout: Optional[str]  # nearest news event
    in_blackout: bool                  # currently in blackout


# Major news event times (GMT) — typical high-impact releases
NEWS_EVENTS = [
    ('USD', (13, 30), 'US NFP/CPI/PCE'),   # US data
    ('USD', (14, 0), 'US ISM/Factory'),    # US data alt
    ('GBP', (7, 0), 'UK data'),            # UK data
    ('GBP', (12, 0), 'BOE'),              # BOE
    ('EUR', (7, 0), 'German data'),        # German data
    ('EUR', (12, 0), 'ECB'),              # ECB
    ('JPY', (0, 50), 'Japan data'),       # Japan data
    ('AUD', (0, 30), 'RBA'),             # RBA
    ('NZD', (22, 0), 'RBNZ'),           # RBNZ
    ('CAD', (13, 30), 'Canada data'),   # Canada data
    ('CHF', (7, 0), 'SNB'),            # SNB
]

SESSION_NAMES = {
    'Sydney': (22, 7),    # 22:00 - 07:00 GMT
    'Tokyo': (0, 9),      # 00:00 - 09:00 GMT
    'London': (8, 17),    # 08:00 - 17:00 GMT
    'NY': (13, 22),       # 13:00 - 22:00 GMT
}

SESSION_QUALITY = {
    'London_NY_overlap': 'prime',      # 13:00-17:00 GMT — highest vol
    'London_only': 'active',           # 8:00-13:00 GMT — good vol
    'NY_only': 'active',               # 17:00-22:00 GMT — moderate vol
    'Tokyo_London_overlap': 'active',  # 8:00-9:00 GMT
    'Tokyo_only': 'quiet',             # 0:00-8:00 GMT
    'Sydney_only': 'quiet',            # 22:00-0:00 GMT
    'off_hours': 'closed',             # 22:00-0:00 GMT for most
}


def analyze(time: Optional[datetime] = None) -> TimingResult:
    """Analyze current timing for entry quality."""
    if time is None:
        time = datetime.now(timezone.utc)

    hour = time.hour
    minute = time.minute
    total_min = hour * 60 + minute

    # Current session
    current_session = _current_session(hour)
    quality = _session_quality(hour)

    # News blackout
    in_bo, next_news = _news_blackout(hour, minute)

    # Entry timing
    if quality == 'prime' and not in_bo:
        entry_timing = 'now'
        timing_score = 90
    elif quality == 'active' and not in_bo:
        entry_timing = 'soon'
        timing_score = 70
    elif quality == 'quiet' or in_bo:
        entry_timing = 'wait'
        timing_score = 40
    else:
        entry_timing = 'avoid'
        timing_score = 10

    # Hours to next session
    next_sesh = _next_session(hour)

    return TimingResult(
        current_session=current_session,
        session_quality=quality,
        hours_to_next_session=next_sesh,
        entry_timing=entry_timing,
        timing_score=timing_score,
        next_news_blackout=next_news,
        in_blackout=in_bo,
    )


def _current_session(hour: int) -> str:
    sessions = []
    if 0 <= hour < 9:
        sessions.append('Tokyo')
    if 8 <= hour < 17:
        sessions.append('London')
    if 13 <= hour < 22:
        sessions.append('NY')
    if 22 <= hour or hour < 7:
        sessions.append('Sydney')

    if not sessions:
        return 'off_hours'
    if len(sessions) > 1:
        return '_'.join(sessions)
    return sessions[0]


def _session_quality(hour: int) -> str:
    if 13 <= hour < 17:
        return 'prime'     # London + NY overlap
    if 8 <= hour < 13:
        return 'active'    # London only
    if 17 <= hour < 22:
        return 'active'    # NY only
    if 0 <= hour < 8:
        return 'quiet'     # Asian session
    return 'closed'


def _news_blackout(hour: int, minute: int) -> tuple[bool, Optional[str]]:
    """Check if we're in a news blackout window. Also return next event."""
    total_min = hour * 60 + minute
    nearest = None
    nearest_dist = float('inf')

    for curr, (ev_h, ev_m), name in NEWS_EVENTS:
        ev_min = ev_h * 60 + ev_m
        start = ev_min - 45
        end = ev_min + 15

        if start <= total_min <= end:
            return True, f'{name} (in progress)'

        # Distance
        dist = min(abs(total_min - start), abs(total_min - end))
        if dist < nearest_dist:
            nearest_dist = dist
            nearest = f'{name} in {dist:.0f}m'

    return False, nearest


def _next_session(hour: int) -> float:
    """Hours until the next major session opens."""
    if hour < 8:
        return 8 - hour
    elif hour < 13:
        return 13 - hour
    elif hour < 22:
        return 22 - hour
    else:
        return (24 - hour) + 8
