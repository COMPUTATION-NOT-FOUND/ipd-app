"""Group elimination for N-Player and the heterogeneous OS simulation (final sprint, Oct 2026).

Rule: ceil(n / G) groups, the next round has half as many groups (rounded up), so
ceil(groups / 2) * G players move on; each group sends the same guaranteed number and the
leftover places are wildcards for the best next-placed players across groups.
"""
import pytest

import core_simulation as cs
import elimination as el
from n_player_simulation import elimination_tournament, group_tournament
from payoff_models import PublicGoodsPayoff
from ranking import apply_ranking_to_result, rank_entries

pytestmark = pytest.mark.regression

CODES = {
    'allc': "def s(last, mine, opps, meta):\n    return 'C'\n",
    'alld': "def s(last, mine, opps, meta):\n    return 'D'\n",
    'tft': "def s(last, mine, opps, meta):\n    return 'C' if not last or last.count('D') * 2 < len(last) else 'D'\n",
    'rnd': "def s(last, mine, opps, meta):\n    return meta['rng'].choice('CD')\n",
}
BAD = "def s(last, mine, opps, meta):\n    return 1\n"


def _pool(n, bad=()):
    out = []
    for i in range(n):
        code = BAD if i in bad else CODES[list(CODES)[i % len(CODES)]]
        out.append({'name': f's{i}', 'code': code, 'func': cs.extract_strategy_func(code, seed=1)})
    return out


# --- the planner ---------------------------------------------------------------------------

def test_plan_matches_the_agreed_example():
    plan = el.plan_rounds(65, 5)
    assert [r['groups'] for r in plan] == [13, 7, 4, 2, 1]
    assert [r['advance'] for r in plan] == [35, 20, 10, 5, 0]
    assert plan[-1]['final'] and not any(r['final'] for r in plan[:-1])


@pytest.mark.parametrize('min_group', [1, 2])
def test_every_round_shrinks_and_groups_are_balanced(min_group):
    for n in range(2, 160):
        for g in range(2, 10):
            for r in el.plan_rounds(n, g, min_group):
                assert max(r['sizes']) - min(r['sizes']) <= 1
                assert min(r['sizes']) >= min(min_group, n)
                assert sum(r['sizes']) == r['players']
                if not r['final']:
                    assert 0 < r['advance'] < r['players']


def test_guaranteed_places_then_wildcards():
    # 13 groups of 5, 35 places: top 2 of every group (26) + the 9 best third places.
    groups = [[g * 5 + p for p in range(5)] for g in range(13)]
    score = {pid: pid for g in groups for pid in g}          # lower id = better, for the test
    adv, wild, out = el.pick_advancers(groups, 35, lambda ids: sorted(ids, key=score.get))
    assert len(adv) == 35 and len(wild) == 9
    assert all(g[0] in adv and g[1] in adv for g in groups)
    assert all(pid % 5 == 2 for pid in wild)                 # wildcards are all third places
    assert set(adv).isdisjoint(out) and len(out) == 65 - 35


def test_snake_split_spreads_the_best():
    groups = el.snake_split(list(range(6)), [2, 2, 2])
    assert groups == [[0, 5], [1, 4], [2, 3]]


# --- N-Player ------------------------------------------------------------------------------

def _np(pool, seed=7, **kw):
    return elimination_tournament(pool, rounds=15, group_size=5, seed=seed,
                                  payoff_model=PublicGoodsPayoff(b=0.4, c=1.0), **kw)


def test_nplayer_bracket_follows_the_plan_and_is_reproducible():
    pool = _pool(26)
    r = _np(pool)
    assert [len(rd['groups']) for rd in r['rounds']] == [6, 3, 2, 1]
    assert [rd['advance'] for rd in r['rounds']] == [15, 10, 5, 0]
    assert r['tournament_info']['style'] == 'elimination'
    assert len(r['leaderboard']) == 26
    stages = [e['stage_reached'] for e in r['leaderboard']]
    assert stages == sorted(stages, reverse=True)            # deeper runs rank higher
    assert [e['name'] for e in _np(pool)['leaderboard']] == [e['name'] for e in r['leaderboard']]


def test_nplayer_disqualified_never_advance_or_rank():
    pool = _pool(17, bad={3, 8})
    r = _np(pool)
    names = {e['name'] for e in r['leaderboard']}
    assert 's3' not in names and 's8' not in names
    assert {d['name'] for d in r['disqualified']} == {'s3', 's8'}
    for rd in r['rounds'][1:]:
        for g in rd['groups']:
            assert all(p['name'] not in ('s3', 's8') for p in g['players'])


def test_nplayer_rerank_keeps_the_bracket_order():
    r = _np(_pool(20), modes=['standard', 'stochastic'])
    before = [e['name'] for e in r['leaderboard']]
    apply_ranking_to_result(r, 'nplayer', {'metric': 'cooperation_percentage'})
    assert [e['name'] for e in r['leaderboard']] == before
    assert r['ranking_text'].startswith('Elimination')


