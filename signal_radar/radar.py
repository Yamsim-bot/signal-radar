"""Signal Radar Engine — weighted scoring combining TA + FA + Sentiment."""

from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional
import numpy as np
import pandas as pd

from .config import Config
from .instruments import INSTRUMENTS, get_symbols, INSTRUMENT_LIST
from .data_fetcher import fetch_bars, fetch_all_bars
from .indicators import compute_all, compute_multi_tf
from .market_structure import analyze as ms_analyze
from .areas_of_value import analyze as aov_analyze
from .timing import analyze as timing_analyze
from .calendar import analyze as calendar_analyze
from .sentiment import analyze as sentiment_analyze
from .fundamental import analyze as fundamental_analyze, FundamentalBreakdown


@dataclass
class BiasExplanation:
    technical_score: float       # -100 to +100
    fundamental_score: float     # -100 to +100
    sentiment_score: float       # -100 to +100
    trend_direction: str
    trend_strength: str
    session_quality: str
    entry_timing: str
    key_support: float
    key_resistance: float
    nearest_support: float
    nearest_resistance: float
    aov_position: str
    confidence: int              # 0-100
    fundamental_breakdown: Optional['FundamentalBreakdown']
    explanation: str             # plain-english why this bias


@dataclass
class InstrumentRadar:
    symbol: str
    name: str
    category: str
    bias: str                    # 'Strong Buy', 'Buy', 'Neutral', 'Sell', 'Strong Sell'
    bias_score: int              # -100 to +100, numeric
    confidence: int              # 0-100
    strength: int                # 1-10 signal strength (directional intensity)
    price: float
    change_pct: float
    explanation: BiasExplanation


@dataclass
class RadarResult:
    timestamp: str
    instruments: list[InstrumentRadar]
    top_buy: list[InstrumentRadar]
    top_sell: list[InstrumentRadar]
    market_sentiment: str        # overall market bias
    market_score: float          # average of all scores
    fundamental: 'FundamentalResult'
    calendar: 'CalendarResult'
    sentiment: 'SentimentResult'


def scan(cfg: Config = Config()) -> RadarResult:
    """Run the full radar scan across all instruments."""
    now = datetime.now(timezone.utc)

    # Fetch data for all symbols
    all_data = fetch_all_bars(cfg.mt5_bars)

    # Run fundamental/sentiment/calendar once (shared across instruments)
    fund_result = fundamental_analyze()
    cal_result = calendar_analyze()
    sent_result = sentiment_analyze()

    # Per-instrument analysis
    instruments = []
    for symbol in get_symbols():
        instr = _instrument_by_symbol(symbol)
        if instr is None:
            continue

        df = all_data.get(symbol)
        if df is None or len(df) < 50:
            # Generate sample data for development
            from .data_fetcher import generate_sample_data
            df = generate_sample_data(symbol)

        result = _analyze_instrument(symbol, instr, df, fund_result, cal_result, sent_result, cfg)
        instruments.append(result)

    # Sort by bias score (strongest bullish first)
    instruments.sort(key=lambda x: x.bias_score, reverse=True)

    top_buy = [i for i in instruments if i.bias in ('Strong Buy', 'Buy')][:5]
    top_sell = [i for i in instruments if i.bias in ('Strong Sell', 'Sell')][:5]

    market_score = np.mean([i.bias_score for i in instruments]) if instruments else 0.0

    if market_score > 30:
        market_sentiment = 'Bullish'
    elif market_score < -30:
        market_sentiment = 'Bearish'
    else:
        market_sentiment = 'Neutral / Mixed'

    return RadarResult(
        timestamp=now.isoformat(),
        instruments=instruments,
        top_buy=top_buy,
        top_sell=top_sell,
        market_sentiment=market_sentiment,
        market_score=round(market_score, 1),
        fundamental=fund_result,
        calendar=cal_result,
        sentiment=sent_result,
    )


def _instrument_by_symbol(symbol: str) -> Optional[dict]:
    """Look up instrument spec by symbol."""
    return INSTRUMENTS.get(symbol, None)


