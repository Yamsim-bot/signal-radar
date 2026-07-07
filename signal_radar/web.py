"""Flask Web Dashboard — radar table, AI chat, position calculator, journal."""

import sys, os, json
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, render_template, request, jsonify, session
import secrets

from .radar import scan as radar_scan
from .config import Config
from .instruments import INSTRUMENTS, get_symbols, pip_value_usd, CATEGORY_LABELS, best_session_str

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(16))

# Journal file
JOURNAL_FILE = Path(__file__).parent / "trading_journal.json"

# Radar result cache (avoids re-running full scan per BUDDY question)
_radar_cache = None
_radar_cache_time = None
_RADAR_CACHE_TTL = timedelta(seconds=120)


def _get_cached_radar():
    """Return cached radar result if fresh, otherwise run a new scan."""
    global _radar_cache, _radar_cache_time
    now = datetime.now(timezone.utc)
    if _radar_cache is not None and _radar_cache_time is not None:
        if now - _radar_cache_time < _RADAR_CACHE_TTL:
            return _radar_cache
    cfg = Config()
    _radar_cache = radar_scan(cfg)
    _radar_cache_time = now
    return _radar_cache


def _warm_cache():
    """Pre-warm the radar cache in background so the first visitor doesn't wait."""
    import time
    time.sleep(3)
    try:
        _get_cached_radar()
    except Exception:
        pass


import threading as _thr
_thr.Thread(target=_warm_cache, daemon=True).start()


