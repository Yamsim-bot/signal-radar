"""CLI Dashboard — color-coded radar table with strength filter."""

import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _green(text): return f'\033[92m{text}\033[0m'
def _red(text): return f'\033[91m{text}\033[0m'
def _yellow(text): return f'\033[93m{text}\033[0m'
def _cyan(text): return f'\033[96m{text}\033[0m'
def _blue(text): return f'\033[94m{text}\033[0m'
def _bold(text): return f'\033[1m{text}\033[0m'
def _dim(text): return f'\033[2m{text}\033[0m'
def _magenta(text): return f'\033[95m{text}\033[0m'


def _color_bias(bias: str) -> str:
    if bias == 'Strong Buy':
        return _green(bias)
    elif bias == 'Buy':
        return _cyan(bias)
    elif bias == 'Neutral':
        return _yellow(bias)
    elif bias == 'Sell':
        return _blue(bias)
    elif bias == 'Strong Sell':
        return _red(bias)
    return bias


def _strength_bar(strength: int, length: int = 10) -> str:
    """Render a strength bar. 10 = full, 1 = minimal."""
    filled = max(0, min(length, strength))
    bar = '#' * filled + '-' * (length - filled)
    if strength >= 8:
        return _green(bar)
    elif strength >= 5:
        return _yellow(bar)
    else:
        return _red(bar)


def _factor_bar(value: float, length: int = 10) -> str:
    """Render a factor score bar: green positive, red negative."""
    half = length
    # Scale: -100 to +100 mapped to -length to +length
    scaled = int((value / 100.0) * half)
    if scaled > 0:
        bar = ' ' * half + _green('+' * min(scaled, half))
    elif scaled < 0:
        bar = _red('-' * min(-scaled, half)) + ' ' * half
    else:
        bar = ' ' * (half - 1) + '|' + ' ' * half
    return bar


def parse_args():
    parser = argparse.ArgumentParser(
        description='Yams Radar — Multi-Asset Trading Bias Scanner',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Examples:\n'
            '  python -m signal_radar.cli                     # full scan\n'
            '  python -m signal_radar.cli -s 7                # only strong signals (7+)\n'
            '  python -m signal_radar.cli --min-strength 5    # medium and above\n'
            '  python -m signal_radar.cli --max-strength 3    # only weak/bearish signals\n'
            '  python -m signal_radar.cli -s 8 --cat major    # strongest majors only\n'
        ),
    )
    parser.add_argument(
        '-s', '--min-strength', type=int, default=1, choices=range(1, 11),
        help='Minimum signal strength 1-10 (1=all, 10=only strongest). Default: 1',
    )
    parser.add_argument(
        '--max-strength', type=int, default=10, choices=range(1, 11),
        help='Maximum signal strength 1-10. Default: 10 (no cap)',
    )
    parser.add_argument(
        '-c', '--category', type=str, default=None,
        choices=['major', 'cross', 'index', 'commodity', 'stock'],
        help='Filter by instrument category',
    )
    parser.add_argument(
        '--top', type=int, default=0,
        help='Show only top N instruments by strength (default: 0 = all)',
    )
    parser.add_argument(
        '-d', '--detail', type=int, default=0,
        help='Show factor breakdown for top N strongest signals (Growth/Inflation/Jobs/Sentiment/Trend/Seasonality)',
    )
    parser.add_argument(
        '--news-only', action='store_true',
        help='Show only instruments with high-impact news events this week',
    )
    parser.add_argument(
        '--risk', type=str, default=None, choices=['risk_on', 'risk_off', 'neutral'],
        help='Filter by risk sentiment environment',
    )
    return parser.parse_args()


