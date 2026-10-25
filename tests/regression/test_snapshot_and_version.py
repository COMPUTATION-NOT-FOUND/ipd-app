"""The app sends its version to the website, shows the website's 'too old' message, and can load
a strategy snapshot (frozen for judging) into the gallery instead of the live submissions."""
from unittest.mock import MagicMock

import pytest

import app as ipd
import hub_client
from version import APP_VERSION

pytestmark = pytest.mark.regression

FROZEN = [{'id': 'p_1', 'name': 'old_tft', 'type': 'tournament', 'submitted_at': '2026-10-23T10:00:00Z',
           'code': "def t(last_moves, my_history, opponents_histories, meta):\n    return 'C'"}]
LIVE = [{'id': 'p_1', 'name': 'new_tft', 'type': 'tournament',
         'code': "def t(last_moves, my_history, opponents_histories, meta):\n    return 'D'"}]


def test_every_website_call_sends_the_app_version(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen['headers'] = headers
        r = MagicMock(status_code=200)
        r.json.return_value = {'strategies': []}
        return r
    monkeypatch.setattr(hub_client.requests, 'get', fake_get)
    monkeypatch.setattr(hub_client, 'HUB_BASE_URL', 'https://site.example')
    hub_client.list_strategies()
    assert seen['headers']['X-IPD-App-Version'] == APP_VERSION == '1.4.3'


def test_too_old_message_from_the_website_is_shown_as_is(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        r = MagicMock(status_code=426)
        r.json.return_value = {'error': 'Your app is too old. Download version 1.4.0.'}
        return r
    monkeypatch.setattr(hub_client.requests, 'get', fake_get)
    monkeypatch.setattr(hub_client, 'HUB_BASE_URL', 'https://site.example')
    with pytest.raises(hub_client.HubError, match='Download version 1.4.0'):
        hub_client.list_strategies()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ipd, 'STRATEGY_CACHE_PATH', str(tmp_path / 'strategy_cache.json'))
    monkeypatch.setattr(ipd, 'PRACTICE_CACHE_PATH', str(tmp_path / 'practice_cache.json'))
    monkeypatch.setattr(ipd, 'GALLERY_SOURCE_PATH', str(tmp_path / 'gallery_source.json'))
    monkeypatch.setattr(hub_client, 'list_strategies', lambda: LIVE)
    monkeypatch.setattr(hub_client, 'list_practice_strategies', lambda: [])
    monkeypatch.setattr(hub_client, 'list_snapshot_strategies',
                        lambda sid: ({'id': sid, 'label': 'Sprint 1 end', 'created_at': '2026-10-23T18:00:00Z'}, FROZEN))
    return tmp_path


def test_loading_a_snapshot_uses_the_frozen_strategies(client, data_dir):
    resp = client.post('/hub/refresh', json={'source': 'snapshot', 'snapshot_id': 'snap1'})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['source']['label'] == 'Sprint 1 end' and data['strategies'][0]['name'] == 'old_tft'
    # A plain Refresh later keeps using the chosen snapshot.
    again = client.post('/hub/refresh').get_json()
    assert again['source']['source'] == 'snapshot' and again['strategies'][0]['name'] == 'old_tft'
    assert client.get('/hub/list').get_json()['source']['snapshot_id'] == 'snap1'
    # Runs use the frozen code and its submission time.
    result = client.post('/tournament', json={'rounds': 5, 'strategies': [
        {'name': 'old_tft', 'website_id': 'p_1'},
        {'name': 'me', 'code': "def m(last_moves, my_history, opponents_histories, meta):\n    return 'C'"}]}).get_json()
    old = next(e for e in result['leaderboard'] if e['name'] == 'old_tft')
    assert old['cooperation_percentage'] == 100 and old['submitted_at'] == '2026-10-23T10:00:00Z'


def test_switching_back_to_live(client, data_dir):
    client.post('/hub/refresh', json={'source': 'snapshot', 'snapshot_id': 'snap1'})
    data = client.post('/hub/refresh', json={'source': 'live'}).get_json()
    assert data['source'] == {'source': 'live'} and data['strategies'][0]['name'] == 'new_tft'
