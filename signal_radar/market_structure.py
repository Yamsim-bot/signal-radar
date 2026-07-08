"""Market Structure — swing highs/lows, trend detection, BOS/CHoCH."""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional

from .config import Config


@dataclass
class SwingPoint:
    idx: int
    price: float
    time: str
    swing_type: str  # 'high' or 'low'
    strength: int    # 1-3 based on pivots either side


@dataclass
class MarketStructureResult:
    trend_direction: str        # 'uptrend', 'downtrend', 'ranging'
    trend_score: float          # -100 (strong downtrend) to +100 (strong uptrend)
    swing_highs: list[SwingPoint]
    swing_lows: list[SwingPoint]
    last_bos: Optional[str]     # 'bullish' or 'bearish' break of structure
    last_choch: Optional[str]   # Change of character
    trend_strength: str         # 'strong', 'moderate', 'weak'
    key_resistance: float       # nearest resistance
    key_support: float          # nearest support


def analyze(df: pd.DataFrame, cfg: Config = Config()) -> MarketStructureResult:
    """Complete market structure analysis."""
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    times = df.index

    # Extract ADX / DI + EMA data from DataFrame (computed by indicators.py)
    adx = df['adx'].values if 'adx' in df.columns else None
    plus_di = df['plus_di'].values if 'plus_di' in df.columns else None
    minus_di = df['minus_di'].values if 'minus_di' in df.columns else None
    ema_fast = df['ema_fast'].values if 'ema_fast' in df.columns else None
    ema_slow = df['ema_slow'].values if 'ema_slow' in df.columns else None

    # Detect swing points
    raw_swings = _detect_swings(high, low, cfg.swing_lookback)
    swing_highs = []
    swing_lows = []
    for s in raw_swings:
        if s['swing_type'] == 'high':
            swing_highs.append(SwingPoint(
                idx=s['idx'], price=s['price'], time=times[s['idx']],
                swing_type='high', strength=s['strength'],
            ))
        else:
            swing_lows.append(SwingPoint(
                idx=s['idx'], price=s['price'], time=times[s['idx']],
                swing_type='low', strength=s['strength'],
            ))

    # Trend scoring
    trend_score = _calc_trend_score(close, high, low, swing_highs, swing_lows, adx, plus_di, minus_di, ema_fast, ema_slow, cfg)
    if trend_score > 30:
        trend_direction = 'uptrend'
    elif trend_score < -30:
        trend_direction = 'downtrend'
    else:
        trend_direction = 'ranging'

    # Trend strength
    abs_t = abs(trend_score)
    if abs_t > 60:
        trend_strength = 'strong'
    elif abs_t > 25:
        trend_strength = 'moderate'
    else:
        trend_strength = 'weak'

    # BOS / CHoCH
    last_bos = _detect_bos(swing_highs, swing_lows)
    last_choch = _detect_choch(swing_highs, swing_lows, close)

    # Key S/R
    key_res = _nearest_level(high[-1], [s.price for s in swing_highs], above=True)
    key_sup = _nearest_level(low[-1], [s.price for s in swing_lows], above=False)

    return MarketStructureResult(
        trend_direction=trend_direction,
        trend_score=round(trend_score, 1),
        swing_highs=swing_highs,
        swing_lows=swing_lows,
        last_bos=last_bos,
        last_choch=last_choch,
        trend_strength=trend_strength,
        key_resistance=round(key_res, 5),
        key_support=round(key_sup, 5),
    )


def _detect_swings(high: np.ndarray, low: np.ndarray, lookback: int = 10) -> list[dict]:
    """Detect swing highs and lows using local extrema."""
    swings = []
    n = len(high)
    for i in range(lookback, n - lookback):
        # Swing high
        if high[i] == max(high[i - lookback:i + lookback + 1]):
            left = sum(1 for j in range(1, lookback + 1) if high[i] > high[i - j])
            right = sum(1 for j in range(1, lookback + 1) if high[i] > high[i + j])
            strength = min(left, right)  # how many pivots it dominates
            swings.append({'idx': i, 'price': high[i], 'swing_type': 'high', 'strength': strength})
        # Swing low
        if low[i] == min(low[i - lookback:i + lookback + 1]):
            left = sum(1 for j in range(1, lookback + 1) if low[i] < low[i - j])
            right = sum(1 for j in range(1, lookback + 1) if low[i] < low[i + j])
            strength = min(left, right)
            swings.append({'idx': i, 'price': low[i], 'swing_type': 'low', 'strength': strength})
    return swings


