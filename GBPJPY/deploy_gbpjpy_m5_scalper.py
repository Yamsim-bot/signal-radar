#!/usr/bin/env python3
"""
GBPJPY M5 Scalper — ADX/DI + ATR Trailing
===========================================
Live EA for Vantage Cent Raw ECN ($500 capital).

Based on backtest winner (consec_loss bugfix applied Jul 2026):
  R2-A0.8-RR1.0-ADX20:
    2% risk, 0.8 ATR SL, RR 1.0, ADX 20 threshold, max 3 consec losses
    72% WR, PF 2.08, 13.6% DD, projected $296/month

Strategy:
  - M5 chart, London session only (08:00–15:00 GMT)
  - ADX(14) >= threshold (20)
  - DI+/DI- direction filter (+DI > -DI for long, -DI > +DI for short)
  - EMA 20/50 trend confirmation
  - ATR-based tight stops (0.8× ATR)
  - Quick 1:1 take profit
  - Partial TP at 1:1 on 50%, trail rest with ATR
  - Circuit breakers (daily loss, total DD, consecutive losses)
  - News blackout around major releases

Usage:
    python deploy_gbpjpy_m5_scalper.py               # Run live (24/7)
    python deploy_gbpjpy_m5_scalper.py --hours 8     # Run for 8 hours
    python deploy_gbpjpy_m5_scalper.py --status      # Check setup only
    python deploy_gbpjpy_m5_scalper.py --find-symbol # Detect symbol name
    python deploy_gbpjpy_m5_scalper.py --backtest    # Run local backtest
"""
import sys, os, json, math, time, argparse, signal
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dataclasses import dataclass, asdict, fields
import numpy as np
import pandas as pd

HERE = Path(__file__).parent.resolve()

_OK  = "[OK]"
_FAIL = "[X]"
_ARROW = "[..]"

try:
    import MetaTrader5 as mt5
    HAS_MT5 = True
except ImportError:
    HAS_MT5 = False


# ─────────────────────────────────────────────────────────
# INSTRUMENT SPEC
# ─────────────────────────────────────────────────────────
PIP = 0.01  # 1 pip = 0.01 price for JPY pairs
COMMISSION_LOT = 0.06  # $0.06 RT per cent lot

# Import proven backtest engine functions
_BT_MODULE = "m5_scalper_backtest"
if sys.path[0] != str(HERE):
    sys.path.insert(0, str(HERE))

from m5_scalper_backtest import (
    compute_indicators as bt_compute_indicators,
    detect_signals as bt_detect_signals,
    simulate_trade as bt_simulate_trade,
    run_backtest as bt_run_backtest,
    INSTRUMENTS as BT_INSTRUMENTS,
    fetch_m5 as bt_fetch_m5,
    resolve_symbol as bt_resolve_symbol,
    pip_value_usd as bt_pip_value,
    in_news_blackout as bt_news_blackout,
    quick_configs as bt_quick_configs,
    generate_configs as bt_generate_configs,
)


