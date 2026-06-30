"""
Pluggable CPU scheduler policies driven by a per-core Prisoner's-Dilemma personality.

Each core runs a strategy that, at every dispatch, emits one move based on system
contention: **Cooperate ("be nice / yield CPU")** or **Defect ("grab / hold CPU")**.
Every real scheduling discipline has a natural place for that single bit:

    FCFS         FIFO, run-to-completion. No personality effect (pure baseline).
    Round Robin  Cooperate -> short quantum (yields early); Defect -> long quantum (hogs).
    SJF          Cooperate -> pick the shortest ready job (honours SJF);
                 Defect -> grab the longest job and hold it (monopolises, starves short jobs).
    Priority     Cooperate -> short quantum (low priority, yields);
                 Defect -> run-to-completion (top priority, monopolises).
    MLFQ         Cooperate -> voluntary early yield -> stays in a high (interactive) queue;
                 Defect -> burns the full quantum -> demoted to a lower queue.
    CFS          Cooperate -> low weight (nice+, recedes); Defect -> high weight (nice-, captures share).
    Affinity     Per-core run-queues (warm caches). The load balancer migrates work from a
                 busy core to an idle one, but a core that is currently Defecting refuses to
                 give up its queued work; a Cooperating core lets the work migrate.

The scheduler owns the ready structure(s); the simulator drives the tick loop, the
contention signal, the PD move, the cache, and metrics.
"""

# --- Quantum / policy constants (cycles) ---
RR_SHORT_QUANTUM = 10      # Cooperate
RR_LONG_QUANTUM = 50       # Defect
PRIORITY_COOP_QUANTUM = 10
MLFQ_QUANTA = [8, 16, 32]          # quantum per level (top -> bottom)
MLFQ_YIELD_SLICE = 4               # a cooperating core releases after this many ticks
MLFQ_BOOST_INTERVAL = 200          # periodic priority boost (anti-starvation)
CFS_SLICE = 10
CFS_WEIGHT_COOP = 1.0
CFS_WEIGHT_DEFECT = 4.0
AFFINITY_QUANTUM = 20
AFFINITY_BALANCE_INTERVAL = 25
AFFINITY_IMBALANCE_THRESHOLD = 2


class Scheduler:
    """Base policy: a single shared FIFO, run-to-completion, personality ignored (FCFS)."""

    name = "base"

    def __init__(self, num_cores, rng):
        self.num_cores = num_cores
        self.rng = rng
        self.ready = []

    def enqueue(self, process, time):
        self.ready.append(process)

    def select(self, core, move, sim):
        """Pick & remove the next process for an idle `core` (or return None)."""
        return self.ready.pop(0) if self.ready else None

    def quantum(self, core, process, move):
        """Time slice for this dispatch; None means run-to-completion (non-preemptive)."""
        return None

    def on_slice_end(self, core, process, used_full, time):
        """Feedback when a slice expires with the process unfinished (before re-enqueue)."""
        pass

    def on_complete(self, core, process, time):
        pass

    def balance(self, sim, time):
        """Per-tick hook for periodic work (load balancing, priority boost)."""
        pass

    def total_ready(self):
        return len(self.ready)


class FCFS(Scheduler):
    name = "fcfs"


class RoundRobin(Scheduler):
    name = "round_robin"

    def quantum(self, core, process, move):
        return RR_SHORT_QUANTUM if move == 'C' else RR_LONG_QUANTUM


class SJF(Scheduler):
    name = "sjf"

    def select(self, core, move, sim):
        if not self.ready:
            return None
        if move == 'C':
            idx = min(range(len(self.ready)), key=lambda k: self.ready[k].remaining_time)
        else:  # Defect: grab the longest job and monopolise the core
            idx = max(range(len(self.ready)), key=lambda k: self.ready[k].remaining_time)
        return self.ready.pop(idx)


class Priority(Scheduler):
    name = "priority"

    def quantum(self, core, process, move):
        # Defect = top priority, run-to-completion; Cooperate = low priority, yields early.
        return None if move == 'D' else PRIORITY_COOP_QUANTUM


