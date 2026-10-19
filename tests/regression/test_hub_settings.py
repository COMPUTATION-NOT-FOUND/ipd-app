"""The Hub page's "Connect to the class website" form (POST /settings/hub).

Students set the website address + class token inside the app instead of editing .env. The
values are tested, saved to .env next to the app, and applied at once. Because the route changes
where the class token is sent, it must refuse cross-site requests and anything that could inject
extra lines into .env. DATA_DIR is redirected to a temp folder so the real .env is never touched.
"""
import pytest

import app as app_module
import hub_client

pytestmark = pytest.mark.regression

GOOD = {'base_url': 'https://class-site.example.com/', 'token': 'd9e965839b0a4c3f9e21'}


@pytest.fixture
def settings_env(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, 'DATA_DIR', str(tmp_path))
    calls = []
    monkeypatch.setattr(hub_client, 'check_connection', lambda url, tok: calls.append((url, tok)))
    # Leave the module-level config as we found it after each test.
    monkeypatch.setattr(hub_client, 'HUB_BASE_URL', hub_client.HUB_BASE_URL)
    monkeypatch.setattr(hub_client, 'HUB_API_TOKEN', hub_client.HUB_API_TOKEN)
    monkeypatch.setattr(app_module, 'HUB_BASE_URL', app_module.HUB_BASE_URL)
    monkeypatch.setattr(app_module, 'HUB_API_TOKEN', app_module.HUB_API_TOKEN)
    for k in ('HUB_BASE_URL', 'HUB_API_TOKEN'):
        monkeypatch.delenv(k, raising=False)
    return tmp_path, calls


def test_connect_tests_saves_and_applies(client, settings_env):
    tmp, calls = settings_env
    (tmp / '.env').write_text('# my settings\nIPD_PORT=5050\nHUB_BASE_URL=https://old.example.com\n', encoding='utf-8')
    resp = client.post('/settings/hub', json=GOOD)
    assert resp.status_code == 200 and resp.get_json()['success'] is True
    assert calls == [('https://class-site.example.com', GOOD['token'])]     # tested before saving
    env = (tmp / '.env').read_text(encoding='utf-8').splitlines()
    assert '# my settings' in env and 'IPD_PORT=5050' in env                  # other lines kept
    assert 'HUB_BASE_URL=https://class-site.example.com' in env               # replaced in place
    assert f"HUB_API_TOKEN={GOOD['token']}" in env                            # appended
    assert sum(l.startswith('HUB_BASE_URL=') for l in env) == 1
    assert hub_client.hub_enabled() and hub_client.HUB_API_TOKEN == GOOD['token']   # live now


def test_status_never_returns_the_token(client, settings_env):
    client.post('/settings/hub', json=GOOD)
    data = client.get('/settings/hub').get_json()
    assert data == {'connected': True, 'base_url': 'https://class-site.example.com'}
    assert GOOD['token'] not in client.get('/hub').get_data(as_text=True)


@pytest.mark.parametrize('payload', [
    {'base_url': 'https://site.example.com\nALLOW_LAN=true', 'token': GOOD['token']},   # .env injection
    {'base_url': GOOD['base_url'], 'token': 'abc\nALLOW_LAN=true'},
    {'base_url': 'http://site.example.com', 'token': GOOD['token']},                   # token over http
    {'base_url': 'site.example.com', 'token': GOOD['token']},
    {'base_url': 'javascript:alert(1)', 'token': GOOD['token']},
    {'base_url': GOOD['base_url'], 'token': 'short'},
    {'base_url': GOOD['base_url'], 'token': 'has spaces in it'},
])
def test_bad_values_are_rejected_and_nothing_saved(client, settings_env, payload):
    tmp, calls = settings_env
    resp = client.post('/settings/hub', json=payload)
    assert resp.status_code == 400
    assert calls == [] and not (tmp / '.env').exists()


def test_localhost_http_allowed_for_testing(client, settings_env):
    resp = client.post('/settings/hub', json={'base_url': 'http://127.0.0.1:8080', 'token': GOOD['token']})
    assert resp.status_code == 200


