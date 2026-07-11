"""Multi-LLM Consensus Engine — Claude, Gemini, DeepSeek, Grok blended analysis.

Each model receives the same market context (headlines + prices + calendar)
and returns a structured sentiment assessment. Results are combined using
weighted consensus with automatic fallback if any model is unavailable.

Usage:
    from .ai_analyst import run_ai_consensus
    result = run_ai_consensus(headlines, live_prices, calendar_events)
    if result:
        print(result.overall_score, result.market_sentiment)

Caveats:
    - Requires API keys via env vars (ANTHROPIC_API_KEY, GEMINI_API_KEY, etc.)
    - Models without a key set are silently skipped.
    - If no keys are set, run_ai_consensus() returns None — caller falls back.
    - Results are cached (5 min TTL) so repeated scans are instant.
"""

import json, os, hashlib, time, threading
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed


# ─── Data classes ─────────────────────────────────────────────────────────

@dataclass
class AIAnalysis:
    """Structured output from a single AI model call."""
    market_sentiment: str        # 'bullish', 'bearish', 'neutral', 'mixed'
    score: float                 # -100 to +100
    confidence: float            # 0-100
    risk_appetite: str           # 'risk_on', 'risk_off', 'neutral'
    key_themes: list[str]        # top market themes driving price action
    currency_lean: Optional[str] # if AI identifies a specific currency lean
    explanation: str             # brief reasoning (under 150 words)
    provider: str = ''           # which model produced this
    model: str = ''              # exact model name
    latency_ms: int = 0          # round-trip time
    raw_response: str = ''       # original JSON for debugging


@dataclass
class ConsensusResult:
    """Blended output from all available AI models."""
    overall_score: float          # -100 to +100, weighted blend
    confidence: float             # 0-100, weighted by model confidence
    market_sentiment: str         # consensus label
    risk_appetite: str            # consensus risk mode
    key_themes: list[str]         # merged across models (ranked)
    explanation: str              # synthesised summary
    individual_results: list[AIAnalysis] = field(default_factory=list)
    models_used: list[str] = field(default_factory=list)
    weights_used: dict[str, float] = field(default_factory=dict)
    cached: bool = False
    latency_ms: int = 0           # total wall-clock time


# ─── Configuration ─────────────────────────────────────────────────────────

# Model definitions — weight, API structure, env-var for key
MODEL_REGISTRY = {
    'claude': {
        'weight': 0.35,
        'model': 'claude-sonnet-4-6',  # fast + high quality
        'api_key_env': 'ANTHROPIC_API_KEY',
        'api_url': 'https://api.anthropic.com/v1/messages',
        'handler': '_call_claude',
    },
    'gemini': {
        'weight': 0.30,
        'model': 'gemini-2.0-flash-001',  # fast, cheap, broad context (free tier)
        'api_key_env': 'GEMINI_API_KEY',
        'api_url': 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
        'handler': '_call_gemini',
    },
    'deepseek': {
        'weight': 0.20,
        'model': 'deepseek-chat',  # strong quantitative reasoning
        'api_key_env': 'DEEPSEEK_API_KEY',
        'api_url': 'https://api.deepseek.com/v1/chat/completions',
        'handler': '_call_openai_compat',
    },
    'grok': {
        'weight': 0.15,
        'model': 'grok-2-latest',  # real-time market awareness
        'api_key_env': 'GROK_API_KEY',
        'api_url': 'https://api.x.ai/v1/chat/completions',
        'handler': '_call_openai_compat',
    },
}

# Cache
_cache: dict[str, tuple[float, ConsensusResult]] = {}  # key -> (timestamp, result)
_cache_lock = threading.Lock()
_CACHE_TTL_SECONDS = 300  # 5 minutes
_AI_TIMEOUT = 12  # per-model timeout seconds


