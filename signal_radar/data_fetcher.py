"""Data fetching — MT5 price data + CSV cache layer."""

import csv, json, os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

from .config import Config, CACHE_DIR
from .instruments import INSTRUMENTS, get_symbols

HAS_MT5 = False
try:
    import MetaTrader5 as mt5
    HAS_MT5 = True
except ImportError:
    pass


def resolve_symbol_mt5(base: str) -> Optional[str]:
    """Try common MT5 symbol suffixes."""
    if not HAS_MT5:
        return None
    candidates = [base, base + "+", base + "-", base + ".c", base + "c",
                  base + ".C", base + "_"]
    for c in candidates:
        info = mt5.symbol_info(c)
        if info:
            tick = mt5.symbol_info_tick(c)
            if tick and tick.bid > 0:
                return c
    return None


def fetch_bars(symbol: str, bars: int = 200, timeframe: str = "M5") -> Optional[pd.DataFrame]:
    """Fetch bars from MT5, Binance (crypto), or CSV cache fallback."""

    # Route crypto to Binance API
    spec = INSTRUMENTS.get(symbol, {})
    if spec.get('crypto'):
        df = fetch_crypto_bars(symbol, bars)
        if df is not None and len(df) > 10:
            _save_cache(df, symbol, timeframe)
            return df
        df = _load_cache(symbol, timeframe)
        if df is not None:
            return df
        return generate_sample_data(symbol, bars)

    # Route non-crypto to Yahoo Finance (works on Render/Linux)
    df = fetch_yahoo_bars(symbol, bars)
    if df is not None and len(df) > 10:
        _save_cache(df, symbol, timeframe)
        return df

    # Try MT5 as fallback (Windows-only, won't work on Render)
    from .config import Config
    mt5_tf = 5
    try:
        mt5_mod = __import__("MetaTrader5", fromlist=["TIMEFRAME_M5"])
        mt5_tf = getattr(mt5_mod, f"TIMEFRAME_{timeframe}", 5)
    except Exception:
        pass

    mt5_ok = False
    if HAS_MT5:
        try:
            mt5_ok = mt5.initialize()
        except Exception:
            mt5_ok = False

    if mt5_ok:
        try:
            resolved = resolve_symbol_mt5(symbol)
            if resolved:
                mt5.symbol_select(resolved, True)
                rates = mt5.copy_rates_from_pos(resolved, mt5_tf, 0, bars)
                mt5.shutdown()
                if rates is not None and len(rates) > 0:
                    df = pd.DataFrame(rates)
                    df['time'] = pd.to_datetime(df['time'], unit='s')
                    df.set_index('time', inplace=True)
                    df.rename(columns={
                        'open': 'open', 'high': 'high', 'low': 'low',
                        'close': 'close', 'tick_volume': 'volume',
                    }, inplace=True)
                    df = df[['open', 'high', 'low', 'close', 'volume']].copy()
                    return df
        except Exception:
            try: mt5.shutdown()
            except Exception: pass

    # Try cache
    cached = _load_cache(symbol, timeframe)
    if cached is not None:
        return cached

    # Last resort: sample data
    return generate_sample_data(symbol, bars)


def _cache_path(symbol: str, timeframe: str) -> Path:
    return CACHE_DIR / f"{symbol}_{timeframe}.csv"


