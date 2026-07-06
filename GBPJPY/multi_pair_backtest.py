#!/usr/bin/env python3
"""
Multi-Pair Strategy Backtest Engine
====================================
Tests GBPJPY + EURJPY EMA/RSI strategy across multiple parameter configs.
Tests each pair individually and both combined with shared risk.

Usage:
    python multi_pair_backtest.py                        # Full sweep
    python multi_pair_backtest.py --quick                # Quick sweep (key configs only)
    python multi_pair_backtest.py --single GBPJPY        # Single pair sweep
    python multi_pair_backtest.py --news-off             # Skip news filter
    python multi_pair_backtest.py --help                 # Help
"""
import sys, os, json, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dataclasses import dataclass, field, asdict
import itertools

# Windows cp1252 safe printing
_OK = "[OK]"
_FAIL = "[FAIL]"
_ARROW = "->"
_WARN = "[!]"
_BULLET = " *"

HERE = Path(__file__).parent.resolve()

# ── Optional MT5 ──
try:
    import MetaTrader5 as mt5
    HAS_MT5 = True
except ImportError:
    HAS_MT5 = False

import pandas as pd
import numpy as np

# ─────────────────────────────────────────────────────────
# CONFIG TEMPLATES
# ─────────────────────────────────────────────────────────
@dataclass
class StrategyConfig:
    """All tunable parameters for one backtest run."""
    name: str = "default"
    risk_pct: float = 1.0
    atr_sl_mult: float = 1.5
    rr_target: float = 2.0
    rsi_period: int = 7
    rsi_threshold: int = 30
    fast_ema: int = 20
    slow_ema: int = 50
    atr_period: int = 14
    min_stop_pips: float = 20.0
    partial_tp_pct: float = 50.0     # % closed at 1:1
    trail_atr_mult: float = 0.75
    max_consecutive_losses: int = 2
    max_daily_loss_pct: float = 5.0
    max_total_dd_pct: float = 20.0
    session_start: int = 8
    session_end: int = 15
    use_news_filter: bool = True
    commission_per_lot: float = 0.06
    bars_lookback: int = 50          # max bars to walk forward after entry
    initial_capital: float = 500.0

    def copy_with(self, **kwargs):
        d = asdict(self)
        d.update(kwargs)
        return StrategyConfig(**d)


# ─────────────────────────────────────────────────────────
# DATA FETCHING
# ─────────────────────────────────────────────────────────
def resolve_symbol(base: str) -> str | None:
    """Try common symbol suffixes to find a tradable one."""
    candidates = [base, base + "+", base + "-", base + ".c", base + "c", base + ".f"]
    for c in candidates:
        info = mt5.symbol_info(c)
        if info:
            tick = mt5.symbol_info_tick(c)
            if tick and tick.bid > 0:
                return c
    return None


def fetch_data(symbol: str, bars: int = 3000) -> pd.DataFrame | None:
    """Download M15 data from MT5. Auto-detects symbol suffix."""
    if not HAS_MT5:
        print(f"   [!] MetaTrader5 not installed -- skipping {symbol}")
        return None

    if not mt5.initialize():
        print(f"   [X] MT5 init failed: {mt5.last_error()}")
        return None

    resolved = resolve_symbol(symbol)
    if not resolved:
        print(f"   [X] {symbol}: no variant found in Market Watch")
        mt5.shutdown()
        return None

    if resolved != symbol:
        print(f"      Resolved: {resolved}")

    mt5.symbol_select(resolved, True)
    rates = mt5.copy_rates_from_pos(resolved, mt5.TIMEFRAME_M15, 0, bars)
    if rates is None or len(rates) < 200:
        print(f"   [X] {resolved}: only {len(rates) if rates else 0} bars")
        mt5.shutdown()
        return None

    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('time', inplace=True)
    return df


