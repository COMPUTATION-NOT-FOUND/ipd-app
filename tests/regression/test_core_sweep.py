"""Regression tests for heterogeneous assignment (run_heterogeneous_simulation).

Heterogeneous = each strategy used at most once = combinations(strategies, num_cores)
with NO replacement, ranked by throughput.
"""

import pytest

from core_simulation import run_heterogeneous_simulation

pytestmark = pytest.mark.regression


STRATS = [
    {'name': 'AllC', 'code': 'def s(last, my, opp):\n    return "C"'},
    {'name': 'AllD', 'code': 'def s(last, my, opp):\n    return "D"'},
    {'name': 'TFT', 'code': 'def s(last, my, opp):\n    return "C" if last is None else last'},
    {'name': 'Grim', 'code': 'def s(last, my, opp):\n    return "D" if "D" in opp else "C"'},
]


def test_enumerates_all_combinations_without_replacement():
    res = run_heterogeneous_simulation(STRATS, num_cores=3, seed=7)
    # C(4, 3) = 4 combinations, each strategy at most once — ALL evaluated, never sampled.
    assert res['total_combinations'] == 4
    assert res['evaluated'] == 4
    assert 'sampled' not in res
    for r in res['results']:
        # Each combination has exactly num_cores distinct strategies, each used once.
        assert len(r['assignment_details']) == 3
        assert all(count == 1 for count in r['combination'].values())
        assert len(r['combination']) == 3
    # Ranked by throughput descending
    tps = [r['throughput'] for r in res['results']]
    assert tps == sorted(tps, reverse=True)


def test_rejects_when_combination_count_exceeds_cap():
    # C(4, 2) = 6 > max_combinations=3 -> reject (no silent sampling).
    with pytest.raises(ValueError, match="Too many heterogeneous combinations"):
        run_heterogeneous_simulation(STRATS, num_cores=2, seed=3, max_combinations=3)


def test_requires_at_least_num_cores_distinct_strategies():
    with pytest.raises(ValueError):
        run_heterogeneous_simulation(STRATS[:2], num_cores=3, seed=1)
