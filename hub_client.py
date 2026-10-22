"""Thin client the LOCAL app uses to FETCH strategies from the website.

The website (ipd-app in HUB_MODE) serves all submitted strategies over HTTP. This
client lets the local app pull that list and individual strategies so a student can
practice against them -- with no Firebase and no credentials on the local machine.

There is intentionally NO publish/upload here: submitting a strategy and uploading
results happen ON the website (where login lives), not from the local app.

This module only does HTTP. The real security boundary is local: any code pulled from
the website is untrusted and MUST be run through is_safe_code (app.py) before it is
executed. The website's server-side screen is convenience, not the gate.

Config comes from the environment (loaded from .env by app.py before this is imported):
    HUB_BASE_URL   the website's URL, e.g. https://cnf.pythonanywhere.com (blank => disabled)
    HUB_API_TOKEN  shared class token that must match the website's HUB_API_TOKEN
"""
import os
import requests

from version import APP_VERSION

HUB_BASE_URL = os.environ.get('HUB_BASE_URL', '').strip().rstrip('/')
HUB_API_TOKEN = os.environ.get('HUB_API_TOKEN', '').strip()

# Network calls are bounded so a slow/unreachable website never hangs a local request.
_TIMEOUT = 15


class HubError(Exception):
    """Raised for any transport or protocol error; carries a user-safe message."""


def hub_enabled():
    """True only when the website URL is configured. Routes use this to hide the
    feature cleanly when the student hasn't pointed at a website."""
    return bool(HUB_BASE_URL)


def _headers():
    # The website checks this and refuses apps that are too old (HTTP 426).
    headers = {'Accept': 'application/json', 'X-IPD-App-Version': APP_VERSION}
    if HUB_API_TOKEN:
        headers['Authorization'] = f'Bearer {HUB_API_TOKEN}'
    return headers


def _require_enabled():
    if not hub_enabled():
        raise HubError('Not connected to the class website yet. Connect on the Settings page.')


def configure(base_url, token):
    """Point this client at a website for the rest of the session (the Hub page's
    "Connect to the class website" form; app.py also saves the values to .env)."""
    global HUB_BASE_URL, HUB_API_TOKEN
    HUB_BASE_URL = (base_url or '').strip().rstrip('/')
    HUB_API_TOKEN = (token or '').strip()


def check_connection(base_url, token):
    """Try the website with these details before they are saved. Raises HubError with a
    message a student can act on; returns None when the website accepts the token."""
    url = f"{base_url.rstrip('/')}/api/strategies"
    try:
        resp = requests.get(url, headers={'Accept': 'application/json', 'X-IPD-App-Version': APP_VERSION,
                                          'Authorization': f'Bearer {token}'}, timeout=_TIMEOUT)
    except requests.RequestException:
        raise HubError('Could not reach that address. Check it is typed correctly and that '
                       'you are online.')
    if resp.status_code == 426:
        raise HubError(_error_text(resp))
    if resp.status_code == 401:
        raise HubError('The website did not accept that token. Check it with your instructor.')
    if resp.status_code == 404:
        raise HubError("That address doesn't look like the class website (no strategy list "
                       "there). Check it with your instructor.")
    if resp.status_code != 200:
        raise HubError(f'The website answered with an error ({resp.status_code}). Try again later.')


def _error_text(resp):
    """The website's own message for a refused request (e.g. 'Your app is too old ...')."""
    try:
        return str(resp.json().get('error') or resp.text)
    except ValueError:
        return resp.text or f'error {resp.status_code}'


def _get_json(path):
    """GET a token-gated website path and return its JSON, raising HubError with a clear message."""
    _require_enabled()
    try:
        resp = requests.get(f'{HUB_BASE_URL}{path}', headers=_headers(), timeout=_TIMEOUT)
    except requests.RequestException as e:
        raise HubError(f'Could not reach the website: {e}')
    if resp.status_code == 426:
        raise HubError(_error_text(resp))          # app too old: say so, with the download link
    if resp.status_code >= 400:
        raise HubError(f'The website answered with an error ({resp.status_code}): {_error_text(resp)}')
    try:
        return resp.json()
    except ValueError:
        raise HubError('Website returned a malformed response.')


def list_snapshots():
    """Frozen copies of every submission, taken by an admin: [{id, label, count, created_at}]."""
    data = _get_json('/api/snapshots')
    return data.get('snapshots', []) if isinstance(data, dict) else []


def list_snapshot_strategies(snapshot_id):
    """(snapshot meta, strategies) for one snapshot, strategies shaped like list_strategies()."""
    from urllib.parse import quote
    data = _get_json(f'/api/snapshots/{quote(str(snapshot_id), safe="")}/strategies')
    if not isinstance(data, dict):
        raise HubError('Website returned a malformed response.')
    return data.get('snapshot') or {}, data.get('strategies') or []


def list_strategies():
    """Return the submitted strategies as a list of {id, name, code, type, flagged, ...}.
    (The website no longer sends author names: every student holds the class token.)"""
    data = _get_json('/api/strategies')
    # Accept either a bare list or {"strategies": [...]} for forward-compatibility.
    return data.get('strategies', data) if isinstance(data, dict) else data


def list_practice_strategies():
    """Return organizer-provided PRACTICE strategies as [{id, name, code}]."""
    data = _get_json('/api/practice-strategies')
    return data.get('strategies', data) if isinstance(data, dict) else data


def stage_result(package):
    """Stage a locally-run tournament result on the website for admin publishing.
    Returns the draft_id. Uses the shared class token (no per-user auth); the actual
    publish on the website is admin-login-gated."""
    _require_enabled()
    try:
        resp = requests.post(f'{HUB_BASE_URL}/api/result-draft', json=package,
                             headers=_headers(), timeout=_TIMEOUT)
    except requests.RequestException as e:
        raise HubError(f'Could not reach the website: {e}')
    if resp.status_code == 426:
        raise HubError(_error_text(resp))
    if resp.status_code >= 400:
        raise HubError(f'Website rejected the result ({resp.status_code}): {_error_text(resp)}')
    try:
        return resp.json().get('draft_id')
    except ValueError:
        raise HubError('Website returned a malformed response.')


def get_strategy(strategy_id):
    """Return the full record for one strategy, including its `code` field."""
    from urllib.parse import quote
    return _get_json(f'/api/strategies/{quote(str(strategy_id), safe="")}')