# ─────────────────────────────────────────────────────────
# STRATEGY — signal detection (same logic as the EA)
# ─────────────────────────────────────────────────────────
def compute_strategy(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    """Add indicator columns to dataframe. Returns new DataFrame."""
    d = df.copy()
    close = d['close'].values
    high = d['high'].values
    low = d['low'].values

    # EMAs
    d['ema_fast'] = pd.Series(close).ewm(span=cfg.fast_ema).mean().values
    d['ema_slow'] = pd.Series(close).ewm(span=cfg.slow_ema).mean().values

    # RSI
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_g = pd.Series(gain).ewm(span=cfg.rsi_period).mean().values
    avg_l = pd.Series(loss).ewm(span=cfg.rsi_period).mean().values
    rs = avg_g / np.maximum(avg_l, 0.001)
    d['rsi'] = 100 - (100 / (1 + rs))

    # ATR
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(abs(high[1:] - close[:-1]), abs(low[1:] - close[:-1]))
    )
    atr_vals = np.concatenate([
        [np.mean(tr[:cfg.atr_period])],
        pd.Series(tr).ewm(span=cfg.atr_period).mean().values
    ])
    if len(atr_vals) < len(close):
        atr_vals = np.concatenate([atr_vals, [atr_vals[-1]] * (len(close) - len(atr_vals))])
    d['atr'] = atr_vals

    # Signals
    d['uptrend'] = d['ema_fast'] > d['ema_slow']
    d['downtrend'] = d['ema_fast'] < d['ema_slow']
    d['rsi_cross_up'] = (d['rsi'].shift(1) <= cfg.rsi_threshold) & (d['rsi'] > cfg.rsi_threshold)
    ob = 100 - cfg.rsi_threshold
    d['rsi_cross_down'] = (d['rsi'].shift(1) >= ob) & (d['rsi'] < ob)

    d['buy_signal'] = d['uptrend'] & d['rsi_cross_up']
    d['sell_signal'] = d['downtrend'] & d['rsi_cross_down']

    return d


