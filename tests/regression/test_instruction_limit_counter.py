"""Instruction cap: millions of strategy calls in one process must not overflow Python's
sys.monitoring version counter ("OverflowError: events set too many times", Python 3.13+)."""
import os
import sys

import pytest

from instruction_limit import (InstructionLimitExceeded, run_with_limit, release_tracer,
                               _idle_tracer)

pytestmark = pytest.mark.regression


def test_tracer_is_parked_not_switched_off_between_calls():
    """The overflow came from settrace(None) after every call. Between calls the idle tracer
    stays installed, and release_tracer switches it off once."""
    if sys.gettrace() is not None:
        pytest.skip("an outer tracer (debugger/coverage) is active")
    assert run_with_limit(lambda: 'C') == 'C'
    assert sys.gettrace() is _idle_tracer
    release_tracer()
    assert sys.gettrace() is None


def test_limit_still_stops_an_infinite_loop():
    def loop():
        while True:
            pass
    with pytest.raises(InstructionLimitExceeded):
        run_with_limit(loop, limit=1000)
    assert run_with_limit(lambda: 'D') == 'D'   # the next call still works
    release_tracer()


def test_outer_tracer_is_restored():
    calls = []

    def outer(frame, event, arg):
        calls.append(event)
        return None
    old = sys.gettrace()
    sys.settrace(outer)
    try:
        run_with_limit(lambda: 'C')
        assert sys.gettrace() is outer
    finally:
        sys.settrace(old)


@pytest.mark.skipif(sys.version_info < (3, 13) or not os.environ.get('IPD_SLOW_TESTS'),
                    reason="slow (~8.4M calls); set IPD_SLOW_TESTS=1 on Python 3.13+")
def test_more_than_2_pow_23_calls_in_one_process():
    f = lambda: 'C'   # noqa: E731
    for _ in range(2 ** 23 + 10):
        run_with_limit(f)
    release_tracer()
