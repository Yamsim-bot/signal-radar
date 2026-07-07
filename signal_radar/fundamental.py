"""Fundamental Analysis — CB stance, rate differentials, COT reports, risk sentiment."""

from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Optional
import re


@dataclass
class CentralBankStance:
    currency: str
    bank_name: str
    current_rate: float          # current interest rate in %
    rate_trend: str              # 'tightening', 'holding', 'easing', 'unknown'
    stance_score: int            # -100 (very dovish) to +100 (very hawkish)
    next_meeting: str            # approximate next meeting date
    meetings_this_year: int = 8  # typical meeting count


@dataclass
class COTData:
    symbol: str
    currency: str
    commercial_long: int
    commercial_short: int
    speculative_long: int
    speculative_short: int
    net_commercial: int          # long - short
    net_speculative: int         # long - short
    extreme_ratio: float         # speculative / commercial ratio
    signal: str                  # 'bullish', 'bearish', 'neutral', 'extreme'


@dataclass
class FundamentalBreakdown:
    """Per-instrument fundamental factor scores -100 to +100 each."""
    growth: float          # GDP, economic expansion/contraction
    inflation: float       # CPI, PPI, wage pressures
    jobs: float            # Employment, NFP, unemployment
    sentiment: float       # Consumer/business confidence, market mood
    trend: float           # Technical trend overlay
    seasonality: float     # Historical seasonal patterns
    total: float           # Weighted sum of all factors


@dataclass
class FundamentalResult:
    overall_score: float                      # -100 to +100
    central_bank_stances: list[CentralBankStance]
    rate_differentials: dict[str, float]       # 'EURUSD': 2.5 (positive = base higher)
    cot_data: list[COTData]
    risk_sentiment: str                        # 'risk_on', 'risk_off', 'neutral'
    risk_score: float                          # -100 to +100
    breakdowns: dict[str, FundamentalBreakdown]  # per-symbol breakdown
    top_bullish: list[str]                     # top 3-5 bullish instruments
    top_bearish: list[str]                     # top 3-5 bearish instruments


# Current central bank rates (approximate, updated quarterly)
CB_RATES = {
    'USD': {'bank': 'Fed', 'rate': 4.50, 'trend': 'holding'},
    'EUR': {'bank': 'ECB', 'rate': 3.25, 'trend': 'easing'},
    'GBP': {'bank': 'BOE', 'rate': 4.75, 'trend': 'holding'},
    'JPY': {'bank': 'BOJ', 'rate': 0.50, 'trend': 'tightening'},
    'AUD': {'bank': 'RBA', 'rate': 4.35, 'trend': 'holding'},
    'NZD': {'bank': 'RBNZ', 'rate': 5.00, 'trend': 'holding'},
    'CAD': {'bank': 'BOC', 'rate': 4.25, 'trend': 'easing'},
    'CHF': {'bank': 'SNB', 'rate': 1.25, 'trend': 'easing'},
}

# Approximate next meeting months (simplified schedule)
CB_MEETINGS = {
    'USD': ['Jan', 'Mar', 'May', 'Jun', 'Jul', 'Sep', 'Nov', 'Dec'],
    'EUR': ['Jan', 'Mar', 'Apr', 'Jun', 'Jul', 'Sep', 'Oct', 'Dec'],
    'GBP': ['Feb', 'Mar', 'May', 'Jun', 'Aug', 'Sep', 'Nov', 'Dec'],
    'JPY': ['Jan', 'Mar', 'Apr', 'Jun', 'Jul', 'Sep', 'Oct', 'Dec'],
    'AUD': ['Feb', 'Mar', 'May', 'Jun', 'Aug', 'Sep', 'Nov', 'Dec'],
    'NZD': ['Feb', 'Apr', 'May', 'Jul', 'Aug', 'Oct', 'Nov'],
    'CAD': ['Jan', 'Mar', 'Apr', 'Jun', 'Jul', 'Sep', 'Oct', 'Dec'],
    'CHF': ['Mar', 'Jun', 'Sep', 'Dec'],
}

# Trend → score mapping
TREND_SCORES = {'tightening': 60, 'holding': 0, 'easing': -60, 'unknown': 0}

