from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash, g
import json
import random
import ast
import re
import secrets
import math
import logging
import os
import sys
import threading
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
try:
    from dotenv import load_dotenv
    # Load the .env sitting next to this file by absolute path. On PythonAnywhere
    # the WSGI process runs from a different working directory, so a bare
    # load_dotenv() (which searches CWD) silently finds nothing and leaves
    # FLASK_SECRET_KEY unset -> the app crashes on import. An absolute path makes
    # the load independent of the working directory.
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    load_dotenv(_env_path)
except ImportError:
    pass

from auth_utils import verify_firebase_token, create_or_update_user, get_user_role, login_required, admin_required, is_admin
from audit_utils import log_audit_event, get_request_context
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from payoff_models import PairwiseMatrixPayoff, PublicGoodsPayoff, KCooperatorTensorPayoff
from n_player_simulation import group_tournament, call_strategy
from tournament_package import sha256_text, canonical_json, build_tournament_package

# --- SECURITY & LIMITS ---
class InstructionLimitExceeded(Exception):
    """Raised when a strategy executes too many instructions (loops)."""
    pass

def run_with_limit(func, *args, limit=10000):
    """Execute a function with a strict instruction (line) limit using closure."""
    instr_count = [limit]  # Use mutable list so trace function can modify it
    
    def trace_instructions(frame, event, arg):
        """Trace function to count lines and enforce limits."""
        if event == 'line':
            instr_count[0] -= 1
            if instr_count[0] <= 0:
                raise InstructionLimitExceeded("Loop limit exceeded")
        return trace_instructions
    
    sys.settrace(trace_instructions)
    try:
        result = func(*args)
        return result
    finally:
        sys.settrace(None)

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

# (Legacy) admin practice ceiling. Kept for compatibility; the local app no longer caps the
# number of practice strategies (it runs on the user's own machine).
try:
    MAX_ADMIN_STRATEGIES = int(os.environ.get('MAX_ADMIN_STRATEGIES', '50'))
except (TypeError, ValueError):
    MAX_ADMIN_STRATEGIES = 50

# (Legacy) practice strategy ceiling. No longer enforced — every mode runs uncapped on the
# user's machine; kept only so old references resolve.
try:
    MAX_PRACTICE_STRATEGIES = int(os.environ.get('MAX_PRACTICE_STRATEGIES', '4'))
except (TypeError, ValueError):
    MAX_PRACTICE_STRATEGIES = 4
if MAX_PRACTICE_STRATEGIES < 2:
    MAX_PRACTICE_STRATEGIES = 4

# Max cores an N-player core simulation may model. A 1v1 tournament is inherently a
# 2-core system; an N-player tournament uses N cores, but is capped so a single run
# can't allocate an unbounded number of simulated cores on the user's machine.
try:
    MAX_NPLAYER_CORES = int(os.environ.get('MAX_NPLAYER_CORES', '64'))
except (TypeError, ValueError):
    MAX_NPLAYER_CORES = 64

# Max strategy->core combinations evaluated by the 'heterogeneous' assignment. The space is
# C(strategies, num_cores) (each strategy used at most once). Every combination is enumerated and
# displayed; if the count would exceed this ceiling the run is rejected (400) so the user reduces
# strategies/cores — never silently sampled. Bounds worst-case compute on the user's machine.
try:
    MAX_COMBINATIONS = int(os.environ.get('MAX_COMBINATIONS', '60'))
except (TypeError, ValueError):
    MAX_COMBINATIONS = 60
if MAX_COMBINATIONS < 1:
    MAX_COMBINATIONS = 60


def _heterogeneous_combination_error(num_cores, n_strategies):
    """Return a user-facing error string if a heterogeneous OS-sim run is infeasible, else None.

    Heterogeneous places one distinct strategy per core, so it needs at least ``num_cores``
    strategies; and it enumerates every ``C(n, cores)`` mixture, which must stay within
    ``MAX_COMBINATIONS`` (we reject rather than sample so every displayed run is complete).
    """
    if n_strategies < num_cores:
        return (f"Heterogeneous assignment needs at least num_cores ({num_cores}) strategies; "
                f"you provided {n_strategies}. Add strategies, lower cores, or use homogeneous.")
    total = math.comb(n_strategies, num_cores)
    if total > MAX_COMBINATIONS:
        return (f"Too many heterogeneous combinations ({total}) for {n_strategies} strategies on "
                f"{num_cores} cores; the maximum is {MAX_COMBINATIONS}. Reduce strategies or cores.")
    return None

# Valid CPU scheduler policies for the OS simulation (see schedulers.py).
VALID_SCHEDULERS = ('fcfs', 'round_robin', 'sjf', 'priority', 'mlfq', 'cfs', 'affinity')
VALID_ASSIGNMENT_MODES = ('homogeneous', 'heterogeneous')

# --------------------------

# Configure logging before Flask app initialization
FLASK_ENV = os.environ.get('FLASK_ENV', 'production')
IS_PRODUCTION = FLASK_ENV == 'production'

# Local single-user mode (see .env.example / README "Deployment model"). When on,
# the app runs as one fixed local owner on the student's own machine: no Firebase
# login is required to practice. The auth decorators (auth_utils.py) short-circuit;
# the before_request hook below injects the matching synthetic session user so the
# 55 routes that read session['user'] keep working unchanged.
LOCAL_MODE = os.environ.get('LOCAL_MODE', '').strip().lower() in ('1', 'true', 'yes', 'on')
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
# to UPLOAD; results/landing are public reads. HUB_MODE and LOCAL_MODE are mutually
# exclusive and both default off (so the test suite models the original hosted app).
HUB_MODE = os.environ.get('HUB_MODE', '').strip().lower() in ('1', 'true', 'yes', 'on')

# Shared class token for the strategy-fetch API (GET /api/strategies). One value used
# on both sides: the website (HUB_MODE) REQUIRES it; the local app SENDS it (hub_client
# reads the same HUB_API_TOKEN). Not per-user login -- a low-sensitivity read token
# distributed with the local app config. Blank disables the endpoint (fails closed).
HUB_API_TOKEN = os.environ.get('HUB_API_TOKEN', '').strip()

app_logger = setup_logging(is_production=IS_PRODUCTION)

