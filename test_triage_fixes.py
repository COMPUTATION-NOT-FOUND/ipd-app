"""Regression tests for the student-bug-test triage fixes.

Covers: strict strategy/bot name validation, duplicate-name rejection on all three run routes,
the machine-local gallery (save/list/delete), the /check-auth endpoint, and the loopback-only
guard that keeps a LAN client from reaching the auto-admin local app.
"""
import pytest

from app import validate_name, check_unique_strategy_names


# --- strict name rule (now applied to strategy + bot names, both repos) ---

class TestStrictStrategyNames:
    @pytest.mark.parametrize("name", ["Hijab's strategy", "Claude's_strategy",
                                      "house_bot_3", "Grim Trigger", "tit-for-tat"])
    def test_accepts_real_names(self, name):
        ok, _ = validate_name(name, field_type="strategy name")
        assert ok, name

    @pytest.mark.parametrize("name", ["a<b", "a>b", "a&b", 'a"b', "x\x00y"])
    def test_rejects_markup_chars(self, name):
        ok, _ = validate_name(name, field_type="strategy name")
        assert not ok, name

    def test_bot_name_rule_matches(self):
        assert validate_name("house_bot_2", field_type="bot name")[0]
        assert not validate_name("bad<bot>", field_type="bot name")[0]


# --- duplicate-name helper + route enforcement ---

class TestUniqueNames:
    def test_helper(self):
        assert check_unique_strategy_names([{'name': 'A'}, {'name': 'B'}])[0]
        ok, msg = check_unique_strategy_names([{'name': 'A'}, {'name': 'a'}])
        assert not ok and 'Duplicate' in msg

    def test_tournament_rejects_dupes(self, client):
        s = [{'name': 'X', 'code': "def s(a,b,c):\n return 'C'"},
             {'name': 'X', 'code': "def s(a,b,c):\n return 'D'"}]
        r = client.post('/tournament', json={'strategies': s, 'rounds': 5})
        assert r.status_code == 400
        assert 'duplicate' in r.get_json()['error'].lower()

    def test_nplayer_rejects_dupes(self, client):
        s = [{'name': 'Y', 'code': "def s(a,b,c,d):\n return 'C'"},
             {'name': 'Y', 'code': "def s(a,b,c,d):\n return 'D'"}]
        r = client.post('/nplayer/tournament', json={'strategies': s, 'rounds': 5, 'seed': 1})
        assert r.status_code == 400
        assert 'duplicate' in r.get_json()['error'].lower()

    def test_os_simulation_rejects_dupes(self, client):
        s = [{'name': 'Z', 'code': "def s(a,b,c,d):\n return 'C'"},
             {'name': 'Z', 'code': "def s(a,b,c,d):\n return 'D'"}]
        r = client.post('/os-simulation', json={'strategies': s, 'num_cores': 2})
        assert r.status_code == 400
        assert 'duplicate' in r.get_json()['error'].lower()


# --- machine-local gallery ---

class TestLocalGallery:
    @pytest.fixture(autouse=True)
    def _tmp_gallery(self, tmp_path, monkeypatch):
        monkeypatch.setattr('app.LOCAL_GALLERY_PATH', str(tmp_path / 'local_gallery.json'))

    def test_save_list_delete_roundtrip(self, client):
        code = "def s(a,b,c):\n    return 'C'"
        r = client.post('/save-strategy', json={'name': 'My Strat', 'code': code})
        assert r.status_code == 200 and r.get_json()['success']

        r = client.get('/local-gallery/list')
        names = [s['name'] for s in r.get_json()['strategies']]
        assert names == ['My Strat']

        r = client.post('/local-gallery/delete', json={'name': 'My Strat'})
        assert r.status_code == 200 and r.get_json()['removed'] == 1
        assert client.get('/local-gallery/list').get_json()['strategies'] == []

    def test_rejects_unsafe_code(self, client):
        r = client.post('/save-strategy',
                        json={'name': 'Evil', 'code': "import os\ndef s(a,b,c):\n return 'C'"})
        assert r.status_code == 400 and 'safety' in r.get_json()['error'].lower()

    def test_rejects_bad_name(self, client):
        r = client.post('/save-strategy',
                        json={'name': 'bad<name>', 'code': "def s(a,b,c):\n return 'C'"})
        assert r.status_code == 400

    def test_resave_overwrites(self, client):
        c1 = "def s(a,b,c):\n return 'C'"
        c2 = "def s(a,b,c):\n return 'D'"
        client.post('/save-strategy', json={'name': 'Dup', 'code': c1})
        client.post('/save-strategy', json={'name': 'Dup', 'code': c2})
        strats = client.get('/local-gallery/list').get_json()['strategies']
        assert len(strats) == 1 and strats[0]['code'] == c2


# --- check-auth + loopback guard ---

class TestAuthAndLoopbackGuard:
    def test_check_auth_loopback(self, client):
        r = client.get('/check-auth', environ_base={'REMOTE_ADDR': '127.0.0.1'})
        assert r.status_code == 200
        assert r.get_json()['logged_in'] is True

    def test_lan_client_blocked(self, client):
        # A non-loopback client must not reach the auto-admin local app.
        r = client.get('/', environ_base={'REMOTE_ADDR': '192.168.1.50'})
        assert r.status_code == 403
        r2 = client.post('/tournament', json={'strategies': [], 'rounds': 1},
                         environ_base={'REMOTE_ADDR': '10.0.0.9'})
        assert r2.status_code == 403
