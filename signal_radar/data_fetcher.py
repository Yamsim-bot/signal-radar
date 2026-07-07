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
    """Fetch bars — cache > sample data (instant) > live feed (background)."""
    spec = INSTRUMENTS.get(symbol, {})

    # 1️⃣ Fast path: check cache first (instant)
    cached = _load_cache(symbol, timeframe)
    if cached is not None:
        return cached

    # 2️⃣ Crypto: try Binance (usually fast), fall back to sample
    if spec.get('crypto'):
        df = fetch_crypto_bars(symbol, bars)
        if df is not None and len(df) > 10:
            _save_cache(df, symbol, timeframe)
            return df
        return generate_sample_data(symbol, bars)

    # 3️⃣ Non-crypto: use sample data immediately (instant, <1ms)
    #    Live data (Yahoo) is fetched as a cache-warming side effect
    sample = generate_sample_data(symbol, bars)

    # 4️⃣ Try Yahoo Finance for cache warming (fast with 3s timeout + 15 workers)
    live = fetch_yahoo_bars(symbol, bars)
    if live is not None and len(live) > 10:
        _save_cache(live, symbol, timeframe)
        return live  # Use live data if it arrived quickly
    return sample  # Fall back to sample — instant!


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
    """Fetch bars for all instruments — INSTANT, never blocks on network.

    Returns whatever is available (cache + sample data) immediately.
    Warms Yahoo cache in a background thread so subsequent scans load live data.
    """
    if symbols is None:
        symbols = get_symbols()
    result = {}
    lock = __import__('threading').Lock()

    # Phase 1: collect cache + sample data (INSTANT — no network calls)
    for sym in symbols:
        spec = INSTRUMENTS.get(sym, {})
        cached = _load_cache(sym, timeframe)
        if cached is not None and len(cached) > 50:
            with lock:
                result[sym] = cached
        elif spec.get('crypto'):
            # Crypto: try Binance fast, fall back to sample
            df = fetch_crypto_bars(sym, bar_count)
            if df is not None and len(df) > 10:
                _save_cache(df, sym, timeframe)
                with lock:
                    result[sym] = df
            else:
                with lock:
                    result[sym] = generate_sample_data(sym, bar_count)
        else:
            # Non-crypto: sample data instantly
            with lock:
                result[sym] = generate_sample_data(sym, bar_count)

    # Phase 2: warm Yahoo cache in background (don't block the caller)
    def _warm_cache():
        for sym in symbols:
            spec = INSTRUMENTS.get(sym, {})
            if spec.get('crypto') or _load_cache(sym, timeframe) is not None:
                continue  # already cached or crypto
            live = fetch_yahoo_bars(sym, bar_count)
            if live is not None and len(live) > 10:
                _save_cache(live, sym, timeframe)

    import threading
    warmer = threading.Thread(target=_warm_cache, daemon=True)
    warmer.start()

    return result


def fetch_live_prices(symbols: Optional[list] = None) -> dict[str, float]:
    """Fetch live current prices for all instruments.

    Two strategies:
      1. Forex (majors + crosses) → Frankfurter API (free, no key, fast)
      2. Stocks/indices/commodities → Yahoo Finance batch download

    Returns dict of symbol -> current price (best effort, may be partial).
    Total time: ~1-2s for all symbols (single Frankfurter call + one Yahoo batch call).
    """
    from .instruments import get_symbols

    if symbols is None:
        symbols = get_symbols()

# ── Live price cache ──
_LIVE_PRICE_CACHE: dict[str, dict[str, float]] = {}
_LIVE_PRICE_CACHE_AT: dict[str, float] = {}
_LIVE_PRICE_CACHE_TTL = 30  # seconds


