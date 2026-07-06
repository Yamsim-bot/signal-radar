#!/usr/bin/env python3
"""
GBPJPY Cent Raw ECN — Realistic Automated Strategy v2
Platform: MetaTrader 5 (Vantage Cent Raw ECN)
Capital:  $500
Target:   ~$25–$50/month ($12–$25 bi-weekly)
Risk:     1% per trade, circuit breakers on loss streak / daily loss / drawdown

Features:
  - EMA 20/50 trend + RSI(7) momentum entry
  - Partial TP at 1:1 (50%) + trailing stop on remaining
  - News filter: blocks trading before high-impact events,
    then enables momentum-mode entries after they settle
  - No-revenge: 2 consecutive SLs = locked for the day
  - Session filter: London peak only (08:00–15:00 GMT)

Commission: $0.06 round-turn per cent lot (Vantage Raw ECN)

Usage:
    python gbpjpy_mt5_ea.py                        # Live mode
    python gbpjpy_mt5_ea.py "C:\MT5\terminal64.exe" # Custom path
    python gbpjpy_mt5_ea.py --backtest              # Paper backtest
    python gbpjpy_mt5_ea.py --news-only             # Print today's news then exit
    python gbpjpy_mt5_ea.py --help                  # Help
"""

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import time
import json
import os
import sys
import re
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dataclasses import dataclass, asdict
from html.parser import HTMLParser

# ─────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────
@dataclass
class Config:
    # Account
    account_type: str = "cent"
    initial_capital: float = 500.0

    # Risk
    risk_per_trade_pct: float = 1.0
    max_daily_loss_pct: float = 5.0
    max_total_drawdown_pct: float = 20.0
    max_consecutive_losses: int = 2
    max_spread_pips: float = 2.0

    # Strategy
    symbol: str = "GBPJPY"
    timeframe: int = mt5.TIMEFRAME_M15
    fast_ema: int = 20
    slow_ema: int = 50
    rsi_period: int = 7
    rsi_threshold: int = 30
    atr_period: int = 14
    atr_sl_multiplier: float = 1.5
    rr_target: float = 2.0
    min_stop_pips: float = 20.0

    # Partial TP & trail
    partial_tp_pct: float = 50.0     # % at 1:1 RR
    trail_atr_mul: float = 0.75

    # Session — peak GBPJPY hours (GMT)
    session_start_hour: int = 8
    session_end_hour: int = 15
    trade_direction: str = "both"

    # News filter
    use_news_filter: bool = True
    block_before_news_min: int = 45
    wait_after_news_min: int = 15
    news_momentum_window_min: int = 30  # post-news momentum entry window

    # Known high-impact event hours (GMT) — used as fallback when ForexFactory
    # is unreachable. All times in HH:MM GMT.
    fallback_news_events: tuple = (
        # UK data (CPI, GDP, Employment, Retail Sales)
        (7, 0, "UK data"),
        # BOE decision / minutes
        (12, 0, "BOE / UK data"),
        # US data (NFP, CPI, PPI, GDP, Durable Goods)
        (13, 30, "US data"),
        (14, 0, "US data (alt)"),
    )

    # Commission
    commission_per_cent_lot_round: float = 0.06

    # Monitoring
    check_interval_sec: int = 60
    journal_file: str = "gbpjpy_journal.json"

    # News cache
    news_cache_file: str = "gbpjpy_news_cache.json"
    news_cache_ttl_hours: int = 6