# Seasonal bias per currency by month (+1 bullish, -1 bearish, 0 neutral)
SEASONALITY = {
    'USD': {1: -1, 2: -1, 3: 0, 4: 0, 5: 1, 6: 1, 7: 1, 8: 1, 9: 0, 10: 0, 11: -1, 12: -1},
    'EUR': {1: 1, 2: 1, 3: 0, 4: 0, 5: -1, 6: -1, 7: 0, 8: 0, 9: 0, 10: 0, 11: 1, 12: 1},
    'GBP': {1: 0, 2: 0, 3: 1, 4: 1, 5: 0, 6: 0, 7: -1, 8: -1, 9: 0, 10: 1, 11: 1, 12: 0},
    'JPY': {1: 1, 2: 1, 3: 1, 4: 0, 5: 0, 6: -1, 7: -1, 8: 0, 9: 0, 10: 0, 11: -1, 12: -1},
    'AUD': {1: 0, 2: 0, 3: 1, 4: 1, 5: 1, 6: 0, 7: 0, 8: -1, 9: -1, 10: 0, 11: 0, 12: 0},
    'NZD': {1: 0, 2: -1, 3: 1, 4: 1, 5: 0, 6: 0, 7: -1, 8: -1, 9: 0, 10: 0, 11: 1, 12: 1},
    'CAD': {1: -1, 2: -1, 3: 0, 4: 0, 5: 1, 6: 1, 7: 1, 8: 0, 9: 0, 10: -1, 11: -1, 12: 0},
    'CHF': {1: 1, 2: 1, 3: 0, 4: 0, 5: -1, 6: -1, 7: -1, 8: 0, 9: 1, 10: 1, 11: 0, 12: 0},
}

# Stock/Index seasonality by month
STOCK_SEASONALITY = {
    1: 1, 2: 1, 3: 0, 4: -1, 5: 0, 6: -1,
    7: 1, 8: 0, 9: -1, 10: 0, 11: 1, 12: 1,  # Santa rally Nov-Dec
}