def fetch_live_prices(symbols: Optional[list] = None) -> dict[str, float]:
    """Fetch live current prices for all instruments.

    Two strategies:
      1. Forex (majors + crosses) → Frankfurter API (free, no key, fast)
      2. Stocks/indices/commodities → Yahoo Finance batch download

    Results are cached for 30s so repeated calls are instant.
    Total blocking time capped at ~3s (Frankfurter + gold-api fast path;
    Yahoo batches only run if cache is stale AND time permits).
    """
    from .instruments import get_symbols

    if symbols is None:
        symbols = get_symbols()

    import time as _time
    _start = _time.time()
    _MAX_BUDGET = 2.5  # total seconds this function may block

    # ═══ Check cache first (instant) ═══
    _cache_key = 'all'
    _now = _time.time()
    cached = _LIVE_PRICE_CACHE.get(_cache_key)
    cached_at = _LIVE_PRICE_CACHE_AT.get(_cache_key, 0)
    if cached is not None and _now - cached_at < _LIVE_PRICE_CACHE_TTL:
        return cached

    prices: dict[str, float] = {}

    # ── Phase 1: Forex via Frankfurter API (single call, ~300-500ms) ──
    forex_symbols = [s for s in symbols if INSTRUMENTS[s].get('category') in ('major', 'cross')]
    if forex_symbols:
        try:
            import requests as _req
            resp = _req.get(
                'https://api.frankfurter.app/latest?from=USD',
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json()
                rates = data.get('rates', {})
                rates['USD'] = 1.0
                for sym in forex_symbols:
                    base, quote = sym[:3], sym[3:]
                    if base in rates and quote in rates:
                        prices[sym] = rates[quote] / rates[base]
        except Exception:
            pass  # Non-fatal — fall back to OHLC sample close

    # ── Phase 2: Precious metals via Kitco (industry-standard spot price) ──
    if _time.time() - _start < _MAX_BUDGET:
        precious = [s for s in symbols if s in ('XAUUSD', 'XAGUSD')]
        if precious:
            try:
                import re as _re
                import requests as _req
                _resp = _req.get(
                    'https://www.kitco.com/market/',
                    timeout=5,
                    headers={'User-Agent': 'Mozilla/5.0'},
                )
                if _resp.status_code == 200:
                    _bids = _re.findall(r'"bid":"?([\d.]+)"?', _resp.text)
                    _asks = _re.findall(r'"ask":"?([\d.]+)"?', _resp.text)
                    if len(_bids) >= 2 and len(_asks) >= 2:
                        # Kitco order: gold (0), silver (1), platinum (2), palladium (3)
                        prices['XAUUSD'] = (float(_bids[0]) + float(_asks[0])) / 2
                        prices['XAGUSD'] = (float(_bids[1]) + float(_asks[1])) / 2
            except Exception:
                pass  # Fall back to sample close

    # ── Phase 3: Yahoo Finance — only if time budget allows ──
    if _time.time() - _start < _MAX_BUDGET:
        non_forex = [s for s in symbols if s not in prices]
        if non_forex:
            yahoo_map = {
                'AAPL': 'AAPL', 'TSLA': 'TSLA', 'GOOG': 'GOOG',
                'AMZN': 'AMZN', 'MSFT': 'MSFT',
                'US30': '^DJI', 'SP500': '^GSPC', 'NAS100': '^IXIC',
                'DAX40': '^GDAXI', 'FTSE100': '^FTSE', 'JP225': '^N225',
                'XTIUSD': 'CL=F', 'XBRUSD': 'BZ=F',
            }

            def _yahoo_batch(syms):
                """Download a batch of Yahoo symbols and return {sym: price}."""
                result = {}
                if not syms:
                    return result
                try:
                    import yfinance as yf
                    import socket
                    old_to = socket.getdefaulttimeout()
                    socket.setdefaulttimeout(4)
                    try:
                        df = yf.download(
                            ' '.join(syms),
                            period='2d',
                            interval='15m',
                            group_by='ticker',
                            progress=False,
                            auto_adjust=False,
                        )
                        if df is not None and not df.empty:
                            if hasattr(df.columns, 'levels') and len(df.columns.levels) > 1:
                                for ysym in syms:
                                    try:
                                        result[ysym] = float(df[ysym]['Close'].iloc[-1])
                                    except (KeyError, TypeError, IndexError, ValueError):
                                        pass
                            else:
                                try:
                                    close_col = next(
                                        (c for c in df.columns if isinstance(c, str) and c.lower() == 'close'),
                                        None,
                                    )
                                    if close_col:
                                        result[syms[0]] = float(df[close_col].iloc[-1])
                                except (KeyError, TypeError, IndexError, ValueError):
                                    pass
                    finally:
                        socket.setdefaulttimeout(old_to)
                except Exception:
                    pass
                return result

            # Separate Yahoo symbols into compatible batches
            yahoo_sym_list = [yahoo_map.get(s, s) for s in non_forex]
            stocks = [s for s in yahoo_sym_list if s in ('AAPL','TSLA','GOOG','AMZN','MSFT')]
            us_idx = [s for s in yahoo_sym_list if s in ('^DJI','^GSPC','^IXIC')]
            eu_idx = [s for s in yahoo_sym_list if s in ('^GDAXI','^FTSE','^N225')]
            futs = [s for s in yahoo_sym_list if '=F' in s]

            for batch in [stocks, us_idx, eu_idx, futs]:
                if _time.time() - _start >= _MAX_BUDGET:
                    break  # out of time — skip remaining batches
                batch_results = _yahoo_batch(batch)
                rev_map = {v: k for k, v in yahoo_map.items()}
                for ysym, yprice in batch_results.items():
                    our_sym = rev_map.get(ysym, ysym)
                    if our_sym in non_forex:
                        prices[our_sym] = yprice

            # Individual retries — only if time allows
            if _time.time() - _start < _MAX_BUDGET:
                failed = [s for s in non_forex if s not in prices or not prices.get(s) or str(prices.get(s)) == 'nan']
                for sym in failed:
                    if _time.time() - _start >= _MAX_BUDGET:
                        break
                    ysym = yahoo_map.get(sym, sym)
                    result = _yahoo_batch([ysym])
                    if result:
                        prices[sym] = result.get(ysym, result.get(sym))

    # ═══ Save to cache ═══
    _LIVE_PRICE_CACHE[_cache_key] = prices
    _LIVE_PRICE_CACHE_AT[_cache_key] = _time.time()

    return prices


def generate_sample_data(symbol: str, bars: int = 200) -> pd.DataFrame:
    """Generate synthetic OHLC data for development/testing when MT5 is down."""
    spec = INSTRUMENTS.get(symbol, {})
    base_price = {
        # Forex Majors (July 2026)
        'EURUSD': 1.143, 'GBPUSD': 1.339, 'USDJPY': 161.9, 'USDCHF': 0.806,
        'USDCAD': 1.422, 'AUDUSD': 0.695, 'NZDUSD': 0.569,
        # Forex Crosses (July 2026)
        'GBPJPY': 216.7, 'EURJPY': 185.1, 'EURGBP': 0.854, 'EURCHF': 0.922,
        'AUDJPY': 112.5, 'CHFJPY': 200.8, 'NZDJPY': 92.1, 'GBPAUD': 1.927,
        'EURAUD': 1.646, 'AUDNZD': 1.221, 'NZDCAD': 0.809, 'AUDCAD': 0.988,
        'GBPCAD': 1.903, 'GBPCHF': 1.079, 'EURNZD': 2.009, 'EURCAD': 1.626,
        'CADCHF': 0.567,
        # Commodities (July 2026)
        'XAUUSD': 4157.0, 'XAGUSD': 61.4, 'XTIUSD': 70.6, 'XBRUSD': 74.3,
        # Indices (July 2026)
        'US30': 52930, 'SP500': 7519, 'NAS100': 25960, 'DAX40': 25492,
        'FTSE100': 10687, 'JP225': 68410,
        # Stocks (July 2026)
        'AAPL': 313, 'TSLA': 406, 'GOOG': 366, 'AMZN': 245, 'MSFT': 393,
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