# ─────────────────────────────────────────────────────────
# CORE — single-trade simulation
# ─────────────────────────────────────────────────────────
def simulate_trade(df: pd.DataFrame, entry_idx: int, direction: str,
                   cfg: StrategyConfig) -> dict:
    """Walk forward from entry_idx and determine outcome.

    Returns dict with keys:
        pnl, exit_reason, exit_idx, hit_partial, bars_held
    """
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    atr = df['atr'].values
    entry_price = close[entry_idx]

    # Price levels
    sl_pips = max(atr[entry_idx] * cfg.atr_sl_mult / 0.01, cfg.min_stop_pips)
    tp_pips = sl_pips * cfg.rr_target
    sl_price = entry_price - sl_pips * 0.01 if direction == 'long' else entry_price + sl_pips * 0.01
    tp1_price = entry_price + sl_pips * 0.01 if direction == 'long' else entry_price - sl_pips * 0.01
    tp2_price = entry_price + tp_pips * 0.01 if direction == 'long' else entry_price - tp_pips * 0.01

    # Pip value per cent lot
    # For JPY pairs: pip_value $ = (pip_in_price = 0.01) × (contract_size = 1000) / price_conversion
    # Simplified: 0.01 * (1000 / entry_price) is the pip value in $
    pip_val = 0.01 * (1000.0 / entry_price)
    lots = max(1, int(cfg.initial_capital * cfg.risk_pct / 100.0 / (sl_pips * pip_val)))
    half_lots = lots * 0.5
    commission = lots * cfg.commission_per_lot

    partial_hit = False
    trail_stop = None
    lookback = min(cfg.bars_lookback, len(df) - entry_idx - 2)

    for j in range(1, lookback + 1):
        idx = entry_idx + j
        h = high[idx]
        l = low[idx]
        c = close[idx]
        a = atr[idx]

        if direction == 'long':
            # SL
            if l <= sl_price:
                pnl = -abs(sl_price - entry_price) / 0.01 * pip_val * lots
                return {
                    'pnl': round(pnl - commission, 2),
                    'exit_reason': 'SL',
                    'exit_idx': idx,
                    'hit_partial': partial_hit,
                    'bars_held': j,
                    'lots': lots,
                    'sl_pips': round(sl_pips, 1),
                    'tp_pips': round(tp_pips, 1),
                }

            # Full TP (check before partial, in case of gap)
            if h >= tp2_price and not partial_hit:
                pnl = abs(tp2_price - entry_price) / 0.01 * pip_val * lots
                return {
                    'pnl': round(pnl - commission, 2),
                    'exit_reason': 'FULL TP',
                    'exit_idx': idx,
                    'hit_partial': False,
                    'bars_held': j,
                    'lots': lots,
                    'sl_pips': round(sl_pips, 1),
                    'tp_pips': round(tp_pips, 1),
                }

            # Partial TP at 1:1
            if not partial_hit and h >= tp1_price:
                # Close 50%
                pnl_1 = abs(tp1_price - entry_price) / 0.01 * pip_val * half_lots
                # Move remaining to breakeven + small buffer
                sl_price = entry_price + 0.3 * 0.01  # 0.3 pip buffer
                trail_stop = sl_price
                partial_hit = True

                # Did full TP also happen this bar?
                if h >= tp2_price:
                    pnl_2 = abs(tp2_price - entry_price) / 0.01 * pip_val * half_lots
                    return {
                        'pnl': round(pnl_1 + pnl_2 - commission, 2),
                        'exit_reason': 'PARTIAL+TP2',
                        'exit_idx': idx,
                        'hit_partial': True,
                        'bars_held': j,
                        'lots': lots,
                        'sl_pips': round(sl_pips, 1),
                        'tp_pips': round(tp_pips, 1),
                    }

            # After partial: trail
            if partial_hit:
                trail_dist = a * cfg.trail_atr_mult
                new_stop = c - trail_dist
                if trail_stop is None or new_stop > trail_stop:
                    trail_stop = new_stop
                if l <= trail_stop:
                    pnl_2 = abs(trail_stop - entry_price) / 0.01 * pip_val * half_lots
                    return {
                        'pnl': round(pnl_1 - commission + pnl_2, 2),
                        'exit_reason': 'PARTIAL+TRAIL',
                        'exit_idx': idx,
                        'hit_partial': True,
                        'bars_held': j,
                        'lots': lots,
                        'sl_pips': round(sl_pips, 1),
                        'tp_pips': round(tp_pips, 1),
                    }
                # Or hit TP2
                if h >= tp2_price:
                    pnl_2 = abs(tp2_price - entry_price) / 0.01 * pip_val * half_lots
                    return {
                        'pnl': round(pnl_1 - commission + pnl_2, 2),
                        'exit_reason': 'PARTIAL+TP2',
                        'exit_idx': idx,
                        'hit_partial': True,
                        'bars_held': j,
                        'lots': lots,
                        'sl_pips': round(sl_pips, 1),
                        'tp_pips': round(tp_pips, 1),
                    }

        else:  # short
            if h >= sl_price:
                pnl = -abs(entry_price - sl_price) / 0.01 * pip_val * lots
                return {
                    'pnl': round(pnl - commission, 2),
                    'exit_reason': 'SL',
                    'exit_idx': idx,
                    'hit_partial': partial_hit,
                    'bars_held': j,
                    'lots': lots,
                    'sl_pips': round(sl_pips, 1),
                    'tp_pips': round(tp_pips, 1),
                }

            if l <= tp2_price and not partial_hit:
                pnl = abs(entry_price - tp2_price) / 0.01 * pip_val * lots
                return {
                    'pnl': round(pnl - commission, 2),
                    'exit_reason': 'FULL TP',
                    'exit_idx': idx,
                    'hit_partial': False,
                    'bars_held': j,
                    'lots': lots,
                    'sl_pips': round(sl_pips, 1),
                    'tp_pips': round(tp_pips, 1),
                }

            if not partial_hit and l <= tp1_price:
                pnl_1 = abs(entry_price - tp1_price) / 0.01 * pip_val * half_lots
                sl_price = entry_price - 0.3 * 0.01
                trail_stop = sl_price
                partial_hit = True
                if l <= tp2_price:
                    pnl_2 = abs(entry_price - tp2_price) / 0.01 * pip_val * half_lots
                    return {
                        'pnl': round(pnl_1 + pnl_2 - commission, 2),
                        'exit_reason': 'PARTIAL+TP2',
                        'exit_idx': idx,
                        'hit_partial': True,
                        'bars_held': j,
                        'lots': lots,
                        'sl_pips': round(sl_pips, 1),
                        'tp_pips': round(tp_pips, 1),
                    }

            if partial_hit:
                trail_dist = a * cfg.trail_atr_mult
                new_stop = c + trail_dist
                if trail_stop is None or new_stop < trail_stop:
                    trail_stop = new_stop
                if h >= trail_stop:
                    pnl_2 = abs(entry_price - trail_stop) / 0.01 * pip_val * half_lots
                    return {
                        'pnl': round(pnl_1 - commission + pnl_2, 2),
                        'exit_reason': 'PARTIAL+TRAIL',
                        'exit_idx': idx,
                        'hit_partial': True,
                        'bars_held': j,
                        'lots': lots,
                        'sl_pips': round(sl_pips, 1),
                        'tp_pips': round(tp_pips, 1),
                    }
                if l <= tp2_price:
                    pnl_2 = abs(entry_price - tp2_price) / 0.01 * pip_val * half_lots
                    return {
                        'pnl': round(pnl_1 - commission + pnl_2, 2),
                        'exit_reason': 'PARTIAL+TP2',
                        'exit_idx': idx,
                        'hit_partial': True,
                        'bars_held': j,
                        'lots': lots,
                        'sl_pips': round(sl_pips, 1),
                        'tp_pips': round(tp_pips, 1),
                    }

    # Trade not resolved within lookback — estimate final P&L at last bar
    last_price = close[entry_idx + lookback]
    if direction == 'long':
        gross = (last_price - entry_price) / 0.01 * pip_val * lots
    else:
        gross = (entry_price - last_price) / 0.01 * pip_val * lots
    return {
        'pnl': round(gross - commission, 2),
        'exit_reason': 'TIMEOUT',
        'exit_idx': entry_idx + lookback,
        'hit_partial': partial_hit,
        'bars_held': lookback,
        'lots': lots,
        'sl_pips': round(sl_pips, 1),
        'tp_pips': round(tp_pips, 1),
    }