# ─── System / User Prompts ────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are an expert financial market analyst specialising in forex, indices, "
    "commodities, and crypto markets.\n\n"
    "Analyse the provided market context and produce a structured sentiment assessment.\n\n"
    "Focus:\n"
    "- Overall market sentiment direction and conviction\n"
    "- Risk appetite — is the market in risk-on (buying equities/commodities) or "
    "risk-off (fleeing to USD/JPY/gold) mode?\n"
    "- Key macro themes driving price action (central banks, geopolitics, data)\n"
    "- Currency-specific leans where evident from the news\n\n"
    "Be specific, data-driven, and avoid generic statements. Base your analysis "
    "only on the headlines and data provided — do not make up information."
)

USER_PROMPT_TEMPLATE = (
    "Analyse the current market sentiment based on this data.\n\n"
    "## Latest Market Headlines\n{headlines}\n\n"
    "## Current Prices (selected pairs)\n{prices}\n\n"
    "## Upcoming High-Impact Calendar Events\n{calendar}\n\n"
    "Return a JSON object with exactly these fields:\n"
    '- "market_sentiment": one of "bullish", "bearish", "neutral", or "mixed"\n'
    '- "score": float between -100 (extremely bearish) and +100 (extremely bullish)\n'
    '- "confidence": float between 0 and 100 — how confident in this assessment\n'
    '- "risk_appetite": one of "risk_on", "risk_off", "neutral"\n'
    '- "currency_lean": string or null — if one currency stands out, e.g. "USD", "JPY", null\n'
    '- "key_themes": array of 3-5 strings — themes driving price action\n'
    '- "explanation": string — specific reasoning (under 120 words)\n\n'
    "Return ONLY valid JSON. No markdown fences. No other text."
)


# ─── Context builder ──────────────────────────────────────────────────────

def _build_context(
    headlines: list,
    prices: dict,
    calendar_events: list,
) -> dict:
    """Build the context dict for AI prompts — compact & efficient."""
    # Top headlines (max 25)
    lines = []
    for h in headlines[:25]:
        src = getattr(h, 'source', 'Unknown')
        title = getattr(h, 'title', str(h))
        lines.append(f"[{src}] {title}")

    # Key price levels
    price_lines = []
    for sym in ('EURUSD', 'GBPUSD', 'USDJPY', 'XAUUSD', 'SP500', 'US30', 'XTIUSD'):
        val = prices.get(sym)
        if val is not None:
            try:
                price_lines.append(f"{sym}: {float(val):.4f}" if val < 1000 else f"{sym}: {float(val):.2f}")
            except (ValueError, TypeError):
                pass

    # Calendar events
    cal_lines = []
    for e in calendar_events[:12]:
        imp = getattr(e, 'impact', 'Low')
        cur = getattr(e, 'currency', '?')
        ev = getattr(e, 'event', str(e))
        if imp in ('High', 'Medium'):
            cal_lines.append(f"[{imp}] {cur} - {ev}")

    return {
        'headlines': '\n'.join(lines) if lines else 'No recent headlines.',
        'prices': '\n'.join(price_lines) if price_lines else 'No price data.',
        'calendar': '\n'.join(cal_lines) if cal_lines else 'No upcoming events.',
    }


def _make_cache_key(headlines: list, prices: dict, calendar_events: list) -> str:
    """Deterministic cache key based on content, not time."""
    parts = []
    for h in headlines[:25]:
        parts.append(getattr(h, 'title', str(h)))
    for k in sorted(prices.keys()):
        parts.append(f"{k}={prices[k]}")
    for e in calendar_events[:12]:
        parts.append(f"{getattr(e, 'currency','?')}-{getattr(e, 'event','?')}-{getattr(e, 'impact','?')}")
    raw = '|'.join(parts)
    return hashlib.md5(raw.encode()).hexdigest()


# ─── Individual model callers ─────────────────────────────────────────────

