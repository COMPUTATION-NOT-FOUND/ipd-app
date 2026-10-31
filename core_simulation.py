import random
import math
import statistics
import itertools
import time
from cache_model import build_core_hierarchies
from schedulers import build_scheduler
from n_player_simulation import (call_strategy, run_with_limit, validate_move,
                                 describe_strategy_error, InstructionLimitExceeded,
                                 EngineTraceError, release_tracer, referenced_names)
from ranking import normalize_ranking, rank_entries, describe_ranking


class StrategyRuntimeError(Exception):
    """A core's strategy failed mid-simulation (crash, instruction cap, or a bad move).

    Carries the core index so the caller can tell which strategy of a mixture to disqualify.
    """
    def __init__(self, core_idx, reason):
        super().__init__(reason)
        self.core_idx = core_idx
        self.reason = reason

# Canonical whitelist of modules a submitted strategy may import. Single source of truth, shared
# by app.py (which imports it) so the sandbox + the static `is_safe_code` checker stay in sync and
# the "one signature works everywhere" promise holds across 1v1 / N-Player / OS-sim.
ALLOWED_IMPORTS = frozenset({
    'math', 'random', 'collections', 'itertools', 'functools',
    'statistics', 'heapq', 'bisect', 'copy', 'operator',
    'fractions', 'decimal', 'enum', 'typing', 'dataclasses',
    'string', 're', 'abc', 'contextlib', 'numbers',
})

# Capture the real __import__ at module load so the sandbox wrapper can delegate to it.
_real_import = __import__


# Guarded attribute access for the sandbox. `is_safe_code` (app.py) blocks `x.__class__`
# syntactically, but the string form getattr(x, '__class__') is invisible to the AST screen and
# would walk straight out of the sandbox. These wrappers apply the same rule at runtime.
def _guarded_getattr(obj, name, *default):
    if isinstance(name, str) and name.startswith('_'):
        raise AttributeError(f"Access to internal attribute '{name}' is not allowed")
    return getattr(obj, name, *default)


def _guarded_setattr(obj, name, value):
    if isinstance(name, str) and name.startswith('_'):
        raise AttributeError(f"Setting internal attribute '{name}' is not allowed")
    setattr(obj, name, value)


def _guarded_delattr(obj, name):
    if isinstance(name, str) and name.startswith('_'):
        raise AttributeError(f"Deleting internal attribute '{name}' is not allowed")
    delattr(obj, name)


def _guarded_hasattr(obj, name):
    # Returns False rather than raising -- hasattr's contract is a bool.
    if isinstance(name, str) and name.startswith('_'):
        return False
    return hasattr(obj, name)


# Guarded `operator` module. operator.attrgetter / operator.methodcaller take attribute names as
# strings, which the AST screen in is_safe_code cannot see and which never pass through the
# guarded getattr above. This applies the same leading-underscore rule to those strings.
# (Fix contributed by a student security review, Oct 2026.)
import types as _types
import operator as _real_operator


def _reject_internal_attr(attr_path):
    if isinstance(attr_path, str):
        for part in attr_path.split('.'):
            if part.startswith('_'):
                raise AttributeError(f"Access to internal attribute '{part}' is not allowed")


def _safe_attrgetter(*attrs):
    for a in attrs:
        _reject_internal_attr(a)
    return _real_operator.attrgetter(*attrs)


def _safe_methodcaller(name, *args, **kwargs):
    _reject_internal_attr(name)
    return _real_operator.methodcaller(name, *args, **kwargs)


def _make_guarded_operator():
    """A fresh module object per import, so one strategy reassigning e.g. operator.attrgetter
    can't affect another strategy (or the real stdlib module the app itself uses)."""
    shim = _types.ModuleType('operator')
    for n in dir(_real_operator):
        if not n.startswith('__'):
            setattr(shim, n, getattr(_real_operator, n))
    shim.attrgetter = _safe_attrgetter
    shim.methodcaller = _safe_methodcaller
    return shim


# Allowed modules that strategies receive as a guarded copy instead of the real module.
# Shared by both sandbox import hooks (here and in app.py's get_safe_globals).
GUARDED_MODULE_FACTORIES = {'operator': _make_guarded_operator}


def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    """`__import__` for strategy code: only ALLOWED_IMPORTS, with guarded copies where defined."""
    top_level = name.split('.')[0]
    if top_level not in ALLOWED_IMPORTS:
        raise ImportError(f"Import of '{name}' is not allowed in strategy code")
    factory = GUARDED_MODULE_FACTORIES.get(top_level)
    if factory is not None:
        return factory()
    return _real_import(name, globals, locals, fromlist, level)


# Builtins exposed to sandboxed strategy code. Kept byte-identical across the three copies
# (app.py, core_simulation.py, ipd-hub/app.py) so a strategy behaves the same in 1v1, N-Player,
# the OS sim and the Hub. Notable inclusions/exclusions:
#   * __build_class__ -- CPython resolves this through __builtins__ for EVERY `class` statement,
#     so without it no strategy can define a class at all.
#   * exception classes -- without them `except ValueError:` is itself a NameError.
#   * BaseException is deliberately absent: InstructionLimitExceeded derives from it precisely so
#     that `except Exception:` inside a strategy cannot swallow the instruction cap.
#   * `format` the builtin is safe; the traversal vector is "{0.__class__}".format(...) and
#     string.Formatter, both still blocked by _FORMAT_TRAVERSAL_METHODS in is_safe_code.
#   * exec/eval/compile/open/input/print stay out (also rejected statically by is_safe_code).
_SANDBOX_BUILTINS = {
    'abs': abs, 'all': all, 'any': any, 'bool': bool,
    'complex': complex, 'divmod': divmod, 'enumerate': enumerate,
    'filter': filter, 'float': float, 'int': int, 'len': len,
    'list': list, 'map': map, 'max': max, 'min': min,
    'pow': pow, 'range': range, 'reversed': reversed,
    'round': round, 'set': set, 'sorted': sorted,
    'str': str, 'sum': sum, 'tuple': tuple, 'zip': zip,
    'dict': dict,
    # --- class support ---
    '__build_class__': __build_class__,
    'object': object, 'super': super, 'property': property,
    'staticmethod': staticmethod, 'classmethod': classmethod,
    # --- ordinary builtins strategies routinely assume ---
    'ord': ord, 'chr': chr, 'isinstance': isinstance, 'issubclass': issubclass,
    'type': type, 'frozenset': frozenset, 'bytes': bytes, 'hash': hash,
    'repr': repr, 'iter': iter, 'next': next, 'slice': slice,
    'callable': callable, 'id': id, 'format': format,
    'bin': bin, 'hex': hex, 'oct': oct,
    # --- guarded attribute access (see wrappers above) ---
    'getattr': _guarded_getattr, 'setattr': _guarded_setattr,
    'delattr': _guarded_delattr, 'hasattr': _guarded_hasattr,
    # --- exception classes ---
    'Exception': Exception, 'ArithmeticError': ArithmeticError,
    'ZeroDivisionError': ZeroDivisionError, 'OverflowError': OverflowError,
    'FloatingPointError': FloatingPointError,
    'ValueError': ValueError, 'TypeError': TypeError, 'KeyError': KeyError,
    'IndexError': IndexError, 'LookupError': LookupError,
    'AttributeError': AttributeError, 'NameError': NameError,
    'RuntimeError': RuntimeError, 'RecursionError': RecursionError,
    'StopIteration': StopIteration, 'NotImplementedError': NotImplementedError,
    'AssertionError': AssertionError,
}


