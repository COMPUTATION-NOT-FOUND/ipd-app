"""Run independent parts of a tournament on several CPU cores.

Why processes, not threads or the GPU
-------------------------------------
Strategies are plain Python run one call at a time under an instruction cap. Python threads
can't run Python code in parallel (the GIL), and none of this maps to a GPU. Separate processes
can, and every machine has several cores.

What runs in parallel
---------------------
Only work whose result can't depend on what ran before it in the same process:
* 1v1: each pair of strategies (game() compiles both strategies fresh for every match).
* OS simulation (homogeneous): each strategy's whole replicated run, plus the baseline. One
  strategy's runs stay together in one process, because a strategy that keeps state between
  calls would otherwise behave differently.
So a run on 8 cores gives exactly the same results as a run on 1.

Settings
--------
IPD_WORKERS (in the app's .env, set from the Settings page): how many cores to use. Default: all
but one. 1 turns it off. Small runs (fewer than MIN_JOBS pieces) always stay in this process,
since starting workers costs about a second each on Windows.
"""
import atexit
import logging
import os
import threading

log = logging.getLogger(__name__)

MIN_JOBS = 4          # below this, starting workers costs more than it saves
MAX_WORKERS = 32

_pool = None
_pool_size = 0
_lock = threading.Lock()


def default_workers():
    return max(1, min(MAX_WORKERS, (os.cpu_count() or 2) - 1))


def worker_count():
    """Cores to use for runs (1 = off)."""
    raw = os.environ.get('IPD_WORKERS', '').strip()
    if not raw:
        return default_workers()
    try:
        return max(1, min(MAX_WORKERS, int(raw)))
    except ValueError:
        return default_workers()


def shutdown():
    global _pool, _pool_size
    with _lock:
        if _pool is not None:
            _pool.shutdown(wait=False, cancel_futures=True)
        _pool, _pool_size = None, 0


atexit.register(shutdown)


def _get_pool(size):
    """One pool for the app's lifetime (rebuilt if the core count setting changes)."""
    global _pool, _pool_size
    with _lock:
        if _pool is None or _pool_size != size:
            if _pool is not None:
                _pool.shutdown(wait=False, cancel_futures=True)
            import multiprocessing as mp
            from concurrent.futures import ProcessPoolExecutor
            # Workers log to the console only, so they never fight over (or rotate) app.log.
            os.environ['IPD_WORKER'] = '1'
            # 'spawn' everywhere: forking a process that runs a threaded web server can deadlock.
            _pool = ProcessPoolExecutor(max_workers=size, mp_context=mp.get_context('spawn'))
            _pool_size = size
        return _pool


def run_calls(calls):
    """Run [(fn, args, kwargs), ...] and return the results in the same order.

    Uses worker processes when there is enough work and more than one core is allowed;
    otherwise (or if the workers can't start) runs them here, one after another. An exception
    raised by a call is raised here, as it would be when running in this process."""
    calls = list(calls)
    workers = worker_count()
    if workers <= 1 or len(calls) < MIN_JOBS:
        return [fn(*args, **kwargs) for fn, args, kwargs in calls]
    from concurrent.futures.process import BrokenProcessPool
    try:
        pool = _get_pool(workers)
        futures = [pool.submit(fn, *args, **kwargs) for fn, args, kwargs in calls]
    except (BrokenProcessPool, OSError, RuntimeError) as e:
        log.warning(f"Could not start worker processes ({e}); running on one core.")
        shutdown()
        return [fn(*args, **kwargs) for fn, args, kwargs in calls]
    try:
        return [f.result() for f in futures]
    except BrokenProcessPool as e:
        # A worker died (out of memory, killed). Redo the whole batch here so the run still ends.
        log.warning(f"A worker process stopped ({e}); finishing on one core.")
        shutdown()
        return [fn(*args, **kwargs) for fn, args, kwargs in calls]