def _load_cache(symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
    path = _cache_path(symbol, timeframe)
    if not path.exists():
        return None
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    if age.total_seconds() > 7200:  # 2h expiry
        return None
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    return df if len(df) > 0 else None


def _save_cache(df: pd.DataFrame, symbol: str, timeframe: str):
    df.to_csv(_cache_path(symbol, timeframe))


def fetch_yahoo_bars(symbol: str, bars: int = 200) -> Optional[pd.DataFrame]:
    """Fetch OHLCV data from Yahoo Finance for forex/indices/commodities/stocks.

    Yahoo Finance symbol mapping (add as needed):
      EURUSD -> EURUSD=X,  GBPUSD -> GBPUSD=X,  USDJPY -> USDJPY=X,
      US30 -> ^DJI,        SP500 -> ^GSPC,       NAS100 -> ^IXIC,
      DAX40 -> ^GDAXI,     FTSE100 -> ^FTSE,     JP225 -> ^N225,
      XAUUSD -> GC=F,      XAGUSD -> SI=F,       XTIUSD -> CL=F,
      XBRUSD -> BZ=F,
      AAPL/TSLA/GOOG/AMZN/MSFT -> same as symbol.
    """
    # Build Yahoo Finance ticker from our symbol
    spec = INSTRUMENTS.get(symbol, {})
    yahoo_map = {
        # Forex — Yahoo uses "=X" suffix
        'EURUSD': 'EURUSD=X', 'GBPUSD': 'GBPUSD=X', 'USDJPY': 'USDJPY=X',
        'USDCHF': 'USDCHF=X', 'USDCAD': 'USDCAD=X', 'AUDUSD': 'AUDUSD=X',
        'NZDUSD': 'NZDUSD=X',
        'GBPJPY': 'GBPJPY=X', 'EURJPY': 'EURJPY=X', 'EURGBP': 'EURGBP=X',
        'EURCHF': 'EURCHF=X', 'AUDJPY': 'AUDJPY=X', 'CHFJPY': 'CHFJPY=X',
        'NZDJPY': 'NZDJPY=X', 'GBPAUD': 'GBPAUD=X', 'EURAUD': 'EURAUD=X',
        'AUDNZD': 'AUDNZD=X', 'NZDCAD': 'NZDCAD=X', 'AUDCAD': 'AUDCAD=X',
        'GBPCAD': 'GBPCAD=X', 'GBPCHF': 'GBPCHF=X', 'EURNZD': 'EURNZD=X',
        'EURCAD': 'EURCAD=X', 'CADCHF': 'CADCHF=X',
        # Indices — Yahoo uses "^" prefix
        'US30': '^DJI', 'SP500': '^GSPC', 'NAS100': '^IXIC',
        'DAX40': '^GDAXI', 'FTSE100': '^FTSE', 'JP225': '^N225',
        # Commodities — Yahoo uses futures codes
        'XAUUSD': 'GC=F', 'XAGUSD': 'SI=F', 'XTIUSD': 'CL=F', 'XBRUSD': 'BZ=F',
        # Stocks — same as symbol
        'AAPL': 'AAPL', 'TSLA': 'TSLA', 'GOOG': 'GOOG', 'AMZN': 'AMZN', 'MSFT': 'MSFT',
    }

    yahoo_sym = yahoo_map.get(symbol)
    if not yahoo_sym:
        return None

    # Map bar count to Yahoo period
    if bars <= 50:
        period = '1d'
    elif bars <= 100:
        period = '2d'
    elif bars <= 200:
        period = '5d'
    else:
        period = '1mo'

    try:
        import yfinance as yf
        # Short timeout: if Yahoo is slow, skip and fall back to cache
        import socket
        orig_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(3)
        try:
            df = yf.download(yahoo_sym, period=period, interval='15m', progress=False, auto_adjust=False)
        finally:
            socket.setdefaulttimeout(orig_timeout)
        if df is None or len(df) < 5:
            return None

        # Flatten yfinance's multi-level columns
        # df has columns like ('Close', 'EURUSD=X') — extract first level
        df.columns = [col[0].lower() for col in df.columns]

        # Required columns
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col not in df.columns:
                return None

        df = df[['open', 'high', 'low', 'close', 'volume']].copy()
        df.index = pd.DatetimeIndex(df.index)
        # Remove timezone info if present
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        return df
    except Exception:
        return None


def fetch_crypto_bars(symbol: str, bars: int = 200) -> Optional[pd.DataFrame]:
    """Fetch crypto OHLCV data from Binance public API."""
    import requests as _requests
    spec = INSTRUMENTS.get(symbol, {})
    binance_pair = spec.get('binance_pair', symbol.replace('USD', 'USDT'))

    # Map timeframe to Binance interval
    interval_map = {'M1': '1m', 'M5': '5m', 'M15': '15m', 'M30': '30m',
                    'H1': '1h', 'H4': '4h', 'D1': '1d'}
    interval = '15m'  # default for detailed analysis

    try:
        url = f"https://api.binance.com/api/v3/klines"
        params = {'symbol': binance_pair, 'interval': interval, 'limit': min(bars, 500)}
        resp = _requests.get(url, params=params, timeout=5)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not data or len(data) < 10:
            return None

        rows = []
        for k in data:
            rows.append({
                'open': float(k[1]),
                'high': float(k[2]),
                'low': float(k[3]),
                'close': float(k[4]),
                'volume': float(k[5]),
            })

        times = pd.to_datetime([int(k[0]) for k in data], unit='ms')
        df = pd.DataFrame(rows, index=pd.DatetimeIndex(times))
        return df
    except Exception:
        return None


def fetch_all_bars(bar_count: int = 200, timeframe: str = "M5",
                   symbols: Optional[list[str]] = None) -> dict[str, pd.DataFrame]:
    """Fetch bars for all instruments concurrently. Returns dict of symbol -> DataFrame."""
    if symbols is None:
        symbols = get_symbols()
    result = {}
    lock = __import__('threading').Lock()

    def _fetch_one(sym):
        df = fetch_bars(sym, bar_count, timeframe)
        if df is not None and len(df) > 50:
            with lock:
                result[sym] = df

    # Use thread pool for parallel fetching (max 15 concurrent workers)
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=15) as pool:
        futures = [pool.submit(_fetch_one, sym) for sym in symbols]
        # Wait for all to complete (with overall timeout)
        for f in as_completed(futures, timeout=60):
            try:
                f.result()
            except Exception:
                pass

    return result


