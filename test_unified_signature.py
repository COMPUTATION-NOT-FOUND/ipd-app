"""
Regression test: one unified 4-arg strategy signature works across all three modes
(1v1, N-player and the OS core simulation), and the legacy 3-arg form is still accepted.

Canonical signature:
    def strategy(last_moves, my_history, opponents_histories, meta) -> 'C' | 'D'
"""

from n_player_simulation import call_strategy
from core_simulation import extract_strategy_func, run_full_simulation


# A 4-arg strategy whose decision depends on inputs only available via the 4-arg
# signature (meta['round'] and the last_moves list), so a wrong/aggregated call or a
# silent default to 'C' would change the observed behaviour.
FOUR_ARG = """
def myStrategy(last_moves, my_history, opponents_histories, meta):
    if not last_moves:
        return 'C'              # first round
    if meta['round'] % 2 == 0:
        return 'D'
    return last_moves[0]
"""

# Legacy 3-arg tit-for-tat.
THREE_ARG = """
def myStrategy(opponent_last_move, my_history, opponent_history):
    return opponent_last_move if opponent_last_move else 'C'
"""


def test_extractor_prefers_four_arg():
    f = extract_strategy_func(FOUR_ARG)
    assert f is not None
    assert f.__code__.co_argcount == 4


def test_four_arg_dispatch_uses_meta_and_last_moves():
    f = extract_strategy_func(FOUR_ARG)
    # First round: empty last_moves -> 'C'
    assert call_strategy(f, [], [], [[]], {'round': 0, 'n_players': 2}) == 'C'
    # Even round with a defecting opponent -> 'D' (meta-driven, not aggregated)
    assert call_strategy(f, ['C'], ['C'], [['C']], {'round': 2, 'n_players': 2}) == 'D'
    # Odd round -> mirrors the (single) opponent's last move
    assert call_strategy(f, ['D'], ['C'], [['D']], {'round': 3, 'n_players': 2}) == 'D'


def test_three_arg_legacy_still_supported():
    g = extract_strategy_func(THREE_ARG)
    assert g.__code__.co_argcount == 3
    # First round -> None aggregated view -> 'C'
    assert call_strategy(g, [], [], [[]], {'round': 0}) == 'C'
    # Sees an aggregated defect -> 'D'
    assert call_strategy(g, ['D'], ['C'], [['D']], {'round': 1}) == 'D'


def test_four_arg_strategy_runs_in_os_simulation():
    # The OS sim must invoke the 4-arg strategy without error (homogeneous benchmark).
    res = run_full_simulation([{'name': 'X', 'code': FOUR_ARG}], num_cores=2, seed=1)
    assert isinstance(res, dict)
    assert 'error' not in res