def _call_claude(cfg: dict, context: dict, timeout: int = _AI_TIMEOUT) -> Optional[AIAnalysis]:
    """Call Anthropic Claude API."""
    start = time.time()
    try:
        import requests
        headers = {
            'x-api-key': cfg['api_key'],
            'anthropic-version': '2023-06-01',
            'content-type': 'application/json',
        }
        payload = {
            'model': cfg['model'],
            'max_tokens': 512,
            'temperature': 0.1,
            'system': SYSTEM_PROMPT,
            'messages': [
                {'role': 'user', 'content': USER_PROMPT_TEMPLATE.format(**context)},
            ],
        }
        resp = requests.post(cfg['api_url'], headers=headers, json=payload, timeout=timeout)
        if resp.status_code != 200:
            return None
        data = resp.json()
        content = data.get('content', [{}])[0].get('text', '')
        text = content.strip()
        text = text.removeprefix('```json').removeprefix('```').removesuffix('```').strip()
        result = json.loads(text)
        return AIAnalysis(
            market_sentiment=result.get('market_sentiment', 'neutral'),
            score=float(result.get('score', 0)),
            confidence=float(result.get('confidence', 50)),
            risk_appetite=result.get('risk_appetite', 'neutral'),
            key_themes=result.get('key_themes', []),
            currency_lean=result.get('currency_lean'),
            explanation=result.get('explanation', ''),
            provider='claude',
            model=cfg['model'],
            latency_ms=int((time.time() - start) * 1000),
            raw_response=text,
        )
    except Exception:
        return None


def _call_gemini(cfg: dict, context: dict, timeout: int = _AI_TIMEOUT) -> Optional[AIAnalysis]:
    """Call Google Gemini API."""
    start = time.time()
    try:
        import requests
        url = cfg['api_url'].format(model=cfg['model']) + f"?key={cfg['api_key']}"
        payload = {
            'contents': [{
                'parts': [{'text': SYSTEM_PROMPT + '\n\n' + USER_PROMPT_TEMPLATE.format(**context)}],
            }],
            'generationConfig': {
                'maxOutputTokens': 512,
                'temperature': 0.1,
            },
        }
        resp = requests.post(url, json=payload, timeout=timeout)
        if resp.status_code != 200:
            return None
        data = resp.json()
        candidates = data.get('candidates', [])
        if not candidates:
            return None
        parts = candidates[0].get('content', {}).get('parts', [])
        text = parts[0].get('text', '') if parts else ''

        text = text.strip()
        text = text.removeprefix('```json').removeprefix('```').removesuffix('```').strip()
        result = json.loads(text)
        return AIAnalysis(
            market_sentiment=result.get('market_sentiment', 'neutral'),
            score=float(result.get('score', 0)),
            confidence=float(result.get('confidence', 50)),
            risk_appetite=result.get('risk_appetite', 'neutral'),
            key_themes=result.get('key_themes', []),
            currency_lean=result.get('currency_lean'),
            explanation=result.get('explanation', ''),
            provider='gemini',
            model=cfg['model'],
            latency_ms=int((time.time() - start) * 1000),
            raw_response=text,
        )
    except Exception:
        return None


def _call_openai_compat(cfg: dict, context: dict, timeout: int = _AI_TIMEOUT) -> Optional[AIAnalysis]:
    """Call any OpenAI-compatible chat API (DeepSeek, Grok, etc.)."""
    start = time.time()
    try:
        import requests
        headers = {
            'Authorization': f"Bearer {cfg['api_key']}",
            'Content-Type': 'application/json',
        }
        payload = {
            'model': cfg['model'],
            'messages': [
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': USER_PROMPT_TEMPLATE.format(**context)},
            ],
            'max_tokens': 512,
            'temperature': 0.1,
        }
        resp = requests.post(cfg['api_url'], headers=headers, json=payload, timeout=timeout)
        if resp.status_code != 200:
            return None
        data = resp.json()
        choices = data.get('choices', [])
        if not choices:
            return None
        text = choices[0].get('message', {}).get('content', '')

        text = text.strip()
        text = text.removeprefix('```json').removeprefix('```').removesuffix('```').strip()
        result = json.loads(text)
        return AIAnalysis(
            market_sentiment=result.get('market_sentiment', 'neutral'),
            score=float(result.get('score', 0)),
            confidence=float(result.get('confidence', 50)),
            risk_appetite=result.get('risk_appetite', 'neutral'),
            key_themes=result.get('key_themes', []),
            currency_lean=result.get('currency_lean'),
            explanation=result.get('explanation', ''),
            provider='deepseek' if 'deepseek' in str(cfg.get('api_url', '')) else 'grok',
            model=cfg['model'],
            latency_ms=int((time.time() - start) * 1000),
            raw_response=text,
        )
    except Exception:
        return None


