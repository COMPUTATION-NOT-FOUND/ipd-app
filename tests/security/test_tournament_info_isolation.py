"""A strategy cannot rewrite the payoff table through its module-level TOURNAMENT_INFO
(review, Oct 2026: it shared the dict game() scores with, so a strategy could pay itself 1000)."""
import pytest

import app as ipd

pytestmark = pytest.mark.security

COOP = "def s(last_moves, my_history, opponents_histories, meta):\n    return 'C'\n"
RIG = ("def rig(last_moves, my_history, opponents_histories, meta):\n"
       "    TOURNAMENT_INFO['payoff_matrix']['DC'] = [1000, -1000]\n"
       "    return 'D'\n")
SPY = ("def spy(last_moves, my_history, opponents_histories, meta):\n"
       "    if TOURNAMENT_INFO['payoff_matrix']['DC'] != [5, 0]:\n"
       "        raise ValueError('saw the other player edit')\n"
       "    return 'C'\n")


def test_rigging_the_table_does_not_change_scores():
    r = ipd.game(RIG, 'rig', COOP, 'coop', rounds=10, seed=1)
    assert r.get('error') is None
    assert (r['a_points'], r['b_points']) == (5, 0)


def test_rigging_with_a_custom_table_does_not_change_scores():
    table = {'CC': [3, 3], 'CD': [0, 5], 'DC': [5, 0], 'DD': [1, 1]}
    r = ipd.game(RIG, 'rig', COOP, 'coop', rounds=10, seed=1, payoff_matrix=table)
    assert (r['a_points'], r['b_points']) == (5, 0)
    assert table['DC'] == [5, 0]


def test_one_player_cannot_see_the_others_edit():
    r = ipd.game(RIG, 'rig', SPY, 'spy', rounds=5, seed=1)
    assert r.get('error') is None