def analyze(now: Optional[datetime] = None, quick: bool = False) -> FundamentalResult:
    """Complete fundamental analysis across all instruments.

    Args:
        now: Optional timestamp override.
        quick: If True, skip live COT fetching for instant results.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Central bank stances
    cb_stances = _get_cb_stances(now)

    # Rate differentials for major pairs
    rate_diffs = _rate_differentials(cb_stances)

    # COT data — skip live fetch in quick mode
    cot_data = _generate_sample_cot() if quick else (_get_cot_data() or _generate_sample_cot())

    # Risk sentiment
    risk_score, risk_sent = _risk_sentiment(cot_data, cb_stances)

    # Forecast breakdowns for all instruments
    from .instruments import INSTRUMENTS as ALL_INSTRS, get_symbols
    breakdowns = {}
    for sym in get_symbols():
        breakdowns[sym] = _compute_breakdown(sym, cb_stances, cot_data, risk_score, now)

    # Overall score — blend CB stance + COT + risk
    overall = _overall_fundamental_score(cb_stances, cot_data, risk_score)

    # Top bullish/bearish from combined analysis
    top_bull, top_bear = _top_bias(cot_data, rate_diffs)

    return FundamentalResult(
        overall_score=round(overall, 1),
        central_bank_stances=cb_stances,
        rate_differentials=rate_diffs,
        cot_data=cot_data,
        risk_sentiment=risk_sent,
        risk_score=round(risk_score, 1),
        breakdowns=breakdowns,
        top_bullish=top_bull,
        top_bearish=top_bear,
    )


def _get_cb_stances(now: datetime) -> list[CentralBankStance]:
    """Build central bank stance list."""
    stances = []
    month_names = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    current_month_name = month_names[now.month]

    for currency, info in CB_RATES.items():
        meetings = CB_MEETINGS[currency]
        trend = info['trend']
        base_score = TREND_SCORES.get(trend, 0)

        # Adjust score based on rate level (higher rates = more hawkish potential)
        rate = info['rate']
        if rate >= 5.0:
            rate_adj = 20
        elif rate >= 3.0:
            rate_adj = 10
        elif rate >= 1.0:
            rate_adj = 0
        else:
            rate_adj = -20  # near zero rates = dovish

        # Find next meeting
        next_mtg = _next_meeting(meetings, current_month_name, month_names)

        stances.append(CentralBankStance(
            currency=currency,
            bank_name=info['bank'],
            current_rate=rate,
            rate_trend=trend,
            stance_score=max(-100, min(100, base_score + rate_adj)),
            next_meeting=next_mtg,
        ))

    return stances


def _next_meeting(meetings: list[str], current_month: str, month_names: list[str]) -> str:
    """Find next meeting date string."""
    current_idx = month_names.index(current_month) if current_month in month_names else 0
    for mtg in meetings:
        mtg_idx = month_names.index(mtg) if mtg in month_names else 0
        if mtg_idx >= current_idx:
            return f'{mtg} 2026'
    return f'{meetings[0]} 2027'


def _rate_differentials(stances: list[CentralBankStance]) -> dict[str, float]:
    """Calculate interest rate differentials for all pairs."""
    rate_map = {s.currency: s.current_rate for s in stances}

    pairs = [
        'EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'USDCAD', 'AUDUSD', 'NZDUSD',
        'GBPJPY', 'EURJPY', 'EURGBP', 'EURCHF', 'AUDJPY', 'CHFJPY', 'NZDJPY',
        'GBPAUD', 'EURAUD',
    ]

    diff = {}
    for pair in pairs:
        base = pair[:3]
        quote = pair[3:]
        if base in rate_map and quote in rate_map:
            diff[pair] = round(rate_map[base] - rate_map[quote], 2)
        else:
            diff[pair] = 0.0

    return diff


def _get_cot_data() -> list[COTData]:
    """Try to fetch real COT data from CFTC."""
    try:
        import requests
        from bs4 import BeautifulSoup
        # CFTC legacy report URL
        resp = requests.get(
            'https://www.cftc.gov/dea/futures/deacmxsf.htm',
            timeout=10,
            headers={'User-Agent': 'Mozilla/5.0'},
        )
        if resp.status_code == 200:
            return _parse_cftc_report(resp.text)
    except Exception:
        pass
    return []


def _parse_cftc_report(html: str) -> list[COTData]:
    """Parse CFTC legacy COT report HTML."""
    cot_list = []
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        # Look for the pre-formatted text / table
        rows = soup.select('pre') or soup.select('table')
        # This is a simplified parser — CFTC format is notoriously dense
        for row in rows[:50]:
            text = row.get_text()
            # Look for currency futures patterns
            for currency, pattern in [('EUR', 'EURO FX'), ('GBP', 'BRITISH POUND'),
                                       ('JPY', 'JAPANESE YEN'), ('CHF', 'SWISS FRANC'),
                                       ('AUD', 'AUSTRALIAN DOLLAR'), ('CAD', 'CANADIAN DOLLAR'),
                                       ('NZD', 'NEW ZEALAND DOLLAR'), ('USD', 'US DOLLAR')]:
                if pattern in text:
                    # Extract numbers from the line
                    nums = re.findall(r'[\d,]+', text)
                    if len(nums) >= 4:
                        cot_list.append(COTData(
                            symbol=pattern[:3],
                            currency=currency,
                            commercial_long=int(nums[0].replace(',', '')),
                            commercial_short=int(nums[1].replace(',', '')),
                            speculative_long=int(nums[2].replace(',', '')),
                            speculative_short=int(nums[3].replace(',', '')),
                            net_commercial=int(nums[0].replace(',', '')) - int(nums[1].replace(',', '')),
                            net_speculative=int(nums[2].replace(',', '')) - int(nums[3].replace(',', '')),
                            extreme_ratio=0.0,
                            signal='neutral',
                        ))
    except Exception:
        pass
    return cot_list


def _generate_sample_cot() -> list[COTData]:
    """Generate sample COT data for development."""
    sample = [
        COTData('EURUSD', 'EUR', 180000, 120000, 90000, 150000,
                60000, -60000, 1.1, 'neutral'),
        COTData('GBPUSD', 'GBP', 75000, 65000, 55000, 70000,
                10000, -15000, 0.8, 'neutral'),
        COTData('USDJPY', 'JPY', 140000, 100000, 80000, 120000,
                40000, -40000, 0.9, 'neutral'),
        COTData('USDCHF', 'CHF', 30000, 25000, 15000, 28000,
                5000, -13000, 0.7, 'neutral'),
        COTData('AUDUSD', 'AUD', 60000, 50000, 35000, 55000,
                10000, -20000, 0.6, 'neutral'),
        COTData('USDCAD', 'CAD', 80000, 70000, 45000, 75000,
                10000, -30000, 0.5, 'bearish'),
        COTData('NZDUSD', 'NZD', 20000, 18000, 12000, 22000,
                2000, -10000, 0.4, 'neutral'),
    ]

    # Calculate extreme ratios and signals
    for cot in sample:
        total_commercial = abs(cot.commercial_long) + abs(cot.commercial_short)
        total_speculative = abs(cot.speculative_long) + abs(cot.speculative_short)
        cot.extreme_ratio = round(
            total_speculative / max(total_commercial, 1), 2
        )

        # Signal logic:
        # Commercial net long = bullish (smart money buying)
        # Speculative net short with commercial net long = contrarian bullish
        if cot.net_commercial > 0 and cot.net_speculative < 0:
            if abs(cot.net_commercial) > 50000:
                cot.signal = 'bullish'
            elif abs(cot.net_commercial) > 20000:
                cot.signal = 'neutral'
        elif cot.net_commercial < 0 and cot.net_speculative > 0:
            if abs(cot.net_commercial) > 50000:
                cot.signal = 'bearish'
            elif abs(cot.net_commercial) > 20000:
                cot.signal = 'neutral'
        else:
            cot.signal = 'neutral'

        # Extreme positioning (speculative crowded)
        if cot.extreme_ratio > 2.0:
            cot.signal = 'extreme_' + cot.signal if cot.signal != 'neutral' else 'extreme'

    return sample


def _risk_sentiment(cot_data: list[COTData],
                    cb_stances: list[CentralBankStance]) -> tuple[float, str]:
    """Determine overall risk sentiment from COT + CB data."""
    if not cot_data:
        return 0.0, 'neutral'

    risk_score = 0.0

    # COT-based risk: if speculators heavily short = risk-off, long = risk-on
    for cot in cot_data:
        if 'JPY' in cot.currency or 'CHF' in cot.currency:
            # Safe-haven currencies — speculative short = risk-on
            if cot.net_speculative < -30000:
                risk_score -= 10  # short safe havens = risk on
            elif cot.net_speculative > 30000:
                risk_score += 10  # long safe havens = risk off

    # CB stance contribution
    tightening_count = sum(1 for cb in cb_stances if cb.rate_trend == 'tightening')
    easing_count = sum(1 for cb in cb_stances if cb.rate_trend == 'easing')

    if tightening_count > easing_count:
        risk_score -= 5  # global tightening = cautious
    elif easing_count > tightening_count:
        risk_score += 5  # global easing = risk-on

    # Clamp and label
    risk_score = max(-100, min(100, risk_score))
    if risk_score > 20:
        sentiment = 'risk_on'
    elif risk_score < -20:
        sentiment = 'risk_off'
    else:
        sentiment = 'neutral'

    return risk_score, sentiment


def _overall_fundamental_score(cb_stances: list[CentralBankStance],
                                cot_data: list[COTData],
                                risk_score: float) -> float:
    """Blend all fundamental components into one -100 to +100 score."""
    score = 0.0

    # CB stances (40% weight)
    if cb_stances:
        cb_avg = sum(s.stance_score for s in cb_stances) / len(cb_stances)
        score += cb_avg * 0.4

    # COT (40% weight)
    if cot_data:
        cot_signals = {'bullish': 30, 'bearish': -30, 'neutral': 0, 'extreme': -50}
        cot_avg = sum(cot_signals.get(c.signal, 0) for c in cot_data) / len(cot_data)
        score += cot_avg * 0.4

    # Risk sentiment (20% weight)
    score += risk_score * 0.2

    return float(max(-100, min(100, score)))


def _compute_breakdown(symbol: str, cb_stances, cot_data, risk_score: float,
                       now: datetime) -> FundamentalBreakdown:
    """Compute per-instrument fundamental breakdown across 6 factors."""
    from .instruments import INSTRUMENTS as ALL_INSTRUMENTS
    instr = ALL_INSTRUMENTS.get(symbol, {})
    cat = instr.get('category', 'major')

    # Parse base/quote currencies
    base = symbol[:3]
    quote = symbol[3:] if len(symbol) == 6 else None

    cb_base = next((cb for cb in cb_stances if cb.currency == base), None)
    cb_quote = next((cb for cb in cb_stances if cb.currency == quote), None) if quote else None

    month = now.month

    def _sign(x):
        return 1 if x > 0 else (-1 if x < 0 else 0)

    def _trend_to_score(trend: str) -> float:
        return {'tightening': 30, 'holding': 0, 'easing': -30}.get(trend, 0)

    # ═══════════════ GROWTH ═══════════════
    growth = 0.0
    if cb_base:
        growth += _trend_to_score(cb_base.rate_trend)  # tightening = expansion
    if cb_quote:
        growth -= _trend_to_score(cb_quote.rate_trend)  # quote tightening = headwind
    # Stocks/indices: rate environment affects growth outlook
    if cat in ('stock', 'index'):
        growth += 10  # general economic growth bias for equities
    growth = float(max(-100, min(100, growth)))

    # ═══════════════ INFLATION ═══════════════
    inflation = 0.0
    if cb_base:
        rate = cb_base.current_rate
        if rate >= 5.0:
            inflation += 20  # high inflation / hawkish
        elif rate >= 3.0:
            inflation += 10
        elif rate <= 0.5:
            inflation -= 15  # near zero = deflation risk / dovish
    if cb_quote:
        q_rate = cb_quote.current_rate
        if q_rate >= 5.0:
            inflation -= 20
        elif q_rate >= 3.0:
            inflation -= 10
    if cat in ('commodity',):
        inflation += 15  # commodities benefit from inflation
    if cat in ('stock',):
        inflation -= 10  # high inflation hurts stocks
    inflation = float(max(-100, min(100, inflation)))

    # ═══════════════ JOBS ═══════════════
    jobs = 0.0
    if cb_base:
        # Tightening = strong jobs market
        if cb_base.rate_trend == 'tightening':
            jobs += 25
        elif cb_base.rate_trend == 'easing':
            jobs -= 25
        # Higher rates usually correlate with strong employment
        if cb_base.current_rate >= 4.0:
            jobs += 10
        elif cb_base.current_rate <= 1.0:
            jobs -= 10
    if cb_quote:
        if cb_quote.rate_trend == 'tightening':
            jobs -= 15
        elif cb_quote.rate_trend == 'easing':
            jobs += 15
    jobs = float(max(-100, min(100, jobs)))

    # ═══════════════ SENTIMENT ═══════════════
    sent = risk_score * 0.5  # from the risk sentiment analysis
    # COT adjustment
    cot_base = next((c for c in cot_data if c.currency == base), None)
    if cot_base:
        if cot_base.signal == 'bullish':
            sent += 20
        elif cot_base.signal == 'bearish':
            sent -= 20
    if quote:
        cot_quote = next((c for c in cot_data if c.currency == quote), None)
        if cot_quote:
            if cot_quote.signal == 'bullish':
                sent -= 10
            elif cot_quote.signal == 'bearish':
                sent += 10
    # Safe-haven adjustment
    if base in ('JPY', 'CHF', 'XAU', 'XAG'):
        if risk_score > 20:
            sent -= 15  # risk-on = safe havens weak
        elif risk_score < -20:
            sent += 15  # risk-off = safe havens strong
    sent = float(max(-100, min(100, sent)))

    # ═══════════════ TREND ═══════════════
    trend_factor = 0.0
    if cb_base and cb_quote:
        # Base stronger stance = bullish trend
        stance_diff = cb_base.stance_score - cb_quote.stance_score
        trend_factor = stance_diff * 0.3
    elif cb_base and cat in ('stock', 'index'):
        trend_factor = cb_base.stance_score * 0.2  # base CB affects equity trend
    trend_factor = float(max(-100, min(100, trend_factor)))

    # ═══════════════ SEASONALITY ═══════════════
    seasonal = 0.0
    if cat in ('major', 'cross') and cb_base:
        base_ss = SEASONALITY.get(cb_base.currency, {}).get(month, 0)
        seasonal += base_ss * 15
    if cat in ('major', 'cross') and cb_quote:
        quote_ss = SEASONALITY.get(cb_quote.currency, {}).get(month, 0)
        seasonal -= quote_ss * 12
    if cat in ('stock', 'index'):
        seasonal += STOCK_SEASONALITY.get(month, 0) * 15
    seasonal = float(max(-100, min(100, seasonal)))

    # ═══════════════ TOTAL ═══════════════
    total = (growth * 0.20 + inflation * 0.15 + jobs * 0.15
             + sent * 0.20 + trend_factor * 0.15 + seasonal * 0.15)
    total = float(max(-100, min(100, total)))

    return FundamentalBreakdown(
        growth=round(growth, 1),
        inflation=round(inflation, 1),
        jobs=round(jobs, 1),
        sentiment=round(sent, 1),
        trend=round(trend_factor, 1),
        seasonality=round(seasonal, 1),
        total=round(total, 1),
    )


def _top_bias(cot_data: list[COTData],
              rate_differentials: dict[str, float]) -> tuple[list[str], list[str]]:
    """Identify top fundamentally bullish/bearish instruments."""
    bullish = []
    bearish = []

    for pair, diff in rate_differentials.items():
        base = pair[:3]
        quote = pair[3:]

        # Find COT for base currency
        cot_base = next((c for c in cot_data if c.currency == base), None)
        cot_quote = next((c for c in cot_data if c.currency == quote), None)

        score = 0.0
        # Rate differential
        score += diff * 5  # positive = bullish for base

        # COT
        if cot_base and cot_base.signal == 'bullish':
            score += 15
        elif cot_base and cot_base.signal == 'bearish':
            score -= 15

        if cot_quote and cot_quote.signal == 'bullish':
            score -= 10  # bullish quote = bearish for base
        elif cot_quote and cot_quote.signal == 'bearish':
            score += 10

        if score > 20:
            bullish.append(pair)
        elif score < -20:
            bearish.append(pair)

    return bullish[:5], bearish[:5]