app = Flask(__name__)


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
    _secret_key = 'dev-insecure-secret-key-do-not-use-in-production'
    app.logger.warning(
        "FLASK_SECRET_KEY not set; using a fixed insecure dev key. "
        "Set FLASK_SECRET_KEY for any non-local deployment."
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


@app.before_request
def _inject_local_owner():
    """In LOCAL_MODE, log the single local owner in automatically so the existing
    auth-gated UI works with no Firebase login. The auth decorators already
    short-circuit in LOCAL_MODE; this just makes session['user'] present for the
    many routes that read it directly. No-op when LOCAL_MODE is off."""
    if LOCAL_MODE and 'user' not in session:
        session['user'] = dict(LOCAL_OWNER)


# Heavy-compute endpoints (practice + tournament/OS-sim runs). On the hosted website
# (HUB_MODE) these must never execute -- all compute happens in the local app -- so we
# fail them fast with a clear message instead of tying up the single free-tier worker.
COMPUTE_ENDPOINTS = frozenset({
    'play_game', 'run_tournament', 'nplayer_tournament', 'os_simulation',
    'admin_run_tournament', 'admin_run_nplayer_tournament', 'admin_run_os_simulation',
    'admin_run_due_scheduled_tournaments', 'run_official_local',
})


@app.before_request
def _block_compute_in_hub_mode():
    """In HUB_MODE, reject the heavy-compute endpoints with 503 + a 'run locally'
    message. No-op when HUB_MODE is off (the local app and the original hosted app
    keep running compute normally)."""
    if HUB_MODE and request.endpoint in COMPUTE_ENDPOINTS:
        return jsonify({
            'error': 'Simulations run in the local app, not on the website. '
                     'Download and run the local app to practice or run tournaments.'
        }), 503


@app.context_processor
def _inject_csp_nonce():
    """Make {{ csp_nonce }} available in every template."""
    return {'csp_nonce': getattr(g, 'csp_nonce', '')}


@app.context_processor
def _inject_modes():
    """Expose the deployment mode to templates so the navbar can show the right links
    (website vs local app)."""
    return {'HUB_MODE': HUB_MODE, 'LOCAL_MODE': LOCAL_MODE}


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


def sanitize_firestore_key(key):
    """Sanitize Firestore document keys to prevent path traversal"""
    if not key: return None
    # Allow alphanumeric, hyphens, and underscores only
    return "".join(c for c in key if c.isalnum() or c in "-_")


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

    # For the default field_type ('name'), enforce a strict character set.
    # This matches the regression tests for validate_name() directly.
    if field_type == 'name':
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


# Modules that user strategies are permitted to import. Single source of truth lives in
# core_simulation so the OS-sim sandbox, the 1v1/N-Player sandbox, and the static `is_safe_code`
# checker all agree. Dangerous stdlib modules (os, sys, subprocess, socket, io, pathlib, shutil,
# importlib, ctypes, pickle, threading, multiprocessing, signal, gc, inspect, ast, dis, time, etc.)
# are intentionally absent.
from core_simulation import ALLOWED_IMPORTS

# Capture the real __import__ at module load time so the sandbox wrapper can use it.
_real_import = __import__


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

    def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        top_level = name.split('.')[0]
        if top_level not in ALLOWED_IMPORTS:
            raise ImportError(f"Import of '{name}' is not allowed in strategy code")
        return _real_import(name, globals, locals, fromlist, level)

    safe_builtins = {
        'abs': abs, 'all': all, 'any': any, 'bool': bool,
        'complex': complex, 'divmod': divmod, 'enumerate': enumerate,
        'filter': filter, 'float': float, 'int': int, 'len': len,
        'list': list, 'map': map, 'max': max, 'min': min,
        'pow': pow, 'range': range, 'reversed': reversed,
        'round': round, 'set': set, 'sorted': sorted,
        'str': str, 'sum': sum, 'tuple': tuple, 'zip': zip,
        'dict': dict,
        '__import__': _safe_import,
        'globals': lambda: globals_dict  # Allow strategies to check what's in their globals
    }

    # Use provided RNG instance or module-level random
    random_obj = rng if rng is not None else random

    globals_dict.update({
        '__builtins__': safe_builtins,
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
        
        # If we found function definitions, prefer ones with preferred parameter count
        if function_names:
            # First try to find a function with exactly prefer_n_args parameters
            for func_name in function_names:
                if function_signatures.get(func_name) == prefer_n_args and func_name in globals_dict and callable(globals_dict[func_name]):
                    return globals_dict[func_name]
            
            # If no preferred-param function, take the first one we found
            for func_name in function_names:
                if func_name in globals_dict and callable(globals_dict[func_name]):
                    return globals_dict[func_name]
    except:
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
    
    # Constants for modes (now configurable)
    DISCOUNT_FACTOR = float(discount_factor)
    STOCHASTIC_PROB = float(stochastic_prob)
    
    # Check for code safety first
    safe_a, error_a = is_safe_code(player_a_code)
    if not safe_a:
        return {
            'winner': 'Error', 'a_points': 0, 'b_points': 0, 'rounds': 0,
            'player_a': player_a_name, 'player_b': player_b_name, 'rounds_detail': [],
            'error': f"Security Violation in Player A code: {error_a}"
        }

    safe_b, error_b = is_safe_code(player_b_code)
    if not safe_b:
        return {
            'winner': 'Error', 'a_points': 0, 'b_points': 0, 'rounds': 0,
            'player_a': player_a_name, 'player_b': player_b_name, 'rounds_detail': [],
            'error': f"Security Violation in Player B code: {error_b}"
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
        # If tournament_info was provided but payoff_matrix is None, update it with resolved default
        if tournament_info.get('payoff_matrix') is None:
            tournament_info['payoff_matrix'] = payoff_matrix
    
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
        return { # Return safe error instead of raising
            'winner': 'Error',
            'a_points': 0,
            'b_points': 0,
            'rounds': 0,
            'player_a': player_a_name,
            'player_b': player_b_name,
            'rounds_detail': [],
            'error': "Could not find strategy functions"
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

        # Get moves
        A_guess = None
        B_guess = None
        error_occurred = False
        error_info = {}
        
        # Execute Player A strategy
        try:
            last_moves_a = [enemy_history[-1]] if enemy_history else []
            meta_a = {'round': current_round, 'n_players': 2, 'player_index': 0, 'tournament_info': tournament_info, 'rng': rng}
            A_guess = run_with_limit(call_strategy, player_a_func, last_moves_a, user_history.copy(), [enemy_history.copy()], meta_a, limit=10000)
        except InstructionLimitExceeded as e:
            error_msg = "Strategy exceeded instruction limit (infinite loop detected)"
            app.logger.error(f"Player A: {error_msg}")
            error_occurred = True
            error_info = {
                'error': error_msg,
                'error_type': 'InstructionLimitExceeded',
                'error_player': 'A',
                'terminated_early': True
            }
        except RecursionError as e:
            error_msg = "Strategy exceeded recursion limit (infinite recursion detected)"
            app.logger.error(f"Player A: {error_msg}")
            error_occurred = True
            error_info = {
                'error': error_msg,
                'error_type': 'RecursionError',
                'error_player': 'A',
                'terminated_early': True
            }
        except Exception as e:
            error_msg = f"{type(e).__name__}: {str(e)}"
            app.logger.error(f"Runtime error in Player A strategy: {error_msg}")
            error_occurred = True
            error_info = {
                'error': error_msg,
                'error_type': type(e).__name__,
                'error_player': 'A',
                'terminated_early': True
            }
        
        # Execute Player B strategy if no error yet
        if not error_occurred:
            try:
                last_moves_b = [user_history[-1]] if user_history else []
                meta_b = {'round': current_round, 'n_players': 2, 'player_index': 1, 'tournament_info': tournament_info, 'rng': rng}
                B_guess = run_with_limit(call_strategy, player_b_func, last_moves_b, enemy_history.copy(), [user_history.copy()], meta_b, limit=10000)
            except InstructionLimitExceeded as e:
                error_msg = "Strategy exceeded instruction limit (infinite loop detected)"
                app.logger.error(f"Player B: {error_msg}")
                error_occurred = True
                error_info = {
                    'error': error_msg,
                    'error_type': 'InstructionLimitExceeded',
                    'error_player': 'B',
                    'terminated_early': True
                }
            except RecursionError as e:
                error_msg = "Strategy exceeded recursion limit (infinite recursion detected)"
                app.logger.error(f"Player B: {error_msg}")
                error_occurred = True
                error_info = {
                    'error': error_msg,
                    'error_type': 'RecursionError',
                    'error_player': 'B',
                    'terminated_early': True
                }
            except Exception as e:
                error_msg = f"{type(e).__name__}: {str(e)}"
                app.logger.error(f"Runtime error in Player B strategy: {error_msg}")
                error_occurred = True
                error_info = {
                    'error': error_msg,
                    'error_type': type(e).__name__,
                    'error_player': 'B',
                    'terminated_early': True
                }
        
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
            
        # Validate moves
        if A_guess not in ['C', 'D']: A_guess = 'C'
        if B_guess not in ['C', 'D']: B_guess = 'C'
        
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
    # On the hosted website there is no local compute, so the practice page is moot;
    # send visitors to the public results/leaderboard instead.
    if HUB_MODE:
        return redirect(url_for('results'))
    return render_template('index.html')
















# Note: The create_admin route has been removed for security. 
# Admins should be created via the Admin Panel or directly in Firestore.









# --- Website strategy-fetch API (consumed by local apps) -----------------------
# The hosted website (HUB_MODE) serves all submitted strategies to local apps over
# HTTP so students can pull and practice against them WITHOUT any Firebase access on
# their machine. Gated by a shared class token (not per-user login). Only name/code/
# author are exposed -- emails/uids are redacted.

def _check_hub_fetch_token():
    """Return None if the request carries the correct class token, else an error
    (response, status) tuple. When HUB_API_TOKEN is unset the strategy API is disabled
    and reads as 404 (fail closed); a missing/wrong token is 401."""
    if not HUB_API_TOKEN:
        return jsonify({'error': 'Not found.'}), 404
    header = request.headers.get('Authorization', '')
    token = header[7:].strip() if header.startswith('Bearer ') else ''
    if token != HUB_API_TOKEN:
        return jsonify({'error': 'Unauthorized.'}), 401
    return None






# --- Admin results round-trip (local run -> downloadable file -> website upload) ---
# Compute is local-only, so an admin runs the official tournament in their LOCAL app
# (`/admin/run-official`), which fetches the submitted strategies from the website and
# returns the result as a downloadable JSON. They then UPLOAD that file on the website
# (`/admin/upload-results`), which stores it so it shows on the public results page.

DEFAULT_WEIGHTS = {'win_rate': 0.33, 'cooperation': 0.34, 'points': 0.33}


@app.route('/admin/run-official', methods=['POST'])
@admin_required
@limiter.limit("5 per minute")
def run_official_local():
    """LOCAL admin tournament: run the official round-robin over the cached submitted
    players plus any local players, and return a publishable result package (leaderboard
    + participants + local-bot code). In COMPUTE_ENDPOINTS, so it's blocked in HUB_MODE."""
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or 'Official Tournament').strip()
    rounds = int(data.get('rounds', 200))
    seed = data.get('seed')
    weights = data.get('weights') or DEFAULT_WEIGHTS
    modes = data.get('modes') or ['standard']
    local_players = data.get('local_players') or []

    # Website players come from the local cache (press Refresh on the Hub to update it).
    strategies = []
    for s in _read_strategy_cache():
        if s.get('code'):
            strategies.append({'name': s['name'], 'code': s['code'],
                               'user_id': s.get('id') or s['name'], 'source': 'website'})

    # Local players the admin added — screened by the local sandbox before running.
    local_bots = []
    for i, lp in enumerate(local_players):
        nm = (lp.get('name') or f'House Bot {i + 1}').strip()
        code = lp.get('code') or ''
        if not code:
            continue
        ok, err = is_safe_code(code)
        if not ok:
            return jsonify({'error': f'Local player "{nm}" rejected by sandbox: {err}'}), 400
        strategies.append({'name': nm, 'code': code, 'user_id': f'local:{nm}', 'source': 'local'})
        local_bots.append({'name': nm, 'code': code})

    if len(strategies) < 2:
        return jsonify({'error': 'Need at least 2 players. Press Refresh to fetch submitted '
                                 'strategies, and/or add local players.'}), 400

    result = round_robin_tournament(strategies, rounds=rounds, modes=modes, weights=weights, seed=seed)
    weighted_leaderboard, winner = determine_weighted_results(result['leaderboard'], weights)

    participants = []
    for s in strategies:
        p = {'name': s['name'], 'source': s['source']}
        if s['source'] == 'website':
            p['website_id'] = s['user_id']
        participants.append(p)

    from datetime import datetime, timezone
    package = {
        'name': name,
        'winner': winner,
        'participant_count': len(strategies),
        'total_matches': result['total_matches'],
        'rounds': rounds,
        'weights': weights,
        'modes': modes,
        'leaderboard': weighted_leaderboard,
        'participants': participants,
        'local_bots': local_bots,   # code for the website to register as bots at publish
        'run_date': datetime.now(timezone.utc).isoformat(),
        'seed_info': {'seed': seed} if seed is not None else None,
    }
    return jsonify(package)


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
STRATEGY_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'strategy_cache.json')


def _read_strategy_cache():
    try:
        with open(STRATEGY_CACHE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, ValueError, OSError):
        return []


def _write_strategy_cache(strategies):
    with open(STRATEGY_CACHE_PATH, 'w', encoding='utf-8') as f:
        json.dump(strategies, f)


# Organizer-provided PRACTICE strategies are cached separately from player submissions
# (two galleries: player submissions vs organizer practice code).
PRACTICE_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'practice_cache.json')


def _read_practice_cache():
    try:
        with open(PRACTICE_CACHE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, ValueError, OSError):
        return []


def _write_practice_cache(strategies):
    with open(PRACTICE_CACHE_PATH, 'w', encoding='utf-8') as f:
        json.dump(strategies, f)


@app.route('/hub', methods=['GET'])
@login_required
def hub():
    """Browse / import strategies fetched from the website, and submit/publish via handoff."""
    import hub_client
    return render_template('hub.html', hub_enabled=hub_client.hub_enabled(),
                           hub_base_url=hub_client.HUB_BASE_URL)


@app.route('/hub/list', methods=['GET'])
@login_required
def hub_list():
    """Return both locally cached galleries (no network): player submissions + organizer
    practice strategies. Refresh updates them."""
    return jsonify({'strategies': _read_strategy_cache(),
                    'practice': _read_practice_cache(), 'cached': True})


@app.route('/hub/refresh', methods=['POST'])
@login_required
@limiter.limit("30 per minute")
def hub_refresh():
    """The only networked gallery call: fetch from the website and overwrite the local
    cache. On failure (offline / website down) keep the existing cache and report."""
    import hub_client
    try:
        strategies = hub_client.list_strategies()
    except hub_client.HubError as e:
        return jsonify({'error': str(e), 'strategies': _read_strategy_cache(),
                        'practice': _read_practice_cache()}), 502
    # Organizer practice strategies are best-effort (older websites may not serve them).
    try:
        practice = hub_client.list_practice_strategies()
    except hub_client.HubError:
        practice = _read_practice_cache()
    try:
        _write_strategy_cache(strategies)
        _write_practice_cache(practice)
    except OSError as e:
        app.logger.warning(f"Could not write strategy cache: {e}")
    return jsonify({'strategies': strategies, 'practice': practice, 'refreshed': True})


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

    return jsonify({'success': True, 'name': name, 'code': code})


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
    # Smoke test: run the strategy in a short match so faulty code (or a name that doesn't
    # match the function) is caught locally before submission.
    try:
        result = game(code, name, _SMOKE_TEST_OPPONENT, 'AllC', rounds=10, mode='standard', seed=1)
    except Exception as e:
        return jsonify({'error': f'Strategy crashed when run locally: {e}'}), 400
    if isinstance(result, dict) and result.get('winner') == 'Error':
        return jsonify({'error': 'Strategy errored in a test match — make sure the function name '
                                 'matches the strategy name and it returns "C"/"D", then re-run.'}), 400
    return jsonify({'ok': True})


# Note: there is no local "publish" route. Strategy submission happens on the website
# (log in there and submit) -- the local app is fetch + practice only, with no upload
# path and no credentials. Admins upload tournament results on the website too.


def round_robin_tournament(strategies, rounds=200, payoff_matrix=None, modes=None, discount_factor=0.95, stochastic_prob=0.995, weights=None, seed=None):
    """
    Run a round robin tournament with multiple strategies and game modes
    """
    if modes is None:
        modes = ['standard']
    
    # Create RNG instance if seed provided
    rng = random.Random(seed) if seed is not None else None
    
    # Context to pass to strategies
    # Ensure consistent structure with reserved keys for future N-player extensions
    tournament_info = {
        'weights': weights,
        'payoff_matrix': payoff_matrix,
        # Reserved keys for future N-player extensions
        'format': '2-player',
        'n_players': 2,
        'payoff_model': 'standard'
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
                'name': strategy['name'],
                'error': error
            })
    
    # Update the strategies list to only include valid ones
    strategies = valid_strategies
    
    # Sort strategies for stable ordering (by name, then user_id if present)
    strategies = sorted(strategies, key=lambda s: (s['name'], s.get('user_id', '')))
    
    if len(strategies) < 2:
        error_msg = f"Not enough valid strategies to run tournament. {len(disqualified_strategies)} were disqualified."
        app.logger.error(error_msg)
        return {
            'leaderboard': [],
            'matches': [],
            'total_matches': 0,
            'error': error_msg,
            'disqualified': disqualified_strategies
        }

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
    
    # Generate random round count for 'random' mode if applicable
    # Use RNG instance if available
    if rng:
        fixed_random_rounds = rng.randint(100, 300)
    else:
        fixed_random_rounds = randint(100, 300)
    
    # Generate all possible pairs
    strategy_pairs = list(combinations(strategies, 2))
    
    app.logger.info(f"=== Round Robin Tournament: {len(strategies)} strategies, {len(strategy_pairs)} matches ===")
    app.logger.info(f"Modes: {modes}")
    
    for i, (strategy_a, strategy_b) in enumerate(strategy_pairs):
        app.logger.info(f"Match {i+1}/{len(strategy_pairs)}: {strategy_a['name']} vs {strategy_b['name']}")
        
        # Run games for all selected modes and average the results
        pair_a_points = 0
        pair_b_points = 0
        pair_mode_results = []
        
        for mode_idx, mode in enumerate(modes):
            # Generate deterministic sub-seed for this match if main seed provided
            match_seed = None
            if seed is not None:
                # Combine tournament seed with match index and mode index
                match_seed = seed + i * 1000 + mode_idx
            
            match_result = game(
                strategy_a['code'],
                strategy_a['name'],
                strategy_b['code'],
                strategy_b['name'],
                rounds,
                payoff_matrix,
                mode=mode,
                fixed_random_rounds=fixed_random_rounds,
                discount_factor=discount_factor,
                stochastic_prob=stochastic_prob,
                tournament_info=tournament_info,
                seed=match_seed
            )
            
            pair_a_points += match_result['a_points']
            pair_b_points += match_result['b_points']
            pair_mode_results.append(match_result)
            
            # Track points per mode
            leaderboard[strategy_a['name']]['mode_points'][mode] += match_result['a_points']
            leaderboard[strategy_b['name']]['mode_points'][mode] += match_result['b_points']

            # Track raw points per mode
            leaderboard[strategy_a['name']]['mode_raw_points'][mode] += match_result['a_total_points']
            leaderboard[strategy_b['name']]['mode_raw_points'][mode] += match_result['b_total_points']
            
            # Track total raw points
            leaderboard[strategy_a['name']]['total_raw_points'] += match_result['a_total_points']
            leaderboard[strategy_b['name']]['total_raw_points'] += match_result['b_total_points']
            
            # Count cooperates and defects directly from match results (accurate totals)
            # Update for Player A
            leaderboard[strategy_a['name']]['cooperates'] += match_result['a_coops']
            leaderboard[strategy_a['name']]['mode_stats'][mode]['cooperates'] += match_result['a_coops']
            
            leaderboard[strategy_a['name']]['defects'] += match_result['a_defects']
            leaderboard[strategy_a['name']]['mode_stats'][mode]['defects'] += match_result['a_defects']
            
            leaderboard[strategy_a['name']]['total_moves'] += match_result['rounds']
            leaderboard[strategy_a['name']]['mode_stats'][mode]['total_moves'] += match_result['rounds']
            
            # Update for Player B
            leaderboard[strategy_b['name']]['cooperates'] += match_result['b_coops']
            leaderboard[strategy_b['name']]['mode_stats'][mode]['cooperates'] += match_result['b_coops']
            
            leaderboard[strategy_b['name']]['defects'] += match_result['b_defects']
            leaderboard[strategy_b['name']]['mode_stats'][mode]['defects'] += match_result['b_defects']
            
            leaderboard[strategy_b['name']]['total_moves'] += match_result['rounds']
            leaderboard[strategy_b['name']]['mode_stats'][mode]['total_moves'] += match_result['rounds']

        # Calculate average score for the pair across all modes
        avg_a_points = pair_a_points / len(modes)
        avg_b_points = pair_b_points / len(modes)
        
        # Update leaderboard points
        leaderboard[strategy_a['name']]['total_points'] += avg_a_points
        leaderboard[strategy_b['name']]['total_points'] += avg_b_points
        
        # Determine winner for the pair based on average score
        if avg_a_points > avg_b_points:
            leaderboard[strategy_a['name']]['wins'] += 1
            leaderboard[strategy_b['name']]['losses'] += 1
        elif avg_a_points < avg_b_points:
            leaderboard[strategy_b['name']]['wins'] += 1
            leaderboard[strategy_a['name']]['losses'] += 1
        else:
            leaderboard[strategy_a['name']]['draws'] += 1
            leaderboard[strategy_b['name']]['draws'] += 1
        
        # Store result (using the last mode's details or a summary)
        match_result = {
            'player_a': strategy_a['name'],
            'player_b': strategy_b['name'],
            'a_points': avg_a_points,
            'b_points': avg_b_points,
            'winner': "A" if avg_a_points > avg_b_points else "B" if avg_b_points > avg_a_points else "Draw",
            'mode_results': pair_mode_results
        }
        
        # Propagate error information if present in any mode result
        for mode_result in pair_mode_results:
            if 'error' in mode_result:
                match_result['error'] = mode_result['error']
                match_result['error_type'] = mode_result.get('error_type', 'Exception')
                match_result['error_player'] = mode_result.get('error_player', 'Unknown')
                match_result['terminated_early'] = mode_result.get('terminated_early', True)
                break
        
        results.append(match_result)
    
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
            'weighted_score': stats['total_points']  # Default to points for initial sort
        }
        enhanced_leaderboard.append(enhanced_entry)
    
    # Sort leaderboard by total points (descending)
    sorted_leaderboard = sorted(
        enhanced_leaderboard,
        key=lambda x: x['total_points'],
        reverse=True
    )
    
    return {
        'leaderboard': sorted_leaderboard,
        'matches': results,
        'total_matches': len(strategy_pairs)
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

    # Practice mode caps — same small ceiling for everyone (admins included) so a co-located
    # class can't overload the single free-tier worker.
    MAX_PRACTICE_ROUNDS = int(os.getenv('MAX_PRACTICE_ROUNDS', '250'))
    strategy_cap = MAX_PRACTICE_STRATEGIES

    strategies = data.get('strategies', [])
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
            seed=seed,
        )
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
    
    # MINOR Issue #6: Env var caps with error handling
    # Student default is 8 players; admins get a higher ceiling (server enforces below).
    try:
        MAX_NPLAYER_STRATEGIES = int(os.getenv('MAX_NPLAYER_STRATEGIES', '8'))
        MAX_NPLAYER_ROUNDS = int(os.getenv('MAX_NPLAYER_ROUNDS', '1000'))
    except (ValueError, TypeError):
        # Fall back to defaults if env vars are invalid
        MAX_NPLAYER_STRATEGIES = 8
        MAX_NPLAYER_ROUNDS = 1000

    user_is_admin = is_admin()
    
    strategies = data.get('strategies', [])
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
                'func': strategy_func
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



