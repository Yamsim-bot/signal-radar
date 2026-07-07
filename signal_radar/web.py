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
    """Serve news headlines and calendar events."""
    from .radar import scan
    from .config import Config
    cfg = Config()
    result = radar_scan(cfg)

    headlines = []
    for h in result.sentiment.headlines[:15]:
        headlines.append({
            'source': h.source,
            'title': h.title,
            'published': h.published,
            'sentiment': round(h.sentiment_score, 3),
            'keywords': h.keywords,
        })

    events = []
    for e in result.calendar.events_this_week[:20]:
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
    for e in result.calendar.central_bank_events[:10]:
        cb_events.append({
            'time': e.time,
            'currency': e.currency,
            'event': e.event,
            'impact': e.impact,
        })

    return jsonify({
        'headlines': headlines,
        'calendar_events': events,
        'central_bank_events': cb_events,
        'sentiment_score': result.sentiment.overall_score,
        'trending_topics': result.sentiment.trending_topics[:8],
        'source_breakdown': result.sentiment.source_breakdown,
        'dovish_count': result.sentiment.dovish_count,
        'hawkish_count': result.sentiment.hawkish_count,
        'risk_on_count': result.sentiment.risk_on_count,
        'risk_off_count': result.sentiment.risk_off_count,
        'high_impact_count': result.calendar.high_impact_count,
    })


@app.route('/api/scan')
def api_scan():
    """Run radar scan, return JSON."""
    cfg = Config()
    result = radar_scan(cfg)
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


@app.route('/api/chat', methods=['POST'])
def api_chat():
    """AI assistant for trading questions."""
    question = request.json.get('question', '').strip()
    if not question:
        return jsonify({'answer': 'Please ask a question about a trading instrument.'})

    # Run radar to get current data
    cfg = Config()
    result = radar_scan(cfg)
    question_lower = question.lower()

    # Find mentioned symbols
    mentioned = []
    for i in result.instruments:
        if i.symbol.lower() in question_lower or i.name.lower() in question_lower:
            mentioned.append(i)

    # Find mentioned categories
    cat_map = {'major': 'major', 'majors': 'major', 'cross': 'cross', 'crosses': 'cross',
               'index': 'index', 'indices': 'index', 'commodity': 'commodity',
               'stock': 'stocks', 'stocks': 'stock'}
    mentioned_cats = [cat_map[w] for w in cat_map if w in question_lower]

    # Build answer
    if not mentioned and not mentioned_cats:
        # General answer
        market = result.market_sentiment
        top_buy = [i for i in result.instruments if i.bias in ('Strong Buy', 'Buy')][:3]
        top_sell = [i for i in result.instruments if i.bias in ('Strong Sell', 'Sell')][:3]

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
                breakdown_str = (
                    f"\n\n**Fundamental factors:**"
                    f"\n• Growth: {fb.growth:+.0f}"
                    f"\n• Inflation: {fb.inflation:+.0f}"
                    f"\n• Jobs: {fb.jobs:+.0f}"
                    f"\n• Sentiment: {fb.sentiment:+.0f}"
                    f"\n• Trend: {fb.trend:+.0f}"
                    f"\n• Seasonality: {fb.seasonality:+.0f}"
                )

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

        answer = (
            f"📈 **{label}** ({len(cat_instrs)} instruments)\n\n"
            f"**Bullish:** {', '.join(f'{i.symbol}({i.strength})' for i in bulls) if bulls else 'None'}\n"
            f"**Bearish:** {', '.join(f'{i.symbol}({i.strength})' for i in bears) if bears else 'None'}\n"
            f"**Neutral:** {', '.join(f'{i.symbol}' for i in neutrals[:5])}{'...' if len(neutrals) > 5 else '' if neutrals else 'None'}\n\n"
            f"Ask about a specific symbol for detailed breakdown."
        )
        return jsonify({'answer': answer})

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
    print('   Signal Radar - Web Dashboard')
    print(f'   Local:   http://127.0.0.1:{port}')
    if port == 5000:
        print(f'   Network: http://{lan_ip}:{port}')
    print('=' * 50)
    app.run(debug=debug, host='0.0.0.0', port=port)
