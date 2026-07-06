#!/usr/bin/env python3
"""
XAUUSD Cent Raw ECN — Deployment Script
========================================
Patches mate_scalper_pro.py for Vantage Cent Raw ECN at runtime.
Fixes: symbol detection, daily_losses hardcode, cent config, verbose logging.

Usage:
    python deploy_xauusd_cent.py                    # Run 24/7
    python deploy_xauusd_cent.py --hours 8          # Run 8 hours
    python deploy_xauusd_cent.py --status           # Diagnostic
    python deploy_xauusd_cent.py --find-symbol      # Auto-detect symbol
"""
import sys, os, json, time, logging
from datetime import datetime, timedelta, date
from pathlib import Path

# Ensure we can import mate_scalper_pro
HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import MetaTrader5 as mt5
import mate_scalper_pro as msp

# ─────────────────────────────────────────────
# CENT ACCOUNT CONFIG
# ─────────────────────────────────────────────
# Vantage Cent Raw ECN specs for XAUUSD:
#   1 cent lot = 1 oz (0.01 standard lot)
#   Commission: $0.06 round-trip per cent lot
#   Pip value:  $0.10 per pip per cent lot
# ─────────────────────────────────────────────
CENT_OVERRIDES = {
    # Broker costs
    "commission_per_lot_rt":      0.06,       # $0.06 per cent lot RT
    "typical_spread_pips":        1.5,        # XAUUSD spread wider than FX
    "slippage_pips":              0.5,
    # Account
    "initial_capital":            500.0,
    "risk_pct":                   1.0,
    # XAUUSD pip specs
    "pip_value":                  0.01,
    "usd_per_pip_per_lot":        0.10,       # per cent lot
    "lot_step":                   0.01,
    "min_lot":                    0.01,
    "max_lot":                    50.0,
    # Entry / Filter (slightly looser for gold)
    "adx_threshold":              20,
    "min_pillars":                2,
    "min_atr_for_trade":          2.0,        # XAUUSD ATR on M5 is ~$3-8
    "low_vol_pillars":            3,
    "low_vol_adx":                22,
    # Circuit breakers
    "max_trades_per_day":         8,
    "max_consecutive_losses":     2,
    "max_daily_loss_pct":         5.0,
    "max_drawdown_pct":           20.0,
    # News
    "use_news_filter":            True,
    "news_minutes_before":        45,
    "news_minutes_after":         15,
    # Trailing
    "use_trailing_stop":          True,
    "use_breakeven":              True,
    "trail_activation":           0.4,
    "trail_distance":             1.0,
    "trail_step":                 0.15,
    # Regime
    "use_regime_filter":          True,
    "use_stealth_mode":           True,
}

LOG_DIR = HERE / "logs"
LOG_DIR.mkdir(exist_ok=True)


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_DIR / "cent_scalper.log", "a") as f:
        f.write(f"[{ts}] {msg}\n")
    print(f"  [{ts}] {msg}")


# ─────────────────────────────────────────────
# SYMBOL AUTO-DETECTION
# ─────────────────────────────────────────────
def find_xauusd_symbol() -> str | None:
    """Try common XAUUSD symbol names until one works."""
    candidates = ["XAUUSD", "XAUUSD+", "XAUUSDc", "XAUUSD.c", "GOLD", "XAUUSD.f", "XAUUSDm"]
    for sym in candidates:
        info = mt5.symbol_info(sym)
        if info:
            tick = mt5.symbol_info_tick(sym)
            if tick:
                return sym
    return None