def test_cross_site_requests_are_refused(client, settings_env):
    tmp, calls = settings_env
    # A plain HTML form from another website (no JSON):
    assert client.post('/settings/hub', data=GOOD).status_code == 415
    # JSON with another site's Origin:
    assert client.post('/settings/hub', json=GOOD, headers={'Origin': 'https://evil.example'}).status_code == 403
    # DNS-rebinding style Host:
    assert client.post('/settings/hub', json=GOOD, headers={'Host': 'evil.example:5000'}).status_code == 403
    assert calls == [] and not (tmp / '.env').exists()
    # The app's own page (same Origin) is fine:
    assert client.post('/settings/hub', json=GOOD, headers={'Origin': 'http://localhost'}).status_code == 200


def test_failed_connection_is_reported_and_not_saved(client, settings_env, monkeypatch):
    tmp, _ = settings_env

    def refuse(url, tok):
        raise hub_client.HubError('The website did not accept that token. Check it with your instructor.')
    monkeypatch.setattr(hub_client, 'check_connection', refuse)
    resp = client.post('/settings/hub', json=GOOD)
    assert resp.status_code == 400 and 'did not accept that token' in resp.get_json()['error']
    assert not (tmp / '.env').exists()


def test_blank_token_keeps_the_saved_one(client, settings_env):
    tmp, calls = settings_env
    client.post('/settings/hub', json=GOOD)
    resp = client.post('/settings/hub', json={'base_url': 'https://new-site.example.com', 'token': ''})
    assert resp.status_code == 200
    assert calls[-1] == ('https://new-site.example.com', GOOD['token'])
    env = (tmp / '.env').read_text(encoding='utf-8')
    assert 'HUB_BASE_URL=https://new-site.example.com' in env and f"HUB_API_TOKEN={GOOD['token']}" in env


def test_blank_token_with_nothing_saved_is_refused(client, settings_env, monkeypatch):
    monkeypatch.setattr(hub_client, 'HUB_API_TOKEN', '')
    resp = client.post('/settings/hub', json={'base_url': GOOD['base_url'], 'token': ''})
    assert resp.status_code == 400 and 'token' in resp.get_json()['error']


def test_disconnect_clears_saved_and_live_settings(client, settings_env):
    tmp, _ = settings_env
    client.post('/settings/hub', json=GOOD)
    assert client.post('/settings/hub/disconnect', json={}).status_code == 200
    env = (tmp / '.env').read_text(encoding='utf-8').splitlines()
    assert 'HUB_BASE_URL=' in env and 'HUB_API_TOKEN=' in env
    assert not hub_client.hub_enabled() and hub_client.HUB_API_TOKEN == ''
    assert client.get('/settings/hub').get_json()['connected'] is False


def test_disconnect_refuses_cross_site(client, settings_env):
    client.post('/settings/hub', json=GOOD)
    assert client.post('/settings/hub/disconnect', data={}).status_code == 415
    assert client.post('/settings/hub/disconnect', json={}, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert hub_client.hub_enabled()


def test_settings_page_and_hub_link(client, settings_env):
    page = client.get('/settings').get_data(as_text=True)
    assert 'id="connBtn"' in page and str(settings_env[0]) in page        # form + where settings live
    client.post('/settings/hub', json=GOOD)
    page = client.get('/settings').get_data(as_text=True)
    assert 'Connected to' in page and GOOD['token'] not in page
    hub = client.get('/hub').get_data(as_text=True)
    assert 'href="/settings"' in hub and 'id="connBtn"' not in hub


def test_settings_from_beside_an_old_exe_are_copied_once(tmp_path, monkeypatch):
    """The downloaded app keeps settings in a per-user folder; files an older version kept next
    to the exe are copied in on first start (and never overwrite newer ones)."""
    old, new = tmp_path / 'exe_folder', tmp_path / 'user_settings'
    old.mkdir()
    (old / '.env').write_text('HUB_BASE_URL=https://old.example.com\n', encoding='utf-8')
    (old / 'local_gallery.json').write_text('[]', encoding='utf-8')
    monkeypatch.setattr(app_module, 'APP_DIR', str(old))
    monkeypatch.setenv('IPD_DATA_DIR', str(new))
    assert app_module._resolve_data_dir() == str(new)
    assert (new / '.env').read_text(encoding='utf-8') == 'HUB_BASE_URL=https://old.example.com\n'
    assert (new / 'local_gallery.json').exists()
    (new / '.env').write_text('HUB_BASE_URL=https://newer.example.com\n', encoding='utf-8')
    app_module._resolve_data_dir()                                           # second start
    assert 'newer' in (new / '.env').read_text(encoding='utf-8')
