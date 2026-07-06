#!/usr/bin/env python3
"""
M5 Scalper Backtest Engine — ADX/DI + ATR Trailing
====================================================
Designed for $250-350/month on $500 cent account.
Tests EURJPY, GBPJPY, XAUUSD with configurable parameters.

Strategy:
  - M5 entry, fast scalping with ADX(14) >= threshold
  - DI+/DI- direction filter
  - EMA 20/50 trend filter
  - Tight ATR stops (0.6-1.2x), quick targets (0.8-2.0x)
  - Partial TP at 1:1 + ATR trailer
  - Multiple instruments, session-aware

Usage:
    python m5_scalper_backtest.py
    python m5_scalper_backtest.py --quick
    python m5_scalper_backtest.py --detailed "R2-A1.0-RR1.0-ADX20"
    python m5_scalper_backtest.py --pair EURJPY
    python m5_scalper_backtest.py --help
"""
import sys, os, json, math, itertools
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, asdict

HERE = Path(__file__).parent.resolve()

try:
    import MetaTrader5 as mt5
    HAS_MT5 = True
except ImportError:
    HAS_MT5 = False

import pandas as pd
import numpy as np


# ─────────────────────────────────────────────────────────
# INSTRUMENT SPECS (Vantage Cent Raw ECN)
# ─────────────────────────────────────────────────────────
INSTRUMENTS = {
    'EURJPY': {
        'pip_factor': 0.01,               # 1 pip = 0.01 price
        'contract_size': 1000,             # cent lot = 1000 base
        'session_start': 8,                # London open GMT
        'session_end': 15,                  # London close GMT
        'typical_spread': 0.4,             # pips
        'margin_percent': 0.2,             # ~0.2% margin
    },
    'GBPJPY': {
        'pip_factor': 0.01,
        'contract_size': 1000,
        'session_start': 8,
        'session_end': 15,
        'typical_spread': 0.6,
        'margin_percent': 0.2,
    },
    'XAUUSD': {
        'pip_factor': 0.01,
        'contract_size': 100,        # 1 cent lot = 1 oz (0.01 standard = 100/100... actually standard = 100 oz, cent = 1 oz)
        'session_start': 12,          # NY open GMT
        'session_end': 20,            # NY close GMT
        'typical_spread': 2.0,
        'margin_percent': 1.0,        # higher margin for gold
    },
}

# Commission per cent lot round-trip (Vantage Cent Raw ECN)
COMMISSION_PER_LOT = 0.06


# ─────────────────────────────────────────────────────────
# PIP VALUE CALCULATION
# ─────────────────────────────────────────────────────────
def pip_value_usd(symbol: str, price: float) -> float:
    """USD value of 1 pip for 1 cent lot."""
    spec = INSTRUMENTS[symbol]
    pip = spec['pip_factor']
    contract = spec['contract_size']
    if symbol == 'XAUUSD':
        # XAUUSD: price is $/oz. 1 cent lot = 1 oz
        # 1 pip = $0.01, value = contract * pip = 1 * 0.01 = $0.01
        # But in practice Vantage quotes pip value as $0.10 per cent lot
        return 0.10
    else:
        # JPY pairs: 1 pip = 0.01, contract = 1000 units
        # Pip value in JPY = 1000 * 0.01 = 10 JPY
        # In USD = 10 JPY / USDJPY rate (approximated by price USD)
        return pip * contract / price


# ─────────────────────────────────────────────────────────
# DATA FETCHING
# ─────────────────────────────────────────────────────────
def resolve_symbol(base: str) -> str | None:
    """Try common suffixes to find the symbol in MT5."""
    candidates = [base, base+"+", base+"-", base+".c", base+"c"]
    for c in candidates:
        info = mt5.symbol_info(c)
        if info:
            tick = mt5.symbol_info_tick(c)
            if tick and tick.bid > 0:
                return c
    return None


def fetch_m5(symbol: str, bars: int = 5000) -> pd.DataFrame | None:
    """Download M5 data from MT5."""
    if not HAS_MT5:
        return None
    if not mt5.initialize():
        return None
    resolved = resolve_symbol(symbol)
    if not resolved:
        mt5.shutdown()
        return None
    mt5.symbol_select(resolved, True)
    rates = mt5.copy_rates_from_pos(resolved, mt5.TIMEFRAME_M5, 0, bars)
    mt5.shutdown()
    if rates is None or len(rates) < 500:
        return None
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('time', inplace=True)
    return df


