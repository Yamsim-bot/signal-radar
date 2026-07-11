"""MATE Framework — Market Direction, Area, Timing, Exit.

Transforms raw analysis from market_structure, areas_of_value, and timing
into a structured MATE breakdown for display and decision-making.
"""
from dataclasses import dataclass, field
from typing import Optional
import numpy as np
import pandas as pd

from .config import Config
from .market_structure import MarketStructureResult
from .areas_of_value import AreasOfValueResult
from .timing import TimingResult


@dataclass
class MateExit:
    """Exit strategy recommendations."""
    tp: Optional[float] = None          # Take profit price
    tp_distance: Optional[float] = None # Distance from current in price units
    sl: Optional[float] = None          # Stop loss price
    sl_distance: Optional[float] = None # Distance from current in price units
    trailing_activation: Optional[float] = None  # Price to activate trailing
    invalidation: Optional[float] = None         # Price that invalidates the thesis
    summary: str = "No trade signal"    # Plain-english exit note


@dataclass
class MateResult:
    """Structured MATE analysis for one instrument."""

    # ── M: Market Direction ──
    market_label: str           # "Uptrend Strong", "Downtrend Moderate", "Ranging"
    market_score: float         # -100 (strong downtrend) to +100 (strong uptrend)
    market_detail: str          # What drives this (EMA, swings, ADX)

    # ── A: Area of Value ──
    area_label: str             # "At Support ⭐", "At Resistance ⚠️", "In No Man's Land"
    area_detail: str            # Proximity to nearest zones
    area_score: float           # -100 (bearish positioning) to +100 (bullish)

    # ── T: Timing ──
    timing_label: str           # "Prime ✅", "Active", "Quiet ⏳", "Avoid ❌"
    timing_detail: str          # Current session + quality
    timing_score: int           # 0-100

    # ── E: Exit ──
    exit_plan: MateExit = field(default_factory=MateExit)

    # ── Aggregate ──
    overall_quality: str = "neutral"  # 'favorable', 'neutral', 'unfavorable'
    drivers: list[str] = field(default_factory=list)  # Key factors driving the score


def analyze_mate(
    ms: MarketStructureResult,
    aov: AreasOfValueResult,
    timing: TimingResult,
    df_ta: pd.DataFrame,
    cfg: Config = Config(),
) -> MateResult:
    """Produce a full MATE analysis from existing TA modules.

    Parameters
    ----------
    ms : MarketStructureResult
        From market_structure.analyze()
    aov : AreasOfValueResult
        From areas_of_value.analyze()
    timing : TimingResult
        From timing.analyze()
    df_ta : pd.DataFrame
        DataFrame with computed indicators (must have 'close', optionally 'atr')
    cfg : Config
        Radar configuration

    Returns
    -------
    MateResult
        Structured MATE breakdown ready for display.
    """
    close = float(df_ta['close'].iloc[-1])
    atr = float(df_ta['atr'].iloc[-1]) if 'atr' in df_ta.columns and not pd.isna(df_ta['atr'].iloc[-1]) else 0.0
    drivers: list[str] = []

    # ──────────────────────────────────────────────
    # M — Market Direction
    # ──────────────────────────────────────────────
    trend_label = _market_label(ms.trend_direction, ms.trend_strength)
    m_detail = _market_detail(ms, df_ta)
    if ms.trend_strength == 'strong':
        drivers.append(f"{trend_label} — trending with conviction")
    elif ms.trend_direction != 'ranging':
        drivers.append(f"{trend_label} — direction established")

    # ──────────────────────────────────────────────
    # A — Area of Value
    # ──────────────────────────────────────────────
    area_label, area_detail = _area_label(aov, close)
    if aov.aov_score > 20:
        drivers.append(f"Price in value zone ({aov.current_position.replace('_', ' ')})")
    elif aov.aov_score < -20:
        drivers.append(f"Price extended ({aov.current_position.replace('_', ' ')})")

    # ──────────────────────────────────────────────
    # T — Timing
    # ──────────────────────────────────────────────
    t_label, t_detail = _timing_label(timing)
    if timing.entry_timing == 'now':
        drivers.append("Prime entry window (London/NY overlap)")
    elif timing.entry_timing == 'soon':
        drivers.append(f"Active session ({timing.current_session})")
    if timing.in_blackout:
        drivers.append("⚠️ News blackout in effect — avoid entry")

    # ──────────────────────────────────────────────
    # E — Exit Strategy
    # ──────────────────────────────────────────────
    exit_plan = _compute_exit(ms, aov, close, atr, m_detail, area_detail)

    # ──────────────────────────────────────────────
    # Overall quality
    # ──────────────────────────────────────────────
    # Aggregate alignment: are M, A, and T all pointing the same way?
    m_aligned = ms.trend_score > 20  # uptrend
    m_bearish = ms.trend_score < -20
    a_aligned = aov.aov_score > 10
    a_bearish = aov.aov_score < -10
    t_aligned = timing.timing_score >= 70

    if (m_aligned or m_bearish) and a_aligned and t_aligned:
        if m_aligned:
            overall = "favorable"
            drivers.append("✅ M+A+T aligned — high-probability setup")
        else:
            overall = "unfavorable"
            drivers.append("❌ M+A+T aligned bearish — avoid long positions")
    elif m_aligned and not a_aligned and t_aligned:
        overall = "neutral"
        drivers.append("Trend up but price in no man's land — wait for pullback to support")
    elif m_aligned or t_aligned:
        overall = "favorable"
    elif m_bearish or a_bearish:
        overall = "unfavorable"
    else:
        overall = "neutral"

    # Limit drivers to top 3-4
    if len(drivers) > 4:
        drivers = drivers[:4]

    return MateResult(
        market_label=trend_label,
        market_score=round(ms.trend_score, 1),
        market_detail=m_detail,
        area_label=area_label,
        area_detail=area_detail,
        area_score=round(aov.aov_score, 1),
        timing_label=t_label,
        timing_detail=t_detail,
        timing_score=timing.timing_score,
        exit_plan=exit_plan,
        overall_quality=overall,
        drivers=drivers,
    )


