"""Flask Web Dashboard — radar table, AI chat, position calculator, journal."""

import sys, os, json, io, base64
from datetime import datetime, timezone, timedelta
from pathlib import Path
from functools import wraps
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import secrets

from .radar import scan as radar_scan
from .config import Config
from .instruments import INSTRUMENTS, get_symbols, pip_value_usd, CATEGORY_LABELS, best_session_str

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(16))

# ─── Authentication config ───────────────────────────────────────────────────
AUTH_FILE = Path(__file__).parent / "auth_config.json"
USERS_FILE = Path(__file__).parent / "users.json"
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')
_PASSWORD_HASH = None

def _get_admin_credentials():
    """Get admin username + password hash. Config file overrides env vars."""
    cfg = _load_auth_config()
    uname = cfg.get('admin_username', ADMIN_USERNAME)
    pw_hash = cfg.get('admin_password_hash', None)
    if not pw_hash:
        from werkzeug.security import generate_password_hash
        pw_hash = generate_password_hash(ADMIN_PASSWORD)
    return uname, pw_hash

def _load_auth_config():
    if AUTH_FILE.exists():
        try:
            return json.loads(AUTH_FILE.read_text())
        except Exception:
            pass
    return {'totp_enabled': False, 'totp_secret': None}

def _save_auth_config(cfg):
    AUTH_FILE.write_text(json.dumps(cfg, indent=2))

def _check_password(pw: str) -> bool:
    from werkzeug.security import check_password_hash
    _, pw_hash = _get_admin_credentials()
    return check_password_hash(pw_hash, pw)

# ─── Multi-user database ──────────────────────────────────────────────────

def _load_users():
    """Load users.json — returns dict of email -> user record."""
    if USERS_FILE.exists():
        try:
            return json.loads(USERS_FILE.read_text())
        except Exception:
            pass
    return {}

def _save_users(users: dict):
    USERS_FILE.write_text(json.dumps(users, indent=2, default=str))

def _find_user_by_email_or_mobile(login: str) -> tuple:
    """Find a user by email OR mobile. Returns (key, record) or (None, None)."""
    users = _load_users()
    login_lower = login.strip().lower()
    for key, rec in users.items():
        if rec.get('email', '').lower() == login_lower:
            return key, rec
        if rec.get('mobile', '') == login:
            return key, rec
    return None, None

def _create_user_record(email: str, password: str, mobile: str = '') -> dict:
    """Create a new user record with hashed password and TOTP secret."""
    from werkzeug.security import generate_password_hash
    import pyotp
    secret = pyotp.random_base32()
    return {
        'email': email.strip().lower(),
        'mobile': mobile.strip(),
        'password_hash': generate_password_hash(password),
        'totp_secret': secret,
        'totp_enabled': False,      # becomes True after signup verification
        'role': 'user',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'display_name': email.split('@')[0] if '@' in email else email,
    }

def login_required(f):
    """Require authenticated session for a route."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('authenticated') or not session.get('totp_verified'):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    """Require admin role for a route."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('authenticated') or not session.get('totp_verified'):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('login_page'))
        if session.get('role') != 'admin':
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Admin access required'}), 403
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated

# TOTP (2FA) helpers
_TOTP = None
def _get_totp():
    global _TOTP
    if _TOTP is None:
        import pyotp
        cfg = _load_auth_config()
        secret = cfg.get('totp_secret')
        if secret:
            _TOTP = pyotp.TOTP(secret)
    return _TOTP

def _get_user_totp(email: str):
    """Get TOTP object for a regular user by email."""
    users = _load_users()
    rec = users.get(email)
    if rec and rec.get('totp_secret'):
        import pyotp
        return pyotp.TOTP(rec['totp_secret'])
    return None

def _generate_totp_secret() -> str:
    """Generate a new TOTP secret and return it."""
    import pyotp
    return pyotp.random_base32()

def _generate_totp_uri(secret: str, name: str = "") -> str:
    """Generate the otpauth:// URI for QR code."""
    import pyotp
    label = name or ADMIN_USERNAME
    return pyotp.totp.TOTP(secret).provisioning_uri(name=label, issuer_name="Yams Radar")

# Journal file
JOURNAL_FILE = Path(__file__).parent / "trading_journal.json"

# Radar result cache (avoids re-running full scan per BUDDY question)
_radar_cache = None
_radar_cache_time = None
_RADAR_CACHE_TTL = timedelta(seconds=120)


def _get_cached_radar():
    """Return cached radar result if fresh, otherwise run a new scan."""
    global _radar_cache, _radar_cache_time
    now = datetime.now(timezone.utc)
    if _radar_cache is not None and _radar_cache_time is not None:
        if now - _radar_cache_time < _RADAR_CACHE_TTL:
            return _radar_cache
    cfg = Config()
    _radar_cache = radar_scan(cfg)
    _radar_cache_time = now
    return _radar_cache


def _warm_cache():
    """Pre-warm the radar cache in background so the first visitor doesn't wait."""
    import time
    time.sleep(3)
    try:
        _get_cached_radar()
    except Exception:
        pass


import threading as _thr
_thr.Thread(target=_warm_cache, daemon=True).start()


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


# ─── Auth Routes ─────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET'])
def login_page():
    if session.get('authenticated') and session.get('totp_verified'):
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')

    # ── Check: is this the admin (config or env-var based)? ──
    admin_user, _ = _get_admin_credentials()
    if username == admin_user:
        if not _check_password(password):
            return jsonify({'status': 'error', 'message': 'Invalid username or password'}), 401
        cfg = _load_auth_config()
        session['authenticated'] = True
        session['role'] = 'admin'
        session['display_name'] = admin_user
        session['user_email'] = None
        if cfg.get('totp_enabled'):
            session['totp_verified'] = False
            return jsonify({'status': 'need_2fa'})
        session['totp_verified'] = True
        session.permanent = True
        app.permanent_session_lifetime = timedelta(hours=8)
        return jsonify({'status': 'ok'})

    # ── Check: regular user by email or mobile ──
    key, rec = _find_user_by_email_or_mobile(username)
    if key is None:
        return jsonify({'status': 'error', 'message': 'Invalid email/mobile or password'}), 401

    from werkzeug.security import check_password_hash
    if not check_password_hash(rec['password_hash'], password):
        return jsonify({'status': 'error', 'message': 'Invalid email/mobile or password'}), 401

    session['authenticated'] = True
    session['role'] = rec.get('role', 'user')
    session['display_name'] = rec.get('display_name', key)
    session['user_email'] = key

    if rec.get('totp_enabled'):
        session['totp_verified'] = False
        return jsonify({'status': 'need_2fa'})

    session['totp_verified'] = True
    session.permanent = True
    app.permanent_session_lifetime = timedelta(hours=8)
    return jsonify({'status': 'ok'})

@app.route('/api/verify-2fa', methods=['POST'])
def api_verify_2fa():
    if not session.get('authenticated'):
        return jsonify({'status': 'error', 'message': 'Login first'}), 401

    code = request.json.get('code', '').strip()

    # Determine which TOTP to verify against
    user_email = session.get('user_email')
    if user_email:
        totp = _get_user_totp(user_email)
    else:
        totp = _get_totp()

    if totp and totp.verify(code, valid_window=1):
        session['totp_verified'] = True
        session.permanent = True
        app.permanent_session_lifetime = timedelta(hours=8)
        return jsonify({'status': 'ok'})
    return jsonify({'status': 'error', 'message': 'Invalid 2FA code'}), 401

@app.route('/api/check-auth')
def api_check_auth():
    ok = session.get('authenticated') and session.get('totp_verified')
    return jsonify({
        'authenticated': ok,
        'role': session.get('role'),
        'display_name': session.get('display_name'),
    })

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))

@app.route('/setup-2fa')
def setup_2fa_page():
    if not session.get('authenticated'):
        return redirect(url_for('login_page'))

    # Handle regular users vs admin
    user_email = session.get('user_email')
    if user_email:
        users = _load_users()
        rec = users.get(user_email, {})
        secret = rec.get('totp_secret')
        already_enabled = rec.get('totp_enabled', False)

        if not secret:
            secret = _generate_totp_secret()
            rec['totp_secret'] = secret
            rec['totp_enabled'] = False
            users[user_email] = rec
            _save_users(users)

        display_name = rec.get('display_name', user_email.split('@')[0])
        uri = _generate_totp_uri(secret, name=display_name)
    else:
        cfg = _load_auth_config()
        secret = cfg.get('totp_secret')
        already_enabled = cfg.get('totp_enabled', False)

        if not secret:
            secret = _generate_totp_secret()
            cfg['totp_secret'] = secret
            cfg['totp_enabled'] = False
            _save_auth_config(cfg)

        uri = _generate_totp_uri(secret)

    # Generate QR code as base64 data URI
    try:
        import qrcode
        qr = qrcode.make(uri)
        buf = io.BytesIO()
        qr.save(buf, format='PNG')
        qr_b64 = base64.b64encode(buf.getvalue()).decode()
        qr_data_uri = f"data:image/png;base64,{qr_b64}"
    except Exception:
        qr_data_uri = None

    return render_template('setup_2fa.html',
                          qr_uri=qr_data_uri,
                          secret=secret,
                          enabled=already_enabled,
                          uri=uri)

@app.route('/api/toggle-2fa', methods=['POST'])
def api_toggle_2fa():
    if not session.get('authenticated') or not session.get('totp_verified'):
        return jsonify({'status': 'error', 'message': 'Must be fully authenticated'}), 401

    enable = request.json.get('enable', False)

    # If this is a regular user (not admin via env vars), toggle in users.json
    user_email = session.get('user_email')
    if user_email:
        users = _load_users()
        if user_email in users:
            if enable:
                if not users[user_email].get('totp_secret'):
                    users[user_email]['totp_secret'] = _generate_totp_secret()
                users[user_email]['totp_enabled'] = True
            else:
                users[user_email]['totp_enabled'] = False
            _save_users(users)
            return jsonify({'status': 'ok', 'enabled': enable})

    # Admin (env-var based)
    cfg = _load_auth_config()
    if enable:
        if not cfg.get('totp_secret'):
            cfg['totp_secret'] = _generate_totp_secret()
        cfg['totp_enabled'] = True
    else:
        cfg['totp_enabled'] = False
    _save_auth_config(cfg)
    global _TOTP
    _TOTP = None  # Reset cache

    return jsonify({'status': 'ok', 'enabled': enable})


# ─── Sign-up Routes (email/mobile registration) ─────────────────────────────

@app.route('/signup', methods=['GET'])
def signup_page():
    """Show registration form."""
    if session.get('authenticated') and session.get('totp_verified'):
        return redirect(url_for('index'))
    return render_template('signup.html')


@app.route('/api/signup', methods=['POST'])
def api_signup():
    """Register a new user with email/password (optional mobile).
    Returns TOTP secret + QR for 2FA setup.
    """
    data = request.json
    email = (data.get('email', '') or '').strip().lower()
    password = data.get('password', '')
    mobile = (data.get('mobile', '') or '').strip()

    if not email or not password:
        return jsonify({'status': 'error', 'message': 'Email and password are required'}), 400

    if '@' not in email or '.' not in email:
        return jsonify({'status': 'error', 'message': 'Please enter a valid email address'}), 400

    if len(password) < 6:
        return jsonify({'status': 'error', 'message': 'Password must be at least 6 characters'}), 400

    # Check for duplicate
    users = _load_users()
    if email in users:
        return jsonify({'status': 'error', 'message': 'An account with this email already exists'}), 409

    # Also check if mobile is taken
    if mobile:
        for key, rec in users.items():
            if rec.get('mobile', '') == mobile:
                return jsonify({'status': 'error', 'message': 'This mobile number is already registered'}), 409

    # Create user
    record = _create_user_record(email, password, mobile)
    users[email] = record
    _save_users(users)

    # Generate QR code
    uri = _generate_totp_uri(record['totp_secret'], name=email.split('@')[0])
    qr_data_uri = None
    try:
        import qrcode
        qr = qrcode.make(uri)
        buf = io.BytesIO()
        qr.save(buf, format='PNG')
        qr_b64 = base64.b64encode(buf.getvalue()).decode()
        qr_data_uri = f"data:image/png;base64,{qr_b64}"
    except Exception:
        pass

    return jsonify({
        'status': 'ok',
        'email': email,
        'secret': record['totp_secret'],
        'qr_uri': qr_data_uri,
        'uri': uri,
        'message': 'Account created! Scan the QR code with your authenticator app, then enter the 6-digit code to activate 2FA.'
    })


@app.route('/api/verify-signup', methods=['POST'])
def api_verify_signup():
    """Verify TOTP code during signup to activate the account."""
    data = request.json
    email = (data.get('email', '') or '').strip().lower()
    code = (data.get('code', '') or '').strip()

    if not email or not code:
        return jsonify({'status': 'error', 'message': 'Email and verification code required'}), 400

    users = _load_users()
    if email not in users:
        return jsonify({'status': 'error', 'message': 'Account not found. Please sign up first.'}), 404

    rec = users[email]
    if rec.get('totp_enabled'):
        return jsonify({'status': 'error', 'message': '2FA already enabled for this account.'}), 400

    # Verify TOTP
    import pyotp
    totp = pyotp.TOTP(rec['totp_secret'])
    if not totp.verify(code, valid_window=1):
        return jsonify({'status': 'error', 'message': 'Invalid code. Please try again.'}), 401

    # Activate account
    rec['totp_enabled'] = True
    users[email] = rec
    _save_users(users)

    return jsonify({'status': 'ok', 'message': 'Account activated! You can now log in with 2FA.'})


# ─── Admin Routes ────────────────────────────────────────────────────────────

@app.route('/admin/users')
@admin_required
def admin_users_page():
    """Admin user management."""
    users = _load_users()
    user_list = []
    for email, rec in users.items():
        user_list.append({
            'email': email,
            'mobile': rec.get('mobile', '') or '—',
            'role': rec.get('role', 'user'),
            'display_name': rec.get('display_name', email),
            'totp_enabled': rec.get('totp_enabled', False),
            'created_at': rec.get('created_at', '')[:10] if rec.get('created_at') else '—',
        })
    cfg = _load_auth_config()
    admin_user, _ = _get_admin_credentials()
    return render_template('admin_users.html', users=user_list,
                           admin_username=admin_user,
                           totp_enabled=cfg.get('totp_enabled', False))


@app.route('/api/admin/settings', methods=['POST'])
@admin_required
def api_admin_settings():
    """Update admin settings: username, password, TOTP toggle."""
    data = request.json
    cfg = _load_auth_config()

    if 'username' in data and data['username']:
        cfg['admin_username'] = data['username'].strip()

    if 'password' in data and data['password']:
        from werkzeug.security import generate_password_hash
        cfg['admin_password_hash'] = generate_password_hash(data['password'])

    if 'totp_enabled' in data:
        cfg['totp_enabled'] = bool(data['totp_enabled'])

    _save_auth_config(cfg)
    return jsonify({'ok': True, 'message': 'Settings updated'})


@app.route('/api/admin/credentials', methods=['GET'])
@admin_required
def api_admin_credentials():
    """Return current admin username (no password)."""
    admin_user, _ = _get_admin_credentials()
    cfg = _load_auth_config()
    return jsonify({
        'username': admin_user,
        'totp_enabled': cfg.get('totp_enabled', False),
    })


@app.route('/api/admin/users', methods=['GET'])
@admin_required
def api_admin_users():
    """Return user list as JSON."""
    users = _load_users()
    user_list = []
    for email, rec in users.items():
        user_list.append({
            'email': email,
            'mobile': rec.get('mobile', '') or '—',
            'role': rec.get('role', 'user'),
            'display_name': rec.get('display_name', email),
            'totp_enabled': rec.get('totp_enabled', False),
            'created_at': rec.get('created_at', '')[:10] if rec.get('created_at') else '—',
        })
    return jsonify({'users': user_list})


@app.route('/api/admin/users/delete', methods=['POST'])
@admin_required
def api_admin_delete_user():
    """Delete a user account."""
    data = request.json
    email = (data.get('email', '') or '').strip().lower()
    if not email:
        return jsonify({'status': 'error', 'message': 'Email required'}), 400

    users = _load_users()
    if email not in users:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    del users[email]
    _save_users(users)
    return jsonify({'status': 'ok', 'message': f'User {email} deleted'})


# ─── MT5 Terminal Routes ───────────────────────────────────────────────────

_TERMINAL_CACHE: dict = {}
_TERMINAL_CACHE_AT: float = 0
_TERMINAL_CACHE_TTL = 2.0  # seconds