def _signal_strength(bias: str, bias_score: float, confidence: int) -> int:
    """Map bias + confidence to a 1-10 strength scale.

    10 = Strong Buy + high conf     6 = Buy (moderate)
    9  = Strong Buy (moderate)      5 = Neutral with lean
    8  = Buy + high conf / Strong   4 = Neutral
       Sell                          3 = Sell
    7  = Buy / Strong Sell          2 = Sell / Strong Sell
                                    1 = Strong Sell (weak conviction)
    """
    abs_score = abs(bias_score)
    base = 5  # neutral midpoint

    if bias in ('Strong Buy', 'Strong Sell'):
        if confidence >= 80:
            base = 10 if bias == 'Strong Buy' else 1
        elif confidence >= 50:
            base = 9 if bias == 'Strong Buy' else 2
        else:
            base = 8 if bias == 'Strong Buy' else 3
    elif bias in ('Buy', 'Sell'):
        if confidence >= 70 and abs_score >= 40:
            base = 8 if bias == 'Buy' else 2
        elif abs_score >= 30:
            base = 7 if bias == 'Buy' else 3
        else:
            base = 6 if bias == 'Buy' else 4
    else:  # Neutral
        if abs_score >= 10:
            base = 5  # leaning neutral
        else:
            base = 4  # true neutral

    return max(1, min(10, base))