def get_safe_globals(seed=None):
    """Return a dictionary of safe globals for strategy execution.

    Args:
        seed: Optional random seed. If provided, creates an independent seeded
              Random instance. If None, provides the global random module.

    DETERMINISM NOTE: When seed is provided, strategies using random.* functions
    will be deterministic and reproducible. When seed is None (default), strategies
    use the global random module and will NOT be deterministic.

    For fully deterministic strategies, use a seed value.

    Mirrors app.py's get_safe_globals (used by 1v1 / N-Player) so a strategy behaves identically in
    the OS simulation: the same `ALLOWED_IMPORTS` are importable, and a bare ``randint`` is injected.
    """
    globals_dict = {}

    # Fresh copy per call: a shared mutable dict would let one strategy's exec leak names into
    # another's.
    safe_builtins = dict(_SANDBOX_BUILTINS)
    safe_builtins['__import__'] = guarded_import

    # Provide seeded random instance if seed is given, otherwise global module
    random_provider = random.Random(seed) if seed is not None else random

    globals_dict.update({
        '__builtins__': safe_builtins,
        # Required for `class` statements: a class body evaluates `__module__ = __name__`, so
        # without this every class definition dies with "name '__name__' is not defined".
        # An inert placeholder, not a real module name -- nothing can be looked up from it.
        '__name__': 'strategy',
        'random': random_provider,
        'math': math,
        'randint': random_provider.randint if seed is not None else random.randint,
    })
    return globals_dict

def extract_strategy_func(code, seed=None):
    """Extract the first callable function from the provided code string.

    Args:
        code: Strategy code as a string
        seed: Optional random seed for deterministic strategy execution

    Returns:
        Callable strategy function or None if extraction fails
    """
    try:
        # Simple extraction logic with optional seeding
        globals_dict = get_safe_globals(seed=seed)
        # Names present before exec (random, math, randint, ...) are sandbox injections, NOT user
        # functions — exclude them so the picker doesn't grab e.g. the injected `randint`.
        injected = set(globals_dict.keys())
        exec(code, globals_dict)
        funcs = [obj for name, obj in globals_dict.items()
                 if callable(obj) and not name.startswith('__') and name not in injected]
        if not funcs:
            return None
        # Functions no other code refers to first (a same-arity helper is called by the strategy,
        # the strategy itself is not); sorted() is stable, so definition order breaks ties.
        used = referenced_names(code)
        funcs = sorted(funcs, key=lambda f: getattr(f, '__name__', '') in used)
        # Prefer the unified 4-arg signature, then the legacy 3-arg, then the first
        # user function — so one 4-arg strategy works across 1v1 / N-player / OS sim.
        for n in (4, 3):
            for f in funcs:
                code_obj = getattr(f, '__code__', None)
                if code_obj is not None and code_obj.co_argcount == n:
                    return f
        return funcs[0]
    except Exception:
        return None


# --- Workload model (documented, seeded synthetic profiles) ---
#
# All times are in simulation ticks; bursts are CPU-time in ticks. Every random draw uses the
# simulator's seeded RNG, so a given seed reproduces the exact dataset.
#
# Modeling basis (so the dataset is defensible at FYP / research level):
#   * Arrivals — the Poisson process (exponential inter-arrival times) is the standard model for
#     independent job arrivals; "Bursty" adds batch arrivals (idle gaps then floods) for spikes.
#   * CPU service times — real CPU/job service times are HEAVY-TAILED (most jobs short, a few very
#     long), well approximated by a log-normal distribution; we use a clamped log-normal rather than
#     a flat uniform range. "Mixed" is an explicit bimodal interactive-(short)+batch-(long) profile.
#   * Memory footprint — each process draws its own working-set size (log-normal), so cache pressure
#     varies across jobs instead of every job sharing one fixed footprint.
WORKLOAD_PROFILES = ['Uniform', 'Poisson', 'Mixed', 'Bursty']
FULL_PROCESS_COUNT = 50      # processes per benchmark run

# Move played by every core in the baseline (vanilla scheduler) run. 'D' = keep running (the
# normal no-yield process), so the scheduler's textbook behaviour is measured without PD yielding.
BASELINE_MOVE = 'D'

# Heavy-tailed CPU bursts (log-normal, clamped). median = typical burst; sigma controls tail weight.
BURST_MEDIAN, BURST_SIGMA = 45.0, 0.6   # general jobs
BURST_MIN, BURST_MAX = 10, 400          # clamp so the tail stays bounded for the sim
UNIFORM_ARRIVAL_INTERVAL = 15           # one arrival every N ticks
UNIFORM_BURST_MIN, UNIFORM_BURST_MAX = 30, 80   # Uniform = regular/light-tailed (flat range)
POISSON_MEAN_INTERARRIVAL = 20          # exponential inter-arrival mean (ticks)
MIXED_ARRIVAL_INTERVAL = 10
MIXED_LONG_JOB_FRACTION = 0.20          # 20% long (batch) jobs, 80% short (interactive)
MIXED_SHORT_MEDIAN, MIXED_SHORT_SIGMA = 20.0, 0.5
MIXED_LONG_MEDIAN, MIXED_LONG_SIGMA = 280.0, 0.4
BURSTY_BATCH_SIZE = 10                   # arrivals cluster into batches of this size
BURSTY_BATCH_INTERVAL = 300              # ticks between batches
BURSTY_BURST_MEDIAN, BURSTY_BURST_SIGMA = 35.0, 0.55

# Per-process memory working set (bytes), log-normal so footprints (and cache pressure) vary.
WORKING_SET_MEDIAN, WORKING_SET_SIGMA = 4096.0, 0.5
WORKING_SET_MIN, WORKING_SET_MAX = 1024, 32768

MAX_SIM_TICKS = 5000           # minimum tick cap; the cap grows with the task count
TICKS_PER_TASK_CAP = 150       # cap = max(MAX_SIM_TICKS, 150 x tasks): 400 tasks on 2 cores need ~25k

# Options offered for the OS simulation (validated by the /os-simulation route).
TASK_COUNT_CHOICES = (50, 100, 200, 400)   # tasks per workload in one run
WARMUP_CHOICES = (0.0, 0.1, 0.2)           # share of earliest tasks left out of the averages
MAX_RUNS = 30                              # seeded runs averaged per configuration
OVERLOAD = 1.0                             # load above this = the queue keeps growing (see _calculate_metrics)

