from flask import Flask, render_template, request, jsonify, session, g, abort
import json
import random
import ast
import re
import secrets
import math
import logging
import os
import sys
import time
import uuid
from random import randint
from itertools import combinations
from logging_config import setup_logging

# Load environment variables from a .env file BEFORE importing anything that reads
# config at import time. auth_utils -> firebase_config reads FLASK_ENV / Firebase
# credentials, and auth_utils reads LOCAL_MODE, all at module load -- so the .env
# load MUST happen before those imports below or .env is ignored. Also runs before
# the os.environ reads later in this file (FLASK_SECRET_KEY etc.). This lets secrets
# live in a git-ignored .env instead of being hard-coded in the WSGI file.
# No-op if python-dotenv isn't installed or no .env exists.
# Where the app's files live. From source, all of these are this folder. In the bundled exe
# (PyInstaller), code, templates and static files are unpacked to a temporary folder
# (RESOURCE_DIR) that is deleted when the app closes; the exe itself sits in APP_DIR; and the
# user's own files (.env, gallery caches, saved strategies) live in DATA_DIR, a per-user
# settings folder, so they survive restarts, updates and moving the exe (see _resolve_data_dir).
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    RESOURCE_DIR = getattr(sys, '_MEIPASS', APP_DIR)
else:
    APP_DIR = RESOURCE_DIR = os.path.dirname(os.path.abspath(__file__))

# Files the app writes and must remember: .env (class website connection), saved Local
# strategies and the gallery caches.
_DATA_FILES = ('.env', 'local_gallery.json', 'strategy_cache.json', 'practice_cache.json',
               '.gallery_key',   # the key that unscrambles the two caches (code_vault.py)
               'gallery_source.json')   # live submissions or which snapshot the cache holds


def _user_data_dir():
    """The OS's per-user settings folder, so the downloaded app remembers its settings no matter
    which folder (or which version of) the exe is started from."""
    if sys.platform == 'win32':
        return os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), 'IPD Practice App')
    if sys.platform == 'darwin':
        return os.path.expanduser('~/Library/Application Support/IPD Practice App')
    return os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'), 'ipd-app')


def _resolve_data_dir():
    """Where DATA_FILES live. From source: the repo folder (as always). The exe: the per-user
    folder, copying in once any files an older exe kept next to itself. IPD_DATA_DIR overrides
    both (used by tests). Falls back to the app's folder if the per-user one can't be created."""
    override = os.environ.get('IPD_DATA_DIR', '').strip()
    if override:
        target = override
    elif getattr(sys, 'frozen', False):
        target = _user_data_dir()
    else:
        return APP_DIR
    try:
        os.makedirs(target, exist_ok=True)
        import shutil
        for name in _DATA_FILES:
            old, new = os.path.join(APP_DIR, name), os.path.join(target, name)
            if os.path.isfile(old) and not os.path.exists(new):
                shutil.copy2(old, new)
        return target
    except OSError:
        return APP_DIR


DATA_DIR = _resolve_data_dir()

try:
    from dotenv import load_dotenv
    # Load the .env sitting next to this file by absolute path. On PythonAnywhere
    # the WSGI process runs from a different working directory, so a bare
    # load_dotenv() (which searches CWD) silently finds nothing and leaves
    # FLASK_SECRET_KEY unset -> the app crashes on import. An absolute path makes
    # the load independent of the working directory.
    _env_path = os.path.join(DATA_DIR, '.env')
    load_dotenv(_env_path)
except ImportError:
    pass

from auth_utils import verify_firebase_token, create_or_update_user, get_user_role, login_required, admin_required, is_admin
from audit_utils import log_audit_event, get_request_context
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from payoff_models import PairwiseMatrixPayoff, PublicGoodsPayoff, KCooperatorTensorPayoff
from n_player_simulation import group_tournament, call_strategy, validate_move, describe_strategy_error
from tournament_package import sha256_text, canonical_json
from ranking import normalize_ranking, rank_entries, describe_ranking, DEFAULT_RANKING
import code_vault
from version import APP_VERSION

# --- SECURITY & LIMITS ---
# Shared instruction cap (one exception class and one tracer for every engine). See
# instruction_limit.py for why the tracer is never switched off after each call.
from instruction_limit import (InstructionLimitExceeded, EngineTraceError,  # noqa: F401  (re-exported)
                               MAX_STRATEGY_INSTRUCTIONS, run_with_limit, release_tracer)

# (There is no per-match wall-clock cap: the app runs on the user's own machine, so a match
# takes as long as it takes. A pathological per-round strategy is still bounded by the per-call
# instruction cap, run_with_limit, which raises instead of looping forever.)

# Max accepted size (in bytes) of a single submitted strategy's source code. Bounds
# DoS via huge payloads before any exec/compile. Generous for legitimate strategies.
try:
    MAX_STRATEGY_CODE_BYTES = int(os.environ.get('MAX_STRATEGY_CODE_BYTES', '20000'))
except (TypeError, ValueError):
    MAX_STRATEGY_CODE_BYTES = 20000

# Memory-bomb bounds. The instruction cap (run_with_limit) bounds *Python-level* work but not a
# single C-level allocation, so a construct like [0]*10**9 or int('9'*10**7) can OOM the machine
# before the instruction cap trips. RLIMIT_AS needs a subprocess we don't have, so is_safe_code
# rejects these at the AST level (statically-detectable cases only; dynamic args stay bounded by
# the instruction cap).
try:
    MAX_LITERAL_INT_DIGITS = int(os.environ.get('MAX_LITERAL_INT_DIGITS', '100'))
except (TypeError, ValueError):
    MAX_LITERAL_INT_DIGITS = 100
if MAX_LITERAL_INT_DIGITS < 1:
    MAX_LITERAL_INT_DIGITS = 100

try:
    MAX_LITERAL_STR_LEN = int(os.environ.get('MAX_LITERAL_STR_LEN', '10000'))
except (TypeError, ValueError):
    MAX_LITERAL_STR_LEN = 10000
if MAX_LITERAL_STR_LEN < 1:
    MAX_LITERAL_STR_LEN = 10000

try:
    MAX_SEQ_MULTIPLY = int(os.environ.get('MAX_SEQ_MULTIPLY', '1000000'))
except (TypeError, ValueError):
    MAX_SEQ_MULTIPLY = 1000000
if MAX_SEQ_MULTIPLY < 1:
    MAX_SEQ_MULTIPLY = 1000000

# Exponentiation digit ceiling. A bare power like 2**32 or 10**1000 is cheap, valid arithmetic
# (a few bytes / a few hundred bytes) and MUST be allowed — only a digit-EXPLOSION such as
# 10**10**8 (≈10^8 decimal digits ≈ 40 MB) is an allocation bomb. So we bound the result's digit
# count, not its magnitude, and keep the limit generous so it never trips on legitimate math.
try:
    MAX_POW_RESULT_DIGITS = int(os.environ.get('MAX_POW_RESULT_DIGITS', '10000'))
except (TypeError, ValueError):
    MAX_POW_RESULT_DIGITS = 10000
if MAX_POW_RESULT_DIGITS < 1:
    MAX_POW_RESULT_DIGITS = 10000

# Defense-in-depth backstop for the dynamic int('9'*n) / int(huge_str) case (Py3.11+): bound the
# digit count CPython will parse from a string. Additive — the AST checks above are the primary
# guard. Guarded by hasattr so older interpreters are unaffected.
if hasattr(sys, 'set_int_max_str_digits'):
    try:
        sys.set_int_max_str_digits(max(640, MAX_LITERAL_INT_DIGITS))
    except (ValueError, OverflowError):
        pass

# Wall-clock budget for a heterogeneous OS-sim run, in seconds. The space is C(strategies,
# num_cores), which explodes: C(14, 8) is 3003 runs, roughly 20 minutes. Runs used to be *rejected*
# past a fixed count of 60 combinations, but 60 is only about 23 seconds of work -- far below what
# anyone minds waiting for, so the cap refused runs nobody would have objected to. Now the run
# proceeds and stops when it runs out of time, reporting how much of the space it covered.
#
# Budgeted by measured time rather than a predicted count because per-combination cost depends on
# how expensive the submitted strategies are, not just on the machine.
try:
    OS_SIM_TIME_BUDGET_S = float(os.environ.get('OS_SIM_TIME_BUDGET_S', '300'))
except (TypeError, ValueError):
    OS_SIM_TIME_BUDGET_S = 300.0
if OS_SIM_TIME_BUDGET_S <= 0:
    OS_SIM_TIME_BUDGET_S = 300.0

# Optional absolute ceiling on the combination count, kept only for back-compat with anyone who
# had set MAX_COMBINATIONS. Unset means no ceiling: the time budget is the real bound. It now
# truncates the (shuffled) space instead of rejecting the run.
try:
    _mc = os.environ.get('MAX_COMBINATIONS')
    MAX_COMBINATIONS = int(_mc) if _mc not in (None, '') else None
except (TypeError, ValueError):
    MAX_COMBINATIONS = None
if MAX_COMBINATIONS is not None and MAX_COMBINATIONS < 1:
    MAX_COMBINATIONS = None


def _heterogeneous_combination_error(num_cores, n_strategies):
    """Return a user-facing error string if a heterogeneous OS-sim run is impossible, else None.

    Only validity is checked here, never cost. Heterogeneous places one *distinct* strategy per
    core, so fewer strategies than cores cannot be arranged at all. An expensive-but-possible run
    is no longer refused: it is bounded by ``OS_SIM_TIME_BUDGET_S`` and reported as sampled.
    """
    if n_strategies < num_cores:
        return (f"Heterogeneous assignment needs at least num_cores ({num_cores}) strategies; "
                f"you provided {n_strategies}. Add strategies, lower cores, or use homogeneous.")
    return None

# Valid CPU scheduler policies for the OS simulation (see schedulers.py).
VALID_SCHEDULERS = ('fcfs', 'round_robin', 'sjf', 'priority', 'mlfq', 'cfs', 'affinity')
VALID_ASSIGNMENT_MODES = ('homogeneous', 'heterogeneous')

# --------------------------

# Configure logging before Flask app initialization
# This repo is always the LOCAL student app (never a public server — that's ipd-hub), so it
# defaults to development: HTTP is fine and no FLASK_SECRET_KEY is required to run.
FLASK_ENV = os.environ.get('FLASK_ENV', 'development')
IS_PRODUCTION = FLASK_ENV == 'production'

# Local single-user mode (see .env.example / README "Deployment model"). When on,
# the app runs as one fixed local owner on the student's own machine: no Firebase
# login is required to practice. The auth decorators (auth_utils.py) short-circuit;
# the before_request hook below injects the matching synthetic session user so the
# 55 routes that read session['user'] keep working unchanged.
# This repo is always the local student app, so LOCAL_MODE defaults ON (auto-logs in the single
# local owner). It's local-only — the website (ipd-hub) is the real auth boundary. Set it to a
# falsey value only for niche testing.
LOCAL_MODE = os.environ.get('LOCAL_MODE', 'true').strip().lower() in ('1', 'true', 'yes', 'on')
LOCAL_OWNER = {
    'uid': 'local-owner',
    'email': 'local@localhost',
    'display_name': 'Local Owner',
    'role': 'admin',
}

# Hub (website) mode (see .env.example / README "Deployment model"). When on, this
# instance is the hosted student-facing website: it keeps Firebase login + storage,
# serves strategies over HTTP, and receives uploads -- but it runs NO heavy compute
# (practice / tournament runs happen only in the local app). Login is required only
# to UPLOAD; results/landing are public reads.

# Shared class token the local app SENDS to the website's strategy-fetch API (hub_client
# reads the same HUB_API_TOKEN). Not per-user login -- a low-sensitivity read token
# provided in the local app config. Blank => the Hub is disabled (offline).
HUB_API_TOKEN = os.environ.get('HUB_API_TOKEN', '').strip()

# Website base URL (same value hub_client reads). Exposed to templates so the page can build
# "open the website" handoff links (e.g. an admin adding a strategy to the shared gallery).
# Blank => no website configured; the handoff buttons explain that instead of opening a bad URL.
HUB_BASE_URL = os.environ.get('HUB_BASE_URL', '').strip().rstrip('/')

app_logger = setup_logging(is_production=IS_PRODUCTION)

app = Flask(__name__, template_folder=os.path.join(RESOURCE_DIR, 'templates'),
            static_folder=os.path.join(RESOURCE_DIR, 'static'))


def _rate_limit_key():
    """Rate-limit per logged-in user, falling back to client IP for anonymous requests.

    Keying on the IP alone means an entire class behind one campus NAT shares a single bucket
    (~50/hour total). Since practice is login-gated, almost every rate-limited request is
    authenticated, so per-user keying gives each student their own budget.
    """
    user = session.get('user')
    if user and user.get('uid'):
        return f"user:{user['uid']}"
    return get_remote_address()


# Initialize Rate Limiter. Per-user keying (above) means the per-user defaults below are generous
# enough for active browsing/editing; the per-route @limiter.limit decorators bound the expensive
# compute endpoints. storage_uri="memory://" is fine for PA's single free worker (per-process,
# resets on reload; would under-enforce across multiple workers).
limiter = Limiter(
    _rate_limit_key,
    app=app,
    default_limits=["2000 per day", "300 per hour"],
    storage_uri="memory://"
)

# Copy logger handlers to Flask app logger
for handler in logging.getLogger().handlers:
    app.logger.addHandler(handler)
app.logger.setLevel(logging.getLogger().level)

# Log startup environment
app.logger.info(f"=" * 50)
app.logger.info(f"Starting Flask application")
app.logger.info(f"Environment: {FLASK_ENV}")
app.logger.info(f"Production mode: {IS_PRODUCTION}")
app.logger.info(f"=" * 50)

# --- SECURITY CONFIGURATION ---
# 1. Secret Key. Must be a STABLE value shared across all worker processes:
#    a per-process os.urandom() key means a session cookie signed by one worker
#    is rejected by another, logging users out unpredictably. Require it in
#    production; fall back to a fixed dev key (with a warning) only in dev.
_secret_key = os.environ.get('FLASK_SECRET_KEY')
if not _secret_key:
    if IS_PRODUCTION:
        raise RuntimeError(
            "FLASK_SECRET_KEY environment variable must be set in production. "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    _secret_key = 'ipd-app-local-session-key'
    app.logger.info(
        "No FLASK_SECRET_KEY set — using the built-in local session key. That's expected for "
        "the local app (sessions are local-only). Set FLASK_SECRET_KEY only for a shared deployment."
    )
app.secret_key = _secret_key

# 2. Cookie Security - Environment-aware
# In production, require HTTPS for cookies. In development, allow HTTP.
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,  # JavaScript cannot access the session cookie (anti-XSS)
    SESSION_COOKIE_SAMESITE='Lax', # Prevents CSRF by not sending cookies on cross-site POSTs
    SESSION_COOKIE_SECURE=IS_PRODUCTION,   # True in production (HTTPS), False in dev (HTTP)
)

if IS_PRODUCTION:
    app.logger.info("SESSION_COOKIE_SECURE enabled (HTTPS required)")
else:
    app.logger.warning("SESSION_COOKIE_SECURE disabled (development mode - HTTP allowed)")

@app.before_request
def _assign_csp_nonce():
    """Mint a fresh per-request nonce so templates can authorize their inline <script> blocks
    without 'unsafe-inline' (item 3). Exposed to Jinja via the context processor below."""
    g.csp_nonce = secrets.token_urlsafe(16)


