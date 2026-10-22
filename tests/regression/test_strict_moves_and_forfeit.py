"""
Regression tests for the student bug-test reports (Oct 2026):

* B2  -- a strategy returning anything other than exactly 'C' / 'D' was silently counted as
         cooperation in 1v1, N-Player, the OS simulation and Hub screening.
* B5  -- a strategy that crashed or hit the instruction cap in round 1 scored 0-0 and the match
         was counted as a DRAW, so the broken strategy lost nothing.
* Disqualification (Oct 2026, second report) -- a strategy that fails at any point is now
         DISQUALIFIED: removed from the leaderboard, and every match it played is thrown out, so
         nobody gains or loses points from having met it.
* Lone N-Player group -- with 3 strategies and group size 2 the leftover player sat alone in a
         group and was credited an automatic win.
"""

import pytest

from app import game, round_robin_tournament, get_safe_globals, extract_strategy_function
from n_player_simulation import validate_move, InvalidMove, group_tournament, game_n
from payoff_models import PublicGoodsPayoff
from core_simulation import run_full_simulation, run_heterogeneous_simulation

pytestmark = pytest.mark.regression

TFT = "def tft(last_moves, my_history, opponents_histories, meta):\n    return last_moves[0] if last_moves else 'C'"
ALL_C = "def all_c(last_moves, my_history, opponents_histories, meta):\n    return 'C'"
ALL_D = "def all_d(last_moves, my_history, opponents_histories, meta):\n    return 'D'"
RETURNS_ONE = "def bad(last_moves, my_history, opponents_histories, meta):\n    return 1"
LOOPS = "def loops(last_moves, my_history, opponents_histories, meta):\n    while True:\n        pass"

PUBLIC_GOODS = PublicGoodsPayoff(b=2.0, c=1.0)


def _compiled(name, code):
    g = get_safe_globals()
    exec(code, g)
    return {'name': name, 'code': code, 'func': extract_strategy_function(code, g, prefer_n_args=4)}


class TestValidateMove:
    @pytest.mark.parametrize('move', ['C', 'D'])
    def test_accepts_exact_moves(self, move):
        assert validate_move(move) == move

    @pytest.mark.parametrize('bad', [1, 0, None, 'c', 'd', 'X', '', 'CD', ' C', True, False, ['C'], object()])
    def test_rejects_everything_else(self, bad):
        with pytest.raises(InvalidMove):
            validate_move(bad)

    def test_message_names_the_value(self):
        with pytest.raises(InvalidMove, match=r"returned 1;"):
            validate_move(1)

    def test_str_subclass_is_rejected(self):
        class Sneaky(str):
            pass
        with pytest.raises(InvalidMove):
            validate_move(Sneaky('C'))


class TestOneVsOne:
    def test_invalid_return_ends_match_with_named_error(self):
        result = game(RETURNS_ONE, 'bad', TFT, 'tft', rounds=10)
        assert result['winner'] == 'Error'
        assert result['error_player'] == 'A'
        assert result['error_type'] == 'InvalidMove'
        assert 'returned 1' in result['error']

    def test_crashing_strategy_is_disqualified(self):
        result = round_robin_tournament(
            [{'name': 'tft', 'code': TFT}, {'name': 'loops', 'code': LOOPS},
             {'name': 'all_d', 'code': ALL_D}], rounds=20)
        assert sorted(e['name'] for e in result['leaderboard']) == ['all_d', 'tft']
        dq = {d['name']: d for d in result['disqualified']}
        assert list(dq) == ['loops']
        assert 'instruction limit' in dq['loops']['reason']
        assert dq['loops']['stage'] == 'during the run'
        # Its match is kept for display, marked void, and not counted.
        void = [m for m in result['matches'] if m.get('void')]
        assert len(void) == 1 and 'loops' in (void[0]['player_a'], void[0]['player_b'])
        assert result['total_matches'] == 1

    def test_opponents_score_as_if_it_never_entered(self):
        with_bad = round_robin_tournament(
            [{'name': 'tft', 'code': TFT}, {'name': 'bad', 'code': RETURNS_ONE},
             {'name': 'all_d', 'code': ALL_D}], rounds=10)
        without = round_robin_tournament(
            [{'name': 'tft', 'code': TFT}, {'name': 'all_d', 'code': ALL_D}], rounds=10)
        keys = ('total_points', 'wins', 'draws', 'losses', 'cooperates', 'total_moves')

        def strip(lb):
            return {e['name']: tuple(e[k] for k in keys) for e in lb}
        assert strip(with_bad['leaderboard']) == strip(without['leaderboard'])

    def test_only_two_strategies_and_one_fails_still_shows_why(self):
        result = round_robin_tournament(
            [{'name': 'tft', 'code': TFT}, {'name': 'bad', 'code': RETURNS_ONE}], rounds=10)
        assert result['leaderboard'] == []
        assert 'disqualified' in result['error']
        assert result['matches'][0]['void'] and 'returned 1' in result['matches'][0]['error']

    def test_hub_screen_rejects_invalid_return(self, client):
        resp = client.post('/hub/screen', json={'name': 'bad', 'code': RETURNS_ONE})
        assert resp.status_code == 400
        assert 'returned 1' in resp.get_json()['error']

    def test_hub_screen_accepts_valid_strategy(self, client):
        resp = client.post('/hub/screen', json={'name': 'tft', 'code': TFT})
        assert resp.status_code == 200
        assert resp.get_json()['ok'] is True