# Cache misses an access incurs, by the level that finally served it (L1 hit = 0 misses).
_MISSES_BY_HIT_LEVEL = {'L1': 0, 'L2': 1, 'L3': 2, 'memory': 3}


def _clamped_lognormal(rng, median, sigma, lo, hi):
    """Seeded log-normal draw (median = exp(mu)) clamped to [lo, hi], returned as an int.

    Log-normal gives the heavy right tail characteristic of real CPU service times / memory
    footprints: most samples near the median, a few much larger.
    """
    val = rng.lognormvariate(math.log(median), sigma)
    return int(max(lo, min(hi, val)))


class Process:
    def __init__(self, pid, arrival_time, burst_time):
        self.pid = pid
        self.arrival_time = arrival_time
        self.burst_time = burst_time
        self.remaining_time = burst_time
        self.start_time = None
        self.completion_time = None
        self.turnaround_time = None
        self.waiting_time = 0
        self.response_time = None
        self.cache_misses = 0
        self.context_switches = 0
        # Scheduler-specific bookkeeping
        self.vruntime = 0.0      # CFS
        self.mlfq_level = 0      # MLFQ
        self.last_core = None    # affinity / cache warmth


def filler_strategy(*_args):
    """A filler core in an elimination group that is short of strategies: it always plays 'D',
    i.e. the plain scheduler (the same as the baseline run). It is never ranked."""
    return 'D'


class Core:
    def __init__(self, id, strategy_func):
        self.id = id
        self.strategy_func = strategy_func
        self.process = None
        self.last_move = 'C'
        self.slice_remaining = 0


