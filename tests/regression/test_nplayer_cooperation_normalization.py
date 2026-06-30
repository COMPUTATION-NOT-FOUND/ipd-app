"""Regression tests: N-player cooperation normalization (parity with 1v1).

group_tournament must emit `norm_cooperation_percentage` that equal-weights each game mode, so a
longer (random/stochastic) mode can't sway cooperation toward strategies that play more rounds.
"""

import pytest

from n_player_simulation import group_tournament
from payoff_models import PairwiseMatrixPayoff

pytestmark = pytest.mark.regression


def _payoff():
    return PairwiseMatrixPayoff(
        payoff_matrix={'CC': [3, 3], 'CD': [0, 5], 'DC': [5, 0], 'DD': [1, 1]},
        aggregate='avg',
    )


def _always_c(last_moves, my_history, opponents_histories, meta):
    return 'C'


def _always_d(last_moves, my_history, opponents_histories, meta):
    return 'D'


def _strats():
    return [
        {'name': 'AllC', 'func': _always_c},
        {'name': 'AllD', 'func': _always_d},
    ]


def test_norm_cooperation_present_and_length_independent():
    """Across mixed-length modes, AllC -> ~100% and AllD -> ~0% normalized cooperation,
    regardless of how long the random/stochastic matches ran."""
    res = group_tournament(
        strategies=_strats(),
        rounds=50,
        seed=7,
        payoff_model=_payoff(),
        weights={'win_rate': 0.33, 'cooperation': 0.34, 'points': 0.33},
        modes=['standard', 'random', 'stochastic'],
    )
    by_name = {e['name']: e for e in res['leaderboard']}

    for entry in res['leaderboard']:
        assert 'norm_cooperation_percentage' in entry
        assert 'normalized_cooperates' in entry
        assert 'normalized_defects' in entry

    assert by_name['AllC']['norm_cooperation_percentage'] == pytest.approx(100.0, abs=1e-6)
    assert by_name['AllD']['norm_cooperation_percentage'] == pytest.approx(0.0, abs=1e-6)


def test_standard_only_norm_equals_raw():
    """With a single mode there is nothing to equalize, so the normalized cooperation percentage
    matches the plain rate."""
    res = group_tournament(
        strategies=_strats(),
        rounds=50,
        seed=7,
        payoff_model=_payoff(),
        weights={'win_rate': 0.33, 'cooperation': 0.34, 'points': 0.33},
        modes=['standard'],
    )
    for entry in res['leaderboard']:
        assert entry['norm_cooperation_percentage'] == pytest.approx(
            entry['cooperation_percentage'], abs=1e-6
        )