# Map provider name to handler function
_HANDLERS = {
    'claude': _call_claude,
    'gemini': _call_gemini,
    'deepseek': _call_openai_compat,
    'grok': _call_openai_compat,
}


# ─── Consensus blending ───────────────────────────────────────────────────

def _available_models() -> dict:
    """Return model configs that have API keys set in environment."""
    available = {}
    for name, cfg in MODEL_REGISTRY.items():
        key = os.environ.get(cfg['api_key_env'], '').strip()
        if key:
            available[name] = {**cfg, 'api_key': key}
    return available


def _blend_consensus(results: list[AIAnalysis], used_models: dict) -> ConsensusResult:
    """Blend individual AI results into a weighted consensus."""
    if not results:
        return ConsensusResult(
            overall_score=0.0, confidence=0, market_sentiment='neutral',
            risk_appetite='neutral', key_themes=[], explanation='No AI models available.',
        )

    total_weight = 0.0
    weighted_score = 0.0
    weighted_confidence = 0.0
    sentiment_votes = {'bullish': 0.0, 'bearish': 0.0, 'neutral': 0.0, 'mixed': 0.0}
    risk_votes = {'risk_on': 0.0, 'risk_off': 0.0, 'neutral': 0.0}
    all_themes = []
    explanations = []
    currency_leans = []

    for r in results:
        w = used_models.get(r.provider, {}).get('weight', 0.15)
        total_weight += w
        weighted_score += r.score * w
        weighted_confidence += r.confidence * w
        sentiment_votes[r.market_sentiment] = sentiment_votes.get(r.market_sentiment, 0) + w
        risk_votes[r.risk_appetite] = risk_votes.get(r.risk_appetite, 0) + w
        all_themes.extend(r.key_themes)
        if r.explanation:
            explanations.append(f"[{r.provider.title()}] {r.explanation}")
        if r.currency_lean:
            currency_leans.append(r.currency_lean)

    # Normalise
    avg_score = weighted_score / total_weight if total_weight > 0 else 0.0
    avg_confidence = weighted_confidence / total_weight if total_weight > 0 else 0.0

    # Consensus sentiment
    consensus_sentiment = max(sentiment_votes, key=sentiment_votes.get)
    # If "mixed" leads but a clear directional signal exists, pick the strongest alternative
    if consensus_sentiment == 'mixed':
        directional = {k: v for k, v in sentiment_votes.items() if k in ('bullish', 'bearish')}
        if directional:
            alt = max(directional, key=directional.get)
            # Only switch if the alternative is close
            if directional[alt] >= sentiment_votes['mixed'] * 0.7:
                consensus_sentiment = alt

    # Consensus risk appetite
    consensus_risk = max(risk_votes, key=risk_votes.get)

    # Themes: deduplicate and rank by frequency
    theme_counts = {}
    for t in all_themes:
        key = t.lower().strip().rstrip('.')
        theme_counts[key] = theme_counts.get(key, 0) + 1
    ranked_themes = sorted(theme_counts, key=theme_counts.get, reverse=True)[:8]

    # Synthesis explanation
    syn = f"Consensus from {', '.join(r.provider.title() for r in results)}. "
    if consensus_sentiment == 'bullish':
        syn += "Markets are broadly bullish. "
    elif consensus_sentiment == 'bearish':
        syn += "Markets are broadly bearish. "
    elif consensus_sentiment == 'mixed':
        syn += "Signals are mixed across models. "
    else:
        syn += "Neutral sentiment. "

    if currency_leans:
        lean_counts = {}
        for c in currency_leans:
            if c:
                lean_counts[c] = lean_counts.get(c, 0) + 1
        top_lean = max(lean_counts, key=lean_counts.get) if lean_counts else None
        if top_lean:
            syn += f"Notable currency lean: {top_lean}. "

    if ranked_themes:
        syn += f"Key themes: {' | '.join(ranked_themes[:5])}."

    return ConsensusResult(
        overall_score=round(max(-100, min(100, avg_score)), 1),
        confidence=round(max(0, min(100, avg_confidence)), 1),
        market_sentiment=consensus_sentiment,
        risk_appetite=consensus_risk,
        key_themes=ranked_themes,
        explanation=syn,
        individual_results=results,
        models_used=[r.provider for r in results],
        weights_used={r.provider: used_models.get(r.provider, {}).get('weight', 0.15)
                      for r in results},
        latency_ms=max(r.latency_ms for r in results) if results else 0,
    )