# Email format used for both signup and admin bulk-add (kept in sync with the signup route).
_ADMIN_EMAIL_PATTERN = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')


def _parse_admin_emails(data):
    """Collect emails from an admin add-user payload.

    Accepts ``emails`` (a list) and/or ``email`` (a single string that may itself be comma/
    newline/whitespace separated — e.g. pasted from a CSV). Returns a de-duplicated, order-preserving
    list (case-insensitive dedupe, original casing kept).
    """
    raw = []
    val = data.get('emails')
    if isinstance(val, list):
        raw.extend(val)
    elif isinstance(val, str):
        raw.append(val)
    if data.get('email'):
        raw.append(data.get('email'))

    tokens = []
    for item in raw:
        if not isinstance(item, str):
            continue
        # Split on commas, semicolons, and any whitespace (newlines from a CSV paste/upload).
        tokens.extend(re.split(r'[\s,;]+', item))

    seen = set()
    out = []
    for tok in tokens:
        email = tok.strip()
        # Require an '@' so CSV header cells ("email") and stray tokens are ignored, not reported
        # as failures. Full format validation happens per-address in the route.
        if not email or '@' not in email:
            continue
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(email)
    return out








































# ---------------------------------------------------------------------------
# Asynchronous tournament jobs
#
# A full tournament (200 rounds, many participants) can run for minutes --
# far longer than PythonAnywhere's ~5 min
# web-request limit. Running it inside the request handler caused the request
# to be killed and replaced with an HTML error page, which the frontend then
# failed to parse as JSON ("Unexpected token '<'"), leaving the progress bar
# stuck. Instead we validate input synchronously, enqueue a job, run the heavy
# compute on a daemon worker thread, and let the UI poll
# /admin/tournament-job/<id> for status.
# ---------------------------------------------------------------------------