# Loopback addresses that are allowed to reach the LOCAL_MODE app. The whole LOCAL_MODE trust
# model assumes the app is only ever reached from the same machine (it auto-authenticates every
# request as the local owner with NO credentials). This is the single place that assumption is
# pinned down, so it still holds if the server is ever bound to 0.0.0.0 (e.g. `waitress-serve`
# without --host).
_LOOPBACK_ADDRS = frozenset({'127.0.0.1', '::1', 'localhost'})

# Opt-in escape hatch: set ALLOW_LAN=true only if you deliberately want to reach this app from
# another device AND understand that doing so grants that device the local owner's (admin) access.
ALLOW_LAN = os.environ.get('ALLOW_LAN', '').strip().lower() in ('1', 'true', 'yes', 'on')


@app.before_request
def _enforce_local_only_and_inject_owner():
    """In LOCAL_MODE, auto-authenticate the request as the single local owner so the auth-gated
    UI works with no Firebase login, and make session['user'] present for the routes that read it.

    Because that auto-login grants admin with no credentials, it is only safe on loopback. The
    auth decorators here are no-ops (this is the credential-free student build), so if the server
    were bound to 0.0.0.0 a LAN client could otherwise use the whole app as admin. Guard against
    that by rejecting non-loopback requests outright (unless ALLOW_LAN is explicitly set). No-op
    when LOCAL_MODE is off (then the real auth boundary — the website — applies)."""
    if not LOCAL_MODE:
        return
    if request.remote_addr not in _LOOPBACK_ADDRS and not ALLOW_LAN:
        abort(403)
    if 'user' not in session:
        session['user'] = dict(LOCAL_OWNER)


@app.context_processor
def _inject_csp_nonce():
    """Make {{ csp_nonce }} available in every template."""
    return {'csp_nonce': getattr(g, 'csp_nonce', '')}


@app.context_processor
def _inject_modes():
    """Expose LOCAL_MODE and the configured website base URL to templates."""
    return {'LOCAL_MODE': LOCAL_MODE, 'hub_base_url': HUB_BASE_URL, 'APP_VERSION': APP_VERSION}


@app.teardown_request
def _release_strategy_tracer(exc=None):
    """Switch the instruction-cap tracer off once per request, not once per strategy call."""
    release_tracer()


@app.after_request
def add_security_headers(response):
    """Add security headers to every response"""
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    # CSP: inline <script> blocks are authorized by a per-request nonce (not 'unsafe-inline'), so a
    # reflected/stored payload that injects a <script> without the nonce won't execute. All inline
    # event-handler attributes were migrated to nonce'd delegated listeners so script-src needs no
    # 'unsafe-inline'. style-src keeps 'unsafe-inline' (inline styles only; not a script vector).
    nonce = getattr(g, 'csp_nonce', '')
    script_src = f"script-src 'self' 'nonce-{nonce}' https://www.gstatic.com https://cdn.jsdelivr.net; " if nonce \
        else "script-src 'self' https://www.gstatic.com https://cdn.jsdelivr.net; "
    csp = (
        "default-src 'self'; "
        + script_src +
        "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
        "font-src 'self' https://cdnjs.cloudflare.com https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self' https://*.googleapis.com;"
    )
    response.headers['Content-Security-Policy'] = csp
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response

# ------------------------------

# --- EMAIL DOMAIN BLACKLIST ---
# Domains blocked from signup to prevent spam/abuse
# Can be configured via environment variable: EMAIL_DOMAIN_BLACKLIST=spam.com,throwaway.net
EMAIL_DOMAIN_BLACKLIST = os.environ.get('EMAIL_DOMAIN_BLACKLIST', '').split(',')
EMAIL_DOMAIN_BLACKLIST = [d.strip().lower() for d in EMAIL_DOMAIN_BLACKLIST if d.strip()]

# Add common disposable email domains (extend as needed)
EMAIL_DOMAIN_BLACKLIST.extend([
    'tempmail.com',
    '10minutemail.com',
    'guerrillamail.com',
    'mailinator.com',
    'throwaway.email',
])

# ------------------------------


def validate_name(name, field_type="name"):
    """
    Validate user-controlled names (strategy names, tournament names) to prevent XSS.
    
    Args:
        name: The name string to validate
        field_type: Type of field for error messages (default: "name")
    
    Returns:
        Tuple of (is_valid: bool, error_message: str)
    """
    import re
    
    # Check if name is provided
    if not name or not isinstance(name, str):
        return False, f"{field_type.capitalize()} is required"
    
    # Strip whitespace for validation
    name = name.strip()
    
    # Check length (1-100 characters)
    if len(name) < 1:
        return False, f"{field_type.capitalize()} must be at least 1 character long"
    if len(name) > 100:
        return False, f"{field_type.capitalize()} must be 100 characters or less"
    
    # Always block HTML tag delimiters and null bytes.
    # (Even if names are escaped, these are strong signals of XSS attempts.)
    if '<' in name or '>' in name or '\x00' in name:
        return False, f"{field_type.capitalize()} contains invalid characters (HTML tags or null bytes not allowed)"

    # Always block control characters (tabs/newlines) to avoid log/CSV/UI injection.
    if any(ch in name for ch in ['\t', '\n', '\r']):
        return False, f"{field_type.capitalize()} contains invalid characters"

    # Enforce a strict character set for the names students actually choose (strategy/bot
    # names) and the default field_type. The leaderboard, the N-Player groups list and the
    # website gallery all render these names, so we keep them to letters, digits, spaces,
    # underscore, hyphen and apostrophe — enough for "Hijab's strategy" or "house_bot_3",
    # but no HTML/quote/ampersand characters. (Must stay in step with ipd-hub's validate_name.)
    if field_type in ('name', 'strategy name', 'bot name'):
        if '&' in name or '"' in name:
            return False, f"{field_type.capitalize()} contains invalid characters"
        if not re.match(r"^[a-zA-Z0-9 _\-']+$", name):
            return False, f"{field_type.capitalize()} contains invalid characters"
    
    # Check for suspicious XSS patterns (case-insensitive)
    suspicious_patterns = [
        r'script',
        r'javascript:',
        r'onerror',
        r'onclick',
        r'onload',
        r'<iframe',
        r'<embed',
        r'<object',
    ]
    
    name_lower = name.lower()
    for pattern in suspicious_patterns:
        if re.search(pattern, name_lower):
            return False, f"{field_type.capitalize()} contains suspicious pattern"
    
    return True, ""


def _is_truthy(v):
    """Interpret a JSON-ish flag (bool/str/int) as a boolean."""
    return str(v).strip().lower() in ('1', 'true', 'yes', 'on')


def check_unique_strategy_names(strategies):
    """Reject a roster that reuses a name. Names are the leaderboard key, so two strategies
    sharing one name silently collapse into a single row (one quietly shadows the other) —
    the UI's "Important Rules" promises names are unique, so enforce it here. Compares on the
    stripped name to match how the engines key results. Returns (is_valid, error_message)."""
    seen = set()
    for strategy in strategies:
        raw = strategy.get('name', '') if isinstance(strategy, dict) else ''
        name = raw.strip() if isinstance(raw, str) else ''
        key = name.lower()
        if key in seen:
            return False, (f"Duplicate strategy name: '{name}'. Each strategy must have a "
                           f"different name (names are used to identify entries in the results). "
                           f"Check your editor boxes and the entries ticked in the gallery, "
                           f"including saved Local ones.")
        seen.add(key)
    return True, ""


def _screening_shortfall(flags):
    """400 response when screening leaves fewer than 2 strategies; `flags` names each one and why."""
    n = len(flags)
    return jsonify({'error': f"Sandbox mode left out {n} {'strategy' if n == 1 else 'strategies'} "
                             f"(see below for which and why), so fewer than 2 remain to run.",
                    'flags': flags, 'screened': True}), 400


# Modules that user strategies are permitted to import. Single source of truth lives in
# core_simulation so the OS-sim sandbox, the 1v1/N-Player sandbox, and the static `is_safe_code`
# checker all agree. Dangerous stdlib modules (os, sys, subprocess, socket, io, pathlib, shutil,
# importlib, ctypes, pickle, threading, multiprocessing, signal, gc, inspect, ast, dis, time, etc.)
# are intentionally absent.
from core_simulation import ALLOWED_IMPORTS, guarded_import


# Guarded attribute access for the sandbox. `is_safe_code` blocks `x.__class__` syntactically
# (any ast.Attribute whose name starts with '_'), but the *string* form getattr(x, '__class__')
# is invisible to the AST screen and would walk straight out of the sandbox. These wrappers apply
# the exact same rule at runtime, so strategies get ordinary attribute helpers without the hole.
def _guarded_getattr(obj, name, *default):
    if isinstance(name, str) and name.startswith('_'):
        raise AttributeError(f"Access to internal attribute '{name}' is not allowed")
    return getattr(obj, name, *default)


def _guarded_setattr(obj, name, value):
    if isinstance(name, str) and name.startswith('_'):
        raise AttributeError(f"Setting internal attribute '{name}' is not allowed")
    setattr(obj, name, value)


def _guarded_delattr(obj, name):
    if isinstance(name, str) and name.startswith('_'):
        raise AttributeError(f"Deleting internal attribute '{name}' is not allowed")
    delattr(obj, name)


def _guarded_hasattr(obj, name):
    # Returns False rather than raising -- hasattr's contract is a bool, and a raising
    # hasattr would be a surprising trap in otherwise-correct strategy code.
    if isinstance(name, str) and name.startswith('_'):
        return False
    return hasattr(obj, name)


# Builtins exposed to sandboxed strategy code. Kept byte-identical across the three copies
# (app.py, core_simulation.py, ipd-hub/app.py) so a strategy behaves the same in 1v1, N-Player,
# the OS sim and the Hub. Notable inclusions/exclusions:
#   * __build_class__ -- CPython resolves this through __builtins__ for EVERY `class` statement,
#     so without it no strategy can define a class at all.
#   * exception classes -- without them `except ValueError:` is itself a NameError.
#   * BaseException is deliberately absent: InstructionLimitExceeded derives from it precisely so
#     that `except Exception:` inside a strategy cannot swallow the instruction cap.
#   * `format` the builtin is safe; the traversal vector is "{0.__class__}".format(...) and
#     string.Formatter, both still blocked by _FORMAT_TRAVERSAL_METHODS in is_safe_code.
#   * exec/eval/compile/open/input/print stay out (also rejected statically by is_safe_code).
_SANDBOX_BUILTINS = {
    'abs': abs, 'all': all, 'any': any, 'bool': bool,
    'complex': complex, 'divmod': divmod, 'enumerate': enumerate,
    'filter': filter, 'float': float, 'int': int, 'len': len,
    'list': list, 'map': map, 'max': max, 'min': min,
    'pow': pow, 'range': range, 'reversed': reversed,
    'round': round, 'set': set, 'sorted': sorted,
    'str': str, 'sum': sum, 'tuple': tuple, 'zip': zip,
    'dict': dict,
    # --- class support ---
    '__build_class__': __build_class__,
    'object': object, 'super': super, 'property': property,
    'staticmethod': staticmethod, 'classmethod': classmethod,
    # --- ordinary builtins strategies routinely assume ---
    'ord': ord, 'chr': chr, 'isinstance': isinstance, 'issubclass': issubclass,
    'type': type, 'frozenset': frozenset, 'bytes': bytes, 'hash': hash,
    'repr': repr, 'iter': iter, 'next': next, 'slice': slice,
    'callable': callable, 'id': id, 'format': format,
    'bin': bin, 'hex': hex, 'oct': oct,
    # --- guarded attribute access (see wrappers above) ---
    'getattr': _guarded_getattr, 'setattr': _guarded_setattr,
    'delattr': _guarded_delattr, 'hasattr': _guarded_hasattr,
    # --- exception classes ---
    'Exception': Exception, 'ArithmeticError': ArithmeticError,
    'ZeroDivisionError': ZeroDivisionError, 'OverflowError': OverflowError,
    'FloatingPointError': FloatingPointError,
    'ValueError': ValueError, 'TypeError': TypeError, 'KeyError': KeyError,
    'IndexError': IndexError, 'LookupError': LookupError,
    'AttributeError': AttributeError, 'NameError': NameError,
    'RuntimeError': RuntimeError, 'RecursionError': RecursionError,
    'StopIteration': StopIteration, 'NotImplementedError': NotImplementedError,
    'AssertionError': AssertionError,
}


def is_safe_code(code):
    """Statically analyze code to ensure it's safe."""
    if not isinstance(code, str):
        return False, "Strategy code must be a string."
    if len(code.encode('utf-8')) > MAX_STRATEGY_CODE_BYTES:
        return False, f"Strategy code exceeds the maximum allowed size ({MAX_STRATEGY_CODE_BYTES} bytes)."
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"Syntax Error: {e}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level = alias.name.split('.')[0]
                if top_level not in ALLOWED_IMPORTS:
                    return False, f"Import of '{alias.name}' is not allowed."
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ''
            top_level = module.split('.')[0]
            if top_level not in ALLOWED_IMPORTS:
                return False, f"Import from '{module}' is not allowed."
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in ['exec', 'eval', 'compile', 'open', 'input', 'print']:
                    return False, f"Function '{node.func.id}' is not allowed."
                # Item 2: reject statically-detectable allocation bombs via int()/range() on a
                # huge constant arg, e.g. range(10**12) or int('9'*10**7) (the literal-cap below
                # also catches the inner string). Dynamic args remain bounded by the time/line caps.
                if node.func.id in ('int', 'range') and node.args:
                    bad = _oversized_literal_arg(node.args)
                    if bad:
                        return False, f"'{node.func.id}()' with an oversized literal argument is not allowed ({bad})."
            # Item 1: str.format / field-traversal methods hide dunder names inside *string
            # literals* (e.g. "{0.__class__.__init__.__globals__}".format(obj)) where the
            # syntactic attribute check below cannot see them. Block the method-call forms; the
            # `format` builtin is already absent from safe_builtins. Also closes string.Formatter
            # (`string` is an allowed import) via vformat/get_field/etc.
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in _FORMAT_TRAVERSAL_METHODS:
                    return False, (f"Method '.{node.func.attr}' is not allowed "
                                   f"(string-based attribute traversal).")
        if isinstance(node, ast.Attribute):
            if node.attr.startswith('_'):
                return False, f"Accessing internal attribute '{node.attr}' is not allowed."
        # Item 2: cap literal sizes so a single huge literal can't OOM the worker.
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                pass  # bool is an int subclass; never oversized
            elif isinstance(node.value, int):
                if len(str(abs(node.value))) > MAX_LITERAL_INT_DIGITS:
                    return False, (f"Integer literal exceeds {MAX_LITERAL_INT_DIGITS} digits "
                                   f"(memory-bomb guard).")
            elif isinstance(node.value, str):
                if len(node.value) > MAX_LITERAL_STR_LEN:
                    return False, (f"String literal exceeds {MAX_LITERAL_STR_LEN} characters "
                                   f"(memory-bomb guard).")
        if isinstance(node, ast.BinOp):
            # Item 2: bound sequence multiplication (`seq * n`, `n * seq`) — a huge factor against a
            # SEQUENCE means a huge list/str allocation (e.g. [0]*10**9, 'a'*10**9). We only flag it
            # when one operand is a sequence *literal*, so ordinary integer scaling like
            # `weight * 2**32` is left alone. Modest powers are folded so [0]*10**9 == [0]*1000000000.
            if isinstance(node.op, ast.Mult):
                if _is_sequence_literal(node.left):
                    factor = _static_int(node.right)
                elif _is_sequence_literal(node.right):
                    factor = _static_int(node.left)
                else:
                    factor = None
                if factor is not None and abs(factor) > MAX_SEQ_MULTIPLY:
                    return False, (f"Sequence multiplication by {factor} exceeds the limit of "
                                   f"{MAX_SEQ_MULTIPLY} (memory-bomb guard).")
            # Item 2: a standalone power (e.g. 2**32, 10**1000) is cheap, valid arithmetic and is
            # allowed; only flag a digit-EXPLOSION such as 10**10**8 that would build a huge int.
            elif isinstance(node.op, ast.Pow):
                digits = _pow_result_digits(node)
                if digits is not None and digits > MAX_POW_RESULT_DIGITS:
                    return False, (f"Exponentiation produces an integer with ~{int(digits)} digits, "
                                   f"exceeding the {MAX_POW_RESULT_DIGITS}-digit limit (memory-bomb guard).")

    return True, None