def generate_sample_data(symbol: str, bars: int = 200) -> pd.DataFrame:
    """Generate synthetic OHLC data for development/testing when MT5 is down."""
    spec = INSTRUMENTS.get(symbol, {})
    base_price = {
        'EURUSD': 1.08, 'GBPUSD': 1.28, 'USDJPY': 150.0, 'USDCHF': 0.88,
        'USDCAD': 1.36, 'AUDUSD': 0.67, 'NZDUSD': 0.61,
        'GBPJPY': 192.0, 'EURJPY': 162.0, 'EURGBP': 0.84, 'EURCHF': 0.95,
        'AUDJPY': 100.0, 'CHFJPY': 170.0, 'NZDJPY': 91.0, 'GBPAUD': 1.91, 'EURAUD': 1.61,
        'XAUUSD': 2350.0, 'XAGUSD': 29.5, 'XTIUSD': 78.0, 'XBRUSD': 82.0,
        'US30': 39000, 'SP500': 5400, 'NAS100': 19500, 'DAX40': 18200, 'FTSE100': 8200, 'JP225': 39500,
        'AAPL': 210, 'TSLA': 250, 'GOOG': 175, 'AMZN': 185, 'MSFT': 430,
        # Crypto
        'BTCUSD': 58000, 'ETHUSD': 3100, 'SOLUSD': 145, 'XRPUSD': 0.52,
        'ADAUSD': 0.45, 'DOGEUSD': 0.12, 'AVAXUSD': 28, 'LINKUSD': 13.5,
        'DOTUSD': 6.2, 'LTCUSD': 72, 'SUIUSD': 1.85, 'APTUSD': 7.5,
    }.get(symbol, 100.0)

    pip = spec.get('pip_factor', 0.01) if spec else 0.01
    vol = base_price * 0.002

    np.random.seed(hash(symbol) % 2**31)
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    times = [now - timedelta(minutes=5 * i) for i in range(bars)]
    times.reverse()

    data = []
    for t in times:
        o = base_price + np.random.randn() * vol * 0.5
        h = o + abs(np.random.randn() * vol)
        l = o - abs(np.random.randn() * vol)
        c = (o + h + l) / 3 + np.random.randn() * vol * 0.2
        c = max(l, min(h, c))
        data.append({'open': round(o, 5), 'high': round(h, 5),
                     'low': round(l, 5), 'close': round(c, 5),
                     'volume': int(np.random.exponential(50) + 10)})

    df = pd.DataFrame(data, index=pd.DatetimeIndex(times))
    return df