# Jobs left 'queued'/'running' with no heartbeat past this many seconds are
# treated as dead (worker recycled) and marked failed by the scheduler poll.
TOURNAMENT_JOB_TIMEOUT_SECONDS = int(os.getenv('TOURNAMENT_JOB_TIMEOUT_SECONDS', '1800'))


def _create_tournament_job(job_type, name, created_by):
    """Create a queued tournament job document and return its id."""
    from firebase_admin import firestore
    from firebase_config import db
    job_id = str(uuid.uuid4())
    db.collection('tournament_jobs').document(job_id).set({
        'type': job_type,
        'name': name,
        'status': 'queued',
        'tournament_id': None,
        'winner': None,
        'error': None,
        'created_by': created_by,
        'created_at': firestore.SERVER_TIMESTAMP,
        'updated_at': firestore.SERVER_TIMESTAMP,
    })
    return job_id


def _update_tournament_job(job_id, **fields):
    """Patch a tournament job document, refreshing its heartbeat timestamp."""
    from firebase_admin import firestore
    from firebase_config import db
    fields['updated_at'] = firestore.SERVER_TIMESTAMP
    try:
        db.collection('tournament_jobs').document(job_id).update(fields)
    except Exception as e:
        app.logger.error(f"Failed to update tournament job {job_id}: {e}")