def main():
    args = parse_args()

    from .radar import scan
    from .config import Config
    from .instruments import CATEGORY_LABELS

    cfg = Config()

    print(_bold('\n' + '=' * 62))
    print(_bold('           YAMS RADAR — Multi-Asset Scanner'))
    print(_bold('=' * 62))

    from . import instruments as instr_mod
    total_instruments = len(instr_mod.get_symbols())
    print(f'\nScanning {total_instruments} instruments...', end=' ')
    sys.stdout.flush()

    result = scan(cfg)

    from datetime import datetime
    print(f'done.  {_dim("Updated:")} {datetime.now().strftime("%Y-%m-%d %H:%M UTC")}')

    # --- Apply filters ---
    filtered = list(result.instruments)

    if args.category:
        filtered = [i for i in filtered if i.category == args.category]

    # Strength filter: 1-10 inclusive
    filtered = [
        i for i in filtered
        if args.min_strength <= i.strength <= args.max_strength
    ]

    # News-only filter: show only symbols with high-impact events
    if args.news_only and hasattr(result, 'calendar'):
        event_currencies = {e.currency for e in result.calendar.events_this_week if e.impact == 'High'}
        filtered = [i for i in filtered if i.symbol[:3] in event_currencies or i.symbol[3:] in event_currencies]

    # Risk sentiment filter
    if args.risk and hasattr(result, 'fundamental'):
        if args.risk == 'risk_on' and result.fundamental.risk_sentiment != 'risk_on':
            filtered = []
        elif args.risk == 'risk_off' and result.fundamental.risk_sentiment != 'risk_off':
            filtered = []
        elif args.risk == 'neutral' and result.fundamental.risk_sentiment != 'neutral':
            filtered = []

    if args.top > 0:
        # Sort by strength descending, then by abs(bias_score) descending
        filtered.sort(key=lambda x: (x.strength, abs(x.bias_score)), reverse=True)
        filtered = filtered[:args.top]

    # Market overview
    market = result.market_sentiment
    if 'Bullish' in market:
        market_colored = _green(market)
    elif 'Bearish' in market:
        market_colored = _red(market)
    else:
        market_colored = _yellow(market)

    shown = len(filtered)
    total = len(result.instruments)

    print(f'\n  {_bold("Market:")} {market_colored}  '
          f'({_bold("Score:")} {result.market_score:+.1f})  |  '
          f'{_bold("Filter:")} strength {args.min_strength}-{args.max_strength}  |  '
          f'{_bold("Showing:")} {shown}/{total} instruments')

    # Show top picks from filtered set
    top_buy = [i for i in filtered if i.bias in ('Strong Buy', 'Buy')][:3]
    top_sell = [i for i in filtered if i.bias in ('Strong Sell', 'Sell')][:3]
    if top_buy:
        print(f'  {_bold("Top buys:")} {_green(", ".join(f"{i.symbol}({i.strength})" for i in top_buy))}')
    if top_sell:
        print(f'  {_bold("Top sells:")} {_red(", ".join(f"{i.symbol}({i.strength})" for i in top_sell))}')

    CATEGORY_ORDER = ['major', 'cross', 'index', 'commodity', 'stock']

    for cat in CATEGORY_ORDER:
        cat_instrs = [i for i in filtered if i.category == cat]
        if not cat_instrs:
            continue

        label = CATEGORY_LABELS.get(cat, cat.upper())
        print(f'\n  {_bold("-- " + label + " --")}')

        # Header — replaced Session column with Best Time column
        print(f'  {_bold("Symbol")}    {"Bias":<14} {"Score":>6} {"Str":>3}  {"Conf":>4}  {"Price":>10}  {"Chg%":>7}  {"Trend":<10}  {"Best Time":<18}  {"Entry"}')
        print(f'  {_dim("------")}   {"----":<14} {"-----":>6} {"---":>3}  {"----":>4}  {"-----":>10}  {"----":>7}  {"-----":<10}  {"--------":<18}  {"-----"}')

        for i in cat_instrs:
            sym = _bold(f'{i.symbol:<6}')
            bias = _color_bias(f'{i.bias:<14}')
            score = f'{i.bias_score:+.0f}'
            strength_display = _strength_bar(i.strength, 3) + f' {i.strength}'
            conf = f'{i.confidence}%'
            price = f'{i.price:<10}'
            chg = f'{i.change_pct:+.2f}%'
            chg_colored = _green(chg) if i.change_pct >= 0 else _red(chg)

            trend = i.explanation.trend_direction
            if trend == 'uptrend':
                trend_colored = _green(f'{trend:<10}')
            elif trend == 'downtrend':
                trend_colored = _red(f'{trend:<10}')
            else:
                trend_colored = _yellow(f'{trend:<10}')

            from .instruments import best_session_str
            best_time = best_session_str(i.symbol)
            best_time_colored = _cyan(f'{best_time:<18}')

            entry = i.explanation.entry_timing
            if entry == 'now':
                entry_colored = _green(f'{entry}')
            elif entry == 'soon':
                entry_colored = _cyan(f'{entry}')
            else:
                entry_colored = _yellow(f'{entry}')

            print(f'  {sym}   {bias} {score:>6}  {strength_display:>5}  {conf:>4}  {price}  {chg_colored:>7}  {trend_colored}  {best_time_colored}  {entry_colored}')

    # Summary bar
    counts = {'Strong Buy': 0, 'Buy': 0, 'Neutral': 0, 'Sell': 0, 'Strong Sell': 0}
    for i in filtered:
        counts[i.bias] = counts.get(i.bias, 0) + 1

    if shown > 0:
        print(f'\n  {_bold("Distribution:")}  '
              f'{_green(f"Strong Buy {counts["Strong Buy"]}")}  '
              f'{_cyan(f"Buy {counts['Buy']}")}  '
              f'{_yellow(f"Neutral {counts['Neutral']}")}  '
              f'{_blue(f"Sell {counts['Sell']}")}  '
              f'{_red(f"Strong Sell {counts['Strong Sell']}")}')

        # Top pick detail
        if filtered:
            # Pick strongest signal
            strongest = max(filtered, key=lambda x: (x.strength, abs(x.bias_score)))
            direction = _green if strongest.bias in ('Strong Buy', 'Buy') else _red
            print(f'\n  {_bold("* Strongest Signal:")} {direction(strongest.symbol)} ({strongest.name})  '
                  f'{_bold("Strength:")} {strongest.strength}/10  {_bold("Bias:")} {_color_bias(strongest.bias)}')
            print(f'    {strongest.explanation.explanation}')

        # Factor breakdown detail for top N
        if args.detail > 0:
            # Sort by strength descending
            top_n = sorted(filtered, key=lambda x: (x.strength, abs(x.bias_score)), reverse=True)[:args.detail]
            for i in top_n:
                fb = i.explanation.fundamental_breakdown
                if fb is None:
                    continue
                direction = _green if i.bias in ('Strong Buy', 'Buy') else _red
                print(f'\n  {_bold("[Factor Detail]")} {direction(i.symbol)} ({i.bias})')
                print(f'     {_bold("Growth:"):<14} {_factor_bar(fb.growth)}  {fb.growth:+.0f}')
                print(f'     {_bold("Inflation:"):<14} {_factor_bar(fb.inflation)}  {fb.inflation:+.0f}')
                print(f'     {_bold("Jobs:"):<14} {_factor_bar(fb.jobs)}  {fb.jobs:+.0f}')
                print(f'     {_bold("Sentiment:"):<14} {_factor_bar(fb.sentiment)}  {fb.sentiment:+.0f}')
                print(f'     {_bold("Trend:"):<14} {_factor_bar(fb.trend)}  {fb.trend:+.0f}')
                print(f'     {_bold("Seasonality:"):<14} {_factor_bar(fb.seasonality)}  {fb.seasonality:+.0f}')
                print(f'     {"-" * 40}')
                print(f'     {_bold("Factor Total:"):<14} {_bold(_factor_bar(fb.total))}  {_bold(f"{fb.total:+.0f}")}')

    # Calendar summary
    print(f'\n  {_bold("Economic Calendar Events This Week:")}')
    cal_events = result.calendar.events_this_week[:8]
    if cal_events:
        for ev in cal_events:
            impact_color = _red if ev.impact == 'High' else (_yellow if ev.impact == 'Medium' else _dim)
            day_str = ev.time[:10] if len(ev.time) > 10 else ev.time
            print(f'    {impact_color(f"[{ev.impact:<6}]")} {day_str}  {ev.currency:<4}  {ev.event}')
    else:
        print(f'    {_dim("No upcoming events")}')

    print(f'\n  {_bold("Central Banks:")}  '
          f'{" | ".join(f"{e.currency}: {e.event}" for e in result.calendar.central_bank_events[:4])}')

    # News headlines
    print(f'\n  {_bold("News Headlines:")}')
    for h in result.sentiment.headlines[:6]:
        score_color = _green if h.sentiment_score > 0.1 else (_red if h.sentiment_score < -0.1 else _yellow)
        score_str = f'{h.sentiment_score:+.2f}'
        print(f'    [{score_color(score_str)}] {h.source:<12} {h.title[:80]}')
    print(f'  {_bold("Trending topics:")}  {", ".join(result.sentiment.trending_topics[:6])}')

    # Sentiment summary
    print(f'\n  {_bold("News Sentiment:")}  '
          f'Score: {result.sentiment.overall_score:+.1f}  |  '
          f'Dovish: {result.sentiment.dovish_count}  Hawkish: {result.sentiment.hawkish_count}  |  '
          f'Risk-on: {result.sentiment.risk_on_count}  Risk-off: {result.sentiment.risk_off_count}')

    src_str = '  |  '.join(f'{s}: {v:+.2f}' for s, v in result.sentiment.source_breakdown.items())
    if src_str:
        print(f'  {_bold("Sources:")}  {src_str}')

    # Fundamental summary
    fund = result.fundamental
    print(f'\n  {_bold("Fundamental:")}  '
          f'Overall: {fund.overall_score:+.1f}  |  '
          f'Risk: {fund.risk_sentiment} ({fund.risk_score:+.1f})  |  '
          f'Bullish: {", ".join(fund.top_bullish[:3])}  |  '
          f'Bearish: {", ".join(fund.top_bearish[:3])}')

    # Filter hint
    shown_desc = f' (filtered to {shown}/{total})' if shown < total else ''
    print(f'\n  {_dim(f"Showing {shown} instruments{shown_desc}")}')
    print(f'  {_dim("Usage: python -m signal_radar.cli --help")}')

    # Legend
    print(f'\n  {_bold("Legend:")}')
    print(f'  {_dim("Bias:")}  {_green("Strong Buy")}  {_cyan("Buy")}  {_yellow("Neutral")}  {_blue("Sell")}  {_red("Strong Sell")}')
    print(f'  {_dim("Strength:")}  {_green("8-10 strong")}  {_yellow("5-7 moderate")}  {_red("1-4 weak")}')
    print(f'  {_dim("Best Time:")}  {_cyan("Trading session hours in GMT")}')
    print(f'  {_dim("Entry:")}  {_green("now")}  {_cyan("soon")}  {_yellow("wait/avoid")}')
    print(f'  {_dim("News:")}  {_green("+score bullish")}  {_red("-score bearish")}  {_yellow("neutral")}')
    print()


if __name__ == '__main__':
    main()