# ─────────────────────────────────────────────────────────
# FOREXFACTORY NEWS CALENDAR
# ─────────────────────────────────────────────────────────
class ForexFactoryParser(HTMLParser):
    """Minimal HTML parser for the ForexFactory calendar page.
    Extracts: time (GMT), currency, impact (high/medium/low), event name.
    """
    def __init__(self):
        super().__init__()
        self.events = []
        self._in_row = False
        self._in_cell = False
        self._cell_type = None
        self._row_data = {}
        self._depth = 0
        self._text_buf = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        classes = attrs_dict.get("class", "")

        if tag == "tr" and "calendar__row" in classes and "calendar__row--day" not in classes:
            # Skip the day-header rows, only take event rows
            bg = attrs_dict.get("style", "")
            if "background" not in bg:
                self._in_row = True
                self._row_data = {}
                self._cell_type = None

        if self._in_row and tag == "td":
            # Determine cell type from class
            if "calendar__time" in classes:
                self._cell_type = "time"
            elif "calendar__currency" in classes:
                self._cell_type = "currency"
            elif "calendar__impact" in classes:
                self._cell_type = "impact"
            elif "calendar__event" in classes:
                self._cell_type = "event"
            else:
                self._cell_type = None

            if self._cell_type:
                self._in_cell = True
                self._text_buf = ""

    def handle_endtag(self, tag):
        if tag == "tr" and self._in_row:
            # Check if we have enough data and it's high-impact
            if all(k in self._row_data for k in ("time", "currency", "impact", "event")):
                imp = self._row_data["impact"].lower()
                if "high" in imp or "h" in imp:
                    currency = self._row_data["currency"].strip()
                    # Only keep events that affect GBPJPY
                    if currency in ("GBP", "JPY", "USD"):
                        self.events.append({
                            "time_str": self._row_data["time"],
                            "currency": currency,
                            "event": self._row_data["event"].strip(),
                        })
            self._in_row = False

        if self._in_cell and tag == "td":
            self._in_cell = False
            self._cell_type = None

    def handle_data(self, data):
        if self._in_cell and self._cell_type:
            text = data.strip()
            if text:
                if self._cell_type == "time":
                    # Calendar shows time like "07:00" or "All Day" or "Day 1"
                    # Remove the timezone suffix (it's always GMT on ForexFactory)
                    self._row_data["time"] = text.split("(")[0].strip()[:5]
                elif self._cell_type == "currency":
                    self._row_data["currency"] = text
                elif self._cell_type == "impact":
                    # Impact is in a span inside td, might not come via text
                    pass
                elif self._cell_type == "event":
                    self._row_data["event"] = text

    def handle_startendtag(self, tag, attrs):
        # <span> with impact indicator - ForexFactory uses a red/amber/grey dot
        if self._in_cell and self._cell_type == "impact" and tag == "span":
            attrs_dict = dict(attrs)
            cls = attrs_dict.get("class", "")
            if "high" in cls:
                self._row_data["impact"] = "high"
            elif "medium" in cls:
                self._row_data["impact"] = "medium"
            elif "low" in cls:
                self._row_data["impact"] = "low"


def fetch_forexfactory_events(date: datetime) -> list[dict]:
    """Fetch high-impact events for a given date from ForexFactory.
    Returns list of {time_str, currency, event}.
    On failure returns empty list (caller falls back to schedule).
    """
    date_str = date.strftime("%Y-%m-%d")
    url = f"https://www.forexfactory.com/calendar?day={date_str}"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
    }

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        parser = ForexFactoryParser()
        parser.feed(html)
        return parser.events
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, Exception) as e:
        print(f"   ⚠️  ForexFactory unreachable: {e}")
        return []


def get_news_events(cfg: Config, today: datetime = None) -> list[dict]:
    """Get today's high-impact news events.
    Tries ForexFactory first, falls back to schedule.
    Caches the result locally.
    """
    if today is None:
        today = datetime.utcnow()

    cache_path = Path(cfg.news_cache_file)
    events = []

    # Try loading from cache
    now_ts = time.time()
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            if cached.get("date") == today.strftime("%Y-%m-%d"):
                age_hours = (now_ts - cached.get("fetched_at", 0)) / 3600
                if age_hours < cfg.news_cache_ttl_hours:
                    events = cached.get("events", [])
                    if events:
                        print(f"   📰 Loaded {len(events)} news events from cache")
        except (json.JSONDecodeError, OSError):
            pass

    # Fetch if cache miss
    if not events:
        print(f"   📰 Fetching ForexFactory calendar for {today:%Y-%m-%d}...")
        fetched = fetch_forexfactory_events(today)

        if fetched:
            events = fetched
            # Cache it
            try:
                cache_path.write_text(json.dumps({
                    "date": today.strftime("%Y-%m-%d"),
                    "fetched_at": now_ts,
                    "events": events,
                }, indent=2))
            except OSError:
                pass
        else:
            # Fallback: schedule-based
            print(f"   📰 Fallback: using schedule-based news filter")
            for h, m, label in cfg.fallback_news_events:
                events.append({"time_str": f"{h:02d}:{m:02d}", "currency": label[:3], "event": label})

    return events