# ─────────────────────────────────────────────
# PATCHED LiveScalper
# ─────────────────────────────────────────────
class CentLiveScalper(msp.LiveScalper):
    """Fixed version for cent accounts with verbose logging."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.symbol = None
        self._diagnostic_log: list[str] = []

    # ── Fix 1: Symbol auto-detect ──
    def ensure_symbol(self) -> bool:
        """Find the right XAUUSD symbol. Returns True if found."""
        if self.symbol:
            return True
        found = find_xauusd_symbol()
        if found:
            self.symbol = found
            log(f"✅ Symbol detected: {self.symbol}")
            # Enable trading
            mt5.symbol_select(self.symbol, True)
            return True
        log("❌ No XAUUSD symbol found in Market Watch")
        return False

    # ── Fix 2: get_signal with verbose logging + fixed daily_losses ──
    def get_signal(self) -> dict | None:
        if not self.check_connection():
            self._log_skip("MT5 not connected")
            return None

        # Ensure symbol
        if not self.ensure_symbol():
            self._log_skip("No symbol")
            return None

        # Fetch data using detected symbol
        rates_m5 = mt5.copy_rates_from(self.symbol, mt5.TIMEFRAME_M5, datetime.now(), 200)
        rates_m15 = mt5.copy_rates_from(self.symbol, mt5.TIMEFRAME_M15, datetime.now(), 200)

        if rates_m5 is None or len(rates_m5) < 100:
            self._log_skip(f"Not enough M5 data: {len(rates_m5) if rates_m5 is not None else 0}")
            return None
        if rates_m15 is None or len(rates_m15) < 100:
            self._log_skip(f"Not enough M15 data: {len(rates_m15) if rates_m15 is not None else 0}")
            return None

        df5 = _rates_to_df(rates_m5)
        df15 = _rates_to_df(rates_m15)
        df5 = msp.compute_indicators(df5)
        df15 = msp.compute_indicators(df15)

        # ── News filter ──
        if self.news_filter:
            blocked, reason = self.news_filter.is_trading_blocked()
            if blocked:
                self._log_skip(f"News: {reason}")
                return None

        # ── Daily limits ──
        today = date.today()
        if today != self.current_day:
            self.current_day = today
            self.daily_trades = 0
            self.daily_losses = 0
            self.consecutive_losses = 0
            log(f"📅 New day — counters reset")

        if self.daily_trades >= self.config.max_trades_per_day:
            self._log_skip(f"Daily trade limit ({self.config.max_trades_per_day}) reached")
            return None

        # ⬇⬇⬇ FIXED: uses config instead of hardcoded 2 ⬇⬇⬇
        if self.daily_losses >= self.config.max_consecutive_losses:
            self._log_skip(f"Daily losses ({self.daily_losses}) >= max consecutive ({self.config.max_consecutive_losses})")
            return None

        if self.consecutive_losses >= self.config.max_consecutive_losses:
            self._log_skip(f"Consecutive losses ({self.consecutive_losses}) >= max ({self.config.max_consecutive_losses})")
            return None

        # ── Drawdown ──
        dd = (self.peak - self.capital) / self.peak * 100 if self.peak > 0 else 0
        if dd > self.config.max_drawdown_pct:
            self._log_skip(f"Max DD ({dd:.1f}%) > {self.config.max_drawdown_pct}%")
            return None

        # ── Signal detection ──
        if len(df5) < 3:
            self._log_skip("Not enough bars for signal")
            return None

        i = len(df5) - 2  # latest complete bar
        bar = df5.iloc[i]

        # Log the current bar conditions
        if hasattr(bar, 'adx') and not np.isnan(bar.adx):
            self._log_diag(
                f"Bar {df5.index[i]:%H:%M}  "
                f"ADX={bar.adx:.1f}  "
                f"ATR=${bar.atr14:.2f}  "
                f"EMAs={'↑' if bar.ema20 > bar.ema50 else '↓'}  "
                f"+DI={bar.plus_di:.1f}  -DI={bar.minus_di:.1f}"
            )

        sig = msp.detect_signal_scalp(df5, df15, i, self.config, self.news_filter)
        if sig is None:
            self._log_skip("No signal conditions met")
            return None

        log(f"📊 SIGNAL: {sig['side']}  ADX={sig['adx']:.1f}  ATR=${sig['atr']:.2f}  Pillars={sig['pillars']}")
        return {'signal': sig, 'df_entry': df5, 'df_trend': df15, 'bar_index': i}

    # ── Fix 3: override execute_trade to use detected symbol ──
    def execute_trade(self, signal_info: dict) -> bool:
        sig = signal_info['signal']
        bar = signal_info['df_entry'].iloc[signal_info['bar_index']]

        regime_mult, regime_label = self.stealth.get_atr_regime(bar)
        sl_pips = (sig['atr'] * regime_mult) / self.config.pip_value
        lots = msp.calc_lots(self.capital, self.config.risk_pct, sl_pips, self.config)
        if lots < self.config.min_lot:
            log(f"   ⏭️  Lot calc: {lots:.4f} < min ({self.config.min_lot})")
            return False

        levels = self.stealth.get_stealth_levels(sig['entry'], sig['atr'], sig['side'], regime_mult)
        order_type = mt5.ORDER_TYPE_BUY if sig['side'] == 'LONG' else mt5.ORDER_TYPE_SELL
        tick = mt5.symbol_info_tick(self.symbol)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": lots,
            "type": order_type,
            "price": tick.ask if sig['side'] == 'LONG' else tick.bid,
            "sl": levels['stop_loss'],
            "tp": levels['take_profit'],
            "deviation": 20,
            "magic": 823778,
            "comment": f"XAU-Cent {regime_label[:1]} {sig['adx']:.0f}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            err = mt5.last_error() if result is None else result.comment
            log(f"❌ Order failed: {err}")
            return False

        self.daily_trades += 1
        self.active_trades.append({
            'ticket': result.order,
            'side': sig['side'],
            'entry': result.price,
            'sl': levels['stop_loss'],
            'tp': levels['take_profit'],
            'lots': lots,
            'regime': regime_label,
            'adx': sig['adx'],
            'entry_time': datetime.now(),
        })
        log(f"✅ {sig['side']} {lots:.2f} lot @ ${result.price:.2f} "
            f"SL=${levels['stop_loss']:.2f} TP=${levels['take_profit']:.2f} "
            f"[{regime_label}] ADX={sig['adx']:.1f}")
        return True

    # ── Manage trades: uses detected symbol ──
    def manage_active_trades(self):
        if not self.active_trades:
            return
        if not self.check_connection() or not self.ensure_symbol():
            return

        positions = mt5.positions_get()
        if positions is None:
            return

        pos_dict = {p.ticket: p for p in positions}
        closed = []
        for t in self.active_trades:
            if t['ticket'] not in pos_dict:
                history = mt5.history_deals_get(t['entry_time'], datetime.now())
                if history:
                    for deal in history:
                        if deal.position_id == t['ticket'] and deal.profit != 0:
                            self.capital += deal.profit
                            if self.capital > self.peak:
                                self.peak = self.capital
                            if deal.profit > 0:
                                self.consecutive_losses = 0
                                log(f"💚 Win ${deal.profit:.2f} — streak reset")
                            else:
                                self.daily_losses += 1
                                self.consecutive_losses += 1
                                log(f"❤️ Loss ${deal.profit:.2f} — streak={self.consecutive_losses}")
                            break
                closed.append(t)

        for t in closed:
            self.active_trades.remove(t)

        # Dynamic SL adjustment
        try:
            rates = mt5.copy_rates_from(self.symbol, mt5.TIMEFRAME_M5, datetime.now(), 10)
            if rates and len(rates) > 0:
                df = _rates_to_df(rates)
                df = msp.compute_indicators(df)
                latest = df.iloc[-1]

                for p in positions:
                    if p.comment.startswith('XAU-Cent') or p.comment.startswith('MATE-Scalp'):
                        regime_mult, _ = self.stealth.get_atr_regime(latest)
                        side = 'LONG' if p.type == mt5.ORDER_TYPE_BUY else 'SHORT'

                        pseudo = msp.Trade(
                            entry_time=datetime.fromtimestamp(p.time),
                            side=side,
                            entry_price=p.price_open,
                            stop_loss=p.sl,
                            take_profit=p.tp,
                            lots_at_open=p.volume,
                        )
                        new_sl = self.stealth.recalculate_dynamic_sl(pseudo, latest, regime_mult)

                        if (side == 'LONG' and new_sl > p.sl) or (side == 'SHORT' and new_sl < p.sl):
                            if abs(new_sl - p.sl) > 0.01:
                                mr = mt5.order_send({
                                    "action": mt5.TRADE_ACTION_SLTP,
                                    "symbol": self.symbol,
                                    "position": p.ticket,
                                    "sl": new_sl,
                                    "tp": p.tp,
                                })
                                if mr and mr.retcode == mt5.TRADE_RETCODE_DONE:
                                    log(f"  ↕️ SL updated on #{p.ticket}: ${new_sl:.2f}")
        except Exception as e:
            pass

    # ── Better logging ──
    def _log_skip(self, reason: str):
        self._diagnostic_log.append(f"[{datetime.now():%H:%M:%S}] SKIP: {reason}")
        if len(self._diagnostic_log) > 100:
            self._diagnostic_log = self._diagnostic_log[-50:]

    def _log_diag(self, msg: str):
        self._diagnostic_log.append(f"[{datetime.now():%H:%M:%S}] {msg}")
        if len(self._diagnostic_log) > 100:
            self._diagnostic_log = self._diagnostic_log[-50:]

    def print_skip_log(self):
        """Print last N skip reasons."""
        last = self._diagnostic_log[-20:]
        if last:
            print("\n  📋 Last 20 diagnostic lines:")
            for line in last:
                print(f"    {line}")

    # ── run_cycle with diagnostics ──
    def run_cycle(self):
        if self.news_filter:
            self.news_filter._ensure_fresh()
        self.manage_active_trades()
        if len(self.active_trades) < 3:
            signal_info = self.get_signal()
            if signal_info:
                self.execute_trade(signal_info)


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def _rates_to_df(rates):
    df = __import__('pandas').DataFrame(rates)
    df['time'] = __import__('pandas').to_datetime(df['time'], unit='s')
    df.set_index('time', inplace=True)
    return df

try:
    import numpy as np
except ImportError:
    import numpy as np  # noqa: F811


# ─────────────────────────────────────────────
# DIAGNOSTIC / STATUS
# ─────────────────────────────────────────────
def status_report():
    print("\n" + "=" * 60)
    print("  XAUUSD CENT SCALPER — STATUS")
    print("=" * 60)

    if not mt5.initialize():
        print(f"  ❌ MT5: {mt5.last_error()}")
        return

    acc = mt5.account_info()
    if acc:
        mode = "DEMO" if acc.trade_mode == 0 else "LIVE"
        print(f"  Account:  {acc.login}@{acc.server}")
        print(f"  Balance:  ${acc.balance:.2f} ({mode})")
        print(f"  Equity:   ${acc.equity:.2f}")
        print(f"  Leverage: 1:{acc.leverage}")

        # Find symbol
        sym = find_xauusd_symbol()
        if sym:
            tick = mt5.symbol_info_tick(sym)
            if tick:
                spread_pips = (tick.ask - tick.bid) / 0.01
                print(f"  Symbol:   {sym}")
                print(f"  Bid:      ${tick.bid:.2f}")
                print(f"  Spread:   ${tick.ask-tick.bid:.2f} ({spread_pips:.0f} pips)")

                # Check for M5 data
                rates = mt5.copy_rates_from(sym, mt5.TIMEFRAME_M5, datetime.now(), 10)
                if rates and len(rates) > 0:
                    print(f"  Data:     ✅ M5 bars available")
                else:
                    print(f"  Data:     ❌ No M5 data (symbol not selected?)")
        else:
            print(f"  Symbol:   ❌ No XAUUSD found")
    else:
        print(f"  ❌ No account: {mt5.last_error()}")

    mt5.shutdown()
    print("=" * 60)


def find_symbol_only():
    """--find-symbol: just detect and print."""
    print("\n  🔍 Scanning for XAUUSD symbol...")
    if not mt5.initialize():
        print(f"  ❌ MT5: {mt5.last_error()}")
        return

    found = find_xauusd_symbol()
    if found:
        info = mt5.symbol_info(found)
        tick = mt5.symbol_info_tick(found)
        print(f"  ✅ Found: {found}")
        print(f"     Bid: ${tick.bid:.2f}  Ask: ${tick.ask:.2f}")
        print(f"     Spread: ${tick.ask-tick.bid:.2f}")
        print(f"     Volume min: {info.volume_min}  step: {info.volume_step}")
    else:
        print(f"  ❌ Not found. Check Market Watch in MT5.")
    mt5.shutdown()


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def get_cent_config():
    """Create a cent-account-optimized ScalperConfig."""
    c = msp.ScalperConfig()
    for key, val in CENT_OVERRIDES.items():
        setattr(c, key, val)
    return c


def main():
    import argparse
    ap = argparse.ArgumentParser(description="XAUUSD Cent Raw ECN Scalper")
    ap.add_argument("--hours", type=float, default=None, help="Run for N hours")
    ap.add_argument("--status", action="store_true", help="Show diagnostic")
    ap.add_argument("--find-symbol", action="store_true", help="Detect XAUUSD symbol")
    args = ap.parse_args()

    if args.status:
        status_report()
        return

    if args.find_symbol:
        find_symbol_only()
        return

    # ── Launch ──
    config = get_cent_config()
    log("=" * 60)
    log("XAUUSD CENT SCALPER — STARTING")
    log(f"Capital: ${config.initial_capital:.2f}  Risk: {config.risk_pct}%/trade")
    log(f"Commission: ${config.commission_per_lot_rt:.2f}/lot RT")
    log(f"Symbol lookup: auto")

    engine = CentLiveScalper(config)

    if args.hours:
        log(f"Run limit: {args.hours}h")
        stop_at = datetime.now() + timedelta(hours=args.hours)

    log("Engine running. Press Ctrl+C to stop.")
    log("=" * 60)

    try:
        engine.running = True
        while engine.running:
            try:
                engine.run_cycle()

                if args.hours and datetime.now() >= stop_at:
                    log(f"⏰ Time limit reached.")
                    break

                # Print diagnostic summary every 30 cycles
                now = datetime.now()
                if now.second < 10:  # ~once per minute
                    engine.print_skip_log()

            except KeyboardInterrupt:
                raise
            except Exception as e:
                log(f"⚠️ Cycle error: {e}")

            time.sleep(30)

    except KeyboardInterrupt:
        log("🛑 Stopped by user")
    finally:
        mt5.shutdown()
        log("Disconnected.")
        engine.print_skip_log()

    # Summary
    log(f"\n📊 Session summary:")
    log(f"   Trades taken: {len(engine.active_trades)}")
    log(f"   Capital: ${engine.capital:.2f}")
    log(f"   Diagnostics in: {LOG_DIR / 'cent_scalper.log'}")


if __name__ == "__main__":
    main()