def _active_tournament_job_exists():
    """True if a tournament job is currently queued or running.

    Free-tier PythonAnywhere has a single worker and a tight CPU budget, so we
    only allow one tournament at a time. A query failure is treated as
    'no active job' so a transient error never blocks running a tournament.
    """
    from firebase_config import db
    try:
        for status in ('queued', 'running'):
            for _ in db.collection('tournament_jobs').where('status', '==', status).limit(1).stream():
                return True
    except Exception as e:
        app.logger.error(f"Tournament job concurrency check failed: {e}")
    return False


def _run_tournament_job(job_id, job_type, config):
    """Daemon-thread worker: run the heavy tournament compute and record status."""
    try:
        _update_tournament_job(job_id, status='running')
        if job_type == 'nplayer':
            result = _run_nplayer_tournament_core(config)
        else:
            result = _run_2player_tournament_core(config)
        _update_tournament_job(
            job_id, status='completed',
            tournament_id=result['tournament_id'], winner=result['winner'],
        )
        app.logger.info(f"Tournament job {job_id} ({job_type}) completed -> {result['tournament_id']}")
    except Exception as e:
        # Log through the logger (raiseExceptions=False) rather than writing the
        # traceback straight to stderr, which can itself raise OSError if the worker's
        # stdout/stderr pipe was closed during a free-tier worker recycle.
        app.logger.exception(f"Tournament job {job_id} ({job_type}) failed: {e}")
        _update_tournament_job(job_id, status='failed', error=str(e))


def _start_tournament_job(job_type, name, created_by, config):
    """Create a job and dispatch it to a background daemon thread. Returns job id."""
    job_id = _create_tournament_job(job_type, name, created_by)
    thread = threading.Thread(
        target=_run_tournament_job,
        args=(job_id, job_type, config),
        daemon=True,
        name=f'tournament-job-{job_id}',
    )
    thread.start()
    return job_id


