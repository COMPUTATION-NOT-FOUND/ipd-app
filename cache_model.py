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


class CacheLine:
    def __init__(self):
        self.valid = False
        self.tag = None
        self.lru_counter = 0


class CacheSet:
    def __init__(self, associativity: int):
        self.lines = [CacheLine() for _ in range(associativity)]
        self.access_counter = 0


class Cache:
    """Set-associative cache with LRU replacement."""

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
        self.sets = [CacheSet(config.associativity) for _ in range(self.num_sets)]

        self.hits = 0
        self.misses = 0

    @staticmethod
    def _is_power_of_two(n: int) -> bool:
        return n > 0 and (n & (n - 1)) == 0

    def _get_set_index(self, address: int) -> int:
        return (address >> self.offset_bits) & ((1 << self.index_bits) - 1)

    def _get_tag(self, address: int) -> int:
        return (address >> self.offset_bits) >> self.index_bits

    def access(self, address: int) -> Tuple[bool, int]:
        """Return (hit, latency_cycles) for accessing `address`."""
        set_index = self._get_set_index(address)
        tag = self._get_tag(address)
        cache_set = self.sets[set_index]

        for i, line in enumerate(cache_set.lines):
            if line.valid and line.tag == tag:
                self.hits += 1
                cache_set.access_counter += 1
                line.lru_counter = cache_set.access_counter
                return (True, self.config.hit_latency_cycles)

        # Miss: evict LRU victim (or an invalid line) and install.
        self.misses += 1
        victim_idx = self._select_victim(cache_set)
        line = cache_set.lines[victim_idx]
        line.valid = True
        line.tag = tag
        cache_set.access_counter += 1
        line.lru_counter = cache_set.access_counter
        return (False, self.config.hit_latency_cycles)

    def _select_victim(self, cache_set: CacheSet) -> int:
        for i, line in enumerate(cache_set.lines):
            if not line.valid:
                return i
        min_counter = min(line.lru_counter for line in cache_set.lines)
        for i, line in enumerate(cache_set.lines):
            if line.lru_counter == min_counter:
                return i
        return 0

    def flush(self):
        """Invalidate every line (used to model a cold cache after migration)."""
        for cache_set in self.sets:
            for line in cache_set.lines:
                line.valid = False
                line.tag = None
                line.lru_counter = 0
            cache_set.access_counter = 0

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
