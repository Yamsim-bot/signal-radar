"""Areas of Value — support/resistance, order blocks, supply/demand, Fibonacci."""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional

from .config import Config
from .market_structure import analyze, SwingPoint


@dataclass
class Zone:
    zone_type: str        # 'support', 'resistance', 'order_block', 'supply', 'demand'
    price_high: float
    price_low: float
    strength: int         # 1-5 based on touches / volume
    touches: int
    is_fresh: bool        # hasn't been tested yet


@dataclass
class AreasOfValueResult:
    support_zones: list[Zone]
    resistance_zones: list[Zone]
    order_blocks: list[Zone]
    fib_levels: dict[str, float]   # {'0.0': x, '0.382': y, ...}
    nearest_support: float
    nearest_resistance: float
    current_position: str          # 'above_resistance', 'in_zone', 'below_support'
    aov_score: float               # -100 (bearish) to +100 (bullish)


def analyze(df: pd.DataFrame, cfg: Config = Config()) -> AreasOfValueResult:
    """Complete areas of value analysis."""
    high = df['high'].values
    low = df['low'].values
    close = df['close'].values
    current = close[-1]

    # Get market structure for swing points
    ms = __import__('signal_radar.market_structure', fromlist=['analyze']).analyze(df, cfg)
    swing_highs = ms.swing_highs
    swing_lows = ms.swing_lows

    # Build S/R zones from clustered swings
    sup_zones = _cluster_zones(swing_lows, 'support')
    res_zones = _cluster_zones(swing_highs, 'resistance')

    # Order blocks
    obs = _find_order_blocks(df)

    # Fibonacci
    fibs = _fib_retracement(swing_highs, swing_lows, close)

    # Position relative to zones
    pos = _current_position(current, sup_zones, res_zones)

    # Score
    score = _aov_score(current, res_zones, sup_zones, obs)

    nearest_s = _nearest(current, [(z.price_low + z.price_high) / 2 for z in sup_zones], above=False)
    nearest_r = _nearest(current, [(z.price_low + z.price_high) / 2 for z in res_zones], above=True)

    return AreasOfValueResult(
        support_zones=sup_zones,
        resistance_zones=res_zones,
        order_blocks=obs,
        fib_levels=fibs,
        nearest_support=round(nearest_s, 5) if nearest_s else round(current * 0.99, 5),
        nearest_resistance=round(nearest_r, 5) if nearest_r else round(current * 1.01, 5),
        current_position=pos,
        aov_score=round(score, 1),
    )


def _cluster_zones(swings: list[SwingPoint], zone_type: str,
                   cluster_pips: float = 5) -> list[Zone]:
    """Cluster nearby swing points into support/resistance zones."""
    if not swings:
        return []

    pip_size = 0.01
    prices = sorted([s.price for s in swings], reverse=(zone_type == 'resistance'))
    zones = []
    used = set()

    for i, p in enumerate(prices):
        if i in used:
            continue
        cluster = [p]
        used.add(i)
        for j in range(i + 1, len(prices)):
            if j in used:
                continue
            if abs(prices[j] - p) <= cluster_pips * pip_size:
                cluster.append(prices[j])
                used.add(j)
        avg_price = np.mean(cluster)
        spread = max(cluster) - min(cluster)
        strength = min(len(cluster), 5)
        touches = len(cluster)
        zones.append(Zone(
            zone_type=zone_type,
            price_high=round(max(cluster), 5),
            price_low=round(min(cluster), 5),
            strength=strength,
            touches=touches,
            is_fresh=False,
        ))

    zones.sort(key=lambda z: z.strength, reverse=True)
    return zones[:10]  # top 10


def _find_order_blocks(df: pd.DataFrame) -> list[Zone]:
    """Identify order blocks — the last candle before a strong directional move."""
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    obs = []

    for i in range(2, len(close) - 1):
        # Bullish OB: big green candle preceded by a down candle
        body_i = abs(close[i] - df['open'].values[i])
        body_i1 = abs(close[i - 1] - df['open'].values[i - 1])
        if (close[i] > close[i - 1] and body_i > body_i1 * 2 and
                close[i - 1] < close[i - 2]):
            obs.append(Zone(
                zone_type='demand',
                price_high=round(high[i - 1], 5),
                price_low=round(low[i - 1], 5),
                strength=2, touches=0, is_fresh=True,
            ))
        # Bearish OB
        if (close[i] < close[i - 1] and body_i > body_i1 * 2 and
                close[i - 1] > close[i - 2]):
            obs.append(Zone(
                zone_type='supply',
                price_high=round(high[i - 1], 5),
                price_low=round(low[i - 1], 5),
                strength=2, touches=0, is_fresh=True,
            ))

    return obs[-5:] if len(obs) > 5 else obs


def _fib_retracement(swing_highs: list, swing_lows: list,
                     close: np.ndarray) -> dict[str, float]:
    """Calculate Fibonacci retracement levels from last major swing."""
    if len(swing_highs) < 1 or len(swing_lows) < 1:
        return {}
    # Use most recent complete swing
    if swing_highs[-1].idx > swing_lows[-1].idx:
        high_pt = swing_highs[-1].price
        low_pt = swing_lows[-1].price
    else:
        high_pt = swing_highs[0].price if swing_highs else close[-1]
        low_pt = swing_lows[0].price if swing_lows else close[-1]

    if high_pt == low_pt:
        return {}

    diff = high_pt - low_pt
    levels = {f'{ratio:.3f}': round(high_pt - ratio * diff, 5)
              for ratio in [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]}
    levels['0.0'] = round(high_pt, 5)
    levels['1.0'] = round(low_pt, 5)
    return levels


def _current_position(price: float, supports: list[Zone], resistances: list[Zone]) -> str:
    """Where is price relative to known zones?"""
    for r in resistances:
        if r.price_low <= price <= r.price_high:
            return 'at_resistance'
        if price > r.price_high and r.strength >= 3:
            return 'above_resistance'
    for s in supports:
        if s.price_low <= price <= s.price_high:
            return 'at_support'
        if price < s.price_low and s.strength >= 3:
            return 'below_support'
    return 'in_no_mans_land'


def _aov_score(current: float, res: list[Zone], sup: list[Zone],
               obs: list[Zone]) -> float:
    """Score current position: positive = bullish AOV."""
    nearest_r = min((z.price_low for z in res), default=None, key=lambda x: abs(x - current) if x > current else float('inf'))
    nearest_s = max((z.price_high for z in sup), default=None, key=lambda x: abs(x - current) if x < current else -float('inf'))

    score = 0.0
    if nearest_r and nearest_s:
        # Distance to resistance vs support
        dist_r = abs(nearest_r - current)
        dist_s = abs(nearest_s - current)
        if dist_s < dist_r:
            score += 20  # closer to support = room to run up
        elif dist_r < dist_s:
            score -= 20

    # Order blocks
    bullish_obs = sum(1 for ob in obs if ob.zone_type == 'demand')
    bearish_obs = sum(1 for ob in obs if ob.zone_type == 'supply')
    score += min(bullish_obs - bearish_obs, 20)

    return float(np.clip(score, -100, 100))


def _nearest(price: float, levels: list[float], above: bool = True) -> Optional[float]:
    """Find nearest level."""
    filtered = [l for l in levels if l and ((above and l > price) or (not above and l < price))]
    if not filtered:
        return None
    return min(filtered, key=lambda x: abs(x - price))