class OSSimulator:
    """Tick-driven multi-core scheduler simulation.

    Each core runs a PD strategy (personality). At every dispatch the core plays one
    move against the *system contention* signal (Cooperate when the system is idle,
    the strategy decides when it is congested). The selected `scheduler` policy
    interprets that Cooperate/Defect bit in its own native terms (see schedulers.py).
    A fixed industry-standard L1/L2/shared-L3 cache hierarchy backs every core.
    """

    def __init__(self, workload_type, core_strategies, scheduler='round_robin',
                 seed=None, num_cores=2, record_trace=False, fixed_move=None, warmup_fraction=0.0,
                 per_core=False):
        if len(core_strategies) != num_cores:
            raise ValueError(f"core_strategies must have {num_cores} strategies, got {len(core_strategies)}")

        self.workload_type = workload_type
        self.num_cores = num_cores
        # When set (e.g. 'D'), cores skip their PD strategy and always play this move, so the
        # scheduler runs in its textbook ("vanilla") form. Used to produce the baseline run.
        self.fixed_move = fixed_move
        # Share of the earliest-arriving tasks left out of the per-task averages (warm-up: they
        # meet an empty system, so they are served unusually fast). 0 = measure every task.
        self.warmup_fraction = max(0.0, min(0.5, float(warmup_fraction or 0.0)))

        # Separate random streams, all derived from the seed (one is picked when none is given).
        # The task list, the scheduler and the strategies' meta['rng'] each draw from their own
        # stream, and every task gets its own memory-access stream (see generate_workload). So on a
        # given seed every strategy faces the same tasks AND each task makes the same memory
        # accesses, however the strategy schedules it or uses randomness itself. (With one shared
        # stream, a strategy drawing random numbers shifted every later memory access.)
        self.seed = seed if seed is not None else random.randrange(1, 2 ** 31)
        self.task_rng = random.Random(f"{self.seed}:tasks")
        self.rng = random.Random(f"{self.seed}:strategy")     # handed to strategies as meta['rng']
        self.cores = [Core(i, core_strategies[i]) for i in range(num_cores)]

        self.scheduler = scheduler if not isinstance(scheduler, str) \
            else build_scheduler(scheduler, num_cores, random.Random(f"{self.seed}:scheduler"))
        self.scheduler_name = getattr(self.scheduler, 'name', str(scheduler))

        self.cache_hierarchies = build_core_hierarchies(random.Random(f"{self.seed}:cache"), num_cores)

        # Per-core PD move history and the contention ("opponent") history it saw.
        self.core_moves = [[] for _ in range(num_cores)]
        self.core_contention = [[] for _ in range(num_cores)]

        # Elimination mode: give each core its own numbers (tasks it started, tasks it finished)
        # and rotate which core picks work first each tick, so no seat is favoured. Off by
        # default, so the legacy runs stay exactly as they were.
        self.per_core = per_core
        self.core_completed = [0] * num_cores
        self.core_busy = [0] * num_cores

        self.completed_processes = []
        self.all_processes = []
        self.busy_ticks = 0          # core-ticks spent running a task (for the load figure)
        self.time = 0
        self.max_time = MAX_SIM_TICKS

        # Optional bounded per-tick trace for the Gantt visual.
        self.record_trace = record_trace
        self.trace = []
        self.trace_cap = 600

    def generate_workload(self, count=FULL_PROCESS_COUNT):
        procs = []
        if self.workload_type == 'Uniform':
            # Regular, evenly-spaced arrivals with a light-tailed (flat) burst range.
            for i in range(count):
                procs.append(Process(i, i * UNIFORM_ARRIVAL_INTERVAL,
                                     self.task_rng.randint(UNIFORM_BURST_MIN, UNIFORM_BURST_MAX)))
        elif self.workload_type == 'Poisson':
            # Exponential inter-arrivals (Poisson process) + heavy-tailed bursts.
            curr = 0
            for i in range(count):
                curr += int(self.task_rng.expovariate(1.0 / POISSON_MEAN_INTERARRIVAL))
                procs.append(Process(i, curr,
                                     _clamped_lognormal(self.task_rng, BURST_MEDIAN, BURST_SIGMA, BURST_MIN, BURST_MAX)))
        elif self.workload_type == 'Mixed':
            # Bimodal: mostly short interactive jobs, a few long batch jobs (each heavy-tailed).
            for i in range(count):
                is_long = self.task_rng.random() < MIXED_LONG_JOB_FRACTION
                if is_long:
                    burst = _clamped_lognormal(self.task_rng, MIXED_LONG_MEDIAN, MIXED_LONG_SIGMA, BURST_MIN, BURST_MAX)
                else:
                    burst = _clamped_lognormal(self.task_rng, MIXED_SHORT_MEDIAN, MIXED_SHORT_SIGMA, BURST_MIN, BURST_MAX)
                procs.append(Process(i, i * MIXED_ARRIVAL_INTERVAL, burst))
        elif self.workload_type == 'Bursty':
            # Batched arrivals (idle gaps then floods) + heavy-tailed bursts.
            for i in range(count):
                arrival = (i // BURSTY_BATCH_SIZE) * BURSTY_BATCH_INTERVAL
                procs.append(Process(i, arrival,
                                     _clamped_lognormal(self.task_rng, BURSTY_BURST_MEDIAN, BURSTY_BURST_SIGMA, BURST_MIN, BURST_MAX)))
        else:  # Default fallback
            for i in range(count):
                procs.append(Process(i, i * MIXED_ARRIVAL_INTERVAL, 50))

        # Give each process its own memory working-set size (log-normal) so cache
        # pressure varies across jobs rather than every job sharing one fixed footprint.
        for p in procs:
            p.working_set_size = _clamped_lognormal(
                self.task_rng, WORKING_SET_MEDIAN, WORKING_SET_SIGMA, WORKING_SET_MIN, WORKING_SET_MAX)

        # Each task's own memory-access stream: the same accesses whenever it runs.
        for p in procs:
            p.mem_rng = random.Random(f"{self.seed}:memory:{p.pid}")

        # Room for every task to finish: 400 tasks on 2 cores need ~25k ticks.
        self.max_time = max(MAX_SIM_TICKS, TICKS_PER_TASK_CAP * len(procs))
        self.all_processes = sorted(procs, key=lambda x: x.arrival_time)
        return len(procs)

    def _contention_move(self, core_idx):
        """Derive the 'opponent' move the core plays against: Defect = system congested."""
        ready = self.scheduler.total_ready()
        others_defecting = sum(
            1 for j, c in enumerate(self.cores)
            if j != core_idx and c.process is not None and c.last_move == 'D'
        )
        queue_pressure = ready > self.num_cores
        majority_defect = self.num_cores > 1 and others_defecting * 2 >= (self.num_cores - 1)
        return 'D' if (queue_pressure or majority_defect) else 'C'

    def _play_move(self, core_idx, contention_move):
        core = self.cores[core_idx]
        # Route through the shared signature negotiation so the same strategy works
        # in 1v1, N-player and the OS sim. The system-contention signal is presented
        # as a single "opponent": its current move + per-core contention history.
        my_history = self.core_moves[core_idx]
        contention_history = self.core_contention[core_idx]
        if self.fixed_move is not None or core.strategy_func is filler_strategy:
            # Baseline run (or a filler core): ignore the PD strategy and play a constant move so
            # the scheduler behaves in its textbook form (no strategy-driven yielding).
            move = self.fixed_move or 'D'
        else:
            meta = {
                'round': len(my_history),
                'n_players': self.num_cores,
                'player_index': core_idx,
                'rng': self.rng,
                'tournament_info': {'format': 'os_simulation', 'n_players': self.num_cores,
                                    'scheduler': self.scheduler_name},
            }
            # Same per-call bounds as 1v1 / N-Player: the instruction cap stops a runaway loop,
            # and the move must be exactly 'C' or 'D'. A failure is raised, not papered over.
            try:
                move = validate_move(run_with_limit(
                    call_strategy,
                    core.strategy_func,
                    [contention_move] if my_history else [],
                    my_history,
                    [contention_history],
                    meta,
                ))
            except EngineTraceError:
                raise   # engine fault: never blame the strategy
            except InstructionLimitExceeded as e:
                raise StrategyRuntimeError(core_idx, describe_strategy_error(e)) from None
            except Exception as e:
                raise StrategyRuntimeError(core_idx, describe_strategy_error(e)) from None
        core.last_move = move
        self.core_moves[core_idx].append(move)
        self.core_contention[core_idx].append(contention_move)
        return move

    def _generate_memory_access(self, process):
        """Realistic per-tick memory accesses with temporal/spatial locality."""
        working_set_base = process.pid * 0x10000  # 64 KB region per process
        # Per-process hot working set (varies by job; falls back to 4 KB if unset).
        working_set_size = getattr(process, 'working_set_size', 4096)
        rng = getattr(process, 'mem_rng', None) or self.task_rng
        # rng.randint(a, b) is exactly a + rng._randbelow(b - a + 1) (CPython 3.12 to 3.14), so
        # calling it directly draws the same numbers with less overhead. This runs every tick.
        below, rand = rng._randbelow, rng.random
        num_accesses = 2 + below(4)                                   # randint(2, 5)
        addresses = []
        for _ in range(num_accesses):
            if rand() < 0.7 and addresses:
                address = max(working_set_base, addresses[-1] - 128 + below(257))   # randint(-128, 128)
            else:
                address = working_set_base + below(working_set_size)  # randint(0, size - 1)
            addresses.append(address)
        return addresses

    def run(self):
        total = len(self.all_processes)
        next_idx = 0

        while (len(self.completed_processes) < total or any(c.process for c in self.cores)) \
                and self.time < self.max_time:
            # 1. Arrivals
            while next_idx < total and self.all_processes[next_idx].arrival_time <= self.time:
                self.scheduler.enqueue(self.all_processes[next_idx], self.time)
                next_idx += 1

            # 2. Periodic scheduler work (MLFQ boost, affinity balancing)
            self.scheduler.balance(self, self.time)

            # 3. Per-core dispatch + execute
            if self.per_core:
                start = self.time % self.num_cores
                order = [(i, self.cores[i]) for i in
                         list(range(start, self.num_cores)) + list(range(start))]
            else:
                order = enumerate(self.cores)
            for i, core in order:
                if core.process is None or core.slice_remaining <= 0:
                    # Slice expired with work unfinished -> preempt and requeue.
                    if core.process is not None:
                        self.scheduler.on_slice_end(core, core.process, True, self.time)
                        self.scheduler.enqueue(core.process, self.time)
                        core.process = None

                    contention_move = self._contention_move(i)
                    move = self._play_move(i, contention_move)
                    proc = self.scheduler.select(core, move, self)
                    if proc is not None:
                        core.process = proc
                        proc.last_core = core.id
                        if proc.start_time is None:
                            proc.start_time = self.time
                            proc.response_time = self.time - proc.arrival_time
                            proc.started_by = i
                        proc.context_switches += 1
                        q = self.scheduler.quantum(core, proc, move)
                        core.slice_remaining = math.inf if q is None else q

                # Execute one tick on the running process.
                if core.process is not None:
                    p = core.process
                    stall = 0
                    addresses = self._generate_memory_access(p)
                    for address in addresses:
                        level, latency = self.cache_hierarchies[i].access(address)
                        p.cache_misses += _MISSES_BY_HIT_LEVEL.get(level, 0)
                        stall += latency
                    stall = max(0, stall - len(addresses))  # one cycle/access overlaps work
                    work_done = 1.0 / (1.0 + stall * 0.01)
                    p.remaining_time -= work_done
                    core.slice_remaining -= 1
                    self.busy_ticks += 1
                    self.core_busy[i] += 1

                    if p.remaining_time <= 0:
                        self.core_completed[i] += 1
                        p.completion_time = self.time
                        p.turnaround_time = p.completion_time - p.arrival_time
                        p.waiting_time = max(0, p.turnaround_time - p.burst_time)
                        self.completed_processes.append(p)
                        self.scheduler.on_complete(core, p, self.time)
                        core.process = None
                        core.slice_remaining = 0

            # 4. Optional bounded trace (per-tick core -> pid)
            if self.record_trace and len(self.trace) < self.trace_cap:
                self.trace.append([c.process.pid if c.process else None for c in self.cores])

            self.time += 1

        return self._calculate_metrics()

    def _calculate_metrics(self):
        done = sorted(self.completed_processes, key=lambda p: (p.arrival_time, p.pid))
        n_done = len(done)
        if n_done == 0:
            return {'global_metrics': {}, 'simulation_config': self._config()}

        # Warm-up: leave the earliest-arriving tasks out of the per-task averages. Throughput and
        # makespan always cover the whole run.
        measured = done[int(n_done * self.warmup_fraction):] or done
        n = len(measured)
        avg_turnaround = sum(p.turnaround_time for p in measured) / n
        avg_waiting = sum(p.waiting_time for p in measured) / n
        responses = [p.response_time for p in measured if p.response_time is not None]
        avg_response = sum(responses) / len(responses) if responses else 0.0
        throughput = (n_done / self.time) * 1000 if self.time > 0 else 0.0
        # Cache misses per task (L1, L2 and L3 misses of its own accesses).
        avg_cache_misses = sum(p.cache_misses for p in measured) / n

        # Load: the CPU time the tasks needed over the CPU time the cores could give while tasks
        # were still arriving. Above 1 (100%) the cores can't keep up, the queue keeps growing,
        # and the waiting averages depend on how many tasks were simulated, not on the scheduler.
        last_arrival = max(p.arrival_time for p in self.all_processes)
        load = self.busy_ticks / (self.num_cores * max(last_arrival, 1))

        return {
            'global_metrics': {
                'avg_turnaround': round(avg_turnaround, 2),
                'avg_waiting': round(avg_waiting, 2),
                'avg_response': round(avg_response, 2),
                'throughput': round(throughput, 2),
                'total_switches': sum(p.context_switches for p in done),
                'avg_cache_misses': round(avg_cache_misses, 2),
                'makespan': self.time,
                'load': round(load, 2),
                'tasks_measured': n,
                'unfinished': len(self.all_processes) - n_done,
            },
            'simulation_config': self._config(),
            'trace': self.trace if self.record_trace else None,
            **({'per_core': self._per_core_metrics(measured)} if self.per_core else {}),
        }

    def _per_core_metrics(self, measured):
        """Each core's own numbers. A task belongs to the core that first started it: that
        core's choice decided how long it waited. Throughput counts the tasks a core finished.
        A core that started no measured task has no response/wait/turnaround (None)."""
        out = []
        for i in range(self.num_cores):
            own = [p for p in measured if getattr(p, 'started_by', None) == i]
            moves = self.core_moves[i]

            def avg(vals):
                return round(sum(vals) / len(vals), 2) if vals else None
            row = {
                'tasks_started': len(own),
                'throughput': round(self.core_completed[i] / self.time * 1000, 2) if self.time else 0.0,
                'busy_share': round(self.core_busy[i] / self.time, 4) if self.time else 0.0,
                'coop_rate': round(100 * sum(1 for m in moves if m == 'C') / len(moves), 1) if moves else 0.0,
            }
            for key, vals in (('avg_response', [p.response_time for p in own if p.response_time is not None]),
                              ('avg_waiting', [p.waiting_time for p in own]),
                              ('avg_turnaround', [p.turnaround_time for p in own]),
                              ('avg_cache_misses', [p.cache_misses for p in own])):
                v = avg(vals)
                if v is not None:
                    row[key] = v
            out.append(row)
        return out

    def _config(self):
        return {
            'num_cores': self.num_cores,
            'scheduler': self.scheduler_name,
            'workload': self.workload_type,
        }

    def realized_coop_rate(self):
        """Fraction of Cooperate moves the cores actually played during this run (0..1).

        This is the strategy's behaviour under the real contention signal, so it lines up
        with the cooperation metric used by the 1v1 / N-Player modes.
        """
        total = sum(len(m) for m in self.core_moves)
        if total == 0:
            return 0.0
        coops = sum(1 for m in self.core_moves for mv in m if mv == 'C')
        return coops / total


def _avg_metrics(metric_dicts):
    """Average a sequence of global_metrics dicts."""
    rows = [m for m in metric_dicts if m]
    if not rows:
        return {'throughput': 0.0, 'avg_waiting': 0.0, 'avg_turnaround': 0.0,
                'avg_response': 0.0, 'avg_cache_misses': 0.0, 'makespan': 0}
    keys = ['throughput', 'avg_waiting', 'avg_turnaround', 'avg_response', 'avg_cache_misses', 'makespan']
    out = {}
    for k in keys:
        out[k] = round(sum(r.get(k, 0) for r in rows) / len(rows), 2)
    return out


# Metrics averaged over seeded runs (per-run values come from OSSimulator._calculate_metrics).
_RUN_METRIC_KEYS = ('throughput', 'avg_waiting', 'avg_turnaround', 'avg_response',
                    'avg_cache_misses', 'makespan', 'coop_rate', 'load')
# Student-t critical values for a two-sided 95% interval, by degrees of freedom (runs - 1).
_T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306,
        9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120,
        17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064,
        25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045}