class TestNPlayer:
    def test_game_n_stops_and_reports_the_failing_player(self):
        funcs = [_compiled('tft', TFT)['func'], _compiled('bad', RETURNS_ONE)['func']]
        result = game_n(funcs, rounds=10, payoff_model=PUBLIC_GOODS, seed=1)
        assert result['match_complete'] is False
        assert result['error_player'] == 1
        assert 'returned 1' in result['error']
        # The round in progress is discarded, so no moves were counted for anyone.
        assert result['rounds_played'] == 0
        assert sum(result['coop_counts_by_player'].values()) == 0

    def test_failing_player_is_disqualified_and_group_replayed(self):
        result = group_tournament(
            [_compiled('all_c', ALL_C), _compiled('loops', LOOPS), _compiled('all_d', ALL_D)],
            rounds=10, seed=1, payoff_model=PUBLIC_GOODS)
        assert sorted(e['name'] for e in result['leaderboard']) == ['all_c', 'all_d']
        assert [d['name'] for d in result['disqualified']] == ['loops']
        assert 'instruction limit' in result['disqualified'][0]['reason']
        assert result['matches'][0]['disqualified'][0]['name'] == 'loops'
        # The others get a full, clean match: same scores as if 'loops' never entered.
        clean = group_tournament([_compiled('all_c', ALL_C), _compiled('all_d', ALL_D)],
                                 rounds=10, seed=1, payoff_model=PUBLIC_GOODS)

        def pts(r):
            return {e['name']: e['total_points'] for e in r['leaderboard']}
        assert pts(result) == pts(clean)
        assert result['matches'][0]['results']['rounds_played'] == 10

    def test_no_error_fields_on_a_clean_run(self):
        result = group_tournament([_compiled('tft', TFT), _compiled('all_d', ALL_D)],
                                  rounds=10, seed=1, payoff_model=PUBLIC_GOODS)
        assert result['runtime_errors'] == [] and result['disqualified'] == []
        assert 'disqualified' not in result['matches'][0]


class TestLonePlayerMerge:
    @pytest.mark.parametrize('n, group_size, expected_sizes', [
        (3, 2, [3]),          # 2 + 1 -> one group of 3
        (5, 2, [2, 3]),       # 2 + 2 + 1 -> the leftover joins the last full group
        (4, 2, [2, 2]),       # divides evenly: unchanged
        (7, 3, [3, 4]),
    ])
    def test_no_group_of_one(self, n, group_size, expected_sizes):
        strategies = [_compiled(f's{i}', ALL_C if i % 2 else ALL_D) for i in range(n)]
        result = group_tournament(strategies, rounds=5, group_size=group_size, seed=3,
                                  payoff_model=PUBLIC_GOODS)
        assert sorted(len(m['players']) for m in result['matches']) == sorted(expected_sizes)
        assert sum(len(m['players']) for m in result['matches']) == n

    def test_nobody_gets_a_free_win(self):
        # Two identical cooperators + one more: previously the lone one won 1/0/0 automatically.
        strategies = [_compiled(f'c{i}', ALL_C) for i in range(3)]
        result = group_tournament(strategies, rounds=5, group_size=2, seed=1, payoff_model=PUBLIC_GOODS)
        assert all(e['wins'] == 0 for e in result['leaderboard'])   # all tie in one group


class TestOSSimulation:
    def test_homogeneous_disqualifies_failing_strategies(self):
        result = run_full_simulation(
            [{'name': 'tft', 'code': TFT}, {'name': 'bad', 'code': RETURNS_ONE},
             {'name': 'loops', 'code': LOOPS}], num_cores=2, seed=1)
        assert list(result['strategies']) == ['tft']
        reasons = {e['name']: e['reason'] for e in result['errors']}
        assert 'returned 1' in reasons['bad']
        assert 'instruction limit' in reasons['loops']

    def test_heterogeneous_skips_mixtures_with_a_failing_strategy(self):
        result = run_heterogeneous_simulation(
            [{'name': 'tft', 'code': TFT}, {'name': 'all_d', 'code': ALL_D},
             {'name': 'bad', 'code': RETURNS_ONE}], num_cores=2, seed=1)
        assert [sorted(r['combination']) for r in result['results']] == [['all_d', 'tft']]
        assert [e['name'] for e in result['errors']] == ['bad']