# ── M helpers ──────────────────────────────────────────

def _market_label(direction: str, strength: str) -> str:
    """Combine direction + strength into a display label."""
    if direction == 'ranging':
        return 'Ranging'
    labels = {
        ('uptrend', 'strong'): '📈 Uptrend Strong',
        ('uptrend', 'moderate'): '📈 Uptrend',
        ('uptrend', 'weak'): 'Uptrend Weak',
        ('downtrend', 'strong'): '📉 Downtrend Strong',
        ('downtrend', 'moderate'): '📉 Downtrend',
        ('downtrend', 'weak'): 'Downtrend Weak',
    }
    return labels.get((direction, strength), direction.capitalize())


def _market_detail(ms: MarketStructureResult, df_ta: pd.DataFrame) -> str:
    """One-liner about what drives the trend view."""
    parts = []
    close = float(df_ta['close'].iloc[-1])
    ema_slow = float(df_ta['ema_slow'].iloc[-1]) if 'ema_slow' in df_ta.columns and not pd.isna(df_ta['ema_slow'].iloc[-1]) else None

    if ema_slow:
        if close > ema_slow * 1.01:
            parts.append(f"Price {((close/ema_slow - 1) * 100):.1f}% above EMA{ms.ema_slow if hasattr(ms, 'ema_slow') else 50}")
        elif close < ema_slow * 0.99:
            parts.append(f"Price {((ema_slow/close - 1) * 100):.1f}% below EMA{ms.ema_slow if hasattr(ms, 'ema_slow') else 50}")

    if ms.last_bos:
        parts.append(f"BOS: {ms.last_bos}")
    if ms.last_choch:
        parts.append(f"CHoCH: {ms.last_choch}")

    if ms.trend_strength == 'strong':
        adx_val = float(df_ta['adx'].iloc[-1]) if 'adx' in df_ta.columns and not pd.isna(df_ta['adx'].iloc[-1]) else None
        if adx_val:
            parts.append(f"ADX {adx_val:.0f}")
    elif ms.trend_strength == 'weak':
        adx_val = float(df_ta['adx'].iloc[-1]) if 'adx' in df_ta.columns and not pd.isna(df_ta['adx'].iloc[-1]) else None
        if adx_val:
            parts.append(f"ADX {adx_val:.0f} — low momentum")

    return ' | '.join(parts) if parts else ms.trend_strength.capitalize()


# ── A helpers ──────────────────────────────────────────

def _area_label(aov: AreasOfValueResult, price: float) -> tuple[str, str]:
    """Human-readable area label with detail."""
    pos = aov.current_position.replace('_', ' ')
    detail_parts = []

    # Label with icon
    labels = {
        'at support': ('⭐ At Support', 'Price touching demand zone — potential bounce'),
        'at resistance': ('⚠️ At Resistance', 'Price touching supply zone — potential reversal'),
        'above resistance': ('🚀 Above Resistance', 'Price has broken above resistance'),
        'below support': ('💀 Below Support', 'Price has broken below support'),
        'in no mans land': ('➖ In No Man\'s Land', 'Price between S/R — no clear edge'),
    }
    label, fallback_detail = labels.get(pos, (pos, ''))

    # Add zone strength if available
    for zone in aov.support_zones:
        if zone.price_low <= price <= zone.price_high:
            detail_parts.append(f"Support (strength {zone.strength}/5, {zone.touches}t)")
            break
    for zone in aov.resistance_zones:
        if zone.price_low <= price <= zone.price_high:
            detail_parts.append(f"Resistance (strength {zone.strength}/5, {zone.touches}t)")
            break

    # Nearest levels
    if aov.nearest_support:
        dist_s = abs(price - aov.nearest_support) / price * 100
        if dist_s < 0.5:
            detail_parts.append(f"Support {dist_s:.2f}% away")
    if aov.nearest_resistance:
        dist_r = abs(price - aov.nearest_resistance) / price * 100
        if dist_r < 0.5:
            detail_parts.append(f"Resistance {dist_r:.2f}% away")

    detail = detail_parts[0] if detail_parts else fallback_detail
    return label, detail