def parse_news_time(time_str: str) -> tuple[float, float | None]:
    """Parse a ForexFactory time string to (start_hours, end_hours_optional).
    Returns (hours_from_midnight, hours_to_midnight_or_None).
    """
    time_str = time_str.strip()

    # "All Day"
    if time_str.lower() == "all day":
        return 0.0, 24.0

    # "Day 1", "Day 2", etc. — skip these, return None
    if time_str.lower().startswith("day"):
        return None, None

    # "07:00" -> (7.0, None)
    m = re.match(r"^(\d{1,2}):(\d{2})", time_str)
    if m:
        hours = int(m.group(1)) + int(m.group(2)) / 60.0
        return hours, None

    # Can't parse — skip
    return None, None


def get_news_blackout_windows(cfg: Config, events: list[dict]) -> list[tuple]:
    """Convert news events to (blackout_start_hours, blackout_end_hours) tuples.
    Each tuple covers the block-before + event-duration + post-news wait.
    Times are in hours since midnight GMT.
    """
    windows = []
    now = datetime.utcnow()
    current_hours = now.hour + now.minute / 60.0

    for ev in events:
        start_h, end_h = parse_news_time(ev["time_str"])
        if start_h is None:
            continue

        block_min = cfg.block_before_news_min / 60.0   # hours before
        wait_min  = cfg.wait_after_news_min / 60.0      # hours after
        momentum_min = cfg.news_momentum_window_min / 60.0

        win_start = start_h - block_min
        win_end   = start_h + wait_min + momentum_min   # includes momentum window
        windows.append((win_start, win_end, ev["currency"], ev["event"], start_h))

    return windows


def is_in_news_blackout(cfg: Config, events: list[dict]) -> tuple:
    """Check if current time is in a news blackout window.
    Returns (in_blackout: bool, event_name: str, mode: str).
    mode is "before", "during", "momentum", or "".
    """
    if not cfg.use_news_filter or not events:
        return False, "", ""

    now = datetime.utcnow()
    current_hours = now.hour + now.minute / 60.0

    for ev in events:
        start_h, end_h = parse_news_time(ev["time_str"])
        if start_h is None:
            continue

        block_h = cfg.block_before_news_min / 60.0
        wait_h  = cfg.wait_after_news_min / 60.0
        momentum_h = cfg.news_momentum_window_min / 60.0

        blackout_start = start_h - block_h
        blackout_end   = start_h + wait_h
        momentum_end   = start_h + wait_h + momentum_h

        if blackout_start <= current_hours < start_h:
            return True, ev["event"], "before"

        if start_h <= current_hours < blackout_end:
            return True, ev["event"], "during"

        if blackout_end <= current_hours < momentum_end:
            return False, ev["event"], "momentum"

    return False, "", ""


