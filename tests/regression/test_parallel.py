"""Runs split across CPU cores (parallel.py) give exactly the same results as one core."""
import json

import pytest

import parallel

pytestmark = pytest.mark.regression

TFT = "def t(l, m, o, meta):\n    return l[0] if l else 'C'"
RND = "def r(l, m, o, meta):\n    return 'C' if meta['rng'].random() < 0.6 else 'D'"
BAD = "def b(l, m, o, meta):\n    return 'C' if meta['round'] < 3 else 1"
# Keeps state between calls: must still match, because one strategy's runs stay in one process.
STATEFUL = "seen = []\ndef s(l, m, o, meta):\n    seen.append(1)\n    return 'D' if len(seen) % 7 == 0 else 'C'"


def _dump(x):
    return json.dumps(x, sort_keys=True, default=str)


@pytest.fixture(scope='module', autouse=True)
def _stop_pool():
    yield
    parallel.shutdown()


def test_1v1_same_on_two_cores(monkeypatch):
    from app import round_robin_tournament
    strats = [{'name': f's{i}', 'code': c} for i, c in enumerate([TFT, RND, BAD, TFT, RND, STATEFUL])]

    def run():
        r = round_robin_tournament(strats, rounds=20, modes=['standard', 'random'], seed=3)
        return _dump([r['leaderboard'], r['disqualified'],
                      [(m['player_a'], m['player_b'], m['a_points'], m.get('void')) for m in r['matches']]])
    monkeypatch.setenv('IPD_WORKERS', '1')
    one = run()
    monkeypatch.setenv('IPD_WORKERS', '2')
    two = run()
    assert one == two


def test_os_same_on_two_cores(monkeypatch):
    import core_simulation as cs
    strats = [{'name': n, 'code': c} for n, c in (('tft', TFT), ('rnd', RND), ('stateful', STATEFUL), ('bad', BAD))]

    def run():
        r = cs.run_full_simulation(strats, num_cores=2, seed=4, runs=2, num_processes=15)
        return _dump([r['strategies'], r['errors'], r['baseline']])
    monkeypatch.setenv('IPD_WORKERS', '1')
    one = run()
    monkeypatch.setenv('IPD_WORKERS', '2')
    two = run()
    assert one == two
    assert '"bad"' in one                     # the failing strategy is reported the same way


@pytest.mark.parametrize('raw, expected', [('1', 1), ('3', 3), ('0', 1), ('999', parallel.MAX_WORKERS),
                                           ('junk', parallel.default_workers()), ('', parallel.default_workers())])
def test_worker_count_setting(monkeypatch, raw, expected):
    monkeypatch.setenv('IPD_WORKERS', raw)
    assert parallel.worker_count() == expected


def test_small_runs_stay_in_this_process(monkeypatch):
    monkeypatch.setenv('IPD_WORKERS', '8')
    before = (parallel._pool, parallel._pool_size)
    calls = [(pow, (2, k), {}) for k in range(parallel.MIN_JOBS - 1)]
    assert parallel.run_calls(calls) == [1, 2, 4]
    assert (parallel._pool, parallel._pool_size) == before      # no workers started or resized


def test_settings_page_saves_the_core_count(client, monkeypatch, tmp_path):
    import app as ipd
    monkeypatch.setattr(ipd, 'DATA_DIR', str(tmp_path))
    resp = client.post('/settings/workers', json={'workers': 3})
    assert resp.status_code == 200 and resp.get_json()['workers'] == 3
    assert 'IPD_WORKERS=3' in (tmp_path / '.env').read_text()
    assert client.post('/settings/workers', json={'workers': 'lots'}).status_code == 400
    assert client.post('/settings/workers', json={'workers': None}).get_json()['workers'] == parallel.default_workers()
    assert 'Cores to use for runs' in client.get('/settings').get_data(as_text=True)
    monkeypatch.setenv('IPD_WORKERS', '1')