def _load_journal():
    if JOURNAL_FILE.exists():
        try:
            with open(JOURNAL_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save_journal(entries):
    with open(JOURNAL_FILE, 'w') as f:
        json.dump(entries, f, indent=2)


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    """Main radar dashboard."""
    return render_template('radar.html', instruments=INSTRUMENTS,
                           symbols=get_symbols(), categories=CATEGORY_LABELS)


@app.route('/api/news')
def api_news():
    """Serve news headlines and calendar events — uses full live sentiment."""
    from .sentiment import analyze as sentiment_analyze
    from .calendar import analyze as calendar_analyze
    from .config import Config
    sent_result = sentiment_analyze(quick=False)   # full live fetch for news tab
    cal_result = calendar_analyze()

    headlines = []
    for h in sent_result.headlines[:15]:
        headlines.append({
            'source': h.source,
            'title': h.title,
            'published': h.published,
            'sentiment': round(h.sentiment_score, 3),
            'keywords': h.keywords,
        })

    events = []
    for e in cal_result.events_this_week[:20]:
        events.append({
            'time': e.time,
            'currency': e.currency,
            'event': e.event,
            'impact': e.impact,
            'actual': e.actual,
            'forecast': e.forecast,
            'previous': e.previous,
        })

    cb_events = []
    for e in cal_result.central_bank_events[:10]:
        cb_events.append({
            'time': e.time,
            'currency': e.currency,
            'event': e.event,
            'impact': e.impact,
        })

    # Cross-reference data for source comparison
    cross_refs = []
    for cr in sent_result.cross_references[:10]:
        cross_refs.append({
            'topic': cr.topic,
            'source_sentiments': cr.source_sentiments,
            'consensus': cr.consensus,
            'agreement_level': cr.agreement_level,
            'discrepancy_flag': cr.discrepancy_flag,
        })

    # Source accuracy / credibility
    src_accuracy = []
    for sa in sent_result.source_accuracy:
        src_accuracy.append({
            'source': sa.source,
            'articles_scraped': sa.articles_scraped,
            'avg_relevance': sa.avg_relevance,
            'unique_topics': sa.unique_topics,
            'credibility_score': sa.credibility_score,
        })

    return jsonify({
        'headlines': headlines,
        'calendar_events': events,
        'central_bank_events': cb_events,
        'sentiment_score': sent_result.overall_score,
        'trending_topics': sent_result.trending_topics[:8],
        'source_breakdown': sent_result.source_breakdown,
        'dovish_count': sent_result.dovish_count,
        'hawkish_count': sent_result.hawkish_count,
        'risk_on_count': sent_result.risk_on_count,
        'risk_off_count': sent_result.risk_off_count,
        'high_impact_count': cal_result.high_impact_count,
        'cross_references': cross_refs,
        'source_accuracy': src_accuracy,
    })


@app.route('/api/scan')
def api_scan():
    """Run radar scan, return JSON. Uses cache for 60s."""
    result = _get_cached_radar()
    data = {
        'timestamp': result.timestamp,
        'market_sentiment': result.market_sentiment,
        'market_score': result.market_score,
        'instruments': [],
        'calendar': {
            'high_impact_count': result.calendar.high_impact_count,
            'next_high_impact': {
                'event': result.calendar.next_high_impact.event,
                'currency': result.calendar.next_high_impact.currency,
                'time': result.calendar.next_high_impact.time,
            } if result.calendar.next_high_impact else None,
            'events': [{
                'time': e.time,
                'currency': e.currency,
                'event': e.event,
                'impact': e.impact,
            } for e in result.calendar.events_this_week[:8]],
        },
        'sentiment': {
            'score': result.sentiment.overall_score,
            'dovish': result.sentiment.dovish_count,
            'hawkish': result.sentiment.hawkish_count,
            'risk_on': result.sentiment.risk_on_count,
            'risk_off': result.sentiment.risk_off_count,
        },
        'fundamental': {
            'score': result.fundamental.overall_score,
            'risk': result.fundamental.risk_sentiment,
            'top_bullish': result.fundamental.top_bullish[:3],
            'top_bearish': result.fundamental.top_bearish[:3],
        },
    }
    for i in result.instruments:
        item = {
            'symbol': i.symbol,
            'name': i.name,
            'best_session': best_session_str(i.symbol),
            'category': i.category,
            'bias': i.bias,
            'bias_score': i.bias_score,
            'confidence': i.confidence,
            'strength': i.strength,
            'price': i.price,
            'change_pct': i.change_pct,
            'explanation': i.explanation.explanation,
            'technical_score': i.explanation.technical_score,
            'fundamental_score': i.explanation.fundamental_score,
            'sentiment_score': i.explanation.sentiment_score,
            'trend_direction': i.explanation.trend_direction,
            'trend_strength': i.explanation.trend_strength,
            'session_quality': i.explanation.session_quality,
            'entry_timing': i.explanation.entry_timing,
            'key_support': i.explanation.key_support,
            'key_resistance': i.explanation.key_resistance,
            'nearest_support': i.explanation.nearest_support,
            'nearest_resistance': i.explanation.nearest_resistance,
            'aov_position': i.explanation.aov_position,
        }
        # Add factor breakdown if available
        fb = i.explanation.fundamental_breakdown
        if fb:
            item['breakdown'] = {
                'growth': fb.growth,
                'inflation': fb.inflation,
                'jobs': fb.jobs,
                'sentiment': fb.sentiment,
                'trend': fb.trend,
                'seasonality': fb.seasonality,
                'total': fb.total,
            }
        else:
            item['breakdown'] = None
        data['instruments'].append(item)

    return jsonify(data)


# ─── Tagalog trading vocabulary ────────────────────────────────────────────
TAGALOG_WORDS = {
    'bumili': 'buy', 'bili': 'buy', 'bilhin': 'buy', 'tumataas': 'rising',
    'pagtaas': 'increase', 'lakas': 'strong', 'malakas': 'strong',
    'mababa': 'low', 'bumaba': 'went_down', 'ibaba': 'down', 'pababa': 'downward',
    'tumaas': 'went_up', 'tataas': 'will_rise', 'bababa': 'will_fall',
    'pera': 'money', 'salapi': 'currency', 'piso': 'peso',
    'presyo': 'price', 'halaga': 'value', 'trading': 'trading',
    'negosyo': 'business', 'merkado': 'market', 'palitan': 'exchange',
    'puhunan': 'capital', 'invest': 'invest', 'tubo': 'profit',
    'luging': 'loss', 'talunan': 'loser', 'panalo': 'winner',
    'kita': 'earnings', 'gastos': 'expenses', 'swerte': 'luck',
    'oras': 'time', 'araw': 'day', 'linggo': 'week', 'buwan': 'month',
    'ngayon': 'now', 'kahapon': 'yesterday', 'bukas': 'tomorrow',
    'ano': 'what', 'bakit': 'why', 'paano': 'how', 'saan': 'where',
    'kailan': 'when', 'sino': 'who', 'magkano': 'how_much',
    'mabagal': 'slow', 'mabilis': 'fast', 'malaki': 'big', 'maliit': 'small',
    'maganda': 'good', 'masama': 'bad', 'tama': 'correct', 'mali': 'wrong',
    'posisyon': 'position', 'order': 'order', 'stop': 'stop', 'limit': 'limit',
    'usd': 'usd', 'dolyar': 'dollar', 'usd/pHP': 'usd_php',
    'euro': 'euro', 'gold': 'gold', 'ginto': 'gold', 'pilak': 'silver',
    'langis': 'oil', 'index': 'index', 'sapi': 'stock',
}

TAGALOG_GREETINGS = ['kamusta', 'hello', 'magandang', 'salamat', 'sige', 'opo', 'oo']

def _detect_tagalog(text: str) -> bool:
    """Check if the input contains Tagalog words."""
    lower = text.lower()
    # Check for distinct Tagalog words
    tagalog_words = {'ang', 'ay', 'ko', 'mo', 'siya', 'sila', 'kami', 'tayo',
                     'ito', 'iyan', 'doon', 'dito', 'ng', 'sa', 'mga', 'po',
                     'opo', 'oo', 'hindi', 'wala', 'meron', 'may', 'kasi',
                     'kaya', 'kung', 'gagawin', 'ginagawa', 'ginawa',
                     'pwede', 'pwedeng', 'dapat', 'kailangan', 'gusto',
                     'yung', 'yong', 'mong', 'kong', 'nito', 'niyan',
                     'kamusta', 'magkano', 'paano', 'bakit', 'saan'}
    words = lower.split()
    tagalog_hits = sum(1 for w in words if w in tagalog_words)
    return tagalog_hits >= 2


@app.route('/api/chat', methods=['POST'])
def api_chat():
    """AI assistant for trading questions. Supports Tagalog."""
    question = request.json.get('question', '').strip()
    if not question:
        return jsonify({'answer': 'Please ask a question about a trading instrument. / Magtanong tungkol sa trading.'})

    is_tagalog = _detect_tagalog(question)
    question_lower = question.lower()

    # Translate Tagalog words
    translated = question_lower
    for tl_word, en_word in TAGALOG_WORDS.items():
        translated = translated.replace(tl_word, en_word)

    # ── Translations ──
    T = {} if not is_tagalog else {
        'welcome': 'Maligayang pagdating! Ako ang Yams Radar assistant.',
        'loading': 'Kinukuha ang pinakabagong datos ng merkado...',
        'market_overview': '✅ Pangkalahatang Merkado',
        'sentiment': 'Sentimyento',
        'bullish': 'Bullish', 'bearish': 'Bearish', 'neutral': 'Neutral',
        'strong_buy': 'Malakas na Bili', 'buy': 'Bili',
        'strong_sell': 'Malakas na Benta', 'sell': 'Benta',
        'score': 'puntos', 'strength': 'lakas', 'confidence': 'kumpiyansa',
        'support': 'Suporta', 'resistance': 'Resistensiya',
        'trend': 'Trend', 'entry': 'Pagpasok', 'timing': 'Oras',
        'top_bullish': 'Pinaka Bullish', 'top_bearish': 'Pinaka Bearish',
        'high_impact': 'mataas na epekto', 'events': 'pangyayari',
        'this_week': 'ngayong linggo', 'news': 'balita',
        'fundamental': 'Pundamental', 'technical': 'Teknikal',
        'session': 'Session', 'level': 'Antas',
        'recommendation': 'Rekomendasyon', 'analysis': 'Pagsusuri',
        'suggest': 'Subukan', 'ask_about': 'Magtanong tungkol sa',
        'for_details': 'para sa detalyadong pagsusuri',
        'none': 'Wala',
    }

    # Use cached radar data (fast — avoids re-scanning for every question)
    result = _get_cached_radar()
    question_lower = translated

    # Find mentioned symbols
    mentioned = []
    for i in result.instruments:
        if i.symbol.lower() in question_lower or i.name.lower() in question_lower:
            mentioned.append(i)

    # Find mentioned categories
    cat_map = {'major': 'major', 'majors': 'major', 'cross': 'cross', 'crosses': 'cross',
               'index': 'index', 'indices': 'index', 'commodity': 'commodity',
               'stock': 'stocks', 'stocks': 'stock', 'crypto': 'crypto',
               'cryptocurrency': 'crypto'}
    mentioned_cats = [cat_map[w] for w in cat_map if w in question_lower]

    # Greeting / basics
    if is_tagalog and any(g in question_lower for g in ['kamusta', 'hello', 'magandang', 'salamat']):
        return jsonify({'answer': (
            f"{T.get('welcome', '')}\n\n"
            f"📊 **{T.get('market_overview', 'Market Overview')}:** "
            f"{result.market_sentiment} ({result.market_score:+.1f})\n\n"
            f"Magtanong lang tulad ng:\n"
            f"• \"Bakit neutral ang EURUSD?\"\n"
            f"• \"Ano ang pinakamalakas na bilhin?\"\n"
            f"• \"Paano ang GBPJPY?\"\n"
            f"• \"Ano ang sentiment ng merkado?\""
        )})

    # Build answer
    if not mentioned and not mentioned_cats:
        # General answer
        market = result.market_sentiment
        top_buy = [i for i in result.instruments if i.bias in ('Strong Buy', 'Buy')][:3]
        top_sell = [i for i in result.instruments if i.bias in ('Strong Sell', 'Sell')][:3]

        if is_tagalog:
            bull_list = ', '.join(f'{i.symbol} ({T.get("strong_buy","Strong Buy") if i.bias=="Strong Buy" else T.get("buy","Buy")}, {T.get("strength","strength")} {i.strength}/10)' for i in top_buy) if top_buy else T.get('none', 'None')
            bear_list = ', '.join(f'{i.symbol} ({T.get("strong_sell","Strong Sell") if i.bias=="Strong Sell" else T.get("sell","Sell")}, {T.get("strength","strength")} {i.strength}/10)' for i in top_sell) if top_sell else T.get('none', 'None')
            answer = (
                f"📊 **{T.get('market_overview','Market Overview')}** — {market} ({result.market_score:+.1f})\n\n"
                f"**{T.get('top_bullish','Top Bullish')}:** {bull_list}\n"
                f"**{T.get('top_bearish','Top Bearish')}:** {bear_list}\n\n"
                f"📰 **{T.get('news','News')} {T.get('sentiment','Sentiment')}:** {result.sentiment.overall_score:+.1f}\n"
                f"🏛️ **{T.get('fundamental','Fundamental')}:** {result.fundamental.overall_score:+.1f} ({result.fundamental.risk_sentiment})\n"
                f"📅 **{T.get('high_impact','High impact')} {T.get('events','events')}:** {result.calendar.high_impact_count} {T.get('this_week','this week')}\n\n"
                f"💡 {T.get('suggest','Try')}: \"Bakit bearish ang XAUUSD?\" {T.get('for_details','for details')}"
            )
        else:
            answer = (
                f"📊 **Market Overview** — Overall sentiment: {market} (score: {result.market_score:+.1f}).\n\n"
                f"**Top bullish:** {', '.join(f'{i.symbol} ({i.bias}, strength {i.strength}/10)' for i in top_buy) if top_buy else 'None'}\n"
                f"**Top bearish:** {', '.join(f'{i.symbol} ({i.bias}, strength {i.strength}/10)' for i in top_sell) if top_sell else 'None'}\n\n"
                f"**News sentiment:** {result.sentiment.overall_score:+.1f} (dovish {result.sentiment.dovish_count}, hawkish {result.sentiment.hawkish_count})\n"
                f"**Fundamental:** {result.fundamental.overall_score:+.1f} ({result.fundamental.risk_sentiment})\n"
                f"**Calendar:** {result.calendar.high_impact_count} high-impact events this week\n\n"
                f"Ask about a specific symbol (e.g., 'Why is EURUSD bearish?') for detailed analysis."
            )
        return jsonify({'answer': answer})

    # Specific instrument questions
    if mentioned:
        answers = []
        for i in mentioned[:3]:
            fb = i.explanation.fundamental_breakdown
            breakdown_str = ''
            if fb:
                if is_tagalog:
                    breakdown_str = (
                        f"\n\n**Pundamental na salik:**"
                        f"\n• Paglago: {fb.growth:+.0f}"
                        f"\n• Implasyon: {fb.inflation:+.0f}"
                        f"\n• Trabaho: {fb.jobs:+.0f}"
                        f"\n• Sentimyento: {fb.sentiment:+.0f}"
                        f"\n• Trend: {fb.trend:+.0f}"
                        f"\n• Seasonality: {fb.seasonality:+.0f}"
                    )
                else:
                    breakdown_str = (
                        f"\n\n**Fundamental factors:**"
                        f"\n• Growth: {fb.growth:+.0f}"
                        f"\n• Inflation: {fb.inflation:+.0f}"
                        f"\n• Jobs: {fb.jobs:+.0f}"
                        f"\n• Sentiment: {fb.sentiment:+.0f}"
                        f"\n• Trend: {fb.trend:+.0f}"
                        f"\n• Seasonality: {fb.seasonality:+.0f}"
                    )

            if is_tagalog:
                bias_tl = {'Strong Buy': 'Malakas na Bili', 'Buy': 'Bili',
                           'Neutral': 'Neutral', 'Sell': 'Benta',
                           'Strong Sell': 'Malakas na Benta'}.get(i.bias, i.bias)
                aov_tl = i.explanation.aov_position.replace('_', ' ')
                answers.append(
                    f"🔍 **{i.symbol}** — {i.name}\n"
                    f"**Posisyong:** {bias_tl} ({i.bias_score:+.0f}) | "
                    f"**Lakas:** {i.strength}/10 | "
                    f"**Kumpiyansa:** {i.confidence}%\n\n"
                    f"**Teknikal:** {i.explanation.technical_score:+.0f} | "
                    f"**Pundamental:** {i.explanation.fundamental_score:+.0f} | "
                    f"**Sentimyento:** {i.explanation.sentiment_score:+.0f}\n\n"
                    f"**Trend:** {i.explanation.trend_direction} ({i.explanation.trend_strength})\n"
                    f"**Mga antas:** Suporta {i.explanation.key_support:.5f}, Resistensiya {i.explanation.key_resistance:.5f}\n"
                    f"**Oras ng pagpasok:** {i.explanation.session_quality} session, {i.explanation.entry_timing}\n"
                    f"**Posisyon sa AOV:** {aov_tl}"
                    f"{breakdown_str}"
                )
            else:
                answers.append(
                    f"🔍 **{i.symbol}** — {i.name}\n"
                    f"**Bias:** {i.bias} (score: {i.bias_score:+.0f}) | "
                    f"**Strength:** {i.strength}/10 | "
                    f"**Confidence:** {i.confidence}%\n\n"
                    f"**Technical:** {i.explanation.technical_score:+.0f} | "
                    f"**Fundamental:** {i.explanation.fundamental_score:+.0f} | "
                    f"**Sentiment:** {i.explanation.sentiment_score:+.0f}\n\n"
                    f"**Trend:** {i.explanation.trend_direction} ({i.explanation.trend_strength})\n"
                    f"**Key levels:** Support {i.explanation.key_support:.5f}, Resistance {i.explanation.key_resistance:.5f}\n"
                    f"**Timing:** {i.explanation.session_quality} session, entry: {i.explanation.entry_timing}\n"
                    f"**AOV position:** {i.explanation.aov_position.replace('_', ' ')}"
                    f"{breakdown_str}"
                )

        return jsonify({'answer': '\n\n---\n\n'.join(answers)})

    # Category questions
    if mentioned_cats:
        cat = mentioned_cats[0]
        cat_instrs = [i for i in result.instruments if i.category == cat]
        label = CATEGORY_LABELS.get(cat, cat.upper())

        bulls = [i for i in cat_instrs if i.bias in ('Strong Buy', 'Buy')]
        bears = [i for i in cat_instrs if i.bias in ('Strong Sell', 'Sell')]
        neutrals = [i for i in cat_instrs if i.bias == 'Neutral']

        if is_tagalog:
            buy_tl = {'Strong Buy': 'Malakas na Bili', 'Buy': 'Bili'}
            sell_tl = {'Strong Sell': 'Malakas na Benta', 'Sell': 'Benta'}
            bull_str = ', '.join(f'{i.symbol}({i.strength}) — {buy_tl.get(i.bias,i.bias)}' for i in bulls) if bulls else 'Wala'
            bear_str = ', '.join(f'{i.symbol}({i.strength}) — {sell_tl.get(i.bias,i.bias)}' for i in bears) if bears else 'Wala'
            neut_str = ', '.join(f'{i.symbol}' for i in neutrals[:5]) if neutrals else 'Wala'
            answer = (
                f"📈 **{label}** ({len(cat_instrs)} na instrumento)\n\n"
                f"**Bullish:** {bull_str}\n"
                f"**Bearish:** {bear_str}\n"
                f"**Neutral:** {neut_str}{'...' if len(neutrals) > 5 else ''}\n\n"
                f"Magtanong tungkol sa specific na instrumento para sa detalyadong breakdown."
            )
        else:
            answer = (
                f"📈 **{label}** ({len(cat_instrs)} instruments)\n\n"
                f"**Bullish:** {', '.join(f'{i.symbol}({i.strength})' for i in bulls) if bulls else 'None'}\n"
                f"**Bearish:** {', '.join(f'{i.symbol}({i.strength})' for i in bears) if bears else 'None'}\n"
                f"**Neutral:** {', '.join(f'{i.symbol}' for i in neutrals[:5])}{'...' if len(neutrals) > 5 else '' if neutrals else 'None'}\n\n"
                f"Ask about a specific symbol for detailed breakdown."
            )
        return jsonify({'answer': answer})

    if is_tagalog:
        return jsonify({'answer': (
            "Hindi ko maintindihan ang tanong mo. Subukan ang:\n\n"
            "• \"Bakit neutral ang EURUSD?\"\n"
            "• \"Ano ang sentiment ng merkado?\"\n"
            "• \"Ipakita ang GBPJPY\"\n"
            "• \"Kumusta ang stocks ngayon?\""
        )})

    return jsonify({'answer': 'I couldn\'t understand your question. Try: "Why is EURUSD neutral?" or "Show me GBPJPY details"'})


@app.route('/api/instruments')
def api_instruments():
    """Return all instruments for dropdowns."""
    symbols = get_symbols()
    result = []
    for sym in symbols:
        spec = INSTRUMENTS[sym]
        pip_factor = spec.get('pip_factor', 0.0001)
        contract_size = spec.get('contract_size', 1000)
        # Vantage-style commission per standard lot RT
        cat = spec.get('category', '')
        if cat == 'major':
            comm_per_lot = 6.00  # $6 RT standard lot
        elif cat == 'cross':
            comm_per_lot = 8.00
        elif cat == 'index':
            comm_per_lot = 2.00
        elif cat == 'commodity':
            comm_per_lot = 5.00
        elif cat == 'stock':
            comm_per_lot = 3.00
        elif cat == 'crypto':
            comm_per_lot = 10.00  # 0.1% on ~$10k = $10
        else:
            comm_per_lot = 6.00
        result.append({
            'symbol': sym,
            'name': spec.get('description', sym),
            'category': cat,
            'category_label': CATEGORY_LABELS.get(cat, cat),
            'pip_factor': pip_factor,
            'contract_size': contract_size,
            'digits': spec.get('digits', 5),
            'commission_per_lot': comm_per_lot,
            'is_crypto': spec.get('crypto', False),
        })
    return jsonify({'instruments': result})

@app.route('/api/calculator/price/<symbol>')
def api_calculator_price(symbol):
    """Get latest price for a symbol from radar data."""
    symbol = symbol.upper()
    from .data_fetcher import fetch_bars
    df = fetch_bars(symbol, bars=3, timeframe="M5")
    if df is not None and len(df) > 0:
        latest = df.iloc[-1]
        return jsonify({
            'symbol': symbol,
            'bid': round(latest['close'], 5),
            'ask': round(latest['close'], 5),
            'spread': 0,
            'high': round(latest['high'], 5),
            'low': round(latest['low'], 5),
            'price': round(latest['close'], 5),
        })
    # Fallback: try to get from radar scan cache
    spec = INSTRUMENTS.get(symbol, {})
    base_price = {
        'EURUSD': 1.08500, 'GBPUSD': 1.28500, 'USDJPY': 150.500, 'USDCHF': 0.88500,
        'USDCAD': 1.36500, 'AUDUSD': 0.67500, 'NZDUSD': 0.61500,
        'GBPJPY': 192.500, 'EURJPY': 162.500, 'XAUUSD': 2350.00,
        'US30': 39000, 'SP500': 5400, 'NAS100': 19500,
        'BTCUSD': 58000, 'ETHUSD': 3100,
    }.get(symbol, 100.0)
    return jsonify({
        'symbol': symbol,
        'bid': base_price,
        'ask': base_price,
        'spread': 0,
        'price': base_price,
    })

@app.route('/api/calculator', methods=['POST'])
def api_calculator():
    """Position calculator: pips, fees, risk per trade."""
    data = request.json
    symbol = data.get('symbol', 'EURUSD').upper()
    lot_size = float(data.get('lot_size', 0.01))
    entry_price = float(data.get('entry_price', 0))
    stop_loss = float(data.get('stop_loss', 0))
    take_profit = float(data.get('take_profit', 0))
    account_currency = data.get('account_currency', 'USD')
    account_balance = float(data.get('account_balance', 500))
    risk_pct = float(data.get('risk_pct', 2.0))

    spec = INSTRUMENTS.get(symbol, {})
    pip_factor = spec.get('pip_factor', 0.0001) if spec else 0.0001
    contract_size = spec.get('contract_size', 1000) if spec else 1000
    digits = spec.get('digits', 5) if spec else 5
    cat = spec.get('category', '')

    # Per-instrument commission (standard lot RT)
    if cat == 'major':
        comm_per_lot = 6.00
    elif cat == 'cross':
        comm_per_lot = 8.00
    elif cat == 'index':
        comm_per_lot = 2.00
    elif cat == 'commodity':
        comm_per_lot = 5.00
    elif cat == 'stock':
        comm_per_lot = 3.00
    elif cat == 'crypto':
        comm_per_lot = 10.00
    else:
        comm_per_lot = 6.00

    # Pip calculations
    if entry_price > 0 and stop_loss > 0:
        sl_pips = round(abs(entry_price - stop_loss) / pip_factor, 1)
    else:
        sl_pips = 0

    if entry_price > 0 and take_profit > 0:
        tp_pips = round(abs(entry_price - take_profit) / pip_factor, 1)
    else:
        tp_pips = 0

    # Pip value
    pip_value = pip_value_usd(symbol, entry_price if entry_price > 0 else 1.0)

    # Commission (scaled from standard lot to user's lot size)
    commission_rt = round(comm_per_lot * lot_size, 2)

    # Risk in USD
    risk_usd = round(sl_pips * pip_value * lot_size + commission_rt, 2)

    # Reward in USD
    reward_usd = round(tp_pips * pip_value * lot_size - commission_rt, 2)

    # Risk/Reward
    rr = round(reward_usd / risk_usd, 2) if risk_usd > 0 else 0

    # % of account at risk
    risk_pct_of_account = round(risk_usd / account_balance * 100, 2) if account_balance > 0 else 0

    # Recommended lot size for target risk %
    if sl_pips > 0 and pip_value > 0:
        risk_per_lot = sl_pips * pip_value + comm_per_lot
        if risk_per_lot > 0:
            recommended_lot = round(account_balance * (risk_pct / 100) / risk_per_lot, 2)
        else:
            recommended_lot = lot_size
    else:
        recommended_lot = lot_size

    # Swap/funding fee (crypto = 0.01% daily, forex = swap points)
    is_crypto = spec.get('crypto', False)
    swap_fee = 0.0
    if is_crypto:
        swap_fee = round(entry_price * lot_size * 0.0001, 2) if entry_price > 0 else 0
    else:
        swap_fee = round(commission_rt * 0.05, 2)  # ~5% of commission for overnight

    return jsonify({
        'symbol': symbol,
        'commodity': spec.get('description', symbol),
        'pip_factor': pip_factor,
        'contract_size': contract_size,
        'digits': digits,
        'entry_price': entry_price,
        'pip_value_usd': round(pip_value, 4),
        'sl_pips': sl_pips,
        'tp_pips': tp_pips,
        'commission_per_lot': comm_per_lot,
        'commission': commission_rt,
        'swap_fee_daily': swap_fee,
        'risk_usd': risk_usd,
        'reward_usd': reward_usd,
        'risk_reward_ratio': rr,
        'risk_pct_of_account': risk_pct_of_account,
        'recommended_lot_size': recommended_lot,
        'max_loss_5pct': round(account_balance * 0.05, 2),
        'max_loss_2pct': round(account_balance * 0.02, 2),
    })


@app.route('/api/journal', methods=['GET', 'POST'])
def api_journal():
    """Trading journal CRUD."""
    if request.method == 'GET':
        entries = _load_journal()
        return jsonify({'entries': entries})

    data = request.json
    entries = _load_journal()

    if data.get('action') == 'delete':
        idx = int(data.get('index', -1))
        if 0 <= idx < len(entries):
            entries.pop(idx)
            _save_journal(entries)
        return jsonify({'ok': True})

    if data.get('action') == 'clear':
        _save_journal([])
        return jsonify({'ok': True})

    # Add entry
    entry = {
        'symbol': data.get('symbol', '').upper(),
        'direction': data.get('direction', 'Buy'),
        'entry_price': float(data.get('entry_price', 0)),
        'exit_price': float(data.get('exit_price', 0)),
        'lot_size': float(data.get('lot_size', 0.01)),
        'stop_loss': float(data.get('stop_loss', 0)),
        'take_profit': float(data.get('take_profit', 0)),
        'date': data.get('date', datetime.now().strftime('%Y-%m-%d %H:%M')),
        'notes': data.get('notes', ''),
        'pnl': float(data.get('pnl', 0)),
        'result': data.get('result', 'open'),
        'id': datetime.now().timestamp(),
    }
    entries.append(entry)
    _save_journal(entries)

    return jsonify({'ok': True, 'entry': entry})


@app.route('/api/journal/stats')
def api_journal_stats():
    """Journal statistics."""
    entries = _load_journal()
    closed = [e for e in entries if e.get('result') in ('win', 'loss')]
    wins = [e for e in closed if e.get('result') == 'win']
    losses = [e for e in closed if e.get('result') == 'loss']

    total_trades = len(closed)
    win_rate = round(len(wins) / total_trades * 100, 1) if total_trades > 0 else 0
    total_pnl = sum(e.get('pnl', 0) for e in closed)
    avg_win = sum(e.get('pnl', 0) for e in wins) / len(wins) if wins else 0
    avg_loss = sum(e.get('pnl', 0) for e in losses) / len(losses) if losses else 0
    best_trade = max(wins, key=lambda x: x.get('pnl', 0)) if wins else None
    worst_trade = min(losses, key=lambda x: x.get('pnl', 0)) if losses else None
    profit_factor = abs(sum(e.get('pnl', 0) for e in wins) / sum(e.get('pnl', 0) for e in losses)) if losses and sum(e.get('pnl', 0) for e in losses) != 0 else float('inf')

    return jsonify({
        'total_trades': total_trades,
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': win_rate,
        'total_pnl': round(total_pnl, 2),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'profit_factor': round(profit_factor, 2) if profit_factor != float('inf') else 'Inf',
        'best_trade': best_trade,
        'worst_trade': worst_trade,
    })


if __name__ == '__main__':
    import socket
    hostname = socket.gethostname()
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '1') == '1'
    try:
        lan_ip = socket.gethostbyname(hostname)
    except Exception:
        lan_ip = '127.0.0.1'
    print('=' * 50)
    print('   Yams Radar - Web Dashboard')
    print(f'   Local:   http://127.0.0.1:{port}')
    if port == 5000:
        print(f'   Network: http://{lan_ip}:{port}')
    print('=' * 50)
    app.run(debug=debug, host='0.0.0.0', port=port)
