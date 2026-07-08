"""Instrument database — 28+ symbols with specs for Vantage Cent Raw ECN."""

from dataclasses import dataclass, field
from typing import Optional

INSTRUMENTS = {
    # ═══════ MAJORS ═══════
    'EURUSD': {
        'category': 'major', 'pip_factor': 0.0001, 'contract_size': 100000,
        'digits': 5, 'sessions': {'London': (8, 17), 'NY': (13, 22)},
        'typical_spread': 0.2, 'margin_pct': 0.2,
        'description': 'Euro / US Dollar', 'currency': 'USD',
    },
    'GBPUSD': {
        'category': 'major', 'pip_factor': 0.0001, 'contract_size': 100000,
        'digits': 5, 'sessions': {'London': (8, 17), 'NY': (13, 22)},
        'typical_spread': 0.5, 'margin_pct': 0.2,
        'description': 'British Pound / US Dollar', 'currency': 'USD',
    },
    'USDJPY': {
        'category': 'major', 'pip_factor': 0.01, 'contract_size': 100000,
        'digits': 3, 'sessions': {'Tokyo': (0, 9), 'London': (8, 17), 'NY': (13, 22)},
        'typical_spread': 0.3, 'margin_pct': 0.2,
        'description': 'US Dollar / Japanese Yen', 'currency': 'JPY',
    },
    'USDCHF': {
        'category': 'major', 'pip_factor': 0.0001, 'contract_size': 100000,
        'digits': 5, 'sessions': {'London': (8, 17), 'NY': (13, 22)},
        'typical_spread': 0.5, 'margin_pct': 0.2,
        'description': 'US Dollar / Swiss Franc', 'currency': 'USD',
    },
    'USDCAD': {
        'category': 'major', 'pip_factor': 0.0001, 'contract_size': 100000,
        'digits': 5, 'sessions': {'London': (8, 17), 'NY': (13, 22)},
        'typical_spread': 0.5, 'margin_pct': 0.2,
        'description': 'US Dollar / Canadian Dollar', 'currency': 'USD',
    },
    'AUDUSD': {
        'category': 'major', 'pip_factor': 0.0001, 'contract_size': 100000,
        'digits': 5, 'sessions': {'Sydney': (22, 7), 'Tokyo': (0, 9), 'London': (8, 17)},
        'typical_spread': 0.5, 'margin_pct': 0.2,
        'description': 'Australian Dollar / US Dollar', 'currency': 'USD',
    },
    'NZDUSD': {
        'category': 'major', 'pip_factor': 0.0001, 'contract_size': 100000,
        'digits': 5, 'sessions': {'Sydney': (22, 7), 'Tokyo': (0, 9)},
        'typical_spread': 0.7, 'margin_pct': 0.2,
        'description': 'New Zealand Dollar / US Dollar', 'currency': 'USD',
    },
    # ═══════ CROSSES ═══════
    'GBPJPY': {
        'category': 'cross', 'pip_factor': 0.01, 'contract_size': 100000,
        'digits': 3, 'sessions': {'London': (8, 17), 'NY': (13, 22)},
        'typical_spread': 0.6, 'margin_pct': 0.2,
        'description': 'British Pound / Japanese Yen', 'currency': 'JPY',
    },
    'EURJPY': {
        'category': 'cross', 'pip_factor': 0.01, 'contract_size': 100000,
        'digits': 3, 'sessions': {'London': (8, 17), 'NY': (13, 22)},
        'typical_spread': 0.4, 'margin_pct': 0.2,
        'description': 'Euro / Japanese Yen', 'currency': 'JPY',
    },
    'EURGBP': {
        'category': 'cross', 'pip_factor': 0.0001, 'contract_size': 100000,
        'digits': 5, 'sessions': {'London': (8, 17)},
        'typical_spread': 0.4, 'margin_pct': 0.2,
        'description': 'Euro / British Pound', 'currency': 'GBP',
    },
    'EURCHF': {
        'category': 'cross', 'pip_factor': 0.0001, 'contract_size': 100000,
        'digits': 5, 'sessions': {'London': (8, 17)},
        'typical_spread': 0.5, 'margin_pct': 0.2,
        'description': 'Euro / Swiss Franc', 'currency': 'CHF',
    },
    'AUDJPY': {
        'category': 'cross', 'pip_factor': 0.01, 'contract_size': 100000,
        'digits': 3, 'sessions': {'Sydney': (22, 7), 'Tokyo': (0, 9)},
        'typical_spread': 0.6, 'margin_pct': 0.2,
        'description': 'Australian Dollar / Japanese Yen', 'currency': 'JPY',
    },
    'CHFJPY': {
        'category': 'cross', 'pip_factor': 0.01, 'contract_size': 100000,
        'digits': 3, 'sessions': {'London': (8, 17)},
        'typical_spread': 0.7, 'margin_pct': 0.2,
        'description': 'Swiss Franc / Japanese Yen', 'currency': 'JPY',
    },
    'NZDJPY': {
        'category': 'cross', 'pip_factor': 0.01, 'contract_size': 100000,
        'digits': 3, 'sessions': {'Sydney': (22, 7), 'Tokyo': (0, 9)},
        'typical_spread': 0.8, 'margin_pct': 0.2,
        'description': 'New Zealand Dollar / Japanese Yen', 'currency': 'JPY',
    },
    'GBPAUD': {
        'category': 'cross', 'pip_factor': 0.0001, 'contract_size': 100000,
        'digits': 5, 'sessions': {'London': (8, 17)},
        'typical_spread': 1.0, 'margin_pct': 0.2,
        'description': 'British Pound / Australian Dollar', 'currency': 'AUD',
    },
    'EURAUD': {
        'category': 'cross', 'pip_factor': 0.0001, 'contract_size': 100000,
        'digits': 5, 'sessions': {'London': (8, 17)},
        'typical_spread': 1.0, 'margin_pct': 0.2,
        'description': 'Euro / Australian Dollar', 'currency': 'AUD',
    },
    'AUDNZD': {
        'category': 'cross', 'pip_factor': 0.0001, 'contract_size': 100000,
        'digits': 5, 'sessions': {'Sydney': (22, 7), 'Tokyo': (0, 9)},
        'typical_spread': 1.2, 'margin_pct': 0.2,
        'description': 'Australian Dollar / New Zealand Dollar', 'currency': 'NZD',
    },
    'NZDCAD': {
        'category': 'cross', 'pip_factor': 0.0001, 'contract_size': 100000,
        'digits': 5, 'sessions': {'Sydney': (22, 7), 'NY': (13, 22)},
        'typical_spread': 1.5, 'margin_pct': 0.2,
        'description': 'New Zealand Dollar / Canadian Dollar', 'currency': 'CAD',
    },
    'AUDCAD': {
        'category': 'cross', 'pip_factor': 0.0001, 'contract_size': 100000,
        'digits': 5, 'sessions': {'Sydney': (22, 7), 'NY': (13, 22)},
        'typical_spread': 1.2, 'margin_pct': 0.2,
        'description': 'Australian Dollar / Canadian Dollar', 'currency': 'CAD',
    },
    'GBPCAD': {
        'category': 'cross', 'pip_factor': 0.0001, 'contract_size': 100000,
        'digits': 5, 'sessions': {'London': (8, 17)},
        'typical_spread': 1.4, 'margin_pct': 0.2,
        'description': 'British Pound / Canadian Dollar', 'currency': 'CAD',
    },
    'GBPCHF': {
        'category': 'cross', 'pip_factor': 0.0001, 'contract_size': 100000,
        'digits': 5, 'sessions': {'London': (8, 17)},
        'typical_spread': 1.0, 'margin_pct': 0.2,
        'description': 'British Pound / Swiss Franc', 'currency': 'CHF',
    },
    'EURNZD': {
        'category': 'cross', 'pip_factor': 0.0001, 'contract_size': 100000,
        'digits': 5, 'sessions': {'London': (8, 17)},
        'typical_spread': 1.5, 'margin_pct': 0.2,
        'description': 'Euro / New Zealand Dollar', 'currency': 'NZD',
    },
    'EURCAD': {
        'category': 'cross', 'pip_factor': 0.0001, 'contract_size': 100000,
        'digits': 5, 'sessions': {'London': (8, 17), 'NY': (13, 22)},
        'typical_spread': 1.2, 'margin_pct': 0.2,
        'description': 'Euro / Canadian Dollar', 'currency': 'CAD',
    },
    'CADCHF': {
        'category': 'cross', 'pip_factor': 0.0001, 'contract_size': 100000,
        'digits': 5, 'sessions': {'NY': (13, 22)},
        'typical_spread': 1.0, 'margin_pct': 0.2,
        'description': 'Canadian Dollar / Swiss Franc', 'currency': 'CHF',
    },
    # ═══════ INDICES ═══════
    'US30': {
        'category': 'index', 'pip_factor': 1.0, 'contract_size': 1,
        'digits': 1, 'sessions': {'NY': (13, 22)},
        'typical_spread': 2.0, 'margin_pct': 1.0,
        'description': 'Dow Jones Industrial Average', 'currency': 'USD',
    },
    'SP500': {
        'category': 'index', 'pip_factor': 0.1, 'contract_size': 1,
        'digits': 1, 'sessions': {'NY': (13, 22)},
        'typical_spread': 0.5, 'margin_pct': 1.0,
        'description': 'S&P 500 Index', 'currency': 'USD',
    },
    'NAS100': {
        'category': 'index', 'pip_factor': 0.1, 'contract_size': 1,
        'digits': 1, 'sessions': {'NY': (13, 22)},
        'typical_spread': 0.8, 'margin_pct': 1.0,
        'description': 'NASDAQ 100 Index', 'currency': 'USD',
    },
    'DAX40': {
        'category': 'index', 'pip_factor': 0.1, 'contract_size': 1,
        'digits': 1, 'sessions': {'London': (8, 17)},
        'typical_spread': 1.0, 'margin_pct': 1.0,
        'description': 'German DAX 40 Index', 'currency': 'EUR',
    },
    'FTSE100': {
        'category': 'index', 'pip_factor': 0.1, 'contract_size': 1,
        'digits': 1, 'sessions': {'London': (8, 17)},
        'typical_spread': 1.0, 'margin_pct': 1.0,
        'description': 'UK FTSE 100 Index', 'currency': 'GBP',
    },
    'JP225': {
        'category': 'index', 'pip_factor': 1.0, 'contract_size': 1,
        'digits': 1, 'sessions': {'Tokyo': (0, 9)},
        'typical_spread': 5.0, 'margin_pct': 1.0,
        'description': 'Nikkei 225 Index', 'currency': 'JPY',
    },
    # ═══════ METALS & CFDs ═══════
    'XAUUSD': {
        'category': 'commodity', 'pip_factor': 0.10, 'contract_size': 100,
        'digits': 2, 'sessions': {'London': (8, 17), 'NY': (13, 22)},
        'typical_spread': 2.0, 'margin_pct': 1.0,
        'description': 'Gold spot vs USD', 'currency': 'USD',
    },
    'XAGUSD': {
        'category': 'commodity', 'pip_factor': 0.001, 'contract_size': 5000,
        'digits': 3, 'sessions': {'London': (8, 17), 'NY': (13, 22)},
        'typical_spread': 3.0, 'margin_pct': 2.0,
        'description': 'Silver spot vs USD', 'currency': 'USD',
    },
    'XTIUSD': {
        'category': 'commodity', 'pip_factor': 0.01, 'contract_size': 1000,
        'digits': 2, 'sessions': {'London': (8, 17), 'NY': (13, 22)},
        'typical_spread': 2.0, 'margin_pct': 2.0,
        'description': 'US Crude Oil (WTI)', 'currency': 'USD',
    },
    'XBRUSD': {
        'category': 'commodity', 'pip_factor': 0.01, 'contract_size': 1000,
        'digits': 2, 'sessions': {'London': (8, 17), 'NY': (13, 22)},
        'typical_spread': 2.0, 'margin_pct': 2.0,
        'description': 'Brent Crude Oil', 'currency': 'USD',
    },
    # ═══════ STOCKS ═══════
    'AAPL': {
        'category': 'stock', 'pip_factor': 0.01, 'contract_size': 1,
        'digits': 2, 'sessions': {'NY': (13, 22)},
        'typical_spread': 3.0, 'margin_pct': 5.0,
        'description': 'Apple Inc.', 'currency': 'USD',
    },
    'TSLA': {
        'category': 'stock', 'pip_factor': 0.01, 'contract_size': 1,
        'digits': 2, 'sessions': {'NY': (13, 22)},
        'typical_spread': 5.0, 'margin_pct': 10.0,
        'description': 'Tesla Inc.', 'currency': 'USD',
    },
    'GOOG': {
        'category': 'stock', 'pip_factor': 0.01, 'contract_size': 1,
        'digits': 2, 'sessions': {'NY': (13, 22)},
        'typical_spread': 3.0, 'margin_pct': 5.0,
        'description': 'Alphabet Inc. (Google)', 'currency': 'USD',
    },
    'AMZN': {
        'category': 'stock', 'pip_factor': 0.01, 'contract_size': 1,
        'digits': 2, 'sessions': {'NY': (13, 22)},
        'typical_spread': 3.0, 'margin_pct': 5.0,
        'description': 'Amazon.com Inc.', 'currency': 'USD',
    },
    'MSFT': {
        'category': 'stock', 'pip_factor': 0.01, 'contract_size': 1,
        'digits': 2, 'sessions': {'NY': (13, 22)},
        'typical_spread': 3.0, 'margin_pct': 5.0,
        'description': 'Microsoft Corp.', 'currency': 'USD',
    },
}