# ─────────────────────────────────────────────────────────
# NEWS FILTER (simplified schedule-based for backtest)
# ─────────────────────────────────────────────────────────
NEWS_CALENDAR = [
    # UK: 07:00, 12:00 GMT
    (7, 0),
    (12, 0),
    # US: 13:30, 14:00, 15:00 GMT
    (13, 30),
    (14, 0),
    (15, 0),
]

def in_news_blackout(bar_time: datetime, cfg: StrategyConfig) -> bool:
    if not cfg.use_news_filter:
        return False
    h, m = bar_time.hour, bar_time.minute
    bar_min = h * 60 + m
    for ev_h, ev_m in NEWS_CALENDAR:
        ev_min = ev_h * 60 + ev_m
        start = ev_min - 45  # 45 min before
        end = ev_min + 15    # 15 min after
        if start <= bar_min <= end:
            return True
    return False


# ─────────────────────────────────────────────────────────
# SINGLE-PAIR BACKTEST
# ─────────────────────────────────────────────────────────
def run_backtest_single(df_raw: pd.DataFrame, cfg: StrategyConfig,
                        symbol: str) -> dict:
    """Run backtest on one pair. Returns trade list + metrics."""
    df = compute_strategy(df_raw, cfg)
    close = df['close'].values

    # Warmup: need enough bars for indicators
    start_idx = max(cfg.slow_ema + cfg.atr_period + 10, 60)

    equity = cfg.initial_capital
    peak = equity
    trades = []
    consec_loss = 0
    day_trades = 0
    day_losses = 0
    last_day = -1
    day_start_eq = equity
    circuit_breaker = False

    for i in range(start_idx, len(df)):
        bt = df.index[i]

        # New day reset
        if bt.day != last_day:
            last_day = bt.day
            day_trades = 0
            day_losses = 0
            day_start_eq = equity
            circuit_breaker = False

        if circuit_breaker:
            continue

        # Session filter
        if not (cfg.session_start <= bt.hour < cfg.session_end):
            continue

        # News filter
        if in_news_blackout(bt, cfg):
            continue

        # Circuit breakers
        if day_losses > 0:
            daily_loss_pct = (day_start_eq - equity) / max(day_start_eq, 1) * 100
            if daily_loss_pct >= cfg.max_daily_loss_pct:
                circuit_breaker = True
                continue

        dd_pct = (peak - equity) / max(peak, 1) * 100
        if dd_pct >= cfg.max_total_dd_pct:
            circuit_breaker = True
            continue

        if consec_loss >= cfg.max_consecutive_losses:
            circuit_breaker = True
            continue

        if day_trades >= 8:  # hard cap per day
            continue

        # Check signal
        direction = None
        if df['buy_signal'].iloc[i]:
            direction = 'long'
        elif df['sell_signal'].iloc[i]:
            direction = 'short'

        if direction is None:
            continue

        # Simulate trade
        result = simulate_trade(df, i, direction, cfg)

        # Update equity
        equity += result['pnl']
        if equity > peak:
            peak = equity

        # Update breaker state
        day_trades += 1
        if result['pnl'] < 0:
            consec_loss += 1
            day_losses += 1
        else:
            consec_loss = 0

        result.update({
            'entry_time': str(bt),
            'direction': direction,
            'entry_price': round(close[i], 3),
            'equity_after': round(equity, 2),
        })
        trades.append(result)

    # Compute metrics
    return _compute_metrics(trades, cfg, equity, symbol)