def make_seeds(seed, runs):
    """Seeds for `runs` runs: seed, seed+1, ... A random starting seed is picked when none is
    given, and reported, so any run can be repeated exactly."""
    runs = max(1, int(runs))
    base = int(seed) if seed is not None else random.randrange(1, 1_000_000)
    return [base + i for i in range(runs)]


def _mean_ci(rows, keys=_RUN_METRIC_KEYS):
    """Average each metric over runs. With 2+ runs, also the 95% confidence-interval half-width
    under 'ci' (the true average is likely within mean +/- ci)."""
    out, ci = {}, {}
    for k in keys:
        vals = [r[k] for r in rows if k in r]
        if not vals:
            continue
        out[k] = round(sum(vals) / len(vals), 2)
        if len(vals) >= 2:
            ci[k] = round(_T95.get(len(vals) - 1, 1.96) * statistics.stdev(vals) / math.sqrt(len(vals)), 2)
    if ci:
        out['ci'] = ci
    return out


# Per-core numbers averaged in elimination mode (see OSSimulator._per_core_metrics).
_CORE_METRIC_KEYS = ('throughput', 'avg_waiting', 'avg_turnaround', 'avg_response',
                     'avg_cache_misses', 'coop_rate', 'tasks_started', 'busy_share')


def _average_over_workloads(per_run, n_runs, keys):
    """One row per run: the plain mean of the four workloads in that run."""
    overall = []
    for i in range(n_runs):
        row = {}
        for k in keys:
            vals = [per_run[wl][i][k] for wl in WORKLOAD_PROFILES if k in per_run[wl][i]]
            if vals:
                row[k] = sum(vals) / len(vals)
        overall.append(row)
    return overall


