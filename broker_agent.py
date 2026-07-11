#!/usr/bin/env python3
"""Broker Agent — runs on your LOCAL trading PC to sync MT5 trades to the radar.

Install:
    pip install MetaTrader5 requests

Usage:
    python broker_agent.py                    # one-time push
    python broker_agent.py --watch            # poll every 60s
    python broker_agent.py --setup            # create config interactively

The script reads MT5 trade history and pushes it to your remote radar
at yams-radar.duckdns.org via a simple API key (no 2FA needed).
"""

import sys
import os
import json
import time
import argparse
from datetime import datetime, timedelta
from pathlib import Path

CONFIG_FILE = Path(__file__).with_name("broker_agent_config.json")


# ─── Config ────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {
        "radar_url": "https://yams-radar.duckdns.org",
        "api_key": "",
        "server": "",
        "login": 0,
        "password": "",
        "account_type": "live",
        "import_days": 90,
        "mt5_path": "",  # optional: path to terminal64.exe if auto-detect fails
    }


def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    print(f"Config saved to {CONFIG_FILE}")


def setup_interactive():
    """Interactive setup — creates config file."""
    cfg = load_config()

    print("=" * 50)
    print("  Broker Agent Setup")
    print("=" * 50)
    print(f"\nRadar URL [{cfg['radar_url']}]: ", end="")
    inp = input().strip()
    if inp:
        cfg["radar_url"] = inp.rstrip("/")

    print(f"API Key [{cfg['api_key']}]: ", end="")
    inp = input().strip()
    if inp:
        cfg["api_key"] = inp

    print(f"MT5 Server [{cfg['server']}]: ", end="")
    inp = input().strip()
    if inp:
        cfg["server"] = inp

    print(f"MT5 Login [{cfg['login']}]: ", end="")
    inp = input().strip()
    if inp:
        cfg["login"] = int(inp)

    print(f"MT5 Password [{cfg['password']}]: ", end="")
    inp = input().strip()
    if inp:
        cfg["password"] = inp

    print(f"Account type (demo/live) [{cfg['account_type']}]: ", end="")
    inp = input().strip()
    if inp:
        cfg["account_type"] = inp

    print(f"MT5 Path (leave empty for auto-detect) [{cfg['mt5_path']}]: ", end="")
    inp = input().strip()
    if inp:
        cfg["mt5_path"] = inp

    print(f"Import days [{cfg['import_days']}]: ", end="")
    inp = input().strip()
    if inp:
        cfg["import_days"] = int(inp)

    save_config(cfg)
    print("\nSetup complete! Run: python broker_agent.py")


# ─── MT5 Connection ────────────────────────────────────────────────────────

def connect_mt5(cfg: dict) -> bool:
    """Initialize MT5 and login to broker."""
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print("ERROR: MetaTrader5 package not installed.")
        print("Run: pip install MetaTrader5")
        return False

    # Use custom MT5 path if configured
    mt5_path = cfg.get("mt5_path", "").strip()
    if mt5_path:
        print(f"Using MT5 path: {mt5_path}")
        if not mt5.initialize(path=mt5_path):
            err = mt5.last_error()
            print(f"MT5 init failed: {err}")
            print(f"Check that the path is correct: {mt5_path}")
            return False
    else:
        if not mt5.initialize():
            err = mt5.last_error()
            print(f"MT5 init failed: {err}")
            print("=" * 55)
            print("  MT5 Terminal not found! Fix options:")
            print("  1. Install MetaTrader 5 from your broker's website")
            print("  2. If already installed, find terminal64.exe and run:")
            print("     python broker_agent.py --setup")
            print("     Then enter the path when prompted")
            print("  3. Make sure you're running 64-bit Python (not 32-bit)")
            print("=" * 55)
            return False

    # If credentials provided, try to login
    if cfg.get("login") and cfg.get("password") and cfg.get("server"):
        authorized = mt5.login(
            login=cfg["login"],
            password=cfg["password"],
            server=cfg["server"]
        )
        if not authorized:
            err = mt5.last_error()
            print(f"MT5 login failed: {err}")
            print(f"  Server: {cfg['server']}")
            print(f"  Login:  {cfg['login']}")
            mt5.shutdown()
            return False
        print(f"Logged in to {cfg['server']} as {cfg['login']}")
    else:
        print("No credentials in config — using currently connected MT5 terminal.")

    return True


def get_account_info():
    import MetaTrader5 as mt5
    info = mt5.account_info()
    if info is None:
        return None
    return {
        "login": info.login,
        "server": info.server,
        "balance": info.balance,
        "equity": info.equity,
        "margin": info.margin,
        "margin_free": info.margin_free,
        "currency": info.currency,
        "leverage": info.leverage,
        "name": info.name,
        "account_type": "live" if info.trade_mode == 0 else "demo",
    }


def get_positions():
    import MetaTrader5 as mt5
    positions = mt5.positions_get()
    if positions is None:
        return []
    result = []
    for p in positions:
        profit = p.profit + (p.commission or 0) + (p.swap or 0)
        result.append({
            "ticket": p.ticket,
            "symbol": p.symbol,
            "volume": p.volume,
            "price_open": p.price_open,
            "price_current": p.price_current,
            "sl": p.sl,
            "tp": p.tp,
            "profit": round(profit, 2),
            "swap": p.swap,
            "type": "Buy" if p.type == 0 else "Sell",
            "time": datetime.fromtimestamp(p.time).strftime("%Y-%m-%d %H:%M"),
        })
    return result


