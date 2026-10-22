"""Classmates' code is not shown in the app (student report, Oct 2026: after Import, another
student's code was visible). Bots and practice strategies keep their code."""
import json

import pytest

import app as ipd
import code_vault

pytestmark = pytest.mark.security

SECRET = "def secret_sauce(last_moves, my_history, opponents_histories, meta):\n    return 'D'"
BOT = "def bot(last_moves, my_history, opponents_histories, meta):\n    return 'C'"
PRACTICE = "def tft(last_moves, my_history, opponents_histories, meta):\n    return last_moves[0] if last_moves else 'C'"
STRATS = [
    {'id': 'p1', 'name': 'classmate', 'code': SECRET, 'type': 'tournament',
     'submitted_at': '2026-10-21T10:00:00Z'},
    {'id': 'bot_1', 'name': 'house', 'code': BOT, 'type': 'bot'},
]
PRACT = [{'id': 'pr1', 'name': 'practice_tft', 'code': PRACTICE}]


@pytest.fixture
def gallery(tmp_path, monkeypatch):
    monkeypatch.setattr(ipd, 'STRATEGY_CACHE_PATH', str(tmp_path / 'strategy_cache.json'))
    monkeypatch.setattr(ipd, 'PRACTICE_CACHE_PATH', str(tmp_path / 'practice_cache.json'))
    ipd._write_strategy_cache(STRATS)
    ipd._write_practice_cache(PRACT)
    return tmp_path


def test_cache_file_is_not_plain_text(gallery):
    raw = (gallery / 'strategy_cache.json').read_bytes()
    assert b'secret_sauce' not in raw and b'classmate' not in raw
    assert ipd._read_strategy_cache() == STRATS


def test_old_plain_cache_is_read_and_rewritten_scrambled(gallery):
    path = gallery / 'strategy_cache.json'
    path.write_text(json.dumps(STRATS), encoding='utf-8')
    assert ipd._read_strategy_cache() == STRATS
    assert path.read_bytes().startswith(code_vault.MAGIC)


def test_list_hides_classmates_code_only(client, gallery):
    body = client.get('/hub/list').get_data(as_text=True)
    assert 'secret_sauce' not in body
    data = json.loads(body)
    by_id = {s['id']: s for s in data['strategies']}
    assert by_id['p1']['hidden'] is True and 'code' not in by_id['p1']
    assert by_id['bot_1']['code'] == BOT                 # bots are meant to be studied
    assert data['practice'][0]['code'] == PRACTICE


def test_import_of_a_classmate_returns_no_code(client, gallery):
    resp = client.post('/hub/import/p1')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['hidden'] is True and 'code' not in data and data['id'] == 'p1'
    assert 'secret_sauce' not in resp.get_data(as_text=True)
    assert client.post('/hub/import/bot_1').get_json()['code'] == BOT


def test_run_by_id_matches_run_with_code(client, gallery):
    by_id = client.post('/tournament', json={'rounds': 10, 'seed': 1, 'strategies': [
        {'name': 'classmate', 'website_id': 'p1'}, {'name': 'house', 'code': BOT}]}).get_json()
    with_code = client.post('/tournament', json={'rounds': 10, 'seed': 1, 'strategies': [
        {'name': 'classmate', 'code': SECRET}, {'name': 'house', 'code': BOT}]}).get_json()
    strip = lambda r: [(e['name'], e['total_points']) for e in r['leaderboard']]   # noqa: E731
    assert strip(by_id) == strip(with_code)
    # The cached submission time rides along for the "earliest submission" tie-break.
    assert {e['name']: e['submitted_at'] for e in by_id['leaderboard']}['classmate'] == '2026-10-21T10:00:00Z'


def test_cached_code_wins_over_code_sent_by_the_page(client, gallery):
    result = client.post('/tournament', json={'rounds': 10, 'seed': 1, 'strategies': [
        {'name': 'classmate', 'website_id': 'p1', 'code': BOT}, {'name': 'house', 'code': BOT}]}).get_json()
    pts = {e['name']: e['total_points'] for e in result['leaderboard']}
    assert pts['classmate'] > pts['house']               # it played the cached all-D code


def test_unknown_id_asks_for_refresh(client, gallery):
    resp = client.post('/tournament', json={'rounds': 10, 'strategies': [
        {'name': 'gone', 'website_id': 'nope'}, {'name': 'house', 'code': BOT}]})
    assert resp.status_code == 400 and 'Refresh' in resp.get_json()['error']


def test_submission_time_from_the_page_is_ignored(client, gallery):
    result = client.post('/tournament', json={'rounds': 10, 'strategies': [
        {'name': 'a', 'code': BOT, 'submitted_at': '2000-01-01T00:00:00Z'},
        {'name': 'b', 'code': BOT}]}).get_json()
    assert all(e['submitted_at'] is None for e in result['leaderboard'])