def _run_replicated(core_funcs, *, num_cores, scheduler, seeds, num_processes, warmup,
                    fixed_move=None, per_core=False):
    """Run all four workloads once per seed. Returns ({workload: mean+ci}, overall mean+ci).
    The overall row averages the four workloads within each run first, so its interval reflects
    run-to-run variation. Raises StrategyRuntimeError if a strategy fails in any run.

    With ``per_core=True`` (elimination mode) a third value is returned: one entry per core,
    ``{'workloads': {workload: mean+ci}, 'avg': mean+ci}``, averaged the same way."""
    per_run = {wl: [] for wl in WORKLOAD_PROFILES}
    core_runs = [{wl: [] for wl in WORKLOAD_PROFILES} for _ in range(num_cores)]
    for s in seeds:
        for wl in WORKLOAD_PROFILES:
            sim = OSSimulator(wl, core_funcs, scheduler=scheduler, seed=s, num_cores=num_cores,
                              fixed_move=fixed_move, warmup_fraction=warmup, per_core=per_core)
            sim.generate_workload(num_processes)
            out = sim.run()
            metrics = out['global_metrics']
            if fixed_move is None:
                metrics['coop_rate'] = round(sim.realized_coop_rate() * 100, 1)
            per_run[wl].append(metrics)
            if per_core:
                rows = out.get('per_core') or [{} for _ in range(num_cores)]
                for c in range(num_cores):
                    core_runs[c][wl].append(rows[c])
    per_wl = {wl: _mean_ci(rows) for wl, rows in per_run.items()}
    overall = _mean_ci(_average_over_workloads(per_run, len(seeds), _RUN_METRIC_KEYS))
    if not per_core:
        return per_wl, overall
    cores = [{'workloads': {wl: _mean_ci(rows, _CORE_METRIC_KEYS) for wl, rows in cr.items()},
              'avg': _mean_ci(_average_over_workloads(cr, len(seeds), _CORE_METRIC_KEYS),
                              _CORE_METRIC_KEYS)}
             for cr in core_runs]
    return per_wl, overall, cores


def _overload_info(baseline):
    """Which workloads overload the cores, judged on the plain-scheduler baseline (same tasks,
    no strategy), so the warning describes the setup rather than one strategy."""
    over = [wl for wl in WORKLOAD_PROFILES
            if baseline['workloads'].get(wl, {}).get('load', 0) > OVERLOAD]
    return {'overloaded_workloads': over}


def _run_info(seeds, num_processes, warmup):
    """What a result was averaged over (shown with the results and kept when published)."""
    return {'runs': len(seeds), 'seeds': list(seeds), 'seed': seeds[0],
            'num_processes': num_processes, 'warmup': warmup}


def build_core_trace(named_funcs, num_cores=2, seed=None, scheduler='round_robin',
                     workload='Mixed', process_count=30, max_ticks=200):
    """Run ONE representative simulation with tracing on, for the Gantt visual.

    `named_funcs` is a list of (name, func) of length num_cores (the core layout to trace).
    Returns {cores: [names], scheduler, ticks: [[pid|null per core], ...]} bounded to
    `max_ticks` so the stored tournament doc stays small.
    """
    funcs = [f for _, f in named_funcs]
    names = [n for n, _ in named_funcs]
    if len(funcs) != num_cores or not all(funcs):
        return None
    sim = OSSimulator(workload, funcs, scheduler=scheduler, seed=seed,
                      num_cores=num_cores, record_trace=True)
    sim.generate_workload(process_count)
    try:
        sim.run()
    except StrategyRuntimeError:
        return None   # the failing strategy is already reported by the main run
    return {'cores': names, 'scheduler': scheduler, 'workload': workload,
            'ticks': sim.trace[:max_ticks]}


def run_baseline_simulation(num_cores=2, seed=None, scheduler='round_robin', runs=1,
                            num_processes=FULL_PROCESS_COUNT, warmup=0.0, seeds=None):
    """Run the chosen scheduler in its textbook ("vanilla") form across all workload profiles.

    Cores play a constant move (no PD strategy), so this is the plain scheduler's performance —
    a reference baseline to compare strategy-driven runs against. Uses the same seeds as the
    strategies it is compared with (pass `seeds`), so the comparison is paired.
    Returns {workloads, avg, scheduler}.
    """
    seeds = list(seeds) if seeds else make_seeds(seed, runs)
    per_wl, avg = _run_replicated([None] * num_cores, num_cores=num_cores, scheduler=scheduler,
                                  seeds=seeds, num_processes=num_processes, warmup=warmup,
                                  fixed_move=BASELINE_MOVE)
    return {'workloads': per_wl, 'avg': avg, 'scheduler': scheduler}


def _homogeneous_job(code, *, seeds, num_cores, scheduler, num_processes, warmup):
    """One strategy on every core, over every seed and workload. Top level so a worker process
    can run it (parallel.py). Returns {workloads, avg} or {error} (a disqualification)."""
    func = extract_strategy_func(code, seed=seeds[0])
    if not func:
        return {'error': 'could not find a strategy function in the code'}
    try:
        per_wl, avg = _run_replicated([func] * num_cores, num_cores=num_cores, scheduler=scheduler,
                                      seeds=seeds, num_processes=num_processes, warmup=warmup)
    except StrategyRuntimeError as e:
        return {'error': e.reason}
    return {'workloads': per_wl, 'avg': avg}


def run_full_simulation(strategies_data, num_cores=2, seed=None, scheduler='round_robin',
                        runs=1, num_processes=FULL_PROCESS_COUNT, warmup=0.0, ranking=None):
    """HOMOGENEOUS: benchmark each strategy replicated across all N cores.

    Each strategy (and the baseline) is run on the same `runs` seeds and the metrics averaged,
    with 95% confidence intervals when runs >= 2. Returns {assignment_mode, scheduler, num_cores,
    baseline, strategies: {name: {workloads, avg, coop_rate}}, errors, runs, seeds, ...}.
    """
    seeds = make_seeds(seed, runs)
    strategies = {}
    errors = []   # [{name, reason}] for strategies left out of the ranking (disqualified)
    # One job per strategy (all its seeds and workloads, in order, in one process), plus the
    # baseline; with several CPU cores they run at once (parallel.py) with identical results.
    import parallel
    calls = [(_homogeneous_job, (strat['code'],), {'seeds': seeds, 'num_cores': num_cores,
                                                   'scheduler': scheduler, 'num_processes': num_processes,
                                                   'warmup': warmup})
             for strat in strategies_data]
    calls.append((run_baseline_simulation, (), {'num_cores': num_cores, 'scheduler': scheduler,
                                                'seeds': seeds, 'num_processes': num_processes,
                                                'warmup': warmup}))
    outcomes = parallel.run_calls(calls)
    baseline = outcomes.pop()
    for strat, outcome in zip(strategies_data, outcomes):
        name = strat['name']
        if 'error' in outcome:
            errors.append({'name': name, 'reason': outcome['error']})
            continue
        per_wl, avg = outcome['workloads'], outcome['avg']
        strategies[name] = {'workloads': per_wl, 'avg': avg, 'coop_rate': avg.get('coop_rate', 0.0),
                            'submitted_at': strat.get('submitted_at')}

    # Order by the chosen ranking (default: the itinerary's "lowest average response time wins").
    # The dict is rebuilt in ranked order and each entry carries its rank.
    ranking = normalize_ranking(ranking, 'os')
    rows = [{'name': n, 'submitted_at': v.get('submitted_at'), **v['avg']} for n, v in strategies.items()]
    ranked = rank_entries(rows, ranking, 'os')
    strategies = {r['name']: {**strategies[r['name']], 'rank': r['rank'], 'rank_label': r['rank_label']}
                  for r in ranked}

    return {
        'assignment_mode': 'homogeneous',
        'scheduler': scheduler,
        'num_cores': num_cores,
        'baseline': baseline,
        'strategies': strategies,
        'order': list(strategies),
        'ranking': ranking,
        'ranking_text': describe_ranking(ranking, 'os'),
        'errors': errors,
        **_overload_info(baseline),
        **_run_info(seeds, num_processes, warmup),
    }


