"""MT5 Terminal Bridge — connect to Vantage (or any broker) MetaTrader 5 account.

Provides:
  - Account summary (balance, equity, margin, etc.)
  - Open positions with live pricing
  - Order history (closed deals today/this week)
  - Pending orders
  - Place, modify, and close trades
  - Symbol market watch data
"""

import time
from datetime import datetime, timedelta
from typing import Optional

HAS_MT5 = False
try:
    import MetaTrader5 as mt5
    HAS_MT5 = True
except ImportError:
    pass

_MT5_INITIALIZED = False
_MT5_LAST_CHECK = 0
_MT5_CACHE_TTL = 2.0  # seconds for positions/account cache


def _ensure_mt5() -> bool:
    """Initialize MT5 if not already connected. Returns True if ready."""
    global _MT5_INITIALIZED, _MT5_LAST_CHECK
    now = time.time()
    if _MT5_INITIALIZED and now - _MT5_LAST_CHECK < 10:
        return True
    if not HAS_MT5:
        return False
    if not _MT5_INITIALIZED:
        _MT5_INITIALIZED = mt5.initialize()
    if _MT5_INITIALIZED:
        # Verify terminal is still responsive
        acc = mt5.account_info()
        if acc is None:
            # Don't blindly shutdown — the terminal may have been initialized
            # with login/password/server by connect_to_broker(). Just flag it
            # as dead and let the caller reconnect with saved credentials.
            _MT5_INITIALIZED = False
    _MT5_LAST_CHECK = now
    return _MT5_INITIALIZED


def get_account_info() -> Optional[dict]:
    """Return current account summary."""
    if not _ensure_mt5():
        return None
    try:
        acc = mt5.account_info()
        if acc is None:
            return None
        return {
            'login': acc.login,
            'server': acc.server,
            'name': acc.name,
            'company': acc.company,
            'currency': acc.currency,
            'balance': round(acc.balance, 2),
            'equity': round(acc.equity, 2),
            'margin': round(acc.margin, 2),
            'margin_free': round(acc.margin_free, 2),
            'margin_level': round(acc.margin_level, 2) if acc.margin_level else 0,
            'leverage': acc.leverage,
            'profit': round(acc.profit, 2),
            'connected': True,
        }
    except Exception as e:
        return {'error': str(e), 'connected': False}


def get_positions() -> list[dict]:
    """Return all open positions with live pricing."""
    if not _ensure_mt5():
        return []
    try:
        positions = mt5.positions_get()
        if positions is None:
            return []
        result = []
        for p in positions:
            pos = {
                'ticket': p.ticket,
                'symbol': p.symbol,
                'type': 'Buy' if p.type == 0 else 'Sell',
                'type_num': int(p.type),
                'volume': p.volume,
                'lot_size': p.volume,
                'entry_price': p.price_open,
                'current_price': p.price_current,
                'sl': p.sl if p.sl else 0,
                'tp': p.tp if p.tp else 0,
                'profit': round(p.profit, 2),
                'swap': round(p.swap, 2),
                'commission': round(p.commission, 2),
                'total_profit': round(p.profit + p.swap + p.commission, 2),
                'time': datetime.fromtimestamp(p.time).strftime('%Y-%m-%d %H:%M'),
                'time_msc': p.time_msc,
                'magic': p.magic,
                'comment': p.comment or '',
                'identifier': p.identifier,
            }
            # Calculate pips
            spec = mt5.symbol_info(p.symbol)
            if spec:
                pos['pip_factor'] = spec.point
                pos['digits'] = spec.digits
                if p.type == 0:  # Buy
                    pos['pips'] = round((p.price_current - p.price_open) / spec.point, 1)
                else:  # Sell
                    pos['pips'] = round((p.price_open - p.price_current) / spec.point, 1)
            else:
                pos['pips'] = 0
            result.append(pos)
        return result
    except Exception:
        return []


def get_order_history(days: int = 1) -> list[dict]:
    """Get closed orders/deals from the last N days."""
    if not _ensure_mt5():
        return []
    try:
        now = datetime.now()
        from_dt = now - timedelta(days=days)
        history = mt5.history_deals_get(from_dt, now)
        if history is None:
            return []
        result = []
        for d in history:
            result.append({
                'ticket': d.ticket,
                'order': d.order,
                'symbol': d.symbol,
                'type': 'Buy' if d.type == 0 else ('Sell' if d.type == 1 else f'Type_{d.type}'),
                'volume': d.volume,
                'price': d.price,
                'profit': round(d.profit, 2),
                'commission': round(d.commission, 2),
                'swap': round(d.swap, 2),
                'total': round(d.profit + d.commission + d.swap, 2),
                'time': datetime.fromtimestamp(d.time).strftime('%Y-%m-%d %H:%M'),
                'comment': d.comment or '',
                'entry': 'entry' if d.entry == 0 else ('out' if d.entry == 1 else 'inout'),
            })
        return result
    except Exception:
        return []


def get_open_orders() -> list[dict]:
    """Get pending orders."""
    if not _ensure_mt5():
        return []
    try:
        orders = mt5.orders_get()
        if orders is None:
            return []
        result = []
        for o in orders:
            type_map = {2: 'Buy Limit', 3: 'Sell Limit', 4: 'Buy Stop', 5: 'Sell Stop'}
            result.append({
                'ticket': o.ticket,
                'symbol': o.symbol,
                'type': type_map.get(int(o.type), f'Type_{o.type}'),
                'volume': o.volume,
                'price': o.price_open,
                'sl': o.sl or 0,
                'tp': o.tp or 0,
                'time': datetime.fromtimestamp(o.time_setup).strftime('%Y-%m-%d %H:%M'),
                'expiration': datetime.fromtimestamp(o.time_expiration).strftime('%Y-%m-%d %H:%M') if o.time_expiration else '',
                'comment': o.comment or '',
            })
        return result
    except Exception:
        return []


