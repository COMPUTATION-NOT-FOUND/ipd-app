import random
import math
import itertools
from cache_model import build_core_hierarchies
from schedulers import build_scheduler
from n_player_simulation import call_strategy

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

    def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        top_level = name.split('.')[0]
        if top_level not in ALLOWED_IMPORTS:
            raise ImportError(f"Import of '{name}' is not allowed in strategy code")
        return _real_import(name, globals, locals, fromlist, level)

    safe_builtins = {
        'abs': abs, 'all': all, 'any': any, 'bool': bool,
        'complex': complex, 'divmod': divmod, 'enumerate': enumerate,
        'filter': filter, 'float': float, 'int': int, 'len': len,
        'list': list, 'map': map, 'max': max, 'min': min,
        'pow': pow, 'range': range, 'reversed': reversed,
        'round': round, 'set': set, 'sorted': sorted,
        'str': str, 'sum': sum, 'tuple': tuple, 'zip': zip,
        'dict': dict,
        '__import__': _safe_import,
    }

    # Provide seeded random instance if seed is given, otherwise global module
    random_provider = random.Random(seed) if seed is not None else random

    globals_dict.update({
        '__builtins__': safe_builtins,
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
        # Prefer the unified 4-arg signature, then the legacy 3-arg, then the first
        # user function — so one 4-arg strategy works across 1v1 / N-player / OS sim.
        for n in (4, 3):
            for f in funcs:
                code_obj = getattr(f, '__code__', None)
                if code_obj is not None and code_obj.co_argcount == n:
                    return f
        return funcs[0]
    except:
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

MAX_SIM_TICKS = 5000


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
                 seed=None, num_cores=2, record_trace=False, fixed_move=None):
        if len(core_strategies) != num_cores:
            raise ValueError(f"core_strategies must have {num_cores} strategies, got {len(core_strategies)}")

        self.workload_type = workload_type
        self.num_cores = num_cores
        # When set (e.g. 'D'), cores skip their PD strategy and always play this move, so the
        # scheduler runs in its textbook ("vanilla") form. Used to produce the baseline run.
        self.fixed_move = fixed_move
        self.rng = random.Random(seed) if seed is not None else random
        self.cores = [Core(i, core_strategies[i]) for i in range(num_cores)]

        self.scheduler = scheduler if not isinstance(scheduler, str) \
            else build_scheduler(scheduler, num_cores, self.rng)
        self.scheduler_name = getattr(self.scheduler, 'name', str(scheduler))

        self.cache_hierarchies = build_core_hierarchies(self.rng, num_cores)

        # Per-core PD move history and the contention ("opponent") history it saw.
        self.core_moves = [[] for _ in range(num_cores)]
        self.core_contention = [[] for _ in range(num_cores)]

        self.completed_processes = []
        self.all_processes = []
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
                                     self.rng.randint(UNIFORM_BURST_MIN, UNIFORM_BURST_MAX)))
        elif self.workload_type == 'Poisson':
            # Exponential inter-arrivals (Poisson process) + heavy-tailed bursts.
            curr = 0
            for i in range(count):
                curr += int(self.rng.expovariate(1.0 / POISSON_MEAN_INTERARRIVAL))
                procs.append(Process(i, curr,
                                     _clamped_lognormal(self.rng, BURST_MEDIAN, BURST_SIGMA, BURST_MIN, BURST_MAX)))
        elif self.workload_type == 'Mixed':
            # Bimodal: mostly short interactive jobs, a few long batch jobs (each heavy-tailed).
            for i in range(count):
                is_long = self.rng.random() < MIXED_LONG_JOB_FRACTION
                if is_long:
                    burst = _clamped_lognormal(self.rng, MIXED_LONG_MEDIAN, MIXED_LONG_SIGMA, BURST_MIN, BURST_MAX)
                else:
                    burst = _clamped_lognormal(self.rng, MIXED_SHORT_MEDIAN, MIXED_SHORT_SIGMA, BURST_MIN, BURST_MAX)
                procs.append(Process(i, i * MIXED_ARRIVAL_INTERVAL, burst))
        elif self.workload_type == 'Bursty':
            # Batched arrivals (idle gaps then floods) + heavy-tailed bursts.
            for i in range(count):
                arrival = (i // BURSTY_BATCH_SIZE) * BURSTY_BATCH_INTERVAL
                procs.append(Process(i, arrival,
                                     _clamped_lognormal(self.rng, BURSTY_BURST_MEDIAN, BURSTY_BURST_SIGMA, BURST_MIN, BURST_MAX)))
        else:  # Default fallback
            for i in range(count):
                procs.append(Process(i, i * MIXED_ARRIVAL_INTERVAL, 50))

        # Give each process its own memory working-set size (log-normal) so cache
        # pressure varies across jobs rather than every job sharing one fixed footprint.
        for p in procs:
            p.working_set_size = _clamped_lognormal(
                self.rng, WORKING_SET_MEDIAN, WORKING_SET_SIGMA, WORKING_SET_MIN, WORKING_SET_MAX)

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
        if self.fixed_move is not None:
            # Baseline run: ignore the PD strategy and play a constant move so the scheduler
            # behaves in its textbook form (no strategy-driven yielding).
            move = self.fixed_move
        else:
            meta = {
                'round': len(my_history),
                'n_players': self.num_cores,
                'player_index': core_idx,
                'rng': self.rng,
                'tournament_info': {'format': 'os_simulation', 'n_players': self.num_cores},
            }
            try:
                move = call_strategy(
                    core.strategy_func,
                    [contention_move] if my_history else [],
                    my_history,
                    [contention_history],
                    meta,
                )
            except Exception:
                move = 'C'
        if move not in ('C', 'D'):
            move = 'C'
        core.last_move = move
        self.core_moves[core_idx].append(move)
        self.core_contention[core_idx].append(contention_move)
        return move

    def _generate_memory_access(self, process):
        """Realistic per-tick memory accesses with temporal/spatial locality."""
        working_set_base = process.pid * 0x10000  # 64 KB region per process
        # Per-process hot working set (varies by job; falls back to 4 KB if unset).
        working_set_size = getattr(process, 'working_set_size', 4096)
        num_accesses = self.rng.randint(2, 5)
        addresses = []
        for _ in range(num_accesses):
            if self.rng.random() < 0.7 and addresses:
                base = addresses[-1]
                offset = self.rng.randint(-128, 128)
                address = max(working_set_base, base + offset)
            else:
                address = working_set_base + self.rng.randint(0, working_set_size - 1)
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
            for i, core in enumerate(self.cores):
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
                        proc.context_switches += 1
                        q = self.scheduler.quantum(core, proc, move)
                        core.slice_remaining = math.inf if q is None else q

                # Execute one tick on the running process.
                if core.process is not None:
                    p = core.process
                    stall = 0
                    addresses = self._generate_memory_access(p)
                    for address in addresses:
                        _, latency = self.cache_hierarchies[i].access(address)
                        stall += latency
                    stall = max(0, stall - len(addresses))  # one cycle/access overlaps work
                    work_done = 1.0 / (1.0 + stall * 0.01)
                    p.remaining_time -= work_done
                    core.slice_remaining -= 1

                    if p.remaining_time <= 0:
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
        n = len(self.completed_processes)
        if n == 0:
            return {'global_metrics': {}, 'simulation_config': self._config()}

        avg_turnaround = sum(p.turnaround_time for p in self.completed_processes) / n
        avg_waiting = sum(p.waiting_time for p in self.completed_processes) / n
        responses = [p.response_time for p in self.completed_processes if p.response_time is not None]
        avg_response = sum(responses) / len(responses) if responses else 0.0
        throughput = (n / self.time) * 1000 if self.time > 0 else 0.0

        # Real cache misses: private L1/L2 per core + the shared L3 counted once.
        l1l2_misses = sum(h.l1.misses + h.l2.misses for h in self.cache_hierarchies)
        l3_misses = self.cache_hierarchies[0].l3.misses
        avg_cache_misses = (l1l2_misses + l3_misses) / n

        return {
            'global_metrics': {
                'avg_turnaround': round(avg_turnaround, 2),
                'avg_waiting': round(avg_waiting, 2),
                'avg_response': round(avg_response, 2),
                'throughput': round(throughput, 2),
                'total_switches': sum(p.context_switches for p in self.completed_processes),
                'avg_cache_misses': round(avg_cache_misses, 2),
                'makespan': self.time,
            },
            'simulation_config': self._config(),
            'trace': self.trace if self.record_trace else None,
        }

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
    sim.run()
    return {'cores': names, 'scheduler': scheduler, 'workload': workload,
            'ticks': sim.trace[:max_ticks]}


def run_baseline_simulation(num_cores=2, seed=None, scheduler='round_robin'):
    """Run the chosen scheduler in its textbook ("vanilla") form across all workload profiles.

    Cores play a constant move (no PD strategy), so this is the plain scheduler's performance —
    a reference baseline to compare strategy-driven runs against. Returns {workloads, avg}.
    """
    per_wl = {}
    for wl in WORKLOAD_PROFILES:
        sim = OSSimulator(wl, [None] * num_cores, scheduler=scheduler,
                          seed=seed, num_cores=num_cores, fixed_move=BASELINE_MOVE)
        sim.generate_workload(FULL_PROCESS_COUNT)
        per_wl[wl] = sim.run()['global_metrics']
    return {'workloads': per_wl, 'avg': _avg_metrics(per_wl.values()), 'scheduler': scheduler}


def run_full_simulation(strategies_data, num_cores=2, seed=None, scheduler='round_robin'):
    """HOMOGENEOUS: benchmark each strategy replicated across all N cores.

    Returns {assignment_mode, scheduler, num_cores, baseline, strategies: {name: {workloads, avg, coop_rate}}}.
    Each per-workload metrics dict carries its own coop_rate; `avg` carries the averaged coop_rate.
    """
    strategies = {}
    for strat in strategies_data:
        name = strat['name']
        func = extract_strategy_func(strat['code'], seed=seed)
        if not func:
            continue
        per_wl = {}
        coop_rates = []
        for wl in WORKLOAD_PROFILES:
            sim = OSSimulator(wl, [func] * num_cores, scheduler=scheduler,
                              seed=seed, num_cores=num_cores)
            sim.generate_workload(FULL_PROCESS_COUNT)
            metrics = sim.run()['global_metrics']
            cr = round(sim.realized_coop_rate() * 100, 1)
            metrics['coop_rate'] = cr
            coop_rates.append(cr)
            per_wl[wl] = metrics
        avg = _avg_metrics(per_wl.values())
        avg['coop_rate'] = round(sum(coop_rates) / len(coop_rates), 1) if coop_rates else 0.0
        strategies[name] = {'workloads': per_wl, 'avg': avg, 'coop_rate': avg['coop_rate']}

    return {
        'assignment_mode': 'homogeneous',
        'scheduler': scheduler,
        'num_cores': num_cores,
        'baseline': run_baseline_simulation(num_cores=num_cores, seed=seed, scheduler=scheduler),
        'strategies': strategies,
    }


def run_heterogeneous_simulation(strategies_data, num_cores=2, seed=None,
                                 scheduler='round_robin', max_combinations=24):
    """HETEROGENEOUS: each strategy used at most once.

    Enumerates ALL itertools.combinations(strategies, num_cores) — no replacement — runs each
    mixture across the workload profiles and ranks by throughput. Every combination is evaluated
    and displayed; if the number of combinations would exceed `max_combinations`, the run is
    rejected (raise) rather than silently sampled, so callers can ask the user to reduce
    strategies/cores.
    """
    funcs, names = [], []
    for strat in strategies_data:
        func = extract_strategy_func(strat['code'], seed=seed)
        if func:
            funcs.append(func)
            names.append(strat['name'])
    k = len(funcs)
    if k < num_cores:
        raise ValueError(
            f"Heterogeneous assignment needs at least num_cores ({num_cores}) distinct "
            f"strategies, got {k}."
        )

    all_combos = list(itertools.combinations(range(k), num_cores))
    total = len(all_combos)
    if total > max_combinations:
        raise ValueError(
            f"Too many heterogeneous combinations ({total}) for {k} strategies on {num_cores} "
            f"cores; the maximum is {max_combinations}. Reduce strategies or cores."
        )
    combos = all_combos

    results = []
    for combo in combos:
        core_strategies = [funcs[i] for i in combo]
        assignment_details = [
            {'core_index': ci, 'strategy_name': names[combo[ci]]} for ci in range(num_cores)
        ]
        composition = {names[i]: 1 for i in combo}  # each strategy at most once

        agg = []
        per_wl = {}
        coop_rates = []
        for wl in WORKLOAD_PROFILES:
            sim = OSSimulator(wl, core_strategies, scheduler=scheduler,
                              seed=seed, num_cores=num_cores)
            sim.generate_workload(FULL_PROCESS_COUNT)
            metrics = sim.run()['global_metrics']
            cr = round(sim.realized_coop_rate() * 100, 1)
            metrics['coop_rate'] = cr
            coop_rates.append(cr)
            agg.append(metrics)
            per_wl[wl] = metrics
        m = _avg_metrics(agg)
        coop_avg = round(sum(coop_rates) / len(coop_rates), 1) if coop_rates else 0.0
        # Per-profile breakdown so the frontend profile dropdown works for heterogeneous too.
        per_wl_summary = {wl: {
            'throughput': mm['throughput'], 'wait_time': mm['avg_waiting'],
            'turnaround': mm['avg_turnaround'], 'response': mm['avg_response'],
            'makespan': mm['makespan'], 'cache_misses': mm['avg_cache_misses'],
            'coop_rate': mm['coop_rate'],
        } for wl, mm in per_wl.items()}
        results.append({
            'combination': composition,
            'assignment_details': assignment_details,
            'throughput': m['throughput'],
            'wait_time': m['avg_waiting'],
            'turnaround': m['avg_turnaround'],
            'response': m['avg_response'],
            'makespan': m['makespan'],
            'cache_misses': m['avg_cache_misses'],
            'coop_rate': coop_avg,
            'workloads': per_wl_summary,
        })

    results.sort(key=lambda r: r['throughput'], reverse=True)
    return {
        'assignment_mode': 'heterogeneous',
        'scheduler': scheduler,
        'num_cores': num_cores,
        'strategy_count': k,
        'total_combinations': total,
        'evaluated': len(results),
        'baseline': run_baseline_simulation(num_cores=num_cores, seed=seed, scheduler=scheduler),
        'results': results,
    }
