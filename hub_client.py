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
    headers = {'Accept': 'application/json'}
    if HUB_API_TOKEN:
        headers['Authorization'] = f'Bearer {HUB_API_TOKEN}'
    return headers


def _require_enabled():
    if not hub_enabled():
        raise HubError('Website is not configured. Set HUB_BASE_URL in your .env.')


def list_strategies():
    """Return the submitted strategies as a list of {id, name, code, author}."""
    _require_enabled()
    try:
        resp = requests.get(f'{HUB_BASE_URL}/api/strategies', headers=_headers(), timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        raise HubError(f'Could not reach the website: {e}')
    except ValueError:
        raise HubError('Website returned a malformed response.')
    # Accept either a bare list or {"strategies": [...]} for forward-compatibility.
    return data.get('strategies', data) if isinstance(data, dict) else data


def list_practice_strategies():
    """Return organizer-provided PRACTICE strategies as [{id, name, code, author}]."""
    _require_enabled()
    try:
        resp = requests.get(f'{HUB_BASE_URL}/api/practice-strategies', headers=_headers(), timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        raise HubError(f'Could not reach the website: {e}')
    except ValueError:
        raise HubError('Website returned a malformed response.')
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
    if resp.status_code >= 400:
        try:
            reason = resp.json().get('error', resp.text)
        except ValueError:
            reason = resp.text
        raise HubError(f'Website rejected the result ({resp.status_code}): {reason}')
    try:
        return resp.json().get('draft_id')
    except ValueError:
        raise HubError('Website returned a malformed response.')


def get_strategy(strategy_id):
    """Return the full record for one strategy, including its `code` field."""
    _require_enabled()
    try:
        resp = requests.get(
            f'{HUB_BASE_URL}/api/strategies/{strategy_id}',
            headers=_headers(), timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        raise HubError(f'Could not fetch strategy {strategy_id}: {e}')
    except ValueError:
        raise HubError('Website returned a malformed response.')
