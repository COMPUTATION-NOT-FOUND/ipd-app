"""What a strategy finds in meta['tournament_info'] in each game (documented in the website FAQ)."""
import pytest

from app import round_robin_tournament
from n_player_simulation import game_n
from payoff_models import PublicGoodsPayoff
import core_simulation as cs

pytestmark = pytest.mark.regression

# Cooperates only when it can read the classic table, the default ranking and this match's mode.
PROBE = """def probe(last_moves, my_history, opponents_histories, meta):
    info = meta['tournament_info']
    m = info['payoff_matrix']
    ok = (m['CC'] == [3, 3] and m['CD'] == [0, 5] and m['DC'] == [5, 0] and m['DD'] == [1, 1]
          and info['ranking']['metric'] == 'total_points' and info['mode'] == 'standard'
          and info['format'] == '2-player' and info['rounds'] == 12
          and info['weights'] == {'points': 1.0, 'cooperation': 0.0, 'win_rate': 0.0}
          and info['ranking']['tiebreak'] == ['cooperation_percentage', 'win_rate', 'earliest_submission'])
    return 'C' if ok else 'D'"""
ALL_C = "def all_c(last_moves, my_history, opponents_histories, meta):\n    return 'C'"
# Tries to fool its opponent by rewriting the table it reads.
POISON = """def poison(last_moves, my_history, opponents_histories, meta):
    meta['tournament_info']['payoff_matrix']['DC'] = [0, 0]
    meta['tournament_info']['ranking']['metric'] = 'wins'
    return 'C'"""


def _coop(result, name):
    return next(e for e in result['leaderboard'] if e['name'] == name)['cooperation_percentage']


def test_1v1_meta_has_matrix_ranking_mode_and_rounds():
    r = round_robin_tournament([{'name': 'probe', 'code': PROBE}, {'name': 'all_c', 'code': ALL_C}],
                               rounds=12)
    assert _coop(r, 'probe') == 100


def test_one_strategy_cannot_change_what_its_opponent_reads():
    r = round_robin_tournament([{'name': 'probe', 'code': PROBE}, {'name': 'poison', 'code': POISON}],
                               rounds=12)
    assert _coop(r, 'probe') == 100


def test_nplayer_meta_has_benefit_and_cost():
    seen = []

    def spy(last_moves, my_history, opponents_histories, meta):
        seen.append(meta['tournament_info']['payoff_model'])
        return 'C'
    game_n([spy, spy, spy], rounds=2, payoff_model=PublicGoodsPayoff(b=0.4, c=1.0), seed=1)
    assert seen[0]['name'] == 'public_goods' and seen[0]['b'] == 0.4 and seen[0]['c'] == 1.0
    assert seen[0]['type'] == 'PublicGoodsPayoff'      # older key kept


def test_os_meta_names_the_scheduler():
    seen = []

    def spy(last_moves, my_history, opponents_histories, meta):
        seen.append(meta['tournament_info'])
        return 'C'
    sim = cs.OSSimulator('Mixed', [spy, spy], scheduler='priority', seed=1, num_cores=2)
    sim.generate_workload(5)
    sim.run()
    assert seen and seen[0]['format'] == 'os_simulation' and seen[0]['n_players'] == 2
    assert seen[0]['scheduler']