# ── T helpers ──────────────────────────────────────────

def _timing_label(timing: TimingResult) -> tuple[str, str]:
    """Human-readable timing label with session detail."""
    labels = {
        'now': ('✅ Prime', f"{timing.current_session} — optimal liquidity"),
        'soon': ('Active', f"{timing.current_session} — moderate liquidity"),
        'wait': ('⏳ Quiet', f"{timing.current_session} — low liquidity"),
        'avoid': ('❌ Avoid', "Off hours or news blackout"),
    }
    label, detail = labels.get(timing.entry_timing, (timing.entry_timing, ''))

    if timing.in_blackout:
        label = '🔇 Blackout'
        detail = f"News blackout — {timing.next_news_blackout or 'upcoming release'}"

    return label, detail


# ── E helpers ──────────────────────────────────────────

def _compute_exit(
    ms: MarketStructureResult,
    aov: AreasOfValueResult,
    price: float,
    atr: float,
    m_detail: str,
    a_detail: str,
) -> MateExit:
    """Compute exit levels: TP (ATR×2), SL, trailing, invalidation."""
    if atr == 0 or atr is None:
        return MateExit(summary="ATR not available — cannot compute exits")

    # Direction bias from trend
    is_bullish = ms.trend_score > 10
    is_bearish = ms.trend_score < -10

    if not is_bullish and not is_bearish:
        # Ranging — no strong directional bias
        return MateExit(summary="No directional bias — wait for breakout or pullback to zones")

    exit_summary_parts = []

    if is_bullish:
        # Long bias
        tp = round(price + atr * 2, 5)
        sl = round(price - atr * 1.5, 5)
        trailing = round(price + atr * 1.5, 5)  # trail once 1.5x ATR profit
        invalidation = round(price - atr * 2.5, 5)

        tp_pips_pct = ((tp / price) - 1) * 100
        sl_pips_pct = ((sl / price) - 1) * 100

        exit_summary_parts.append(f"TP: {tp} (+{tp_pips_pct:.1f}%)")
        exit_summary_parts.append(f"SL: {sl} ({sl_pips_pct:.1f}%)")
        exit_summary_parts.append(f"Trail after {trailing} (+{(((trailing/price)-1)*100):.1f}%)")
        exit_summary_parts.append(f"Invalidate below {invalidation}")

        # Check if near a good entry zone
        if aov.current_position in ('at_support', 'in_no_mans_land'):
            exit_summary_parts.append("— Good R:R zone")
        elif aov.current_position in ('at_resistance', 'above_resistance'):
            exit_summary_parts.append("— ⚠️ Near resistance")

    else:
        # Short bias
        tp = round(price - atr * 2, 5)
        sl = round(price + atr * 1.5, 5)
        trailing = round(price - atr * 1.5, 5)
        invalidation = round(price + atr * 2.5, 5)

        tp_pips_pct = ((tp / price) - 1) * 100
        sl_pips_pct = ((sl / price) - 1) * 100

        exit_summary_parts.append(f"TP: {tp} ({tp_pips_pct:.1f}%)")
        exit_summary_parts.append(f"SL: {sl} (+{abs(sl_pips_pct):.1f}%)")
        exit_summary_parts.append(f"Trail after {trailing} ({(((trailing/price)-1)*100):.1f}%)")
        exit_summary_parts.append(f"Invalidate above {invalidation}")

        if aov.current_position in ('at_resistance', 'in_no_mans_land'):
            exit_summary_parts.append("— Good R:R zone")
        elif aov.current_position in ('at_support', 'below_support'):
            exit_summary_parts.append("— ⚠️ Near support")

    return MateExit(
        tp=tp,
        tp_distance=round(abs(tp - price), 5),
        sl=sl,
        sl_distance=round(abs(sl - price), 5),
        trailing_activation=trailing,
        invalidation=invalidation,
        summary=' | '.join(exit_summary_parts),
    )
