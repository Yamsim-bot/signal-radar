"""Instrument database — 28+ symbols with specs for Vantage Cent Raw ECN."""

from dataclasses import dataclass, field
from typing import Optional

INSTRUMENTS = {
    # ═══════ MAJORS ═══════
    'EURUSD': {
        'category': 'major', 'pip_factor': 0.0001, 'contract_size': 1000,
        'digits': 5, 'sessions': {'London': (8, 17), 'NY': (13, 22)},
        'typical_spread': 0.2, 'margin_pct': 0.2,
        'description': 'Euro / US Dollar', 'currency': 'USD',
    },
    'GBPUSD': {
        'category': 'major', 'pip_factor': 0.0001, 'contract_size': 1000,
        'digits': 5, 'sessions': {'London': (8, 17), 'NY': (13, 22)},
        'typical_spread': 0.5, 'margin_pct': 0.2,
        'description': 'British Pound / US Dollar', 'currency': 'USD',
    },
    'USDJPY': {
        'category': 'major', 'pip_factor': 0.01, 'contract_size': 1000,
        'digits': 3, 'sessions': {'Tokyo': (0, 9), 'London': (8, 17), 'NY': (13, 22)},
        'typical_spread': 0.3, 'margin_pct': 0.2,
        'description': 'US Dollar / Japanese Yen', 'currency': 'JPY',
    },
    'USDCHF': {
        'category': 'major', 'pip_factor': 0.0001, 'contract_size': 1000,
        'digits': 5, 'sessions': {'London': (8, 17), 'NY': (13, 22)},
        'typical_spread': 0.5, 'margin_pct': 0.2,
        'description': 'US Dollar / Swiss Franc', 'currency': 'USD',
    },
    'USDCAD': {
        'category': 'major', 'pip_factor': 0.0001, 'contract_size': 1000,
        'digits': 5, 'sessions': {'London': (8, 17), 'NY': (13, 22)},
        'typical_spread': 0.5, 'margin_pct': 0.2,
        'description': 'US Dollar / Canadian Dollar', 'currency': 'USD',
    },
    'AUDUSD': {
        'category': 'major', 'pip_factor': 0.0001, 'contract_size': 1000,
        'digits': 5, 'sessions': {'Sydney': (22, 7), 'Tokyo': (0, 9), 'London': (8, 17)},
        'typical_spread': 0.5, 'margin_pct': 0.2,
        'description': 'Australian Dollar / US Dollar', 'currency': 'USD',
    },
    'NZDUSD': {
        'category': 'major', 'pip_factor': 0.0001, 'contract_size': 1000,
        'digits': 5, 'sessions': {'Sydney': (22, 7), 'Tokyo': (0, 9)},
        'typical_spread': 0.7, 'margin_pct': 0.2,
        'description': 'New Zealand Dollar / US Dollar', 'currency': 'USD',
    },
    # ═══════ CROSSES ═══════
    'GBPJPY': {
        'category': 'cross', 'pip_factor': 0.01, 'contract_size': 1000,
        'digits': 3, 'sessions': {'London': (8, 17), 'NY': (13, 22)},
        'typical_spread': 0.6, 'margin_pct': 0.2,
        'description': 'British Pound / Japanese Yen', 'currency': 'JPY',
    },
    'EURJPY': {
        'category': 'cross', 'pip_factor': 0.01, 'contract_size': 1000,
        'digits': 3, 'sessions': {'London': (8, 17), 'NY': (13, 22)},
        'typical_spread': 0.4, 'margin_pct': 0.2,
        'description': 'Euro / Japanese Yen', 'currency': 'JPY',
    },
    'EURGBP': {
        'category': 'cross', 'pip_factor': 0.0001, 'contract_size': 1000,
        'digits': 5, 'sessions': {'London': (8, 17)},
        'typical_spread': 0.4, 'margin_pct': 0.2,
        'description': 'Euro / British Pound', 'currency': 'GBP',
    },
    'EURCHF': {
        'category': 'cross', 'pip_factor': 0.0001, 'contract_size': 1000,
        'digits': 5, 'sessions': {'London': (8, 17)},
        'typical_spread': 0.5, 'margin_pct': 0.2,
        'description': 'Euro / Swiss Franc', 'currency': 'CHF',
    },
    'AUDJPY': {
        'category': 'cross', 'pip_factor': 0.01, 'contract_size': 1000,
        'digits': 3, 'sessions': {'Sydney': (22, 7), 'Tokyo': (0, 9)},
        'typical_spread': 0.6, 'margin_pct': 0.2,
        'description': 'Australian Dollar / Japanese Yen', 'currency': 'JPY',
    },
    'CHFJPY': {
        'category': 'cross', 'pip_factor': 0.01, 'contract_size': 1000,
        'digits': 3, 'sessions': {'London': (8, 17)},
        'typical_spread': 0.7, 'margin_pct': 0.2,
        'description': 'Swiss Franc / Japanese Yen', 'currency': 'JPY',
    },
    'NZDJPY': {
        'category': 'cross', 'pip_factor': 0.01, 'contract_size': 1000,
        'digits': 3, 'sessions': {'Sydney': (22, 7), 'Tokyo': (0, 9)},
        'typical_spread': 0.8, 'margin_pct': 0.2,
        'description': 'New Zealand Dollar / Japanese Yen', 'currency': 'JPY',
    },
    'GBPAUD': {
        'category': 'cross', 'pip_factor': 0.0001, 'contract_size': 1000,
        'digits': 5, 'sessions': {'London': (8, 17)},
        'typical_spread': 1.0, 'margin_pct': 0.2,
        'description': 'British Pound / Australian Dollar', 'currency': 'AUD',
    },
    'EURAUD': {
        'category': 'cross', 'pip_factor': 0.0001, 'contract_size': 1000,
        'digits': 5, 'sessions': {'London': (8, 17)},
        'typical_spread': 1.0, 'margin_pct': 0.2,
        'description': 'Euro / Australian Dollar', 'currency': 'AUD',
    },
    'AUDNZD': {
        'category': 'cross', 'pip_factor': 0.0001, 'contract_size': 1000,
        'digits': 5, 'sessions': {'Sydney': (22, 7), 'Tokyo': (0, 9)},
        'typical_spread': 1.2, 'margin_pct': 0.2,
        'description': 'Australian Dollar / New Zealand Dollar', 'currency': 'NZD',
    },
    'NZDCAD': {
        'category': 'cross', 'pip_factor': 0.0001, 'contract_size': 1000,
        'digits': 5, 'sessions': {'Sydney': (22, 7), 'NY': (13, 22)},
        'typical_spread': 1.5, 'margin_pct': 0.2,
        'description': 'New Zealand Dollar / Canadian Dollar', 'currency': 'CAD',
    },
    'AUDCAD': {
        'category': 'cross', 'pip_factor': 0.0001, 'contract_size': 1000,
        'digits': 5, 'sessions': {'Sydney': (22, 7), 'NY': (13, 22)},
        'typical_spread': 1.2, 'margin_pct': 0.2,
        'description': 'Australian Dollar / Canadian Dollar', 'currency': 'CAD',
    },
    'GBPCAD': {
        'category': 'cross', 'pip_factor': 0.0001, 'contract_size': 1000,
        'digits': 5, 'sessions': {'London': (8, 17)},
        'typical_spread': 1.4, 'margin_pct': 0.2,
        'description': 'British Pound / Canadian Dollar', 'currency': 'CAD',
    },
    'GBPCHF': {
        'category': 'cross', 'pip_factor': 0.0001, 'contract_size': 1000,
        'digits': 5, 'sessions': {'London': (8, 17)},
        'typical_spread': 1.0, 'margin_pct': 0.2,
        'description': 'British Pound / Swiss Franc', 'currency': 'CHF',
    },
    'EURNZD': {
        'category': 'cross', 'pip_factor': 0.0001, 'contract_size': 1000,
        'digits': 5, 'sessions': {'London': (8, 17)},
        'typical_spread': 1.5, 'margin_pct': 0.2,
        'description': 'Euro / New Zealand Dollar', 'currency': 'NZD',
    },
    'EURCAD': {
        'category': 'cross', 'pip_factor': 0.0001, 'contract_size': 1000,
        'digits': 5, 'sessions': {'London': (8, 17), 'NY': (13, 22)},
        'typical_spread': 1.2, 'margin_pct': 0.2,
        'description': 'Euro / Canadian Dollar', 'currency': 'CAD',
    },
    'CADCHF': {
        'category': 'cross', 'pip_factor': 0.0001, 'contract_size': 1000,
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
        'category': 'commodity', 'pip_factor': 0.01, 'contract_size': 100,
        'digits': 2, 'sessions': {'London': (8, 17), 'NY': (13, 22)},
        'typical_spread': 2.0, 'margin_pct': 1.0,
        'description': 'Gold spot vs USD', 'currency': 'USD',
    },
    'XAGUSD': {
        'category': 'commodity', 'pip_factor': 0.001, 'contract_size': 500,
        'digits': 3, 'sessions': {'London': (8, 17), 'NY': (13, 22)},
        'typical_spread': 3.0, 'margin_pct': 2.0,
        'description': 'Silver spot vs USD', 'currency': 'USD',
    },
    'XTIUSD': {
        'category': 'commodity', 'pip_factor': 0.01, 'contract_size': 100,
        'digits': 2, 'sessions': {'London': (8, 17), 'NY': (13, 22)},
        'typical_spread': 2.0, 'margin_pct': 2.0,
        'description': 'US Crude Oil (WTI)', 'currency': 'USD',
    },
    'XBRUSD': {
        'category': 'commodity', 'pip_factor': 0.01, 'contract_size': 100,
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

    # ═══════ CRYPTO ═══════
    'BTCUSD': {
        'category': 'crypto', 'pip_factor': 0.1, 'contract_size': 1,
        'digits': 2, 'sessions': {'Crypto': (0, 24)},
        'typical_spread': 1.0, 'margin_pct': 5.0,
        'description': 'Bitcoin vs USD', 'currency': 'USD',
        'binance_pair': 'BTCUSDT', 'crypto': True,
    },
    'ETHUSD': {
        'category': 'crypto', 'pip_factor': 0.01, 'contract_size': 1,
        'digits': 2, 'sessions': {'Crypto': (0, 24)},
        'typical_spread': 0.5, 'margin_pct': 5.0,
        'description': 'Ethereum vs USD', 'currency': 'USD',
        'binance_pair': 'ETHUSDT', 'crypto': True,
    },
    'SOLUSD': {
        'category': 'crypto', 'pip_factor': 0.01, 'contract_size': 1,
        'digits': 3, 'sessions': {'Crypto': (0, 24)},
        'typical_spread': 1.0, 'margin_pct': 10.0,
        'description': 'Solana vs USD', 'currency': 'USD',
        'binance_pair': 'SOLUSDT', 'crypto': True,
    },
    'XRPUSD': {
        'category': 'crypto', 'pip_factor': 0.0001, 'contract_size': 1,
        'digits': 4, 'sessions': {'Crypto': (0, 24)},
        'typical_spread': 0.5, 'margin_pct': 10.0,
        'description': 'XRP vs USD', 'currency': 'USD',
        'binance_pair': 'XRPUSDT', 'crypto': True,
    },
    'ADAUSD': {
        'category': 'crypto', 'pip_factor': 0.0001, 'contract_size': 1,
        'digits': 4, 'sessions': {'Crypto': (0, 24)},
        'typical_spread': 0.5, 'margin_pct': 10.0,
        'description': 'Cardano vs USD', 'currency': 'USD',
        'binance_pair': 'ADAUSDT', 'crypto': True,
    },
    'DOGEUSD': {
        'category': 'crypto', 'pip_factor': 0.0001, 'contract_size': 1,
        'digits': 5, 'sessions': {'Crypto': (0, 24)},
        'typical_spread': 0.5, 'margin_pct': 10.0,
        'description': 'Dogecoin vs USD', 'currency': 'USD',
        'binance_pair': 'DOGEUSDT', 'crypto': True,
    },
    'AVAXUSD': {
        'category': 'crypto', 'pip_factor': 0.01, 'contract_size': 1,
        'digits': 3, 'sessions': {'Crypto': (0, 24)},
        'typical_spread': 1.0, 'margin_pct': 10.0,
        'description': 'Avalanche vs USD', 'currency': 'USD',
        'binance_pair': 'AVAXUSDT', 'crypto': True,
    },
    'LINKUSD': {
        'category': 'crypto', 'pip_factor': 0.001, 'contract_size': 1,
        'digits': 3, 'sessions': {'Crypto': (0, 24)},
        'typical_spread': 1.0, 'margin_pct': 10.0,
        'description': 'Chainlink vs USD', 'currency': 'USD',
        'binance_pair': 'LINKUSDT', 'crypto': True,
    },
    'DOTUSD': {
        'category': 'crypto', 'pip_factor': 0.01, 'contract_size': 1,
        'digits': 3, 'sessions': {'Crypto': (0, 24)},
        'typical_spread': 1.0, 'margin_pct': 10.0,
        'description': 'Polkadot vs USD', 'currency': 'USD',
        'binance_pair': 'DOTUSDT', 'crypto': True,
    },
    'LTCUSD': {
        'category': 'crypto', 'pip_factor': 0.01, 'contract_size': 1,
        'digits': 2, 'sessions': {'Crypto': (0, 24)},
        'typical_spread': 1.0, 'margin_pct': 10.0,
        'description': 'Litecoin vs USD', 'currency': 'USD',
        'binance_pair': 'LTCUSDT', 'crypto': True,
    },
    'SUIUSD': {
        'category': 'crypto', 'pip_factor': 0.001, 'contract_size': 1,
        'digits': 3, 'sessions': {'Crypto': (0, 24)},
        'typical_spread': 1.0, 'margin_pct': 10.0,
        'description': 'Sui vs USD', 'currency': 'USD',
        'binance_pair': 'SUIUSDT', 'crypto': True,
    },
    'APTUSD': {
        'category': 'crypto', 'pip_factor': 0.01, 'contract_size': 1,
        'digits': 3, 'sessions': {'Crypto': (0, 24)},
        'typical_spread': 1.0, 'margin_pct': 10.0,
        'description': 'Aptos vs USD', 'currency': 'USD',
        'binance_pair': 'APTUSDT', 'crypto': True,
    },
}

INSTRUMENT_LIST = sorted(INSTRUMENTS.keys(), key=lambda s: (
    {'major': 0, 'cross': 1, 'index': 2, 'commodity': 3, 'stock': 4, 'crypto': 5}[INSTRUMENTS[s]['category']],
    s,
))

CATEGORIES = {
    'major': 'Forex Majors',
    'cross': 'Forex Crosses',
    'index': 'Indices',
    'commodity': 'Commodities & CFDs',
    'stock': 'Stocks',
    'crypto': 'Cryptocurrencies',
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

    # Crypto trades 24/7 — show peak volatility window
    if spec.get('crypto'):
        return '24/7 — Peak 13-22 GMT'

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


def pip_value_usd(symbol: str, price: float) -> float:
    """USD value of 1 pip for 1 cent lot at given price."""
    spec = INSTRUMENTS[symbol]
    pip = spec['pip_factor']
    contract = spec['contract_size']
    cur = spec.get('currency', 'USD')
    if cur == 'USD':
        return pip * contract
    elif cur == 'JPY':
        return pip * contract / price if price > 0 else 0
    elif cur == 'GBP':
        # Approx: GBPUSD rate, assume ~1.28
        return pip * contract / 1.28
    elif cur == 'EUR':
        return pip * contract / 1.08
    elif cur == 'AUD':
        return pip * contract / 0.67
    elif cur == 'CHF':
        return pip * contract / 1.12
    return pip * contract / price if price > 0 else 0