def _compute_metrics(trades: list, cfg: StrategyConfig, final_eq: float,
                     symbol: str) -> dict:
    """Compute summary metrics from trade list."""
    if not trades:
        return {'symbol': symbol, 'trades': 0, 'error': 'no trades'}

    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    total_pnl = sum(t['pnl'] for t in trades)
    gross_win = sum(t['pnl'] for t in wins) if wins else 0
    gross_loss = abs(sum(t['pnl'] for t in losses)) if losses else 0

    win_rate = len(wins) / len(trades) * 100 if trades else 0
    avg_win = np.mean([t['pnl'] for t in wins]) if wins else 0
    avg_loss = np.mean([t['pnl'] for t in losses]) if losses else 0
    profit_factor = gross_win / max(gross_loss, 0.01)

    # Track drawdown
    eq = cfg.initial_capital
    peak = eq
    max_dd = 0
    for t in trades:
        eq += t['pnl']
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # Monthly breakdown
    monthly = {}
    for t in trades:
        month_key = t['entry_time'][:7]
        monthly.setdefault(month_key, 0)
        monthly[month_key] += t['pnl']

    avg_monthly = np.mean(list(monthly.values())) if monthly else 0

    # Partial TP stats
    partial_count = sum(1 for t in trades if t.get('hit_partial'))
    sl_count = sum(1 for t in trades if t['exit_reason'] == 'SL')
    full_tp_count = sum(1 for t in trades if t['exit_reason'] in ('FULL TP', 'PARTIAL+TP2'))
    trail_exit = sum(1 for t in trades if t['exit_reason'] == 'PARTIAL+TRAIL')

    return {
        'symbol': symbol,
        'config_name': cfg.name,
        'risk_pct': cfg.risk_pct,
        'atr_sl_mult': cfg.atr_sl_mult,
        'rsi_th': cfg.rsi_threshold,
        'max_consec': cfg.max_consecutive_losses,
        'trades': len(trades),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate_pct': round(win_rate, 1),
        'total_pnl': round(total_pnl, 2),
        'total_return_pct': round(total_pnl / cfg.initial_capital * 100, 1),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'profit_factor': round(profit_factor, 2),
        'max_drawdown_pct': round(max_dd, 1),
        'avg_monthly': round(avg_monthly, 2),
        'monthly_months': len(monthly),
        'partial_count': partial_count,
        'sl_count': sl_count,
        'full_tp_count': full_tp_count,
        'trail_exit_count': trail_exit,
        'final_equity': round(final_eq, 2),
        'return_per_trade': round(total_pnl / max(len(trades), 1), 2),
        'monthly_map': monthly,
        'pos_win_rate': round(len([m for m in monthly.values() if m > 0]) / max(len(monthly), 1) * 100, 0),
    }