def _run_core_simulation(strategies, *, num_cores, scheduler, core_assignment_mode,
                         core_sim_seed):
    """Run the OS simulation on exactly the supplied strategies, returning result/config blocks.

    No screening / top-K selection: the user's submitted strategies *are* the pool.
    Homogeneous benchmarks each strategy on all N cores; heterogeneous enumerates every
    `combinations(strategies, num_cores)` mixture (each strategy at most once) and ranks by
    throughput.
    """
    from core_simulation import run_full_simulation, run_heterogeneous_simulation

    core_simulation_results = None
    core_simulation_heterogeneous = None
    pool = [{'name': s['name'], 'code': s['code']} for s in strategies]
    try:
        if core_assignment_mode == 'heterogeneous':
            core_simulation_heterogeneous = run_heterogeneous_simulation(
                pool, num_cores=num_cores, seed=core_sim_seed, scheduler=scheduler,
                max_combinations=MAX_COMBINATIONS)
        else:
            core_simulation_results = run_full_simulation(
                pool, num_cores=num_cores, seed=core_sim_seed, scheduler=scheduler)
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
    try:
        from core_simulation import build_core_trace, extract_strategy_func
        code_by_name = {s['name']: s['code'] for s in pool}

        def _layout_traces(layout, label):
            """One trace per workload profile for a given core layout."""
            named_funcs = [(n, extract_strategy_func(code_by_name.get(n, ''), seed=core_sim_seed))
                           for n in layout]
            if not named_funcs or not all(f for _, f in named_funcs):
                return []
            out = []
            for wl in WORKLOAD_PROFILES:
                tr = build_core_trace(named_funcs, num_cores=num_cores, seed=core_sim_seed,
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
            for s in pool[:TRACE_LAYOUT_CAP]:
                traces.extend(_layout_traces([s['name']] * num_cores, s['name']))
    except Exception as trace_error:
        app.logger.error(f"Core trace error: {trace_error}")

    core_simulation_config = {
        'simulate_cores': True,
        'num_cores': num_cores,
        'scheduler': scheduler,
        'core_assignment_mode': core_assignment_mode,
        'selected_strategies': [s['name'] for s in pool],
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
                                   core_assignment_mode, seed):
    """Run the OS core simulation on its own (no tournament needed).

    Runs on exactly the supplied strategies — no screening, no top-K. Returns the three
    `core_simulation_*` blocks (the shape the results renderer expects).
    """
    return _run_core_simulation(
        strategies, num_cores=num_cores, scheduler=scheduler,
        core_assignment_mode=core_assignment_mode, core_sim_seed=seed)


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
    # MAX_COMBINATIONS still bounds the cores×assignment blow-up below.
    strategies = data.get('strategies', [])
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

    try:
        result = run_standalone_core_simulation(
            clean, num_cores=num_cores, scheduler=scheduler,
            core_assignment_mode=core_assignment_mode, seed=seed)
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

    weighted_leaderboard, weighted_winner = determine_weighted_results(result.get('leaderboard', []), weights)

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








def determine_weighted_results(leaderboard, weights):
    """Calculate weighted scores and return sorted leaderboard and winner"""
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
    
    # Sort by weighted score descending
    sorted_leaderboard = sorted(weighted_leaderboard, key=lambda x: x['weighted_score'], reverse=True)
    
    # Winner is the first entry
    winner = sorted_leaderboard[0]['name'] if sorted_leaderboard else "No participants"
    
    return sorted_leaderboard, winner










def _parse_iso_datetime_utc(value):
    """Parse an ISO-8601 datetime string into a timezone-aware UTC datetime."""
    from datetime import datetime, timezone

    if not isinstance(value, str) or not value.strip():
        raise ValueError('Expected non-empty ISO datetime string')

    text = value.strip()
    # Support common 'Z' suffix for UTC.
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'

    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _is_revealed(doc_dict, now=None):
    """True if a result document is publicly visible.

    Scheduled tournaments are computed immediately but stored hidden with a
    ``reveal_at`` datetime; they only become public once that time passes. Docs
    without ``reveal_at`` (ordinary manual runs) are always revealed.
    """
    from datetime import datetime, timezone

    reveal_at = (doc_dict or {}).get('reveal_at')
    if reveal_at is None:
        return True
    if now is None:
        now = datetime.now(timezone.utc)
    # Firestore returns tz-aware datetimes; guard against naive values just in case.
    if isinstance(reveal_at, datetime) and reveal_at.tzinfo is None:
        reveal_at = reveal_at.replace(tzinfo=timezone.utc)
    try:
        return reveal_at <= now
    except TypeError:
        # Unexpected type -> fail closed (treat as not yet revealed).
        return False


def _datetime_to_iso_z(value):
    """Convert Firestore/Python datetime objects to compact ISO-8601 '...Z' strings."""
    from datetime import datetime, timezone

    if value is None:
        return None

    # Most Firestore timestamp fields arrive as datetime.
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc).replace(microsecond=0)
        return dt.isoformat().replace('+00:00', 'Z')

    # If it's already JSON-safe (string/number), return as-is.
    return value








def _execute_2player_scheduled_tournament(name, config, reveal_at=None, schedule_id=None):
    """Run a 2-player tournament from a scheduled config dict.

    The compute happens now (at schedule-creation time), but the stored result is
    kept hidden until ``reveal_at`` so the public results pages only unlock it once
    the scheduled time arrives. Returns ``(success, error_str, result_id)``.
    """
    from firebase_config import db
    from datetime import datetime, timezone

    selected_ids = config.get('selected_ids', [])
    rounds = config.get('rounds') or 200
    payoff_matrix = config.get('payoff_matrix')
    modes = config.get('modes') or ['standard']
    discount_factor = float(config.get('discount_factor') or 0.95)
    stochastic_prob = float(config.get('stochastic_prob') or 0.995)
    weights = config.get('weights') or {'win_rate': 0.33, 'cooperation': 0.34, 'points': 0.33}
    seed = config.get('seed')

    strategies = []
    for doc in db.collection('tournament_strategies').stream():
        if selected_ids and doc.id not in selected_ids:
            continue
        d = doc.to_dict()
        strategies.append({
            'name': d.get('name', 'Unnamed'),
            'code': d.get('code', 'return "C"'),
            'player_email': d.get('user_email', 'Unknown'),
            'player_name': d.get('user_display_name', 'Unknown Player'),
            'user_id': doc.id,
        })

    if len(strategies) < 2:
        return False, f'Only {len(strategies)} strategies available (need ≥2)', None

    result = round_robin_tournament(
        strategies, rounds, payoff_matrix,
        modes=modes, discount_factor=discount_factor,
        stochastic_prob=stochastic_prob, weights=weights, seed=seed,
    )

    winner = result['leaderboard'][0]['name'] if result.get('leaderboard') else 'Unknown'
    tournament_data = {
        'name': name,
        'leaderboard': result['leaderboard'],
        'total_matches': result['total_matches'],
        'run_date': datetime.now(timezone.utc).isoformat(),
        'rounds': rounds,
        'modes': modes,
        'weights': weights,
        'winner': winner,
        'participants': [{'name': s['name']} for s in strategies],
        'participant_count': len(strategies),
        'from_schedule': True,
    }

    # Hide the result until its scheduled reveal time. Stored as a real datetime
    # so the public read endpoints can compare it against "now".
    if reveal_at is not None:
        tournament_data['reveal_at'] = reveal_at
        tournament_data['hidden'] = True
    if schedule_id is not None:
        tournament_data['schedule_id'] = schedule_id
    _ts, doc_ref = db.collection('tournament_results').add(tournament_data)
    return True, None, doc_ref.id