# ─── Public API ───────────────────────────────────────────────────────────

def run_ai_consensus(
    headlines: list,
    live_prices: Optional[dict] = None,
    calendar_events: Optional[list] = None,
    bypass_cache: bool = False,
) -> Optional[ConsensusResult]:
    """Run multi-LLM consensus on market context.

    Args:
        headlines: List of NewsHeadline or any objects with .source and .title
        live_prices: Dict of {symbol: price} (from fetch_live_prices)
        calendar_events: List of CalendarEvent objects (from eco_calendar.analyze)
        bypass_cache: If True, force a fresh analysis

    Returns:
        ConsensusResult if at least one AI model succeeded, else None.
        Callers should fall back to VADER/keyword scoring when None.
    """
    if live_prices is None:
        live_prices = {}
    if calendar_events is None:
        calendar_events = []

    context = _build_context(headlines, live_prices, calendar_events)
    cache_key = _make_cache_key(headlines, live_prices, calendar_events)

    # Check cache
    if not bypass_cache:
        with _cache_lock:
            entry = _cache.get(cache_key)
            if entry and (time.time() - entry[0]) < _CACHE_TTL_SECONDS:
                cached = entry[1]
                cached.cached = True
                return cached

    # Determine which models are available
    models = _available_models()
    if not models:
        return None  # No API keys configured — caller falls back

    # Run all available models in parallel
    results: list[AIAnalysis] = []
    start_all = time.time()

    with ThreadPoolExecutor(max_workers=min(len(models), 8)) as pool:
        future_map = {}
        for name, cfg in models.items():
            handler = _HANDLERS.get(name)
            if handler:
                future = pool.submit(handler, cfg, context)
                future_map[future] = name

        for future in as_completed(future_map, timeout=_AI_TIMEOUT + 2):
            try:
                result = future.result()
                if result is not None:
                    results.append(result)
            except Exception:
                pass  # Individual model failures are non-fatal

    if not results:
        return None  # All models failed

    consensus = _blend_consensus(results, models)
    consensus.latency_ms = int((time.time() - start_all) * 1000)

    # Cache
    with _cache_lock:
        _cache[cache_key] = (time.time(), consensus)

    return consensus


def invalidate_cache():
    """Force fresh analysis on next call."""
    with _cache_lock:
        _cache.clear()


def get_cache_stats() -> dict:
    """Return cache diagnostics."""
    with _cache_lock:
        return {
            'size': len(_cache),
            'ttl_seconds': _CACHE_TTL_SECONDS,
            'keys': list(_cache.keys()),
            'ages': [int(time.time() - ts) for ts, _ in _cache.values()],
        }