# Heterogeneous results use shorter metric names (wait_time, turnaround, ...); keep the CI with them.
_HET_NAMES = {'throughput': 'throughput', 'avg_waiting': 'wait_time', 'avg_turnaround': 'turnaround',
              'avg_response': 'response', 'makespan': 'makespan', 'avg_cache_misses': 'cache_misses',
              'coop_rate': 'coop_rate', 'load': 'load'}


def _het_summary(m):
    out = {short: m[long] for long, short in _HET_NAMES.items() if long in m}
    if 'ci' in m:
        out['ci'] = {_HET_NAMES[k]: v for k, v in m['ci'].items() if k in _HET_NAMES}
    return out


def run_heterogeneous_simulation(strategies_data, num_cores=2, seed=None,
                                 scheduler='round_robin', max_combinations=None,
                                 time_budget_s=None, unlimited=False,
                                 runs=1, num_processes=FULL_PROCESS_COUNT, warmup=0.0,
                                 ranking=None):
    """HETEROGENEOUS: each strategy used at most once.

    Enumerates itertools.combinations(strategies, num_cores) — no replacement — runs each mixture
    across the workload profiles and ranks by throughput.

    The full space is C(n, num_cores), which grows explosively: C(14, 8) is 3003 runs, roughly 20
    minutes. Rather than refusing outright past a fixed count (the old behaviour, which cut off at
    60 combinations, i.e. about 23 seconds of work), the run is bounded by wall-clock time and
    reports honestly how much of the space it covered:

      time_budget_s   stop once the *measured* average cost of a combination says the next one
                      would overrun this many seconds. Measured rather than predicted because per-
                      combination cost depends on how expensive the submitted strategies are, not
                      just on the machine. None means no time bound.
      max_combinations  optional absolute ceiling on the count. None means no ceiling. Truncates;
                      it no longer raises.
      unlimited       bypass both and evaluate the entire space.

    At least one combination is always evaluated, so even an absurdly small budget returns a
    leaderboard rather than nothing.

    When the whole space is not covered, the subset is drawn *after a seeded shuffle*, never as a
    lexicographic prefix. `itertools.combinations` emits low indices first, so a prefix is badly
    biased: for C(14, 8) truncated to 150, strategies 0-3 appear in all 150 combinations while 12
    and 13 appear in 51. A student's rank would then track their position in the list rather than
    their strategy. Shuffling with the run's own seed keeps results reproducible.
    """
    seeds = make_seeds(seed, runs)
    funcs, names = [], []
    errors = {}   # strategy name -> reason; these are disqualified from every mixture
    for strat in strategies_data:
        func = extract_strategy_func(strat['code'], seed=seeds[0])
        if func:
            funcs.append(func)
            names.append(strat['name'])
        else:
            errors[strat['name']] = 'could not find a strategy function in the code'
    k = len(funcs)
    if k < num_cores:
        raise ValueError(
            f"Heterogeneous assignment needs at least num_cores ({num_cores}) distinct "
            f"strategies, got {k}."
        )

    all_combos = list(itertools.combinations(range(k), num_cores))
    total = len(all_combos)

    # Shuffle before any truncation so a partial run is an unbiased sample of the space rather
    # than a lexicographic prefix (see the docstring for the measured skew). Seeded off the run's
    # own seed, so the same seed always selects the same subset. Done unconditionally: when the
    # whole space is covered the order is irrelevant (results are sorted by throughput at the
    # end), so there is no branch here to get wrong.
    combos = all_combos[:]
    random.Random(seeds[0]).shuffle(combos)

    ceiling = None if unlimited else max_combinations
    if ceiling is not None and ceiling > 0:
        combos = combos[:ceiling]

    budget = None if unlimited else time_budget_s

    results = []
    elapsed = 0.0
    covered = 0   # mixtures run or ruled out (by a disqualified member): how much of the space was seen
    for combo in combos:
        # A strategy that already failed is disqualified, so skip every mixture containing it.
        if any(names[i] in errors for i in combo):
            covered += 1
            continue
        # Stop before starting a combination the running average says would overrun the budget.
        # `results` is checked so at least one always runs: a budget smaller than a single
        # combination should still produce a leaderboard, not an empty one.
        if budget is not None and results:
            avg = elapsed / len(results)
            if elapsed + avg > budget:
                break
        started = time.perf_counter()
        covered += 1
        core_strategies = [funcs[i] for i in combo]
        assignment_details = [
            {'core_index': ci, 'strategy_name': names[combo[ci]]} for ci in range(num_cores)
        ]
        composition = {names[i]: 1 for i in combo}  # each strategy at most once

        try:
            per_wl, m = _run_replicated(core_strategies, num_cores=num_cores, scheduler=scheduler,
                                        seeds=seeds, num_processes=num_processes, warmup=warmup)
        except StrategyRuntimeError as e:
            errors.setdefault(names[combo[e.core_idx]], e.reason)
            elapsed += time.perf_counter() - started
            continue
        # Per-profile breakdown so the frontend profile dropdown works for heterogeneous too.
        per_wl_summary = {wl: _het_summary(mm) for wl, mm in per_wl.items()}
        results.append({
            'combination': composition,
            'assignment_details': assignment_details,
            **_het_summary(m),
            'workloads': per_wl_summary,
        })
        elapsed += time.perf_counter() - started

    # A strategy can fail in a later mixture after already appearing in earlier ones; drop those
    # earlier mixtures too, so a disqualified strategy never shows up in the ranking.
    if errors:
        results = [r for r in results
                   if not any(d['strategy_name'] in errors for d in r['assignment_details'])]
    # Mixtures use short metric names (response, wait_time, ...); map the catalog names onto them.
    ranking = normalize_ranking(ranking, 'os')

    def _het_get(row, metric):
        return row.get(_HET_NAMES.get(metric, metric))
    results = rank_entries(results, ranking, 'os', get=_het_get)
    baseline_result = run_baseline_simulation(num_cores=num_cores, scheduler=scheduler, seeds=seeds,
                                              num_processes=num_processes, warmup=warmup)
    return {
        'assignment_mode': 'heterogeneous',
        'scheduler': scheduler,
        'num_cores': num_cores,
        'strategy_count': k,
        'total_combinations': total,
        'evaluated': len(results),
        'ranking': ranking,
        'ranking_text': describe_ranking(ranking, 'os'),
        # Consumers must be able to tell a complete sweep from a sample: the top-ranked mixture is
        # only provably the best when sampled is False. `seed` is reported because it selects
        # *which* subset was drawn, so a sampled run can be reproduced exactly.
        'sampled': covered < total,
        'elapsed_seconds': round(elapsed, 3),
        'baseline': baseline_result,
        'results': results,
        **_overload_info(baseline_result),
        **_run_info(seeds, num_processes, warmup),
        'errors': [{'name': n, 'reason': r} for n, r in errors.items()],
    }