# ─────────────────────────────────────────────────────────
# INDICATORS
# ─────────────────────────────────────────────────────────
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add EMA, ATR, ADX, DI, RSI columns. Returns new DataFrame."""
    d = df.copy()
    close = d['close'].values
    high = d['high'].values
    low = d['low'].values

    # EMAs
    d['ema20'] = pd.Series(close).ewm(span=20).mean().values
    d['ema50'] = pd.Series(close).ewm(span=50).mean().values
    d['uptrend'] = d['ema20'] > d['ema50']
    d['downtrend'] = d['ema20'] < d['ema50']

    # RSI(7)
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_g = pd.Series(gain).ewm(span=7).mean().values
    avg_l = pd.Series(loss).ewm(span=7).mean().values
    rs = avg_g / np.maximum(avg_l, 0.001)
    d['rsi'] = 100 - (100 / (1 + rs))

    # ATR(14)
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(abs(high[1:] - close[:-1]), abs(low[1:] - close[:-1]))
    )
    atr_vals = np.concatenate([
        [np.mean(tr[:14])],
        pd.Series(tr).ewm(span=14, adjust=False).mean().values
    ])
    if len(atr_vals) < len(close):
        atr_vals = np.concatenate([atr_vals, [atr_vals[-1]] * (len(close) - len(atr_vals))])
    d['atr'] = atr_vals

    # ADX / DI (Wilder's 14-period)
    length = len(close)

    # +DM and -DM
    plus_dm = np.zeros(length)
    minus_dm = np.zeros(length)
    for i in range(1, length):
        up_move = high[i] - high[i-1]
        down_move = low[i-1] - low[i]
        if up_move > down_move and up_move > 0:
            plus_dm[i] = up_move
        if down_move > up_move and down_move > 0:
            minus_dm[i] = down_move

    # True Range
    true_range = np.zeros(length)
    true_range[0] = high[0] - low[0]
    for i in range(1, length):
        true_range[i] = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))

    # Wilder's smoothing (first value = SMA of 14, then EMA with alpha=1/14)
    period = 14
    tr_smooth = np.zeros(length)
    pdm_smooth = np.zeros(length)
    mdm_smooth = np.zeros(length)

    tr_smooth[period-1] = np.mean(true_range[:period])
    pdm_smooth[period-1] = np.mean(plus_dm[:period])
    mdm_smooth[period-1] = np.mean(minus_dm[:period])

    alpha = 1.0 / period
    for i in range(period, length):
        tr_smooth[i] = true_range[i] * alpha + tr_smooth[i-1] * (1 - alpha)
        pdm_smooth[i] = plus_dm[i] * alpha + pdm_smooth[i-1] * (1 - alpha)
        mdm_smooth[i] = minus_dm[i] * alpha + mdm_smooth[i-1] * (1 - alpha)

    # DI values
    plus_di = np.where(tr_smooth > 0, 100 * pdm_smooth / tr_smooth, 0)
    minus_di = np.where(tr_smooth > 0, 100 * mdm_smooth / tr_smooth, 0)
    d['plus_di'] = plus_di
    d['minus_di'] = minus_di

    # DX and ADX
    dx = np.where((plus_di + minus_di) > 0,
                  100 * abs(plus_di - minus_di) / (plus_di + minus_di), 0)
    adx = np.zeros(length)
    adx[period*2-1] = np.mean(dx[:period*2])
    for i in range(period*2, length):
        adx[i] = dx[i] * alpha + adx[i-1] * (1 - alpha)
    d['adx'] = adx

    # DI crossovers
    d['di_cross_up'] = (d['plus_di'] > d['minus_di']) & (d['plus_di'].shift(1) <= d['minus_di'].shift(1))
    d['di_cross_down'] = (d['minus_di'] > d['plus_di']) & (d['minus_di'].shift(1) <= d['plus_di'].shift(1))

    # Trend established (ADX >= threshold and DI direction set)
    d['di_uptrend'] = d['plus_di'] > d['minus_di']
    d['di_downtrend'] = d['minus_di'] > d['plus_di']

    return d


# ─────────────────────────────────────────────────────────
# SIGNAL DETECTION
# ─────────────────────────────────────────────────────────
def detect_signals(df: pd.DataFrame, i: int, cfg: dict) -> dict:
    """Check for scalping signals at bar i.

    Returns dict with 'direction' or None.

    Entry types:
      - ADX trend: ADX >= threshold + DI direction + EMA trend
      - DI crossover: ADX >= threshold + DI cross + EMA trend
    """
    bar = df.iloc[i]
    prev = df.iloc[i-1]

    direction = None
    entry_type = None

    adx_thresh = cfg['adx_threshold']
    use_di_cross = cfg.get('use_di_cross', False)
    use_rsi_filter = cfg.get('use_rsi_filter', False)
    rsi_thresh = cfg.get('rsi_threshold', 50)

    if use_di_cross:
        # DI crossover entry (fewer, higher quality signals)
        if bar['di_cross_up'] and bar['uptrend'] and bar['adx'] >= adx_thresh:
            direction = 'long'
            entry_type = 'DI_cross_up'
        elif bar['di_cross_down'] and bar['downtrend'] and bar['adx'] >= adx_thresh:
            direction = 'short'
            entry_type = 'DI_cross_down'
    else:
        # ADX trend entry (more signals)
        if bar['di_uptrend'] and bar['uptrend'] and bar['adx'] >= adx_thresh:
            if not use_rsi_filter or bar['rsi'] > rsi_thresh:
                direction = 'long'
                entry_type = 'ADX_trend_up'
        elif bar['di_downtrend'] and bar['downtrend'] and bar['adx'] >= adx_thresh:
            if not use_rsi_filter or bar['rsi'] < (100 - rsi_thresh):
                direction = 'short'
                entry_type = 'ADX_trend_down'

    if direction is None:
        return {'direction': None}

    # Calculate levels
    atr = bar['atr']
    price = bar['close']
    sl_pips = max(atr * cfg['atr_sl_mult'] / 0.01, 12.0)  # min 12 pips for M5
    tp_pips = sl_pips * cfg['rr_target']

    return {
        'direction': direction,
        'entry_type': entry_type,
        'price': price,
        'sl_pips': round(sl_pips, 1),
        'tp_pips': round(tp_pips, 1),
        'atr': atr,
        'adx': bar['adx'],
        'rsi': bar['rsi'],
    }


# ─────────────────────────────────────────────────────────
# TRADE SIMULATION
# ─────────────────────────────────────────────────────────
def simulate_trade(df: pd.DataFrame, entry_idx: int, signal: dict,
                   cfg: dict, symbol: str) -> dict:
    """Walk forward and determine trade outcome."""
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    atr = df['atr'].values

    entry_price = signal['price']
    direction = signal['direction']
    sl_pips = signal['sl_pips']
    tp_pips = signal['tp_pips']

    # Position sizing
    risk_usd = cfg['initial_capital'] * cfg['risk_pct'] / 100.0
    pv = pip_value_usd(symbol, entry_price)
    lots_raw = risk_usd / (sl_pips * pv) if sl_pips > 0 and pv > 0 else 1
    lots = max(1, int(lots_raw))
    half_lots = lots * 0.5
    commission = lots * COMMISSION_PER_LOT

    # Price levels
    pip = 0.01
    if direction == 'long':
        sl_price = entry_price - sl_pips * pip
        tp1_price = entry_price + sl_pips * pip      # 1:1 for partial
        tp2_price = entry_price + tp_pips * pip       # full target
    else:
        sl_price = entry_price + sl_pips * pip
        tp1_price = entry_price - sl_pips * pip
        tp2_price = entry_price - tp_pips * pip

    partial_hit = False
    trail_stop = None
    pnl_1 = 0.0
    lookback = min(80, len(df) - entry_idx - 2)

    for j in range(1, lookback + 1):
        idx = entry_idx + j
        h = high[idx]
        l = low[idx]
        c = close[idx]
        a = atr[idx]

        if direction == 'long':
            # SL check
            if l <= sl_price:
                gross = -abs(sl_price - entry_price) / pip * pv * lots
                return {
                    'pnl': round(gross - commission, 2),
                    'exit_reason': 'SL',
                    'exit_idx': idx,
                    'hit_partial': False,
                    'bars_held': j,
                    'lots': lots,
                    'sl_pips': sl_pips,
                }

            # Full TP (check before partial for gap moves)
            if not partial_hit and h >= tp2_price:
                gross = abs(tp2_price - entry_price) / pip * pv * lots
                return {
                    'pnl': round(gross - commission, 2),
                    'exit_reason': 'FULL TP',
                    'exit_idx': idx,
                    'hit_partial': False,
                    'bars_held': j,
                    'lots': lots,
                    'sl_pips': sl_pips,
                }

            # Partial TP at 1:1
            if not partial_hit and h >= tp1_price:
                pnl_1 = abs(tp1_price - entry_price) / pip * pv * half_lots
                sl_price = entry_price + 0.2 * pip  # breakeven + 0.2 pip
                trail_stop = sl_price
                partial_hit = True

                # Check if full TP also happened same bar
                if h >= tp2_price:
                    pnl_2 = abs(tp2_price - entry_price) / pip * pv * half_lots
                    return {
                        'pnl': round(pnl_1 + pnl_2 - commission, 2),
                        'exit_reason': 'PARTIAL+FULL',
                        'exit_idx': idx,
                        'hit_partial': True,
                        'bars_held': j,
                        'lots': lots,
                        'sl_pips': sl_pips,
                    }

            # After partial: trail
            if partial_hit:
                trail_dist = a * cfg.get('trail_atr_mult', 0.8)
                new_stop = c - trail_dist
                if new_stop > trail_stop:
                    trail_stop = new_stop

                if l <= trail_stop:
                    pnl_2 = abs(trail_stop - entry_price) / pip * pv * half_lots
                    return {
                        'pnl': round(pnl_1 + pnl_2 - commission, 2),
                        'exit_reason': 'PARTIAL+TRAIL',
                        'exit_idx': idx,
                        'hit_partial': True,
                        'bars_held': j,
                        'lots': lots,
                        'sl_pips': sl_pips,
                    }

                # TP2 remaining
                if h >= tp2_price:
                    pnl_2 = abs(tp2_price - entry_price) / pip * pv * half_lots
                    return {
                        'pnl': round(pnl_1 + pnl_2 - commission, 2),
                        'exit_reason': 'PARTIAL+TP2',
                        'exit_idx': idx,
                        'hit_partial': True,
                        'bars_held': j,
                        'lots': lots,
                        'sl_pips': sl_pips,
                    }

        else:  # short
            if h >= sl_price:
                gross = -abs(entry_price - sl_price) / pip * pv * lots
                return {
                    'pnl': round(gross - commission, 2),
                    'exit_reason': 'SL',
                    'exit_idx': idx,
                    'hit_partial': False,
                    'bars_held': j,
                    'lots': lots,
                    'sl_pips': sl_pips,
                }

            if not partial_hit and l <= tp2_price:
                gross = abs(entry_price - tp2_price) / pip * pv * lots
                return {
                    'pnl': round(gross - commission, 2),
                    'exit_reason': 'FULL TP',
                    'exit_idx': idx,
                    'hit_partial': False,
                    'bars_held': j,
                    'lots': lots,
                    'sl_pips': sl_pips,
                }

            if not partial_hit and l <= tp1_price:
                pnl_1 = abs(entry_price - tp1_price) / pip * pv * half_lots
                sl_price = entry_price - 0.2 * pip
                trail_stop = sl_price
                partial_hit = True
                if l <= tp2_price:
                    pnl_2 = abs(entry_price - tp2_price) / pip * pv * half_lots
                    return {
                        'pnl': round(pnl_1 + pnl_2 - commission, 2),
                        'exit_reason': 'PARTIAL+FULL',
                        'exit_idx': idx,
                        'hit_partial': True,
                        'bars_held': j,
                        'lots': lots,
                        'sl_pips': sl_pips,
                    }

            if partial_hit:
                trail_dist = a * cfg.get('trail_atr_mult', 0.8)
                new_stop = c + trail_dist
                if new_stop < trail_stop:
                    trail_stop = new_stop
                if h >= trail_stop:
                    pnl_2 = abs(entry_price - trail_stop) / pip * pv * half_lots
                    return {
                        'pnl': round(pnl_1 + pnl_2 - commission, 2),
                        'exit_reason': 'PARTIAL+TRAIL',
                        'exit_idx': idx,
                        'hit_partial': True,
                        'bars_held': j,
                        'lots': lots,
                        'sl_pips': sl_pips,
                    }
                if l <= tp2_price:
                    pnl_2 = abs(entry_price - tp2_price) / pip * pv * half_lots
                    return {
                        'pnl': round(pnl_1 + pnl_2 - commission, 2),
                        'exit_reason': 'PARTIAL+TP2',
                        'exit_idx': idx,
                        'hit_partial': True,
                        'bars_held': j,
                        'lots': lots,
                        'sl_pips': sl_pips,
                    }

    # Timed out — estimate at last bar
    last_price = close[entry_idx + lookback]
    if direction == 'long':
        gross = (last_price - entry_price) / pip * pv * lots
    else:
        gross = (entry_price - last_price) / pip * pv * lots
    return {
        'pnl': round(gross - commission, 2),
        'exit_reason': 'TIMEOUT',
        'exit_idx': entry_idx + lookback,
        'hit_partial': partial_hit,
        'bars_held': lookback,
        'lots': lots,
        'sl_pips': sl_pips,
    }


# ─────────────────────────────────────────────────────────
# NEWS FILTER
# ─────────────────────────────────────────────────────────
NEWS_CALENDAR = [
    (7, 0),    # UK data
    (12, 0),   # BOE / UK
    (13, 30),  # US data (NFP, CPI)
    (14, 0),   # US data alt
    (15, 0),   # US data alt
]

def in_news_blackout(bt: datetime) -> bool:
    h, m = bt.hour, bt.minute
    bar_min = h * 60 + m
    for ev_h, ev_m in NEWS_CALENDAR:
        ev_min = ev_h * 60 + ev_m
        start = ev_min - 45
        end = ev_min + 15
        if start <= bar_min <= end:
            return True
    return False


# ─────────────────────────────────────────────────────────
# SINGLE-INSTRUMENT BACKTEST
# ─────────────────────────────────────────────────────────
def run_backtest(df_raw: pd.DataFrame, cfg: dict, symbol: str) -> dict:
    """Run backtest on one instrument."""
    df = compute_indicators(df_raw)
    close = df['close'].values

    # Warmup
    start_idx = 100  # enough for all indicators

    equity = cfg['initial_capital']
    peak = equity
    trades = []
    consec_loss = 0
    day_trades = 0
    day_losses = 0
    last_day = -1
    day_start_eq = equity
    circuit_breaker = False
    max_trades_per_day = cfg.get('max_trades_per_day', 15)
    max_consec = cfg['max_consecutive_losses']
    max_daily_loss_pct = cfg['max_daily_loss_pct']
    max_dd_pct = cfg['max_total_dd_pct']

    spec = INSTRUMENTS[symbol]
    sess_start = spec['session_start']
    sess_end = spec['session_end']

    for i in range(start_idx, len(df)):
        bt = df.index[i]
        h = bt.hour

        # New day — reset all per-day state
        if bt.day != last_day:
            last_day = bt.day
            day_trades = 0
            day_losses = 0
            consec_loss = 0
            day_start_eq = equity
            circuit_breaker = False

        if circuit_breaker:
            continue

        # Session
        if not (sess_start <= h < sess_end):
            continue

        # News
        if in_news_blackout(bt):
            continue

        # Daily loss
        if day_start_eq > 0:
            dl_pct = (day_start_eq - equity) / day_start_eq * 100
            if dl_pct >= max_daily_loss_pct:
                circuit_breaker = True
                continue

        # Total DD
        if peak > 0:
            dd = (peak - equity) / peak * 100
            if dd >= max_dd_pct:
                circuit_breaker = True
                continue

        # Consecutive losses
        if consec_loss >= max_consec:
            circuit_breaker = True
            continue

        # Daily trade cap
        if day_trades >= max_trades_per_day:
            continue

        # Signal detection
        signal = detect_signals(df, i, cfg)
        if signal['direction'] is None:
            continue

        # Simulate trade
        result = simulate_trade(df, i, signal, cfg, symbol)

        equity += result['pnl']
        if equity > peak:
            peak = equity

        day_trades += 1
        if result['pnl'] < 0:
            consec_loss += 1
            day_losses += 1
        else:
            consec_loss = 0

        result.update({
            'entry_time': str(bt),
            'direction': signal['direction'],
            'entry_type': signal['entry_type'],
            'entry_price': round(close[i], 3),
            'adx': round(signal.get('adx', 0), 1),
            'rsi': round(signal.get('rsi', 0), 1),
            'equity_after': round(equity, 2),
        })
        trades.append(result)

    return summarize(trades, cfg, equity, symbol)


def summarize(trades: list, cfg: dict, final_eq: float, symbol: str) -> dict:
    """Compute metrics."""
    if not trades:
        return {'symbol': symbol, 'trades': 0, 'error': 'no trades'}

    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    total_pnl = sum(t['pnl'] for t in trades)
    gross_win = sum(t['pnl'] for t in wins) or 0
    gross_loss = abs(sum(t['pnl'] for t in losses)) or 0.01

    win_rate = len(wins) / len(trades) * 100
    avg_win = float(np.mean([t['pnl'] for t in wins])) if wins else 0
    avg_loss = float(np.mean([t['pnl'] for t in losses])) if losses else 0

    # Drawdown
    eq = cfg['initial_capital']
    peak = eq
    max_dd = 0
    for t in trades:
        eq += t['pnl']
        if eq > peak:
            peak = eq
        dd = (peak - eq) / max(peak, 1) * 100
        if dd > max_dd:
            max_dd = dd

    # Monthly
    monthly = {}
    for t in trades:
        m = t['entry_time'][:7]
        monthly[m] = monthly.get(m, 0) + t['pnl']
    avg_monthly = float(np.mean(list(monthly.values()))) if monthly else 0

    # Exit breakdown
    sl_count = sum(1 for t in trades if t['exit_reason'] == 'SL')
    full_tp = sum(1 for t in trades if 'FULL' in t['exit_reason'] or 'TP2' in t['exit_reason'])
    partial = sum(1 for t in trades if t.get('hit_partial'))
    trail = sum(1 for t in trades if 'TRAIL' in t['exit_reason'])

    # Average bars held
    avg_bars = float(np.mean([t['bars_held'] for t in trades])) if trades else 0

    # Trades per day
    days_traded = set(t['entry_time'][:10] for t in trades)
    trades_per_day = len(trades) / max(len(days_traded), 1)

    # Monthly projection
    months = len(monthly)
    projected_monthly = total_pnl / max(months, 1)

    # Sharpe-like: avg_trade_return / std_trade_return * sqrt(trades)
    returns = [t['pnl'] for t in trades]
    sharpe = 0
    if len(returns) > 1 and np.std(returns) > 0:
        sharpe = float(np.mean(returns) / np.std(returns) * math.sqrt(len(returns)))

    return {
        'symbol': symbol,
        'risk_pct': cfg['risk_pct'],
        'atr_sl_mult': cfg['atr_sl_mult'],
        'rr_target': cfg['rr_target'],
        'adx_threshold': cfg['adx_threshold'],
        'use_di_cross': cfg.get('use_di_cross', False),
        'use_rsi_filter': cfg.get('use_rsi_filter', False),
        'max_consec': cfg['max_consecutive_losses'],
        'abbrev': cfg.get('abbrev', ''),
        'trades': len(trades),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate_pct': round(win_rate, 1),
        'total_pnl': round(total_pnl, 2),
        'total_return_pct': round(total_pnl / cfg['initial_capital'] * 100, 1),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'profit_factor': round(gross_win / gross_loss, 2),
        'max_drawdown_pct': round(max_dd, 1),
        'avg_monthly': round(avg_monthly, 2),
        'projected_monthly': round(projected_monthly, 2),
        'trades_per_day': round(trades_per_day, 1),
        'avg_bars_held': round(avg_bars, 1),
        'sharpe': round(sharpe, 2),
        'sl_count': sl_count,
        'partial_count': partial,
        'full_tp_count': full_tp,
        'trail_exit_count': trail,
        'final_equity': round(final_eq, 2),
        'monthly_map': {k: round(v, 2) for k, v in monthly.items()},
        'pos_months_pct': round(len([m for m in monthly.values() if m > 0]) / max(len(monthly), 1) * 100, 0),
    }


# ─────────────────────────────────────────────────────────
# COMBINED BACKTEST (EURJPY + XAUUSD - different sessions)
# ─────────────────────────────────────────────────────────
def run_combined(data: dict, cfg: dict) -> dict:
    """Run combined backtest on multiple non-overlapping instruments.

    EURJPY (8-15) + XAUUSD (12-20) with overlap handling:
    - During 12-15: only allow 1 position at a time across both
    - Outside overlap: trade each independently
    """
    if len(data) < 2:
        return {'symbol': '+'.join(data.keys()), 'error': 'need 2+ instruments'}

    # Compute indicators for each
    computed = {}
    for sym, df in data.items():
        computed[sym] = compute_indicators(df)

    syms = list(computed.keys())
    spec1 = INSTRUMENTS[syms[0]]
    spec2 = INSTRUMENTS[syms[1]]

    # Merge into chronological order
    merged = []
    for sym, df in computed.items():
        d = df.copy()
        d['_pair'] = sym
        merged.append(d)
    combined = pd.concat(merged).sort_index()

    equity = cfg['initial_capital']
    peak = equity
    trades = []
    consec_loss = 0
    day_trades = 0
    day_losses = 0
    last_day = -1
    day_start_eq = equity
    circuit_breaker = False

    pos1 = False  # position on syms[0]
    pos2 = False  # position on syms[1]
    overlap_active = False

    start_idx = 100

    # Track last entry time per pair to avoid re-entering too fast
    last_entry_time = {s: None for s in syms}

    for i in range(start_idx, len(combined)):
        row = combined.iloc[i]
        bt = combined.index[i]
        h = bt.hour
        pair = row['_pair']
        is_p1 = pair == syms[0]

        # New day — reset all per-day state
        if bt.day != last_day:
            last_day = bt.day
            day_trades = 0
            day_losses = 0
            consec_loss = 0
            day_start_eq = equity
            circuit_breaker = False

        if circuit_breaker:
            continue

        # Session check
        spec = spec1 if is_p1 else spec2
        if not (spec['session_start'] <= h < spec['session_end']):
            continue

        # News
        if in_news_blackout(bt):
            continue

        # Daily loss
        if day_start_eq > 0:
            dl = (day_start_eq - equity) / day_start_eq * 100
            if dl >= cfg['max_daily_loss_pct']:
                circuit_breaker = True
                continue

        # DD
        if peak > 0:
            dd = (peak - equity) / peak * 100
            if dd >= cfg['max_total_dd_pct']:
                circuit_breaker = True
                continue

        if consec_loss >= cfg['max_consecutive_losses']:
            circuit_breaker = True
            continue

        if day_trades >= cfg.get('max_trades_per_day', 20):
            continue

        # Overlap handling: 12-15 GMT both London and NY active
        # During overlap, only one position at a time
        in_overlap = (h >= 12 and h < 15)
        if in_overlap:
            overlap_active = True
            # If we have a position on one pair, skip the other
            if is_p1 and pos1:
                continue
            if not is_p1 and pos2:
                continue
            if is_p1 and pos2:
                continue  # other pair has position
            if not is_p1 and pos1:
                continue
        else:
            overlap_active = False
            # Outside overlap, still respect per-pair positions
            if is_p1 and pos1:
                continue
            if not is_p1 and pos2:
                continue

        # Min gap check: don't re-enter same pair within 5 bars (25 min)
        last_entry = last_entry_time.get(pair)
        if last_entry is not None:
            minutes_since = (bt - last_entry).total_seconds() / 60
            if minutes_since < 30:
                continue

        # Find position in the individual pair's dataframe
        pair_idx = computed[pair].index.get_loc(bt)
        if isinstance(pair_idx, slice):
            pair_idx = pair_idx.start
        elif isinstance(pair_idx, np.ndarray):
            pair_idx = int(pair_idx[pair_idx.argmax()])

        if pair_idx < 100:
            continue

        # Signal
        signal = detect_signals(computed[pair], pair_idx, cfg)
        if signal['direction'] is None:
            continue

        result = simulate_trade(computed[pair], pair_idx, signal, cfg, pair)

        equity += result['pnl']
        if equity > peak:
            peak = equity

        day_trades += 1
        if result['pnl'] < 0:
            consec_loss += 1
            day_losses += 1
        else:
            consec_loss = 0

        if is_p1:
            pos1 = True if result['pnl'] >= 0 else False
        else:
            pos2 = True if result['pnl'] >= 0 else False

        last_entry_time[pair] = bt

        result.update({
            'entry_time': str(bt),
            'symbol': pair,
            'direction': signal['direction'],
            'entry_type': signal['entry_type'],
            'entry_price': round(row['close'], 3) if not pd.isna(row['close']) else 0,
            'adx': round(signal.get('adx', 0), 1),
            'equity_after': round(equity, 2),
        })
        trades.append(result)

    return summarize(trades, cfg, equity, '+'.join(syms))


# ─────────────────────────────────────────────────────────
# CONFIG GENERATION
# ─────────────────────────────────────────────────────────
def generate_configs() -> list[dict]:
    """Full parameter grid."""
    params = list(itertools.product(
        [2.0, 3.0],                       # risk_pct
        [0.8, 1.0, 1.2],                  # atr_sl_mult
        [0.8, 1.0, 1.2, 1.5, 2.0],        # rr_target
        [18, 20, 22],                      # adx_threshold
        [True, False],                     # use_di_cross
        [True, False],                     # use_rsi_filter
        [3, 4],                            # max_consecutive_losses
    ))
    configs = []
    for risk, atr, rr, adx, di_cross, rsi_f, consec in params:
        a = f"R{risk}-A{atr}-RR{rr}-ADX{adx}{'-XC' if di_cross else ''}{'-RSI' if rsi_f else ''}-C{consec}"
        a = a.replace('.', 'p')
        configs.append({
            'risk_pct': risk,
            'atr_sl_mult': atr,
            'rr_target': rr,
            'adx_threshold': adx,
            'use_di_cross': di_cross,
            'use_rsi_filter': rsi_f,
            'rsi_threshold': 50,
            'max_consecutive_losses': consec,
            'max_daily_loss_pct': 5.0,
            'max_total_dd_pct': 20.0,
            'max_trades_per_day': 15,
            'trail_atr_mult': 0.8,
            'initial_capital': 500.0,
            'abbrev': a,
        })
    return configs


def quick_configs() -> list[dict]:
    """Focused configs for fast testing."""
    base = {
        'risk_pct': 2.0,
        'atr_sl_mult': 1.0,
        'rr_target': 1.0,
        'adx_threshold': 20,
        'use_di_cross': False,
        'use_rsi_filter': False,
        'rsi_threshold': 50,
        'max_consecutive_losses': 3,
        'max_daily_loss_pct': 5.0,
        'max_total_dd_pct': 20.0,
        'max_trades_per_day': 15,
        'trail_atr_mult': 0.8,
        'initial_capital': 500.0,
        'abbrev': '',
    }

    variants = [
        # (risk, atr, rr, adx, di_cross, rsi, consec, abbrev)
        (2.0, 1.0, 1.0, 20, False, False, 3, "R2-A1.0-RR1.0-ADX20"),
        (2.0, 0.8, 1.0, 20, False, False, 3, "R2-A0.8-RR1.0-ADX20"),
        (2.0, 1.0, 0.8, 20, False, False, 3, "R2-A1.0-RR0.8-ADX20"),
        (2.0, 1.0, 1.2, 20, False, False, 3, "R2-A1.0-RR1.2-ADX20"),
        (2.0, 1.0, 1.5, 20, False, False, 3, "R2-A1.0-RR1.5-ADX20"),
        (2.0, 1.2, 1.0, 20, False, False, 3, "R2-A1.2-RR1.0-ADX20"),
        (2.0, 1.0, 1.0, 22, False, False, 3, "R2-A1.0-RR1.0-ADX22"),
        (2.0, 1.0, 1.0, 25, False, False, 3, "R2-A1.0-RR1.0-ADX25"),
        (2.0, 1.0, 1.0, 18, False, False, 3, "R2-A1.0-RR1.0-ADX18"),
        (2.0, 1.0, 1.0, 20, True, False, 3, "R2-A1.0-RR1.0-ADX20-XC"),
        (2.0, 1.0, 1.0, 20, False, True, 3, "R2-A1.0-RR1.0-ADX20-RSI"),
        (3.0, 1.0, 1.0, 20, False, False, 3, "R3-A1.0-RR1.0-ADX20"),
        (2.0, 1.0, 1.0, 20, False, False, 4, "R2-A1.0-RR1.0-ADX20-C4"),
        (2.0, 0.8, 1.2, 20, False, False, 3, "R2-A0.8-RR1.2-ADX20"),
        (3.0, 0.8, 1.0, 20, False, False, 4, "R3-A0.8-RR1.0-ADX20-C4"),
        (2.0, 0.8, 0.8, 18, False, False, 3, "R2-A0.8-RR0.8-ADX18"),
        (3.0, 0.8, 1.2, 22, False, False, 4, "R3-A0.8-RR1.2-ADX22-C4"),
    ]

    configs = []
    for risk, atr, rr, adx, di_cross, rsi_f, consec, abbrev in variants:
        c = dict(base)
        c.update({
            'risk_pct': risk,
            'atr_sl_mult': atr,
            'rr_target': rr,
            'adx_threshold': adx,
            'use_di_cross': di_cross,
            'use_rsi_filter': rsi_f,
            'max_consecutive_losses': consec,
            'abbrev': abbrev,
        })
        configs.append(c)
    return configs


# ─────────────────────────────────────────────────────────
# RESULTS DISPLAY
# ─────────────────────────────────────────────────────────
def fmt_label(cfg: dict) -> str:
    return cfg.get('abbrev', 'custom')

def print_results(results: list[dict]):
    valid = [r for r in results if r.get('error') is None and r['trades'] > 0]
    if not valid:
        print("  No results.")
        return

    valid.sort(key=lambda r: r['total_pnl'], reverse=True)

    print(f"\n{'='*130}")
    print(f"  M5 SCALPER BACKTEST RESULTS — Sorted by Total P&L")
    print(f"{'='*130}")
    hdr = f"  {'Config':<30s} {'Sym':<12s} {'Trades':>6s} {'TD':>4s} {'W%':>5s} "
    hdr += f"{'Tot$':>8s} {'Ret%':>6s} {'PF':>5s} {'MDD':>5s} "
    hdr += f"{'AvgW':>7s} {'AvgL':>7s} {'$/Mo':>7s} {'$/Tr':>6s}"
    print(hdr)
    print(f"  {'-'*30} {'-'*12} {'-'*6} {'-'*4} {'-'*5} "
          f"{'-'*8} {'-'*6} {'-'*5} {'-'*5} "
          f"{'-'*7} {'-'*7} {'-'*7} {'-'*6}")

    for r in valid:
        tpday = r.get('trades_per_day', 0)
        prof = r.get('profit_factor', 0)
        pnl_per_trade = r['total_pnl'] / max(r['trades'], 1)
        print(f"  {r.get('abbrev', r['symbol']):<30s} {r['symbol']:<12s} "
              f"{r['trades']:>6d} {tpday:>4.1f} {r['win_rate_pct']:>5.1f} "
              f"${r['total_pnl']:>+7.2f} {r['total_return_pct']:>+6.1f} "
              f"{prof:>5.2f} {r['max_drawdown_pct']:>5.1f} "
              f"${r['avg_win']:>+7.2f} ${r['avg_loss']:>+7.2f} "
              f"${r.get('projected_monthly', 0):>+7.2f} ${pnl_per_trade:>+6.2f}")

    # Top 5 by profit factor (minimum 10 trades)
    print(f"\n  Best by profit factor (min 10 trades):")
    top_pf = [r for r in valid if r['trades'] >= 10]
    top_pf.sort(key=lambda r: r['profit_factor'], reverse=True)
    for r in top_pf[:5]:
        print(f"    {r.get('abbrev', r['symbol']):<30s} {r['symbol']:<12s} "
              f"PF={r['profit_factor']:.2f}  Tot=${r['total_pnl']:+.2f}  "
              f"WR={r['win_rate_pct']:.1f}%  MDD={r['max_drawdown_pct']:.1f}%  "
              f"Trades={r['trades']}")

    # Top 5 by trades/day (scalper-friendly)
    print(f"\n  Most active (trades/day):")
    top_tpd = sorted(valid, key=lambda r: r.get('trades_per_day', 0), reverse=True)[:5]
    for r in top_tpd:
        print(f"    {r.get('abbrev', r['symbol']):<30s} {r['symbol']:<12s} "
              f"{r['trades_per_day']:.1f}/day  Tot=${r['total_pnl']:+.2f}  "
              f"WR={r['win_rate_pct']:.1f}%  MDD={r['max_drawdown_pct']:.1f}%")

    # Top 5 by projected monthly
    print(f"\n  Best projected monthly (min 10 trades):")
    top_mo = [r for r in valid if r['trades'] >= 10]
    top_mo.sort(key=lambda r: r.get('projected_monthly', -999), reverse=True)
    for r in top_mo[:5]:
        print(f"    {r.get('abbrev', r['symbol']):<30s} {r['symbol']:<12s} "
              f"${r.get('projected_monthly', 0):>+7.2f}/mo  "
              f"Tot=${r['total_pnl']:+.2f}  "
              f"WR={r['win_rate_pct']:.1f}%  MDD={r['max_drawdown_pct']:.1f}%  "
              f"{r['trades_per_day']:.1f}t/d")

    print()


def print_detailed(r: dict):
    print(f"\n{'='*60}")
    print(f"  {r['symbol']} | {r.get('abbrev', 'custom')}")
    print(f"  Risk: {r['risk_pct']}%  ATR SL: {r['atr_sl_mult']}  "
          f"RR: {r['rr_target']}  ADX: {r['adx_threshold']}  "
          f"DI-X: {r.get('use_di_cross', False)}  RSI: {r.get('use_rsi_filter', False)}")
    print(f"{'='*60}")
    print(f"  Trades:      {r['trades']} (W: {r['wins']} L: {r['losses']})")
    print(f"  Trades/day:  {r.get('trades_per_day', 0):.1f}")
    print(f"  Win rate:    {r['win_rate_pct']}%")
    print(f"  Total P&L:   ${r['total_pnl']:+.2f} ({r['total_return_pct']:+.1f}%)")
    print(f"  Final eq:    ${r['final_equity']:.2f}")
    print(f"  Profit fac:  {r['profit_factor']:.2f}")
    print(f"  Max DD:      {r['max_drawdown_pct']:.1f}%")
    print(f"  Avg bars held: {r.get('avg_bars_held', 0)}")
    print(f"  Avg Win:     ${r['avg_win']:+.2f}")
    print(f"  Avg Loss:    ${r['avg_loss']:+.2f}")
    print(f"  Proj Month:  ${r.get('projected_monthly', 0):+.2f}")
    print(f"  Sharpe:      {r.get('sharpe', 0):.2f}")
    print(f"  Exit: SL={r['sl_count']} Partial={r['partial_count']} "
          f"FullTP={r['full_tp_count']} Trail={r['trail_exit_count']}")

    if r.get('monthly_map'):
        print(f"\n  Monthly:")
        for m in sorted(r['monthly_map'].keys()):
            v = r['monthly_map'][m]
            print(f"    {m}: ${v:+.2f}")


# ─────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────
def main():
    import argparse
    ap = argparse.ArgumentParser(description="M5 Scalper Backtest")
    ap.add_argument("--quick", action="store_true", help="Focused config set")
    ap.add_argument("--pair", type=str, default=None,
                    help="Single pair: EURJPY, GBPJPY, or XAUUSD")
    ap.add_argument("--detailed", type=str, default=None,
                    help="Show detailed results for matching config")
    ap.add_argument("--bars", type=int, default=3000, help="M5 bars to fetch")
    ap.add_argument("--combined", action="store_true", help="Test EURJPY+XAUUSD combined")
    args = ap.parse_args()

    if not HAS_MT5:
        print("[X] Requires MetaTrader5 package")
        sys.exit(1)

    # Determine which instruments
    if args.pair:
        symbols = [args.pair.upper()]
    else:
        symbols = ['EURJPY', 'GBPJPY', 'XAUUSD']

    # Fetch data
    print("\n[IN] Fetching M5 data from MT5...")
    data = {}
    for sym in symbols:
        print(f"   {sym}...")
        df = fetch_m5(sym, bars=args.bars)
        if df is not None and len(df) > 500:
            data[sym] = df
            print(f"   -> {len(df)} bars  ({df.index[0].date()} -> {df.index[-1].date()})")
        else:
            print(f"   [X] No data for {sym}")

    if not data:
        print("[X] No data available")
        sys.exit(1)

    # Generate configs
    if args.quick:
        configs = quick_configs()
    else:
        configs = generate_configs()
    print(f"\n[OK] Testing {len(configs)} configs on {len(data)} instrument(s)...")

    # Run backtests
    results = []
    for cfg in configs:
        for sym, df in data.items():
            r = run_backtest(df, cfg, sym)
            r['abbrev'] = cfg['abbrev']
            results.append(r)

    # Combined (EURJPY + XAUUSD) — only when explicitly requested
    if args.combined:
        if 'EURJPY' in data and 'XAUUSD' in data:
            print(f"\n[..] Running EURJPY+XAUUSD combined ({len(configs)} configs)...")
            combined_data = {'EURJPY': data['EURJPY'], 'XAUUSD': data['XAUUSD']}
            for cfg in configs:
                r = run_combined(combined_data, cfg)
                r['abbrev'] = cfg['abbrev']
                results.append(r)

    # Print results
    print_results(results)

    # Filter to actionable (min 10 trades, PF > 1.0)
    actionable = [r for r in results
                  if r.get('error') is None and r['trades'] >= 10
                  and r['profit_factor'] > 1.0]
    if actionable:
        actionable.sort(key=lambda r: r['total_pnl'], reverse=True)
        print(f"{'='*60}")
        print(f"  ACTIONABLE PROFITABLE CONFIGS ({len(actionable)} found)")
        print(f"{'='*60}")
        for r in actionable[:10]:
            print(f"    {r.get('abbrev', 'custom'):<30s} {r['symbol']:<12s} "
                  f"${r['projected_monthly']:>+7.2f}/mo  "
                  f"WR={r['win_rate_pct']:.1f}%  PF={r['profit_factor']:.2f}  "
                  f"MDD={r['max_drawdown_pct']:.1f}%  {r['trades_per_day']:.1f}t/d")

    # Summary recommendation
    if actionable:
        best = max(actionable, key=lambda r: r.get('projected_monthly', 0))
        print(f"\n{'='*60}")
        print(f"  BEST CONFIG for $250-350/month target:")
        print(f"{'='*60}")
        print(f"  {best.get('abbrev', best['symbol'])} on {best['symbol']}")
        print(f"  Risk: {best['risk_pct']}%  ATR: {best['atr_sl_mult']}  "
              f"RR: {best['rr_target']}  ADX: {best['adx_threshold']}")
        print(f"  Trades/day: {best['trades_per_day']}  WR: {best['win_rate_pct']}%")
        print(f"  Proj Monthly: ${best['projected_monthly']:.2f}")
        print(f"  On $500 = {best['projected_monthly']/500*100:.1f}%/month")

        # Show top 3 combined
        combined_results = [r for r in actionable if '+' in r['symbol']]
        if combined_results:
            combined_results.sort(key=lambda r: r.get('projected_monthly', 0), reverse=True)
            best_c = combined_results[0]
            print(f"\n  BEST COMBINED:")
            print(f"  {best_c.get('abbrev', 'custom')} on {best_c['symbol']}")
            print(f"  Proj Monthly: ${best_c['projected_monthly']:.2f}")
            print(f"  WR: {best_c['win_rate_pct']}%  PF: {best_c['profit_factor']:.2f}  "
                  f"MDD: {best_c['max_drawdown_pct']}%")
    else:
        print("\n[!] No profitable configs with >= 10 trades found.")

    # Save
    out_path = HERE / "m5_scalper_results.json"
    safe = []
    for r in results:
        sr = {k: v for k, v in r.items() if k != 'monthly_map'}
        if 'monthly_map' in r:
            sr['monthly_map'] = {str(k): round(v, 2) for k, v in r['monthly_map'].items()}
        safe.append(sr)
    out_path.write_text(json.dumps(safe, indent=2))
    print(f"\n[SAVE] Results saved to {out_path}")


if __name__ == "__main__":
    main()