def _get_terminal_data():
    """Get cached terminal data if fresh, otherwise fetch new."""
    global _TERMINAL_CACHE, _TERMINAL_CACHE_AT
    now = __import__('time').time()
    if now - _TERMINAL_CACHE_AT < _TERMINAL_CACHE_TTL and _TERMINAL_CACHE:
        return _TERMINAL_CACHE
    from .mt5_terminal import (
        get_account_info, get_positions, get_order_history,
        get_open_orders, get_market_watch,
    )
    _TERMINAL_CACHE = {
        'account': get_account_info(),
        'positions': get_positions(),
        'history': get_order_history(days=1),
        'orders': get_open_orders(),
        'watch': get_market_watch(),
    }
    _TERMINAL_CACHE_AT = now
    return _TERMINAL_CACHE


@app.route('/api/broker/diagnose', methods=['GET'])
@login_required
def api_broker_diagnose():
    """Diagnose MT5 connection state — useful for debugging sync issues."""
    from .mt5_terminal import HAS_MT5, _ensure_mt5
    info = {
        "has_mt5_package": HAS_MT5,
    }
    if HAS_MT5:
        import MetaTrader5 as mt5
        info["last_error"] = str(mt5.last_error())
        is_conn = _ensure_mt5()
        info["ensure_mt5_result"] = is_conn
        if is_conn:
            acc = mt5.account_info()
            if acc:
                info["account"] = {"login": acc.login, "server": acc.server, "name": acc.name}
            else:
                info["account"] = None
            # Check history availability
            from datetime import datetime, timedelta
            deals = mt5.history_deals_get(datetime.now() - timedelta(days=30), datetime.now())
            info["history_deals_30d"] = len(deals) if deals else 0
            if deals is None:
                info["history_deals_error"] = str(mt5.last_error())
        # Check saved config
        from .broker_sync import load_config_safe
        user_id = session.get('user_id', session.get('username', 'anonymous'))
        cfg = load_config_safe(user_id)
        info["saved_config"] = cfg is not None
        info["config_server"] = cfg.get("server", "—") if cfg else "—"
    return jsonify(info)


@app.route('/api/terminal/account')
@login_required
def api_terminal_account():
    """Get MT5 account summary."""
    data = _get_terminal_data()
    return jsonify(data.get('account', {'connected': False}))


@app.route('/api/terminal/positions')
@login_required
def api_terminal_positions():
    """Get open positions."""
    from .mt5_terminal import get_positions as _gp
    return jsonify({'positions': _gp()})


@app.route('/api/terminal/history')
@login_required
def api_terminal_history():
    """Get order history (last 1 day)."""
    from .mt5_terminal import get_order_history as _gh
    return jsonify({'history': _gh(days=1)})


@app.route('/api/terminal/orders')
@login_required
def api_terminal_orders():
    """Get pending orders."""
    from .mt5_terminal import get_open_orders as _goo
    return jsonify({'orders': _goo()})


@app.route('/api/terminal/watch')
@login_required
def api_terminal_watch():
    """Get market watch data."""
    from .mt5_terminal import get_market_watch as _gmw
    return jsonify({'watch': _gmw()})


@app.route('/api/terminal/summary')
@login_required
def api_terminal_summary():
    """Full terminal dashboard summary (positions + account + p/l)."""
    data = _get_terminal_data()
    return jsonify(data)


@app.route('/api/terminal/place-order', methods=['POST'])
@login_required
def api_terminal_place_order():
    """Place a market order."""
    data = request.json
    from .mt5_terminal import place_order
    result = place_order(
        symbol=data.get('symbol', '').upper(),
        order_type=data.get('type', 'buy'),
        volume=float(data.get('volume', 0.01)),
        sl=float(data.get('sl', 0)),
        tp=float(data.get('tp', 0)),
        comment=data.get('comment', 'Yams Radar'),
    )
    return jsonify(result)


@app.route('/api/terminal/close-position', methods=['POST'])
@login_required
def api_terminal_close():
    """Close a position by ticket number."""
    data = request.json
    from .mt5_terminal import close_position
    result = close_position(
        ticket=int(data.get('ticket', 0)),
        volume=float(data.get('volume', 0)) or None,
    )
    return jsonify(result)


@app.route('/api/terminal/modify-position', methods=['POST'])
@login_required
def api_terminal_modify():
    """Modify SL/TP on a position."""
    data = request.json
    from .mt5_terminal import modify_position
    result = modify_position(
        ticket=int(data.get('ticket', 0)),
        sl=float(data.get('sl', 0)),
        tp=float(data.get('tp', 0)),
    )
    return jsonify(result)


# ─── Broker Sync Routes ───────────────────────────────────────────────────────

@app.route('/api/broker/config', methods=['GET'])
@login_required
def api_broker_get_config():
    """Get the current user's saved broker config (no password)."""
    from .broker_sync import load_config_safe
    user_id = session.get('user_id', session.get('username', 'anonymous'))
    cfg = load_config_safe(user_id)
    if cfg:
        return jsonify({"saved": True, "config": cfg})
    return jsonify({"saved": False, "config": None})


@app.route('/api/broker/config', methods=['POST'])
@login_required
def api_broker_save_config():
    """Save broker config for the current user."""
    from .broker_sync import save_config
    data = request.json
    user_id = session.get('user_id', session.get('username', 'anonymous'))
    result = save_config(
        user_id=user_id,
        broker_name=data.get('broker_name', 'My Broker'),
        server=data.get('server', ''),
        login=int(data.get('login', 0)),
        password=data.get('password', ''),
        account_type=data.get('account_type', 'demo'),
    )
    return jsonify({"success": True, "config": result})


@app.route('/api/broker/config', methods=['DELETE'])
@login_required
def api_broker_delete_config():
    """Delete the current user's saved broker config."""
    from .broker_sync import delete_config
    user_id = session.get('user_id', session.get('username', 'anonymous'))
    deleted = delete_config(user_id)
    return jsonify({"success": deleted})


@app.route('/api/broker/connect', methods=['POST'])
@login_required
def api_broker_connect():
    """Connect MT5 to a broker using saved or provided credentials."""
    from .broker_sync import connect_to_broker, load_config
    data = request.json
    user_id = session.get('user_id', session.get('username', 'anonymous'))

    login = data.get('login')
    password = data.get('password')
    server = data.get('server')

    # If no credentials provided, try saved config
    if not all([login, password, server]):
        saved = load_config(user_id)
        if saved:
            login = saved['login']
            password = saved['password']
            server = saved['server']

    if not all([login, password, server]):
        return jsonify({"success": False, "error": "No credentials provided and no saved config found"})

    result = connect_to_broker(
        login=int(login),
        password=password,
        server=server,
        user_id=user_id,
    )

    # Clear terminal cache on successful connect
    if result.get('success'):
        global _TERMINAL_CACHE, _TERMINAL_CACHE_AT
        _TERMINAL_CACHE = {}
        _TERMINAL_CACHE_AT = 0

    return jsonify(result)


@app.route('/api/broker/disconnect', methods=['POST'])
@login_required
def api_broker_disconnect():
    """Disconnect MT5 from current broker."""
    from .broker_sync import disconnect as broker_disconnect
    user_id = session.get('user_id', session.get('username', 'anonymous'))
    result = broker_disconnect(user_id=user_id)
    global _TERMINAL_CACHE, _TERMINAL_CACHE_AT
    _TERMINAL_CACHE = {}
    _TERMINAL_CACHE_AT = 0
    return jsonify(result)


@app.route('/api/broker/status', methods=['GET'])
@login_required
def api_broker_status():
    """Get current MT5 connection status and owner info."""
    from .broker_sync import get_connected_user_slug
    from .mt5_terminal import _MT5_INITIALIZED
    owner = get_connected_user_slug()
    return jsonify({
        "connected": _MT5_INITIALIZED,
        "owner": owner,
    })


