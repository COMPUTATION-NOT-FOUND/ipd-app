"""
Cache simulator: one fixed, industry-standard (Skylake-class) L1/L2/L3 hierarchy
with LRU replacement. Deterministic when seeded.

Geometry (per core unless noted), 64-byte lines:
- L1-D : 32 KB,  8-way,  ~4-cycle hit
- L2   : 512 KB, 8-way,  ~12-cycle hit
- L3   : 8 MB,   16-way, ~40-cycle hit   (SHARED across all cores)
- DRAM : ~200-cycle miss penalty

There are deliberately no user-tunable knobs: production uses this single model so
the reported "cache misses" are always a real, explainable number.
"""
import random
import math
from typing import Tuple, Dict


# --- Industry-standard hierarchy constants (Skylake-class desktop) ---
LINE_SIZE = 64
L1_SIZE_BYTES = 32 * 1024
L1_ASSOC = 8
L1_HIT_CYCLES = 4
L2_SIZE_BYTES = 512 * 1024
L2_ASSOC = 8
L2_HIT_CYCLES = 12
L3_SIZE_BYTES = 8 * 1024 * 1024
L3_ASSOC = 16
L3_HIT_CYCLES = 40
DRAM_PENALTY_CYCLES = 200


class CacheConfig:
    """Configuration for a single cache level (LRU)."""

    def __init__(self, level: str, size_bytes: int, line_size: int = LINE_SIZE,
                 associativity: int = 8, hit_latency_cycles: int = 1):
        self.level = level
        self.size_bytes = size_bytes
        self.line_size = line_size
        self.associativity = associativity
        self.hit_latency_cycles = hit_latency_cycles

        if size_bytes % line_size != 0:
            raise ValueError("Size must be a multiple of line size")
        if (size_bytes // line_size) % associativity != 0:
            raise ValueError("Invalid associativity (must divide the cache evenly)")


class Cache:
    """Set-associative cache with LRU replacement.

    Each set is a short list of tags, least recently used first, created only when that set is
    first touched. This is the same true-LRU policy as one counter per line (a hit moves the tag
    to the end; a miss evicts the front once the set is full), so hits and misses are identical,
    without building every set up front: an 8 MB L3 alone has 8192 sets, and a run builds a fresh
    hierarchy per core, workload and seed.
    """

    def __init__(self, config: CacheConfig, rng: random.Random):
        self.config = config
        self.rng = rng

        if not self._is_power_of_two(config.line_size):
            raise ValueError(f"line_size must be power of 2, got {config.line_size}")
        if not self._is_power_of_two(config.associativity):
            raise ValueError(f"associativity must be power of 2, got {config.associativity}")

        self.num_lines = config.size_bytes // config.line_size
        self.num_sets = self.num_lines // config.associativity
        if not self._is_power_of_two(self.num_sets):
            raise ValueError(f"num_sets must be power of 2, got {self.num_sets}")

        self.offset_bits = int(math.log2(config.line_size))
        self.index_bits = int(math.log2(self.num_sets))
        self._index_mask = self.num_sets - 1
        self._assoc = config.associativity
        self._sets = {}                       # set index -> [tags], least recently used first
        self._hit = (True, config.hit_latency_cycles)
        self._miss = (False, config.hit_latency_cycles)

        self.hits = 0
        self.misses = 0

    @staticmethod
    def _is_power_of_two(n: int) -> bool:
        return n > 0 and (n & (n - 1)) == 0

    def _get_set_index(self, address: int) -> int:
        return (address >> self.offset_bits) & self._index_mask

    def _get_tag(self, address: int) -> int:
        return (address >> self.offset_bits) >> self.index_bits

    def access(self, address: int) -> Tuple[bool, int]:
        """Return (hit, latency_cycles) for accessing `address`."""
        block = address >> self.offset_bits
        tag = block >> self.index_bits
        tags = self._sets.get(block & self._index_mask)
        if tags is None:
            self._sets[block & self._index_mask] = [tag]
            self.misses += 1
            return self._miss
        if tag in tags:
            if tags[-1] != tag:               # move to most recently used
                tags.remove(tag)
                tags.append(tag)
            self.hits += 1
            return self._hit
        # Miss: evict the least recently used tag once the set is full, then install.
        self.misses += 1
        if len(tags) >= self._assoc:
            del tags[0]
        tags.append(tag)
        return self._miss

    def flush(self):
        """Invalidate every line (used to model a cold cache after migration)."""
        self._sets.clear()

    def get_stats(self) -> Dict:
        total = self.hits + self.misses
        hit_rate = self.hits / total if total > 0 else 0.0
        return {
            'hits': self.hits,
            'misses': self.misses,
            'accesses': total,
            'hit_rate': hit_rate,
            'miss_rate': 1.0 - hit_rate,
        }


class CacheHierarchy:
    """Per-core L1 -> L2 -> shared L3 -> Memory."""

    def __init__(self, l1: Cache, l2: Cache, l3: Cache, memory_latency_cycles: int = DRAM_PENALTY_CYCLES):
        self.l1 = l1
        self.l2 = l2
        self.l3 = l3
        self.memory_latency = memory_latency_cycles

    def access(self, address: int) -> Tuple[str, int]:
        """Return (level_hit, total_latency). level_hit in {L1, L2, L3, memory}."""
        total = 0
        hit, lat = self.l1.access(address)
        total += lat
        if hit:
            return ("L1", total)
        hit, lat = self.l2.access(address)
        total += lat
        if hit:
            return ("L2", total)
        hit, lat = self.l3.access(address)
        total += lat
        if hit:
            return ("L3", total)
        total += self.memory_latency
        return ("memory", total)

    def flush_private(self):
        """Flush this core's private L1/L2 (the shared L3 is preserved)."""
        self.l1.flush()
        self.l2.flush()

    def get_stats(self) -> Dict:
        return {
            'L1': self.l1.get_stats(),
            'L2': self.l2.get_stats(),
            'L3': self.l3.get_stats(),
        }


def build_core_hierarchies(rng: random.Random, num_cores: int):
    """Build `num_cores` hierarchies with private L1/L2 and ONE shared L3."""
    shared_l3 = Cache(CacheConfig("L3", L3_SIZE_BYTES, LINE_SIZE, L3_ASSOC, L3_HIT_CYCLES), rng)
    hierarchies = []
    for _ in range(num_cores):
        l1 = Cache(CacheConfig("L1", L1_SIZE_BYTES, LINE_SIZE, L1_ASSOC, L1_HIT_CYCLES), rng)
        l2 = Cache(CacheConfig("L2", L2_SIZE_BYTES, LINE_SIZE, L2_ASSOC, L2_HIT_CYCLES), rng)
        hierarchies.append(CacheHierarchy(l1, l2, shared_l3))
    return hierarchies