# ─────────────────────────────────────────────────────────
# STRATEGY CORE
# ─────────────────────────────────────────────────────────
class GBPJPYStrategy:
    """EMA 20/50 trend + RSI(7) momentum with partial TP and trailing."""

    def __init__(self, config: Config):
        self.cfg = config
        self.journal = []
        self._start_equity = config.initial_capital
        self._day_start_equity = config.initial_capital
        self._last_check_day = 0
        self._consecutive_losses = 0
        self._last_trade_count = 0
        self._circuit_breaker = False
        self._breaker_reason = ""

        # Trade management tracking
        self._entry_price = 0.0
        self._initial_lots = 0
        self._partial_hit = False
        self._trail_stop = 0.0

    def compute_signals(self, rates: pd.DataFrame, in_momentum: bool = False) -> dict:
        """Return signal dict. `in_momentum` relaxes entry threshold."""
        close = rates['close'].values
        high  = rates['high'].values
        low   = rates['low'].values

        # EMAs
        fast_ema = pd.Series(close).ewm(span=self.cfg.fast_ema).mean().values
        slow_ema = pd.Series(close).ewm(span=self.cfg.slow_ema).mean().values

        # RSI
        delta = np.diff(close, prepend=close[0])
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)
        avg_gain = pd.Series(gain).ewm(span=self.cfg.rsi_period).mean().values
        avg_loss = pd.Series(loss).ewm(span=self.cfg.rsi_period).mean().values
        rs = avg_gain / np.where(avg_loss == 0, 0.001, avg_loss)
        rsi = 100 - (100 / (1 + rs))

        # ATR
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(
                abs(high[1:] - close[:-1]),
                abs(low[1:] - close[:-1])
            )
        )
        atr = np.concatenate([
            [np.mean(tr[:self.cfg.atr_period])],
            pd.Series(tr).ewm(span=self.cfg.atr_period).mean().values
        ])
        if len(atr) < len(close):
            atr = np.concatenate([atr, [atr[-1]] * (len(close) - len(atr))])

        i = -1
        uptrend   = fast_ema[i] > slow_ema[i]
        downtrend = fast_ema[i] < slow_ema[i]
        rsi_prev  = rsi[i-1] if i > 0 else rsi[i]
        rsi_curr  = rsi[i]
        rsi_os    = self.cfg.rsi_threshold
        rsi_ob    = 100 - self.cfg.rsi_threshold

        # Normal entry
        long_signal  = uptrend and rsi_prev <= rsi_os and rsi_curr > rsi_os
        short_signal = downtrend and rsi_prev >= rsi_ob and rsi_curr < rsi_ob

        # Momentum window: relaxed RSI threshold (enter trend earlier)
        if in_momentum:
            rsi_os_m = rsi_os + 10
            rsi_ob_m = rsi_ob - 10
            long_signal  = long_signal or (uptrend and rsi_prev <= rsi_os_m and rsi_curr > rsi_os_m)
            short_signal = short_signal or (downtrend and rsi_prev >= rsi_ob_m and rsi_curr < rsi_ob_m)

        # Direction filter
        dir_map = {'long': [True, False], 'short': [False, True], 'both': [True, True]}
        df = dir_map.get(self.cfg.trade_direction, [True, True])
        long_signal  = long_signal and df[0]
        short_signal = short_signal and df[1]

        result = {'direction': None, 'sl_pips': 0, 'tp_pips': 0, 'size_lots': 0, 'reasons': []}
        if not (long_signal or short_signal):
            result['reasons'].append('No signal' if not in_momentum else 'No momentum signal')
            return result

        sl_pips  = max(atr[i] * self.cfg.atr_sl_multiplier / 0.01, self.cfg.min_stop_pips)
        tp_pips  = sl_pips * self.cfg.rr_target

        direction = 'long' if long_signal else 'short'
        reasons = []
        if in_momentum:
            reasons.append('Post-news momentum')
        reasons.append('Trend ' + ('up' if direction == 'long' else 'down'))
        reasons.append(f'RSI: {rsi_curr:.0f}')

        result['direction'] = direction
        result['sl_pips']   = round(sl_pips, 1)
        result['tp_pips']   = round(tp_pips, 1)
        result['reasons']   = reasons
        return result

    def compute_position_size(self, equity: float, price: float, sl_pips: float) -> int:
        risk_usd = equity * self.cfg.risk_per_trade_pct / 100.0
        pip_val  = 0.01 * (1000 / price)   # per cent lot
        raw_lots = risk_usd / (sl_pips * pip_val)
        return max(1, min(int(raw_lots), 100))

    def check_circuit_breakers(self, account, open_positions: list, day: int) -> tuple:
        """Returns (allowed, reason)."""
        if self._circuit_breaker:
            return False, self._breaker_reason

        equity = account.equity
        open_pnl = sum(p.profit for p in open_positions)
        current_value = equity + open_pnl

        # Track consecutive losses from MT5 position history
        if day != self._last_check_day:
            self._day_start_equity = current_value
            self._last_check_day = day
            # Reset consecutive losses on new day... actually no,
            # the rule says "if 2 consecutive SLs, stop for the day"
            # but a win breaks the streak, and it resets at new day.
            # Let's reset on new day:
            self._consecutive_losses = 0

        daily_loss_pct = (self._day_start_equity - current_value) / max(self._day_start_equity, 1) * 100
        total_dd_pct   = (self.cfg.initial_capital - equity) / self.cfg.initial_capital * 100

        # Check consecutive losses from trade history
        self._update_consecutive_losses(account)

        if self._consecutive_losses >= self.cfg.max_consecutive_losses:
            self._circuit_breaker = True
            self._breaker_reason = f"{self.cfg.max_consecutive_losses} SL hits — locked for day"
            return False, self._breaker_reason

        if daily_loss_pct >= self.cfg.max_daily_loss_pct:
            self._circuit_breaker = True
            self._breaker_reason = f"Daily loss: {daily_loss_pct:.1f}%"
            return False, self._breaker_reason

        if total_dd_pct >= self.cfg.max_total_drawdown_pct:
            self._circuit_breaker = True
            self._breaker_reason = f"Max DD: {total_dd_pct:.1f}%"
            return False, self._breaker_reason

        return True, ""

    def _update_consecutive_losses(self, account):
        """Count consecutive losing closed trades."""
        if account is None:
            return
        try:
            # Get last N closed deals
            from datetime import datetime as dt
            history = mt5.history_deals_get(
                dt(2020, 1, 1),
                dt.utcnow()
            )
            if history and len(history) > self._last_trade_count:
                # Check recent closed trades
                sorted_history = sorted(history, key=lambda d: d.time, reverse=True)
                count = 0
                for deal in sorted_history:
                    if deal.symbol != self.cfg.symbol:
                        continue
                    if deal.profit < 0:
                        count += 1
                    else:
                        break  # win breaks streak
                self._consecutive_losses = count
                self._last_trade_count = len(history)
        except Exception:
            pass  # MT5 history read can fail silently


