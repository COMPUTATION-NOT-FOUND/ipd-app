"""Regression tests for the pluggable scheduler suite, contention signal, and cache
determinism."""

import pytest

from core_simulation import (
    OSSimulator, run_full_simulation,
)
from schedulers import SCHEDULERS, build_scheduler

pytestmark = pytest.mark.regression


ALLC = {'name': 'AllC', 'code': 'def s(last, my, opp):\n    return "C"'}
ALLD = {'name': 'AllD', 'code': 'def s(last, my, opp):\n    return "D"'}
TFT = {'name': 'TFT', 'code': 'def s(last, my, opp):\n    return "C" if last is None else last'}


def _avg(strategy_result):
    return strategy_result['avg']


class TestSchedulerSuite:
    def test_all_schedulers_run_and_report_metrics(self):
        for name in SCHEDULERS:
            res = run_full_simulation([ALLC, ALLD], num_cores=2, seed=42, scheduler=name)
            for strat in ('AllC', 'AllD'):
                m = res['strategies'][strat]['avg']
                assert m['throughput'] > 0
                assert m['makespan'] > 0

    def test_fcfs_is_personality_blind(self):
        res = run_full_simulation([ALLC, ALLD], num_cores=2, seed=42, scheduler='fcfs')
        # FCFS ignores the Cooperate/Defect bit, so AllC and AllD behave identically.
        assert _avg(res['strategies']['AllC'])['throughput'] == _avg(res['strategies']['AllD'])['throughput']
        assert _avg(res['strategies']['AllC'])['avg_response'] == _avg(res['strategies']['AllD'])['avg_response']

    def test_round_robin_defect_favours_throughput_coop_favours_response(self):
        res = run_full_simulation([ALLC, ALLD], num_cores=2, seed=42, scheduler='round_robin')
        allc = _avg(res['strategies']['AllC'])
        alld = _avg(res['strategies']['AllD'])
        # Defectors hold long quanta -> higher throughput; Cooperators yield -> better response.
        assert alld['throughput'] >= allc['throughput']
        assert allc['avg_response'] <= alld['avg_response']

    def test_sjf_defectors_hurt_response(self):
        res = run_full_simulation([ALLC, ALLD], num_cores=2, seed=42, scheduler='sjf')
        allc = _avg(res['strategies']['AllC'])
        alld = _avg(res['strategies']['AllD'])
        # Defectors grab the longest jobs and monopolise -> worse response than cooperators.
        assert alld['avg_response'] >= allc['avg_response']


class TestContentionSignal:
    def test_tft_defects_under_congestion_cooperates_when_idle(self):
        sim = OSSimulator('Uniform', [_tft(), _tft()], scheduler='round_robin', seed=1, num_cores=2)
        sim.generate_workload(50)
        # Idle system (empty ready queue) -> Cooperate.
        assert sim._contention_move(0) == 'C'
        # Flood the ready queue beyond num_cores -> congested -> the contention "opponent" defects.
        for p in sim.all_processes:
            sim.scheduler.enqueue(p, 0)
        assert sim._contention_move(0) == 'D'


class TestCacheDeterminism:
    def test_same_seed_same_cache_misses(self):
        a = run_full_simulation([ALLC], num_cores=2, seed=7, scheduler='round_robin')
        b = run_full_simulation([ALLC], num_cores=2, seed=7, scheduler='round_robin')
        assert a['strategies']['AllC']['avg']['avg_cache_misses'] == b['strategies']['AllC']['avg']['avg_cache_misses']

    def test_cache_misses_are_real_nonzero(self):
        res = run_full_simulation([ALLC], num_cores=2, seed=7, scheduler='round_robin')
        assert res['strategies']['AllC']['avg']['avg_cache_misses'] > 0


def _tft():
    def s(last, my, opp):
        return 'C' if last is None else last
    return s
