"""Utilities for writing Firestore-compatible documents.

Firestore requires map keys to be strings and does not accept tuple/set types.
This module provides a conservative sanitizer that only transforms common
Python containers and primitives, leaving unknown objects untouched so Firestore
sentinels (e.g., firestore.SERVER_TIMESTAMP) can pass through.
"""

from __future__ import annotations

import math
from typing import Any


def to_firestore_safe(value: Any) -> Any:
    """Recursively convert a Python value to a Firestore-safe shape.

    Conversions:
    - dict keys: converted via str(key)
    - tuple/set: converted to list
    - list: elements converted recursively
    - float NaN/Inf: converted to None (Firestore rejects non-finite floats)

    Unknown objects are returned unchanged.
    """

    if isinstance(value, dict):
        return {str(k): to_firestore_safe(v) for k, v in value.items()}

    if isinstance(value, list):
        return [to_firestore_safe(v) for v in value]

    if isinstance(value, (tuple, set)):
        return [to_firestore_safe(v) for v in value]

    if isinstance(value, float) and not math.isfinite(value):
        return None

    return value


# --- Core-simulation trace (Gantt) Firestore compatibility -------------------
#
# A per-core trace stores `ticks` as a list-of-lists (one row per tick, one cell per core).
# Firestore REJECTS arrays that directly contain arrays, so persisting a trace as-is makes the
# whole document write fail. We encode each tick row as a map `{'c': [...]}` (an array may contain
# maps, and a map may contain an array — only array-directly-in-array is disallowed) on write, and
# decode it back to a plain list-of-lists on read so the JS renderer is unchanged.

def _encode_trace(trace):
    if not isinstance(trace, dict):
        return trace
    ticks = trace.get('ticks')
    if isinstance(ticks, list) and ticks and isinstance(ticks[0], list):
        out = dict(trace)
        out['ticks'] = [{'c': row} for row in ticks]
        out['ticks_encoded'] = True
        return out
    return trace


def _decode_trace(trace):
    if not isinstance(trace, dict):
        return trace
    if trace.get('ticks_encoded'):
        out = dict(trace)
        out['ticks'] = [
            (row.get('c') if isinstance(row, dict) else row)
            for row in (trace.get('ticks') or [])
        ]
        out.pop('ticks_encoded', None)
        return out
    return trace


def encode_core_sim_traces(config):
    """Return a copy of a core_simulation_config with its trace(s) made Firestore-safe.

    Safe to call on None or a config without traces. Used right before persisting.
    """
    if not isinstance(config, dict):
        return config
    out = dict(config)
    if isinstance(out.get('traces'), list):
        out['traces'] = [_encode_trace(t) for t in out['traces']]
    if isinstance(out.get('trace'), dict):
        out['trace'] = _encode_trace(out['trace'])
    return out


def decode_core_sim_traces(config):
    """Reverse :func:`encode_core_sim_traces` so the renderer gets plain list-of-lists ticks."""
    if not isinstance(config, dict):
        return config
    out = dict(config)
    if isinstance(out.get('traces'), list):
        out['traces'] = [_decode_trace(t) for t in out['traces']]
    if isinstance(out.get('trace'), dict):
        out['trace'] = _decode_trace(out['trace'])
    return out