# ─────────────────────────────────────────────────────────
# MT5 BRIDGE
# ─────────────────────────────────────────────────────────
class MT5Bridge:
    def __init__(self, config: Config):
        self.cfg = config
        self.connected = False

    def connect(self, path: str = None) -> bool:
        if not mt5.initialize(path=path):
            print(f"❌ MT5 init: {mt5.last_error()}")
            return False
        info = mt5.terminal_info()
        if info is None:
            print(f"❌ Terminal info: {mt5.last_error()}")
            return False
        account = mt5.account_info()
        if account is None:
            print(f"❌ Account info: {mt5.last_error()}")
            return False
        self.connected = True
        print(f"✅ Connected to {account.server} | ${account.balance:.2f}")

        si = mt5.symbol_info(self.cfg.symbol)
        if si is None:
            print(f"❌ {self.cfg.symbol} not found")
            mt5.shutdown()
            return False
        if not si.trade_mode:
            mt5.symbol_select(self.cfg.symbol, True)
        print(f"   Spread: {si.spread * 0.01:.1f} pips")
        return True

    def disconnect(self):
        if self.connected:
            mt5.shutdown()
            self.connected = False

    def get_rates(self, bars: int = 100) -> pd.DataFrame | None:
        rates = mt5.copy_rates_from_pos(self.cfg.symbol, self.cfg.timeframe, 0, bars)
        if rates is None or len(rates) < self.cfg.slow_ema + 5:
            return None
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df.set_index('time', inplace=True)
        return df

    def in_session(self, now: datetime = None) -> bool:
        if now is None:
            now = datetime.utcnow()
        h = now.hour
        return self.cfg.session_start_hour <= h < self.cfg.session_end_hour

    def get_spread(self) -> float:
        info = mt5.symbol_info(self.cfg.symbol)
        return info.spread * 0.01 if info else 99.9

    def get_positions(self) -> list:
        return list(mt5.positions_get(symbol=self.cfg.symbol) or [])

    def send_order(self, direction: str, size_lots: int, sl_pips: float, tp_pips: float,
                   price: float, reasons: list) -> dict:
        ot = mt5.ORDER_TYPE_BUY if direction == 'long' else mt5.ORDER_TYPE_SELL
        sl = price - sl_pips * 0.01 if direction == 'long' else price + sl_pips * 0.01
        tp = price + tp_pips * 0.01 if direction == 'long' else price - tp_pips * 0.01
        vol = size_lots / 100.0

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.cfg.symbol,
            "volume": vol,
            "type": ot,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 10,
            "magic": 20240706,
            "comment": f"GBPJPY {direction[0].upper()}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(req)
        if result is None:
            return {"success": False, "error": str(mt5.last_error())}
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return {"success": False, "retcode": result.retcode, "comment": result.comment}

        print(f"✅ {direction.upper()} {size_lots} lots @ {price:.3f} | SL: {sl:.3f} TP1: {tp:.3f}")
        return {"success": True, "order": result.order, "price": price, "volume": vol, "sl": sl, "tp": tp}

    def modify_position(self, ticket: int, sl: float = None, tp: float = None) -> bool:
        req = {"position": ticket, "symbol": self.cfg.symbol}
        if sl is not None:
            req["sl"] = sl
        if tp is not None:
            req["tp"] = tp
        result = mt5.order_send({**req, "action": mt5.TRADE_ACTION_SLTP})
        return result is not None and result.retcode == mt5.TRADE_RETCODE_DONE

    def close_position_partial(self, ticket: int, volume: float) -> dict:
        position = mt5.positions_get(ticket=ticket)
        if not position:
            return {"success": False, "error": "Position not found"}
        pos = position[0]
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.cfg.symbol,
            "volume": volume,
            "type": mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY,
            "position": ticket,
            "price": mt5.symbol_info_tick(self.cfg.symbol).ask if pos.type == mt5.ORDER_TYPE_BUY else mt5.symbol_info_tick(self.cfg.symbol).bid,
            "deviation": 10,
            "magic": 20240706,
            "comment": "Partial TP",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(req)
        if result is None:
            return {"success": False, "error": str(mt5.last_error())}
        return {"success": result.retcode == mt5.TRADE_RETCODE_DONE, "retcode": result.retcode}