# ─────────────────────────────────────────────────────────
# DUAL-PAIR BACKTEST (shared equity + circuit breakers)
# ─────────────────────────────────────────────────────────
def run_backtest_dual(df1_raw: pd.DataFrame, df2_raw: pd.DataFrame,
                       cfg: StrategyConfig, sym1: str, sym2: str) -> dict:
    """Run backtest on two pairs sharing one equity pool.

    Processes bars interleaved by timestamp so both pairs
    are evaluated chronologically.
    """
    d1 = compute_strategy(df1_raw, cfg)
    d2 = compute_strategy(df2_raw, cfg)

    # Merge both dataframes by time index
    d1['_pair'] = sym1
    d2['_pair'] = sym2
    combined = pd.concat([d1, d2]).sort_index()

    equity = cfg.initial_capital
    peak = equity
    trades = []
    consec_loss = 0
    day_trades = 0
    day_losses = 0
    last_day = -1
    day_start_eq = equity
    circuit_breaker = False

    # Track positions per pair
    pos_pair1 = False  # currently in a trade on pair 1
    pos_pair2 = False

    start_idx = max(cfg.slow_ema + cfg.atr_period + 10, 60)

    for i in range(start_idx, len(combined)):
        row = combined.iloc[i]
        bt = combined.index[i]
        is_p1 = row['_pair'] == sym1

        # New day reset
        if bt.day != last_day:
            last_day = bt.day
            day_trades = 0
            day_losses = 0
            day_start_eq = equity
            circuit_breaker = False

        if circuit_breaker:
            continue

        # Session
        if not (cfg.session_start <= bt.hour < cfg.session_end):
            continue

        # News
        if in_news_blackout(bt, cfg):
            continue

        # Daily loss breaker
        if day_losses > 0:
            dlp = (day_start_eq - equity) / max(day_start_eq, 1) * 100
            if dlp >= cfg.max_daily_loss_pct:
                circuit_breaker = True
                continue

        # DD breaker
        dd_pct = (peak - equity) / max(peak, 1) * 100
        if dd_pct >= cfg.max_total_dd_pct:
            circuit_breaker = True
            continue

        # Consecutive loss breaker
        if consec_loss >= cfg.max_consecutive_losses:
            circuit_breaker = True
            continue

        # Daily trade cap (total across both pairs)
        if day_trades >= 12:
            continue

        if is_p1 and pos_pair1:
            continue
        if not is_p1 and pos_pair2:
            continue

        # Check signal for this pair at this bar
        direction = None
        if row.get('buy_signal'):
            direction = 'long'
        elif row.get('sell_signal'):
            direction = 'short'
        if direction is None:
            continue

        # Find index in the computed pair dataframe
        orig_df = d1 if is_p1 else d2
        try:
            orig_idx = orig_df.index.get_loc(bt)
        except KeyError:
            continue
        if isinstance(orig_idx, slice):
            orig_idx = orig_idx.start
        elif isinstance(orig_idx, np.ndarray):
            orig_idx = orig_idx[0]

        result = simulate_trade(orig_df, orig_idx, direction, cfg)

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
            pos_pair1 = True
        else:
            pos_pair2 = True

        result.update({
            'entry_time': str(bt),
            'symbol': sym1 if is_p1 else sym2,
            'direction': direction,
            'entry_price': round(row['close'], 3) if not pd.isna(row['close']) else 0,
            'equity_after': round(equity, 2),
        })
        trades.append(result)

    metrics = _compute_metrics(trades, cfg, equity, f"{sym1}+{sym2}")
    return metrics


# ─────────────────────────────────────────────────────────
# PARAMETER SWEEPER
# ─────────────────────────────────────────────────────────
def generate_configs() -> list[StrategyConfig]:
    """Generate parameter combinations to test."""
    # Base config
    base = StrategyConfig()

    risk_levels = [1.0, 1.5, 2.0, 2.5, 3.0]
    atr_mults = [1.0, 1.2, 1.5, 2.0]
    rsi_ths = [25, 30, 35]
    consecs = [2, 3, 4]

    configs = []

    # Generate descriptive names
    for r, a, rs, c in itertools.product(risk_levels, atr_mults, rsi_ths, consecs):
        name = f"R{r}-A{a}-RSI{rs}-C{c}".replace(".", "p")
        configs.append(base.copy_with(
            name=name, risk_pct=r, atr_sl_mult=a,
            rsi_threshold=rs, max_consecutive_losses=c
        ))

    return configs


def quick_configs() -> list[StrategyConfig]:
    """Smaller set for --quick mode."""
    base = StrategyConfig()

    params = [
        # (risk, atr, rsi, consec)
        (1.0, 1.5, 30, 2),   # baseline
        (1.5, 1.5, 30, 2),
        (2.0, 1.5, 30, 2),
        (2.0, 1.2, 30, 3),
        (2.0, 1.0, 30, 3),
        (2.0, 1.5, 30, 3),
        (2.0, 1.5, 25, 3),
        (2.0, 1.5, 35, 3),
        (2.5, 1.2, 30, 3),
        (3.0, 1.0, 30, 4),
        (3.0, 1.2, 30, 4),
        (1.5, 1.0, 30, 2),
    ]

    configs = []
    for r, a, rs, c in params:
        name = f"R{r}-A{a}-RSI{rs}-C{c}".replace(".", "p")
        configs.append(base.copy_with(
            name=name, risk_pct=r, atr_sl_mult=a,
            rsi_threshold=rs, max_consecutive_losses=c
        ))
    return configs