def _execute_nplayer_scheduled_tournament(name, config, reveal_at=None, schedule_id=None):
    """Run an N-player tournament from a scheduled config dict.

    Computes now but keeps the result hidden until ``reveal_at`` (see the 2-player
    counterpart). Returns ``(success, error_str, result_id)``.
    """
    import uuid
    from firebase_admin import firestore as _fs
    from firebase_config import db
    from datetime import datetime, timezone

    rounds = int(config.get('rounds') or 200)
    selected_ids = config.get('selected_ids', [])
    weights = config.get('weights') or {'win_rate': 0.33, 'cooperation': 0.34, 'points': 0.33}
    payoff_model_config = config.get('payoff_model', None)
    mode = config.get('mode', 'standard')
    modes = config.get('modes', None)
    group_size = config.get('group_size', None)
    seed = config.get('seed', None)
    discount_factor = float(config.get('discount_factor') or 0.95)
    stochastic_prob = float(config.get('stochastic_prob') or 0.995)

    context_modes = modes if modes is not None else [mode]

    # Load strategies
    strategies = []
    for doc in db.collection('tournament_strategies').stream():
        if selected_ids and doc.id not in selected_ids:
            continue
        d = doc.to_dict()
        strategies.append({
            'name': d.get('name', 'Unnamed'),
            'code': d.get('code', 'return "C"'),
            'player_email': d.get('user_email', 'Unknown'),
            'player_name': d.get('user_display_name', 'Unknown Player'),
            'user_id': doc.id,
        })

    if len(strategies) < 1:
        return False, f'Only {len(strategies)} strategies available (need ≥1)', None

    # Parse payoff model
    try:
        payoff_model = parse_payoff_model(payoff_model_config)
    except Exception as e:
        return False, f'Invalid payoff model: {e}', None

    # Compile strategies
    compiled_strategies = []
    for strat in strategies:
        code = strat['code']
        strat_name = strat['name']
        safe, error = is_safe_code(code)
        if not safe:
            return False, f'Strategy "{strat_name}" failed safety check: {error}', None
        rng = random.Random(seed) if seed is not None else None
        tournament_info = {
            'weights': weights,
            'payoff_model': payoff_model_config.get('type') if payoff_model_config else 'pairwise_matrix',
            'format': 'n-player',
            'n_players': len(strategies),
            'group_size': group_size,
            'mode': context_modes[0] if context_modes else mode,
            'modes': context_modes,
            'discount_factor': discount_factor,
            'stochastic_prob': stochastic_prob,
        }
        globals_dict = get_safe_globals({'TOURNAMENT_INFO': tournament_info}, rng)
        exec(code, globals_dict)
        strategy_func = extract_strategy_function(code, globals_dict, prefer_n_args=4)
        if not callable(strategy_func):
            return False, f'Strategy "{strat_name}" does not define a callable function', None
        compiled_strategies.append({'name': strat_name, 'code': code, 'func': strategy_func})

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

    weighted_leaderboard, weighted_winner = determine_weighted_results(result.get('leaderboard', []), weights)

    participants = [{'name': s['name']} for s in strategies]
    tournament_id = str(uuid.uuid4())
    tournament_doc = {
        'name': name,
        'format': 'n-player',
        'created_at': _fs.SERVER_TIMESTAMP,
        'rounds': rounds,
        'group_size': group_size,
        'seed': seed,
        'payoff_model': payoff_model_config,
        'weights': weights,
        'mode': result.get('tournament_info', {}).get('mode', mode),
        'modes': result.get('tournament_info', {}).get('modes', context_modes),
        'discount_factor': discount_factor,
        'stochastic_prob': stochastic_prob,
        'participants': participants,
        'results': weighted_leaderboard,
        'leaderboard': weighted_leaderboard,
        'matches': result.get('matches', []),
        'tournament_info': result.get('tournament_info', {}),
        'winner': weighted_winner,
        'participant_count': len(strategies),
        'total_matches': result.get('tournament_info', {}).get('n_groups', 1),
        'from_schedule': True,
    }

    # Hide the result until its scheduled reveal time (see 2-player counterpart).
    if reveal_at is not None:
        tournament_doc['reveal_at'] = reveal_at
        tournament_doc['hidden'] = True
    if schedule_id is not None:
        tournament_doc['schedule_id'] = schedule_id

    from firestore_utils import to_firestore_safe
    db.collection('nplayer_tournament_results').document(tournament_id).set(to_firestore_safe(tournament_doc))
    return True, None, tournament_id


def _execute_os_simulation_scheduled(name, config, reveal_at=None, schedule_id=None):
    """Run a standalone OS simulation from a scheduled config dict.

    Computes now but keeps the result hidden until ``reveal_at`` (mirrors the tournament
    executors). Returns ``(success, error_str, result_id)``.
    """
    import uuid
    from firebase_admin import firestore as _fs
    from firebase_config import db
    from firestore_utils import to_firestore_safe, encode_core_sim_traces

    selected_ids = config.get('selected_ids', [])
    num_cores = int(config.get('num_cores', 2))
    scheduler = config.get('scheduler', 'round_robin')
    core_assignment_mode = config.get('core_assignment_mode', 'homogeneous')
    seed = config.get('seed', None)

    # Resolve selected participants to {name, code}.
    strategies = []
    for doc in db.collection('tournament_strategies').stream():
        if selected_ids and doc.id not in selected_ids:
            continue
        d = doc.to_dict()
        strategies.append({'name': d.get('name', 'Unnamed'), 'code': d.get('code', 'return "C"')})
    if len(strategies) < 2:
        return False, f'Only {len(strategies)} strategies available (need ≥2)', None
    if core_assignment_mode == 'heterogeneous':
        het_error = _heterogeneous_combination_error(num_cores, len(strategies))
        if het_error:
            return False, het_error, None

    try:
        result = run_standalone_core_simulation(
            strategies, num_cores=num_cores, scheduler=scheduler,
            core_assignment_mode=core_assignment_mode, seed=seed)
    except Exception as e:
        return False, f'OS simulation failed: {e}', None

    sim_id = str(uuid.uuid4())
    sim_doc = {
        'name': name,
        'format': 'os-simulation',
        'created_at': _fs.SERVER_TIMESTAMP,
        'num_cores': num_cores,
        'scheduler': scheduler,
        'core_assignment_mode': core_assignment_mode,
        'seed': seed,
        'participants': [s['name'] for s in strategies],
        'core_simulation_results': result.get('core_simulation_results'),
        'core_simulation_heterogeneous': result.get('core_simulation_heterogeneous'),
        # Firestore-safe trace encoding (arrays-of-arrays are rejected otherwise).
        'core_simulation_config': encode_core_sim_traces(result.get('core_simulation_config')),
    }
    if reveal_at is not None:
        sim_doc['reveal_at'] = reveal_at
        sim_doc['hidden'] = True
    if schedule_id is not None:
        sim_doc['schedule_id'] = schedule_id
    db.collection('os_simulation_results').document(sim_id).set(to_firestore_safe(sim_doc))
    return True, None, sim_id


# Maps a schedule collection to the function that executes its tournaments. Used by
# the eager "run now, reveal later" dispatcher below.
_SCHEDULE_EXECUTORS = {
    'tournament_schedule': _execute_2player_scheduled_tournament,
    'nplayer_tournament_schedule': _execute_nplayer_scheduled_tournament,
    'os_simulation_schedule': _execute_os_simulation_scheduled,
}


def _run_scheduled_tournament_now(collection, schedule_id, name, config, reveal_at):
    """Compute a scheduled tournament immediately and record its outcome.

    The result document is written hidden until ``reveal_at`` (the schedule's
    ``scheduled_for``); we only track running/completed/failed status on the
    schedule doc here. Designed to run on a daemon thread so it can't hit the
    ~5-min web-request limit on PythonAnywhere free tier.
    """
    from firebase_admin import firestore as _fs
    from firebase_config import db

    executor = _SCHEDULE_EXECUTORS[collection]
    schedule_ref = db.collection(collection).document(schedule_id)
    try:
        schedule_ref.update({'status': 'running'})
        ok, err, result_id = executor(name, config, reveal_at=reveal_at, schedule_id=schedule_id)
        if ok:
            schedule_ref.update({
                'status': 'completed',
                'result_id': result_id,
                'executed_at': _fs.SERVER_TIMESTAMP,
            })
            app.logger.info(f"Scheduled tournament '{name}' computed -> result {result_id} (reveals at {reveal_at})")
        else:
            schedule_ref.update({'status': 'failed', 'error': err})
            app.logger.error(f"Scheduled tournament '{name}' failed: {err}")
    except Exception as exc:
        app.logger.exception(f"Scheduled tournament '{name}' raised: {exc}")
        try:
            schedule_ref.update({'status': 'failed', 'error': str(exc)})
        except Exception:
            pass


