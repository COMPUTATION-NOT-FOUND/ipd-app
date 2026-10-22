"""Per-call instruction cap for strategy code (shared by 1v1, N-Player and the OS simulation).

Why the tracer is never switched off per call
---------------------------------------------
From Python 3.12, ``sys.settrace`` runs on top of ``sys.monitoring``. Every time tracing is turned
ON or OFF, the interpreter bumps a process-wide version counter, and from 3.13 that counter is
capped at 2**23 - 1. Turning the tracer on and off around each strategy call therefore fails with
``OverflowError: events set too many times`` after 8,388,607 calls, which a large tournament (the
OS simulation in particular) can reach in one process.

Swapping one trace function for another does not bump the counter. So ``run_with_limit`` swaps
between a counting tracer and an idle one, and ``release_tracer`` switches tracing fully off once,
at the end of a whole run.
"""

import os
import sys


class InstructionLimitExceeded(BaseException):
    """Raised when a strategy executes too many instructions.

    Derives from BaseException, NOT Exception, on purpose: sandboxed strategies can name
    `Exception`, so a `while True: try: ... except Exception: pass` body would otherwise
    swallow this and defeat the instruction cap entirely. Every run_with_limit call site
    catches InstructionLimitExceeded explicitly before its `except Exception` clause.
    """
    pass


class EngineTraceError(RuntimeError):
    """The interpreter refused to install the tracer. An engine problem, never the strategy's fault."""


# Per-round instruction (line-event) budget for a single strategy call. Bounds runaway loops
# without disqualifying legitimately expensive strategies.
try:
    MAX_STRATEGY_INSTRUCTIONS = int(os.environ.get('MAX_STRATEGY_INSTRUCTIONS', '200000'))
except (TypeError, ValueError):
    MAX_STRATEGY_INSTRUCTIONS = 200000
if MAX_STRATEGY_INSTRUCTIONS < 1000:
    MAX_STRATEGY_INSTRUCTIONS = 200000


def _idle_tracer(frame, event, arg):
    """Installed between strategy calls. Returning None means no frame gets line-traced."""
    return None


def _set_tracer(func):
    try:
        sys.settrace(func)
    except OverflowError as e:
        raise EngineTraceError(f"engine could not install the instruction counter: {e}") from None


def run_with_limit(func, *args, limit=None):
    """Call ``func(*args)``, raising InstructionLimitExceeded after ``limit`` line events."""
    if limit is None:
        limit = MAX_STRATEGY_INSTRUCTIONS
    instr_count = [limit]  # mutable so the tracer can change it

    def trace_instructions(frame, event, arg):
        if event == 'line':
            instr_count[0] -= 1
            if instr_count[0] <= 0:
                raise InstructionLimitExceeded("Loop limit exceeded")
        return trace_instructions

    previous = sys.gettrace()
    _set_tracer(trace_instructions)
    try:
        return func(*args)
    finally:
        # Never settrace(None) here (see module docstring). Restore an outer tracer such as a
        # debugger or coverage if there was one, else park the idle tracer.
        sys.settrace(previous if previous is not None else _idle_tracer)


def release_tracer():
    """Switch tracing fully off. Call once at the end of a whole run, not after each call."""
    if sys.gettrace() is _idle_tracer:
        sys.settrace(None)