# Item 1: str/Formatter methods whose format strings can perform attribute/index traversal.
_FORMAT_TRAVERSAL_METHODS = frozenset({
    'format', 'format_map', 'format_field', 'vformat', 'get_field', 'convert_field',
})


def _static_int(node):
    """Return the int value of an int literal or a foldable int Pow (e.g. 10**9), else None.

    Folds a constant `base ** exp` so `[0] * 10**9` is caught the same as `[0] * 1000000000`,
    but caps the exponent first so evaluating the guard can't itself be the memory bomb.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
        base = _static_int(node.left)
        exp = _static_int(node.right)
        if base is not None and exp is not None and 0 <= exp <= 64 and abs(base) <= 1024:
            return base ** exp
    return None


def _is_sequence_literal(node):
    """True for literals whose `* n` materialises an n-times-larger sequence (list/tuple/set/str/
    bytes, or a comprehension). Plain names/numbers are excluded so integer scaling isn't flagged."""
    if isinstance(node, (ast.List, ast.Tuple, ast.Set, ast.ListComp, ast.SetComp)):
        return True
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
        return True
    return False


def _pow_result_digits(node):
    """Approx number of decimal digits of a statically-evaluable ``base ** exp``, else None.

    Computed via logarithms so the guard itself never materialises a huge integer. Returns None
    when either operand isn't statically known (then the runtime line/time caps apply) or when the
    result is <= 1 (no allocation concern). Only used to flag digit-explosion bombs like 10**10**8.
    """
    base = _static_int(node.left)
    exp = _static_int(node.right)
    if base is None or exp is None:
        return None
    if exp <= 0 or abs(base) <= 1:
        return None
    return exp * math.log10(abs(base)) + 1


def _oversized_literal_arg(args):
    """If any arg is an oversized int/str literal, return a short description, else None."""
    for arg in args:
        val = _static_int(arg)
        if val is not None and abs(val) > MAX_SEQ_MULTIPLY:
            return f"value {val}"
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and len(arg.value) > MAX_LITERAL_STR_LEN:
            return f"{len(arg.value)}-char string"
    return None


def get_safe_globals(context=None, rng=None):
    """Return a dictionary of safe globals for strategy execution."""
    # Create globals_dict first so we can reference it in the globals() function
    globals_dict = {}

    # Fresh copy per call: the closure below captures this call's globals_dict, and a shared
    # mutable dict would let one strategy's exec leak names into another's. The import hook is
    # shared with the OS sim (core_simulation.guarded_import): ALLOWED_IMPORTS only, with guarded
    # copies of modules such as `operator`.
    safe_builtins = dict(_SANDBOX_BUILTINS)
    safe_builtins.update({
        '__import__': guarded_import,
        'globals': lambda: globals_dict  # Allow strategies to check what's in their globals
    })

    # Use provided RNG instance or module-level random
    random_obj = rng if rng is not None else random

    globals_dict.update({
        '__builtins__': safe_builtins,
        # Required for `class` statements: a class body evaluates `__module__ = __name__`, so
        # without this every class definition dies with "name '__name__' is not defined".
        # An inert placeholder, not a real module name -- nothing can be looked up from it.
        '__name__': 'strategy',
        'random': random_obj,
        'math': math,
        'randint': random_obj.randint if rng else randint
    })

    if context:
        globals_dict.update(context)

    return globals_dict


def extract_strategy_function(code, globals_dict, prefer_n_args=3):
    """
    Extract strategy function from code using AST to find the actual function definition.
    This avoids accidentally picking up builtins like randint.
    
    Args:
        code: The strategy code string
        globals_dict: The globals dictionary after exec()
        prefer_n_args: Preferred number of parameters (3 for 2-player, 4 for N-player)
    
    Returns:
        Callable strategy function or None
    """
    # Try AST approach first
    try:
        tree = ast.parse(code)
        function_names = []
        function_signatures = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                function_names.append(node.name)
                # Count parameters
                function_signatures[node.name] = len(node.args.args)
        
        # If we found function definitions, prefer ones with preferred parameter count.
        # Try the requested arity, then the OTHER supported entry-point arity, then give up
        # and take the first function defined. The middle pass matters: without it, a strategy
        # that defines a helper above its 3-arg entry point has the HELPER selected as the
        # strategy (and then fails with a TypeError on the first round), because the caller
        # asks for prefer_n_args=4. Mirrors extract_strategy_func() in core_simulation.py.
        if function_names:
            for n_args in (prefer_n_args, 4, 3):
                for func_name in function_names:
                    if (function_signatures.get(func_name) == n_args
                            and func_name in globals_dict
                            and callable(globals_dict[func_name])):
                        return globals_dict[func_name]

            # No function with a recognised entry-point arity: take the first one we found
            for func_name in function_names:
                if func_name in globals_dict and callable(globals_dict[func_name]):
                    return globals_dict[func_name]
    except (SyntaxError, ValueError, TypeError):
        pass
    
    # Fallback: find first callable that's not a known builtin
    known_builtins = {'randint', 'abs', 'all', 'any', 'bool', 'complex', 'divmod', 
                     'enumerate', 'filter', 'float', 'int', 'len', 'list', 'map', 
                     'max', 'min', 'pow', 'range', 'reversed', 'round', 'set', 
                     'sorted', 'str', 'sum', 'tuple', 'zip', 'dict'}
    
    for name, obj in globals_dict.items():
        if callable(obj) and not name.startswith('__') and name not in known_builtins:
            return obj
    
    return None


def game(player_a_code, player_a_name, player_b_code, player_b_name, rounds=200, payoff_matrix=None, mode="standard", fixed_random_rounds=None, discount_factor=0.95, stochastic_prob=0.995, tournament_info=None, seed=None):
    # Default payoff matrix if none provided
    if payoff_matrix is None:
        payoff_matrix = {
            'CC': [3, 3],
            'CD': [0, 5],
            'DC': [5, 0],
            'DD': [1, 1]
        }
    
    # Create RNG instance if seed provided, else None (use module random)
    rng = random.Random(seed) if seed is not None else None
    # The generator strategies get as meta['rng']. It must always exist: strategies are told to use
    # it for randomness, and with no seed it used to be None, so they crashed and forfeited.
    strategy_rng = rng if rng is not None else random.Random()

    # Constants for modes (now configurable)
    DISCOUNT_FACTOR = float(discount_factor)
    STOCHASTIC_PROB = float(stochastic_prob)
    
    # Check for code safety first
    safe_a, error_a = is_safe_code(player_a_code)
    if not safe_a:
        return {
            'winner': 'Error', 'a_points': 0, 'b_points': 0, 'rounds': 0,
            'player_a': player_a_name, 'player_b': player_b_name, 'rounds_detail': [],
            'error': f"Security Violation in Player A code: {error_a}",
            'error_player': 'A', 'error_type': 'SecurityViolation',
        }

    safe_b, error_b = is_safe_code(player_b_code)
    if not safe_b:
        return {
            'winner': 'Error', 'a_points': 0, 'b_points': 0, 'rounds': 0,
            'player_a': player_a_name, 'player_b': player_b_name, 'rounds_detail': [],
            'error': f"Security Violation in Player B code: {error_b}",
            'error_player': 'B', 'error_type': 'SecurityViolation',
        }

    # Context for strategies (Weights, etc)
    # Strategies can access TOURNAMENT_INFO dict if provided
    # Always provide TOURNAMENT_INFO structure, even if empty
    if tournament_info is None:
        tournament_info = {
            'weights': None,
            'payoff_matrix': payoff_matrix,
            # Reserved keys for future N-player extensions
            'format': '2-player',
            'n_players': 2,
            'payoff_model': 'standard'
        }
    else:
        # Copy, then fill in the resolved table (if missing) and this match's mode. Copying keeps
        # the tournament's shared dict unchanged between matches.
        tournament_info = dict(tournament_info)
        if tournament_info.get('payoff_matrix') is None:
            tournament_info['payoff_matrix'] = payoff_matrix
    tournament_info['mode'] = mode
    # Each player reads its own copy, so one strategy can't change the table the other one sees.
    import copy as _copy
    player_info = [_copy.deepcopy(tournament_info), _copy.deepcopy(tournament_info)]
    
    exec_context = {'TOURNAMENT_INFO': tournament_info}

    # Execute the strategy codes in isolated scopes to extract function names
    # Security note: globals() access in strategies is best-effort sandboxing.
    # Untrusted code should be reviewed; see is_safe_code() for restrictions.
    player_a_globals = get_safe_globals(exec_context, rng)
    player_b_globals = get_safe_globals(exec_context, rng)
    
    # Execute Player A code
    try:
        exec(player_a_code, player_a_globals)
    except SyntaxError as e:
        error_msg = f"SyntaxError: {e.msg}"
        if e.lineno:
            error_msg += f" (line {e.lineno})"
        app.logger.error(f"Syntax error in Player A code: {error_msg}")
        return {
            'winner': 'Error',
            'a_points': 0,
            'b_points': 0,
            'rounds': 0,
            'player_a': player_a_name,
            'player_b': player_b_name,
            'rounds_detail': [],
            'error': error_msg,
            'error_type': 'SyntaxError',
            'error_player': 'A',
            'terminated_early': True
        }
    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        app.logger.error(f"Error compiling Player A code: {error_msg}")
        return {
            'winner': 'Error',
            'a_points': 0,
            'b_points': 0,
            'rounds': 0,
            'player_a': player_a_name,
            'player_b': player_b_name,
            'rounds_detail': [],
            'error': error_msg,
            'error_type': type(e).__name__,
            'error_player': 'A',
            'terminated_early': True
        }
    
    # Execute Player B code
    try:
        exec(player_b_code, player_b_globals)
    except SyntaxError as e:
        error_msg = f"SyntaxError: {e.msg}"
        if e.lineno:
            error_msg += f" (line {e.lineno})"
        app.logger.error(f"Syntax error in Player B code: {error_msg}")
        return {
            'winner': 'Error',
            'a_points': 0,
            'b_points': 0,
            'rounds': 0,
            'player_a': player_a_name,
            'player_b': player_b_name,
            'rounds_detail': [],
            'error': error_msg,
            'error_type': 'SyntaxError',
            'error_player': 'B',
            'terminated_early': True
        }
    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        app.logger.error(f"Error compiling Player B code: {error_msg}")
        return {
            'winner': 'Error',
            'a_points': 0,
            'b_points': 0,
            'rounds': 0,
            'player_a': player_a_name,
            'player_b': player_b_name,
            'rounds_detail': [],
            'error': error_msg,
            'error_type': type(e).__name__,
            'error_player': 'B',
            'terminated_early': True
        }

    # Extract strategy functions using improved extraction
    player_a_func = extract_strategy_function(player_a_code, player_a_globals, prefer_n_args=4)
    player_b_func = extract_strategy_function(player_b_code, player_b_globals, prefer_n_args=4)
    
    if player_a_func is None or player_b_func is None:
        # Name the player whose code has no usable function, so the right one is disqualified.
        missing = 'A' if player_a_func is None else 'B'
        return { # Return safe error instead of raising
            'winner': 'Error',
            'a_points': 0,
            'b_points': 0,
            'rounds': 0,
            'player_a': player_a_name,
            'player_b': player_b_name,
            'rounds_detail': [],
            'error': "no strategy function found in the code",
            'error_player': missing,
            'error_type': 'MissingFunction',
        }

    A_total_points = 0
    B_total_points = 0
    A_weighted_points = 0
    B_weighted_points = 0
    total_weight = 0
    
    # Track moves for counts
    a_coops = 0
    b_coops = 0
    a_defects = 0
    b_defects = 0
    
    user_history = []
    enemy_history = []
    rounds_detail = []

    # Determine round limit based on mode
    round_limit = rounds
    if mode == 'random':
        if fixed_random_rounds is not None:
            round_limit = fixed_random_rounds
        else:
            # Use RNG instance if available, else module-level random
            if rng:
                round_limit = rng.randint(100, 300)
            else:
                round_limit = randint(100, 300)
    
    current_round = 0

    while True:
        # No per-match wall-clock cap: this runs on the user's own machine, so a match takes
        # as long as it takes. A pathological per-round strategy is still bounded by the
        # per-call instruction cap (run_with_limit), which raises instead of looping forever.

        # Check termination conditions
        if mode == 'stochastic':
            if current_round > 0:
                # Use RNG instance if available, else module-level random
                rand_val = rng.random() if rng else random.random()
                if rand_val > STOCHASTIC_PROB:
                    break
            if current_round >= 2000: # Safety cap
                break
        else:
            if current_round >= round_limit:
                break

        # Get moves. Each call is bounded by the instruction cap and must return exactly 'C' or
        # 'D' (validate_move); any failure ends the match with that player named as the culprit.
        def _play(func, player, last_moves, own_history, opp_history, player_index):
            meta = {'round': current_round, 'n_players': 2, 'player_index': player_index,
                    'tournament_info': player_info[player_index], 'rng': strategy_rng}
            try:
                move = validate_move(run_with_limit(call_strategy, func, last_moves, own_history.copy(),
                                                    [opp_history.copy()], meta, limit=MAX_STRATEGY_INSTRUCTIONS))
                return move, None
            except EngineTraceError:
                raise   # engine fault: never blame the strategy
            except InstructionLimitExceeded as e:
                exc = e
            except Exception as e:
                exc = e
            reason = describe_strategy_error(exc)
            app.logger.error(f"Player {player} strategy failed: {reason}")
            return None, {'error': reason, 'error_type': type(exc).__name__,
                          'error_player': player, 'terminated_early': True}

        A_guess, error_info = _play(player_a_func, 'A', [enemy_history[-1]] if enemy_history else [],
                                    user_history, enemy_history, 0)
        if error_info is None:
            B_guess, error_info = _play(player_b_func, 'B', [user_history[-1]] if user_history else [],
                                        enemy_history, user_history, 1)
        error_occurred = error_info is not None

        # If error occurred, return with partial results
        if error_occurred:
            # Calculate final scores up to this point
            if total_weight > 0:
                A_final_score = A_weighted_points / total_weight
                B_final_score = B_weighted_points / total_weight
            else:
                A_final_score = 0
                B_final_score = 0
            
            result = {
                'winner': 'Error',
                'a_points': A_final_score,
                'b_points': B_final_score,
                'a_total_points': A_total_points,
                'b_total_points': B_total_points,
                'rounds': current_round,
                'player_a': player_a_name,
                'player_b': player_b_name,
                'a_coops': a_coops,
                'b_coops': b_coops,
                'a_defects': a_defects,
                'b_defects': b_defects,
                'rounds_detail': rounds_detail,
                'mode': mode
            }
            result.update(error_info)
            return result

        # Update move counters
        if A_guess == 'C': a_coops += 1
        else: a_defects += 1
        
        if B_guess == 'C': b_coops += 1
        else: b_defects += 1

        # Determine payoffs
        move_combination = A_guess + B_guess
        round_payoffs = payoff_matrix.get(move_combination, [1, 1])
        A_round_points = round_payoffs[0]
        B_round_points = round_payoffs[1]
        
        # Calculate weights for scoring
        if mode == 'discounted':
            weight = DISCOUNT_FACTOR ** current_round
        else:
            weight = 1.0
            
        A_weighted_points += A_round_points * weight
        B_weighted_points += B_round_points * weight
        total_weight += weight
        
        A_total_points += A_round_points
        B_total_points += B_round_points
        
        # Store details (only first 50 to save space if needed, but keeping all for now)
        if current_round < 50: 
            rounds_detail.append({
                'round': current_round + 1,
                'a_move': A_guess,
                'b_move': B_guess,
                'a_round_points': A_round_points,
                'b_round_points': B_round_points
            })

        user_history.append(A_guess)
        enemy_history.append(B_guess)
        current_round += 1

    # Calculate Average Score (Independent of Rounds)
    if total_weight > 0:
        A_final_score = A_weighted_points / total_weight
        B_final_score = B_weighted_points / total_weight
    else:
        A_final_score = 0
        B_final_score = 0

    # Determine winner based on Average Score
    if A_final_score > B_final_score:
        winner = "Player A Wins"
    elif A_final_score < B_final_score:
        winner = "Player B Wins"
    else:
        winner = "Draw"

    result = {
        'winner': winner,
        'a_points': A_final_score, # Return Average Score
        'b_points': B_final_score, # Return Average Score
        'a_total_points': A_total_points, # Return Raw Total Score
        'b_total_points': B_total_points, # Return Raw Total Score
        'rounds': current_round,
        'player_a': player_a_name,
        'player_b': player_b_name,
        'a_coops': a_coops,
        'b_coops': b_coops,
        'a_defects': a_defects,
        'b_defects': b_defects,
        'rounds_detail': rounds_detail,
        'mode': mode
    }

    return result



