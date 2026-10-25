"""The strategy picker chooses the entry point, not a same-arity helper defined above it
(review, Oct 2026: the first 4-arg function won, so a 4-arg helper was run as the strategy)."""
import app as ipd
import core_simulation
from n_player_simulation import referenced_names

HELPER_FIRST = """
def helper(last_moves, my_history, opponents_histories, meta):
    return len(my_history)

def my_strategy(last_moves, my_history, opponents_histories, meta):
    return 'C' if helper(last_moves, my_history, opponents_histories, meta) < 3 else 'D'
"""
HELPER_LAST = """
def my_strategy(last_moves, my_history, opponents_histories, meta):
    return 'C' if helper(last_moves, my_history, opponents_histories, meta) < 3 else 'D'

def helper(last_moves, my_history, opponents_histories, meta):
    return len(my_history)
"""
SELF_CALL = """
def strat(last_moves, my_history, opponents_histories, meta):
    if meta.get('again'):
        return 'C'
    return strat(last_moves, my_history, opponents_histories, {'again': True})

def other(a, b, c, d):
    return 'D'
"""
TWO_UNUSED = """
def first(a, b, c, d):
    return 'C'

def second(a, b, c, d):
    return 'D'
"""


def _pick(code):
    g = ipd.get_safe_globals()
    exec(code, g)
    return ipd.extract_strategy_function(code, g, prefer_n_args=4).__name__


def test_helper_above_strategy_is_skipped():
    assert _pick(HELPER_FIRST) == 'my_strategy'
    r = ipd.game(HELPER_FIRST, 'a', HELPER_LAST, 'b', rounds=6, seed=1)
    assert r.get('error') is None and r['rounds'] == 6


def test_helper_below_strategy_still_works():
    assert _pick(HELPER_LAST) == 'my_strategy'


def test_recursion_does_not_count_as_a_reference():
    assert 'strat' not in referenced_names(SELF_CALL)
    assert _pick(SELF_CALL) == 'strat'


def test_ties_keep_definition_order():
    assert _pick(TWO_UNUSED) == 'first'


def test_os_sim_picker_matches():
    assert core_simulation.extract_strategy_func(HELPER_FIRST).__name__ == 'my_strategy'
    assert core_simulation.extract_strategy_func(TWO_UNUSED).__name__ == 'first'