# ─────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────
@dataclass
class Config:
    """Tunable strategy parameters."""
    # Position sizing
    capital: float = 500.0
    risk_pct: float = 2.0           # % risk per trade (R2 conservative)
    max_lots: int = 50

    # Entry
    adx_threshold: int = 20
    use_di_cross: bool = False       # False = ADX + DI direction; True = DI cross only
    use_rsi_filter: bool = False
    ema_fast: int = 20
    ema_slow: int = 50

    # Exit
    atr_sl_mult: float = 0.8        # SL distance as multiple of ATR
    rr_target: float = 1.0          # TP = SL × RR
    trail_atr_mult: float = 0.8     # Trail distance after partial TP
    partial_tp_pct: float = 0.5     # 50% partial at 1:1

    # Session
    sess_start_hour: int = 8        # London open GMT
    sess_end_hour: int = 15          # London close GMT

    # Circuit breakers
    max_consecutive_losses: int = 3
    max_daily_loss_pct: float = 5.0
    max_total_dd_pct: float = 20.0
    max_daily_trades: int = 15

    # Misc
    news_blackout_min: int = 30     # minutes before/after news
    max_spread_pips: float = 2.0    # max spread to enter
    commission_rt: float = COMMISSION_LOT
    backtest_bars: int = 5000

    def abbrev(self) -> str:
        return (f"R{int(self.risk_pct)}-A{self.atr_sl_mult}"
                f"-RR{self.rr_target}-ADX{self.adx_threshold}"
                f"-C{self.max_consecutive_losses}")

    def to_dict(self) -> dict:
        """Convert config to dict for backtest engine."""
        return {
            'risk_pct': self.risk_pct,
            'atr_sl_mult': self.atr_sl_mult,
            'rr_target': self.rr_target,
            'adx_threshold': self.adx_threshold,
            'use_di_cross': self.use_di_cross,
            'use_rsi_filter': self.use_rsi_filter,
            'max_consecutive_losses': self.max_consecutive_losses,
            'max_daily_loss_pct': self.max_daily_loss_pct,
            'max_total_dd_pct': self.max_total_dd_pct,
            'max_trades_per_day': self.max_daily_trades,
            'trail_atr_mult': self.trail_atr_mult,
            'initial_capital': self.capital,
        }


# ─────────────────────────────────────────────────────────
# SYMBOL RESOLUTION
# ─────────────────────────────────────────────────────────
from m5_scalper_backtest import resolve_symbol as bt_resolve_symbol

def resolve_symbol(base: str = "GBPJPY") -> str | None:
    return bt_resolve_symbol(base)


# ─────────────────────────────────────────────────────────
# CONFIG-TO-CFG HELPER
# ─────────────────────────────────────────────────────────
def _cfg_to_dict(cfg: Config) -> dict:
    return cfg.to_dict()


# ─────────────────────────────────────────────────────────
# PIP VALUE
# ─────────────────────────────────────────────────────────
def pip_value_usd(symbol: str, price: float | None = None) -> float:
    """USD value of 1 pip for 1 cent lot (JPY pairs)."""
    from m5_scalper_backtest import pip_value_usd as bt_pip
    return bt_pip(symbol, price or 200.0)


# ─────────────────────────────────────────────────────────
# POSITION SIZING
# ─────────────────────────────────────────────────────────
def compute_lots(signal: dict, cfg: Config, price: float) -> int:
    """Calculate cent lots based on risk."""
    risk_usd = cfg.capital * cfg.risk_pct / 100.0
    pv = pip_value_usd('GBPJPY', price)
    sl_pips = signal['sl_dist'] / PIP
    lots_raw = risk_usd / (sl_pips * pv) if sl_pips > 0 and pv > 0 else 1
    return max(1, min(int(lots_raw), cfg.max_lots))


# ─────────────────────────────────────────────────────────
# MT5 ORDER EXECUTION
# ─────────────────────────────────────────────────────────
def open_position(signal: dict, cfg: Config, symbol: str, resolved: str) -> dict:
    """Place market order with SL/TP."""
    price = signal['price']
    direction = signal['direction']
    lots = compute_lots(signal, cfg, price)

    order_type = mt5.ORDER_TYPE_BUY if direction == 'long' else mt5.ORDER_TYPE_SELL

    # Request
    request = {
        'action': mt5.TRADE_ACTION_DEAL,
        'symbol': resolved,
        'volume': lots,
        'type': order_type,
        'price': price,
        'sl': signal['sl_price'],
        'tp': signal['tp2_price'] if cfg.rr_target > 1.0 else signal['tp1_price'],
        'deviation': 20,
        'magic': 20260701,
        'comment': f"M5_{cfg.abbrev()}",
        'type_time': mt5.ORDER_TIME_GTC,
        'type_filling': mt5.ORDER_FILLING_IOC,
    }

    # For sell orders, need to use current bid
    tick = mt5.symbol_info_tick(resolved)
    if tick:
        request['price'] = tick.ask if direction == 'long' else tick.bid
        # Recalculate SL/TP from current price
        diff = request['price'] - price if direction == 'long' else price - request['price']
        if direction == 'long':
            request['sl'] = request['price'] - signal['sl_dist']
            request['tp'] = request['price'] + signal['tp_dist']
        else:
            request['sl'] = request['price'] + signal['sl_dist']
            request['tp'] = request['price'] - signal['tp_dist']

    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        return {
            'success': True,
            'order': result.order,
            'lots': lots,
            'price': result.price,
            'comment': f"OPEN {direction} {lots}@{result.price:.3f} SL={request['sl']:.3f} TP={request['tp']:.3f}",
        }
    else:
        err = result.comment if result else 'order_send failed'
        return {'success': False, 'error': err}