# ─────────────────────────────────────────────────────────
# RESULTS DISPLAY
# ─────────────────────────────────────────────────────────
def print_results_table(results: list[dict]):
    """Print formatted results table."""
    # Filter out errors
    results = [r for r in results if r.get('error') is None and r['trades'] > 0]

    if not results:
        print("  No results to display.")
        return

    # Sort by total_pnl descending
    results.sort(key=lambda r: r['total_pnl'], reverse=True)

    print(f"\n{'='*110}")
    print(f"  BACKTEST RESULTS — Sorted by Total P&L")
    print(f"{'='*110}")
    print(f"  {'Config':<20s} {'Sym':<10s} {'Trades':>7s} {'W%':>5s} "
          f"{'Tot$':>8s} {'Ret%':>6s} {'PF':>5s} {'MDD%':>6s} "
          f"{'AvgW':>7s} {'AvgL':>7s} {'AvgMo':>7s}")
    print(f"  {'-'*20} {'-'*10} {'-'*7} {'-'*5} {'-'*8} {'-'*6} "
          f"{'-'*5} {'-'*6} {'-'*7} {'-'*7} {'-'*7}")

    for r in results:
        print(f"  {r['config_name']:<20s} {r['symbol']:<10s} "
              f"{r['trades']:>7d} {r['win_rate_pct']:>5.1f} "
              f"${r['total_pnl']:>+7.2f} {r['total_return_pct']:>+6.1f} "
              f"{r['profit_factor']:>5.2f} {r['max_drawdown_pct']:>6.1f} "
              f"${r['avg_win']:>+7.2f} ${r['avg_loss']:>+7.2f} ${r['avg_monthly']:>+7.2f}")

    print(f"\n  Best by profit factor:")
    best_pf = sorted(results, key=lambda r: r['profit_factor'], reverse=True)[:5]
    for r in best_pf:
        print(f"    {r['config_name']:<20s} {r['symbol']:<10s} "
              f"PF={r['profit_factor']:.2f}  Tot=${r['total_pnl']:+.2f}  "
              f"MDD={r['max_drawdown_pct']:.1f}%  Trades={r['trades']}")

    print(f"\n  Best by return/drawdown ratio:")
    best_rdd = sorted(results, key=lambda r: abs(r['total_return_pct']) / max(abs(r['max_drawdown_pct']), 0.1) if abs(r['max_drawdown_pct']) > 0.1 else 0, reverse=True)[:5]
    for r in best_rdd:
        ratio = abs(r['total_return_pct']) / max(abs(r['max_drawdown_pct']), 0.1)
        print(f"    {r['config_name']:<20s} {r['symbol']:<10s} "
              f"R/DD={ratio:.2f}  Ret={r['total_return_pct']:+.1f}%  "
              f"MDD={r['max_drawdown_pct']:.1f}%  PF={r['profit_factor']:.2f}")

    print()


def print_detailed(result: dict):
    """Print detailed breakdown for one config."""
    print(f"\n{'='*60}")
    print(f"  {result['symbol']} | {result['config_name']}")
    print(f"  Risk: {result['risk_pct']}%  ATR: {result['atr_sl_mult']}  "
          f"RSI: {result['rsi_th']}  Consec: {result['max_consec']}")
    print(f"{'='*60}")
    print(f"  Trades:      {result['trades']} (W: {result['wins']} L: {result['losses']})")
    print(f"  Win rate:    {result['win_rate_pct']}%")
    print(f"  Total P&L:   ${result['total_pnl']:+.2f} ({result['total_return_pct']:+.1f}%)")
    print(f"  Final eq:    ${result['final_equity']:.2f}")
    print(f"  Profit fac:  {result['profit_factor']:.2f}")
    print(f"  Max DD:      {result['max_drawdown_pct']:.1f}%")
    print(f"  Avg Win:     ${result['avg_win']:+.2f}")
    print(f"  Avg Loss:    ${result['avg_loss']:+.2f}")
    print(f"  Avg Monthly: ${result['avg_monthly']:+.2f} ({result['monthly_months']} months)")
    print(f"  Pos months:  {result['pos_win_rate']:.0f}%")
    print(f"  Exit breakdown: SL={result['sl_count']} Partial={result['partial_count']} "
          f"FullTP={result['full_tp_count']} Trail={result['trail_exit_count']}")

    # Monthly breakdown
    if result.get('monthly_map'):
        print(f"\n  Monthly P&L:")
        for month in sorted(result['monthly_map'].keys()):
            val = result['monthly_map'][month]
            marker = "🟢" if val > 0 else "🔴"
            print(f"    {month}: {marker} ${val:+.2f}")


