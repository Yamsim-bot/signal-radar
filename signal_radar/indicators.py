"""Technical indicators — ADX, RSI, MACD, BB, EMA, ATR."""

import warnings
import numpy as np
import pandas as pd

# Suppress harmless division-by-zero warnings in indicator calculations
warnings.filterwarnings('ignore', 'invalid value', category=RuntimeWarning)

from .config import Config


def compute_all(df: pd.DataFrame, cfg: Config = Config()) -> pd.DataFrame:
    """Compute all technical indicators on OHLC DataFrame."""
    df = df.copy()
    close = df['close'].values.astype(float)
    high = df['high'].values.astype(float)
    low = df['low'].values.astype(float)

    df['ema_fast'] = _ema(close, cfg.ema_fast)
    df['ema_slow'] = _ema(close, cfg.ema_slow)
    df['atr'] = _atr(high, low, close, cfg.adx_period)
    df['rsi'] = _rsi(close, cfg.rsi_period)

    adx_df = _adx(high, low, close, cfg.adx_period)
    for col in adx_df.columns:
        df[col] = adx_df[col]

    bb = _bollinger(close, cfg.bb_period, cfg.bb_std)
    for col in bb.columns:
        df[col] = bb[col]

    macd = _macd(close, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    for col in macd.columns:
        df[col] = macd[col]

    return df


def _ema(values: np.ndarray, period: int) -> np.ndarray:
    """Exponential Moving Average."""
    alpha = 2 / (period + 1)
    result = np.full_like(values, np.nan)
    result[period - 1] = np.mean(values[:period])
    for i in range(period, len(values)):
        result[i] = alpha * values[i] + (1 - alpha) * result[i - 1]
    return result


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """Average True Range (Wilder's)."""
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]),
                               np.abs(low[1:] - close[:-1])))
    tr = np.concatenate([[tr[0]], tr])
    result = np.full_like(tr, np.nan)
    result[period - 1] = np.mean(tr[1:period])
    for i in range(period, len(tr)):
        result[i] = (result[i - 1] * (period - 1) + tr[i]) / period
    return result


def _rsi(values: np.ndarray, period: int = 14) -> np.ndarray:
    """Relative Strength Index."""
    deltas = np.diff(values)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.full_like(values, np.nan)
    avg_loss = np.full_like(values, np.nan)
    avg_gain[period] = np.mean(gains[:period])
    avg_loss[period] = np.mean(losses[:period])
    for i in range(period + 1, len(values)):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gains[i - 1]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + losses[i - 1]) / period
    rs = avg_gain / np.maximum(avg_loss, 0.001)
    rsi = 100 - (100 / (1 + rs))
    rsi[:period] = np.nan
    return rsi


def _adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> pd.DataFrame:
    """ADX + DI+/DI- (Wilder's). Returns DataFrame with adx, plus_di, minus_di."""
    n = len(high)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    for i in range(1, n):
        up = high[i] - high[i - 1]
        dn = low[i - 1] - low[i]
        if up > dn and up > 0:
            plus_dm[i] = up
        if dn > up and dn > 0:
            minus_dm[i] = dn

    tr = np.maximum(high - low,
                    np.maximum(np.abs(high - np.roll(close, 1)),
                               np.abs(low - np.roll(close, 1))))
    tr[0] = tr[1]

    def _wilder_smooth(vals):
        result = np.zeros(n)
        result[period - 1] = np.mean(vals[1:period + 1])
        for i in range(period, n):
            result[i] = (result[i - 1] * (period - 1) + vals[i]) / period
        return result

    tr_s = _wilder_smooth(tr)
    pdm_s = _wilder_smooth(plus_dm)
    mdm_s = _wilder_smooth(minus_dm)

    pdi = np.divide(100 * pdm_s, tr_s, out=np.zeros_like(tr_s), where=tr_s > 0)
    mdi = np.divide(100 * mdm_s, tr_s, out=np.zeros_like(tr_s), where=tr_s > 0)
    dx_sum = pdi + mdi
    dx = np.divide(100 * np.abs(pdi - mdi), dx_sum, out=np.zeros_like(dx_sum), where=dx_sum > 0)
    adx = _wilder_smooth(dx)
    adx[:period * 2] = np.nan

    return pd.DataFrame({
        'adx': adx, 'plus_di': pdi, 'minus_di': mdi,
        'di_uptrend': pdi > mdi, 'di_downtrend': mdi > pdi,
    })


def _bollinger(close: np.ndarray, period: int = 20, std: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands."""
    sma = np.full_like(close, np.nan)
    upper = np.full_like(close, np.nan)
    lower = np.full_like(close, np.nan)
    for i in range(period - 1, len(close)):
        sma[i] = np.mean(close[i - period + 1:i + 1])
        sd = np.std(close[i - period + 1:i + 1])
        upper[i] = sma[i] + std * sd
        lower[i] = sma[i] - std * sd
    return pd.DataFrame({'bb_mid': sma, 'bb_upper': upper, 'bb_lower': lower})


def _macd(close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """MACD oscillator."""
    ema_f = _ema(close, fast)
    ema_s = _ema(close, slow)
    macd = ema_f - ema_s
    sig = _ema(macd, signal)
    hist = macd - sig
    return pd.DataFrame({'macd': macd, 'macd_signal': sig, 'macd_hist': hist})


def compute_multi_tf(df: pd.DataFrame) -> dict:
    """Compute H1 and H4 indicators from M5 data for multi-TF analysis."""
    cfg = Config()
    min_req = max(cfg.ema_slow, cfg.adx_period * 2, cfg.rsi_period)
    result = {}

    df_h1 = df.resample('1h').agg({
        'open': 'first', 'high': 'max', 'low': 'min',
        'close': 'last', 'volume': 'sum'
    }).dropna()
    if len(df_h1) >= min_req:
        result['h1'] = compute_all(df_h1, cfg)

    df_h4 = df.resample('4h').agg({
        'open': 'first', 'high': 'max', 'low': 'min',
        'close': 'last', 'volume': 'sum'
    }).dropna()
    if len(df_h4) >= min_req:
        result['h4'] = compute_all(df_h4, cfg)

    return result