@app.route('/api/broker/sync-trades', methods=['POST'])
@login_required
def api_broker_sync_trades():
    """Import MT5 history deals into the trading journal, deduplicated by ticket."""
    from .mt5_terminal import _ensure_mt5, HAS_MT5 as _HAS_MT5
    if not _HAS_MT5:
        return jsonify({"success": False, "error": "MetaTrader5 package not installed"})

    # Try to ensure MT5 is connected; fallback to saved broker config if needed
    if not _ensure_mt5():
        from .broker_sync import load_config, connect_to_broker
        user_id = session.get('user_id', session.get('username', 'anonymous'))
        saved = load_config(user_id)
        if saved and saved.get('server') and saved.get('login') and saved.get('password'):
            conn = connect_to_broker(int(saved['login']), saved['password'], saved['server'], user_id)
            if not conn.get('success'):
                return jsonify({"success": False, "error": "MT5 not connected and auto-reconnect failed. Please sync broker again."})
        else:
            return jsonify({"success": False, "error": "MT5 not connected. Use Sync Broker to connect first."})

    try:
        import MetaTrader5 as mt5
        from datetime import datetime, timedelta

        days = int(request.json.get('days', 90)) if request.json else 90
        now = datetime.now()
        from_dt = now - timedelta(days=days)
        deals = mt5.history_deals_get(from_dt, now)
        if deals is None:
            # Sometimes the terminal needs an extra moment to sync history
            # Try once more after a brief delay
            import time as _t
            _t.sleep(0.5)
            deals = mt5.history_deals_get(from_dt, now)
        if deals is None:
            err = mt5.last_error()
            return jsonify({
                "success": False,
                "error": f"No MT5 history available (err: {err}). Try syncing broker then wait a few seconds.",
            })
        if len(deals) == 0:
            return jsonify({"success": True, "imported": 0, "message": "No new trades found in MT5 history"})

        entries = _load_journal()
        existing_tickets = set()
        for e in entries:
            if e.get('mt5_ticket'):
                existing_tickets.add(int(e['mt5_ticket']))
            # Also match by order field if available
            if e.get('mt5_order'):
                existing_tickets.add(int(e['mt5_order']) * -1)  # negative to avoid collisions

        imported = 0
        for d in deals:
            ticket = d.ticket
            if ticket in existing_tickets:
                continue

            # Only import complete deal entries (entry=1 means "out" = closed trade)
            if d.entry != 1:
                continue

            order_type = 'Buy' if d.type == 0 else ('Sell' if d.type == 1 else f'Type_{d.type}')

            entry = {
                'symbol': d.symbol,
                'trade_title': f'{d.symbol} {order_type} (MT5)',
                'trade_status': 'closed',
                'position': order_type,
                'direction': order_type,
                'risk_amount': 0,
                'entry_price': d.price,
                'exit_price': d.price,
                'lot_size': d.volume,
                'stop_loss': 0,
                'take_profit': 0,
                'date_opened': datetime.fromtimestamp(d.time).strftime('%Y-%m-%dT%H:%M'),
                'date_closed': datetime.fromtimestamp(d.time).strftime('%Y-%m-%dT%H:%M'),
                'date': datetime.fromtimestamp(d.time).strftime('%Y-%m-%d %H:%M'),
                'pnl': round(d.profit + d.commission + d.swap, 2),
                'result': 'win' if (d.profit + d.commission + d.swap) > 0 else 'loss',
                'notes': f'Imported from MT5 | {d.comment or ""}',
                'entry_reason': '',
                'exit_reason': '',
                'mt5_ticket': ticket,
                'mt5_order': d.order,
                'mt5_commission': round(d.commission, 2),
                'mt5_swap': round(d.swap, 2),
                'mt5_time': datetime.fromtimestamp(d.time).strftime('%Y-%m-%d %H:%M'),
            }
            entries.append(entry)
            existing_tickets.add(ticket)
            imported += 1

        _save_journal(entries)
        # Clear terminal cache so fresh data loads
        global _TERMINAL_CACHE, _TERMINAL_CACHE_AT
        _TERMINAL_CACHE = {}
        _TERMINAL_CACHE_AT = 0

        return jsonify({
            "success": True,
            "imported": imported,
            "total": len(entries),
            "message": f"Imported {imported} trades from MT5 history ({len(entries)} total journal entries)",
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route('/')
@login_required
def index():
    """Main radar dashboard."""
    return render_template('radar.html', instruments=INSTRUMENTS,
                           symbols=get_symbols(), categories=CATEGORY_LABELS,
                           role=session.get('role'),
                           display_name=session.get('display_name'))


@app.route('/api/news')
@login_required
def api_news():
    """Serve news headlines and calendar events — uses full live sentiment.

    Query params:
        from_date (str):  Filter headlines from this date YYYY-MM-DD (optional)
        to_date (str):    Filter headlines until this date YYYY-MM-DD (optional)
        source (str):     Filter by source name (optional, e.g. 'ForexLive')
        limit (int):      Max headlines to return (default 25)
    """
    from datetime import datetime as _dt
    from .sentiment import analyze as sentiment_analyze
    from .eco_calendar import analyze as calendar_analyze
    from .config import Config

    from_date_str = request.args.get('from_date', '').strip()
    to_date_str = request.args.get('to_date', '').strip()
    source_filter = request.args.get('source', '').strip()
    keyword = request.args.get('keyword', '').strip().lower()
    limit_str = request.args.get('limit', '25').strip()

    try:
        limit = max(1, min(100, int(limit_str)))
    except (ValueError, TypeError):
        limit = 25

    sent_result = sentiment_analyze(quick=False)   # full live fetch for news tab
    cal_result = calendar_analyze()

    # AI enhancement: multi-LLM consensus on live headlines
    try:
        from .sentiment import enhance_with_ai
        from .data_fetcher import fetch_live_prices
        ai_prices = fetch_live_prices()
        enhance_with_ai(
            sent_result,
            live_prices=ai_prices,
            calendar_events=cal_result.events_this_week,
        )
    except Exception:
        pass  # Non-fatal — AI is a bonus

    # Parse date filters
    from_dt = None
    to_dt = None
    try:
        if from_date_str:
            from_dt = _dt.strptime(from_date_str, '%Y-%m-%d')
        if to_date_str:
            to_dt = _dt.strptime(to_date_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
    except (ValueError, TypeError):
        pass

    # Collect all matching headlines first
    headlines_raw = []
    for h in sent_result.headlines:
        # Source filter
        if source_filter and h.source.lower() != source_filter.lower():
            continue

        # Date filter
        if from_dt or to_dt:
            try:
                pub = _dt.fromisoformat(h.published.replace('Z', '+00:00'))
                if from_dt and pub < from_dt:
                    continue
                if to_dt and pub > to_dt:
                    continue
            except (ValueError, TypeError):
                pass  # include headlines with unparseable dates

        # Keyword filter
        if keyword and keyword not in h.title.lower():
            if not any(keyword in kw.lower() for kw in h.keywords or []):
                continue

        headlines_raw.append({
            'source': h.source,
            'title': h.title,
            'published': h.published,
            'sentiment': round(h.sentiment_score, 3),
            'keywords': h.keywords,
        })

    # Sort by published date (newest first), then truncate to limit
    def _sort_key(h):
        try:
            return _dt.fromisoformat(h['published'].replace('Z', '+00:00'))
        except:
            return _dt.min
    headlines_raw.sort(key=_sort_key, reverse=True)
    # Mix sources by round-robin when timestamps are identical
    mixed = []
    by_source = {}
    for h in headlines_raw:
        by_source.setdefault(h['source'], []).append(h)
    sources = list(by_source.keys())
    # Round-robin: take one from each source in turn
    while any(by_source[s] for s in sources):
        for s in sources:
            if by_source[s]:
                mixed.append(by_source[s].pop(0))
    headlines = mixed[:limit]

    events = []
    for e in cal_result.events_this_week[:30]:
        events.append({
            'time': e.time,
            'currency': e.currency,
            'event': e.event,
            'impact': e.impact,
            'actual': e.actual,
            'forecast': e.forecast,
            'previous': e.previous,
            'beat_miss': e.beat_miss,
            'volatility': e.volatility,
            'affected_pairs': e.affected_pairs,
            'day_label': e.day_label,
            'time_short': e.time_short,
        })

    cb_events = []
    for e in cal_result.central_bank_events[:10]:
        cb_events.append({
            'time': e.time,
            'currency': e.currency,
            'event': e.event,
            'impact': e.impact,
        })

    # Cross-reference data for source comparison
    cross_refs = []
    for cr in sent_result.cross_references[:10]:
        cross_refs.append({
            'topic': cr.topic,
            'source_sentiments': cr.source_sentiments,
            'consensus': cr.consensus,
            'agreement_level': cr.agreement_level,
            'discrepancy_flag': cr.discrepancy_flag,
        })

    # Source accuracy / credibility
    src_accuracy = []
    for sa in sent_result.source_accuracy:
        src_accuracy.append({
            'source': sa.source,
            'articles_scraped': sa.articles_scraped,
            'avg_relevance': sa.avg_relevance,
            'unique_topics': sa.unique_topics,
            'credibility_score': sa.credibility_score,
        })

    return jsonify({
        'headlines': headlines,
        'calendar_events': events,
        'central_bank_events': cb_events,
        'sentiment_score': sent_result.overall_score,
        'trending_topics': sent_result.trending_topics[:8],
        'source_breakdown': sent_result.source_breakdown,
        'dovish_count': sent_result.dovish_count,
        'hawkish_count': sent_result.hawkish_count,
        'risk_on_count': sent_result.risk_on_count,
        'risk_off_count': sent_result.risk_off_count,
        'high_impact_count': cal_result.high_impact_count,
        'calendar_source': cal_result.source,
        'cross_references': cross_refs,
        'source_accuracy': src_accuracy,
        'ai_analysis': _serialize_ai_analysis(sent_result),
    })


def _serialize_ai_analysis(sent_result) -> Optional[dict]:
    """Serialize AI consensus analysis for the JSON API response.

    Returns None when AI analysis is not available (no API keys, or not yet run).
    """
    ai = getattr(sent_result, 'ai_analysis', None)
    if ai is None:
        return None
    return {
        'overall_score': ai.overall_score,
        'confidence': ai.confidence,
        'market_sentiment': ai.market_sentiment,
        'risk_appetite': ai.risk_appetite,
        'key_themes': ai.key_themes[:8],
        'explanation': ai.explanation,
        'models_used': ai.models_used,
        'cached': ai.cached,
        'latency_ms': ai.latency_ms,
    }


@app.route('/api/calendar')
@login_required
def api_calendar():
    """Economic calendar with date range — serves events + news + live rates.

    Query params:
        from (str): Start date YYYY-MM-DD (default: 2 months ago)
        to (str):   End date YYYY-MM-DD (default: today)
        currency (str): Filter by currency code (optional)
        impact (str):   Filter by impact level (optional)
    """
    from datetime import date as date_type
    from .eco_calendar import analyze_range, analyze as cal_analyze

    # Parse query params
    query_from = request.args.get('from', '')
    query_to = request.args.get('to', '')
    filter_currency = request.args.get('currency', '').upper()
    filter_impact = request.args.get('impact', '').capitalize()

    today = date_type.today()

    # Default range: 2 months back to today
    if not query_from:
        from_d = today - timedelta(days=60)
    else:
        try:
            from_d = datetime.strptime(query_from, '%Y-%m-%d').date()
        except ValueError:
            from_d = today - timedelta(days=60)

    if not query_to:
        to_d = today + timedelta(days=7)  # include near future
    else:
        try:
            to_d = datetime.strptime(query_to, '%Y-%m-%d').date()
        except ValueError:
            to_d = today + timedelta(days=7)

    start_str = from_d.strftime('%Y-%m-%d')
    end_str = to_d.strftime('%Y-%m-%d')

    # Fetch events
    cal_result = analyze_range(start_str, end_str)
    raw_events = cal_result.events_this_week

    # Apply filters
    if filter_currency:
        raw_events = [e for e in raw_events if e.currency == filter_currency]
    if filter_impact:
        raw_events = [e for e in raw_events if e.impact == filter_impact]

    # Group events by date
    grouped = {}
    for e in raw_events:
        day = e.time[:10]
        if day not in grouped:
            grouped[day] = []
        grouped[day].append({
            'time': e.time,
            'time_short': e.time_short,
            'currency': e.currency,
            'event': e.event,
            'impact': e.impact,
            'actual': e.actual,
            'forecast': e.forecast,
            'previous': e.previous,
            'beat_miss': e.beat_miss,
            'volatility': e.volatility,
            'affected_pairs': e.affected_pairs,
            'is_past': e.is_past,
            'day_label': e.day_label,
        })

    return jsonify({
        'from': start_str,
        'to': end_str,
        'total_events': len(raw_events),
        'high_impact_count': cal_result.high_impact_count,
        'source': cal_result.source,
        'fundamental_score': cal_result.fundamental_score,
        'grouped_events': grouped,
        'cb_events': [{
            'time': e.time,
            'currency': e.currency,
            'event': e.event,
            'impact': e.impact,
        } for e in cal_result.central_bank_events],
    })


@app.route('/api/live-rates')
@login_required
def api_live_rates():
    """Live forex rates for major pairs — bid, change, spread."""
    from .data_fetcher import fetch_live_prices
    from .config import Config

    # Display format (with /) → internal symbol format (no /)
    majors_display = ['EUR/USD', 'GBP/USD', 'USD/JPY', 'USD/CHF',
                      'USD/CAD', 'AUD/USD', 'NZD/USD', 'XAU/USD']
    # fetch_live_prices uses INSTRUMENTS keys which have no slash
    majors_internal = [s.replace('/', '') for s in majors_display]

    prices = fetch_live_prices(majors_internal)
    cfg = Config()

    # Build a lookup from internal name → displayed name
    display_lookup = dict(zip(majors_internal, majors_display))

    rates = []
    for sym_int in majors_internal:
        price = prices.get(sym_int)
        sym_display = display_lookup[sym_int]
        if price and price > 0:
            spy = (price * 10000) if sym_int not in ('USDJPY', 'XAUUSD') else (price * 100)
            rates.append({
                'symbol': sym_display,
                'bid': round(price, 5),
                'change_6h': 0.0,
                'change_pct_6h': 0.0,
                'pip_factor': INSTRUMENTS.get(sym_display, {}).get('pip_factor', 0.0001),
            })

    return jsonify({'rates': rates, 'count': len(rates)})


@app.route('/api/scan')
@login_required
def api_scan():
    """Run radar scan, return JSON. Uses cache for 60s."""
    result = _get_cached_radar()
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
            'ai': _serialize_ai_analysis(result.sentiment),
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
            # MATE Framework fields
            'mate_market': i.explanation.mate.market_label if i.explanation.mate else None,
            'mate_market_detail': i.explanation.mate.market_detail if i.explanation.mate else None,
            'mate_market_score': i.explanation.mate.market_score if i.explanation.mate else None,
            'mate_area': i.explanation.mate.area_label if i.explanation.mate else None,
            'mate_area_detail': i.explanation.mate.area_detail if i.explanation.mate else None,
            'mate_area_score': i.explanation.mate.area_score if i.explanation.mate else None,
            'mate_timing': i.explanation.mate.timing_label if i.explanation.mate else None,
            'mate_timing_detail': i.explanation.mate.timing_detail if i.explanation.mate else None,
            'mate_timing_score': i.explanation.mate.timing_score if i.explanation.mate else None,
            'mate_exit_summary': i.explanation.mate.exit_plan.summary if i.explanation.mate else None,
            'mate_exit_tp': i.explanation.mate.exit_plan.tp if i.explanation.mate else None,
            'mate_exit_sl': i.explanation.mate.exit_plan.sl if i.explanation.mate else None,
            'mate_exit_tp_distance': i.explanation.mate.exit_plan.tp_distance if i.explanation.mate else None,
            'mate_exit_sl_distance': i.explanation.mate.exit_plan.sl_distance if i.explanation.mate else None,
            'mate_quality': i.explanation.mate.overall_quality if i.explanation.mate else 'neutral',
            'mate_drivers': i.explanation.mate.drivers if i.explanation.mate else [],
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


# ─── Intelligent Chat Engine (rules-based, no API key needed) ───────────

_CHAT_MEMORY: dict[str, list[dict]] = {}  # session_id -> [{role, content, symbol?}]

TRADING_SYNONYMS = {
    'eurusd': 'EURUSD', 'gbpusd': 'GBPUSD', 'usdjpy': 'USDJPY',
    'usdchf': 'USDCHF', 'usdcad': 'USDCAD', 'audusd': 'AUDUSD',
    'nzdusd': 'NZDUSD', 'gbpjpy': 'GBPJPY', 'eurjpy': 'EURJPY',
    'eurgbp': 'EURGBP', 'gpbjpy': 'GBPJPY', 'xauusd': 'XAUUSD',
    'xagusd': 'XAGUSD', 'gold': 'XAUUSD', 'ginto': 'XAUUSD',
    'silver': 'XAGUSD', 'pilak': 'XAGUSD', 'oil': 'XTIUSD',
    'langis': 'XTIUSD', 'crude': 'XTIUSD', 'brent': 'XBRUSD',
    'btc': 'BTCUSD', 'bitcoin': 'BTCUSD', 'eth': 'ETHUSD',
    'ethereum': 'ETHUSD', 'sp500': 'SP500', 's&p': 'SP500',
    'dow': 'US30', 'us30': 'US30', 'nasdaq': 'NAS100',
    'nas100': 'NAS100', 'dax': 'DAX40', 'ftse': 'FTSE100',
    'nikkei': 'JP225', 'aapl': 'AAPL', 'apple': 'AAPL',
    'tsla': 'TSLA', 'tesla': 'TSLA', 'goog': 'GOOG',
    'google': 'GOOG', 'amzn': 'AMZN', 'amazon': 'AMZN',
    'msft': 'MSFT', 'microsoft': 'MSFT',
}

CATEGORY_KEYWORDS = {
    'major': ['major', 'majors', 'forex', 'fx', 'currency'],
    'cross': ['cross', 'crosses'],
    'index': ['index', 'indices', 'indicator'],
    'commodity': ['commodity', 'commodities', 'metal', 'metals', 'gold', 'silver', 'oil'],
    'stock': ['stock', 'stocks', 'equity', 'equities', 'share', 'shares'],
}

INTENT_PATTERNS = [
    # Most specific first — order matters!
    ('account', ['my balance', 'my equity', 'my margin', 'open positions', 'my trades',
                  'mt5 balance', 'account info', 'account summary', 'terminal']),
    ('journal_lookup', ['journal', 'my performance', 'my trading record', 'win rate',
                         'profit factor', 'my pnl', 'my P&L', 'track record']),
    ('candlestick', ['candlestick', 'candle pattern', 'doji', 'hammer', 'engulfing',
                     'pin bar', 'shooting star', 'morning star', 'evening star']),
    ('chart_pattern', ['chart pattern', 'head and shoulder', 'double top', 'double bottom',
                        'triangle', 'flag pattern', 'wedge']),
    ('fibonacci', ['fibonacci', 'fib level', 'fib retracement', 'golden ratio']),
    ('psychology', ['psychology', 'mindset', 'discipline', 'revenge trading', 'fomo',
                    'trading emotion', 'mental']),
    ('sessions', ['trading session', 'london session', 'new york session', 'market hours',
                  'when to trade', 'best time to trade', 'session times',
                  'london open', 'london close', 'ny open', 'ny close',
                  'tokyo open', 'asia session', 'sydney session']),
    ('position_sizing', ['position size', 'lot size', 'how many lots', 'risk per trade',
                          'sizing']),
    ('diversification', ['diversify', 'diversification', 'portfolio', 'correlation']),
    ('weather', ['weather', 'panahon', 'news today']),
    ('news_events', ['news', 'balita', 'calendar', 'event', 'eco', 'economic']),
    ('pretrade', ['pretrade', 'pre.trade', 'checklist', 'check trade', 'double check', 'bago pumasok',
                  'review trade', 'tama ba', 'sigurado', 'ready to trade', 'go or no go',
                  'trade check', 'coach me', 'coaching']),
    ('compare', ['compare', 'vs', 'versus', 'difference', 'pagitan', 'ikumpara']),
    ('why', ['why', 'bakit', 'reason', 'dahilan', 'explain', 'ipaliwanag']),
    ('best', ['best', 'top', 'strongest', 'pinakamalakas', 'recommend', 'suggest', 'subukan']),
    ('worst', ['worst', 'weakest', 'bottom', 'pinakamahina']),
    ('market_overview', ['market', 'overview', 'summary', 'how.*market', 'overall', 'general', 'sentiment', 'merkado']),
    ('risk', ['risk', 'risk_on', 'risk_off', 'appetite']),
    ('technical', ['technical', 'teknikal', 'chart', 'trend', 'rsi', 'macd', 'adx']),
    ('fundamental', ['fundamental', 'pundamental', 'central bank', 'cb', 'rate', 'interest', 'cot']),
    ('strategy', ['strategy', 'estratchiya', 'trade', 'entry', 'signal', 'setup']),
    ('education', ['what is', 'ano ang', 'how to', 'paano', 'meaning', 'ibig sabihin', 'help', 'tulong']),
    ('greeting', ['hello', 'hi there', 'hey', 'good morning', 'good evening', 'kamusta', 'magandang', 'salamat']),
]

TAGALOG_WORDS = {
    'bumili': 'buy', 'bili': 'buy', 'bilhin': 'buy', 'tumataas': 'rising',
    'pagtaas': 'increase', 'lakas': 'strong', 'malakas': 'strong',
    'mababa': 'low', 'bumaba': 'went_down', 'ibaba': 'down', 'pababa': 'downward',
    'tumaas': 'went_up', 'tataas': 'will_rise', 'bababa': 'will_fall',
    'pera': 'money', 'salapi': 'currency', 'piso': 'peso', 'halaga': 'value',
    'negosyo': 'business', 'merkado': 'market', 'palitan': 'exchange',
    'puhunan': 'capital', 'tubo': 'profit', 'luging': 'loss',
    'panalo': 'winner', 'kita': 'earnings', 'gastos': 'expenses',
    'oras': 'time', 'araw': 'day', 'linggo': 'week', 'buwan': 'month',
    'ngayon': 'now', 'kahapon': 'yesterday', 'bukas': 'tomorrow',
    'ano': 'what', 'bakit': 'why', 'paano': 'how', 'saan': 'where',
    'magkano': 'how_much', 'mabagal': 'slow', 'mabilis': 'fast',
    'malaki': 'big', 'maliit': 'small', 'maganda': 'good', 'masama': 'bad',
    'posisyon': 'position', 'ginto': 'gold', 'pilak': 'silver', 'langis': 'oil',
    'sapi': 'stock', 'dolyar': 'dollar', 'euro': 'euro',
}

_TAGALOG_DETECT_WORDS = {
    'ang', 'ay', 'ko', 'mo', 'siya', 'sila', 'kami', 'tayo',
    'ito', 'iyan', 'doon', 'dito', 'ng', 'sa', 'mga', 'po',
    'opo', 'oo', 'hindi', 'wala', 'meron', 'may', 'kasi',
    'kaya', 'kung', 'pwede', 'pwedeng', 'dapat', 'kailangan', 'gusto',
    'yung', 'yong', 'mong', 'kong', 'kamusta', 'magkano',
}


def _detect_tagalog(text: str) -> bool:
    lower = text.lower().split()
    hits = sum(1 for w in lower if w in _TAGALOG_DETECT_WORDS)
    return hits >= 2


def _translate_tagalog(text: str) -> str:
    t = text.lower()
    for tl, en in TAGALOG_WORDS.items():
        t = t.replace(tl, en)
    return t


def _detect_intent(q: str) -> str:
    """Classify user question into an intent category."""
    for intent, patterns in INTENT_PATTERNS:
        for pat in patterns:
            import re
            if re.search(pat, q, re.IGNORECASE):
                return intent
    return 'general'


def _extract_symbols(q: str) -> list[str]:
    """Extract instrument symbols from question text."""
    found = set()
    ql = q.upper().replace(' ', '').replace('/', '')
    for alias, sym in TRADING_SYNONYMS.items():
        if alias.upper() in ql or alias.lower() in q.lower():
            found.add(sym)
    # Direct symbol match (e.g. "EURUSD", "XAUUSD")
    for sym in INSTRUMENTS:
        if sym.lower() in q.lower():
            found.add(sym)
    return list(found)


def _extract_categories(q: str) -> list[str]:
    """Extract category from question."""
    ql = q.lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in ql:
                return [cat]
    return []


def _format_change(pct: float) -> str:
    if pct > 0:
        return f"📈 +{pct:.2f}%"
    elif pct < 0:
        return f"📉 {pct:.2f}%"
    return "➡️ 0.00%"


def _bias_emoji(bias: str) -> str:
    return {'Strong Buy': '🟢', 'Buy': '✅', 'Neutral': '⚪', 'Sell': '🔴', 'Strong Sell': '⛔'}.get(bias, '⚪')


def _generate_instrument_analysis(symbol: str, i, is_tagalog: bool) -> str:
    """Generate a dynamic narrative analysis for one instrument."""
    emoji = _bias_emoji(i.bias)
    change = _format_change(i.change_pct)

    # Score interpretation
    if abs(i.bias_score) >= 60:
        strength_word = "very strong" if not is_tagalog else "napakalakas"
    elif abs(i.bias_score) >= 30:
        strength_word = "moderate" if not is_tagalog else "katamtaman"
    else:
        strength_word = "mild" if not is_tagalog else "mahina"

    # Trend description
    trend_map = {
        'uptrend': 'moving higher' if not is_tagalog else 'tumataas',
        'downtrend': 'moving lower' if not is_tagalog else 'bumababa',
        'ranging': 'moving sideways' if not is_tagalog else 'gumagalaw patagilid',
    }
    trend_desc = trend_map.get(i.explanation.trend_direction, 'ranging')

    # Entry timing
    timing_advice = ''
    if i.explanation.entry_timing == 'now':
        timing_advice = 'Prime entry window open now.' if not is_tagalog else 'Magandang oras para pumasok ngayon.'
    elif i.explanation.entry_timing == 'soon':
        timing_advice = 'Good entry approaching soon.' if not is_tagalog else 'Malapit na magandang oras para pumasok.'
    elif i.explanation.entry_timing == 'wait':
        timing_advice = 'Better to wait for a clearer signal.' if not is_tagalog else 'Maghintay para sa mas malinaw na signal.'

    # Session context
    session_note = ''
    if i.explanation.session_quality in ('high', 'active'):
        session_note = 'Liquidity is good.' if not is_tagalog else 'Maganda ang liquidity.'
    elif i.explanation.session_quality == 'quiet':
        session_note = 'Volatility is low — expect slower moves.' if not is_tagalog else 'Mababa ang galaw.'

    # Key level context
    current = i.price
    support = i.explanation.key_support
    resistance = i.explanation.key_resistance
    range_pct = abs(resistance - support) / current * 100 if current > 0 else 0

    # AOV position description
    aov_desc = i.explanation.aov_position.replace('_', ' ')
    aov_note = ''
    if 'support' in i.explanation.aov_position:
        aov_note = 'Price is near a support zone.' if not is_tagalog else 'Malapit ang presyo sa suporta.'
    elif 'resistance' in i.explanation.aov_position:
        aov_note = 'Price is near a resistance zone.' if not is_tagalog else 'Malapit ang presyo sa resistensiya.'

    # Build narrative
    if is_tagalog:
        bias_tl = {'Strong Buy': 'Malakas na Bili', 'Buy': 'Bili',
                   'Neutral': 'Neutral', 'Sell': 'Benta',
                   'Strong Sell': 'Malakas na Benta'}.get(i.bias, i.bias)
        lines = [
            f"{emoji} **{i.symbol}** ({i.name}) — **{bias_tl}**",
            f"Presyo: **${i.price:.5f}** {change}",
            f"",
            f"Ang presyo ay {trend_desc} na may {strength_word} na bias "
            f"(iskor: {i.bias_score:+.0f}, kumpiyansa: {i.confidence}%).",
            f"",
            f"📊 **Teknikal:** {i.explanation.technical_score:+.0f} | "
            f"🏛️ **Pundamental:** {i.explanation.fundamental_score:+.0f} | "
            f"📰 **Sentimyento:** {i.explanation.sentiment_score:+.0f}",
            f"",
            f"🔑 Support: {support:.5f} | Resistance: {resistance:.5f}",
        ]
        if aov_note:
            lines.append(f"📍 {aov_note}")
        if timing_advice:
            lines.append(f"⏰ {timing_advice}")
        if session_note:
            lines.append(f"🌍 {session_note}")
    else:
        confidence_note = ''
        if i.confidence >= 70:
            confidence_note = 'High conviction signal.'
        elif i.confidence >= 50:
            confidence_note = 'Moderate conviction.'
        else:
            confidence_note = 'Low conviction — needs confirmation.'

        lines = [
            f"{emoji} **{i.symbol}** ({i.name}) — **{i.bias}** (score {i.bias_score:+.0f})",
            f"Price: **${i.price:.5f}** {change}  |  Confidence: {i.confidence}% ({confidence_note})",
            f"",
            f"The price is **{trend_desc}** with {strength_word} {i.explanation.trend_strength} strength. "
            f"Signal strength: {i.strength}/10.",
            f"",
            f"📊 **Technical:** {i.explanation.technical_score:+.0f} | "
            f"🏛️ **Fundamental:** {i.explanation.fundamental_score:+.0f} | "
            f"📰 **Sentiment:** {i.explanation.sentiment_score:+.0f}",
            f"",
            f"🔑 **Key Support:** {support:.5f}  |  **Key Resistance:** {resistance:.5f}",
        ]
        if aov_note:
            lines.append(f"📍 **AOV:** {aov_desc} — {aov_note}")
        if timing_advice:
            lines.append(f"⏰ **Timing:** {timing_advice}")
        if session_note:
            lines.append(f"🌍 **Session:** {i.explanation.session_quality} — {session_note}")

    # Fundamental breakdown
    fb = i.explanation.fundamental_breakdown
    if fb:
        if is_tagalog:
            lines.append(f"\n🏛️ **Mga Salik:** Paglago {fb.growth:+.0f}, "
                        f"Implasyon {fb.inflation:+.0f}, Trabaho {fb.jobs:+.0f}, "
                        f"Sentimyento {fb.sentiment:+.0f}, Trend {fb.trend:+.0f}")
        else:
            lines.append(f"\n🏛️ **Fundamental factors:** Growth {fb.growth:+.0f}, "
                        f"Inflation {fb.inflation:+.0f}, Jobs {fb.jobs:+.0f}, "
                        f"Sentiment {fb.sentiment:+.0f}, Trend {fb.trend:+.0f}")

    return '\n'.join(lines)


def _generate_market_overview(r, is_tagalog: bool) -> tuple[str, list[str]]:
    """Generate market overview narrative."""
    market = r.market_sentiment
    score = r.market_score

    # Interpret market mood
    if score >= 50:
        mood = "strongly bullish — risk appetite is high across all assets." if not is_tagalog else "malakas ang bullish — mataas ang risk appetite."
    elif score >= 20:
        mood = "leaning bullish with selective strength." if not is_tagalog else "medyo bullish ngunit pumipili."
    elif score > -20:
        mood = "mixed — no clear directional bias." if not is_tagalog else "magkahalo — walang malinaw na direksyon."
    elif score > -50:
        mood = "leaning bearish — caution warranted." if not is_tagalog else "medyo bearish — kailangan ng ingat."
    else:
        mood = "strongly bearish — risk-off mode across the board." if not is_tagalog else "malakas ang bearish — iwas sa risk."

    bulls = [i for i in r.instruments if i.bias in ('Strong Buy', 'Buy')]
    bears = [i for i in r.instruments if i.bias in ('Strong Sell', 'Sell')]
    neutrals = [i for i in r.instruments if i.bias == 'Neutral']

    if is_tagalog:
        answer = (
            f"📊 **Pangkalahatang Merkado**\n\n"
            f"Ang merkado ay {mood}\n"
            f"**Iskor:** {score:+.1f} | **Sentimyento:** {market}\n\n"
            f"**Bullish:** {len(bulls)} na instrumento | "
            f"**Bearish:** {len(bears)} | "
            f"**Neutral:** {len(neutrals)}\n\n"
            f"📰 **Balita:** {r.sentiment.overall_score:+.1f} "
            f"(dovish: {r.sentiment.dovish_count}, hawkish: {r.sentiment.hawkish_count})\n"
            f"🏛️ **Pundamental:** {r.fundamental.overall_score:+.1f} ({r.fundamental.risk_sentiment})\n"
            f"📅 **High impact events:** {r.calendar.high_impact_count} ngayong linggo"
        )
    else:
        answer = (
            f"📊 **Market Overview**\n\n"
            f"The overall market is **{mood}**\n"
            f"**Score:** {score:+.1f} | **Sentiment:** {market} | "
            f"**Instruments:** {len(r.instruments)}\n\n"
            f"🟢 **Bullish:** {len(bulls)}  |  "
            f"🔴 **Bearish:** {len(bears)}  |  "
            f"⚪ **Neutral:** {len(neutrals)}\n\n"
            f"📰 **News sentiment:** {r.sentiment.overall_score:+.1f} "
            f"(dovish: {r.sentiment.dovish_count}, hawkish: {r.sentiment.hawkish_count})\n"
            f"🏛️ **Fundamental:** {r.fundamental.overall_score:+.1f} ({r.fundamental.risk_sentiment})\n"
            f"📅 **High-impact events:** {r.calendar.high_impact_count} this week"
        )

    # Follow-up suggestions
    top_bull = [i for i in r.instruments if i.bias in ('Strong Buy', 'Buy')][:3]
    top_bear = [i for i in r.instruments if i.bias in ('Strong Sell', 'Sell')][:3]
    suggests = []
    if top_bull:
        suggests.append(f"Why is {top_bull[0].symbol} bullish?")
    if top_bear:
        suggests.append(f"Why is {top_bear[0].symbol} bearish?")
    suggests.append("What's the news today?")
    suggests.append("Compare XAUUSD and EURUSD")

    return answer, suggests


def _generate_instrument_answer(symbols: list[str], r, question: str, is_tagalog: bool) -> tuple[str, list[str]]:
    """Generate analysis for one or more instruments."""
    instruments = [i for i in r.instruments if i.symbol in symbols]
    if not instruments:
        # Try fuzzy match — any instruments whose name contains part of the question
        ql = question.lower()
        instruments = [i for i in r.instruments
                       if any(word in i.name.lower() for word in ql.split()
                              if len(word) > 3)]
    if not instruments:
        if is_tagalog:
            return f"Hindi ko mahanap ang '{', '.join(symbols)}'. Subukan ang EURUSD, XAUUSD, o SP500.", [
                "Show me EURUSD", "Show me XAUUSD", "Market overview"]
        return f"I couldn't find '{', '.join(symbols)}'. Try EURUSD, XAUUSD, or SP500.", [
            "Show me EURUSD", "Show me XAUUSD", "Market overview"]

    answers = []
    for i in instruments[:3]:
        answers.append(_generate_instrument_analysis(i.symbol, i, is_tagalog))

    suggests = []
    primary = instruments[0].symbol
    if len(instruments) == 1:
        others = [x.symbol for x in r.instruments if x.category == instruments[0].category and x.symbol != primary][:2]
        if others:
            suggests.append(f"Compare {primary} vs {others[0]}")
        suggests.append(f"Why is {primary} {'bullish' if instruments[0].bias in ('Strong Buy', 'Buy') else 'bearish' if instruments[0].bias in ('Strong Sell', 'Sell') else 'neutral'}?")
    suggests.append("Market overview")
    suggests.append("What's new in the news?")

    return '\n\n---\n\n'.join(answers), suggests


def _generate_category_answer(cats: list[str], r, is_tagalog: bool) -> tuple[str, list[str]]:
    """Generate category-level analysis."""
    cat = cats[0]
    cat_list = [i for i in r.instruments if i.category == cat]
    if not cat_list:
        return f"No instruments in {cat}." if not is_tagalog else f"Walang instrument sa {cat}.", ["Market overview"]

    label = CATEGORY_LABELS.get(cat, cat.upper())
    bulls = [i for i in cat_list if i.bias in ('Strong Buy', 'Buy')]
    bears = [i for i in cat_list if i.bias in ('Strong Sell', 'Sell')]
    neut = [i for i in cat_list if i.bias == 'Neutral']

    avg_score = sum(i.bias_score for i in cat_list) / len(cat_list)
    top_bull = bulls[:2] if bulls else []
    top_bear = bears[:2] if bears else []

    if is_tagalog:
        answer = (
            f"📈 **{label}** ({len(cat_list)} na instrumento)\n\n"
            f"**Average bias:** {avg_score:+.1f}\n"
            f"🟢 Bullish: {len(bulls)} | 🔴 Bearish: {len(bears)} | ⚪ Neutral: {len(neut)}\n\n"
        )
        if top_bull:
            answer += f"**Pinakamalakas:** " + ', '.join(f"{i.symbol} ({i.bias}, {i.strength}/10)" for i in top_bull) + "\n"
        if top_bear:
            answer += f"**Pinakamahina:** " + ', '.join(f"{i.symbol} ({i.bias}, {i.strength}/10)" for i in top_bear) + "\n"
    else:
        answer = (
            f"📈 **{label}** ({len(cat_list)} instruments)\n\n"
            f"**Average bias score:** {avg_score:+.1f}\n"
            f"🟢 Bullish: {len(bulls)} | 🔴 Bearish: {len(bears)} | ⚪ Neutral: {len(neut)}\n\n"
        )
        if top_bull:
            answer += f"**Strongest:** " + ', '.join(f"{i.symbol} ({i.bias}, strength {i.strength}/10)" for i in top_bull) + "\n"
        if top_bear:
            answer += f"**Weakest:** " + ', '.join(f"{i.symbol} ({i.bias}, strength {i.strength}/10)" for i in top_bear) + "\n"

    answer += f"\n💡 Ask about a specific symbol for detailed analysis."

    suggests = []
    if top_bull:
        suggests.append(f"Why is {top_bull[0].symbol} bullish?")
    if top_bear:
        suggests.append(f"Why is {top_bear[0].symbol} bearish?")
    suggests.append("Market overview")

    return answer, suggests


def _generate_comparison_answer(symbols: list[str], r, is_tagalog: bool) -> tuple[str, list[str]]:
    """Compare two or more instruments."""
    insts = [i for i in r.instruments if i.symbol in symbols]
    if len(insts) < 2:
        if is_tagalog:
            return "Kailangan ko ng dalawang instrument para ikumpara.", ["Show me EURUSD", "Market overview"]
        return "I need at least two instruments to compare.", ["Show me EURUSD", "Market overview"]

    rows = []
    for i in insts:
        emoji = _bias_emoji(i.bias)
        rows.append(
            f"| {emoji} **{i.symbol}** | ${i.price:.5f} | {i.bias} ({i.bias_score:+.0f}) | "
            f"{i.confidence}% | {i.explanation.trend_direction} | "
            f"{i.explanation.technical_score:+.0f} / {i.explanation.fundamental_score:+.0f}"
        )

    table = "| Symbol | Price | Bias | Conf | Trend | TA/FA |\n|-------|-------|------|------|-------|-------|\n" + '\n'.join(rows)

    if is_tagalog:
        answer = f"📊 **Paghahambing**\n\n{table}\n\n💡 Magtanong tungkol sa specific na instrument para sa detalye."
    else:
        answer = f"📊 **Comparison**\n\n{table}\n\n💡 Ask about a specific instrument for detailed analysis."

    suggests = [f"Why is {insts[0].symbol} {insts[0].bias.lower()}?",
                f"Why is {insts[1].symbol} {insts[1].bias.lower()}?",
                "Market overview"]
    return answer, suggests


def _generate_news_answer(r, is_tagalog: bool) -> tuple[str, list[str]]:
    """Generate news/calendar summary."""
    if is_tagalog:
        lines = [
            "📰 **Balita at Kaganapan**\n",
            f"**Sentimyento ng Balita:** {r.sentiment.overall_score:+.1f}",
            f"Dovish: {r.sentiment.dovish_count}  |  Hawkish: {r.sentiment.hawkish_count}",
            f"Risk-on: {r.sentiment.risk_on_count}  |  Risk-off: {r.sentiment.risk_off_count}",
        ]
    else:
        lines = [
            "📰 **News & Calendar**\n",
            f"**News Sentiment:** {r.sentiment.overall_score:+.1f}",
            f"Dovish: {r.sentiment.dovish_count}  |  Hawkish: {r.sentiment.hawkish_count}",
            f"Risk-on: {r.sentiment.risk_on_count}  |  Risk-off: {r.sentiment.risk_off_count}",
        ]

    if r.calendar.high_impact_count > 0:
        cal = r.calendar
        if is_tagalog:
            lines.append(f"\n📅 **Mataas na epekto:** {cal.high_impact_count} kaganapan ngayong linggo")
        else:
            lines.append(f"\n📅 **High-impact events:** {cal.high_impact_count} this week")

        # Show next event
        if cal.next_high_impact:
            e = cal.next_high_impact
            if is_tagalog:
                lines.append(f"  Susunod: **{e.event}** ({e.currency}) — {e.time}")
            else:
                lines.append(f"  Next: **{e.event}** ({e.currency}) — {e.time}")

    # Trending topics
    if r.sentiment.trending_topics:
        topics = ', '.join(r.sentiment.trending_topics[:5])
        lines.append(f"\n🔥 **Trending:** {topics}")

    suggests = ["Market overview", "Show me EURUSD", "What's the best trade now?"]
    return '\n'.join(lines), suggests


def _generate_why_answer(symbols: list[str], question: str, r, is_tagalog: bool) -> tuple[str, list[str]]:
    """Answer 'why' questions about instrument bias."""
    if not symbols:
        # Try to get the last discussed symbol from memory
        if is_tagalog:
            return "Sino ang gusto mong pagusapan? Halimbawa: 'Bakit bearish ang XAUUSD?'", \
                   ["Why is XAUUSD bearish?", "Why is EURUSD bullish?", "Market overview"]
        return "Which instrument? Try: 'Why is XAUUSD bearish?' or 'Why is EURUSD neutral?'", \
               ["Why is XAUUSD bearish?", "Why is EURUSD bullish?", "Market overview"]

    return _generate_instrument_answer(symbols, r, question, is_tagalog)


def _generate_strategy_answer(symbols: list[str], question: str, r, is_tagalog: bool) -> tuple[str, list[str]]:
    """Answer strategy/trade-related questions."""
    if not symbols:
        # Show top setups
        bulls = [i for i in r.instruments if i.bias in ('Strong Buy', 'Buy')][:3]
        bears = [i for i in r.instruments if i.bias in ('Strong Sell', 'Sell')][:3]
        if is_tagalog:
            ans = "**Mga Setup ngayon:**\n\n"
            ans += "**🟢 Bumili:** " + (', '.join(f"{i.symbol} (lakas {i.strength}/10)" for i in bulls) if bulls else "Wala")
            ans += "\n\n**🔴 Benta:** " + (', '.join(f"{i.symbol} (lakas {i.strength}/10)" for i in bears) if bears else "Wala")
        else:
            ans = "**Top Setups Right Now:**\n\n"
            ans += "**🟢 Buy setups:** " + (', '.join(f"{i.symbol} (strength {i.strength}/10)" for i in bulls) if bulls else "None")
            ans += "\n\n**🔴 Sell setups:** " + (', '.join(f"{i.symbol} (strength {i.strength}/10)" for i in bears) if bears else "None")
        suggests = []
        if bulls: suggests.append(f"Why is {bulls[0].symbol} a buy?")
        if bears: suggests.append(f"Why is {bears[0].symbol} a sell?")
        suggests.append("Market overview")
        return ans, suggests

    return _generate_instrument_answer(symbols, r, question, is_tagalog)


def _generate_pretrade_checklist(symbols: list[str], question: str, r, is_tagalog: bool) -> tuple[str, list[str]]:
    """Generate a structured pre-trade checklist / coaching response."""
    if is_tagalog:
        ans = "🤖 **BUDDY Trade Coach — Pre-Trade Checklist**\n\n"
        ans += "Bago ka pumasok sa trade, sagutin mo muna ito:\n\n"
    else:
        ans = "🤖 **BUDDY Trade Coach — Pre-Trade Checklist**\n\n"
        ans += "Before you enter a trade, run through this checklist:\n\n"

    # ── Section 1: Market Direction (M) ──
    instr_list = []
    if symbols and r:
        instr_list = [i for i in r.instruments if i.symbol == symbols[0]]
        if instr_list:
            i = instr_list[0]
            ans += f"**📌 1. Market Direction (M)**\n"
            ans += f"{i.symbol}: bias **{i.bias}**, trend strength **{i.trend_strength}/5**\n\n"
        else:
            ans += f"**📌 1. Market Direction (M)**\nNo data for {symbols[0]}\n\n"
    else:
        ans += "**📌 1. Market Direction (M)**\nCheck the trend first — it's your friend!\n\n"

    # ── Section 2: Area of Value (A) ──
    ans += "**🎯 2. Area of Value (A)**\n"
    ans += "- Are you at a key support/resistance zone?\n"
    ans += "- Is price respecting a trendline or EMA?\n"
    ans += "- Wait for price to come to YOU, don't chase\n\n"

    # ── Section 3: Timing (T) ──
    if is_tagalog:
        ans += "**⏰ 3. Timing (T)**\n"
        ans += "- May confirmation candle ba?\n"
        ans += "- Ano sabi ng stochastic / RSI?\n"
        ans += "- Tama ba ang session para sa pair na ito?\n\n"
    else:
        ans += "**⏰ 3. Timing (T)**\n"
        ans += "- Do you have a confirmation candle?\n"
        ans += "- What does stochastic / RSI say?\n"
        ans += "- Is this the right session for this pair?\n\n"

    # ── Section 4: Exit (E) ──
    ans += "**🚪 4. Exit Strategy (E)**\n"
    ans += "- Where is your Stop Loss?\n"
    ans += "- Where is your Take Profit?\n"
    ans += "- What's your Risk:Reward? (aim for 1:2+)\n\n"

    # ── Section 5: Risk Check ──
    if is_tagalog:
        ans += "**⚠️ 5. Risk Check**\n"
        ans += "- Ilang % ng capital mo ang nakataya? (max 1-2%)\n"
        ans += "- Okay ka ba mentally?\n"
        ans += "- Ito ba ay 'revenge trade' o FOMO?\n"
    else:
        ans += "**⚠️ 5. Risk Check**\n"
        ans += "- What % of your capital is at risk? (max 1-2%)\n"
        ans += "- Are you in the right headspace?\n"
        ans += "- Is this a revenge trade or FOMO?\n\n"

    ans += "---\n✅ **Answer these before clicking Buy/Sell!**"

    suggests = []
    if symbols and instr_list:
        suggests.append(f"Why is {symbols[0]} {instr_list[0].bias}?")
    suggests.append("Show me best setups")
    suggests.append("Market overview")

    return ans, suggests


SESSION_INFO = {
    'london': {'open': '08:00', 'close': '17:00', 'tz': 'UTC', 'emoji': '🇬🇧', 'name': 'London'},
    'new_york': {'open': '13:00', 'close': '22:00', 'tz': 'UTC', 'emoji': '🇺🇸', 'name': 'New York'},
    'asia': {'open': '23:00', 'close': '08:00', 'tz': 'UTC', 'emoji': '🇯🇵', 'name': 'Tokyo/Asia'},
    'sydney': {'open': '21:00', 'close': '06:00', 'tz': 'UTC', 'emoji': '🇦🇺', 'name': 'Sydney'},
}

CANDLESTICK_PATTERNS = {
    'doji': ('**Doji** — indecision candle where open ≈ close.\n\n'
             'Signals potential reversal when it appears after a strong trend.\n'
             '*Long-legged doji* = higher volatility indecision.\n'
             '*Dragonfly doji* = possible bullish reversal (hammer-like).\n'
             '*Gravestone doji* = possible bearish reversal.'),
    'hammer': ('**Hammer** — bullish reversal candle.\n\n'
               'Small body at the top, long lower wick (2×+ the body).\n'
               'Appears at the bottom of a downtrend after selling exhaustion.\n'
               'Confirmation: next candle closes above the hammer\'s close.\n'
               'Related: **Shooting Star** is the bearish version at market tops.'),
    'engulfing': ('**Engulfing Pattern** — two-candle reversal pattern.\n\n'
                  '**Bullish Engulfing:** Red candle → bigger green candle that completely '
                  '\"engulfs\" the red candle\'s body. Strong buy signal at support.\n'
                  '**Bearish Engulfing:** Green candle → bigger red candle that engulfs it. '
                  'Strong sell signal at resistance.\n'
                  'The bigger the engulfing candle, the stronger the signal.'),
    'morning_star': ('**Morning Star** — three-candle bullish reversal.\n\n'
                     '1️⃣ Tall red bearish candle (selling momentum)\n'
                     '2️⃣ Small body candle (indecision — doji or small body)\n'
                     '3️⃣ Tall green candle closing above the midpoint of candle #1\n\n'
                     '**Evening Star** is the bearish equivalent at market tops.'),
    'pin_bar': ('**Pin Bar / Pinocchio Bar** — single-candle reversal.\n\n'
                'Long wick (tail), small body at the opposite end.\n'
                'Rejects a price level: wick = price was rejected there.\n'
                '**Bullish pin bar:** Long lower wick at support → buyers stepped in.\n'
                '**Bearish pin bar:** Long upper wick at resistance → sellers took over.'),
    'shooting_star': ('**Shooting Star** — bearish reversal candle at market tops.\n\n'
                      'Small body at the lower end, long upper wick (2×+ body).\n'
                      'Price rallied but sellers pushed it back down = rejection.\n'
                      'Needs confirmation: next candle closes below the star\'s close.'),
}

CHART_PATTERNS = {
    'head_and_shoulders': ('**Head and Shoulders** — major reversal pattern.\n\n'
                           '1️⃣ **Left shoulder:** Rally → pullback\n'
                           '2️⃣ **Head:** Higher high → pullback to neckline\n'
                           '3️⃣ **Right shoulder:** Lower high → fails to break head high\n'
                           '**Breakneck:** Price breaks below the neckline → confirmed\n'
                           '**Target:** Height from head to neckline, projected down.\n'
                           '**Inverse H&S** is the bullish equivalent at market bottoms.'),
    'double_top': ('**Double Top** — bearish reversal after an uptrend.\n\n'
                   'Price hits resistance twice at the same level, forming an "M" shape.\n'
                   'The valley between peaks is the neckline.\n'
                   'Breakdown below neckline = confirmed.\n'
                   '**Target:** Distance from peak to neckline, projected down.\n'
                   '**Double Bottom** is the bullish equivalent (looks like a "W").'),
    'triangle': ('**Triangles** — continuation patterns (usually).\n\n'
                 '**Ascending Triangle:** Higher lows + flat top resistance → bullish.\n'
                 '**Descending Triangle:** Lower highs + flat bottom support → bearish.\n'
                 '**Symmetrical Triangle:** Converging highs/lows → breakout either way.\n'
                 'Entry: Wait for a confirmed breakout with volume above the trendline.'),
    'flag': ('**Flag & Pennant** — short-term continuation patterns.\n\n'
             '**Flag:** Sharp move (flagpole) → sloping consolidation channel.\n'
             '**Pennant:** Sharp move → contracting symmetrical triangle.\n'
             'Both resolve in the direction of the prior move.\n'
             'Target: Length of the flagpole projected from the breakout.'),
}


def _generate_sessions_answer(question: str, is_tagalog: bool) -> tuple[str, list[str]]:
    """Answer trading session time questions."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    h = now.hour
    m = now.min

    # Determine which sessions are currently open
    status_lines = []
    for key, s in SESSION_INFO.items():
        open_h, open_m = int(s['open'][:2]), int(s['open'][3:])
        close_h, close_m = int(s['close'][:2]), int(s['close'][3:])

        open_min = open_h * 60 + open_m
        close_min = close_h * 60 + close_m
        now_min = h * 60 + m

        is_open = False
        if close_min > open_min:
            is_open = open_min <= now_min < close_min
        else:
            is_open = now_min >= open_min or now_min < close_min

        status = '🟢 OPEN' if is_open else '🔴 Closed'
        status_lines.append(f"  {s['emoji']} **{s['name']}:** {s['open']}–{s['close']} UTC  {status}")

    overlap_zones = {
        'asia_london': ('23:00–08:00', '08:00–17:00'),
        'london_ny': ('13:00–17:00 UTC',  '🇬🇧🇺🇸 **London + New York overlap** — highest volatility, best for forex'),
    }

    london_open = 13 * 60  # 13:00 UTC
    london_close = 17 * 60
    ny_open = 13 * 60     # 13:00 UTC
    ny_close = 22 * 60

    if london_open <= now_min < london_close and ny_open <= now_min < ny_close:
        overlap_note = '🔥 **London + New York overlap is active NOW** — highest volatility!'
    else:
        overlap_note = '🔥 London–NY overlap: 13:00–17:00 UTC (highest volatility for forex)'

    if is_tagalog:
        answer = (
            "🌍 **Trading Sessions**\n\n"
            "Mga oras ng sesyon (UTC):\n\n" +
            '\n'.join(status_lines) +
            f"\n\n{overlap_note}\n\n"
            "💡 Best session para sa iyong pair:\n"
            "  • EURUSD/GBPUSD → London + NY overlap\n"
            "  • USDJPY → Asia session (Tokyo)\n"
            "  • XAUUSD → London + NY overlap\n"
            "  • BTCUSD → Any session, but NY afternoon = highest volume"
        )
    else:
        answer = (
            "🌍 **Trading Sessions**\n\n"
            "Current session times (UTC):\n\n" +
            '\n'.join(status_lines) +
            f"\n\n{overlap_note}\n\n"
            "💡 Best trading times by instrument:\n"
            "  • **EURUSD/GBPUSD** → London + NY overlap (13:00–17:00 UTC)\n"
            "  • **USDJPY** → Asia session (Tokyo open 23:00 UTC)\n"
            "  • **XAUUSD** → London + NY overlap\n"
            "  • **BTCUSD** → Any session, NY afternoon = highest volume"
        )

    suggests = [
        "Compare EURUSD and GBPUSD",
        "Market overview",
        "Show me XAUUSD"
    ]
    return answer, suggests


def _generate_psychology_answer(question: str, is_tagalog: bool) -> tuple[str, list[str]]:
    """Answer trading psychology questions."""
    q = question.lower()

    topics = {
        'revenge': ('revenge', 'ganti'),
        'fomo': ('fomo', 'miss out'),
        'fear': ('fear', 'takot', 'scared'),
        'greed': ('greed', 'greedy', 'sobra'),
        'overconfidence': ('overconfidence', 'overconfident', 'sobrang kumpiyansa'),
        'discipline': ('discipline', 'disiplina', 'disiplinado'),
    }

    found_topic = None
    for topic, keywords in topics.items():
        if any(kw in q for kw in keywords):
            found_topic = topic
            break

    if found_topic == 'revenge':
        if is_tagalog:
            answer = (
                "😤 **Revenge Trading** — pinakamalaking kaaway ng trader!\n\n"
                "Nangyayari ito kapag natalo ka, at gusto mong mabawi agad ang lugi.\n\n"
                "⚠️ **Bakit masama:**\n"
                "• Hindi mo na sinusunod ang iyong trading plan\n"
                "• Over-sized na ang posisyon (gustong bumawi agad)\n"
                "• Mas malaki ang posibleng talo\n\n"
                "✅ **Solusyon:**\n"
                "• Magpahinga muna — lakad, tubig, o tapusin ang araw\n"
                "• Balikan ang iyong trading plan bago pumasok ulit\n"
                "• Kung natalo ng 2 sunod, TIGIL na sa araw na iyon"
            )
        else:
            answer = (
                "😤 **Revenge Trading** — the #1 account killer!\n\n"
                "It happens when you take a loss and immediately try to win it back.\n\n"
                "⚠️ **Why it's dangerous:**\n"
                "• You abandon your trading plan\n"
                "• You oversize positions to \"get it back fast\"\n"
                "• Losses compound faster than wins\n\n"
                "✅ **How to stop:**\n"
                "• Walk away — take a 15-minute break after any loss\n"
                "• Review your plan before the next trade\n"
                "• If you lose 2 in a row, STOP for the day"
            )

    elif found_topic == 'fomo':
        if is_tagalog:
            answer = (
                "😰 **FOMO (Fear Of Missing Out)**\n\n"
                "Yung feeling na \"sasabog na ito, kailangan pumasok na agad!\"\n\n"
                "⚠️ **Bakit delikado:**\n"
                "• Pumapasok ka ng walang tamang setup\n"
                "• Malapit ka na sa resistance kaya wala nang room\n"
                "• Stop loss masyadong malapit = madaling ma-stop out\n\n"
                "✅ **Solusyon:**\n"
                "• Ang market ay laging magbibigay ng panibagong pagkakataon\n"
                "• Hintayin ang pullback para sa mas magandang entry\n"
                "• Kung hindi mo naabutan, okay lang — susunod ulit"
            )
        else:
            answer = (
                "😰 **FOMO (Fear Of Missing Out)**\n\n"
                "That feeling when price is exploding and you MUST get in now!\n\n"
                "⚠️ **Why it hurts:**\n"
                "• You enter without a proper setup\n"
                "• Price is already at resistance — no room left\n"
                "• Stop loss too tight = easy stop-out\n\n"
                "✅ **How to handle:**\n"
                "• The market always gives second chances\n"
                "• Wait for a pullback to value before entering\n"
                "• Missed it? No problem — there's always the next trade"
            )

    elif found_topic == 'discipline':
        if is_tagalog:
            answer = (
                "🎯 **Trading Discipline** — ang susi sa consistent profit!\n\n"
                "Ang disiplina ay ang kakayahang sumunod sa iyong plano, "
                "kahit mahirap o nakakatakot.\n\n"
                "**Mga dapat gawin:**\n"
                "1️⃣ **Gumawa ng trading plan** — isulat ang rules mo\n"
                "2️⃣ **Sundin ang plan** — walang deviation\n"
                "3️⃣ **Journal lahat ng trade** — matuto sa pagkakamali\n"
                "4️⃣ **Risk management lagi** — 1-2% lang per trade\n"
                "5️⃣ **Review weekly** — ano ang maganda at hindi\n\n"
                "💡 \"Plan your trade, trade your plan!\""
            )
        else:
            answer = (
                "🎯 **Trading Discipline** — the key to consistency!\n\n"
                "Discipline is doing what you planned, even when it's hard.\n\n"
                "**Your discipline checklist:**\n"
                "1️⃣ **Write a trading plan** — your rules on paper\n"
                "2️⃣ **Follow the plan** — no deviations, no exceptions\n"
                "3️⃣ **Journal every trade** — learn from mistakes\n"
                "4️⃣ **Risk 1-2% max** — per trade, always\n"
                "5️⃣ **Weekly review** — what worked, what didn't\n\n"
                "💡 \"Plan your trade, trade your plan!\""
            )

    else:
        if is_tagalog:
            answer = (
                "🧠 **Trading Psychology** — 90% ng trading ay mental!\n\n"
                "**Mga common na psychological pitfalls:**\n\n"
                "😤 **Revenge Trading** — gusto mong bumawi agad sa talo\n"
                "😰 **FOMO** — takot na maiwan, pumapasok kahit wala sa plano\n"
                "🤑 **Greed** — hindi kumukuha ng profit, hinahayaan lang\n"
                "😨 **Fear** — natatakot pumasok sa magandang setup\n"
                "🤥 **Overconfidence** — sunod-sunod na panalo → nagiging careles\n\n"
                "✅ **Mindset tips:**\n"
                "• Focus sa process, hindi sa pera\n"
                "• Treat trading as a business, hindi sugal\n"
                "• Accept losses as cost of doing business\n"
                "• Keep a trading journal — it reveals your patterns"
            )
        else:
            answer = (
                "🧠 **Trading Psychology** — 90% of trading is mental!\n\n"
                "**Common psychological pitfalls:**\n\n"
                "😤 **Revenge Trading** — trying to win back losses immediately\n"
                "😰 **FOMO** — entering without a setup, afraid to miss out\n"
                "🤑 **Greed** — letting winners turn into losers\n"
                "😨 **Fear** — not pulling the trigger on a valid setup\n"
                "🤥 **Overconfidence** — getting careless after winning streaks\n\n"
                "✅ **Mindset tips:**\n"
                "• Focus on the process, not the money\n"
                "• Treat trading as a business, not gambling\n"
                "• Accept losses as the cost of doing business\n"
                "• Keep a trading journal — patterns reveal themselves"
            )

    suggests = [
        "What is revenge trading?",
        "How to avoid FOMO?",
        "Give me a trading tip",
        "Market overview"
    ]
    return answer, suggests


def _generate_position_sizing_answer(symbols: list[str], question: str, r, is_tagalog: bool) -> tuple[str, list[str]]:
    """Answer position sizing questions."""
    if is_tagalog:
        answer = (
            "📐 **Position Sizing — calculate your lot size**\n\n"
            "**Ang formula:**\n"
            "`Lot Size = (Account Balance × Risk %) ÷ (Stop Loss in pips × Pip Value)`\n\n"
            "**Halimbawa:**\n"
            "• Balance: $500\n"
            "• Risk: 2% ($10)\n"
            "• Stop Loss: 20 pips sa EURUSD\n"
            "• Pip Value: $10/standard lot\n\n"
            "Lot = ($10) ÷ (20 × $10) = 0.05 mini lot\n\n"
            "💡 Gamitin ang **Calculator tab** para sa automatic na computation!\n\n"
            "**Rule of thumb:**\n"
            "• $200–500 account → 0.01–0.05 lots max\n"
            "• $1,000–2,000 account → 0.05–0.20 lots max\n"
            "• Risk 1-2% max per trade — protektahan ang capital!"
        )
    else:
        answer = (
            "📐 **Position Sizing — how to calculate lot size**\n\n"
            "**The formula:**\n"
            "`Lot Size = (Account Balance × Risk %) ÷ (Stop Loss in pips × Pip Value)`\n\n"
            "**Example:**\n"
            "• Balance: $500\n"
            "• Risk: 2% ($10)\n"
            "• Stop Loss: 20 pips on EURUSD\n"
            "• Pip Value: $10 per standard lot\n\n"
            "Lot = $10 ÷ (20 × $10) = 0.05 lots (mini)\n\n"
            "💡 Use the **Calculator tab** for automatic computation!\n\n"
            "**Rule of thumb:**\n"
            "• $200–500 account → 0.01–0.05 lots max\n"
            "• $1,000–2,000 account → 0.05–0.20 lots max\n"
            "• Never risk more than 1-2% on any single trade"
        )

    suggests = [
        "What is a pip?",
        "How do lots work?",
        "Show me best setups",
        "Market overview"
    ]
    return answer, suggests


def _generate_candlestick_answer(question: str, is_tagalog: bool) -> tuple[str, list[str]]:
    """Answer candlestick pattern questions."""
    q = question.lower()

    # Detect which pattern they're asking about
    for key in CANDLESTICK_PATTERNS:
        if any(kw in q for kw in [key] + key.split('_')):
            answer = CANDLESTICK_PATTERNS[key]
            if is_tagalog:
                answer += "\n\n💡 Tanong ka pa tungkol sa ibang candlestick patterns!"
            else:
                answer += "\n\n💡 Ask about other candlestick patterns too!"
            suggests = [
                "What is a doji?",
                "Explain engulfing pattern",
                "What is a pin bar?",
                "Market overview"
            ]
            return answer, suggests

    # General candlestick overview
    if is_tagalog:
        answer = (
            "🕯️ **Candlestick Patterns**\n\n"
            "Ang candlestick ay nagpapakita ng **Open, High, Low, Close** sa isang time frame.\n\n"
            "**Bullish patterns (senyales ng pagtaas):**\n"
            "• **Hammer** — mahabang lower wick sa downtrend\n"
            "• **Bullish Engulfing** — malaking green candle na lumalamon sa red\n"
            "• **Morning Star** — 3-candle reversal pattern\n"
            "• **Bullish Pin Bar** — mahabang tail sa support\n\n"
            "**Bearish patterns (senyales ng pagbaba):**\n"
            "• **Shooting Star** — mahabang upper wick sa resistance\n"
            "• **Bearish Engulfing** — malaking red candle na lumalamon sa green\n"
            "• **Evening Star** — bearish version ng morning star\n"
            "• **Bearish Pin Bar** — mahabang tail sa resistance\n\n"
            "**Indecision:**\n"
            "• **Doji** — open ≈ close, ibig sabihin nag-aalinlangan ang market\n\n"
            "💡 Magtanong tungkol sa specific pattern: Hammer, Doji, Engulfing, atbp!"
        )
    else:
        answer = (
            "🕯️ **Candlestick Patterns**\n\n"
            "Each candlestick shows **Open, High, Low, Close** for a time period.\n\n"
            "**Bullish reversal patterns (price may go up):**\n"
            "• **Hammer** — long lower wick at the bottom of a downtrend\n"
            "• **Bullish Engulfing** — big green candle eats the prior red one\n"
            "• **Morning Star** — 3-candle reversal (red → doji → big green)\n"
            "• **Bullish Pin Bar** — long tail at support = buyers stepped in\n\n"
            "**Bearish reversal patterns (price may go down):**\n"
            "• **Shooting Star** — long upper wick at resistance\n"
            "• **Bearish Engulfing** — big red candle eats the prior green one\n"
            "• **Evening Star** — bearish version of the morning star\n"
            "• **Bearish Pin Bar** — long tail at resistance = sellers took over\n\n"
            "**Indecision:**\n"
            "• **Doji** — open equals close, market can't decide direction\n\n"
            "💡 Ask about a specific pattern: Hammer, Doji, Engulfing, Pin Bar!"
        )

    suggests = [
        "What is a hammer?",
        "Explain doji",
        "What is engulfing?",
        "Show me XAUUSD"
    ]
    return answer, suggests


def _generate_chart_pattern_answer(question: str, is_tagalog: bool) -> tuple[str, list[str]]:
    """Answer chart pattern questions."""
    q = question.lower()

    for key, explanation in CHART_PATTERNS.items():
        keywords = [key.replace('_', ' '), key.replace('_', ''), 'hns', 'h&s'] if key == 'head_and_shoulders' else [key.replace('_', ' ')]
        if any(kw in q for kw in keywords):
            answer = explanation
            if is_tagalog:
                answer += "\n\n💡 Tanong ka pa tungkol sa ibang chart patterns!"
            else:
                answer += "\n\n💡 Ask about other chart patterns too!"
            suggests = [
                "What is a double top?",
                "Explain head and shoulders",
                "Market overview",
                "Show me best setups"
            ]
            return answer, suggests

    if is_tagalog:
        answer = (
            "📈 **Chart Patterns**\n\n"
            "**Reversal Patterns (pagbabago ng direksyon):**\n"
            "• **Head and Shoulders** — bearish reversal pagkatapos ng uptrend\n"
            "• **Double Top / Double Bottom** — M (bearish) o W (bullish)\n"
            "• **Rounding Bottom** — mabagal na bullish reversal\n\n"
            "**Continuation Patterns (pagpapatuloy ng trend):**\n"
            "• **Triangles** — ascending (bullish), descending (bearish), symmetrical\n"
            "• **Flags & Pennants** — maikling pahinga bago magpatuloy ang trend\n"
            "• **Wedges** — rising wedge (bearish), falling wedge (bullish)\n\n"
            "💡 Magtanong tungkol sa specific pattern!"
        )
    else:
        answer = (
            "📈 **Chart Patterns**\n\n"
            "**Reversal Patterns (trend changes direction):**\n"
            "• **Head and Shoulders** — bearish reversal after uptrend\n"
            "• **Double Top / Double Bottom** — M-shaped (bearish) or W-shaped (bullish)\n"
            "• **Rounding Bottom** — slow bullish reversal (cup and handle)\n\n"
            "**Continuation Patterns (trend continues after pause):**\n"
            "• **Triangles** — ascending (bullish), descending (bearish), symmetrical\n"
            "• **Flags & Pennants** — brief consolidation before trend resumes\n"
            "• **Wedges** — rising wedge (bearish), falling wedge (bullish)\n\n"
            "💡 Ask about a specific pattern!"
        )

    suggests = [
        "What is head and shoulders?",
        "Explain double top",
        "Show me XAUUSD"
    ]
    return answer, suggests


def _generate_fibonacci_answer(is_tagalog: bool) -> tuple[str, list[str]]:
    """Answer Fibonacci questions."""
    if is_tagalog:
        answer = (
            "🔢 **Fibonacci — retracement at extension levels**\n\n"
            "Ang Fibonacci ay ginagamit para sa **support/resistance zones** "
            "at **price targets**.\n\n"
            "**Key Retracement Levels (pullback):**\n"
            "• 23.6% — mababaw na pullback\n"
            "• **38.2%** — common pullback level\n"
            "• **50.0%** — midpoint (psychologically important)\n"
            "• **61.8%** — \"golden ratio\" — pinakamahalagang level\n"
            "• 78.6% — deep pullback\n\n"
            "**Extension Levels (target):**\n"
            "• 127.2% — unang target\n"
            "• 161.8% — pangunahing target\n"
            "• 261.8% — extreme target\n\n"
            "💡 **Paano gamitin:**\n"
            "1. I-draw mula sa swing low hanggang swing high\n"
            "2. Ang 38.2%, 50%, at 61.8% ay potential entry zones\n"
            "3. Ang 161.8% ay potential take profit level"
        )
    else:
        answer = (
            "🔢 **Fibonacci — retracement & extension levels**\n\n"
            "Fibonacci is used to identify **potential support/resistance zones** "
            "and **price targets**.\n\n"
            "**Key Retracement Levels (pullback entries):**\n"
            "• **23.6%** — shallow pullback\n"
            "• **38.2%** — common pullback level\n"
            "• **50.0%** — psychologically important midpoint\n"
            "• **61.8%** — the golden ratio (most important level)\n"
            "• **78.6%** — deep pullback\n\n"
            "**Extension Levels (profit targets):**\n"
            "• **127.2%** — first target\n"
            "• **161.8%** — primary target (most common)\n"
            "• **261.8%** — extended target (strong trends)\n\n"
            "💡 **How to use:**\n"
            "1. Draw from swing low to swing high (downtrend: high to low)\n"
            "2. 38.2%, 50%, 61.8% are potential entry zones\n"
            "3. 161.8% is a common take-profit level"
        )

    suggests = [
        "What are Fibonacci levels?",
        "Show me XAUUSD",
        "What is support and resistance?",
        "Market overview"
    ]
    return answer, suggests


def _generate_diversification_answer(r, is_tagalog: bool) -> tuple[str, list[str]]:
    """Answer portfolio/diversification questions using radar data."""
    if not r:
        if is_tagalog:
            answer = "Wala akong radar data para suriin ang diversification."
        else:
            answer = "I don't have radar data to analyze diversification."
        return answer, ["Market overview", "Show me best setups"]

    categories = {}
    for i in r.instruments:
        cat = i.category
        if cat not in categories:
            categories[cat] = {'count': 0, 'bullish': 0, 'bearish': 0}
        categories[cat]['count'] += 1
        if i.bias in ('Strong Buy', 'Buy'):
            categories[cat]['bullish'] += 1
        elif i.bias in ('Strong Sell', 'Sell'):
            categories[cat]['bearish'] += 1

    cat_lines = []
    for cat, stats in categories.items():
        label = CATEGORY_LABELS.get(cat, cat.upper())
        cat_lines.append(f"  • **{label}:** {stats['count']} instruments "
                        f"(🟢 {stats['bullish']} bullish / 🔴 {stats['bearish']} bearish)")

    if is_tagalog:
        answer = (
            "📊 **Diversification Analysis**\n\n"
            "Ang iyong radar ay sumusubaybay sa mga sumusunod na kategorya:\n\n" +
            '\n'.join(cat_lines) +
            "\n\n**Diversification Tips:**\n"
            "• Huwag ilagay lahat ng capital sa isang pares lang\n"
            "• Pagsamahin ang iba't ibang kategorya: forex + commodities + indices\n"
            "• Iwasan ang correlated pairs (EURUSD & GBPUSD ay madalas sabay gumalaw)\n"
            "• Kapag bearish sa isang asset, maghanap ng bullish sa ibang category\n"
            "• Laging may cash reserve — hindi lahat ng capital dapat nasa trades"
        )
    else:
        answer = (
            "📊 **Diversification Analysis**\n\n"
            "Your radar is tracking these categories:\n\n" +
            '\n'.join(cat_lines) +
            "\n\n**Diversification Tips:**\n"
            "• Don't put all capital in one pair\n"
            "• Mix categories: forex + commodities + indices\n"
            "• Avoid correlated pairs (EURUSD & GBPUSD often move together)\n"
            "• When bearish in one asset, look for bullish elsewhere\n"
            "• Always keep cash reserve — don't be fully invested"
        )

    suggests = [
        "Show me the market overview",
        "How are the majors?",
        "What's the best trade now?"
    ]
    return answer, suggests


def _generate_account_answer(symbols: list[str], r, is_tagalog: bool) -> tuple[str, list[str]]:
    """Answer account/MT5 related questions using live terminal data."""
    try:
        from .mt5_terminal import get_account_info, get_positions
    except ImportError:
        get_account_info = lambda: None
        get_positions = lambda: []

    acc = get_account_info() if 'get_account_info' in dir() else None
    if not acc:
        if is_tagalog:
            answer = (
                "🔌 **Hindi naka-connect ang MT5 Terminal**\n\n"
                "Para magamit ang account info:\n"
                "1. Pumunta sa **Terminal tab**\n"
                "2. I-connect ang iyong broker (Vantage, Exness, etc.)\n"
                "3. Balikan mo ako at magtanong ulit!\n\n"
                "Pwede kitang tanungin tungkol sa market analysis kahit hindi naka-connect ang MT5."
            )
        else:
            answer = (
                "🔌 **MT5 Terminal is not connected**\n\n"
                "To check your account info:\n"
                "1. Go to the **Terminal tab**\n"
                "2. Connect your broker (Vantage, Exness, etc.)\n"
                "3. Ask me again!\n\n"
                "I can still answer market analysis questions even without MT5 connected."
            )
        suggests = ["Market overview", "Show me XAUUSD", "What are the best setups?"]
        return answer, suggests

    balance = acc.get('balance', 0)
    equity = acc.get('equity', 0)
    margin = acc.get('margin', 0)
    profit = acc.get('profit', 0)
    margin_level = acc.get('margin_level', 0)
    leverage = acc.get('leverage', 0)
    currency = acc.get('currency', 'USD')
    server = acc.get('server', '')
    login = acc.get('login', '')

    positions = get_positions()
    pos_count = len(positions)

    if pos_count > 0:
        total_position_pnl = sum(p.get('profit', 0) for p in positions)
        pos_lines = []
        for p in positions[:5]:
            pos_lines.append(f"  • {p['symbol']} {p['type']} {p['volume']} lot "
                            f"@ {p['entry_price']}  P&L: **{p.get('profit', 0):+.2f}**")
        pos_section = '\n'.join(pos_lines)
        if pos_count > 5:
            pos_section += f'\n  ... and {pos_count - 5} more positions'
    else:
        pos_section = '  No open positions'
        total_position_pnl = 0

    if is_tagalog:
        answer = (
            "🏦 **MT5 Account Summary**\n\n"
            f"**Account:** {login} @ {server}\n"
            f"**Currency:** {currency}  |  **Leverage:** 1:{leverage}\n\n"
            f"💰 **Balance:** ${balance:.2f}\n"
            f"📊 **Equity:** ${equity:.2f}\n"
            f"📉 **Margin:** ${margin:.2f}\n"
            f"📈 **Margin Level:** {margin_level:.2f}%\n"
            f"💵 **Floating P&L:** {profit:+.2f}\n\n"
            f"**Open Positions ({pos_count}):**\n{pos_section}"
        )
    else:
        answer = (
            "🏦 **MT5 Account Summary**\n\n"
            f"**Account:** {login} @ {server}\n"
            f"**Currency:** {currency}  |  **Leverage:** 1:{leverage}\n\n"
            f"💰 **Balance:** ${balance:.2f}\n"
            f"📊 **Equity:** ${equity:.2f}\n"
            f"📉 **Margin:** ${margin:.2f}\n"
            f"📈 **Margin Level:** {margin_level:.2f}%\n"
            f"💵 **Floating P&L:** {profit:+.2f}\n\n"
            f"**Open Positions ({pos_count}):**\n{pos_section}"
        )

    suggests = [
        "What's my balance?",
        "Show me open positions",
        "Market overview",
        "What's XAUUSD doing?"
    ]
    return answer, suggests


def _generate_journal_answer(question: str, is_tagalog: bool) -> tuple[str, list[str]]:
    """Answer questions about trading journal / performance."""
    try:
        import os, json
        journal_path = os.path.join(os.path.dirname(__file__), 'journal_entries.json')
        if os.path.exists(journal_path):
            with open(journal_path) as f:
                entries = json.load(f)
        else:
            entries = []
    except Exception:
        entries = []

    if not entries:
        if is_tagalog:
            answer = (
                "📓 **Wala pang journal entries.**\n\n"
                "Itala ang iyong trades sa **Journal tab** para makita ko ang iyong performance!\n\n"
                "Ang journal ay makakatulong sa iyo na:\n"
                "• Makita ang pattern sa iyong trading\n"
                "• Alamin kung aling setup ang pinakamabisa\n"
                "• I-track ang win rate at profit factor"
            )
        else:
            answer = (
                "📓 **No journal entries yet.**\n\n"
                "Log your trades in the **Journal tab** so I can analyze your performance!\n\n"
                "Journaling helps you:\n"
                "• See patterns in your trading\n"
                "• Know which setups work best for you\n"
                "• Track your win rate and profit factor"
            )
        suggests = ["Market overview", "Show me XAUUSD", "What are the best setups?"]
        return answer, suggests

    closed = [e for e in entries if e.get('status', '').lower() in ('closed', 'win', 'loss')]
    total = len(closed)
    wins = [e for e in closed if e.get('pnl', 0) > 0 or e.get('status', '').lower() == 'win']
    losses = [e for e in closed if e.get('pnl', 0) <= 0 and e.get('status', '').lower() != 'win']
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / total * 100) if total > 0 else 0

    total_pnl = sum(e.get('pnl', 0) for e in closed if isinstance(e.get('pnl'), (int, float)))
    avg_win = sum(e.get('pnl', 0) for e in wins) / win_count if win_count > 0 else 0
    avg_loss = sum(e.get('pnl', 0) for e in losses) / loss_count if loss_count > 0 else 0
    profit_factor = abs(sum(e.get('pnl', 0) for e in wins) / sum(e.get('pnl', 0) for e in losses)) if loss_count > 0 and sum(e.get('pnl', 0) for e in losses) != 0 else float('inf')

    if is_tagalog:
        answer = (
            "📓 **Trading Journal Summary**\n\n"
            f"**Total Closed Trades:** {total}\n"
            f"**Wins:** {win_count}  |  **Losses:** {loss_count}\n"
            f"**Win Rate:** {win_rate:.1f}%\n"
            f"**Total P&L:** {total_pnl:+.2f}\n"
            f"**Average Win:** {avg_win:+.2f}  |  **Average Loss:** {avg_loss:+.2f}\n"
            f"**Profit Factor:** {'∞' if profit_factor == float('inf') else f'{profit_factor:.2f}'}\n\n"
            "💡 Mag-log ng trades para sa mas magandang analysis!"
        )
    else:
        answer = (
            "📓 **Trading Journal Summary**\n\n"
            f"**Total Closed Trades:** {total}\n"
            f"**Wins:** {win_count}  |  **Losses:** {loss_count}\n"
            f"**Win Rate:** {win_rate:.1f}%\n"
            f"**Total P&L:** {total_pnl:+.2f}\n"
            f"**Average Win:** {avg_win:+.2f}  |  **Average Loss:** {avg_loss:+.2f}\n"
            f"**Profit Factor:** {'∞' if profit_factor == float('inf') else f'{profit_factor:.2f}'}\n\n"
            "💡 Log more trades for better analysis!"
        )

    suggests = [
        "What's my win rate?",
        "Show me best setups",
        "Market overview"
    ]
    return answer, suggests


def _generate_greeting(is_tagalog: bool) -> tuple[str, list[str]]:
    if is_tagalog:
        return (
            "🤖 **Let me explain, BUDDY!** — Maligayang pagdating!\n\n"
            "Ako ang iyong trading assistant. Maaari kang magtanong tulad ng:\n"
            "• \"Ano ang market overview?\"\n"
            "• \"Bakit bearish ang XAUUSD?\"\n"
            "• \"Paano ang mga major ngayon?\"\n"
            "• \"I-compare ang EURUSD at GBPUSD\"\n"
            "• \"Ano ang balita ngayon?\""
        ), ["Market overview", "Show me XAUUSD", "What's the best trade now?"]
    return (
        "🤖 **Let me explain, BUDDY!** — at your service!\n\n"
        "I'm your trading assistant. Try asking:\n"
        "• \"What's the market overview?\"\n"
        "• \"Why is XAUUSD bearish?\"\n"
        "• \"How are the majors looking?\"\n"
        "• \"Compare EURUSD and GBPUSD\"\n"
        "• \"What's in the news today?\"\n"
        "• \"What's the best trade right now?\"\n"
        "• \"What's my MT5 balance?\"\n"
        "• \"Trading psychology tips\"\n"
        "• \"Explain candlestick patterns\"\n"
        "• \"What are Fibonacci levels?\"\n"
        "• \"When do trading sessions open?\"\n"
        "• \"How to calculate position size?\""
    ), ["Market overview", "Why is XAUUSD bearish?", "Compare EURUSD and GBPUSD", "What's my balance?"]


def _generate_education_answer(question: str, r, is_tagalog: bool) -> tuple[str, list[str]]:
    """Answer educational questions about trading concepts using radar data as context."""
    q = question.lower()

    # Detect what the user is asking about
    concepts = {
        'pip': ['pip', 'pips', 'point'],
        'lot': ['lot', 'lots', 'lot size'],
        'spread': ['spread', 'bid-ask'],
        'margin': ['margin', 'leverage'],
        'swap': ['swap', 'rollover', 'overnight fee'],
        'rsi': ['rsi', 'relative strength'],
        'adx': ['adx', 'average directional'],
        'macd': ['macd', 'moving average'],
        'support': ['support', 'resistance', 's&r', 'key level'],
        'trend': ['trend', 'trending', 'downtrend', 'uptrend'],
        'stop': ['stop loss', 'stoploss', 'sl'],
        'take': ['take profit', 'takeprofit', 'tp'],
        'risk management': ['risk management', 'risk reward', 'rr', 'position sizing'],
        'mate': ['mate', 'market direction', 'area of value', 'area of alignment',
                 'timing', 'exit strategy', 'the 30 minute trader', '30min trader'],
        'support_resistance': ['support and resistance', 's and r', 'key level', 's&r', 'zones'],
        'position_sizing': ['position sizing', 'lot size calc'],
        'psychology': ['trading psychology', 'mindset', 'discipline'],
    }

    found_concept = None
    for concept, keywords in concepts.items():
        for kw in keywords:
            if kw in q:
                found_concept = concept
                break
        if found_concept:
            break

    if not found_concept:
        if is_tagalog:
            return ("Puwede kitang turuan tungkol sa trading concepts. Itanong mo lang: "
                    "'Ano ang pip?', 'Paano ang lot?', 'Ano ang RSI?'"), \
                   ["What is a pip?", "What is RSI?", "How do pips work?", "Market overview"]
        return ("I can explain trading concepts! Try asking: "
                "'What's a pip?', 'How do lots work?', 'What is RSI?', "
                "'How does margin work?'"), \
               ["What is a pip?", "What is RSI?", "How do pips work?", "Market overview"]

    explanations = {
        'pip': (
            "**What is a Pip?**\n\n"
            "A **pip** (percentage in point) is the smallest price move in forex.\n\n"
            f"For most pairs (EURUSD, GBPUSD): **1 pip = 0.0001**\n"
            f"For JPY pairs (USDJPY, EURJPY): **1 pip = 0.01**\n"
            f"For gold (XAUUSD): **1 pip = 0.10** ($0.10 per ounce)\n\n"
            f"**Pip value example:**\n"
            f"EURUSD at 1.14330, 1 standard lot (100k units):\n"
            f"1 pip move = **$10.00** profit/loss\n\n"
            f"💡 Your position calculator shows all pip values automatically!"
        ),
        'lot': (
            "**What is a Lot?**\n\n"
            "A **lot** is a standardized trading size:\n\n"
            "| Type | Units | Pip Value (EURUSD) |\n"
            "|------|-------|--------------------|\n"
            "| **Standard** | 100,000 | $10.00/pip |\n"
            "| **Mini** | 10,000 | $1.00/pip |\n"
            "| **Micro** | 1,000 | $0.10/pip |\n\n"
            "In this calculator, **1 lot = 1 standard lot (100k units)**. "
            "Enter 0.01 for micro, 0.10 for mini, 1.0 for standard."
        ),
        'margin': (
            "**What is Margin & Leverage?**\n\n"
            "**Margin** is the deposit required to open a position.\n\n"
            "**Example:**\n"
            "• Account: $1,000\n"
            "• Leverage: 1:100\n"
            "• Margin for 1 mini lot (10k EURUSD) = ~$100\n\n"
            "**⚠️ Warning:**\n"
            "While leverage amplifies gains, it also amplifies losses.\n"
            "With 1:100 leverage, a 1% move in price = 100% change in your margin.\n"
            "Always use proper risk management!\n\n"
            "💡 The **Terminal tab** shows your live margin levels when connected."
        ),
        'mate': (
            "**📚 The MATE Framework — The 30 Minute Trader**\n\n"
            "MATE is the core trading strategy framework:\n\n"
            "**📌 M — Market Direction**\n"
            "* Know the trend: Uptrend, Downtrend, or Sideways\n"
            "* Series of Higher Highs/Higher Lows = Uptrend\n"
            "* Series of Lower Highs/Lower Lows = Downtrend\n"
            "* Use EMA 200 as your primary trend filter\n"
            "* \"Pag UP TREND, you look for BUYING opportunities\"\n\n"
            "**🎯 A — Area of Value**\n"
            "* Support & Resistance are ZONES, not lines\n"
            "* Buy when price falls to support zone\n"
            "* Sell when price rises to resistance zone\n"
            "* Higher timeframe = more significant S/R\n"
            "* Don't trade between S/R (50/50 chance)\n\n"
            "**⏰ T — Timing**\n"
            "* Wait for confirmation candles before entering\n"
            "* Use candlestick patterns + stochastic/RSI\n"
            "* Make sure at least 60% probability before entry\n\n"
            "**🚪 E — Exit Strategy**\n"
            "* Know your Take Profit and Stop Loss BEFORE entering\n"
            "* Trail your stop as the trade moves in your favor\n"
            "* Risk only 1-2% of capital per trade\n\n"
            "💡 *MATE keeps you disciplined: Trend → Zone → Confirm → Exit*"
        ),
        'rsi': (
            "**What is RSI?**\n\n"
            "The **Relative Strength Index (RSI)** measures momentum on a 0-100 scale:\n\n"
            "• **Overbought** (RSI > 70): Price may reverse down\n"
            "• **Oversold** (RSI < 30): Price may bounce up\n"
            "• **Neutral** (RSI 30-70): Normal range\n\n"
            "💡 The Radar uses RSI as part of its technical score."
        ),
        'adx': (
            "**What is ADX?**\n\n"
            "The **Average Directional Index (ADX)** measures trend strength:\n\n"
            "• ADX > 25: Strong trend (bullish or bearish)\n"
            "• ADX < 25: Weak/range-bound market\n\n"
            "It also includes **DI+** and **DI-** to show direction:\n"
            "• DI+ > DI- = Uptrend\n"
            "• DI- > DI+ = Downtrend\n\n"
            "💡 The Radar uses ADX for trend strength scoring."
        ),
        'support_resistance': (
            "**Support & Resistance — the foundation of technical analysis**\n\n"
            "**Support** is a price level where buying pressure is strong enough to "
            "prevent the price from falling further.\n\n"
            "**Resistance** is a level where selling pressure prevents price from rising.\n\n"
            "**Key rules:**\n"
            "• Old resistance becomes new support after a breakout\n"
            "• Old support becomes new resistance after a breakdown\n"
            "• The more times price touches a level, the stronger it is\n"
            "• Higher timeframes = more significant levels\n\n"
            "💡 The Radar shows key support/resistance for every instrument!\n"
            "💡 The MATE framework says: **buy at support, sell at resistance**"
        ),
        'psychology': (
            "**Trading Psychology — the mental side of trading**\n\n"
            "Even with a great strategy, your emotions can wreck your account:\n\n"
            "**Common psychological traps:**\n"
            "• **Revenge trading:** Trying to win back losses immediately\n"
            "• **FOMO:** Fear of missing out leads to chasing price\n"
            "• **Greed:** Not taking profit when it's there\n"
            "• **Overconfidence:** Getting careless after wins\n"
            "• **Analysis paralysis:** Over-analyzing and missing moves\n\n"
            "**Mindset rules:**\n"
            "1. Focus on the process, not the money\n"
            "2. Accept losses as business expenses\n"
            "3. Stick to your plan — always\n"
            "4. Journal to learn from your mistakes\n"
            "5. Take breaks — the market isn't going anywhere"
        ),
        'position_sizing': (
            "**Position Sizing — how much to risk per trade**\n\n"
            "Position sizing is the most important risk management tool:\n\n"
            "**The 1-2% Rule:**\n"
            "Never risk more than 1-2% of your account on any single trade.\n\n"
            "**Formula:**\n"
            "`Position Size = (Account × Risk %) ÷ (Stop Loss × Pip Value)`\n\n"
            "**Example:**\n"
            "• $1,000 account, 2% risk = $20 max loss\n"
            "• 20 pip stop on EURUSD (pip value = $10/standard lot)\n"
            "• Lot size = $20 ÷ (20 × $10) = 0.10 lots\n\n"
            "💡 Use the **Calculator tab** to automatically compute your lot size!"
        ),
    }

    answer = explanations.get(found_concept, "I can explain that concept!")
    suggests = ["What is a pip?", "How do lots work?", "What is RSI?", "Market overview"]
    return answer, suggests


def _generate_general_answer(question: str, symbols: list[str], r, is_tagalog: bool) -> tuple[str, list[str]]:
    """Fallback when no specific intent matches."""
    if symbols:
        return _generate_instrument_answer(symbols, r, question, is_tagalog)

    if is_tagalog:
        return ("Hindi ko maintindihan ang tanong mo. Subukan ang:\n\n"
                "• \"Ano ang market overview?\"\n"
                "• \"Bakit bearish ang XAUUSD?\"\n"
                "• \"I-compare ang EURUSD at GBPUSD\"\n"
                "• \"Ano ang trading concepts?\""), \
               ["Market overview", "Show me XAUUSD", "Compare EURUSD and GBPUSD"]
    return ("I'm not sure what you're asking. Try:\n\n"
            "• \"What's the market overview?\"\n"
            "• \"Why is XAUUSD bearish?\"\n"
            "• \"Compare EURUSD and GBPUSD\"\n"
            "• \"What trading concepts can you teach me?\""), \
           ["Market overview", "Why is XAUUSD bearish?", "Compare EURUSD and GBPUSD"]


@app.route('/api/chat', methods=['POST'])
@login_required
def api_chat():
    """Intelligent trading assistant — rules-based, fast, no API key needed."""
    data = request.json
    question = data.get('question', '').strip()
    history_ctx = data.get('context', '')  # last discussed symbol for follow-ups
    session_id = request.remote_addr or 'default'

    if not question:
        return jsonify({'answer': 'Please ask a question about trading!', 'suggestions': [], 'context': ''})

    is_tagalog = _detect_tagalog(question)
    q_normalized = _translate_tagalog(question) if is_tagalog else question.lower()

    # Detect intent
    intent = _detect_intent(q_normalized)

    # Extract symbols from question, or fall back to conversation context
    symbols = _extract_symbols(question)
    if not symbols and history_ctx:
        # Check if follow-up references the context symbol
        followup_keywords = ['it', 'this', 'that', 'what about', 'siya', 'ito', 'iyan']
        if any(kw in q_normalized for kw in followup_keywords):
            symbols = [history_ctx] if history_ctx in INSTRUMENTS else []

    # Extract categories
    cats = _extract_categories(q_normalized)

    # Get fresh radar data (cached)
    if symbols or cats or intent != 'greeting':
        r = _get_cached_radar()
    else:
        r = None

    # Route by intent
    if intent == 'greeting':
        answer, suggests = _generate_greeting(is_tagalog)
        context = ''
    elif intent == 'market_overview':
        answer, suggests = _generate_market_overview(r, is_tagalog)
        context = ''
    elif intent == 'news_events':
        answer, suggests = _generate_news_answer(r, is_tagalog)
        context = ''
    elif intent == 'why':
        answer, suggests = _generate_why_answer(symbols, question, r, is_tagalog)
        context = symbols[0] if symbols else ''
    elif intent == 'compare':
        # If only one symbol found, add context symbol
        if len(symbols) < 2 and history_ctx and history_ctx not in symbols:
            symbols.append(history_ctx)
        # If still only one, add a contrasting one
        if len(symbols) < 2:
            for s in ['EURUSD', 'XAUUSD', 'GBPUSD']:
                if s not in symbols:
                    symbols.append(s)
                    break
        answer, suggests = _generate_comparison_answer(symbols, r, is_tagalog)
        context = symbols[0] if symbols else ''
    elif intent == 'pretrade':
        answer, suggests = _generate_pretrade_checklist(symbols, question, r, is_tagalog)
        context = symbols[0] if symbols else ''
    elif intent == 'sessions':
        answer, suggests = _generate_sessions_answer(question, is_tagalog)
        context = ''
    elif intent == 'psychology':
        answer, suggests = _generate_psychology_answer(question, is_tagalog)
        context = ''
    elif intent == 'position_sizing':
        answer, suggests = _generate_position_sizing_answer(symbols, question, r, is_tagalog)
        context = symbols[0] if symbols else ''
    elif intent == 'candlestick':
        answer, suggests = _generate_candlestick_answer(question, is_tagalog)
        context = ''
    elif intent == 'chart_pattern':
        answer, suggests = _generate_chart_pattern_answer(question, is_tagalog)
        context = ''
    elif intent == 'fibonacci':
        answer, suggests = _generate_fibonacci_answer(is_tagalog)
        context = ''
    elif intent == 'diversification':
        answer, suggests = _generate_diversification_answer(r, is_tagalog)
        context = ''
    elif intent == 'account':
        answer, suggests = _generate_account_answer(symbols, r, is_tagalog)
        context = symbols[0] if symbols else ''
    elif intent == 'journal_lookup':
        answer, suggests = _generate_journal_answer(question, is_tagalog)
        context = ''
    elif intent == 'strategy' or intent == 'best' or intent == 'worst':
        answer, suggests = _generate_strategy_answer(symbols, question, r, is_tagalog)
        context = symbols[0] if symbols else ''
    elif intent == 'education':
        answer, suggests = _generate_education_answer(question, r, is_tagalog)
        context = ''
    elif cats:
        answer, suggests = _generate_category_answer(cats, r, is_tagalog)
        context = ''
    elif symbols:
        answer, suggests = _generate_instrument_answer(symbols, r, question, is_tagalog)
        context = symbols[0] if symbols else ''
    elif intent == 'technical' or intent == 'fundamental' or intent == 'risk':
        # Refers to a specific aspect — show market overview with note
        overview, suggests = _generate_market_overview(r, is_tagalog)
        if intent == 'technical':
            aspect = "technical analysis" if not is_tagalog else "teknikal"
        elif intent == 'fundamental':
            aspect = "fundamental analysis" if not is_tagalog else "pundamental"
        else:
            aspect = "risk sentiment" if not is_tagalog else "risk sentiment"
        answer = f"🔍 You asked about **{aspect}**. Here's the market view:\n\n{overview}"
        context = ''
    else:
        answer, suggests = _generate_general_answer(question, symbols, r, is_tagalog)
        context = symbols[0] if symbols else ''

    return jsonify({'answer': answer, 'suggestions': suggests[:4], 'context': context})


@app.route('/api/instruments')
@login_required
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

@app.route('/api/calculator/price/<symbol>')
@login_required
def api_calculator_price(symbol):
    """Get latest price for a symbol from radar data.
    Prefers live spot price for precious metals (Kitco), then Yahoo bars."""
    symbol = symbol.upper()
    from .data_fetcher import fetch_live_prices
    live = fetch_live_prices([symbol])
    if live and symbol in live and live[symbol] > 0:
        p = round(live[symbol], 5)
        return jsonify({'symbol': symbol, 'bid': p, 'ask': p, 'spread': 0, 'price': p})
    from .data_fetcher import fetch_bars
    df = fetch_bars(symbol, bars=3, timeframe="M5")
    if df is not None and len(df) > 0:
        latest = df.iloc[-1]
        return jsonify({
            'symbol': symbol,
            'bid': round(latest['close'], 5),
            'ask': round(latest['close'], 5),
            'spread': 0,
            'high': round(latest['high'], 5),
            'low': round(latest['low'], 5),
            'price': round(latest['close'], 5),
        })
    # Fallback: try to get from radar scan cache
    spec = INSTRUMENTS.get(symbol, {})
    base_price = {
        'EURUSD': 1.08500, 'GBPUSD': 1.28500, 'USDJPY': 150.500, 'USDCHF': 0.88500,
        'USDCAD': 1.36500, 'AUDUSD': 0.67500, 'NZDUSD': 0.61500,
        'GBPJPY': 192.500, 'EURJPY': 162.500, 'XAUUSD': 2400.00,
        'US30': 39000, 'SP500': 5400, 'NAS100': 19500,
        'BTCUSD': 58000, 'ETHUSD': 3100,
    }.get(symbol, 100.0)
    return jsonify({
        'symbol': symbol,
        'bid': base_price,
        'ask': base_price,
        'spread': 0,
        'price': base_price,
    })


@app.route('/api/price/live', methods=['POST'])
@login_required
def api_live_price():
    """Get live current prices for a list of symbols."""
    data = request.json
    symbols = data.get('symbols', [])
    from .data_fetcher import fetch_live_prices
    prices = fetch_live_prices(symbols if symbols else None)
    return jsonify({'prices': prices})


@app.route('/api/calculator', methods=['POST'])
@login_required
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


def _calc_pnl(symbol: str, direction: str, entry: float, exit: float, lot_size: float) -> float:
    """Auto-calculate P&L from trade params using instrument specs."""
    from .instruments import INSTRUMENTS, pip_value_usd
    if not entry or not exit or not lot_size:
        return 0.0
    spec = INSTRUMENTS.get(symbol.upper())
    if not spec:
        return round((exit - entry) * lot_size * 1000, 2)
    pip = spec.get('pip_factor', 0.0001)
    contract = spec.get('contract_size', 1000)
    price = (entry + exit) / 2
    pv = pip_value_usd(symbol.upper(), price)
    pips = (exit - entry) / pip
    if direction.lower() == 'sell':
        pips = -pips
    return round(pips * pv * lot_size, 2)


ENTRY_REASONS = [
    ('', 'Select reason...'),
    ('trend', 'Trend following'),
    ('breakout', 'Breakout'),
    ('pullback', 'Pullback / Retracement'),
    ('support', 'Support / Resistance'),
    ('pattern', 'Chart pattern'),
    ('indicator', 'Indicator signal'),
    ('news', 'News / Fundamental'),
    ('sentiment', 'Market sentiment'),
]

EXIT_REASONS = [
    ('', 'Select reason...'),
    ('tp', 'Take Profit hit'),
    ('sl', 'Stop Loss hit'),
    ('manual_profit', 'Manual (profit target)'),
    ('manual_loss', 'Manual (cut loss)'),
    ('reversal', 'Reversal signal'),
    ('time', 'Time-based exit'),
    ('news', 'News event'),
]


@app.route('/api/journal', methods=['GET', 'POST'])
@login_required
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

    if data.get('action') == 'update':
        """Update fields of an existing open trade (live editing)."""
        idx = int(data.get('index', -1))
        if idx < 0 or idx >= len(entries):
            return jsonify({'ok': False, 'error': 'Invalid index'}), 400

        entry = entries[idx]
        # Allowed updatable fields
        for field in ['entry_price', 'exit_price', 'stop_loss', 'take_profit',
                       'lot_size', 'notes', 'symbol', 'direction', 'result',
                       'entry_reason', 'exit_reason', 'date', 'pnl',
                       'trade_title', 'trade_status', 'position', 'risk_amount',
                       'mood', 'reflection_tags', 'chart_screenshot', 'chart_link',
                       'date_opened', 'date_closed']:
            if field in data and data[field] is not None and data[field] != '':
                entry[field] = data[field]

        # Auto-convert numeric fields
        for num_field in ['entry_price', 'exit_price', 'stop_loss', 'take_profit',
                          'lot_size', 'pnl']:
            if num_field in entry and isinstance(entry[num_field], str):
                try:
                    entry[num_field] = float(entry[num_field])
                except (ValueError, TypeError):
                    entry[num_field] = 0

        # Recalculate P&L if trade was closed
        if entry.get('result') in ('win', 'loss') and entry.get('entry_price') and entry.get('exit_price'):
            entry['pnl'] = _calc_pnl(
                entry.get('symbol', ''),
                entry.get('direction', 'Buy'),
                float(entry.get('entry_price', 0)),
                float(entry.get('exit_price', 0)),
                float(entry.get('lot_size', 0.01)),
            )

        entries[idx] = entry
        _save_journal(entries)
        return jsonify({'ok': True, 'entry': entry})

    # Auto-calculate P&L if not manually provided
    manual_pnl = data.get('pnl')
    entry_price = float(data.get('entry_price', 0))
    exit_price = float(data.get('exit_price', 0))
    lot_size = float(data.get('lot_size', 0.01))

    if manual_pnl and float(manual_pnl) != 0:
        pnl = float(manual_pnl)
    else:
        pnl = _calc_pnl(
            data.get('symbol', ''),
            data.get('direction', 'Buy'),
            entry_price, exit_price, lot_size,
        )

    # Determine trade status
    trade_status = data.get('trade_status', 'closed')
    position = data.get('position', data.get('direction', 'Buy'))

    # Add entry — TMT fields
    entry = {
        'symbol': data.get('symbol', '').upper(),
        'trade_title': data.get('trade_title', ''),
        'trade_status': trade_status,
        'position': position,
        'direction': position,  # backward compat
        'risk_amount': float(data.get('risk_amount', 0)),
        'entry_price': entry_price,
        'exit_price': exit_price if trade_status == 'closed' else 0,
        'lot_size': lot_size,
        'stop_loss': float(data.get('stop_loss', 0)),
        'take_profit': float(data.get('take_profit', 0)),
        'date_opened': data.get('date_opened', datetime.now().strftime('%Y-%m-%dT%H:%M')),
        'date_closed': data.get('date_closed', '') if trade_status == 'closed' else '',
        'date': data.get('date_opened', datetime.now().strftime('%Y-%m-%d %H:%M')),  # backward compat
        'pnl': pnl if trade_status == 'closed' else 0.0,
        'result': data.get('result', 'open') if trade_status == 'closed' else 'open',
        'notes': data.get('notes', ''),
        'entry_reason': data.get('entry_reason', ''),
        'exit_reason': data.get('exit_reason', '') if trade_status == 'closed' else '',
        'mood': data.get('mood', ''),
        'reflection_tags': data.get('reflection_tags', []),
        'chart_screenshot': data.get('chart_screenshot', ''),
        'chart_link': data.get('chart_link', ''),
        'id': datetime.now().timestamp(),
    }
    entries.append(entry)
    _save_journal(entries)

    return jsonify({'ok': True, 'entry': entry})


@app.route('/api/journal/stats')
@login_required
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


@app.route('/api/journal/live-pnl', methods=['POST'])
@login_required
def api_journal_live_pnl():
    """Calculate live P&L for open trades using current market prices."""
    data = request.json
    indices = data.get('indices', [])  # list of journal entry indices
    entries = _load_journal()

    live_prices = {}
    results = []

    for idx in indices:
        if idx < 0 or idx >= len(entries):
            continue
        entry = entries[idx]
        symbol = entry.get('symbol', '')
        direction = entry.get('direction', 'Buy')
        entry_price = float(entry.get('entry_price', 0))
        lot_size = float(entry.get('lot_size', 0.01))
        entry_id = entry.get('id', idx)

        if not symbol or not entry_price:
            results.append({'id': entry_id, 'index': idx, 'pnl': 0, 'current_price': 0, 'error': 'Missing params'})
            continue

        # Fetch live price (use cache)
        if symbol not in live_prices:
            from .data_fetcher import fetch_single_live_price
            live_price = fetch_single_live_price(symbol)
            if live_price is None:
                results.append({'id': entry_id, 'index': idx, 'pnl': 0, 'current_price': 0, 'error': 'No price'})
                continue
            live_prices[symbol] = live_price
        else:
            live_price = live_prices[symbol]

        # Calculate P&L
        pnl = _calc_pnl(symbol, direction, entry_price, live_price, lot_size)
        pips = (live_price - entry_price) / INSTRUMENTS.get(symbol, {}).get('pip_factor', 0.0001) if INSTRUMENTS.get(symbol, {}).get('pip_factor', 0.0001) else 0
        if direction.lower() == 'sell':
            pips = -pips

        results.append({
            'id': entry_id,
            'index': idx,
            'pnl': round(pnl, 2),
            'pips': round(pips, 1),
            'current_price': round(live_price, 5),
            'change_pct': round((live_price - entry_price) / entry_price * 100, 2),
        })

    return jsonify({'results': results})


if __name__ == '__main__':
    import socket
    hostname = socket.gethostname()
# ─── Remote Broker Agent Endpoint ───────────────────────────────────────────
# Allows a local Python script (running on user's PC where MT5 is installed)
# to push MT5 trade data to the radar via a shared API key.

@app.route('/api/broker/remote-push', methods=['POST'])
def api_broker_remote_push():
    """Accept MT5 trade data pushed from a local broker agent.

    The agent must provide the correct X-Api-Key header matching
    the BROKER_REMOTE_KEY env var set on the server.
    """
    from .broker_sync import load_config, get_connected_user_slug

    expected_key = os.environ.get('BROKER_REMOTE_KEY', '')
    if not expected_key:
        return jsonify({'ok': False, 'error': 'Remote sync not configured on server'}), 503

    provided = request.headers.get('X-Api-Key', '')
    if provided != expected_key:
        return jsonify({'ok': False, 'error': 'Invalid API key'}), 401

    data = request.json
    if not data or 'action' not in data:
        return jsonify({'ok': False, 'error': 'Missing action field'}), 400

    action = data['action']
    entries = _load_journal()

    if action == 'add_trades':
        """Add multiple MT5 trades to the journal, deduplicated by mt5_ticket."""
        trades = data.get('trades', [])
        existing_tickets = set()
        for e in entries:
            if e.get('mt5_ticket'):
                existing_tickets.add(int(e['mt5_ticket']))

        imported = 0
        for t in trades:
            ticket = int(t.get('ticket', 0))
            if ticket and ticket in existing_tickets:
                continue
            if ticket:
                existing_tickets.add(ticket)

            entry = {
                'symbol': t.get('symbol', '').upper(),
                'trade_title': t.get('trade_title', f"{t.get('symbol', '')} {t.get('direction', '')} (MT5)"),
                'trade_status': 'closed',
                'position': t.get('direction', 'Buy'),
                'direction': t.get('direction', 'Buy'),
                'risk_amount': float(t.get('risk_amount', 0)),
                'entry_price': float(t.get('entry_price', 0)),
                'exit_price': float(t.get('exit_price', 0)),
                'lot_size': float(t.get('lot_size', 0.01)),
                'stop_loss': float(t.get('stop_loss', 0)),
                'take_profit': float(t.get('take_profit', 0)),
                'date_opened': t.get('date_opened', ''),
                'date_closed': t.get('date_closed', ''),
                'date': t.get('date_opened', ''),
                'pnl': float(t.get('pnl', 0)),
                'result': t.get('result', 'win') if float(t.get('pnl', 0)) != 0 else 'open',
                'notes': t.get('notes', 'Imported from MT5'),
                'mt5_ticket': ticket,
                'id': datetime.now().timestamp() + imported / 1000,
            }
            entries.append(entry)
            imported += 1

        if imported:
            _save_journal(entries)
        return jsonify({'ok': True, 'imported': imported})

    elif action == 'account_info':
        """Update account info (balance, equity, etc.) stored in a side file."""
        info_file = Path(__file__).parent / "remote_broker_account.json"
        info_file.write_text(json.dumps(data.get('info', {}), indent=2))
        return jsonify({'ok': True, 'saved': 'account_info'})

    elif action == 'positions':
        """Store current open positions (for live P&L display)."""
        pos_file = Path(__file__).parent / "remote_broker_positions.json"
        pos_file.write_text(json.dumps(data.get('positions', []), indent=2))
        return jsonify({'ok': True, 'saved': 'positions'})

    return jsonify({'ok': False, 'error': f'Unknown action: {action}'}), 400


    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '1') == '1'
    try:
        lan_ip = socket.gethostbyname(hostname)
    except Exception:
        lan_ip = '127.0.0.1'
    print('=' * 50)
    print('   Yams Radar - Web Dashboard')
    print(f'   Local:   http://127.0.0.1:{port}')
    if port == 5000:
        print(f'   Network: http://{lan_ip}:{port}')
    print('=' * 50)
    app.run(debug=debug, host='0.0.0.0', port=port)