def _dispatch_scheduled_tournament(collection, schedule_id, name, config, reveal_at):
    """Run a scheduled tournament now: inline under the test sync flag, else on a
    background daemon thread (mirrors _start_tournament_job)."""
    if app.config.get('RUN_TOURNAMENTS_SYNC'):
        _run_scheduled_tournament_now(collection, schedule_id, name, config, reveal_at)
        return
    thread = threading.Thread(
        target=_run_scheduled_tournament_now,
        args=(collection, schedule_id, name, config, reveal_at),
        daemon=True,
        name=f'scheduled-tournament-{schedule_id}',
    )
    thread.start()






























def _replay_nplayer_tournament(package, db):
    """Helper function to replay N-player tournament from package (stub - not yet implemented)"""
    return jsonify({
        'error': 'N-player tournament replay is not yet implemented. Please use 2-player tournament replay.'
    }), 501


def _replay_2player_tournament(package, db):
    """Helper function to replay 2-player tournament from package"""
    from firebase_admin import firestore
    import uuid
    from tournament_package import extract_run_config_from_package
    
    try:
        # Extract configuration from package
        config = extract_run_config_from_package(package)
        
        # Build strategies list from package participants
        strategies = []
        for participant in package['participants']:
            # Skip participants without code (redacted packages)
            if 'code' not in participant or participant['code'] is None:
                return jsonify({'error': 'Cannot replay package without strategy code'}), 400
            
            # Validate code safety
            code = participant['code']
            safe, safety_error = is_safe_code(code)
            if not safe:
                return jsonify({
                    'error': f"Unsafe code in participant '{participant['label']}': {safety_error}"
                }), 400
            
            strategies.append({
                'name': participant['label'],
                'code': code,
                'player_email': participant.get('email') or 'replayed@system.local',
                'player_name': participant.get('display_name') or f"Replayed_{participant['label']}",
                'user_id': participant['stable_id']
            })
        
        if len(strategies) < 2:
            return jsonify({'error': 'At least 2 strategies with code required to replay tournament'}), 400
        
        # Extract tournament configuration
        tournament_name = config['name']
        rounds = config.get('rounds') or 200
        modes = config['modes']
        discount_factor = config.get('discount_factor') or 0.95
        stochastic_prob = config.get('stochastic_prob') or 0.995
        payoff_matrix = config['payoff_matrix']
        weights = config['weights']
        
        # Extract seed for determinism
        seed_info = config.get('seed_info')
        tournament_seed = seed_info.get('tournament_seed') if seed_info else None
        
        # Run tournament engine
        app.logger.info(f"Replaying tournament '{tournament_name}' with {len(strategies)} strategies")
        tournament_result = round_robin_tournament(
            strategies,
            rounds,
            payoff_matrix,
            modes=modes,
            discount_factor=discount_factor,
            stochastic_prob=stochastic_prob,
            weights=weights,
            seed=tournament_seed
        )
        
        # Check if tournament ran successfully
        if 'error' in tournament_result:
            return jsonify({'error': f"Tournament failed: {tournament_result['error']}"}), 500
        
        # Extract winner from leaderboard (first place)
        leaderboard = tournament_result['leaderboard']
        winner = leaderboard[0]['name'] if leaderboard else 'Unknown'
        
        # Store result in Firestore with new tournament_id
        new_tournament_id = str(uuid.uuid4())
        
        tournament_doc = {
            'name': tournament_name,
            'rounds': rounds,
            'modes': modes,
            'discount_factor': discount_factor,
            'stochastic_prob': stochastic_prob,
            'payoff_matrix': payoff_matrix,
            'weights': weights,
            'seed_info': seed_info,
            'participants': strategies,
            'winner': winner,
            'leaderboard': leaderboard,
            'matches': tournament_result.get('matches', []),
            'total_matches': tournament_result['total_matches'],
            'participant_count': len(strategies),
            'run_date': firestore.SERVER_TIMESTAMP,
            'run_by': session['user']['uid'],
            'replayed_from_package': True,
            'original_package_sha256': package['integrity']['package_sha256'],
            # Core-sim fields (not used in replay but kept for schema consistency)
            'core_simulation_config': None,
            'core_simulation_results': None
        }
        
        db.collection('tournament_results').document(new_tournament_id).set(tournament_doc)
        
        # Log audit event
        context = get_request_context(request)
        log_audit_event(
            action='tournament_replayed',
            actor_uid=session['user']['uid'],
            actor_email=session['user']['email'],
            actor_role=session['user'].get('role', 'admin'),
            remote_ip=context['remote_ip'],
            user_agent=context['user_agent'],
            success=True,
            metadata={
                'tournament_id': new_tournament_id,
                'package_sha256': package['integrity']['package_sha256'],
                'original_tournament_id': package.get('source', {}).get('tournament_id'),
                'tournament_name': tournament_name,
                'participant_count': len(strategies)
            }
        )
        
        # Return success with results
        return jsonify({
            'success': True,
            'tournament_id': new_tournament_id,
            'winner': winner,
            'leaderboard': leaderboard
        }), 200
        
    except ValueError as e:
        # ValueError typically comes from validation or expected checks
        app.logger.warning(f"Replay validation error: {str(e)}")
        return jsonify({'error': str(e)}), 400
    
    except Exception as e:
        # Unexpected errors
        app.logger.error(f"Replay unexpected error: {str(e)}", exc_info=True)
        return jsonify({'error': 'Failed to replay tournament. Please check package validity.'}), 500






def _recover_stale_tournament_jobs():
    """Mark abandoned async tournament jobs as failed.

    Scheduled tournaments no longer need a background runner -- they compute at
    schedule-creation time and reveal at their scheduled_for (see
    _dispatch_scheduled_tournament). This periodic task only protects the separate
    manual async-run path: a free-tier worker can be recycled mid-run, leaving a
    'tournament_jobs' entry stuck in 'queued'/'running' forever (the UI would poll
    it indefinitely). Mark such jobs failed once their heartbeat is too old.
    """
    from firebase_admin import firestore as _fs
    from firebase_config import db
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    try:
        cutoff = now - timedelta(seconds=TOURNAMENT_JOB_TIMEOUT_SECONDS)
        for status in ('queued', 'running'):
            for doc in db.collection('tournament_jobs').where('status', '==', status).stream():
                d = doc.to_dict()
                updated = d.get('updated_at')
                if updated is not None and updated < cutoff:
                    doc.reference.update({
                        'status': 'failed',
                        'error': 'Worker timed out or was recycled before the tournament finished.',
                        'updated_at': _fs.SERVER_TIMESTAMP,
                    })
                    app.logger.warning(f"Marked stale tournament job {doc.id} as failed")
    except Exception as e:
        app.logger.error(f"Stale tournament-job recovery error: {e}")


def _scheduler_loop():
    import time
    while True:
        time.sleep(60)
        try:
            _recover_stale_tournament_jobs()
        except Exception as e:
            app.logger.error(f"Scheduler loop error: {e}")


import threading as _threading
_scheduler_thread = _threading.Thread(target=_scheduler_loop, daemon=True, name='tournament-job-recovery')
_scheduler_thread.start()


if __name__ == '__main__':
    # Environment-based debug mode
    # Default to False for production safety
    # Set FLASK_DEBUG=True environment variable to enable debug mode
    debug_mode = os.environ.get('FLASK_DEBUG', 'False') == 'True'
    
    if debug_mode:
        print("WARNING: Debug mode is ENABLED. Only use this in development!")
        print("Set FLASK_DEBUG=False or remove the variable for production.")
    else:
        print("Debug mode is disabled (production-safe)")
    
    host = '127.0.0.1'
    port = 5000
    print(f"Starting server on http://{host}:{port}")
    app.run(host=host, port=port, debug=debug_mode)