def test_nplayer_legacy_unchanged_apart_from_points_per_round():
    pool = _pool(9)
    r = group_tournament(pool, rounds=15, group_size=4, seed=5, payoff_model=PublicGoodsPayoff(b=0.4, c=1.0))
    assert 'rounds' not in r and 'style' not in r['tournament_info']
    assert all('stage_reached' not in e for e in r['leaderboard'])


# --- OS simulation -------------------------------------------------------------------------

def test_os_per_core_tasks_add_up():
    funcs = [cs.extract_strategy_func(CODES[k], seed=1) for k in ('allc', 'alld', 'tft')]
    sim = cs.OSSimulator('Mixed', funcs, seed=4, num_cores=3, per_core=True)
    sim.generate_workload(50)
    out = sim.run()
    per_core = out['per_core']
    assert sum(c['tasks_started'] for c in per_core) == out['global_metrics']['tasks_measured']
    assert sum(sim.core_completed) == len(sim.completed_processes)


def test_os_legacy_simulator_has_no_per_core_block():
    funcs = [cs.extract_strategy_func(CODES['tft'], seed=1)] * 2
    sim = cs.OSSimulator('Uniform', funcs, seed=4, num_cores=2)
    sim.generate_workload(30)
    assert 'per_core' not in sim.run()


def test_os_elimination_runs_with_fillers_and_disqualifies():
    pool = [{'name': p['name'], 'code': p['code']} for p in _pool(7, bad={2})]
    r = cs.run_heterogeneous_elimination(pool, num_cores=3, seed=2, runs=1, num_processes=50)
    assert r['style'] == 'elimination'
    assert {e['name'] for e in r['errors']} == {'s2'}
    names = [e['name'] for e in r['leaderboard']]
    assert 's2' not in names and len(names) == 6
    assert any(g['fillers'] for g in r['group_runs'])        # 7 strategies on 3 cores -> padding
    assert [rd['final'] for rd in r['rounds']][-1]
    again = cs.run_heterogeneous_elimination(pool, num_cores=3, seed=2, runs=1, num_processes=50)
    assert [e['name'] for e in again['leaderboard']] == names


def test_os_elimination_rerank_and_preview():
    from ranking import preview_rows
    pool = [{'name': p['name'], 'code': p['code']} for p in _pool(5)]
    het = cs.run_heterogeneous_elimination(pool, num_cores=2, seed=3, runs=1, num_processes=50)
    result = {'core_simulation_heterogeneous': het}
    before = [e['name'] for e in het['leaderboard']]
    apply_ranking_to_result(result, 'os', {'metric': 'throughput'})
    assert [e['name'] for e in het['leaderboard']] == before
    assert result['winner'] == before[0]
    assert [row['name'] for row in preview_rows(result, 'os')] == before


def test_rank_entries_without_stages_is_unchanged():
    rows = [{'name': 'a', 'total_points': 1}, {'name': 'b', 'total_points': 3}]
    assert [e['name'] for e in rank_entries(rows, None, 'nplayer')] == ['b', 'a']


# --- routes --------------------------------------------------------------------------------

TWO_CODES = [{'name': f'p{i}', 'code': CODES[k]} for i, k in enumerate(['allc', 'alld', 'tft', 'rnd', 'allc'])]


def test_nplayer_route_style_option(client):
    body = {'strategies': TWO_CODES, 'rounds': 10, 'group_size': 2, 'seed': 1, 'style': 'elimination',
            'payoff_model': {'type': 'public_goods', 'b': 0.4, 'c': 1}}
    r = client.post('/nplayer/tournament', json=body)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['tournament_info']['style'] == 'elimination'
    legacy = client.post('/nplayer/tournament', json={**body, 'style': None})
    assert 'rounds' not in legacy.get_json()
    bad = client.post('/nplayer/tournament', json={**body, 'style': 'knockout'})
    assert bad.status_code == 400


def test_os_route_style_option(client):
    body = {'strategies': TWO_CODES[:3], 'num_cores': 4, 'core_assignment_mode': 'heterogeneous',
            'style': 'elimination', 'runs': 1, 'num_processes': 50, 'seed': 2}
    r = client.post('/os-simulation', json=body)
    assert r.status_code == 200, r.get_json()          # 3 strategies on 4 cores: one filler
    het = r.get_json()['core_simulation_heterogeneous']
    assert het['style'] == 'elimination' and len(het['leaderboard']) == 3
    assert r.get_json()['core_simulation_config']['style'] == 'elimination'
    legacy = client.post('/os-simulation', json={**body, 'style': 'legacy'})
    assert legacy.status_code == 400                    # legacy still needs >= num_cores strategies
