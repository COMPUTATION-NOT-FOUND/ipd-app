"""Tests for the opt-in isolated (out-of-process) strategy screening.

The in-process instruction cap can't see work that loops in C (e.g. ReDoS via the `re` module),
so `isolation.screen_strategies` runs each strategy in a separate process with a wall-clock
timeout and flags the ones that hang, crash, or error. These tests prove a hanging strategy is
flagged as a timeout and excluded, a crashing one is flagged, and a normal one survives.
"""
import pytest

import isolation

pytestmark = pytest.mark.security


GOOD = {'name': 'Good', 'code': "def s(last_moves, my_history, opp, meta):\n    return 'C'"}
ALLD = {'name': 'AllD', 'code': "def s(last_moves, my_history, opp, meta):\n    return 'D'"}
# Catastrophic backtracking: runs in C, invisible to the sys.settrace instruction counter.
REDOS = {'name': 'ReDoS', 'website_id': 'w-redos',
         'code': "import re\ndef s(last_moves, my_history, opp, meta):\n"
                 "    re.match(r'(a+)+b', 'a' * 40 + 'c')\n    return 'C'"}
CRASH = {'name': 'Boom',
         'code': "def s(last_moves, my_history, opp, meta):\n    raise ValueError('boom')"}


def test_good_strategy_survives():
    status, _ = isolation.screen_strategy(GOOD['code'], GOOD['name'], rounds=10, timeout_s=5)
    assert status == 'ok'


def test_redos_is_flagged_timeout():
    status, detail = isolation.screen_strategy(REDOS['code'], REDOS['name'], rounds=10, timeout_s=2)
    assert status == 'timeout'
    assert 'limit' in detail.lower()


def test_crashing_strategy_is_flagged():
    status, _ = isolation.screen_strategy(CRASH['code'], CRASH['name'], rounds=10, timeout_s=5)
    # game() catches the exception and reports an errored match; either classification is a flag.
    assert status in ('crash', 'error')


def test_screen_roster_excludes_and_flags():
    survivors, flags = isolation.screen_strategies(
        [GOOD, ALLD, REDOS, CRASH], rounds=10, timeout_s=2)
    names = {s['name'] for s in survivors}
    assert names == {'Good', 'AllD'}

    by_name = {f['name']: f for f in flags}
    assert set(by_name) == {'ReDoS', 'Boom'}
    assert by_name['ReDoS']['reason'] == 'timeout'
    # website_id must round-trip so the flag can be applied on the website.
    assert by_name['ReDoS']['website_id'] == 'w-redos'


def test_os_simulation_sandbox_mode_leaves_out_a_crashing_strategy(client):
    """Sandbox mode on the OS Simulation tab: the crashing strategy is flagged and excluded, the
    rest still run (the toggle used to exist only on 1v1 / N-Player)."""
    resp = client.post('/os-simulation', json={
        'strategies': [GOOD, ALLD, CRASH], 'num_cores': 2, 'scheduler': 'round_robin',
        'core_assignment_mode': 'homogeneous', 'seed': 1, 'isolated': True})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['screened'] is True
    assert [f['name'] for f in data['flags']] == ['Boom']
    assert set(data['core_simulation_results']['strategies']) == {'Good', 'AllD'}


def test_sandbox_shortfall_names_how_many_were_left_out(client):
    resp = client.post('/os-simulation', json={
        'strategies': [GOOD, CRASH], 'num_cores': 2, 'isolated': True})
    assert resp.status_code == 400
    data = resp.get_json()
    assert data['error'].startswith('Sandbox mode left out 1 strategy ')
    assert [f['name'] for f in data['flags']] == ['Boom']


def test_screening_uses_spawn_not_fork():
    # Forking the threaded server can copy held locks into the child (review, Oct 2026).
    import isolation
    assert isolation._mp_context().get_start_method() == 'spawn'
