"""Regression tests for heterogeneous assignment (run_heterogeneous_simulation).

Heterogeneous = each strategy used at most once = combinations(strategies, num_cores)
with NO replacement, ranked by throughput.

The full space is C(n, cores) and explodes (C(14, 8) = 3003 runs, ~20 min), so a run is bounded by
a measured wall-clock budget and reports what fraction of the space it covered. It used to be
refused outright past a fixed count instead; these tests pin the replacement behaviour.
"""

import collections
import itertools

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
    # Unbounded run covers the space, so the leaderboard really is exhaustive.
    assert res['sampled'] is False
    for r in res['results']:
        # Each combination has exactly num_cores distinct strategies, each used once.
        assert len(r['assignment_details']) == 3
        assert all(count == 1 for count in r['combination'].values())
        assert len(r['combination']) == 3
    # Ranked by throughput descending
    tps = [r['throughput'] for r in res['results']]
    assert tps == sorted(tps, reverse=True)


def test_ceiling_truncates_instead_of_rejecting():
    # C(4, 2) = 6 with a ceiling of 3: the run proceeds over a subset rather than raising.
    res = run_heterogeneous_simulation(STRATS, num_cores=2, seed=3, max_combinations=3)
    assert res['total_combinations'] == 6
    assert res['evaluated'] == 3
    assert res['sampled'] is True
    assert len(res['results']) == 3


def test_time_budget_bounds_the_run():
    # A budget far below the cost of the full space stops the run early.
    res = run_heterogeneous_simulation(STRATS, num_cores=2, seed=3, time_budget_s=0.5)
    assert res['sampled'] is True
    assert 0 < res['evaluated'] < res['total_combinations']


def test_always_evaluates_at_least_one_combination():
    # An impossible budget must still return a leaderboard, not an empty one: better a
    # single-row result than a blank page.
    res = run_heterogeneous_simulation(STRATS, num_cores=2, seed=3, time_budget_s=1e-9)
    assert res['evaluated'] == 1
    assert len(res['results']) == 1


def test_unlimited_bypasses_both_bounds():
    res = run_heterogeneous_simulation(STRATS, num_cores=2, seed=3, max_combinations=2,
                                       time_budget_s=1e-9, unlimited=True)
    assert res['evaluated'] == res['total_combinations'] == 6
    assert res['sampled'] is False


def _sampled_subset(seed):
    res = run_heterogeneous_simulation(STRATS, num_cores=2, seed=seed, max_combinations=3)
    return sorted(tuple(sorted(r['combination'])) for r in res['results'])


def test_sampled_subset_is_reproducible_from_the_seed():
    # Same seed selects the same subset, so a sampled run can be reproduced exactly.
    assert _sampled_subset(11) == _sampled_subset(11)


def test_different_seeds_select_different_subsets():
    # Guards against the shuffle silently not happening: if the subset were a lexicographic
    # prefix it would be identical regardless of seed.
    seeds = [_sampled_subset(s) for s in (1, 2, 3, 4, 5, 6, 7, 8)]
    assert any(x != seeds[0] for x in seeds[1:])


def test_sample_gives_every_strategy_roughly_equal_exposure():
    """A partial run must not favour strategies by their position in the input list.

    `itertools.combinations` emits low indices first, so truncating its output is badly biased:
    for C(14, 8) cut to 150, strategies 0-3 appear in all 150 combinations while 12-13 appear in
    51. Ranking would then track list position rather than strategy quality. The shuffle fixes
    that; this test fails if it is ever removed.
    """
    n, cores, cap = 14, 8, 150
    lexicographic = list(itertools.combinations(range(n), cores))[:cap]
    lex_counts = collections.Counter(i for combo in lexicographic for i in combo)
    lex_spread = max(lex_counts.values()) - min(lex_counts.values())

    import random as _random
    shuffled = list(itertools.combinations(range(n), cores))
    _random.Random(7).shuffle(shuffled)
    shuf_counts = collections.Counter(i for combo in shuffled[:cap] for i in combo)
    shuf_spread = max(shuf_counts.values()) - min(shuf_counts.values())

    # The biased ordering spreads exposure by ~99; a shuffled sample keeps it far tighter.
    assert lex_spread > 50, "expected the lexicographic prefix to be strongly biased"
    assert shuf_spread < lex_spread / 3
    assert min(shuf_counts.values()) > 0, "every strategy must appear at least once"


def test_requires_at_least_num_cores_distinct_strategies():
    with pytest.raises(ValueError):
        run_heterogeneous_simulation(STRATS[:2], num_cores=3, seed=1)
