"""Broker Sync — per-user MT5/MT4 broker connection management.

Each user can save their own broker credentials (encrypted at rest) and
connect/disconnect from their broker account. Supports demo and live accounts.
"""

import json
import os
import time
from pathlib import Path
from typing import Optional

# ── Encryption ──────────────────────────────────────────────────────────────
_fernet = None

def _get_cipher():
    global _fernet
    if _fernet is not None:
        return _fernet
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    import base64

    key_material = os.environ.get(
        'BROKER_ENCRYPTION_KEY',
        'yams-radar-default-broker-key-2026'
    ).encode()
    salt = b'yams-radar-salt-2026'
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600_000)
    key = base64.urlsafe_b64encode(kdf.derive(key_material))
    _fernet = Fernet(key)
    return _fernet


def _encrypt(plain: str) -> str:
    if not plain:
        return ''
    return _get_cipher().encrypt(plain.encode()).decode()


def _decrypt(token: str) -> str:
    if not token:
        return ''
    try:
        return _get_cipher().decrypt(token.encode()).decode()
    except Exception:
        return ''


# ── Config file ─────────────────────────────────────────────────────────────
CONFIG_FILE = Path(__file__).parent / "broker_configs.json"
_lock = [False]  # simple re-entrancy guard


def _load_all() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_all(configs: dict):
    CONFIG_FILE.write_text(json.dumps(configs, indent=2))


# ── Public API ───────────────────────────────────────────────────────────────

def save_config(
    user_id: str,
    broker_name: str,
    server: str,
    login: int,
    password: str,
    account_type: str = "demo",
) -> dict:
    """Save encrypted broker config for a user. Returns the saved config dict."""
    all_cfgs = _load_all()
    entry = {
        "broker_name": broker_name,
        "server": server,
        "login": login,
        "password_enc": _encrypt(password),
        "account_type": account_type,
        "updated_at": time.time(),
    }
    all_cfgs[user_id] = entry
    _save_all(all_cfgs)
    return {
        "broker_name": broker_name,
        "server": server,
        "login": login,
        "account_type": account_type,
        "updated_at": entry["updated_at"],
    }


def load_config(user_id: str) -> Optional[dict]:
    """Return decrypted broker config for a user, or None."""
    all_cfgs = _load_all()
    raw = all_cfgs.get(user_id)
    if raw is None:
        return None
    return {
        "broker_name": raw.get("broker_name", ""),
        "server": raw["server"],
        "login": raw["login"],
        "password": _decrypt(raw.get("password_enc", "")),
        "account_type": raw.get("account_type", "demo"),
    }


def load_config_safe(user_id: str) -> Optional[dict]:
    """Return config WITHOUT password (for frontend display)."""
    raw = load_config(user_id)
    if raw is None:
        return None
    raw.pop("password", None)
    return raw


def delete_config(user_id: str) -> bool:
    """Delete a user's broker config. Returns True if existed."""
    all_cfgs = _load_all()
    existed = user_id in all_cfgs
    all_cfgs.pop(user_id, None)
    _save_all(all_cfgs)
    return existed


def get_connected_user_slug() -> Optional[str]:
    """Figure out which user is currently connected to MT5.

    Reads the 'owner' marker file written on connect. Returns user_id or None.
    """
    marker = Path(__file__).parent / ".mt5_connection_owner"
    if marker.exists():
        try:
            return marker.read_text().strip()
        except Exception:
            pass
    return None


def _set_connection_owner(user_id: Optional[str]):
    marker = Path(__file__).parent / ".mt5_connection_owner"
    if user_id:
        marker.write_text(user_id)
    else:
        try:
            marker.unlink(missing_ok=True)
        except Exception:
            pass


def connect_to_broker(
    login: int,
    password: str,
    server: str,
    user_id: str = "",
) -> dict:
    """Initialize MT5 with specific broker credentials.

    Handles both MT5 and MT4 accounts. Shuts down any existing connection first.
    Returns dict with success status and details.
    """
    HAS_MT5 = False
    try:
        import MetaTrader5 as mt5
        HAS_MT5 = True
    except ImportError:
        return {"success": False, "error": "MetaTrader5 package not installed"}

    if not HAS_MT5:
        return {"success": False, "error": "MetaTrader5 package not installed"}

    try:
        # Shutdown any existing connection
        try:
            mt5.shutdown()
        except Exception:
            pass

        # Initialize with credentials
        initialized = mt5.initialize(login=login, password=password, server=server)
        if not initialized:
            error = mt5.last_error()
            return {
                "success": False,
                "error": f"Failed to initialize: {error}",
            }

        # Verify by fetching account info
        acc = mt5.account_info()
        if acc is None:
            mt5.shutdown()
            return {"success": False, "error": "Connected but cannot read account info"}

        # Update the global state in mt5_terminal
        from . import mt5_terminal
        mt5_terminal._MT5_INITIALIZED = True
        mt5_terminal._MT5_LAST_CHECK = time.time()

        # Mark who owns this connection
        if user_id:
            _set_connection_owner(user_id)

        return {
            "success": True,
            "login": acc.login,
            "server": acc.server,
            "name": acc.name,
            "company": acc.company,
            "currency": acc.currency,
            "balance": round(acc.balance, 2),
            "equity": round(acc.equity, 2),
            "leverage": acc.leverage,
            "account_type": "live" if acc.trade_mode == 0 else "demo",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def disconnect(user_id: str = "") -> dict:
    """Disconnect MT5 and clear the connection owner marker."""
    try:
        import MetaTrader5 as mt5
        try:
            mt5.shutdown()
        except Exception:
            pass
        from . import mt5_terminal
        mt5_terminal._MT5_INITIALIZED = False
        mt5_terminal._MT5_LAST_CHECK = 0
    except Exception:
        pass

    if user_id:
        _set_connection_owner(None)
    return {"success": True, "message": "Disconnected"}


def list_saved_users() -> list[dict]:
    """Return a list of users who have saved broker configs (no passwords)."""
    all_cfgs = _load_all()
    return [
        {
            "user_id": uid,
            "broker_name": cfg.get("broker_name", ""),
            "server": cfg.get("server", ""),
            "login": cfg.get("login", ""),
            "account_type": cfg.get("account_type", "demo"),
        }
        for uid, cfg in all_cfgs.items()
    ]