# ─────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────
def main():
    import argparse
    ap = argparse.ArgumentParser(description="Multi-Pair Strategy Backtest")
    ap.add_argument("--quick", action="store_true", help="Quick test (subset of configs)")
    ap.add_argument("--single", type=str, default=None,
                    help="Test one pair only: GBPJPY or EURJPY")
    ap.add_argument("--news-off", action="store_true", help="Disable news filter")
    ap.add_argument("--detailed", type=str, default=None,
                    help="Show detailed results for a config name match")
    ap.add_argument("--bars", type=int, default=3000,
                    help="Number of M15 bars to fetch (default 3000)")
    args = ap.parse_args()

    if not HAS_MT5:
        print("❌ Requires MetaTrader5 Python package.")
        print("   pip install MetaTrader5")
        sys.exit(1)

    # ── Fetch data ──
    print("\n📥 Fetching M15 data from MT5...")
    syms = ['GBPJPY', 'EURJPY']
    if args.single:
        syms = [args.single.upper()]

    data = {}
    for sym in syms:
        print(f"   {sym}...")
        df = fetch_data(sym, bars=args.bars)
        if df is not None:
            data[sym] = df
            print(f"      {len(df)} bars  ({df.index[0].date()} → {df.index[-1].date()})")
        else:
            print(f"   ❌ Could not fetch {sym}")

    if not data:
        print("\n❌ No data available. Check MT5 connection and symbol names.")
        sys.exit(1)

    # ── Generate configs ──
    if args.quick:
        configs = quick_configs()
    else:
        configs = generate_configs()
    print(f"\n⚙️  Testing {len(configs)} configurations...")

    if args.news_off:
        for c in configs:
            c.use_news_filter = False

    # ── Run backtests ──
    all_results = []
    total = len(configs) * len(data)

    for idx, cfg in enumerate(configs):
        for sym, df in data.items():
            result = run_backtest_single(df, cfg, sym)
            all_results.append(result)

    # If we have both pairs, also run combined
    if len(data) == 2:
        print(f"\n🔄 Running dual-pair backtests ({len(configs)} configs)...")
        syms_list = list(data.keys())
        for idx, cfg in enumerate(configs):
            result = run_backtest_dual(
                data[syms_list[0]], data[syms_list[1]],
                cfg, syms_list[0], syms_list[1]
            )
            all_results.append(result)

    # ── Print results ──
    print_results_table(all_results)

    # ── Detailed view if requested ──
    if args.detailed:
        for r in all_results:
            if args.detailed.lower() in r['config_name'].lower() or \
               args.detailed in r.get('symbol', ''):
                r_show = r.copy()
                r_show['config_name'] = r['config_name']
                print_detailed(r_show)

    # ── Save to JSON ──
    out_path = HERE / "backtest_results.json"
    serializable = []
    for r in all_results:
        sr = {k: v for k, v in r.items() if k != 'monthly_map'}
        sr['monthly_map'] = {str(k): round(v, 2) for k, v in r.get('monthly_map', {}).items()}
        serializable.append(sr)
    out_path.write_text(json.dumps(serializable, indent=2))
    print(f"\n💾 Full results saved to {out_path}")

    # ── Summary recommendation ──
    valid = [r for r in all_results if r.get('error') is None and r['trades'] > 10]
    if valid:
        # Best combination of return + safety
        scored = []
        for r in valid:
            # Score: return% - 2*MDD% + PF*10 + win_rate/10
            score = r['total_return_pct'] - 2 * r['max_drawdown_pct'] + r['profit_factor'] * 10 + r['win_rate_pct'] / 10
            scored.append((score, r))
        scored.sort(key=lambda x: x[0], reverse=True)

        print(f"\n{'='*60}")
        print(f"  🏆 TOP 3 RECOMMENDED CONFIGS")
        print(f"{'='*60}")
        for score, r in scored[:3]:
            print(f"\n  #{scored.index((score, r)) + 1} — {r['config_name']} on {r['symbol']}")
            print(f"     Risk {r['risk_pct']}% | ATR SL {r['atr_sl_mult']} | RSI {r['rsi_th']} | Consec {r['max_consec']}")
            print(f"     Trades: {r['trades']}  WR: {r['win_rate_pct']}%  PF: {r['profit_factor']:.2f}")
            print(f"     P&L: ${r['total_pnl']:+.2f} ({r['total_return_pct']:+.1f}%)  MDD: {r['max_drawdown_pct']:.1f}%")
            print(f"     Avg $/trade: ${r['return_per_trade']:.2f}  Avg $/month: ${r['avg_monthly']:.2f}")

        best = scored[0][1]
        print(f"\n{'='*60}")
        print(f"  → Estimated monthly on $500: ${best['avg_monthly']:.2f}")
        print(f"  → At this rate, monthly return: {best['avg_monthly']/500*100:.1f}%")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()