INSTRUMENT_LIST = sorted(INSTRUMENTS.keys(), key=lambda s: (
    {'major': 0, 'cross': 1, 'index': 2, 'commodity': 3, 'stock': 4}[INSTRUMENTS[s]['category']],
    s,
))

CATEGORIES = {
    'major': 'Forex Majors',
    'cross': 'Forex Crosses',
    'index': 'Indices',
    'commodity': 'Commodities & CFDs',
    'stock': 'Stocks',
}

# Alias used by CLI
CATEGORY_LABELS = CATEGORIES

def get_symbols(category: Optional[str] = None) -> list[str]:
    """Get symbols, optionally filtered by category."""
    if category is None:
        return INSTRUMENT_LIST.copy()
    return [s for s in INSTRUMENT_LIST if INSTRUMENTS[s]['category'] == category]

def best_session_str(symbol: str) -> str:
    """Return human-readable best trading session window for a symbol."""
    spec = INSTRUMENTS.get(symbol)
    if not spec:
        return 'N/A'
    sessions = spec.get('sessions', {})

    if not sessions:
        return 'Any time'

    # Prime = London/NY overlap = 13-17 GMT
    has_london = 'London' in sessions
    has_ny = 'NY' in sessions
    has_tokyo = 'Tokyo' in sessions
    has_sydney = 'Sydney' in sessions

    if has_london and has_ny:
        return 'London/NY 13-17 GMT'
    if has_london and has_tokyo:
        return 'London/Tokyo 08-09 GMT'
    if has_sydney and has_tokyo:
        return 'Sydney/Tokyo 22-00 GMT'
    if has_sydney and has_ny:
        return 'Sydney/NY 22-00 GMT'

    # Single session
    for name, (start, end) in sorted(sessions.items(), key=lambda x: x[1][0]):
        return f'{name} {start:02d}-{end:02d} GMT'

    return 'N/A'