@app.route('/')
@login_required
def index():
    return render_template('index.html')
















# Note: The create_admin route has been removed for security. 
# Admins should be created via the Admin Panel or directly in Firestore.







@app.route('/hub/publish-result', methods=['POST'])
@login_required
@limiter.limit("10 per minute")
def hub_publish_result():
    """Stage a result package to the website (class token) and return the admin publish
    URL to open. The website then forces admin login and the prefilled publish page."""
    import hub_client
    package = request.get_json(silent=True) or {}
    # New shape nests the run output under 'result' (with a 'kind'); accept the legacy flat
    # shape (leaderboard at top level) too.
    has_result = isinstance(package.get('result'), dict) or package.get('leaderboard') is not None
    if not has_result:
        return jsonify({'error': 'No result to publish — run a tournament first.'}), 400
    try:
        draft_id = hub_client.stage_result(package)
    except hub_client.HubError as e:
        return jsonify({'error': str(e)}), 502
    base = hub_client.HUB_BASE_URL.rstrip('/')
    return jsonify({'success': True, 'publish_url': f'{base}/admin/publish?draft={draft_id}'})






# --- Strategy Hub client routes (used by the LOCAL app) ------------------------
# In the local app these let a student browse the website's strategy API and import a
# peer's strategy. The hard rule: any code coming FROM the website is untrusted and is
# run through is_safe_code() locally before it is ever handed back to be executed --
# the local sandbox is the real gate, not the website's server-side screen.

# Local strategy cache: fetched strategies persist here so the gallery works offline.
# It is ONLY refreshed when the user presses Refresh (POST /hub/refresh); every other
# read serves from this file. git-ignored, local-only, holds public strategy source.
STRATEGY_CACHE_PATH = os.path.join(DATA_DIR, 'strategy_cache.json')


# Both gallery caches are stored scrambled (code_vault) so classmates' code can't be read just by
# opening the file. The app reads it back to run matches; the browser never gets it (see
# _public_gallery and _resolve_website_strategies).
def _read_strategy_cache():
    return code_vault.load_list(STRATEGY_CACHE_PATH)


def _write_strategy_cache(strategies):
    code_vault.save_list(STRATEGY_CACHE_PATH, strategies)


# Organizer-provided PRACTICE strategies are cached separately from player submissions
# (two galleries: player submissions vs organizer practice code).
PRACTICE_CACHE_PATH = os.path.join(DATA_DIR, 'practice_cache.json')


def _read_practice_cache():
    return code_vault.load_list(PRACTICE_CACHE_PATH)


# Which list the strategy cache holds: the live submissions, or a snapshot an admin froze at the
# end of a round (students keep editing; a snapshot does not change). Saved so Refresh and the
# results remember it.
GALLERY_SOURCE_PATH = os.path.join(DATA_DIR, 'gallery_source.json')
_LIVE_SOURCE = {'source': 'live'}


