"""Isolated (out-of-process) screening of strategy code.

Why this exists
---------------
The in-process instruction cap (``run_with_limit`` in app.py, via ``sys.settrace``) only counts
Python *line* events, so it only ever bounds interpreted loops. Work that runs in C — e.g.
catastrophic-regex backtracking (ReDoS, ``re`` is an allowed import) or a large ``itertools``
expansion — can burn minutes of CPU without the line counter ever moving, and a normal match has
no wall-clock cap. One such strategy, including one pulled from a classmate via the Hub, can hang
the whole run.

This module screens each strategy in a SEPARATE PROCESS with a hard wall-clock timeout before the
real tournament runs. A strategy that hangs (timeout), raises, or errors in a short test match is
reported so the caller can exclude and flag it. Killing a *process* reliably stops a C-level hang
that ``settrace`` cannot interrupt, and it pins the blame on the exact strategy.

It is opt-in: normal practice runs stay in-process and fast. The admin "Run isolated" option on a
tournament turns it on (used when running the official tournament over untrusted student code).
"""
import multiprocessing as mp
import os
import queue as _queue


# Defaults (overridable via env). Rounds are against a trivial all-cooperate opponent — enough to
# trip a per-round hang or crash without being slow. Timeout is per strategy.
try:
    SCREEN_ROUNDS = int(os.environ.get('ISOLATED_SCREEN_ROUNDS', '50'))
except (TypeError, ValueError):
    SCREEN_ROUNDS = 50
try:
    SCREEN_TIMEOUT_S = float(os.environ.get('ISOLATED_SCREEN_TIMEOUT_S', '10'))
except (TypeError, ValueError):
    SCREEN_TIMEOUT_S = 10.0


def _mp_context():
    """Prefer the ``fork`` start method where available (Linux/macOS): the child is a copy of the
    already-running app, so screening adds no per-strategy import cost and needs no __main__ guard.
    Windows has no fork, so fall back to the platform default (spawn), which re-imports app.py in
    the child — correct, just slower. (Py 3.14 defaults to forkserver, which re-imports the *main*
    module and breaks when that isn't importable, so we never rely on the default on Linux.)"""
    try:
        return mp.get_context('fork')
    except ValueError:
        return mp.get_context()


def _screen_worker(code, name, rounds, q):
    """Child process: run the strategy through a short real match and report the outcome.

    Imports ``game`` lazily so that spawning this worker doesn't import the Flask app until the
    child actually runs (and never at module import time in the parent)."""
    try:
        from app import game, _SMOKE_TEST_OPPONENT
        result = game(code, name, _SMOKE_TEST_OPPONENT, 'AllC',
                      rounds=rounds, mode='standard', seed=1)
        if isinstance(result, dict) and result.get('winner') == 'Error':
            q.put(('error', str(result.get('error') or 'strategy errored in a test match')))
        else:
            q.put(('ok', ''))
    except BaseException as e:   # noqa: BLE001 — also catches the instruction-cap BaseException
        q.put(('crash', f'{type(e).__name__}: {e}'))


def screen_strategy(code, name, rounds=None, timeout_s=None):
    """Screen a single strategy in a worker process.

    Returns (status, detail) where status is one of:
      'ok'      — ran a full short match cleanly
      'timeout' — exceeded the wall-clock limit (likely an infinite loop or C-level hang)
      'crash'   — raised an exception while running
      'error'   — ran but the engine reported it errored (e.g. bad return value)
    """
    rounds = SCREEN_ROUNDS if rounds is None else rounds
    timeout_s = SCREEN_TIMEOUT_S if timeout_s is None else timeout_s

    ctx = _mp_context()
    q = ctx.Queue()
    p = ctx.Process(target=_screen_worker, args=(code, name, rounds, q), daemon=True)
    p.start()
    try:
        # Read the result directly with a timeout: this both waits for the worker and enforces the
        # wall-clock cap, avoiding the join()/feeder-thread race where a finished child's result
        # isn't yet readable.
        status, detail = q.get(timeout=timeout_s)
    except _queue.Empty:
        status, detail = 'timeout', f'exceeded {timeout_s:g}s wall-clock limit'
    finally:
        if p.is_alive():
            p.terminate()
        p.join(1)
    return status, detail


def screen_strategies(strategies, rounds=None, timeout_s=None):
    """Screen a roster of ``{name, code, ...}`` dicts.

    Returns (survivors, flags):
      survivors — the roster entries that passed, in the original order
      flags     — a list of {name, reason, detail[, website_id, source, author]} for the rest,
                  suitable for display and for the publish-to-website flag handoff.
    """
    survivors, flags = [], []
    for s in strategies:
        name = s.get('name', '') if isinstance(s, dict) else ''
        code = s.get('code', '') if isinstance(s, dict) else ''
        status, detail = screen_strategy(code, name, rounds=rounds, timeout_s=timeout_s)
        if status == 'ok':
            survivors.append(s)
            continue
        flag = {'name': name, 'reason': status, 'detail': detail}
        for k in ('website_id', 'source', 'author'):
            if isinstance(s, dict) and s.get(k):
                flag[k] = s[k]
        flags.append(flag)
    return survivors, flags
