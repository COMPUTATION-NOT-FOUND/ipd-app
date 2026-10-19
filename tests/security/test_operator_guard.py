"""Regression tests for the guarded `operator` module (student security review, Oct 2026).

operator.attrgetter / methodcaller take attribute names as strings, which the AST screen can't
see. Strategies now receive a guarded copy of `operator` that applies the same leading-underscore
rule as the guarded getattr. These tests check the rule holds through BOTH sandbox entry points
(app.py for 1v1 / N-Player, core_simulation.py for the OS sim) and that ordinary use still works.
Adapted from the student's test_operator_shim.py.
"""
import pytest

import app
import core_simulation
from core_simulation import _safe_attrgetter, _safe_methodcaller

pytestmark = pytest.mark.security


def test_attrgetter_internal_name_blocked():
    with pytest.raises(AttributeError):
        _safe_attrgetter('__class__')


def test_attrgetter_dotted_internal_name_blocked():
    with pytest.raises(AttributeError):
        _safe_attrgetter('value.__class__')


def test_methodcaller_internal_name_blocked():
    with pytest.raises(AttributeError):
        _safe_methodcaller('__init__')


def test_attrgetter_normal_use_still_works():
    class Foo:
        mood = 'happy'
    assert _safe_attrgetter('mood')(Foo()) == 'happy'


def test_attrgetter_nested_normal_use_still_works():
    class Bar:
        value = 42
    class Foo:
        bar = Bar()
    assert _safe_attrgetter('bar.value')(Foo()) == 42


def _strategy_from(get_globals, code):
    g = get_globals()
    exec(code, g)
    return g['s']


IMPORT_FORMS = [
    "import operator\nget = operator.attrgetter\n",
    "from operator import attrgetter as get\n",
]


@pytest.mark.parametrize('get_globals', [app.get_safe_globals, core_simulation.get_safe_globals],
                         ids=['app', 'os_sim'])
@pytest.mark.parametrize('prelude', IMPORT_FORMS, ids=['import', 'from_import'])
def test_both_sandboxes_hand_out_the_guarded_attrgetter(get_globals, prelude):
    code = prelude + "def s(last_moves, my_history, opponents_histories, meta):\n    return get('__class__')(())\n"
    assert app.is_safe_code(code)[0]          # the static screen can't see a name inside a string
    with pytest.raises(AttributeError, match='internal attribute'):
        _strategy_from(get_globals, code)([], [], [], {})


@pytest.mark.parametrize('get_globals', [app.get_safe_globals, core_simulation.get_safe_globals],
                         ids=['app', 'os_sim'])
def test_ordinary_operator_use_in_a_strategy(get_globals):
    code = ("import operator\n"
            "def s(last_moves, my_history, opponents_histories, meta):\n"
            "    n = operator.add(len(my_history), 1)\n"
            "    first = operator.itemgetter(0)\n"
            "    return 'C' if n % 2 else first(['D'])\n")
    s = _strategy_from(get_globals, code)
    assert s([], [], [], {}) == 'C'
    assert s([], ['C'], [], {}) == 'D'


def test_each_import_gets_its_own_copy():
    """One strategy rebinding operator.attrgetter must not leak into another strategy or into
    the real stdlib module."""
    import operator as real_operator
    tamper = ("import operator\noperator.attrgetter = None\n"
              "def s(last_moves, my_history, opponents_histories, meta):\n    return 'C'\n")
    _strategy_from(app.get_safe_globals, tamper)
    check = ("import operator\n"
             "def s(last_moves, my_history, opponents_histories, meta):\n"
             "    return operator.attrgetter('real')(meta)\n")
    class Meta:
        real = 'D'
    assert _strategy_from(app.get_safe_globals, check)([], [], [], Meta()) == 'D'
    assert real_operator.attrgetter is not None


def test_guarded_strategy_fails_its_match_with_a_clear_reason():
    code = ("import operator\n"
            "def s(last_moves, my_history, opponents_histories, meta):\n"
            "    operator.attrgetter('__class__')(())\n    return 'C'\n")
    result = app.game(code, 'probe', "def t(a, b, c, d):\n    return 'C'", 'allc', rounds=5)
    assert result['winner'] == 'Error'
    assert 'internal attribute' in result['error']
