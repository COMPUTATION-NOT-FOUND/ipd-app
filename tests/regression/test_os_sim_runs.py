"""OS Simulation: separate random streams, averaged seeded runs, task count, warm-up and the
overload (load) figure."""
import pytest

import core_simulation as cs

pytestmark = pytest.mark.regression

TFT_CODE = "def t(l, m, o, meta):\n    return l[0] if l else 'C'"
TFT = cs.extract_strategy_func(TFT_CODE)
# Same decisions as TFT, but draws from meta['rng'] every call.
TFT_RANDOM_USER = cs.extract_strategy_func(
    "def t(l, m, o, meta):\n    meta['rng'].random()\n    return l[0] if l else 'C'")


def _sim(funcs, seed=3, wl='Mixed', cores=2, n=50, warmup=0.0):
    sim = cs.OSSimulator(wl, funcs, seed=seed, num_cores=cores, warmup_fraction=warmup)
    sim.generate_workload(n)
    return sim


def test_strategy_randomness_does_not_change_tasks_or_memory():
    """With one shared random stream, a strategy drawing random numbers shifted every later memory
    access. Now tasks and each task's memory accesses depend only on the seed."""
    a, b = _sim([TFT, TFT]), _sim([TFT_RANDOM_USER, TFT_RANDOM_USER])
    assert [(p.arrival_time, p.burst_time, p.working_set_size) for p in a.all_processes] == \
           [(p.arrival_time, p.burst_time, p.working_set_size) for p in b.all_processes]
    ma, mb = a.run()['global_metrics'], b.run()['global_metrics']
    assert ma == mb      # same decisions + same tasks + same memory => identical results


def test_same_seed_is_reproducible_and_different_seeds_differ():
    assert _sim([TFT, TFT], seed=5).run()['global_metrics'] == _sim([TFT, TFT], seed=5).run()['global_metrics']
    assert _sim([TFT, TFT], seed=5).run()['global_metrics'] != _sim([TFT, TFT], seed=6).run()['global_metrics']


def test_runs_average_single_seed_runs_and_report_ci():
    r = cs.run_full_simulation([{'name': 'tft', 'code': TFT_CODE}], seed=10, runs=3)
    assert r['runs'] == 3 and r['seeds'] == [10, 11, 12] and r['seed'] == 10
    got = r['strategies']['tft']['workloads']['Poisson']
    singles = [_sim([TFT, TFT], seed=s, wl='Poisson').run()['global_metrics']['avg_waiting'] for s in (10, 11, 12)]
    assert got['avg_waiting'] == pytest.approx(sum(singles) / 3, abs=0.01)
    assert got['ci']['avg_waiting'] > 0 and 'ci' in r['strategies']['tft']['avg']
    # The baseline uses the same seeds (paired comparison).
    assert r['baseline']['avg']['ci']
    again = cs.run_full_simulation([{'name': 'tft', 'code': TFT_CODE}], seed=10, runs=3)
    assert again['strategies'] == r['strategies']


def test_no_seed_still_reports_the_seeds_used():
    r = cs.run_full_simulation([{'name': 'tft', 'code': TFT_CODE}], runs=2)
    assert len(r['seeds']) == 2 and r['seeds'][1] == r['seeds'][0] + 1


def test_single_run_has_no_ci():
    r = cs.run_full_simulation([{'name': 'tft', 'code': TFT_CODE}], seed=1, runs=1)
    assert 'ci' not in r['strategies']['tft']['avg']


def test_400_tasks_finish_instead_of_being_cut_off():
    g = _sim([TFT, TFT], n=400, wl='Uniform').run()['global_metrics']
    assert g['unfinished'] == 0 and g['makespan'] > cs.MAX_SIM_TICKS


def test_warmup_leaves_out_the_earliest_tasks():
    full = _sim([TFT] * 8, wl='Bursty', cores=8).run()['global_metrics']
    warm = _sim([TFT] * 8, wl='Bursty', cores=8, warmup=0.2)
    gw = warm.run()['global_metrics']
    assert full['tasks_measured'] == 50 and gw['tasks_measured'] == 40
    assert gw['throughput'] == full['throughput']                     # whole run either way
    assert gw['avg_waiting'] != full['avg_waiting']


def test_cache_misses_per_task_match_the_cache_totals():
    sim = _sim([TFT, TFT], wl='Uniform')
    g = sim.run()['global_metrics']
    totals = sum(h.l1.misses + h.l2.misses for h in sim.cache_hierarchies) + sim.cache_hierarchies[0].l3.misses
    assert g['avg_cache_misses'] == pytest.approx(totals / 50, abs=0.01)


def test_load_flags_overloaded_setups_only():
    two = cs.run_baseline_simulation(num_cores=2, seed=1, runs=2)
    eight = cs.run_baseline_simulation(num_cores=8, seed=1, runs=2)
    assert two['workloads']['Mixed']['load'] > 1.0          # 2 cores can't keep up
    assert eight['workloads']['Bursty']['load'] < 1.0       # 8 cores can
    assert 'Mixed' in cs._overload_info(two)['overloaded_workloads']
    assert 'Bursty' not in cs._overload_info(eight)['overloaded_workloads']


def test_heterogeneous_runs_carry_ci_and_info():
    pool = [{'name': n, 'code': TFT_CODE} for n in ('a', 'b', 'c')]
    r = cs.run_heterogeneous_simulation(pool, num_cores=2, seed=4, runs=2)
    assert r['runs'] == 2 and r['seeds'] == [4, 5]
    assert 'ci' in r['results'][0] and 'wait_time' in r['results'][0]['ci']
    assert 'overloaded_workloads' in r


@pytest.mark.parametrize('field, value', [('runs', 0), ('runs', 31), ('runs', 'x'),
                                          ('num_processes', 75), ('warmup', 0.5)])
def test_route_rejects_bad_options(client, field, value):
    body = {'strategies': [{'name': 'a', 'code': TFT_CODE}, {'name': 'b', 'code': TFT_CODE}], field: value}
    assert client.post('/os-simulation', json=body).status_code == 400


def test_route_passes_options_through(client):
    body = {'strategies': [{'name': 'a', 'code': TFT_CODE}, {'name': 'b', 'code': TFT_CODE}],
            'seed': 2, 'runs': 2, 'num_processes': 50, 'warmup': 0.1}
    data = client.post('/os-simulation', json=body).get_json()
    res = data['core_simulation_results']
    assert res['runs'] == 2 and res['seeds'] == [2, 3] and res['warmup'] == 0.1
    assert data['core_simulation_config']['runs'] == 2