# --- Heterogeneous elimination (final sprint) ----------------------------------------------

def _elimination_group_job(codes, *, seeds, num_cores, scheduler, num_processes, warmup):
    """One elimination group: one strategy per core (``None`` = filler core), every seed and
    workload, with each core's own numbers. Top level so a worker process can run it.

    A strategy that fails is disqualified and the group is replayed with a filler core in its
    seat (fresh copies of the others), so the rest are not scored against a broken run.
    Returns {'cores': [per-core {workloads, avg}], 'group': {workloads, avg},
    'errors': {seat: reason}}.
    """
    errors = {}
    while True:
        funcs = []
        for seat, code in enumerate(codes):
            func = None
            if code is not None and seat not in errors:
                func = extract_strategy_func(code, seed=seeds[0])
                if not func:
                    errors[seat] = 'could not find a strategy function in the code'
            funcs.append(func or filler_strategy)
        try:
            per_wl, overall, cores = _run_replicated(
                funcs, num_cores=num_cores, scheduler=scheduler, seeds=seeds,
                num_processes=num_processes, warmup=warmup, per_core=True)
        except StrategyRuntimeError as e:
            errors[e.core_idx] = e.reason
            continue
        return {'cores': cores, 'group': {'workloads': per_wl, 'avg': overall}, 'errors': errors}


def run_heterogeneous_elimination(strategies_data, num_cores=2, seed=None,
                                  scheduler='round_robin', runs=1,
                                  num_processes=FULL_PROCESS_COUNT, warmup=0.0, ranking=None):
    """HETEROGENEOUS, elimination style: groups of ``num_cores`` different strategies share
    one machine (one strategy per core) and each strategy is scored on its own core.

    Groups follow the rule in elimination.py with G = num_cores. A group short of strategies
    gets filler cores (the plain scheduler), which are never ranked. Every group runs on the
    same seeds, so every strategy faces the same task lists. Inside a group cores are ranked
    by ``ranking`` (default: lowest average response time of the tasks that core started).

    Returns {'assignment_mode': 'heterogeneous', 'style': 'elimination', 'leaderboard',
    'bracket', 'baseline', 'errors', ...}. Leaderboard rows use the long metric names
    (avg_response, avg_waiting, ...) plus ``stage_reached`` / ``stage_order``.
    """
    import elimination
    import parallel

    seeds = make_seeds(seed, runs)
    spec = normalize_ranking(ranking, 'os')
    n = len(strategies_data)
    if n < 1:
        raise ValueError("Heterogeneous elimination needs at least one strategy.")
    players = list(range(n))
    random.Random(seeds[0]).shuffle(players)
    last_row = {}       # strategy -> its core's row from the last group it played
    round_rows = {}     # (round, strategy) -> its row in that round (for the bracket)
    errors = {}         # strategy -> reason (disqualified)
    group_runs = []     # whole-group numbers, for reference
    started = time.perf_counter()
    job_kw = {'seeds': seeds, 'num_cores': num_cores, 'scheduler': scheduler,
              'num_processes': num_processes, 'warmup': warmup}

    def order_by_spec(pids):
        rows = rank_entries([last_row[p] for p in pids], spec, 'os')
        return [r['_pid'] for r in rows]

    def play_round(round_no, groups):
        calls = [(_elimination_group_job,
                  ([strategies_data[i]['code'] for i in members] + [None] * (num_cores - len(members)),),
                  job_kw) for members in groups]
        played = []
        for gi, (members, out) in enumerate(zip(groups, parallel.run_calls(calls))):
            for seat, reason in out['errors'].items():
                if seat < len(members):
                    errors.setdefault(members[seat], reason)
            for seat, pid in enumerate(members):
                if pid in errors:
                    continue
                core = out['cores'][seat]
                last_row[pid] = round_rows[(round_no, pid)] = {
                    'name': strategies_data[pid]['name'], '_pid': pid,
                    'submitted_at': strategies_data[pid].get('submitted_at'),
                    'core_index': seat, 'round': round_no, **core['avg'],
                    'workloads': core['workloads']}
            group_runs.append({'round': round_no, 'group_index': gi,
                               'players': [strategies_data[i]['name'] for i in members],
                               'fillers': num_cores - len(members), **_het_summary(out['group']['avg'])})
            played.append((members, order_by_spec([p for p in members if p not in errors])))
        return played

    rounds_played, stage_of = elimination.run_bracket(players, num_cores, play_round,
                                                      order_by_spec, min_group=1)
    keep = ('avg_response', 'avg_waiting', 'avg_turnaround', 'throughput', 'avg_cache_misses',
            'coop_rate', 'tasks_started')
    bracket = elimination.bracket_json(
        rounds_played, lambda p: strategies_data[p]['name'], errors,
        lambda r, p: {k: round_rows[(r, p)].get(k) for k in keep})

    leaderboard = []
    for pid, (stage, pos) in stage_of.items():
        row = {k: v for k, v in last_row[pid].items() if k != '_pid'}
        row.update(stage_reached=stage, stage_order=pos)
        leaderboard.append(row)
    leaderboard = rank_entries(leaderboard, spec, 'os')

    baseline_result = run_baseline_simulation(num_cores=num_cores, scheduler=scheduler, seeds=seeds,
                                              num_processes=num_processes, warmup=warmup)
    from ranking import ELIMINATION_NOTE
    return {
        'assignment_mode': 'heterogeneous',
        'style': 'elimination',
        'scheduler': scheduler,
        'num_cores': num_cores,
        'strategy_count': n,
        'bracket_rounds': len(bracket),
        'ranking': spec,
        'ranking_text': ELIMINATION_NOTE + ' ' + describe_ranking(spec, 'os'),
        'elapsed_seconds': round(time.perf_counter() - started, 3),
        'leaderboard': leaderboard,
        'bracket': bracket,
        'group_runs': group_runs,
        'baseline': baseline_result,
        **_overload_info(baseline_result),
        **_run_info(seeds, num_processes, warmup),
        'errors': [{'name': strategies_data[i]['name'], 'reason': r} for i, r in sorted(errors.items())],
    }
