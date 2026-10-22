"""The faster cache model (lazy sets, tags kept least recently used first) must hit and miss
exactly like the original one-counter-per-line LRU it replaced."""
import random

import pytest

from cache_model import Cache, CacheConfig, build_core_hierarchies

pytestmark = pytest.mark.regression


class ReferenceCache:
    """The original implementation: every set built up front, one LRU counter per line."""

    def __init__(self, num_sets, assoc, offset_bits):
        self.sets = [{'counter': 0, 'lines': [[False, None, 0] for _ in range(assoc)]} for _ in range(num_sets)]
        self.offset_bits = offset_bits
        self.index_bits = num_sets.bit_length() - 1

    def access(self, address):
        idx = (address >> self.offset_bits) & ((1 << self.index_bits) - 1)
        tag = (address >> self.offset_bits) >> self.index_bits
        s = self.sets[idx]
        for line in s['lines']:
            if line[0] and line[1] == tag:
                s['counter'] += 1
                line[2] = s['counter']
                return True
        victim = next((line for line in s['lines'] if not line[0]), None)
        if victim is None:
            low = min(line[2] for line in s['lines'])
            victim = next(line for line in s['lines'] if line[2] == low)
        victim[0], victim[1] = True, tag
        s['counter'] += 1
        victim[2] = s['counter']
        return False


@pytest.mark.parametrize('size, assoc, seed', [(1024, 2, 1), (4096, 4, 2), (32 * 1024, 8, 3)])
def test_same_hits_and_misses_as_the_original(size, assoc, seed):
    cfg = CacheConfig('T', size, 64, assoc, 1)
    fast = Cache(cfg, random.Random(0))
    ref = ReferenceCache(size // 64 // assoc, assoc, 6)
    rng = random.Random(seed)
    hot = [rng.randrange(0, 1 << 20) for _ in range(40)]
    for step in range(30000):
        # Mix of re-used (hot) and fresh addresses, so sets fill, hit and evict.
        addr = rng.choice(hot) + rng.randrange(0, 256) if rng.random() < 0.7 else rng.randrange(0, 1 << 24)
        assert fast.access(addr)[0] == ref.access(addr), step
        if step == 15000:
            fast.flush()
            ref = ReferenceCache(size // 64 // assoc, assoc, 6)
    assert fast.hits + fast.misses == 30000 and 0 < fast.hits < 30000


def test_hierarchy_shares_l3_and_reports_stats():
    h = build_core_hierarchies(random.Random(1), 2)
    assert h[0].l3 is h[1].l3
    assert h[0].access(4096)[0] == 'memory'
    assert h[1].access(4096)[0] == 'L3'          # the other core's miss filled the shared L3
    assert h[0].access(4096)[0] == 'L1'
    assert h[0].get_stats()['L1']['hits'] == 1