# ─────────────────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────────────────
def main():
    cfg = Config()
    strategy = GBPJPYStrategy(cfg)
    bridge = MT5Bridge(cfg)

    if "--help" in sys.argv:
        print(__doc__)
        return

    if "--news-only" in sys.argv:
        print(f"\n📰 Today's high-impact events ({datetime.utcnow():%Y-%m-%d}):")
        events = get_news_events(cfg)
        if events:
            for ev in events:
                print(f"   {ev['time_str']:>8s}  {ev['currency']:4s}  {ev['event']}")
        else:
            print("   No events or unable to fetch calendar.")
        return

    if "--backtest" in sys.argv:
        run_backtest(cfg, strategy)
        return

    # Connect
    mt5_path = None
    for arg in sys.argv[1:]:
        if not arg.startswith("--"):
            mt5_path = arg
            break

    if not bridge.connect(path=mt5_path):
        input("Press Enter to exit...")
        return

    # Pre-load news
    news_events = []
    if cfg.use_news_filter:
        print("   Loading news calendar...")
        news_events = get_news_events(cfg)
        if news_events:
            for ev in news_events:
                print(f"   📰 {ev['time_str']:>8s}  {ev['currency']:4s}  {ev['event'][:50]}")

    try:
        print(f"\n🚀 GBPJPY Cent EA v2 — Checking every {cfg.check_interval_sec}s")
        print(f"   Risk: {cfg.risk_per_trade_pct}%/trade | Max consec losses: {cfg.max_consecutive_losses}")
        print(f"   Session: {cfg.session_start_hour}:00–{cfg.session_end_hour}:00 GMT")
        print(f"   News filter: {'ON' if cfg.use_news_filter else 'OFF'}")
        print(f"   Press Ctrl+C to stop\n")

        # Per-trade tracking
        trade_active = False
        entry_price = 0.0
        initial_lots = 0
        partial_hit = False
        trail_stop = 0.0

        while True:
            now = datetime.utcnow()
            day = now.day
            account = mt5.account_info()
            if account is None:
                time.sleep(30)
                continue

            # --- Circuit breakers ---
            positions = bridge.get_positions()
            allowed, reason = strategy.check_circuit_breakers(account, positions, day)
            if not allowed:
                print(f"[{now:%H:%M}] ⛔ {reason}")
                time.sleep(300)
                continue

            # --- News filter ---
            in_blackout, ev_name, mode = is_in_news_blackout(cfg, news_events)
            if in_blackout:
                print(f"[{now:%H:%M}] 📰 News blackout ({ev_name}) — skipping")
                time.sleep(cfg.check_interval_sec)
                continue

            in_momentum = mode == "momentum"
            if in_momentum and trade_active:
                # Already in a trade during momentum window
                pass

            # --- Trade management (partial TP + trail) ---
            if trade_active and positions:
                pos = positions[0]
                current_price = mt5.symbol_info_tick(cfg.symbol).bid if pos.type == mt5.ORDER_TYPE_BUY else mt5.symbol_info_tick(cfg.symbol).ask

                if not partial_hit:
                    # Check if price hit the 1:1 level
                    pip_dist = (current_price - entry_price) / 0.01 if pos.type == mt5.ORDER_TYPE_BUY else (entry_price - current_price) / 0.01
                    sl_pips_local = max(account.equity * cfg.risk_per_trade_pct / 100.0 / (0.01 * (1000 / current_price)) / initial_lots, cfg.min_stop_pips)

                    if pip_dist >= sl_pips_local:
                        # Close 50%
                        half_vol = pos.volume / 2
                        result = bridge.close_position_partial(pos.ticket, half_vol)
                        if result.get("success"):
                            partial_hit = True
                            trail_stop = entry_price + 0.5 * 0.01  # breakeven + small buffer
                            if pos.type == mt5.ORDER_TYPE_SELL:
                                trail_stop = entry_price - 0.5 * 0.01
                            print(f"   ✂️ Partial TP hit — closed 50% @ {current_price:.3f}, moving to breakeven+")
                            # Update SL on remaining
                            bridge.modify_position(pos.ticket, sl=trail_stop)
                else:
                    # Trail remaining position
                    if pos.type == mt5.ORDER_TYPE_BUY:
                        new_stop = current_price - max(cfg.trail_atr_mul * 0.01, cfg.min_stop_pips * 0.01 * 0.5)  # simplified
                        if new_stop > trail_stop:
                            trail_stop = new_stop
                            bridge.modify_position(pos.ticket, sl=round(trail_stop, 3))
                    else:
                        new_stop = current_price + max(cfg.trail_atr_mul * 0.01, cfg.min_stop_pips * 0.01 * 0.5)
                        if new_stop < trail_stop or trail_stop == 0:
                            trail_stop = new_stop
                            bridge.modify_position(pos.ticket, sl=round(trail_stop, 3))

            if positions:
                # Position exists — log and wait
                pos = positions[0]
                print(f"[{now:%H:%M}] Position: {pos.volume*100:.0f} lots | P&L: ${pos.profit:.2f}{' 📰' if in_momentum else ''}")
                time.sleep(cfg.check_interval_sec)
                continue

            # --- Session filter ---
            if not bridge.in_session(now):
                time.sleep(60)
                continue

            # --- Spread ---
            if bridge.get_spread() > cfg.max_spread_pips:
                time.sleep(cfg.check_interval_sec)
                continue

            # --- Get signal ---
            rates = bridge.get_rates(bars=100)
            if rates is None:
                time.sleep(cfg.check_interval_sec)
                continue

            signal = strategy.compute_signals(rates, in_momentum=in_momentum)
            if signal['direction'] is None:
                time.sleep(cfg.check_interval_sec)
                continue

            price = rates['close'].iloc[-1]
            lots = strategy.compute_position_size(account.equity, price, signal['sl_pips'])

            print(f"[{now:%H:%M}] 📊 Signal: {signal['direction'].upper()}"
                  f"  Size: {lots} lots  SL: {signal['sl_pips']}  TP: {signal['tp_pips']}"
                  f"{' 📰Post' if in_momentum else ''}")

            result = bridge.send_order(signal['direction'], lots,
                                       signal['sl_pips'], signal['tp_pips'],
                                       price, signal['reasons'])

            # Journal
            entry = {
                'time': now.isoformat(),
                'signal': signal,
                'news_mode': 'momentum' if in_momentum else 'normal',
                'equity': account.equity,
                'result': result,
            }
            strategy.journal.append(entry)
            with open(cfg.journal_file, 'w') as f:
                json.dump(strategy.journal, f, indent=2)

            if result.get('success'):
                trade_active = True
                entry_price = price
                initial_lots = lots
                partial_hit = False
                trail_stop = 0.0
            else:
                trade_active = False

            time.sleep(cfg.check_interval_sec)

    except KeyboardInterrupt:
        print("\n🛑 Stopped")
    finally:
        bridge.disconnect()
        pnl = (mt5.account_info().equity if mt5.account_info() else 0) - cfg.initial_capital
        print(f"\n📊 P&L: ${pnl:+.2f}  Trades: {len(strategy.journal)}")
        if strategy.journal:
            placed = sum(1 for j in strategy.journal if j['result'].get('success'))
            print(f"   Filled: {placed}  Missed: {len(strategy.journal) - placed}")