def _calc_trend_score(close: np.ndarray, high: np.ndarray, low: np.ndarray,
                       swing_highs: list, swing_lows: list,
                       adx: np.ndarray | None = None,
                       plus_di: np.ndarray | None = None,
                       minus_di: np.ndarray | None = None,
                       ema_fast: np.ndarray | None = None,
                       ema_slow: np.ndarray | None = None,
                       cfg: Config = Config()) -> float:
    """Compute trend score from -100 to +100 using EMA50 crossover, swing structure, and ADX/DI."""
    n = len(close)
    if n < 50:
        return 0

    # ── Price vs EMA50 (weight: 30%) ──
    # Use actual EMA values from the indicators module if available
    if ema_slow is not None and not np.isnan(ema_slow[-1]):
        ema_s = float(ema_slow[-1])
    else:
        ema_s = float(np.mean(close[-cfg.ema_slow:]))
    if ema_fast is not None and not np.isnan(ema_fast[-1]):
        ema_f = float(ema_fast[-1])
    else:
        ema_f = float(np.mean(close[-cfg.ema_fast:]))

    # Price relative to slow EMA — trend bias
    price_vs_ema50 = (close[-1] - ema_s) / max(ema_s, 1) * 100
    score = np.clip(price_vs_ema50 * 3, -30, 30)

    # Fast vs slow EMA crossover — adds conviction
    if ema_f > ema_s:
        score += 5   # bullish crossover
    elif ema_f < ema_s:
        score -= 5   # bearish crossover

    # ── Higher highs / higher lows (weight: 30%) ──
    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        last_hh = swing_highs[-2:]
        last_ll = swing_lows[-2:]
        hh_slope = 0
        if len(last_hh) >= 2 and last_hh[-1].idx > last_hh[-2].idx:
            hh_slope = (last_hh[-1].price - last_hh[-2].price) / (last_hh[-1].idx - last_hh[-2].idx) * 100
        ll_slope = 0
        if len(last_ll) >= 2 and last_ll[-1].idx > last_ll[-2].idx:
            ll_slope = (last_ll[-1].price - last_ll[-2].price) / (last_ll[-1].idx - last_ll[-2].idx) * 100
        score += np.clip((hh_slope + ll_slope) * 2, -30, 30)

    # ── ADX trend strength (weight: 25%) ──
    if adx is not None and not np.all(np.isnan(adx)):
        last_adx = float(adx[-1])
        if not np.isnan(last_adx):
            if last_adx > 25:
                # Strong trend — amplify existing direction
                score += 15  # trending = reliable
            elif last_adx > 20:
                score += 5   # developing trend
            else:
                score -= 8   # weak/no trend = ranging
        # DI direction (weight: 15%)
        if plus_di is not None and minus_di is not None:
            last_pdi = float(plus_di[-1])
            last_mdi = float(minus_di[-1])
            if not (np.isnan(last_pdi) or np.isnan(last_mdi)):
                if last_pdi > last_mdi:
                    score += 16  # bullish DI cross
                elif last_mdi > last_pdi:
                    score -= 16  # bearish DI cross
                # Magnify by ADX strength
                adx_val = last_adx if not np.isnan(last_adx) else 0
                if adx_val > 30:
                    score *= 1.3  # strong conviction
                elif adx_val > 25:
                    score *= 1.15
    else:
        # Fallback when no ADX data — small bias from price action only
        pass

    return float(np.clip(score, -100, 100))


def _detect_bos(swing_highs: list, swing_lows: list) -> Optional[str]:
    """Detect last Break of Structure."""
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return None
    # Bullish BOS: price breaks above last swing high
    if swing_highs[-1].price > swing_highs[-2].price:
        return 'bullish'
    # Bearish BOS: price breaks below last swing low
    if swing_lows[-1].price < swing_lows[-2].price:
        return 'bearish'
    return None


def _detect_choch(swing_highs: list, swing_lows: list, close: np.ndarray) -> Optional[str]:
    """Detect Change of Character (trend reversal signal)."""
    if len(swing_highs) < 3 or len(swing_lows) < 3:
        return None
    # In an uptrend, a lower low after higher high suggests reversal
    if (swing_highs[-2].price > swing_highs[-3].price and
            swing_lows[-1].price < swing_lows[-2].price):
        return 'bearish_choch'
    # In a downtrend, a higher high after lower low suggests reversal
    if (swing_lows[-2].price < swing_lows[-3].price and
            swing_highs[-1].price > swing_highs[-2].price):
        return 'bullish_choch'
    return None


def _nearest_level(price: float, levels: list[float], above: bool = True) -> float:
    """Find the nearest price level (resistance if above, support if below)."""
    if not levels:
        return price * (1.005 if above else 0.995)
    if above:
        above_levels = [l for l in levels if l > price]
        return min(above_levels) if above_levels else max(levels)
    else:
        below_levels = [l for l in levels if l < price]
        return max(below_levels) if below_levels else min(levels)