def get_closed_trades(cfg: dict, last_ticket: int = 0):
    """Fetch closed trade history from MT5."""
    import MetaTrader5 as mt5

    now = datetime.now()
    from_dt = now - timedelta(days=cfg.get("import_days", 365))

    deals = mt5.history_deals_get(from_dt, now)
    if deals is None:
        err = mt5.last_error()
        print(f"No MT5 history: {err}")
        return []

    trades = []
    for d in deals:
        ticket = d.ticket
        if ticket <= last_ticket:
            continue
        if d.entry != 1:  # only "out" entries = closed trades
            continue

        pnl = round(d.profit + (d.commission or 0) + (d.swap or 0), 2)
        order_type = "Buy" if d.type == 0 else "Sell"

        trades.append({
            "ticket": ticket,
            "symbol": d.symbol,
            "direction": order_type,
            "entry_price": d.price if d.entry == 0 else d.price,
            "exit_price": d.price if d.entry == 1 else 0,
            "lot_size": d.volume,
            "pnl": pnl,
            "result": "win" if pnl > 0 else "loss",
            "commission": d.commission or 0,
            "swap": d.swap or 0,
            "date_opened": datetime.fromtimestamp(d.time).strftime("%Y-%m-%dT%H:%M"),
            "date_closed": datetime.fromtimestamp(d.time).strftime("%Y-%m-%dT%H:%M"),
            "notes": f"Imported from MT5 | {d.comment or ''}",
        })

    return trades


# ─── API Push ───────────────────────────────────────────────────────────────

def push_to_radar(cfg: dict, endpoint: str, payload: dict) -> dict:
    """POST data to the radar API."""
    import requests

    url = f"{cfg['radar_url']}{endpoint}"
    headers = {
        "X-Api-Key": cfg["api_key"],
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        print(f"API push failed: {e}")
        if hasattr(e, 'response') and e.response is not None:
            try:
                return e.response.json()
            except Exception:
                return {"ok": False, "error": str(e)}
        return {"ok": False, "error": str(e)}


# ─── Main ──────────────────────────────────────────────────────────────────

def sync_trades(cfg: dict):
    """Fetch trades from MT5 and push to radar."""
    import MetaTrader5 as mt5

    print("\nConnecting to MT5...")
    if not connect_mt5(cfg):
        return False

    try:
        # Get account info
        info = get_account_info()
        if info:
            print(f"Account: {info['name']} | Balance: {info['balance']} {info['currency']}")
            result = push_to_radar(cfg, "/api/broker/remote-push", {
                "action": "account_info",
                "info": info,
            })
            if result.get("ok"):
                print("  Account info pushed")

        # Get open positions
        positions = get_positions()
        if positions:
            print(f"Open positions: {len(positions)}")
            result = push_to_radar(cfg, "/api/broker/remote-push", {
                "action": "positions",
                "positions": positions,
            })
            if result.get("ok"):
                print(f"  {len(positions)} positions pushed")
        else:
            print("No open positions")

        # Get closed trades
        trades = get_closed_trades(cfg)
        print(f"Closed trades found: {len(trades)}")

        if trades:
            # Push in batches of 20
            batch_size = 20
            total_imported = 0
            for i in range(0, len(trades), batch_size):
                batch = trades[i:i + batch_size]
                result = push_to_radar(cfg, "/api/broker/remote-push", {
                    "action": "add_trades",
                    "trades": batch,
                })
                if result.get("ok"):
                    total_imported += result.get("imported", 0)
                    print(f"  Batch {i // batch_size + 1}: {result.get('imported', 0)} imported")
                else:
                    print(f"  Batch failed: {result.get('error', 'unknown')}")

            print(f"\nTotal imported: {total_imported} new trades")
        else:
            print("No new trades to import")

        # Update last sync time
        cfg["last_sync"] = datetime.now().isoformat()
        save_config(cfg)

    finally:
        mt5.shutdown()

    return True


def main():
    parser = argparse.ArgumentParser(description="Broker Agent — sync MT5 trades to radar")
    parser.add_argument("--watch", "-w", action="store_true", help="Poll mode (every 60s)")
    parser.add_argument("--setup", "-s", action="store_true", help="Interactive setup")
    parser.add_argument("--interval", "-i", type=int, default=60, help="Poll interval in seconds")
    args = parser.parse_args()

    if args.setup:
        setup_interactive()
        return

    cfg = load_config()

    # Validate config
    if not cfg.get("api_key"):
        print("ERROR: No API key configured. Run: python broker_agent.py --setup")
        sys.exit(1)

    if not cfg.get("radar_url"):
        print("ERROR: No radar URL configured. Run: python broker_agent.py --setup")
        sys.exit(1)

    if args.watch:
        print(f"Watch mode — polling every {args.interval}s (Ctrl+C to stop)")
        while True:
            sync_trades(cfg)
            print(f"\nWaiting {args.interval}s...")
            time.sleep(args.interval)
    else:
        sync_trades(cfg)


if __name__ == "__main__":
    main()