def place_order(
    symbol: str,
    order_type: str,  # 'buy' or 'sell'
    volume: float,
    sl: float = 0,
    tp: float = 0,
    comment: str = '',
    magic: int = 123456,
) -> dict:
    """Place a market order (instant execution)."""
    if not _ensure_mt5():
        return {'success': False, 'error': 'MT5 not connected'}
    try:
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            return {'success': False, 'error': f'Symbol {symbol} not found'}
        if not symbol_info.visible:
            mt5.symbol_select(symbol, True)

        # Get current price
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return {'success': False, 'error': f'No tick data for {symbol}'}

        order_type_int = mt5.ORDER_TYPE_BUY if order_type.lower() == 'buy' else mt5.ORDER_TYPE_SELL
        price = tick.ask if order_type_int == mt5.ORDER_TYPE_BUY else tick.bid
        sl_price = sl if sl > 0 else 0
        tp_price = tp if tp > 0 else 0

        request = {
            'action': mt5.TRADE_ACTION_DEAL,
            'symbol': symbol,
            'volume': float(volume),
            'type': order_type_int,
            'price': price,
            'sl': float(sl_price),
            'tp': float(tp_price),
            'deviation': 20,
            'magic': magic,
            'comment': comment,
            'type_time': mt5.ORDER_TIME_GTC,
            'type_filling': mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result is None:
            return {'success': False, 'error': 'Order send returned None'}

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return {
                'success': False,
                'error': f'Order failed: retcode={result.retcode}, {result.comment}',
                'retcode': result.retcode,
            }

        return {
            'success': True,
            'ticket': result.order,
            'price': price,
            'volume': volume,
            'type': order_type,
            'message': f'Order placed: #{result.order}',
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}


def close_position(ticket: int, volume: Optional[float] = None) -> dict:
    """Close a position by ticket number."""
    if not _ensure_mt5():
        return {'success': False, 'error': 'MT5 not connected'}
    try:
        positions = mt5.positions_get(ticket=ticket)
        if not positions or len(positions) == 0:
            return {'success': False, 'error': f'Position #{ticket} not found'}
        pos = positions[0]

        close_volume = volume if volume else pos.volume
        close_type = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(pos.symbol)
        close_price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask

        request = {
            'action': mt5.TRADE_ACTION_DEAL,
            'symbol': pos.symbol,
            'volume': close_volume,
            'type': close_type,
            'position': ticket,
            'price': close_price,
            'deviation': 20,
            'magic': pos.magic,
            'comment': 'Closed by Yams Radar',
            'type_time': mt5.ORDER_TIME_GTC,
            'type_filling': mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result is None:
            return {'success': False, 'error': 'Close order returned None'}

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return {
                'success': False,
                'error': f'Close failed: retcode={result.retcode}',
                'retcode': result.retcode,
            }

        return {
            'success': True,
            'ticket': result.order,
            'message': f'Position #{ticket} closed',
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}


def modify_position(ticket: int, sl: float = 0, tp: float = 0) -> dict:
    """Modify SL/TP on an open position."""
    if not _ensure_mt5():
        return {'success': False, 'error': 'MT5 not connected'}
    try:
        positions = mt5.positions_get(ticket=ticket)
        if not positions or len(positions) == 0:
            return {'success': False, 'error': f'Position #{ticket} not found'}
        pos = positions[0]

        request = {
            'action': mt5.TRADE_ACTION_SLTP,
            'symbol': pos.symbol,
            'position': ticket,
            'sl': float(sl) if sl > 0 else pos.sl,
            'tp': float(tp) if tp > 0 else pos.tp,
        }

        result = mt5.order_send(request)
        if result is None:
            return {'success': False, 'error': 'Modify returned None'}
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return {'success': False, 'error': f'Modify failed: retcode={result.retcode}'}

        return {'success': True, 'message': f'Position #{ticket} modified'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def get_market_watch(symbols: Optional[list] = None) -> list[dict]:
    """Get market watch data (bid, ask, spread, etc.) for symbols."""
    if not _ensure_mt5():
        return []
    from .instruments import get_symbols
    if symbols is None:
        symbols = get_symbols()
    result = []
    for sym in symbols:
        try:
            tick = mt5.symbol_info_tick(sym)
            if tick:
                result.append({
                    'symbol': sym,
                    'bid': tick.bid,
                    'ask': tick.ask,
                    'spread': tick.spread,
                    'last': tick.last,
                })
            else:
                # Try to select symbol
                mt5.symbol_select(sym, True)
                tick = mt5.symbol_info_tick(sym)
                if tick:
                    result.append({
                        'symbol': sym,
                        'bid': tick.bid,
                        'ask': tick.ask,
                        'spread': tick.spread,
                        'last': tick.last,
                    })
        except Exception:
            pass
    return result


def disconnect():
    """Shutdown MT5 connection."""
    global _MT5_INITIALIZED
    if _MT5_INITIALIZED:
        try:
            mt5.shutdown()
        except Exception:
            pass
        _MT5_INITIALIZED = False