class MLFQ(Scheduler):
    name = "mlfq"

    def __init__(self, num_cores, rng):
        super().__init__(num_cores, rng)
        self.levels = [[] for _ in MLFQ_QUANTA]
        self._last_boost = 0

    def enqueue(self, process, time):
        lvl = getattr(process, 'mlfq_level', 0)
        lvl = max(0, min(lvl, len(self.levels) - 1))
        self.levels[lvl].append(process)

    def select(self, core, move, sim):
        for level in self.levels:
            if level:
                return level.pop(0)
        return None

    def quantum(self, core, process, move):
        lvl = getattr(process, 'mlfq_level', 0)
        lvl = max(0, min(lvl, len(self.levels) - 1))
        if move == 'C':
            return min(MLFQ_QUANTA[lvl], MLFQ_YIELD_SLICE)  # voluntary early yield
        return MLFQ_QUANTA[lvl]

    def on_slice_end(self, core, process, used_full, time):
        # A core that Defected burned its full quantum -> demote. Cooperators keep their level.
        if core.last_move == 'D' and process.mlfq_level < len(self.levels) - 1:
            process.mlfq_level += 1

    def balance(self, sim, time):
        if time - self._last_boost >= MLFQ_BOOST_INTERVAL:
            self._last_boost = time
            for lvl in range(1, len(self.levels)):
                for p in self.levels[lvl]:
                    p.mlfq_level = 0
                    self.levels[0].append(p)
                self.levels[lvl] = []

    def total_ready(self):
        return sum(len(level) for level in self.levels)


class CFS(Scheduler):
    name = "cfs"

    def select(self, core, move, sim):
        if not self.ready:
            return None
        idx = min(range(len(self.ready)), key=lambda k: self.ready[k].vruntime)
        return self.ready.pop(idx)

    def quantum(self, core, process, move):
        return CFS_SLICE

    def on_slice_end(self, core, process, used_full, time):
        weight = CFS_WEIGHT_DEFECT if core.last_move == 'D' else CFS_WEIGHT_COOP
        process.vruntime += CFS_SLICE / weight


class Affinity(Scheduler):
    name = "affinity"

    def __init__(self, num_cores, rng):
        super().__init__(num_cores, rng)
        self.queues = [[] for _ in range(num_cores)]
        self._round_robin = 0
        self._last_balance = 0

    def enqueue(self, process, time):
        target = getattr(process, 'last_core', None)
        if target is None or not (0 <= target < self.num_cores):
            target = self._round_robin % self.num_cores
            self._round_robin += 1
        self.queues[target].append(process)

    def select(self, core, move, sim):
        q = self.queues[core.id]
        return q.pop(0) if q else None

    def quantum(self, core, process, move):
        return AFFINITY_QUANTUM

    def balance(self, sim, time):
        if time - self._last_balance < AFFINITY_BALANCE_INTERVAL:
            return
        self._last_balance = time
        loads = [len(q) for q in self.queues]
        busy = max(range(self.num_cores), key=lambda i: loads[i])
        idle = min(range(self.num_cores), key=lambda i: loads[i])
        if loads[busy] - loads[idle] < AFFINITY_IMBALANCE_THRESHOLD:
            return
        # The busy core refuses to give up work while it is Defecting.
        if sim.cores[busy].last_move == 'D':
            return
        p = self.queues[busy].pop()
        p.last_core = idle  # migrated -> will run cold on `idle`'s private cache
        self.queues[idle].append(p)

    def total_ready(self):
        return sum(len(q) for q in self.queues)


SCHEDULERS = {
    'fcfs': FCFS,
    'round_robin': RoundRobin,
    'sjf': SJF,
    'priority': Priority,
    'mlfq': MLFQ,
    'cfs': CFS,
    'affinity': Affinity,
}


def build_scheduler(name, num_cores, rng):
    if name not in SCHEDULERS:
        raise ValueError(f"Unknown scheduler '{name}'. Valid: {sorted(SCHEDULERS)}")
    return SCHEDULERS[name](num_cores, rng)