def close_position(position: tuple, reason: str = "manual"):
    """Close an MT5 position by ticket."""
    ticket = position.ticket
    symbol = position.symbol
    lots = position.volume
    direction = position.type  # 0=buy, 1=sell

    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        return {'success': False, 'error': 'no tick'}

    close_type = mt5.ORDER_TYPE_SELL if direction == 0 else mt5.ORDER_TYPE_BUY
    close_price = tick.bid if direction == 0 else tick.ask

    request = {
        'action': mt5.TRADE_ACTION_DEAL,
        'symbol': symbol,
        'volume': lots,
        'type': close_type,
        'position': ticket,
        'price': close_price,
        'deviation': 20,
        'magic': 20260701,
        'comment': reason,
        'type_time': mt5.ORDER_TIME_GTC,
        'type_filling': mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        return {'success': True, 'price': close_price, 'reason': reason}
    return {'success': False, 'error': result.comment if result else 'close failed'}


def modify_sl(ticket: int, symbol: str, new_sl: float) -> dict:
    """Move stop loss on an open position."""
    request = {
        'action': mt5.TRADE_ACTION_SLTP,
        'position': ticket,
        'symbol': symbol,
        'sl': new_sl,
        'tp': None,
        'deviation': 10,
        'magic': 20260701,
    }
    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        return {'success': True}
    return {'success': False, 'error': result.comment if result else 'modify failed'}


# ─────────────────────────────────────────────────────────
# TRADE MANAGER
# ─────────────────────────────────────────────────────────
class TradeManager:
    """Manages open positions with trailing stop and partial TP."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.positions = {}  # {ticket: {symbol, dir, entry, sl, partial_hit, ...}}
        self.last_check = {}

    def refresh(self):
        """Get current positions from MT5."""
        self.positions = {}
        positions = mt5.positions_get(symbol=self.resolved) if hasattr(self, 'resolved') else mt5.positions_get()
        if positions:
            for pos in positions:
                self.positions[pos.ticket] = {
                    'ticket': pos.ticket,
                    'symbol': pos.symbol,
                    'direction': 'long' if pos.type == 0 else 'short',
                    'entry': pos.price_open,
                    'sl': pos.sl,
                    'tp': pos.tp,
                    'lots': pos.volume,
                    'profit': pos.profit,
                    'partial_hit': False,  # will be tracked in memory
                    'entry_time': datetime.fromtimestamp(pos.time),
                }

    def manage(self, df: pd.DataFrame, symbol: str, resolved: str):
        """Check and manage open positions (trailing, partial TP)."""
        self.resolved = resolved
        self.refresh()
        if not self.positions:
            return

        for ticket, pos in list(self.positions.items()):
            tick = mt5.symbol_info_tick(resolved)
            if not tick:
                continue

            current = tick.bid if pos['direction'] == 'long' else tick.ask
            atr = float(df['atr'].iloc[-1]) if len(df) > 0 else 0.004

            # Check partial TP at 1:1
            if not pos['partial_hit'] and self.cfg.rr_target > 1.0:
                sl_dist = abs(pos['entry'] - pos['sl'])
                if pos['direction'] == 'long' and current >= pos['entry'] + sl_dist:
                    # Close half
                    self.close_half(pos)
                    pos['partial_hit'] = True
                    # Move SL to breakeven + small buffer
                    new_sl = pos['entry'] + 0.002  # +0.2 pips
                    modify_sl(ticket, resolved, new_sl)
                    pos['sl'] = new_sl
                elif pos['direction'] == 'short' and current <= pos['entry'] - sl_dist:
                    self.close_half(pos)
                    pos['partial_hit'] = True
                    new_sl = pos['entry'] - 0.002
                    modify_sl(ticket, resolved, new_sl)
                    pos['sl'] = new_sl

            # Trailing stop after partial
            if pos['partial_hit']:
                trail_dist = atr * self.cfg.trail_atr_mult
                if pos['direction'] == 'long':
                    new_sl = current - trail_dist
                    if new_sl > pos['sl']:
                        modify_sl(ticket, resolved, new_sl)
                        pos['sl'] = new_sl
                else:
                    new_sl = current + trail_dist
                    if new_sl < pos['sl']:
                        modify_sl(ticket, resolved, new_sl)
                        pos['sl'] = new_sl

    def close_half(self, pos: dict):
        """Close 50% of a position."""
        half_lots = max(0.01, pos['lots'] * 0.5)
        tick = mt5.symbol_info_tick(pos['symbol'])
        if not tick:
            return
        close_type = mt5.ORDER_TYPE_SELL if pos['direction'] == 'long' else mt5.ORDER_TYPE_BUY
        close_price = tick.bid if pos['direction'] == 'long' else tick.ask
        request = {
            'action': mt5.TRADE_ACTION_DEAL,
            'symbol': pos['symbol'],
            'volume': half_lots,
            'type': close_type,
            'position': pos['ticket'],
            'price': close_price,
            'deviation': 20,
            'magic': 20260701,
            'comment': 'PARTIAL_TP',
            'type_time': mt5.ORDER_TIME_GTC,
            'type_filling': mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)


# ─────────────────────────────────────────────────────────
# LIVE SIGNAL DETECTION
# ─────────────────────────────────────────────────────────
def live_signal_check(df: pd.DataFrame, cfg: Config) -> dict:
    """Check signal on the last complete bar for live trading.

    Uses the proven detection engine. df must already have indicators.
    """
    if len(df) < 150:
        return {'action': 'skip', 'reason': 'insufficient data'}

    i = len(df) - 1
    bt = df.index[i]
    bar = df.iloc[i]

    # Session
    if not (cfg.sess_start_hour <= bt.hour < cfg.sess_end_hour):
        return {'action': 'skip', 'reason': 'outside session'}

    # News
    from m5_scalper_backtest import in_news_blackout
    if in_news_blackout(bt):
        return {'action': 'skip', 'reason': 'news blackout'}

    # Use proven signal detector
    cfg_dict = cfg.to_dict()
    sig = bt_detect_signals(df, i, cfg_dict)
    if sig['direction'] is None:
        return {'action': 'skip', 'reason': 'no signal'}

    # Spread check
    tick = mt5.symbol_info_tick(cfg_dict.get('_resolved', ''))
    if tick and tick.spread / 10 > cfg.max_spread_pips * 10:
        return {'action': 'skip', 'reason': f'spread {tick.spread} too high'}

    atr = float(bar['atr'])
    price = float(bar['close'])
    sl_dist = max(atr * cfg.atr_sl_mult, 0.003)
    tp_dist = sl_dist * cfg.rr_target

    return {
        'action': 'enter',
        'direction': sig['direction'],
        'entry_type': sig['entry_type'],
        'price': price,
        'sl_price': price - sl_dist if sig['direction'] == 'long' else price + sl_dist,
        'tp1_price': price + sl_dist if sig['direction'] == 'long' else price - sl_dist,
        'tp2_price': price + tp_dist if sig['direction'] == 'long' else price - tp_dist,
        'sl_dist': sl_dist,
        'tp_dist': tp_dist,
        'atr': atr,
        'adx': float(bar['adx']),
    }


# ─────────────────────────────────────────────────────────
# BACKTEST (delegated to proven engine)
# ─────────────────────────────────────────────────────────
def run_backtest(cfg: Config, symbol: str = "GBPJPY", bars: int = 5000) -> dict:
    """Run backtest using the proven engine."""
    if not HAS_MT5:
        return {'error': 'MT5 not available'}
    if not mt5.initialize():
        return {'error': 'MT5 init failed'}
    resolved = bt_resolve_symbol(symbol)
    if not resolved:
        mt5.shutdown()
        return {'error': f'{symbol} not found'}
    mt5.symbol_select(resolved, True)
    rates = mt5.copy_rates_from_pos(resolved, mt5.TIMEFRAME_M5, 0, bars)
    mt5.shutdown()
    if rates is None or len(rates) < 500:
        return {'error': 'insufficient data'}
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('time', inplace=True)

    cfg_dict = cfg.to_dict()
    cfg_dict['abbrev'] = cfg.abbrev()
    result = bt_run_backtest(df, cfg_dict, 'GBPJPY')
    if isinstance(result, dict):
        result['return_pct'] = result.get('total_return_pct', 0)
    return result


# ─────────────────────────────────────────────────────────
# LIVE LOOP
# ─────────────────────────────────────────────────────────
def live_loop(cfg: Config, symbol: str, max_hours: int = 0):
    """Main live trading loop."""
    if not HAS_MT5:
        print(f" {_FAIL} MetaTrader5 package not installed")
        return

    print(f" {_ARROW} Initializing MT5...")
    if not mt5.initialize():
        print(f" {_FAIL} MT5 init failed: {mt5.last_error()}")
        return
    print(f" {_OK} MT5 initialized (build {mt5.version()[0]})")

    # Resolve symbol
    resolved = resolve_symbol(symbol)
    if not resolved:
        print(f" {_FAIL} Symbol {symbol} not found on MT5")
        mt5.shutdown()
        return
    print(f" {_OK} Using symbol: {resolved}")

    mt5.symbol_select(resolved, True)
    tick = mt5.symbol_info_tick(resolved)
    if tick:
        print(f"   Bid: {tick.bid}  Ask: {tick.ask}  Spread: {tick.spread}")

    # Account info
    acc = mt5.account_info()
    if acc:
        print(f"   Account: {acc.login}  Balance: ${acc.balance:.2f}  "
              f"Equity: ${acc.equity:.2f}  Leverage: 1:{acc.leverage}")
        cfg.capital = acc.balance
    else:
        print(f"   (no account info)")

    # Trade manager
    manager = TradeManager(cfg)
    manager.resolved = resolved

    # State
    last_bar_time = None
    start_time = time.time()
    trade_count = 0
    day_count = 0
    daily_pnl = 0.0
    consec_loss = 0
    peak_eq = acc.balance if acc else cfg.capital
    cur_eq = acc.balance if acc else cfg.capital

    print(f"\n {_OK} Starting live trading loop...")
    print(f"   Config: {cfg.abbrev()}")
    print(f"   Session: {cfg.sess_start_hour}:00-{cfg.sess_end_hour}:00 GMT")
    print(f"   Max hours: {'unlimited' if max_hours == 0 else max_hours}")
    print()

    def shutdown(sig=None, frame=None):
        print(f"\n {_ARROW} Shutting down...")
        mt5.shutdown()
        print(f" {_OK} Done")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    while True:
        # Time check
        if max_hours > 0 and (time.time() - start_time) / 3600 > max_hours:
            print(f" {_OK} Reached {max_hours}h limit")
            break

        # Current time
        now = datetime.now(timezone.utc)
        h, m = now.hour, now.minute
        bar_min = (h * 60 + m) // 5 * 5  # round down to M5

        # Session check
        in_session = cfg.sess_start_hour <= h < cfg.sess_end_hour
        if not in_session:
            if last_bar_time is not None:
                print(f" {_ARROW} Session closed ({h:02d}:{m:02d} GMT) — waiting for {cfg.sess_start_hour}:00 GMT...")
                last_bar_time = None
            time.sleep(30)
            continue

        # Wait for new M5 bar
        current_bar = f"{now.date()} {h:02d}:{bar_min:02d}"
        if current_bar == last_bar_time:
            time.sleep(5)
            continue
        last_bar_time = current_bar

        # Refresh account state
        acc = mt5.account_info()
        if acc:
            cur_eq = acc.equity
            if cur_eq > peak_eq:
                peak_eq = cur_eq
            dd_pct = (peak_eq - cur_eq) / max(peak_eq, 1) * 100

        # Day reset
        if now.day != day_count:
            day_count = now.day
            daily_pnl = 0
            # Reset circuit breakers each day
            if consec_loss > 0:
                print(f"   New day — resetting consec_loss counter")
            consec_loss = 0

        # Check circuit breakers
        if consec_loss >= cfg.max_consecutive_losses:
            print(f"   Circuit breaker: {consec_loss} consec losses — pausing for today")
            time.sleep(60)
            continue

        if cur_eq < peak_eq * (1 - cfg.max_total_dd_pct / 100):
            print(f"   Circuit breaker: total DD > {cfg.max_total_dd_pct}% — pausing")
            time.sleep(120)
            continue

        if daily_pnl < -cfg.max_daily_loss_pct / 100 * cfg.capital:
            print(f"   Daily loss limit hit ({daily_pnl:.2f}) — pausing for today")
            time.sleep(300)
            continue

        # Check existing positions
        positions = mt5.positions_get(symbol=resolved)
        if positions and len(positions) > 0:
            # Manage trailing
            df = fetch_bars(resolved, 200)
            if df is not None and len(df) > 100:
                d = bt_compute_indicators(df)
                manager.manage(d, symbol, resolved)
            time.sleep(10)
            continue

        # Want to ensure max daily trades
        if trade_count >= cfg.max_daily_trades:
            time.sleep(60)
            continue

        # Fetch bars and check signal
        df = fetch_bars(resolved, 200)
        if df is None or len(df) < 100:
            time.sleep(5)
            continue

        d = bt_compute_indicators(df)
        signal = live_signal_check(d, cfg)

        if signal['action'] != 'enter':
            continue

        # Spread check
        tick = mt5.symbol_info_tick(resolved)
        if tick and tick.spread / 10 > cfg.max_spread_pips * 10:
            continue

        # Execute trade
        result = open_position(signal, cfg, symbol, resolved)
        if result['success']:
            trade_count += 1
            print(f"\n  [{datetime.now().strftime('%H:%M:%S')}] "
                  f"{result['comment']}")
        else:
            print(f"   Order failed: {result.get('error', 'unknown')}")

        time.sleep(5)

    mt5.shutdown()
    print(f" {_OK} Done — {trade_count} trades executed")


def fetch_bars(symbol: str, count: int = 200) -> pd.DataFrame | None:
    """Fetch recent M5 bars."""
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, count)
    if rates is None or len(rates) < 20:
        return None
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('time', inplace=True)
    return df


# ─────────────────────────────────────────────────────────
# DIAGNOSTICS
# ─────────────────────────────────────────────────────────
def show_status():
    """Display account and symbol info."""
    if not HAS_MT5:
        print(f" {_FAIL} MetaTrader5 not installed")
        return
    if not mt5.initialize():
        print(f" {_FAIL} MT5 init failed: {mt5.last_error()}")
        return

    acc = mt5.account_info()
    if acc:
        print(f"\n Account:     {acc.login}")
        print(f" Name:        {acc.name}")
        print(f" Server:      {acc.server}")
        print(f" Balance:     ${acc.balance:.2f}")
        print(f" Equity:      ${acc.equity:.2f}")
        print(f" Leverage:    1:{acc.leverage}")

    for sym in ['GBPJPY+', 'GBPJPY', 'GBPJPY.c', 'GBPJPYc']:
        info = mt5.symbol_info(sym)
        if info:
            tick = mt5.symbol_info_tick(sym)
            print(f"\n Symbol:      {sym}")
            print(f" Digits:      {info.digits}")
            print(f" Spread:      {info.spread} ({info.spread/10:.1f} pips)")
            if tick:
                print(f" Bid/Ask:     {tick.bid}/{tick.ask}")
            print(f" Trade mode:  {info.trade_mode} (0=disabled, 4=full)")
            break

    mt5.shutdown()


def find_symbol():
    """Print all matching symbols."""
    if not HAS_MT5:
        return
    if not mt5.initialize():
        return
    symbols = mt5.symbols_get()
    if symbols:
        for s in symbols:
            if 'GBPJPY' in s.name or 'EURJPY' in s.name:
                tick = mt5.symbol_info_tick(s.name)
                if tick and tick.bid > 0:
                    print(f"  {s.name}: bid={tick.bid} ask={tick.ask} spread={tick.spread}")
    mt5.shutdown()


# ─────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="GBPJPY M5 ADX Scalper")
    ap.add_argument("--hours", type=int, default=0, help="Run for N hours (0=unlimited)")
    ap.add_argument("--status", action="store_true", help="Show account/symbol info")
    ap.add_argument("--find-symbol", action="store_true", help="List matching symbols")
    ap.add_argument("--backtest", action="store_true", help="Run backtest")
    ap.add_argument("--bars", type=int, default=5000, help="Backtest bars")
    ap.add_argument("--risk", type=float, default=3.0, help="Risk % override")
    ap.add_argument("--atr", type=float, default=0.8, help="ATR SL mult override")
    ap.add_argument("--rr", type=float, default=1.0, help="RR ratio override")
    ap.add_argument("--adx", type=int, default=20, help="ADX threshold override")
    args = ap.parse_args()

    cfg = Config(
        risk_pct=args.risk,
        atr_sl_mult=args.atr,
        rr_target=args.rr,
        adx_threshold=args.adx,
    )

    print(f" GBPJPY M5 Scalper v1.0")
    print(f" Strategy: {cfg.abbrev()}")
    print(f" Capital:  ${cfg.capital:.2f}")
    print(f" Session:  {cfg.sess_start_hour}:00-{cfg.sess_end_hour}:00 GMT")

    if args.status:
        show_status()
        return

    if args.find_symbol:
        find_symbol()
        return

    if args.backtest:
        print(f"\n {_ARROW} Running backtest ({args.bars} bars)...")
        result = run_backtest(cfg, bars=args.bars)
        if result.get('error'):
            print(f" {_FAIL} {result['error']}")
        else:
            print(f"\n {_OK} Backtest Results:")
            print(f"   Trades:      {result['trades']} (W:{result['wins']} L:{result['losses']})")
            print(f"   Win rate:    {result['win_rate_pct']}%")
            print(f"   Total P&L:   ${result['total_pnl']:+.2f} ({result['total_return_pct']:+.1f}%)")
            print(f"   Profit fac:  {result['profit_factor']:.2f}")
            print(f"   Max DD:      {result['max_drawdown_pct']:.1f}%")
            print(f"   Trades/day:  {result['trades_per_day']}")
            print(f"   Proj/month:  ${result['projected_monthly']:.2f}")
            if result.get('monthly_map'):
                print(f"   By month: {result['monthly_map']}")
        return

    # Live
    print(f"\n {_ARROW} Starting live mode...")
    live_loop(cfg, "GBPJPY", max_hours=args.hours)


if __name__ == "__main__":
    main()
