"""Yams Radar Engine — weighted scoring combining TA + FA + Sentiment."""

from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional
import numpy as np
import pandas as pd

from .config import Config
from .instruments import INSTRUMENTS, get_symbols, INSTRUMENT_LIST
from .data_fetcher import fetch_bars, fetch_all_bars, fetch_live_prices
from .indicators import compute_all, compute_multi_tf
from .market_structure import analyze as ms_analyze
from .areas_of_value import analyze as aov_analyze
from .timing import analyze as timing_analyze
from .eco_calendar import analyze as calendar_analyze
from .sentiment import analyze as sentiment_analyze
from .fundamental import analyze as fundamental_analyze, FundamentalBreakdown
from .confluence import fetch_all as confluence_fetch_all, fetch_pivots
from .mate import analyze_mate, MateResult


@dataclass
class BiasExplanation:
    technical_score: float       # -100 to +100
    fundamental_score: float     # -100 to +100
    sentiment_score: float       # -100 to +100
    confluence_score: float      # -100 to +100 (retail sentiment contrarian + pivot positioning)
    myfxbook_signal: str         # 'bullish','bearish','neutral' or 'N/A'
    retail_long_pct: float       # myFXbook % long, for UI display
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
    explanation: str             # plain-english why this bias
    fundamental_breakdown: Optional['FundamentalBreakdown'] = None
    mate: Optional['MateResult'] = None  # MATE framework analysis


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

    # Patch live prices into the last bar of each DataFrame
    # Uses Frankfurter API for forex (fast, free) + Yahoo batch for stocks/indices/commodities
    live_prices = fetch_live_prices(list(all_data.keys()))
    for sym, price in live_prices.items():
        if sym in all_data and price and price > 0:
            try:
                all_data[sym].loc[all_data[sym].index[-1], 'close'] = price
            except (KeyError, IndexError):
                pass  # Non-fatal — keep sample close

    # Run fundamental/sentiment/calendar once (shared across instruments)
    fund_result = fundamental_analyze(quick=True)
    cal_result = calendar_analyze()
    sent_result = sentiment_analyze(quick=True)  # instant — sample headlines

    # AI Enhancement: multi-LLM consensus replaces VADER keyword scoring
    # Runs in parallel (~2-4s if cache cold, ~0ms if cached)
    if cfg.use_ai_sentiment:
        try:
            from .sentiment import enhance_with_ai
            enhance_with_ai(
                sent_result,
                live_prices=live_prices,
                calendar_events=cal_result.events_this_week,
                cfg=cfg,
            )
        except ImportError:
            pass  # AI module not available — VADER fallback is fine

    # Confluence data from myFXbook, FXStreet, Finviz (quick, non-blocking)
    all_syms = get_symbols()
    confluence_data = confluence_fetch_all(all_syms)
    pivot_data = fetch_pivots(all_syms)

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

        result = _analyze_instrument(symbol, instr, df, fund_result, cal_result, sent_result,
                                       confluence_data.get(symbol), pivot_data.get(symbol), cfg)
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
    confluence_entry,
    pivot_entry,
    cfg: Config,
) -> InstrumentRadar:
    """Run full TA + blend with FA/sentiment for one instrument."""
    # Compute indicators
    df_ta = compute_all(df, cfg)

    # Multi-TF
    multi_tf = compute_multi_tf(df) if len(df) >= 60 else {}

    # Market Structure
    ms = ms_analyze(df_ta, cfg)

    # Areas of Value
    aov = aov_analyze(df_ta, cfg)

    # Timing
    timing = timing_analyze()

    # --- Mate Analysis ---
    mate_result = analyze_mate(ms, aov, timing, df_ta, cfg)

    # --- Technical Score (-100 to +100) ---
    tech_score = _compute_technical_score(ms, aov, timing, df_ta, cfg)

    # --- Fundamental Score per symbol ---
    fund_score = _compute_fundamental_score(
        symbol, instr, fund_result, cal_result
    )

    # --- Sentiment Score per symbol ---
    sent_score = _compute_sentiment_score(symbol, sent_result)

    # --- Price change ---
    close = df['close'].values
    current_price = float(close[-1])
    change_pct = float((close[-1] - close[-len(close) // 20]) / close[-len(close) // 20] * 100) if len(close) > 20 else 0.0

    # --- Confluence Score (retail contrarian + pivot positioning) ---
    conf_score, myfxbook_signal, retail_long_pct = _compute_confluence_score(
        confluence_entry, pivot_entry, current_price
    )

    # --- Blended Score ---
    blended = (
        tech_score * cfg.weight_technical
        + fund_score * cfg.weight_fundamental
        + sent_score * cfg.weight_sentiment
        + conf_score * cfg.weight_confluence
    )
    blended = float(max(-100, min(100, blended)))

    # --- Bias label ---
    bias, bias_score = _bias_from_score(blended)

    # --- Confidence ---
    confidence = _confidence(ms, aov, timing, tech_score, fund_score, sent_score, sent_result)

    # --- Strength 1-10 ---
    strength = _signal_strength(bias, blended, confidence)

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
            confluence_score=round(conf_score, 1),
            myfxbook_signal=myfxbook_signal,
            retail_long_pct=round(retail_long_pct, 1),
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
            explanation=explanation,
            fundamental_breakdown=fund_breakdown,
            mate=mate_result,
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

    # Crypto: use overall market sentiment + risk, no CB/COT data
    if instr.get('crypto'):
        score += fund_result.overall_score * 0.3
        # Crypto thrives on risk appetite
        if fund_result.risk_sentiment == 'risk_on':
            score += 20
        elif fund_result.risk_sentiment == 'risk_off':
            score -= 20
        return float(max(-100, min(100, score)))

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
    """Map overall sentiment to an instrument-specific score.

    Two modes:
      1. AI-powered (preferred) — uses multi-LLM consensus when sent_result
         has ai_analysis attached. Smarter, context-aware, replaces VADER.
      2. VADER fallback — keyword matching for when no AI is available.
    """
    base = symbol[:3]
    quote = symbol[3:]
    instr = INSTRUMENTS.get(symbol, {})

    # ═══════════════════════════════════════════════════════════
    # AI-POWERED PATH — multi-LLM consensus (Claude+Gemini+DeepSeek+Grok)
    # ═══════════════════════════════════════════════════════════
    ai = getattr(sent_result, 'ai_analysis', None)
    if ai is not None:
        score = ai.overall_score

        # AI currency lean — check individual models for consensus on a currency
        currency_leads = {}
        for r in ai.individual_results:
            if r.currency_lean:
                c = r.currency_lean.upper()
                currency_leads[c] = currency_leads.get(c, 0) + 1
        top_lean = max(currency_leads, key=currency_leads.get) if currency_leads else None

        if top_lean and currency_leads[top_lean] >= len(ai.individual_results) * 0.4:
            if top_lean == base:
                score += 15  # AI consensus picked this base currency -> bullish lean
            elif top_lean == quote:
                score -= 10  # AI consensus picked quote currency -> bearish for pair

        # Risk appetite adjustments (AI-aware, better than keyword-counting)
        high_beta = {'AUD', 'NZD', 'GBP', 'SP500', 'US30', 'NASDAQ', 'DAX40'}
        safe_haven = {'JPY', 'CHF', 'XAU'}
        is_high_beta = base in high_beta or symbol in high_beta
        is_safe_haven = base in safe_haven or symbol in safe_haven

        if ai.risk_appetite == 'risk_on':
            if is_high_beta:
                score += 12
            elif is_safe_haven:
                score -= 12
        elif ai.risk_appetite == 'risk_off':
            if is_high_beta:
                score -= 12
            elif is_safe_haven:
                score += 12

        # Crypto: ultra-high beta amplification
        if instr.get('crypto'):
            if ai.risk_appetite == 'risk_on':
                score += 20
            elif ai.risk_appetite == 'risk_off':
                score -= 20

        return float(max(-100, min(100, score)))

    # ═══════════════════════════════════════════════════════════
    # VADER FALLBACK PATH — keyword-based (legacy)
    # ═══════════════════════════════════════════════════════════
    score = sent_result.overall_score

    # Adjust based on currency-specific keyword mentions
    all_text = ' '.join(h.title.lower() for h in sent_result.headlines)

    # Crypto-specific sentiment
    if instr.get('crypto'):
        crypto_names = {
            'BTCUSD': 'bitcoin', 'ETHUSD': 'ethereum', 'SOLUSD': 'solana',
            'XRPUSD': 'xrp', 'ADAUSD': 'cardano', 'DOGEUSD': 'dogecoin',
            'AVAXUSD': 'avalanche', 'LINKUSD': 'chainlink', 'DOTUSD': 'polkadot',
            'LTCUSD': 'litecoin', 'SUIUSD': 'sui', 'APTUSD': 'aptos',
        }
        name = crypto_names.get(symbol, base.lower())

        crypto_bullish = any(f'{name} rally' in all_text or f'{name} surge' in all_text
                              or f'{name} jumps' in all_text or f'{name} gains' in all_text
                              for _ in [1])
        crypto_bearish = any(f'{name} crash' in all_text or f'{name} drop' in all_text
                              or f'{name} falls' in all_text or f'{name} slump' in all_text
                              for _ in [1])

        if crypto_bullish and not crypto_bearish:
            score += 25
        elif crypto_bearish and not crypto_bullish:
            score -= 25

        # Amplified risk sentiment for crypto
        if sent_result.risk_on_count > sent_result.risk_off_count:
            score += 20
        elif sent_result.risk_off_count > sent_result.risk_on_count:
            score -= 20

        return float(max(-100, min(100, score)))

    # Currency-specific keywords
    base_positive = any(f'{base.lower()} rally' in all_text or f'{base.lower()} gains' in all_text for _ in [1])
    base_negative = any(f'{base.lower()} drop' in all_text or f'{base.lower()} falls' in all_text for _ in [1])

    if base_positive and not base_negative:
        score += 10
    elif base_negative and not base_positive:
        score -= 10

    # Risk sentiment adjustments (keyword-counting based)
    high_beta = {'AUD', 'NZD', 'GBP', 'SP500', 'US30', 'NASDAQ', 'DAX40'}
    safe_haven = {'JPY', 'CHF', 'XAU'}
    if sent_result.risk_on_count > sent_result.risk_off_count:
        if base in high_beta:
            score += 10
        elif base in safe_haven:
            score -= 10
    elif sent_result.risk_off_count > sent_result.risk_on_count:
        if base in high_beta:
            score -= 10
        elif base in safe_haven:
            score += 10

    return float(max(-100, min(100, score)))


def _compute_confluence_score(confluence_entry, pivot_entry, price: float) -> tuple[float, str, float]:
    """Compute confluence score from myFXbook retail sentiment + pivot positioning.

    Returns (score: -100 to +100, myfxbook_signal: str, retail_long_pct: float).
    Retail sentiment is used as a contrarian indicator — extreme retail positioning
    often signals reversals.

    Pivot positioning adds a second layer: price above pivot = bullish, below = bearish.
    """
    score = 0.0
    myfxbook_signal = 'N/A'
    retail_long_pct = 50.0

    if confluence_entry is not None:
        # Contrarian retail sentiment
        contrarian = confluence_entry.contrarian_score()
        score += contrarian

        # Pivot position
        pvt_score = confluence_entry.pivot_score(price)
        score += pvt_score * 0.5  # half weight vs retail sentiment

        # Pass through for UI display
        if confluence_entry.myfxbook is not None:
            myfxbook_signal = confluence_entry.myfxbook.signal
            retail_long_pct = confluence_entry.myfxbook.long_pct
        elif confluence_entry.fxstreet_sentiment is not None:
            myfxbook_signal = confluence_entry.fxstreet_sentiment.signal
            retail_long_pct = confluence_entry.fxstreet_sentiment.long_pct

    return float(max(-100, min(100, score))), myfxbook_signal, retail_long_pct


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


def _confidence(ms, aov, timing, tech_score, fund_score, sent_score, sent_result=None) -> int:
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

    # AI bonus: when multi-LLM analysis is available, sentiment is much more reliable
    if sent_result and getattr(sent_result, 'ai_analysis', None):
        ai = sent_result.ai_analysis
        c += int(ai.confidence * 0.12)  # up to +12 from AI confidence level
        c += 5  # base bonus for using AI (more reliable than VADER)

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