# Cache for live USD rates used by pip_value_usd
_USD_RATES_CACHE: dict[str, float] = {}
_USD_RATES_CACHE_AT: float = 0
_USD_RATES_TTL = 120  # 2-minute cache

def _refresh_usd_rates():
    """Fetch live USD rates from Frankfurter API for pip value conversion."""
    global _USD_RATES_CACHE, _USD_RATES_CACHE_AT
    import time as _t
    now = _t.time()
    if now - _USD_RATES_CACHE_AT < _USD_RATES_TTL and _USD_RATES_CACHE:
        return
    try:
        import requests as _req
        resp = _req.get('https://api.frankfurter.app/latest?from=USD', timeout=3)
        if resp.status_code == 200:
            _USD_RATES_CACHE = resp.json().get('rates', {})
            _USD_RATES_CACHE['USD'] = 1.0
            _USD_RATES_CACHE_AT = now
    except Exception:
        pass  # Keep stale cache or fall back to defaults


def pip_value_usd(symbol: str, price: float) -> float:
    """USD value of 1 pip for 1 standard lot at given price.

    For USD-quoted pairs (EURUSD, XAUUSD, etc.) the pip value is direct.
    For non-USD quotes, we convert using the current quote→USD rate.
    """
    spec = INSTRUMENTS[symbol]
    pip = spec['pip_factor']
    contract = spec['contract_size']
    quote_cur = spec.get('currency', 'USD')

    # Pip value in quote currency
    pv_quote = pip * contract

    if quote_cur == 'USD':
        return pv_quote

    # Fetch live USD rates for conversion
    _refresh_usd_rates()

    # Convert from quote currency to USD
    if quote_cur == 'JPY':
        # pv in JPY → divide by USDJPY rate
        usdjpy = _USD_RATES_CACHE.get('JPY', 0)
        if usdjpy > 0:
            return pv_quote / usdjpy
        # Fallback: if this IS USDJPY, use the passed-in price
        if symbol == 'USDJPY' and price > 0:
            return pv_quote / price
        return pv_quote / 162.0  # hard fallback
    else:
        # For GBP, EUR, AUD, NZD: pv_quote × (CUR/USD rate)
        # CUR/USD = 1 / USD/CUR
        usd_per_cur = _USD_RATES_CACHE.get(quote_cur, 0)
        if usd_per_cur > 0:
            return pv_quote * usd_per_cur
        # Fallback hardcoded rates (updated July 2026)
        fallback = {
            'GBP': 1.34, 'EUR': 1.14, 'AUD': 0.70, 'NZD': 0.62,
            'CHF': 1.14, 'CAD': 1.42,
        }
        return pv_quote * fallback.get(quote_cur, 1.0)