# ─────────────────────────────────────────────────────────
# BACKTEST
# ─────────────────────────────────────────────────────────
def run_backtest(cfg: Config, strategy: GBPJPYStrategy):
    print("📊 Quick backtest on last 500 candles...\n")
    if not mt5.initialize():
        print(f"❌ MT5 init: {mt5.last_error()}")
        return
    mt5.symbol_select(cfg.symbol, True)
    rates = mt5.copy_rates_from_pos(cfg.symbol, cfg.timeframe, 0, 500)
    mt5.shutdown()
    if rates is None:
        print("❌ No data")
        return

    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('time', inplace=True)

    equity = cfg.initial_capital
    trades = []
    streak = 0

    start_idx = max(cfg.slow_ema + cfg.atr_period + 10, 60)

    for i in range(start_idx, len(df)):
        chunk = df.iloc[:i+1]
        bar_time = df.index[i]

        # Session filter
        h = bar_time.hour
        if not (cfg.session_start_hour <= h < cfg.session_end_hour):
            continue

        # Simplified news filter (schedule-based for backtest)
        if cfg.use_news_filter:
            in_news = False
            for ev_h, ev_m, _ in cfg.fallback_news_events:
                ev_min = ev_h * 60 + ev_m
                bar_min = h * 60 + bar_time.minute
                if ev_min - cfg.block_before_news_min <= bar_min <= ev_min + cfg.wait_after_news_min:
                    in_news = True
                    break
            if in_news:
                continue

        # Circuit breaker
        if streak >= cfg.max_consecutive_losses:
            streak = 0  # reset next day
            continue

        signal = strategy.compute_signals(chunk)
        if signal['direction'] is None:
            continue

        price = chunk['close'].iloc[-1]
        lots = strategy.compute_position_size(equity, price, signal['sl_pips'])
        sl_dist = signal['sl_pips'] * 0.01
        tp_pips_1 = signal['sl_pips'] * 1.0  # 1:1 for partial
        tp_pips_2 = signal['tp_pips']        # full target

        # Walk forward to see what happens
        hit_sl = hit_tp1 = False
        for j in range(1, min(50, len(df) - i - 1)):
            bar_low = df.iloc[i+j]['low']
            bar_high = df.iloc[i+j]['high']

            if signal['direction'] == 'long':
                if bar_low <= price - sl_dist:
                    hit_sl = True
                    break
                if bar_high >= price + tp_pips_1 * 0.01:
                    hit_tp1 = True
                    # Check if the remaining hits TP2
                    for k in range(j+1, min(50, len(df) - i - 1)):
                        bk = df.iloc[i+k]
                        if bk['low'] <= price:  # breakeven
                            hit_sl = True
                            break
                        if bk['high'] >= price + tp_pips_2 * 0.01:
                            break
                    break
            else:
                if bar_high >= price + sl_dist:
                    hit_sl = True
                    break
                if bar_low <= price - tp_pips_1 * 0.01:
                    hit_tp1 = True
                    break

        commission = lots * cfg.commission_per_cent_lot_round
        pip_val = 0.01 * (1000 / price)

        if hit_sl:
            gross = -signal['sl_pips'] * pip_val * lots
        elif hit_tp1:
            # Partial TP: 50% at 1:1, rest hit breakeven/trail exit
            pnl_1 = tp_pips_1 * pip_val * (lots * 0.5)
            pnl_2 = 0.0  # breakeven on remaining after partial
            gross = pnl_1 + pnl_2
        else:
            gross = signal['tp_pips'] * pip_val * lots

        net = gross - commission
        equity += net

        streak = streak + 1 if net < 0 else 0

        trades.append({
            'time': str(bar_time),
            'dir': signal['direction'],
            'lots': lots,
            'entry': price,
            'pnl': round(net, 2),
            'reason': 'SL' if hit_sl else ('partial TP' if hit_tp1 else 'full TP'),
        })

    if not trades:
        print("No trades in backtest period.")
        return

    wins  = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    wr = len(wins) / len(trades) * 100
    total = sum(t['pnl'] for t in trades)
    avg_win = np.mean([t['pnl'] for t in wins]) if wins else 0
    avg_loss = np.mean([t['pnl'] for t in losses]) if losses else 0
    profit_factor = abs(sum(t['pnl'] for t in wins) / max(abs(sum(t['pnl'] for t in losses)), 0.01))

    print(f"{'='*55}")
    print(f"  GBPJPY Cent v2 — Backtest")
    print(f"{'='*55}")
    print(f"  Period:   {df.index[0].date()} → {df.index[-1].date()}")
    print(f"  Trades:   {len(trades)}  (W: {len(wins)} L: {len(losses)})")
    print(f"  Win rate: {wr:.1f}%")
    print(f"  Net P&L:  ${total:+.2f} ({total/cfg.initial_capital*100:+.1f}%)")
    print(f"  Final eq: ${equity:.2f}")
    print(f"  Avg W/L:  ${avg_win:+.2f} / ${avg_loss:+.2f}")
    print(f"  PF:       {profit_factor:.2f}")
    print(f"  Comm:     ~${commission:.3f}/trade")


if __name__ == "__main__":
    main()