def _analyze_instrument(
    symbol: str,
    instr: dict,
    df: pd.DataFrame,
    fund_result: 'FundamentalResult',
    cal_result: 'CalendarResult',
    sent_result: 'SentimentResult',
    cfg: Config,
) -> InstrumentRadar:
    """Run full TA + blend with FA/sentiment for one instrument."""
    # Compute indicators
    df_ta = compute_all(df, cfg)

    # Multi-TF
    multi_tf = compute_multi_tf(df) if len(df) >= 100 else {}

    # Market Structure
    ms = ms_analyze(df_ta, cfg)

    # Areas of Value
    aov = aov_analyze(df_ta, cfg)

    # Timing
    timing = timing_analyze()

    # --- Technical Score (-100 to +100) ---
    tech_score = _compute_technical_score(ms, aov, timing, df_ta, cfg)

    # --- Fundamental Score per symbol ---
    fund_score = _compute_fundamental_score(
        symbol, instr, fund_result, cal_result
    )

    # --- Sentiment Score per symbol ---
    sent_score = _compute_sentiment_score(symbol, sent_result)

    # --- Blended Score ---
    blended = (
        tech_score * cfg.weight_technical
        + fund_score * cfg.weight_fundamental
        + sent_score * cfg.weight_sentiment
    )
    blended = float(max(-100, min(100, blended)))

    # --- Bias label ---
    bias, bias_score = _bias_from_score(blended)

    # --- Confidence ---
    confidence = _confidence(ms, aov, timing, tech_score, fund_score, sent_score)

    # --- Strength 1-10 ---
    strength = _signal_strength(bias, blended, confidence)

    # --- Price change ---
    close = df['close'].values
    current_price = float(close[-1])
    change_pct = float((close[-1] - close[-len(close) // 20]) / close[-len(close) // 20] * 100) if len(close) > 20 else 0.0

    # --- Fundamental Breakdown ---
    fund_breakdown = fund_result.breakdowns.get(symbol, None)

    # --- Explanation ---
    explanation = _build_explanation(symbol, bias, tech_score, fund_score, sent_score,
                                      ms, aov, timing, fund_breakdown)

    return InstrumentRadar(
        symbol=symbol,
        name=instr.get('description', symbol),
        category=instr.get('category', 'forex'),
        bias=bias,
        bias_score=round(bias_score, 1),
        confidence=confidence,
        strength=strength,
        price=round(current_price, instr.get('digits', 5)),
        change_pct=round(change_pct, 2),
        explanation=BiasExplanation(
            technical_score=round(tech_score, 1),
            fundamental_score=round(fund_score, 1),
            sentiment_score=round(sent_score, 1),
            trend_direction=ms.trend_direction,
            trend_strength=ms.trend_strength,
            session_quality=timing.session_quality,
            entry_timing=timing.entry_timing,
            key_support=round(ms.key_support, 5),
            key_resistance=round(ms.key_resistance, 5),
            nearest_support=round(aov.nearest_support, 5),
            nearest_resistance=round(aov.nearest_resistance, 5),
            aov_position=aov.current_position,
            confidence=confidence,
            fundamental_breakdown=fund_breakdown,
            explanation=explanation,
        ),
    )


def _compute_technical_score(ms, aov, timing, df_ta: pd.DataFrame, cfg: Config) -> float:
    """Compute technical score from market structure, AOV, and timing."""
    score = 0.0

    # Trend score (40% weight)
    score += ms.trend_score * 0.40

    # AOV score (25% weight)
    score += aov.aov_score * 0.25

    # Timing score (15% weight)
    timing_pct = (timing.timing_score / 100.0) * 100  # 0-100 → -50 to +50 range
    score += (timing_pct - 50) * 0.15

    # RSI momentum (10% weight)
    rsi = df_ta['rsi'].iloc[-1] if 'rsi' in df_ta.columns and not pd.isna(df_ta['rsi'].iloc[-1]) else 50
    if rsi > 70:
        score -= 10  # overbought
    elif rsi < 30:
        score += 10  # oversold

    # ADX trend strength (10% weight)
    adx = df_ta['adx'].iloc[-1] if 'adx' in df_ta.columns and not pd.isna(df_ta['adx'].iloc[-1]) else 25
    if adx > 25:
        # Strong trend — align with direction
        di_uptrend = df_ta.get('di_uptrend', pd.Series([False])).iloc[-1]
        if di_uptrend:
            score += 10
        else:
            score -= 10
    else:
        # Weak trend — range-bound
        score -= 5

    # BB squeeze (5% weight)
    bb_upper = df_ta['bb_upper'].iloc[-1] if 'bb_upper' in df_ta.columns else None
    bb_lower = df_ta['bb_lower'].iloc[-1] if 'bb_lower' in df_ta.columns else None
    close = df_ta['close'].iloc[-1]
    if bb_upper and bb_lower:
        bb_width = (bb_upper - bb_lower) / close
        if bb_width < 0.02:
            score += 5  # squeeze = potential breakout

    return float(max(-100, min(100, score)))


def _compute_fundamental_score(
    symbol: str,
    instr: dict,
    fund_result,
    cal_result,
) -> float:
    """Compute fundamental score for one instrument."""
    score = 0.0

    # CB stance contribution
    base = symbol[:3]
    quote = symbol[3:]

    cb_base = next((cb for cb in fund_result.central_bank_stances if cb.currency == base), None)
    cb_quote = next((cb for cb in fund_result.central_bank_stances if cb.currency == quote), None)

    if cb_base and cb_quote:
        # Rate differential direction
        rate_diff = cb_base.current_rate - cb_quote.current_rate
        score += rate_diff * 5  # scaled

        # Stance differential
        stance_diff = cb_base.stance_score - cb_quote.stance_score
        score += stance_diff * 0.3

    # Calendar: high impact events for this currency
    cal_events = [e for e in cal_result.events_this_week
                  if e.currency == base or e.currency == quote]
    for e in cal_events:
        if e.impact == 'High':
            score -= 5  # uncertainty
        elif e.impact == 'Medium':
            score -= 2

    # COT data
    cot_entry = next((c for c in fund_result.cot_data if c.currency == base), None)
    if cot_entry:
        if cot_entry.signal == 'bullish':
            score += 15
        elif cot_entry.signal == 'bearish':
            score -= 15

    # Blend with overall fundamental score
    score += fund_result.overall_score * 0.2

    # Factor breakdown (Growth, Inflation, Jobs, Sentiment, Trend, Seasonality)
    breakdown = fund_result.breakdowns.get(symbol)
    if breakdown:
        score += breakdown.total * 0.25  # add factor analysis weight

    return float(max(-100, min(100, score)))


def _compute_sentiment_score(symbol: str, sent_result) -> float:
    """Map overall sentiment to an instrument-specific score."""
    base = symbol[:3]
    quote = symbol[3:]

    score = sent_result.overall_score

    # Adjust based on currency-specific keyword mentions
    all_text = ' '.join(h.title.lower() for h in sent_result.headlines)

    # Currency-specific keywords
    base_positive = any(f'{base.lower()} rally' in all_text or f'{base.lower()} gains' in all_text for _ in [1])
    base_negative = any(f'{base.lower()} drop' in all_text or f'{base.lower()} falls' in all_text for _ in [1])

    if base_positive and not base_negative:
        score += 10
    elif base_negative and not base_positive:
        score -= 10

    # Sent from risk sentiment
    if sent_result.risk_on_count > sent_result.risk_off_count:
        # Risk-on: bullish for high-beta (AUD, NZD, stocks), bearish for safe havens (JPY, CHF, gold)
        high_beta = {'AUD', 'NZD', 'GBP', 'SP500', 'US30', 'NASDAQ', 'DAX40'}
        safe_haven = {'JPY', 'CHF', 'XAU'}
        if base in high_beta:
            score += 10
        elif base in safe_haven:
            score -= 10
    elif sent_result.risk_off_count > sent_result.risk_on_count:
        high_beta = {'AUD', 'NZD', 'GBP', 'SP500', 'US30', 'NASDAQ', 'DAX40'}
        safe_haven = {'JPY', 'CHF', 'XAU'}
        if base in high_beta:
            score -= 10
        elif base in safe_haven:
            score += 10

    return float(max(-100, min(100, score)))


def _bias_from_score(score: float) -> tuple[str, float]:
    """Convert numeric score to bias label."""
    if score >= 60:
        return 'Strong Buy', score
    elif score >= 20:
        return 'Buy', score
    elif score > -20:
        return 'Neutral', score
    elif score > -60:
        return 'Sell', score
    else:
        return 'Strong Sell', score


def _confidence(ms, aov, timing, tech_score, fund_score, sent_score) -> int:
    """Compute confidence 0-100."""
    c = 50  # base

    # Strong trend adds confidence
    if ms.trend_strength == 'strong':
        c += 15
    elif ms.trend_strength == 'moderate':
        c += 5

    # Timing adds confidence
    if timing.entry_timing == 'now':
        c += 15
    elif timing.entry_timing == 'soon':
        c += 5

    # Score alignment (if all three agree, higher confidence)
    scores = [tech_score, fund_score, sent_score]
    all_same_sign = all(s > 0 for s in scores) or all(s < 0 for s in scores)
    if all_same_sign:
        c += 10

    # Certainty from magnitude
    avg_abs = (abs(tech_score) + abs(fund_score) + abs(sent_score)) / 3
    c += int(avg_abs / 5)

    # Cap
    return min(100, max(0, c))


def _build_explanation(symbol: str, bias: str, tech: float, fund: float, sent: float,
                        ms, aov, timing, fund_breakdown=None) -> str:
    """Build plain-english explanation for instrument bias."""
    parts = []

    # Bias summary
    parts.append(f"{symbol} is **{bias}** with a technical score of {tech:.0f}, "
                 f"fundamental score of {fund:.0f}, and sentiment score of {sent:.0f}.")

    # Trend
    parts.append(f"Trend: {ms.trend_direction} ({ms.trend_strength}).")

    # Key levels
    parts.append(f"Key support at {ms.key_support:.5f}, resistance at {ms.key_resistance:.5f}.")

    # AOV position
    parts.append(f"Price is currently {aov.current_position.replace('_', ' ')}.")

    # Timing
    if timing.in_blackout:
        parts.append("In news blackout — avoid entry.")
    elif timing.entry_timing == 'now':
        parts.append("Prime entry window (London/NY overlap).")
    elif timing.entry_timing == 'soon':
        parts.append(f"Good entry timing ({timing.current_session} session).")
    else:
        parts.append(f"Suboptimal timing ({timing.current_session} session, quality: {timing.session_quality}).")

    # Fundamental breakdown
    if fund_breakdown:
        parts.append("Fundamental factors -- "
                     f"Growth: {fund_breakdown.growth:+.0f}, "
                     f"Inflation: {fund_breakdown.inflation:+.0f}, "
                     f"Jobs: {fund_breakdown.jobs:+.0f}, "
                     f"Sentiment: {fund_breakdown.sentiment:+.0f}, "
                     f"Trend: {fund_breakdown.trend:+.0f}, "
                     f"Seasonality: {fund_breakdown.seasonality:+.0f}.")

    return ' '.join(parts)