def _read_gallery_source():
    try:
        with open(GALLERY_SOURCE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get('source') in ('live', 'snapshot'):
            return data
    except (OSError, ValueError):
        pass
    return dict(_LIVE_SOURCE)


def _write_gallery_source(source):
    with open(GALLERY_SOURCE_PATH, 'w', encoding='utf-8') as f:
        json.dump(source, f)


def _write_practice_cache(strategies):
    code_vault.save_list(PRACTICE_CACHE_PATH, strategies)


# --- Classmates' code stays out of the browser (student report, Oct 2026) ---------------------
# A classmate's submission ("player", i.e. any website strategy that is not a bot) is listed by
# name and id only. Runs send the id and the server fills in the code from the cache. Bots and
# organizer practice strategies keep their code: they are there to be studied.

def _is_classmate(record):
    return isinstance(record, dict) and record.get('type', 'tournament') != 'bot'


def _public_gallery(strategies):
    out = []
    for rec in strategies or []:
        if _is_classmate(rec):
            rec = {k: v for k, v in rec.items() if k != 'code'}
            rec['hidden'] = True
        out.append(rec)
    return out


def _resolve_website_strategies(strategies):
    """Fill in code (and the submission time) for strategies picked from the website gallery
    by ``website_id``. The cached code always wins over code sent by the page. Returns
    (strategies, error_message)."""
    if not isinstance(strategies, list):
        return strategies, None
    cache = {str(r.get('id')): r for r in _read_strategy_cache() + _read_practice_cache()
             if isinstance(r, dict) and r.get('id') is not None}
    out = []
    for item in strategies:
        if not isinstance(item, dict):
            out.append(item)
            continue
        item = dict(item)
        wid = item.get('website_id')
        rec = cache.get(str(wid)) if wid is not None else None
        if rec is not None:
            item['code'] = rec.get('code', '')
            if isinstance(rec.get('submitted_at'), str):
                item['submitted_at'] = rec['submitted_at']
        else:
            if wid is not None and not item.get('code'):
                return None, (f'"{item.get("name", "?")}" is not in the local gallery any more. '
                              'Press Refresh Gallery while online, then run again.')
            # Only website strategies carry a trusted submission time.
            item.pop('submitted_at', None)
        out.append(item)
    return out, None


# Local personal gallery: strategies the user saves on THIS machine ("Save locally"). Unlike
# the Hub caches above (which mirror the website), this is purely local and private — it never
# leaves the machine and is git-ignored. It gives the "Save to Gallery" button something real to
# do in the local app (the website-backed save it used to call lives only in ipd-hub now).
LOCAL_GALLERY_PATH = os.path.join(DATA_DIR, 'local_gallery.json')


def _read_local_gallery():
    try:
        with open(LOCAL_GALLERY_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, ValueError, OSError):
        return []


def _write_local_gallery(strategies):
    with open(LOCAL_GALLERY_PATH, 'w', encoding='utf-8') as f:
        json.dump(strategies, f)


@app.route('/hub', methods=['GET'])
@login_required
def hub():
    """Browse / import strategies fetched from the website, and submit/publish via handoff."""
    import hub_client
    return render_template('hub.html', hub_enabled=hub_client.hub_enabled(),
                           hub_base_url=hub_client.HUB_BASE_URL,
                           hub_has_token=bool(hub_client.HUB_API_TOKEN))


# --- "Connect to the class website" (Hub page form) ---------------------------------------
# Lets a student set the website address + class token from inside the app (Settings page)
# instead of editing .env by hand. The values are saved to DATA_DIR/.env and applied at once.
#
# This changes where the app sends the class token and fetches code from, so it must only be
# reachable from the app's own page: any website the student visits can send requests to
# 127.0.0.1. Hence: JSON only (a cross-site form can't send JSON without a CORS preflight, which
# this app never grants), the Origin header (if sent) must be this app, and the Host must be
# loopback (blocks DNS-rebinding). Values are validated strictly so nothing can be injected into
# .env, and the connection is tested before anything is saved.
_HUB_URL_RE = re.compile(r'^https?://[A-Za-z0-9.\-]+(:\d{1,5})?(/[A-Za-z0-9._~\-/]*)?$')
_HUB_TOKEN_RE = re.compile(r'^[A-Za-z0-9_\-]{8,200}$')


def _save_env_values(values):
    """Set KEY=value lines in DATA_DIR/.env, keeping every other line; written atomically."""
    path = os.path.join(DATA_DIR, '.env')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = ['# Settings for the IPD practice app (saved by the Settings page).']
    remaining = dict(values)
    for i, line in enumerate(lines):
        key = line.split('=', 1)[0].strip()
        if '=' in line and not line.lstrip().startswith('#') and key in remaining:
            lines[i] = f'{key}={remaining.pop(key)}'
    lines.extend(f'{k}={v}' for k, v in remaining.items())
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    os.replace(tmp, path)


def _settings_write_guard():
    """Only the app's own page may change the connection (see the block comment above). Returns
    an error response to send back, or None when the request may proceed."""
    if request.host.rsplit(':', 1)[0] not in ('127.0.0.1', 'localhost'):
        abort(403)
    origin = request.headers.get('Origin')
    if origin and origin != f'{request.scheme}://{request.host}':
        abort(403)
    if not request.is_json:
        return jsonify({'error': 'Expected JSON.'}), 415
    return None


def _apply_hub_settings(base_url, token):
    """Use these website settings for the rest of this session (saving is separate)."""
    import hub_client
    global HUB_BASE_URL, HUB_API_TOKEN
    os.environ['HUB_BASE_URL'], os.environ['HUB_API_TOKEN'] = base_url, token
    HUB_BASE_URL, HUB_API_TOKEN = base_url, token
    hub_client.configure(base_url, token)


@app.route('/settings', methods=['GET'])
@login_required
def settings_page():
    """Settings tab: the class website connection (and where settings are kept)."""
    import hub_client
    return render_template('settings.html', hub_enabled=hub_client.hub_enabled(),
                           hub_base_url=hub_client.HUB_BASE_URL,
                           hub_has_token=bool(hub_client.HUB_API_TOKEN),
                           data_dir=DATA_DIR)


@app.route('/settings/hub', methods=['GET', 'POST'])
@login_required
@limiter.limit("20 per minute", methods=['POST'])
def hub_settings():
    """GET: connection status (never the token). POST {base_url, token}: test, save, apply.
    A blank token keeps the saved one, so the address can be changed without re-typing it."""
    import hub_client
    if request.method == 'GET':
        return jsonify({'connected': hub_client.hub_enabled() and bool(hub_client.HUB_API_TOKEN),
                        'base_url': hub_client.HUB_BASE_URL})

    refused = _settings_write_guard()
    if refused:
        return refused

    data = request.get_json(silent=True) or {}
    base_url = str(data.get('base_url') or '').strip().rstrip('/')
    token = str(data.get('token') or '').strip() or hub_client.HUB_API_TOKEN
    if len(base_url) > 200 or not _HUB_URL_RE.match(base_url):
        return jsonify({'error': 'Enter the website address in full, e.g. '
                                 'https://your-class-site.pythonanywhere.com'}), 400
    host = base_url.split('://', 1)[1].split('/', 1)[0].split(':', 1)[0]
    if base_url.startswith('http://') and host not in ('127.0.0.1', 'localhost'):
        return jsonify({'error': 'The address must start with https:// (the token must not be '
                                 'sent unencrypted).'}), 400
    if not token:
        return jsonify({'error': 'Enter the class token your instructor gave you.'}), 400
    if not _HUB_TOKEN_RE.match(token):
        return jsonify({'error': 'That token looks wrong: it should be one long run of letters '
                                 'and numbers, exactly as your instructor gave it.'}), 400

    try:
        hub_client.check_connection(base_url, token)
    except hub_client.HubError as e:
        return jsonify({'error': str(e)}), 400

    # The connection was verified, so use it for this session even if saving fails.
    _apply_hub_settings(base_url, token)
    try:
        _save_env_values({'HUB_BASE_URL': base_url, 'HUB_API_TOKEN': token})
    except OSError as e:
        app.logger.error(f"Could not save .env: {e}")
        return jsonify({'error': "Connected, but the settings couldn't be saved (is the settings "
                                 "folder read-only?). They will work until you close the app."}), 500
    return jsonify({'success': True, 'base_url': base_url})


@app.route('/settings/hub/disconnect', methods=['POST'])
@login_required
@limiter.limit("20 per minute")
def hub_settings_disconnect():
    """Forget the class website: clear the saved address and token."""
    refused = _settings_write_guard()
    if refused:
        return refused
    _apply_hub_settings('', '')
    try:
        _save_env_values({'HUB_BASE_URL': '', 'HUB_API_TOKEN': ''})
    except OSError as e:
        app.logger.error(f"Could not save .env: {e}")
        return jsonify({'error': "Disconnected for now, but the change couldn't be saved."}), 500
    return jsonify({'success': True})


@app.route('/hub/list', methods=['GET'])
@login_required
def hub_list():
    """Return both locally cached galleries (no network): player submissions + organizer
    practice strategies. Refresh updates them."""
    return jsonify({'strategies': _public_gallery(_read_strategy_cache()),
                    'practice': _read_practice_cache(), 'cached': True,
                    'source': _read_gallery_source()})


@app.route('/hub/refresh', methods=['POST'])
@login_required
@limiter.limit("30 per minute")
def hub_refresh():
    """The only networked gallery call: fetch from the website and overwrite the local
    cache. On failure (offline / website down) keep the existing cache and report."""
    import hub_client
    data = request.get_json(silent=True) or {}
    saved = _read_gallery_source()
    wanted = data.get('source') or saved.get('source', 'live')
    try:
        if wanted == 'snapshot':
            snapshot_id = str(data.get('snapshot_id') or saved.get('snapshot_id') or '')
            if not snapshot_id:
                return jsonify({'error': 'Pick a snapshot first.'}), 400
            meta, strategies = hub_client.list_snapshot_strategies(snapshot_id)
            source = {'source': 'snapshot', 'snapshot_id': snapshot_id,
                      'label': str(meta.get('label', '')), 'created_at': meta.get('created_at'),
                      'count': len(strategies)}
        else:
            strategies = hub_client.list_strategies()
            source = dict(_LIVE_SOURCE)
    except hub_client.HubError as e:
        return jsonify({'error': str(e), 'strategies': _public_gallery(_read_strategy_cache()),
                        'practice': _read_practice_cache(), 'source': saved}), 502
    # Organizer practice strategies are best-effort (older websites may not serve them).
    try:
        practice = hub_client.list_practice_strategies()
    except hub_client.HubError:
        practice = _read_practice_cache()
    try:
        _write_strategy_cache(strategies)
        _write_practice_cache(practice)
        _write_gallery_source(source)
    except OSError as e:
        app.logger.warning(f"Could not write strategy cache: {e}")
    return jsonify({'strategies': _public_gallery(strategies), 'practice': practice, 'refreshed': True,
                    'source': source})


@app.route('/hub/snapshots', methods=['GET'])
@login_required
@limiter.limit("30 per minute")
def hub_snapshots():
    """Snapshots an admin took on the website, for the gallery source picker."""
    import hub_client
    try:
        return jsonify({'snapshots': hub_client.list_snapshots(), 'source': _read_gallery_source()})
    except hub_client.HubError as e:
        return jsonify({'error': str(e), 'snapshots': [], 'source': _read_gallery_source()}), 502


@app.route('/hub/import/<strategy_id>', methods=['POST'])
@login_required
@limiter.limit("60 per minute")
def hub_import(strategy_id):
    """Resolve a strategy from the local cache and return its code ONLY after the local
    AST screen passes. Untrusted input -> is_safe_code() is the gate before the code is
    delivered to the practice editor. Works offline (no network)."""
    pool = _read_strategy_cache() + _read_practice_cache()
    record = next((s for s in pool if str(s.get('id')) == str(strategy_id)), None)
    code = (record or {}).get('code', '')
    name = (record or {}).get('name', 'imported_strategy')
    if not code:
        return jsonify({'error': 'Strategy not in cache. Press Refresh while online.'}), 404

    # Bound size, then run the same hardened AST screen used for local submissions.
    if len(str(code).encode('utf-8')) > MAX_STRATEGY_CODE_BYTES:
        return jsonify({'error': f'Strategy exceeds the maximum allowed size ({MAX_STRATEGY_CODE_BYTES} bytes).'}), 400
    safe, error = is_safe_code(code)
    if not safe:
        return jsonify({'error': f'Imported strategy rejected by local sandbox: {error}'}), 400

    # Pass through any admin flag so the page can warn before the user runs this strategy.
    reply = {'success': True, 'name': name, 'id': record.get('id'),
             'flagged': bool((record or {}).get('flagged', False)),
             'flag_reason': (record or {}).get('flag_reason', '')}
    # A classmate's code stays hidden: the page gets the name and id, and runs use the id.
    in_practice = any(str(r.get('id')) == str(strategy_id) for r in _read_practice_cache()
                      if isinstance(r, dict))
    if _is_classmate(record) and not in_practice:
        reply['hidden'] = True
    else:
        reply['code'] = code
    return jsonify(reply)


_SMOKE_TEST_OPPONENT = "def AllC(last_moves, my_history, opponents_histories, meta):\n    return 'C'"


@app.route('/hub/screen', methods=['POST'])
@login_required
@limiter.limit("30 per minute")
def hub_screen():
    """Gate a strategy before it can be submitted to the website: AST screen (is_safe_code)
    PLUS a smoke test that actually runs it in a short match. This enforces the rule that a
    strategy must be run locally and pass screening before submission (a deterrent against
    uploading faulty/unscreened code). Returns {ok:true} only when both pass."""
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    code = data.get('code') or ''
    if not name or not code:
        return jsonify({'error': 'Name and code are required.'}), 400
    if len(str(code).encode('utf-8')) > MAX_STRATEGY_CODE_BYTES:
        return jsonify({'error': f'Strategy exceeds the maximum allowed size ({MAX_STRATEGY_CODE_BYTES} bytes).'}), 400
    is_valid, name_err = validate_name(name, field_type="strategy name")
    if not is_valid:
        return jsonify({'error': name_err}), 400
    safe, screen_err = is_safe_code(code)
    if not safe:
        return jsonify({'error': f'Screening failed: {screen_err}'}), 400
    # Smoke test: run the strategy in a short match so faulty code is caught locally before
    # submission. (The entry point is found by signature, not by name — the strategy name is
    # just a display label — so the function need not share the name.)
    try:
        result = game(code, name, _SMOKE_TEST_OPPONENT, 'AllC', rounds=10, mode='standard', seed=1)
    except Exception as e:
        return jsonify({'error': f'Strategy crashed when run locally: {e}'}), 400
    if isinstance(result, dict) and result.get('winner') == 'Error':
        reason = result.get('error') or 'unknown error'
        return jsonify({'error': f'Strategy failed in a test match: {reason}. Make sure your code '
                                 'defines a strategy function with the right signature that returns '
                                 'exactly "C" or "D", then re-run.'}), 400
    return jsonify({'ok': True})


# --- Local personal gallery (save strategies on this machine) ------------------
# The index page calls GET /check-auth on load to decide which save controls to show, and the
# per-strategy "Save locally" button posts to /save-strategy. In the website build these lived
# in ipd-hub; here they are backed by local_gallery.json so they work with no login or network.

@app.route('/check-auth', methods=['GET'])
@login_required
@limiter.limit("60 per minute")
def check_auth():
    """Report login/admin state to the page. In the local app the single owner is always the
    implicit admin (auth_utils stubs), so this is mostly used to show the right save controls."""
    user = session.get('user') or {}
    return jsonify({
        'logged_in': bool(user.get('uid')),
        'is_admin': is_admin(),
        'local_mode': LOCAL_MODE,
    })


@app.route('/save-strategy', methods=['POST'])
@login_required
@limiter.limit("60 per minute")
def save_strategy():
    """Save a strategy to the user's private, machine-local gallery (local_gallery.json).
    Screened with the same name + is_safe_code checks as a tournament entry. De-duped by name
    (re-saving a name overwrites it). Never leaves this machine."""
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    code = data.get('code') or ''

    is_valid, name_err = validate_name(name, field_type="strategy name")
    if not is_valid:
        return jsonify({'error': f'Invalid strategy name: {name_err}'}), 400
    if not code.strip():
        return jsonify({'error': 'Strategy code is required.'}), 400
    if len(code.encode('utf-8')) > MAX_STRATEGY_CODE_BYTES:
        return jsonify({'error': f'Strategy exceeds the maximum allowed size '
                                 f'({MAX_STRATEGY_CODE_BYTES} bytes).'}), 400
    safe, screen_err = is_safe_code(code)
    if not safe:
        return jsonify({'error': f'Strategy failed the safety check: {screen_err}'}), 400

    try:
        gallery = _read_local_gallery()
        gallery = [s for s in gallery if s.get('name') != name]   # overwrite same-named entry
        gallery.append({
            'id': 'local_' + sha256_text(name)[:16],
            'name': name,
            'code': code,
            'type': 'local',
            'source': 'local',
        })
        _write_local_gallery(gallery)
    except OSError as e:
        app.logger.error(f"Could not write local gallery: {e}")
        return jsonify({'error': 'Could not save strategy — disk write failed.'}), 500

    return jsonify({'success': True, 'message': f'Saved "{name}" to your local gallery.'})


@app.route('/local-gallery/list', methods=['GET'])
@login_required
def local_gallery_list():
    """Return the machine-local saved strategies (for the participant checklist / import)."""
    return jsonify({'strategies': _read_local_gallery()})


@app.route('/local-gallery/delete', methods=['POST'])
@login_required
@limiter.limit("60 per minute")
def local_gallery_delete():
    """Remove one strategy from the local gallery by id or name."""
    data = request.get_json(silent=True) or {}
    sid = data.get('id')
    name = data.get('name')
    if not sid and not name:
        return jsonify({'error': 'Provide an id or name to delete.'}), 400
    try:
        gallery = _read_local_gallery()
        kept = [s for s in gallery if s.get('id') != sid and s.get('name') != name]
        _write_local_gallery(kept)
    except OSError as e:
        app.logger.error(f"Could not write local gallery: {e}")
        return jsonify({'error': 'Could not update local gallery — disk write failed.'}), 500
    return jsonify({'success': True, 'removed': len(gallery) - len(kept)})


# Note: there is no local "publish" route. Strategy submission happens on the website
# (log in there and submit) -- the local app is fetch + practice only, with no upload
# path and no credentials. Admins upload tournament results on the website too.


def round_robin_tournament(strategies, rounds=200, payoff_matrix=None, modes=None, discount_factor=0.95, stochastic_prob=0.995, weights=None, seed=None, ranking=None):
    """
    Run a round robin tournament with multiple strategies and game modes
    """
    if modes is None:
        modes = ['standard']
    
    # Create RNG instance if seed provided
    rng = random.Random(seed) if seed is not None else None
    
    # Context to pass to strategies
    # Ensure consistent structure with reserved keys for future N-player extensions
    # Weights passed the old way still count, unless the ranking already names its own.
    if weights and not (isinstance(ranking, dict) and ranking.get('weights')):
        ranking = {**(ranking if isinstance(ranking, dict) else {}), 'weights': weights}
    ranking_spec = normalize_ranking(ranking, '1v1')
    tournament_info = {
        # The weighted-score weights of this run (Sprint 1: all points).
        'weights': ranking_spec['weights'],
        'payoff_matrix': payoff_matrix,
        # Reserved keys for future N-player extensions
        'format': '2-player',
        'n_players': 2,
        'payoff_model': 'standard',
        # How this tournament is ranked (strategies can read it), its modes and match length.
        'ranking': ranking_spec,
        'modes': list(modes),
        'rounds': rounds,
    }

    # --- Step 1: Pre-validation and Filtering ---
    valid_strategies = []
    disqualified_strategies = []

    app.logger.info("Validating strategies before tournament...")
    
    for strategy in strategies:
        safe, error = is_safe_code(strategy['code'])
        if safe:
            valid_strategies.append(strategy)
        else:
            app.logger.warning(f"DISQUALIFIED strategy '{strategy['name']}' ({strategy.get('player_name', 'Unknown')}). Reason: {error}")
            disqualified_strategies.append({
                'name': strategy['name'], 'reason': error, 'error': error, 'stage': 'safety check',
            })
    
    # Update the strategies list to only include valid ones
    strategies = valid_strategies
    
    # Sort strategies for stable ordering (by name, then user_id if present)
    strategies = sorted(strategies, key=lambda s: (s['name'], s.get('user_id', '')))
    
    if len(strategies) < 2:
        n_dq = len(disqualified_strategies)
        error_msg = (f"Not enough valid strategies to run the tournament: {n_dq} "
                     f"{'was' if n_dq == 1 else 'were'} disqualified (see below for which and why).")
        app.logger.error(error_msg)
        return {
            'leaderboard': [],
            'matches': [],
            'total_matches': 0,
            'error': error_msg,
            'disqualified': disqualified_strategies
        }

    # Generate random round count for 'random' mode if applicable
    # Use RNG instance if available
    if rng:
        fixed_random_rounds = rng.randint(100, 300)
    else:
        fixed_random_rounds = randint(100, 300)

    # Generate all possible pairs
    strategy_pairs = list(combinations(strategies, 2))
    # Strategies that failed during a match (crash, instruction cap, a move other than 'C'/'D').
    # name -> {name, reason, stage, opponent, mode}. A failed strategy is DISQUALIFIED: it is
    # removed from the leaderboard and every match it played is thrown out, so nobody gains or
    # loses points from having met it.
    runtime_dq = {}

    app.logger.info(f"=== Round Robin Tournament: {len(strategies)} strategies, {len(strategy_pairs)} matches ===")
    app.logger.info(f"Modes: {modes}")

    # --- Step 2: play every pairing (a strategy stops playing once it has been disqualified) ---
    played = []   # (strategy_a, strategy_b, [match_result per mode])
    for i, (strategy_a, strategy_b) in enumerate(strategy_pairs):
        if strategy_a['name'] in runtime_dq or strategy_b['name'] in runtime_dq:
            continue   # would be thrown out anyway
        app.logger.info(f"Match {i+1}/{len(strategy_pairs)}: {strategy_a['name']} vs {strategy_b['name']}")
        pair_mode_results = []
        for mode_idx, mode in enumerate(modes):
            # Deterministic sub-seed for this match if a main seed was given
            match_seed = None
            if seed is not None:
                match_seed = seed + i * 1000 + mode_idx
            match_result = game(
                strategy_a['code'], strategy_a['name'], strategy_b['code'], strategy_b['name'],
                rounds, payoff_matrix, mode=mode, fixed_random_rounds=fixed_random_rounds,
                discount_factor=discount_factor, stochastic_prob=stochastic_prob,
                tournament_info=tournament_info, seed=match_seed,
            )
            pair_mode_results.append(match_result)
            if match_result.get('error'):
                break   # no point playing the other modes of a pair that is void
        for r in pair_mode_results:
            if r.get('error'):
                who = r.get('error_player')
                culprits = {'A': [strategy_a], 'B': [strategy_b]}.get(who, [strategy_a, strategy_b])
                for culprit, opponent in ((c, strategy_b if c is strategy_a else strategy_a) for c in culprits):
                    runtime_dq.setdefault(culprit['name'], {
                        'name': culprit['name'], 'reason': r['error'], 'error': r['error'],
                        'stage': 'during the run', 'opponent': opponent['name'],
                        'mode': r.get('mode'),
                    })
        played.append((strategy_a, strategy_b, pair_mode_results))

    def _void_record(strategy_a, strategy_b, pair_mode_results):
        # A match that involved a disqualified strategy. Kept so the page can show what went
        # wrong (round, error, partial score), but it is not counted for anyone.
        n = max(len(pair_mode_results), 1)
        rec = {
            'player_a': strategy_a['name'],
            'player_b': strategy_b['name'],
            'a_points': sum(r['a_points'] for r in pair_mode_results) / n,
            'b_points': sum(r['b_points'] for r in pair_mode_results) / n,
            'winner': 'Void',
            'void': True,
            'mode_results': pair_mode_results,
        }
        for r in pair_mode_results:
            if r.get('error'):
                rec['error'] = r['error']
                rec['error_type'] = r.get('error_type', 'Exception')
                rec['error_player'] = r.get('error_player', 'Unknown')
                rec['terminated_early'] = r.get('terminated_early', True)
                break
        return rec

    void_matches = [_void_record(a, b, pm) for a, b, pm in played
                    if a['name'] in runtime_dq or b['name'] in runtime_dq]

    strategies = [s for s in strategies if s['name'] not in runtime_dq]
    disqualified_strategies = disqualified_strategies + list(runtime_dq.values())
    if len(strategies) < 2:
        n_dq = len(disqualified_strategies)
        error_msg = (f"Not enough strategies left to rank: {n_dq} "
                     f"{'was' if n_dq == 1 else 'were'} disqualified (see below for which and why).")
        app.logger.error(error_msg)
        return {'leaderboard': [], 'matches': void_matches, 'total_matches': 0, 'error': error_msg,
                'disqualified': disqualified_strategies,
                'runtime_errors': list(runtime_dq.values())}

    # --- Step 3: score only the matches between strategies that were not disqualified ---
    results = []
    leaderboard = {strategy['name']: {
        'total_points': 0,
        'total_raw_points': 0,
        'wins': 0,
        'draws': 0,
        'losses': 0,
        'cooperates': 0,
        'defects': 0,
        'total_moves': 0,
        'mode_points': {mode: 0 for mode in modes},
        'mode_raw_points': {mode: 0 for mode in modes},
        'mode_stats': {mode: {'cooperates': 0, 'defects': 0, 'total_moves': 0} for mode in modes}
    } for strategy in strategies}
    submitted_at = {s['name']: s.get('submitted_at') for s in strategies}

    for strategy_a, strategy_b, pair_mode_results in played:
        if strategy_a['name'] in runtime_dq or strategy_b['name'] in runtime_dq:
            continue
        A, B = leaderboard[strategy_a['name']], leaderboard[strategy_b['name']]
        pair_a_points = pair_b_points = 0
        for mode, match_result in zip(modes, pair_mode_results):
            pair_a_points += match_result['a_points']
            pair_b_points += match_result['b_points']
            for side, prefix in ((A, 'a'), (B, 'b')):
                side['mode_points'][mode] += match_result[f'{prefix}_points']
                side['mode_raw_points'][mode] += match_result[f'{prefix}_total_points']
                side['total_raw_points'] += match_result[f'{prefix}_total_points']
                side['cooperates'] += match_result[f'{prefix}_coops']
                side['mode_stats'][mode]['cooperates'] += match_result[f'{prefix}_coops']
                side['defects'] += match_result[f'{prefix}_defects']
                side['mode_stats'][mode]['defects'] += match_result[f'{prefix}_defects']
                side['total_moves'] += match_result['rounds']
                side['mode_stats'][mode]['total_moves'] += match_result['rounds']

        # Average score for the pair across all modes
        avg_a_points = pair_a_points / len(modes)
        avg_b_points = pair_b_points / len(modes)
        A['total_points'] += avg_a_points
        B['total_points'] += avg_b_points

        if avg_a_points > avg_b_points:
            pair_winner = 'A'
        elif avg_a_points < avg_b_points:
            pair_winner = 'B'
        else:
            pair_winner = 'Draw'
        if pair_winner == 'A':
            A['wins'] += 1
            B['losses'] += 1
        elif pair_winner == 'B':
            B['wins'] += 1
            A['losses'] += 1
        else:
            A['draws'] += 1
            B['draws'] += 1

        results.append({
            'player_a': strategy_a['name'],
            'player_b': strategy_b['name'],
            'a_points': avg_a_points,
            'b_points': avg_b_points,
            'winner': pair_winner,
            'mode_results': pair_mode_results,
        })

    # Calculate percentages and prepare leaderboard
    # Max average point per round is max payoff (e.g. 5)
    if payoff_matrix:
        max_round_points = max([points[0] for points in payoff_matrix.values()])
    else:
        max_round_points = 5
        
    # Max possible points is simply max_round_points * (number of matches played per strategy)
    # Since we sum averages, the max score per match is max_round_points.
    matches_per_strategy = len(strategies) - 1
    max_possible_points = max_round_points * matches_per_strategy
    
    # Add calculated percentages to leaderboard
    enhanced_leaderboard = []
    matches_per_strategy = len(strategies) - 1
    
    for name, stats in leaderboard.items():
        # Raw percentage (historical truth)
        cooperation_percentage = (stats['cooperates'] / stats['total_moves'] * 100) if stats['total_moves'] > 0 else 0
        
        # Points percentage (fraction of theoretical max)
        # New Formula: (Avg Points Per Round / Max Possible Points Per Round) * 100
        # Avg Points Per Round = total_raw_points / total_moves
        if stats['total_moves'] > 0 and max_round_points > 0:
            avg_points_per_round = stats['total_raw_points'] / stats['total_moves']
            points_percentage = (avg_points_per_round / max_round_points) * 100
        else:
            points_percentage = 0
        
        # Calculate average mode points (points per match in that mode)
        avg_mode_points = {m: (pts / matches_per_strategy if matches_per_strategy > 0 else 0.0) for m, pts in stats['mode_points'].items()}
        
        # Calculate per-mode stats with percentages AND normalization
        enriched_mode_stats = {}
        normalized_coops = 0
        normalized_defects = 0
        normalized_total_moves = 0
        target_mode_volume = matches_per_strategy * rounds # The standard volume if every game was fixed rounds
        
        for mode, mode_data in stats['mode_stats'].items():
            mode_total_moves = mode_data['total_moves']
            
            if mode_total_moves > 0:
                mode_coop_pct = (mode_data['cooperates'] / mode_total_moves * 100)
                
                # Normalization: Project what counts would be if this mode ran for 'rounds' length
                # Formula: (Actual Coops / Actual Moves) * (Matches * Standard Rounds)
                coop_ratio = mode_data['cooperates'] / mode_total_moves
                defect_ratio = mode_data['defects'] / mode_total_moves
                
                mode_norm_coops = coop_ratio * target_mode_volume
                mode_norm_defects = defect_ratio * target_mode_volume
                
                normalized_coops += mode_norm_coops
                normalized_defects += mode_norm_defects
                normalized_total_moves += target_mode_volume
            else:
                mode_coop_pct = 0
                mode_norm_coops = 0
                mode_norm_defects = 0
                
            enriched_mode_stats[mode] = {
                'cooperates': mode_data['cooperates'],
                'defects': mode_data['defects'],
                'norm_cooperates': int(round(mode_norm_coops)),
                'norm_defects': int(round(mode_norm_defects)),
                'total_moves': mode_total_moves,
                'cooperation_percentage': mode_coop_pct
            }

        # Calculate Normalized Cooperation Percentage
        norm_coop_percentage = (normalized_coops / normalized_total_moves * 100) if normalized_total_moves > 0 else 0

        enhanced_entry = {
            'name': name,
            'total_points': stats['total_points'],
            'total_raw_points': stats['total_raw_points'],
            'mode_points': avg_mode_points,
            'mode_raw_points': stats['mode_raw_points'],
            'mode_stats': enriched_mode_stats,
            'wins': stats['wins'],
            'draws': stats['draws'],
            'losses': stats['losses'],
            'cooperates': stats['cooperates'],
            'defects': stats['defects'],
            'total_moves': stats['total_moves'],
            'normalized_cooperates': int(round(normalized_coops)),
            'normalized_defects': int(round(normalized_defects)),
            'cooperation_percentage': cooperation_percentage,         # Actual historical %
            'norm_cooperation_percentage': norm_coop_percentage,      # Normalized % for scoring
            'points_percentage': points_percentage,
            'weighted_score': stats['total_points'],  # Default to points for initial sort
            'submitted_at': submitted_at.get(name),
        }
        enhanced_leaderboard.append(enhanced_entry)

    # Order by the chosen ranking (default: the itinerary's "highest total points wins").
    ranking = ranking_spec
    sorted_leaderboard = rank_entries(enhanced_leaderboard, ranking, '1v1')

    return {
        'leaderboard': sorted_leaderboard,
        # Counted matches first, then the void ones (shown greyed out, "not counted").
        'matches': results + void_matches,
        'total_matches': len(results),
        'ranking': ranking,
        'ranking_text': describe_ranking(ranking, '1v1'),
        # Every strategy left out of the ranking, with why: safety check or failed during the run.
        'disqualified': disqualified_strategies,
        'runtime_errors': list(runtime_dq.values()),
    }


@app.route('/play', methods=['POST'])
@login_required
@limiter.limit("20 per minute")
def play_game():
    data = request.json
    # Basic play route uses standard mode by default
    result = game(
        data['player_a_code'],
        data['player_a_name'],
        data['player_b_code'],
        data['player_b_name'],
        int(data.get('rounds', 200)),
        data.get('payoff_matrix', None),
        mode='standard'
    )
    return jsonify(result)


@app.route('/tournament', methods=['POST'])
@login_required
@limiter.limit("10 per minute")
def run_tournament():
    # Helper for running via simple POST (if used externally)
    data = request.json

    strategies = data.get('strategies', [])
    strategies, resolve_error = _resolve_website_strategies(strategies)
    if resolve_error:
        return jsonify({'error': resolve_error}), 400
    rounds = int(data.get('rounds', 200))
    payoff_matrix = data.get('payoff_matrix', None)

    # Validate strategies list
    if not isinstance(strategies, list):
        return jsonify({'error': 'Strategies must be a list'}), 400

    if len(strategies) < 2:
        return jsonify({'error': 'At least 2 strategies required for tournament'}), 400

    # No strategy/round caps on the local app — it's the user's own machine (the UI shows a
    # pre-run time estimate). Keep the lower bound only.
    if rounds < 1:
        return jsonify({'error': 'Rounds must be at least 1'}), 400

    # Validate strategy names
    for strategy in strategies:
        strategy_name = strategy.get('name', '')
        is_valid, error_message = validate_name(strategy_name, field_type="strategy name")
        if not is_valid:
            return jsonify({'error': f'Invalid strategy name: {error_message}'}), 400

    # Names are the leaderboard key, so duplicates would silently collapse into one row.
    is_unique, dupe_error = check_unique_strategy_names(strategies)
    if not is_unique:
        return jsonify({'error': dupe_error}), 400

    # Optional sandbox mode (isolated screening): run each strategy in a separate process with a wall-clock
    # timeout first, so one that hangs (e.g. ReDoS in C) or crashes is flagged and excluded
    # rather than stalling the whole tournament. Opt-in via `isolated` ("Run in sandbox mode").
    screen_flags = []
    isolated = _is_truthy(data.get('isolated'))
    if isolated:
        import isolation
        survivors, screen_flags = isolation.screen_strategies(strategies)
        if len(survivors) < 2:
            return _screening_shortfall(screen_flags)
        strategies = survivors

    # Game modes (1v1 supports the same modes as N-player). Accept a `modes` list and
    # optional discount/stochastic params, validated the same way as /nplayer/tournament.
    allowed_modes = {'standard', 'discounted', 'stochastic', 'random'}
    modes = data.get('modes', None)
    if modes is None:
        modes = ['standard']
    else:
        if not isinstance(modes, list) or any(not isinstance(m, str) for m in modes):
            return jsonify({'error': 'modes must be a list of strings'}), 400
        if len(modes) == 0:
            return jsonify({'error': 'modes must not be empty'}), 400
        if len(set(modes)) != len(modes):
            return jsonify({'error': 'modes must not contain duplicates'}), 400
        if any(m not in allowed_modes for m in modes):
            return jsonify({'error': f"Invalid modes. Allowed: {sorted(allowed_modes)}"}), 400

    try:
        raw_discount_factor = data.get('discount_factor', None)
        raw_stochastic_prob = data.get('stochastic_prob', None)
        discount_factor = 0.95 if raw_discount_factor is None else float(raw_discount_factor)
        stochastic_prob = 0.995 if raw_stochastic_prob is None else float(raw_stochastic_prob)
    except (TypeError, ValueError):
        return jsonify({'error': 'discount_factor and stochastic_prob must be numbers'}), 400
    if not (0.0 < discount_factor <= 1.0):
        return jsonify({'error': 'discount_factor must be in (0, 1]'}), 400
    if not (0.0 < stochastic_prob <= 1.0):
        return jsonify({'error': 'stochastic_prob must be in (0, 1]'}), 400

    # Optional seed for reproducible practice runs.
    raw_seed = data.get('seed', None)
    try:
        seed = int(raw_seed) if raw_seed not in (None, '') else None
    except (TypeError, ValueError):
        return jsonify({'error': 'seed must be an integer'}), 400

    try:
        tournament_result = round_robin_tournament(
            strategies, rounds, payoff_matrix, modes=modes,
            discount_factor=discount_factor, stochastic_prob=stochastic_prob,
            seed=seed, ranking=data.get('ranking'),
        )
        if isolated:
            tournament_result['flags'] = screen_flags
            tournament_result['screened'] = True
        return jsonify(tournament_result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/nplayer/tournament', methods=['POST'])
@login_required
@limiter.limit("10 per minute")
def nplayer_tournament():
    """
    Run N-player tournament with configurable payoff model.
    
    Request JSON:
    {
        "strategies": [{"name": "A", "code": "..."}],
        "rounds": 200,
        "seed": 42,
        "group_size": 5,  # optional, defaults to all
        "payoff_model": {
            "type": "public_goods",  # or "pairwise_matrix"
            "b": 2.0,
            "c": 1.0,
            "nonlinear": {"type": "linear"}
        },
        "weights": {
            "cooperation": 0.3,
            "win_rate": 0.4,
            "points": 0.3
        }
    }
    
    Response: Same structure as existing /tournament endpoint
    """
    # CRITICAL Issue #1: Guard against invalid/missing JSON body
    if request.json is None:
        return jsonify({'error': 'Invalid or missing JSON body'}), 400
    
    data = request.json
    
    # Per-match round cap (the only N-player limit still enforced — see below). No strategy-count
    # cap: the tournament runs on the user's own machine.
    try:
        MAX_NPLAYER_ROUNDS = int(os.getenv('MAX_NPLAYER_ROUNDS', '1000'))
    except (ValueError, TypeError):
        MAX_NPLAYER_ROUNDS = 1000

    user_is_admin = is_admin()
    
    strategies = data.get('strategies', [])
    strategies, resolve_error = _resolve_website_strategies(strategies)
    if resolve_error:
        return jsonify({'error': resolve_error}), 400
    rounds = int(data.get('rounds', 200))
    seed = data.get('seed', None)
    group_size = data.get('group_size', None)
    payoff_model_config = data.get('payoff_model', None)
    weights = data.get('weights', None)
    mode = data.get('mode', 'standard')
    modes = data.get('modes', None)

    # Optional mode parameters for N-player
    try:
        raw_discount_factor = data.get('discount_factor', None)
        raw_stochastic_prob = data.get('stochastic_prob', None)
        # Treat explicit null as missing (use defaults)
        discount_factor = 0.95 if raw_discount_factor is None else float(raw_discount_factor)
        stochastic_prob = 0.995 if raw_stochastic_prob is None else float(raw_stochastic_prob)
    except (TypeError, ValueError):
        return jsonify({'error': 'discount_factor and stochastic_prob must be numbers'}), 400

    if not (0.0 < discount_factor <= 1.0):
        return jsonify({'error': 'discount_factor must be in (0, 1]'}), 400
    if not (0.0 < stochastic_prob <= 1.0):
        return jsonify({'error': 'stochastic_prob must be in (0, 1]'}), 400

    allowed_modes = {'standard', 'discounted', 'stochastic', 'random'}
    if modes is not None:
        if not isinstance(modes, list) or any(not isinstance(m, str) for m in modes):
            return jsonify({'error': 'modes must be a list of strings'}), 400
        if len(modes) == 0:
            return jsonify({'error': 'modes must not be empty'}), 400
        if len(set(modes)) != len(modes):
            return jsonify({'error': 'modes must not contain duplicates'}), 400
        if any(m not in allowed_modes for m in modes):
            return jsonify({'error': f"Invalid modes. Allowed: {sorted(allowed_modes)}"}), 400
    else:
        if not isinstance(mode, str):
            return jsonify({'error': 'mode must be a string'}), 400
        if mode not in allowed_modes:
            return jsonify({'error': f"Invalid mode. Allowed: {sorted(allowed_modes)}"}), 400
    
    # CRITICAL Issue #2: Validate group_size
    if group_size is not None:
        # Check if it's an integer
        if not isinstance(group_size, int):
            return jsonify({'error': 'group_size must be an integer or null'}), 400
        # Check if it's positive
        if group_size < 1:
            return jsonify({'error': 'group_size must be at least 1'}), 400
    
    # Validate strategies list
    if not isinstance(strategies, list):
        return jsonify({'error': 'Strategies must be a list'}), 400
    
    if len(strategies) < 1:
        return jsonify({'error': 'At least 1 strategy required for N-player tournament'}), 400

    # No strategy-count cap on the local app (it runs on the user's own machine, like 1v1).

    # A group can't be larger than the number of strategies available to fill it.
    if group_size is not None and group_size > len(strategies):
        return jsonify({'error': f'group_size ({group_size}) cannot exceed the number of strategies ({len(strategies)}).'}), 400

    # Validate rounds
    if rounds < 1:
        return jsonify({'error': 'Rounds must be at least 1'}), 400
    
    if rounds > MAX_NPLAYER_ROUNDS:
        return jsonify({'error': f'N-player mode limited to {MAX_NPLAYER_ROUNDS} rounds per match.'}), 400
    
    # Validate strategy names
    for strategy in strategies:
        strategy_name = strategy.get('name', '')
        is_valid, error_message = validate_name(strategy_name, field_type="strategy name")
        if not is_valid:
            return jsonify({'error': f'Invalid strategy name: {error_message}'}), 400

    # Names are the leaderboard key, so duplicates would silently collapse into one row.
    is_unique, dupe_error = check_unique_strategy_names(strategies)
    if not is_unique:
        return jsonify({'error': dupe_error}), 400

    # Optional sandbox mode (opt-in via `isolated`): exclude + flag strategies that hang or
    # crash in a separate process before the real run (see run_tournament for the rationale).
    screen_flags = []
    isolated = _is_truthy(data.get('isolated'))
    if isolated:
        import isolation
        survivors, screen_flags = isolation.screen_strategies(strategies)
        if len(survivors) < 2:
            return _screening_shortfall(screen_flags)
        strategies = survivors

    # Parse and validate strategy code
    compiled_strategies = []
    for strategy in strategies:
        code = strategy.get('code', '')
        name = strategy.get('name', 'Unknown')
        
        # Validate code safety
        safe, error = is_safe_code(code)
        if not safe:
            return jsonify({'error': f'Strategy "{name}" failed safety check: {error}'}), 400
        
        # Compile strategy function
        try:
            # Create RNG for determinism if seed provided
            rng = random.Random(seed) if seed is not None else None
            
            # MAJOR Issue #3: Inject TOURNAMENT_INFO context for N-player strategies
            tournament_info = {
                'weights': weights,
                'payoff_model': payoff_model_config.get('type') if payoff_model_config else 'pairwise_matrix',
                'format': 'n-player',
                'n_players': len(strategies),
                'group_size': group_size,
                'mode': mode,
                'modes': modes if modes is not None else [mode],
                'discount_factor': discount_factor,
                'stochastic_prob': stochastic_prob,
            }
            exec_context = {'TOURNAMENT_INFO': tournament_info}
            
            globals_dict = get_safe_globals(exec_context, rng)
            
            # MAJOR Issue #5: Module-level exec() for strategy definition
            # Note: exec() here defines functions at module scope; actual strategy
            # execution is limited by run_with_limit() in game logic. Static code
            # analysis via is_safe_code() prevents obvious infinite loops.
            exec(code, globals_dict)
            
            # MAJOR Issue #4: Prefer 4-arg functions for N-player strategies
            strategy_func = extract_strategy_function(code, globals_dict, prefer_n_args=4)
            
            if not callable(strategy_func):
                return jsonify({'error': f'Strategy "{name}" does not define a callable function'}), 400
            
            compiled_strategies.append({
                'name': name,
                'code': code,
                'func': strategy_func,
                'submitted_at': strategy.get('submitted_at'),
            })
        except Exception as e:
            return jsonify({'error': f'Failed to compile strategy "{name}": {str(e)}'}), 400
    
    # Parse payoff model
    try:
        payoff_model = parse_payoff_model(payoff_model_config)
    except Exception as e:
        return jsonify({'error': f'Invalid payoff model configuration: {str(e)}'}), 400
    
    # Persistence is a website feature; the local app has no Firebase, so never persist
    # here. Results are produced for in-browser display only (admins publish official
    # runs via the website's upload page). Forced off so the stubbed db is never touched.
    persist = data.get('persist', False)
    should_persist = False
    
    # Run tournament
    try:
        result = group_tournament(
            strategies=compiled_strategies,
            rounds=rounds,
            group_size=group_size,
            seed=seed,
            payoff_model=payoff_model,
            weights=weights,
            mode=mode,
            modes=modes,
            discount_factor=discount_factor,
            stochastic_prob=stochastic_prob,
            ranking=data.get('ranking'),
        )
        
        # If admin requested persistence, save to Firestore
        if should_persist:
            from firebase_admin import firestore
            from firebase_config import db
            from firestore_utils import to_firestore_safe
            from tournament_package import sha256_text
            import uuid
            from datetime import datetime, timezone
            
            # Generate tournament ID
            tournament_id = str(uuid.uuid4())
            
            # Build seed_info for reproducibility
            seed_info = None
            if seed is not None:
                seed_info = {
                    'tournament_seed': seed,
                    'derived_seeds': {}
                }
                # Add derived seeds if multiple groups were run
                tournament_info = result.get('tournament_info', {})
                n_groups = tournament_info.get('n_groups', 1)
                for i in range(n_groups):
                    seed_info['derived_seeds'][f'group_{i}'] = seed + i * 10000 if seed is not None else None
            
            # Build participants list with code hashes
            participants = []
            for strat in compiled_strategies:
                code = strat['code']
                code_sha256 = sha256_text(code)
                
                participants.append({
                    'name': strat['name'],
                    'code': code,
                    'code_sha256': code_sha256,
                    'user_id': session['user']['uid'],  # Admin who ran it
                    'player_email': session['user'].get('email', ''),
                    'player_name': session['user'].get('displayName', '')
                })
            
            # Build tournament document
            tournament_doc = {
                'name': f"N-Player Tournament {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}",
                'format': 'n-player',
                'created_at': firestore.SERVER_TIMESTAMP,
                'rounds': rounds,
                'group_size': group_size,
                'seed': seed,
                'seed_info': seed_info,
                'payoff_model': payoff_model_config,
                'weights': weights,
                'participants': participants,
                'results': result.get('leaderboard', []),
                'leaderboard': result.get('leaderboard', []),
                'matches': result.get('matches', []),
                'tournament_info': result.get('tournament_info', {}),
                'winner': result['leaderboard'][0]['name'] if result.get('leaderboard') else '',
                'participant_count': len(compiled_strategies),
                'total_matches': result.get('tournament_info', {}).get('n_groups', 1),
                'run_by': session['user']['uid'],
                'run_by_email': session['user'].get('email', ''),
                'package_inputs_hash': sha256_text(json.dumps({
                    'strategies': [s['name'] for s in compiled_strategies],
                    'rounds': rounds,
                    'group_size': group_size,
                    'seed': seed,
                    'payoff_model': payoff_model_config,
                    'weights': weights,
                    'mode': mode,
                    'modes': modes,
                    'discount_factor': discount_factor,
                    'stochastic_prob': stochastic_prob,
                }, sort_keys=True))
            }
            
            # Save to Firestore
            db.collection('nplayer_tournament_results').document(tournament_id).set(to_firestore_safe(tournament_doc))
            
            # Add tournament_id to response
            result['tournament_id'] = tournament_id
            app.logger.info(f"N-player tournament persisted with ID: {tournament_id}")

        if isolated:
            result['flags'] = screen_flags
            result['screened'] = True
        return jsonify(result)
    except Exception as e:
        app.logger.error(f"N-player tournament error: {str(e)}")
        return jsonify({'error': str(e)}), 500


def parse_payoff_model(config):
    """
    Parse payoff model configuration and return PayoffModel instance.
    
    Args:
        config: Dict with payoff model config or None
    
    Returns:
        PayoffModel instance
    """
    if config is None:
        # Default to pairwise with standard PD matrix
        return PairwiseMatrixPayoff(
            payoff_matrix={'CC': [3, 3], 'CD': [0, 5], 'DC': [5, 0], 'DD': [1, 1]},
            aggregate='sum'
        )
    
    model_type = config.get('type', 'pairwise_matrix')
    
    if model_type == 'public_goods':
        b = config.get('b', 2.0)
        c = config.get('c', 1.0)
        nonlinear = config.get('nonlinear', None)
        # Allow shorthand: nonlinear can be provided as a string (e.g. 'linear'/'power')
        # Normalize to the dict shape expected by PublicGoodsPayoff.
        if isinstance(nonlinear, str):
            nonlinear = {'type': nonlinear}
        # Backward/compat wiring: allow top-level alpha to feed into nonlinear config
        # when using a power benefit function.
        if isinstance(nonlinear, dict):
            if nonlinear.get('type') == 'power' and 'alpha' not in nonlinear and 'alpha' in config:
                nonlinear['alpha'] = config.get('alpha')
        return PublicGoodsPayoff(b=b, c=c, nonlinear=nonlinear)
    
    elif model_type == 'pairwise_matrix':
        payoff_matrix = config.get('payoff_matrix', None)
        if payoff_matrix is None:
            # Use default PD matrix
            payoff_matrix = {'CC': [3, 3], 'CD': [0, 5], 'DC': [5, 0], 'DD': [1, 1]}
        aggregate = config.get('aggregate', 'sum')
        return PairwiseMatrixPayoff(payoff_matrix=payoff_matrix, aggregate=aggregate)

    elif model_type == 'kcoop_tensor':
        u_C = config.get('u_C', None)
        u_D = config.get('u_D', None)
        if u_C is None or u_D is None:
            raise ValueError("kcoop_tensor payoff model requires both 'u_C' and 'u_D' arrays")
        return KCooperatorTensorPayoff(u_C=u_C, u_D=u_D)

    else:
        raise ValueError(f"Unknown payoff model type: {model_type}")


# Admin API Routes
# ... (Admin routes) ...



# Admin API Routes



def _run_core_simulation(strategies, *, num_cores, scheduler, core_assignment_mode,
                         core_sim_seed, unlimited=False, runs=1, num_processes=50, warmup=0.0,
                         ranking=None):
    """Run the OS simulation on exactly the supplied strategies, returning result/config blocks.

    No screening / top-K selection: the user's submitted strategies *are* the pool.
    Homogeneous benchmarks each strategy on all N cores; heterogeneous enumerates every
    `combinations(strategies, num_cores)` mixture (each strategy at most once) and ranks by
    throughput.
    """
    from core_simulation import run_full_simulation, run_heterogeneous_simulation

    core_simulation_results = None
    core_simulation_heterogeneous = None
    pool = [{'name': s['name'], 'code': s['code'], 'submitted_at': s.get('submitted_at')}
            for s in strategies]
    try:
        if core_assignment_mode == 'heterogeneous':
            core_simulation_heterogeneous = run_heterogeneous_simulation(
                pool, num_cores=num_cores, seed=core_sim_seed, scheduler=scheduler,
                max_combinations=MAX_COMBINATIONS,
                time_budget_s=OS_SIM_TIME_BUDGET_S, unlimited=unlimited,
                runs=runs, num_processes=num_processes, warmup=warmup, ranking=ranking)
        else:
            core_simulation_results = run_full_simulation(
                pool, num_cores=num_cores, seed=core_sim_seed, scheduler=scheduler,
                runs=runs, num_processes=num_processes, warmup=warmup, ranking=ranking)
    except Exception as sim_error:
        app.logger.error(f"Core Simulation Error: {sim_error}")
        core_simulation_results = {'error': str(sim_error)}

    # Bounded per-core traces for the Gantt visual: one timeline per (layout × workload) so the
    # results-page "Workload dataset" dropdown can switch the timeline between datasets (not just
    # Mixed). Each layout = a submitted strategy (homogeneous) or a ranked mixture (heterogeneous).
    # Capped on layouts and tick length to keep the persisted document small.
    from core_simulation import WORKLOAD_PROFILES
    TRACE_LAYOUT_CAP = 6          # layouts traced (× 4 workloads each)
    TRACE_MAX_TICKS = 160         # per-trace tick cap (4× traces, so a touch shorter)
    traces = []
    trace_seed = ((core_simulation_results or {}).get('seed')
                  or (core_simulation_heterogeneous or {}).get('seed') or core_sim_seed)
    try:
        from core_simulation import build_core_trace, extract_strategy_func
        code_by_name = {s['name']: s['code'] for s in pool}

        def _layout_traces(layout, label):
            """One trace per workload profile for a given core layout."""
            named_funcs = [(n, extract_strategy_func(code_by_name.get(n, ''), seed=trace_seed))
                           for n in layout]
            if not named_funcs or not all(f for _, f in named_funcs):
                return []
            out = []
            for wl in WORKLOAD_PROFILES:
                tr = build_core_trace(named_funcs, num_cores=num_cores, seed=trace_seed,
                                      scheduler=scheduler, workload=wl, max_ticks=TRACE_MAX_TICKS)
                if tr is not None:
                    tr['label'] = label          # which strategy / mixture
                    tr['workload'] = wl           # which dataset (drives the dropdown)
                    out.append(tr)
            return out

        if core_assignment_mode == 'heterogeneous' and core_simulation_heterogeneous \
                and core_simulation_heterogeneous.get('results'):
            for combo in core_simulation_heterogeneous['results'][:TRACE_LAYOUT_CAP]:
                layout = [d['strategy_name'] for d in combo['assignment_details']]
                traces.extend(_layout_traces(layout, ' + '.join(layout)))
        else:
            # Trace the top-ranked strategies; disqualified ones have no timeline.
            ranked_names = (core_simulation_results or {}).get('order') or []
            for name in ranked_names[:TRACE_LAYOUT_CAP]:
                traces.extend(_layout_traces([name] * num_cores, name))
    except Exception as trace_error:
        app.logger.error(f"Core trace error: {trace_error}")

    _dq = list(((core_simulation_results or {}).get('errors') or [])
               + ((core_simulation_heterogeneous or {}).get('errors') or []))
    _dq_names = {d['name'] for d in _dq}
    core_simulation_config = {
        'simulate_cores': True,
        'num_cores': num_cores,
        'scheduler': scheduler,
        'core_assignment_mode': core_assignment_mode,
        # Only the strategies still in the ranking; disqualified ones are listed separately.
        'selected_strategies': [s['name'] for s in pool if s['name'] not in _dq_names],
        'disqualified': _dq,
        'runs': runs,
        'num_processes': num_processes,
        'warmup': warmup,
    }
    if traces:
        core_simulation_config['traces'] = traces
        core_simulation_config['trace'] = traces[0]  # back-compat single-trace field

    return {
        'core_simulation_results': core_simulation_results,
        'core_simulation_heterogeneous': core_simulation_heterogeneous,
        'core_simulation_config': core_simulation_config,
    }


def run_standalone_core_simulation(strategies, *, num_cores, scheduler,
                                   core_assignment_mode, seed, unlimited=False,
                                   runs=1, num_processes=50, warmup=0.0, ranking=None):
    """Run the OS core simulation on its own (no tournament needed).

    Runs on exactly the supplied strategies — no screening, no top-K. Returns the three
    `core_simulation_*` blocks (the shape the results renderer expects).
    """
    return _run_core_simulation(
        strategies, num_cores=num_cores, scheduler=scheduler,
        core_assignment_mode=core_assignment_mode, core_sim_seed=seed, unlimited=unlimited,
        runs=runs, num_processes=num_processes, warmup=warmup, ranking=ranking)


@app.route('/os-simulation', methods=['POST'])
@login_required
@limiter.limit("10 per minute")
def os_simulation():
    """Standalone OS core simulation: select strategies + cores/scheduler/top-K and run.

    Independent of the tournament flows. Returns core_simulation_results,
    core_simulation_heterogeneous and core_simulation_config.
    """
    data = request.json or {}

    # No strategy-count cap on the local app (it runs on the user's own machine, like 1v1).
    # A heterogeneous cores×assignment blow-up is bounded by OS_SIM_TIME_BUDGET_S at run time
    # rather than refused up front, unless the caller opted out via `unlimited`.
    strategies = data.get('strategies', [])
    strategies, resolve_error = _resolve_website_strategies(strategies)
    if resolve_error:
        return jsonify({'error': resolve_error}), 400
    if not isinstance(strategies, list):
        return jsonify({'error': 'Strategies must be a list'}), 400
    if len(strategies) < 2:
        return jsonify({'error': 'At least 2 strategies required for OS simulation'}), 400

    # Validate names + code safety; normalise to {name, code}.
    clean = []
    for strategy in strategies:
        name = strategy.get('name', '') if isinstance(strategy, dict) else ''
        code = strategy.get('code', '') if isinstance(strategy, dict) else ''
        is_valid, error_message = validate_name(name, field_type="strategy name")
        if not is_valid:
            return jsonify({'error': f'Invalid strategy name: {error_message}'}), 400
        safe, err = is_safe_code(code)
        if not safe:
            return jsonify({'error': f'Strategy "{name}" failed safety check: {err}'}), 400
        clean.append({'name': name, 'code': code})

    # Names identify entries in the ranking, so duplicates would collapse into one.
    is_unique, dupe_error = check_unique_strategy_names(clean)
    if not is_unique:
        return jsonify({'error': dupe_error}), 400

    # Core-sim parameters.
    try:
        num_cores = int(data.get('num_cores', 2))
    except (TypeError, ValueError):
        return jsonify({'error': 'num_cores must be an integer'}), 400
    if not (2 <= num_cores <= 8):
        return jsonify({'error': 'num_cores must be between 2 and 8'}), 400

    scheduler = data.get('scheduler', 'round_robin')
    if scheduler not in VALID_SCHEDULERS:
        return jsonify({'error': f"scheduler must be one of {list(VALID_SCHEDULERS)}"}), 400

    core_assignment_mode = data.get('core_assignment_mode', 'homogeneous')
    if core_assignment_mode not in VALID_ASSIGNMENT_MODES:
        return jsonify({'error': f"core_assignment_mode must be one of {list(VALID_ASSIGNMENT_MODES)}"}), 400
    if core_assignment_mode == 'heterogeneous':
        het_error = _heterogeneous_combination_error(num_cores, len(clean))
        if het_error:
            return jsonify({'error': het_error}), 400

    raw_seed = data.get('seed', None)
    try:
        seed = int(raw_seed) if raw_seed not in (None, '') else None
    except (TypeError, ValueError):
        return jsonify({'error': 'seed must be an integer'}), 400

    # Opt out of the time budget and evaluate the whole space. The user has been warned in the
    # browser what that costs; this runs on their own machine, so it is their call to make.
    unlimited = data.get('unlimited', False)
    if not isinstance(unlimited, bool):
        return jsonify({'error': 'unlimited must be a boolean'}), 400

    # How many seeded runs to average (each a different set of tasks), how many tasks per
    # workload, and the share of earliest tasks left out as warm-up.
    from core_simulation import TASK_COUNT_CHOICES, WARMUP_CHOICES, MAX_RUNS
    try:
        runs = int(data.get('runs', 10))
        num_processes = int(data.get('num_processes', 50))
        warmup = float(data.get('warmup', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'runs, num_processes and warmup must be numbers'}), 400
    if not (1 <= runs <= MAX_RUNS):
        return jsonify({'error': f'runs must be between 1 and {MAX_RUNS}'}), 400
    if num_processes not in TASK_COUNT_CHOICES:
        return jsonify({'error': f'num_processes must be one of {list(TASK_COUNT_CHOICES)}'}), 400
    if warmup not in WARMUP_CHOICES:
        return jsonify({'error': f'warmup must be one of {list(WARMUP_CHOICES)}'}), 400

    # Optional sandbox mode (same as 1v1 / N-Player): test-run each strategy in a separate,
    # time-limited process first and leave out any that hang or crash. Done after the cheap
    # parameter checks so a bad form value is reported without waiting for the screen.
    screen_flags = []
    isolated = _is_truthy(data.get('isolated'))
    if isolated:
        import isolation
        clean, screen_flags = isolation.screen_strategies(clean)
        if len(clean) < 2:
            return _screening_shortfall(screen_flags)
        if core_assignment_mode == 'heterogeneous':
            het_error = _heterogeneous_combination_error(num_cores, len(clean))
            if het_error:
                return jsonify({'error': het_error, 'flags': screen_flags, 'screened': True}), 400

    try:
        result = run_standalone_core_simulation(
            clean, num_cores=num_cores, scheduler=scheduler,
            core_assignment_mode=core_assignment_mode, seed=seed, unlimited=unlimited,
            runs=runs, num_processes=num_processes, warmup=warmup, ranking=data.get('ranking'))
        if isolated:
            result['flags'] = screen_flags
            result['screened'] = True
        return jsonify(result)
    except Exception as e:
        app.logger.error(f"Standalone OS simulation error: {e}")
        return jsonify({'error': str(e)}), 500










def _run_2player_tournament_core(config):
    """Run a 2-player round-robin tournament off-request. Returns (tournament_id, winner).

    Raises on failure; the worker records it as a failed job. This is the heavy
    compute extracted from /admin/run-tournament so it can run outside the
    request cycle.
    """
    from firebase_admin import firestore
    from firebase_config import db

    tournament_name = config['tournament_name']
    rounds = config['rounds']
    weights = config['weights']
    payoff_matrix = config['payoff_matrix']
    modes = config['modes']
    selected_ids = config['selected_ids']
    seed = config['seed']
    tournament_seed = config['tournament_seed']
    discount_factor = config['discount_factor']
    stochastic_prob = config['stochastic_prob']
    strategies = config['strategies']
    run_by = config['created_by']

    # Run the tournament on the fixed/user ranking weights (no grid search).
    tournament_result = round_robin_tournament(
        strategies, rounds, payoff_matrix, modes=modes,
        discount_factor=discount_factor, stochastic_prob=stochastic_prob,
        weights=weights, seed=tournament_seed
    )

    weighted_leaderboard, winner = determine_weighted_results(tournament_result['leaderboard'], weights)

    # Store tournament results
    tournament_data = {
        'name': tournament_name,
        'winner': winner,
        'participant_count': len(strategies),
        'total_matches': tournament_result['total_matches'],
        'rounds': rounds,
        'weights': weights,
        'payoff_matrix': payoff_matrix,
        'modes': modes,
        'selected_ids': selected_ids,
        'discount_factor': discount_factor,
        'stochastic_prob': stochastic_prob,
        'leaderboard': weighted_leaderboard,
        'participants': strategies,
        'run_date': firestore.SERVER_TIMESTAMP,
        'run_by': run_by,
        'seed_info': {
            'seed': seed,
            'tournament_seed': tournament_seed,
        } if seed is not None else None
    }

    sorted_strategies = sorted(strategies, key=lambda s: (s.get('user_id', ''), s['name']))
    package_inputs = {
        'tournament_config': {
            'seed': seed,
            'rounds': rounds,
            'modes': modes,
            'payoff_matrix': payoff_matrix,
            'weights': weights,
            'discount_factor': discount_factor,
            'stochastic_prob': stochastic_prob
        },
        'participant_code_hashes': [
            (s.get('user_id', ''), s['name'], sha256_text(s['code']))
            for s in sorted_strategies
        ]
    }
    tournament_data['package_inputs_hash'] = sha256_text(canonical_json(package_inputs))

    doc_ref = db.collection('tournament_results').add(tournament_data)
    app.logger.info(f"2-player tournament persisted with ID: {doc_ref[1].id}")
    return {
        'tournament_id': doc_ref[1].id,
        'winner': winner,
        'participant_count': len(strategies),
        'total_matches': tournament_result['total_matches'],
        'seed': seed,
    }


def _run_nplayer_tournament_core(config):
    """Run an N-player tournament off-request. Returns (tournament_id, winner).

    Raises on failure; the worker records it as a failed job. This is the heavy
    compute extracted from /admin/run-nplayer-tournament.
    """
    from firebase_admin import firestore
    from firebase_config import db
    from firestore_utils import to_firestore_safe

    tournament_name = config['tournament_name']
    rounds = config['rounds']
    seed = config['seed']
    group_size = config['group_size']
    weights = config['weights']
    payoff_model_config = config['payoff_model_config']
    mode = config['mode']
    modes = config['modes']
    discount_factor = config['discount_factor']
    stochastic_prob = config['stochastic_prob']
    strategies = config['strategies']
    run_by = config['created_by']
    run_by_email = config['created_by_email']

    payoff_model = parse_payoff_model(payoff_model_config)

    def _compile_strategies(weights_for_context, modes_for_context):
        compiled = []
        for strat in strategies:
            code = strat.get('code', '')
            name = strat.get('name', 'Unknown')
            safe, error = is_safe_code(code)
            if not safe:
                raise ValueError(f'Strategy "{name}" failed safety check: {error}')
            rng = random.Random(seed) if seed is not None else None
            tournament_info = {
                'weights': weights_for_context,
                'payoff_model': payoff_model_config.get('type') if payoff_model_config else 'pairwise_matrix',
                'format': 'n-player',
                'n_players': len(strategies),
                'group_size': group_size,
                'mode': (modes_for_context[0] if modes_for_context else mode),
                'modes': (modes_for_context if modes_for_context else [mode]),
                'discount_factor': discount_factor,
                'stochastic_prob': stochastic_prob,
            }
            exec_context = {'TOURNAMENT_INFO': tournament_info}
            globals_dict = get_safe_globals(exec_context, rng)
            exec(code, globals_dict)
            strategy_func = extract_strategy_function(code, globals_dict, prefer_n_args=4)
            if not callable(strategy_func):
                raise ValueError(f'Strategy "{name}" does not define a callable function')
            compiled.append({'name': name, 'code': code, 'func': strategy_func})
        return compiled

    context_modes = modes if modes is not None else [mode]

    # Run the main tournament on the fixed/user ranking weights (no grid search).
    compiled_strategies = _compile_strategies(weights, context_modes)
    result = group_tournament(
        strategies=compiled_strategies,
        rounds=rounds,
        group_size=group_size,
        seed=seed,
        payoff_model=payoff_model,
        weights=weights,
        mode=mode,
        modes=modes,
        discount_factor=discount_factor,
        stochastic_prob=stochastic_prob,
    )

    weighted_leaderboard, weighted_winner = determine_weighted_results(result.get('leaderboard', []), weights,
                                                                        mode='nplayer')

    # Build seed_info for reproducibility
    seed_info = None
    if seed is not None:
        seed_info = {
            'tournament_seed': seed,
            'derived_seeds': {}
        }
        tournament_info = result.get('tournament_info', {})
        n_groups = tournament_info.get('n_groups', 1)
        for i in range(n_groups):
            seed_info['derived_seeds'][f'group_{i}'] = seed + i * 10000 if seed is not None else None

    # Build participants list with code hashes
    participants = []
    for strat in strategies:
        code = strat['code']
        code_sha256 = sha256_text(code)
        participants.append({
            'name': strat['name'],
            'code': code,
            'code_sha256': code_sha256,
            'user_id': strat['user_id'],
            'player_email': strat['player_email'],
            'player_name': strat['player_name']
        })

    tournament_id = str(uuid.uuid4())
    tournament_doc = {
        'name': tournament_name,
        'format': 'n-player',
        'created_at': firestore.SERVER_TIMESTAMP,
        'rounds': rounds,
        'group_size': group_size,
        'seed': seed,
        'seed_info': seed_info,
        'payoff_model': payoff_model_config,
        'weights': weights,
        'mode': result.get('tournament_info', {}).get('mode', mode),
        'modes': result.get('tournament_info', {}).get('modes', modes if modes is not None else [mode]),
        'discount_factor': discount_factor,
        'stochastic_prob': stochastic_prob,
        'participants': participants,
        'results': weighted_leaderboard,
        'leaderboard': weighted_leaderboard,
        'matches': result.get('matches', []),
        'tournament_info': result.get('tournament_info', {}),
        'winner': weighted_winner,
        'participant_count': len(compiled_strategies),
        'total_matches': result.get('tournament_info', {}).get('n_groups', 1),
        'run_by': run_by,
        'run_by_email': run_by_email,
    }

    package_inputs = {
        'strategies': [s['name'] for s in compiled_strategies],
        'rounds': rounds,
        'group_size': group_size,
        'seed': seed,
        'payoff_model': payoff_model_config,
        'weights': weights,
        'mode': mode,
        'modes': modes,
        'discount_factor': discount_factor,
        'stochastic_prob': stochastic_prob,
    }
    tournament_doc['package_inputs_hash'] = sha256_text(canonical_json(package_inputs))

    db.collection('nplayer_tournament_results').document(tournament_id).set(to_firestore_safe(tournament_doc))
    app.logger.info(f"N-player tournament persisted with ID: {tournament_id}")
    return {
        'tournament_id': tournament_id,
        'winner': weighted_winner,
        'participant_count': len(compiled_strategies),
        'seed': seed,
    }








def determine_weighted_results(leaderboard, weights, ranking=None, mode='1v1'):
    """Add the weighted score to each entry, then order by ``ranking`` (default: the itinerary's
    total points). Returns (sorted leaderboard, winner name)."""
    weights = weights or {}
    if not leaderboard:
        return [], "No participants"
    
    # Calculate weighted scores for each participant
    weighted_leaderboard = []
    for entry in leaderboard:
        # Calculate individual scores (0-1 scale)
        total_games = entry.get('wins', 0) + entry.get('draws', 0) + entry.get('losses', 0)
        win_rate_score = (entry.get('wins', 0) / max(total_games, 1)) if total_games > 0 else 0
        
        # Use NORMALIZED cooperation percentage if available, else raw
        if 'norm_cooperation_percentage' in entry:
            cooperation_score = (entry.get('norm_cooperation_percentage', 0) / 100)
        else:
            cooperation_score = (entry.get('cooperation_percentage', 0) / 100) if entry.get('cooperation_percentage') else 0
            
        points_score = (entry.get('points_percentage', 0) / 100) if entry.get('points_percentage') else 0
        
        # Calculate weighted score
        weighted_score = (
            win_rate_score * weights.get('win_rate', 0) +
            cooperation_score * weights.get('cooperation', 0) +
            points_score * weights.get('points', 0)
        ) * 100
        
        # Add weighted score to entry
        entry_with_score = entry.copy()
        entry_with_score['weighted_score'] = weighted_score
        weighted_leaderboard.append(entry_with_score)
    
    spec = dict(ranking) if isinstance(ranking, dict) else {}
    if weights and not spec.get('weights'):
        spec['weights'] = weights
    sorted_leaderboard = rank_entries(weighted_leaderboard, spec, mode)
    
    # Winner is the first entry
    winner = sorted_leaderboard[0]['name'] if sorted_leaderboard else "No participants"
    
    return sorted_leaderboard, winner












def main(open_browser=False):
    """Start the local server (used by `python app.py` and by the bundled exe's launcher)."""
    import socket
    import threading
    import webbrowser

    # Environment-based debug mode
    # Default to False for production safety
    # Set FLASK_DEBUG=True environment variable to enable debug mode
    debug_mode = os.environ.get('FLASK_DEBUG', 'False') == 'True'

    if debug_mode:
        print("WARNING: Debug mode is ENABLED. Only use this in development!")
        print("Set FLASK_DEBUG=False or remove the variable for production.")
    else:
        print("Debug mode is disabled (production-safe)")

    # Always loopback: this single-user app auto-logs in the local owner, so it must never listen
    # on the network (there is no host flag to forget).
    host = '127.0.0.1'
    # IPD_PORT (in .env or the environment) moves the app off 5000 if something else uses it.
    try:
        port = int(os.environ.get('IPD_PORT', '5000'))
    except (TypeError, ValueError):
        port = 5000
    if not (1024 <= port <= 65535):
        port = 5000
    url = f"http://{host}:{port}"

    # Already running (e.g. the exe was double-clicked twice)? Don't fail on the busy port: just
    # point the browser at the running copy.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        already_running = probe.connect_ex((host, port)) == 0
    if already_running:
        print(f"The app is already running at {url}")
        if open_browser:
            webbrowser.open(url)
        return

    print(f"Starting server on {url}")
    if open_browser:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    if debug_mode:
        app.run(host=host, port=port, debug=True)
    else:
        # Waitress (already a dependency) instead of the Flask dev server, which can hang on
        # Windows. This makes `python app.py` the one run command on every OS.
        try:
            from waitress import serve
        except ImportError:
            app.run(host=host, port=port, debug=False)
        else:
            print("Keep this window open while you use the app. Close it (or press Ctrl+C) to stop.")
            serve(app, host=host, port=port)


if __name__ == '__main__':
    main()
