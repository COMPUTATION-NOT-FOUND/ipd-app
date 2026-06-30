"""
Sandbox hardening regression tests (one-last-run security pass).

Covers two in-process defenses added to is_safe_code() because the PythonAnywhere
free tier cannot spawn a subprocess for OS-level isolation:

  Item 1 — block string-based attribute traversal (str.format / Formatter field syntax),
           which can hide dunder names inside string literals the AST attr-check can't see.
  Item 2 — reject statically-detectable memory-bomb constructs (huge literals, oversized
           sequence multiplication / exponentiation, int()/range() on huge constants).

Tests PASS when the dangerous construct is rejected and benign code is still allowed.
"""

import pytest

from app import is_safe_code

pytestmark = pytest.mark.security


class TestFormatTraversalBlocked:
    """Item 1: format / field-traversal methods must be rejected."""

    @pytest.mark.parametrize("code", [
        "'{0.__class__}'.format(x)",
        "'{0.__class__.__init__.__globals__}'.format(obj)",
        "x.format_map({})",
        "'{0[secret]}'.format_map(d)",
        "import string\nstring.Formatter().vformat(s, a, k)",
        "string.Formatter().get_field('0.__class__', (x,), {})",
    ])
    def test_format_traversal_rejected(self, code):
        safe, err = is_safe_code(code)
        assert safe is False
        assert err and ('format' in err.lower() or 'traversal' in err.lower())

    @pytest.mark.parametrize("code", [
        # f-strings are fine: any attribute access is syntactic and the dunder check covers it.
        "def s(a, b, c, d):\n    name = f'move-{a}'\n    return 1",
        # Normal (non-dunder) attribute access on allowed objects stays allowed.
        "def s(a, b, c, d):\n    return len([1, 2, 3])",
    ])
    def test_benign_string_use_allowed(self, code):
        safe, err = is_safe_code(code)
        assert safe is True, err


class TestMemoryBombBlocked:
    """Item 2: statically-detectable allocation bombs must be rejected."""

    @pytest.mark.parametrize("code", [
        "[0] * 10**9",             # huge list allocation
        "10**9 * [0]",             # ... either operand order
        "[1, 2] * 10**8",
        "(1,) * 10**9",
        "'a' * 10**9",             # huge string allocation
        "int('9' * 10**7)",        # the inner string-multiply is the bomb
        "range(10**12)",
        "x = " + "9" * 200,        # 200-digit integer literal
        "x = 10**10**8",           # digit-EXPLOSION (~10^8 digits), not just a big value
    ])
    def test_memory_bomb_rejected(self, code):
        safe, err = is_safe_code(code)
        assert safe is False
        assert err and ('memory-bomb' in err.lower() or 'oversized' in err.lower()
                        or 'exceeds' in err.lower())

    @pytest.mark.parametrize("code", [
        "def s(a, b, c, d):\n    return [0] * 100",
        "def s(a, b, c, d):\n    return sum(range(1000000))",
        "def s(a, b, c, d):\n    return 'x' * 50",
        "def s(a, b, c, d):\n    return 12345 * 6",
        "def s(a, b, c, d):\n    return int('42')",
        # Regression: a bare power is cheap, valid arithmetic and must NOT be flagged as a bomb.
        # (Reported by a real strategy `multiplicative_update_merciful` using 2**32 as a scale.)
        "MOD = 2**32\ndef s(a, b, c, d):\n    return (a * 2**32) % MOD",
        "def s(a, b, c, d):\n    return 2**64",
        "def s(a, b, c, d):\n    return 10**1000",
        "def s(a, b, c, d):\n    n = 5\n    return n * 10**9",   # int * int, not a sequence
    ])
    def test_benign_sizes_allowed(self, code):
        safe, err = is_safe_code(code)
        assert safe is True, err


class TestRealStrategyStillPasses:
    """A normal tit-for-tat strategy must survive the hardened checker."""

    def test_tit_for_tat_allowed(self):
        code = (
            "def strategy(last_move, my_history, opponent_histories, info):\n"
            "    if last_move is None:\n"
            "        return 1\n"
            "    return last_move\n"
        )
        safe, err = is_safe_code(code)
        assert safe is True, err
