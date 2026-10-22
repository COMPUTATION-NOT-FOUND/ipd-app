"""How results are ranked: which number decides the order, and how ties are broken.

A ranking spec looks like this (stored on every result, so the website shows the same order):

    {"metric": "total_points", "direction": "desc",
     "tiebreak": ["wins", "cooperation_percentage", "earliest_submission"],
     "shared_rank": False}

Entries are sorted by ``metric``; when two are equal, each ``tiebreak`` metric is tried in turn
(each in its own natural direction); if everything is still equal the original order is kept.
With ``shared_rank`` on, entries equal on the main metric share a place (1, =2, =2, 4).

The defaults follow the tournament itinerary: 1v1 and N-Player rank by highest total points, the
OS simulation by lowest average response time.

Keep in step with static/js/ranking.js (same catalog, same rules).
"""
from functools import cmp_to_key

# Values closer than this count as equal (floats from averaging rarely match exactly).
TOLERANCE = 1e-9

# metric -> (label, natural direction). 'asc' means lower is better.
PD_METRICS = {
    'total_points': ('Total points', 'desc'),
    'wins': ('Wins', 'desc'),
    'losses': ('Losses', 'asc'),
    'cooperation_percentage': ('Cooperation %', 'desc'),
    'points_percentage': ('Points % of maximum', 'desc'),
    'weighted_score': ('Weighted score', 'desc'),
    'earliest_submission': ('Earliest submission', 'asc'),
}
OS_METRICS = {
    'avg_response': ('Average response time', 'asc'),
    'avg_waiting': ('Average waiting time', 'asc'),
    'avg_turnaround': ('Average turnaround time', 'asc'),
    'throughput': ('Throughput', 'desc'),
    'makespan': ('Makespan', 'asc'),
    'avg_cache_misses': ('Average cache misses', 'asc'),
    'coop_rate': ('Cooperation %', 'desc'),
    'earliest_submission': ('Earliest submission', 'asc'),
}
METRICS = {'1v1': PD_METRICS, 'nplayer': PD_METRICS, 'os': OS_METRICS}

DEFAULT_RANKING = {
    '1v1': {'metric': 'total_points', 'direction': 'desc',
            'tiebreak': ['wins', 'cooperation_percentage', 'earliest_submission'],
            'shared_rank': False},
    'nplayer': {'metric': 'total_points', 'direction': 'desc',
                'tiebreak': ['wins', 'cooperation_percentage', 'earliest_submission'],
                'shared_rank': False},
    'os': {'metric': 'avg_response', 'direction': 'asc',
           'tiebreak': ['avg_waiting', 'throughput', 'earliest_submission'],
           'shared_rank': False},
}


def normalize_ranking(spec, mode):
    """Return a clean, complete spec for ``mode``. Unknown or missing parts fall back to the
    default, so a spec sent from a browser can never name a field that isn't in the catalog."""
    default = DEFAULT_RANKING[mode]
    catalog = METRICS[mode]
    if not isinstance(spec, dict):
        spec = {}
    metric = spec.get('metric')
    if metric not in catalog or metric == 'earliest_submission':
        metric = default['metric']
        direction = default['direction']
    else:
        direction = spec.get('direction')
        if direction not in ('asc', 'desc'):
            direction = catalog[metric][1]
    tiebreak = spec.get('tiebreak')
    if not isinstance(tiebreak, list):
        tiebreak = default['tiebreak']
    clean = []
    for t in tiebreak:
        if isinstance(t, str) and t in catalog and t != metric and t not in clean:
            clean.append(t)
    return {'metric': metric, 'direction': direction, 'tiebreak': clean[:6],
            'shared_rank': bool(spec.get('shared_rank', default['shared_rank']))}


def describe_ranking(spec, mode):
    """Plain-English line shown above a table, e.g.
    'Ranked by total points (highest first). Ties: wins, then cooperation %.'"""
    catalog = METRICS[mode]
    label = catalog[spec['metric']][0]
    first = 'lowest first' if spec['direction'] == 'asc' else 'highest first'
    text = f"Ranked by {label[0].lower() + label[1:]} ({first})."
    if spec['shared_rank']:
        text += " Equal scores share a place."
    elif spec['tiebreak']:
        names = [catalog[t][0] for t in spec['tiebreak']]
        names = [n[0].lower() + n[1:] for n in names]
        text += " Ties: " + ", then ".join(names) + "."
    return text


def _value(entry, metric, get):
    v = get(entry, metric)
    if metric == 'earliest_submission':
        return v if isinstance(v, str) and v else None   # ISO 8601 strings sort by time
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return v


def _compare(a, b, direction):
    """-1 if a ranks above b. Missing values always rank last."""
    if a is None and b is None:
        return 0
    if a is None:
        return 1
    if b is None:
        return -1
    if isinstance(a, (int, float)) and abs(a - b) <= TOLERANCE:
        return 0
    if a == b:
        return 0
    better = a < b if direction == 'asc' else a > b
    return -1 if better else 1


def _default_get(entry, metric):
    if metric == 'earliest_submission':
        return entry.get('submitted_at')
    return entry.get(metric)


def rank_entries(entries, spec, mode, get=None):
    """Sort ``entries`` (dicts) by ``spec`` and set ``rank`` (int) and ``rank_label`` ('=2') on
    each. Returns a new list; the input order is the last tie-break."""
    spec = normalize_ranking(spec, mode)
    get = get or _default_get
    catalog = METRICS[mode]
    chain = [(spec['metric'], spec['direction'])]
    if not spec['shared_rank']:
        chain += [(t, catalog[t][1]) for t in spec['tiebreak']]

    keyed = [(i, e, [_value(e, m, get) for m, _ in chain]) for i, e in enumerate(entries)]

    def cmp(x, y):
        for (m, d), va, vb in zip(chain, x[2], y[2]):
            c = _compare(va, vb, d)
            if c:
                return c
        return (x[0] > y[0]) - (x[0] < y[0])

    keyed.sort(key=cmp_to_key(cmp))

    out = []
    for pos, (_, e, vals) in enumerate(keyed):
        if spec['shared_rank'] and pos and _compare(vals[0], keyed[pos - 1][2][0], spec['direction']) == 0:
            rank = out[-1]['rank']
        else:
            rank = pos + 1
        e['rank'] = rank
        out.append(e)
    for e in out:
        shared = sum(1 for o in out if o['rank'] == e['rank']) > 1
        e['rank_label'] = f"={e['rank']}" if shared else str(e['rank'])
    return out


# --- Whole results (used when the website publishes a result) ------------------------------

# Mixture rows from the heterogeneous OS simulation use short metric names.
_OS_SHORT = {'avg_response': 'response', 'avg_waiting': 'wait_time', 'avg_turnaround': 'turnaround',
             'throughput': 'throughput', 'makespan': 'makespan', 'avg_cache_misses': 'cache_misses',
             'coop_rate': 'coop_rate'}


def mode_for_kind(kind):
    return {'os': 'os', 'nplayer': 'nplayer'}.get(kind, '1v1')


def _het_get(row, metric):
    return row.get(_OS_SHORT.get(metric, metric))


def apply_ranking_to_result(result, kind, spec):
    """Re-rank a stored result in place by ``spec`` and set ``ranking``, ``ranking_text`` and
    ``winner``. Works for 1v1 / N-Player (``leaderboard``) and the OS simulation."""
    mode = mode_for_kind(kind)
    spec = normalize_ranking(spec, mode)
    result['ranking'] = spec
    result['ranking_text'] = describe_ranking(spec, mode)
    if mode in ('1v1', 'nplayer'):
        board = [e for e in (result.get('leaderboard') or []) if isinstance(e, dict)]
        result['leaderboard'] = rank_entries(board, spec, mode)
        result['winner'] = result['leaderboard'][0].get('name') if result['leaderboard'] else 'No participants'
        return result
    winner = None
    homo = result.get('core_simulation_results')
    if isinstance(homo, dict) and isinstance(homo.get('strategies'), dict):
        strategies = homo['strategies']
        rows = [{'name': n, 'submitted_at': (v or {}).get('submitted_at'), **((v or {}).get('avg') or {})}
                for n, v in strategies.items()]
        ranked = rank_entries(rows, spec, 'os')
        homo['strategies'] = {r['name']: {**strategies[r['name']], 'rank': r['rank'],
                                          'rank_label': r['rank_label']} for r in ranked}
        homo['order'] = [r['name'] for r in ranked]
        homo['ranking'] = spec
        winner = ranked[0]['name'] if ranked else None
    het = result.get('core_simulation_heterogeneous')
    if isinstance(het, dict) and isinstance(het.get('results'), list):
        het['results'] = rank_entries([r for r in het['results'] if isinstance(r, dict)], spec, 'os',
                                      get=_het_get)
        het['ranking'] = spec
        if het['results'] and winner is None:
            winner = ' + '.join((het['results'][0].get('combination') or {}).keys())
    if winner:
        result['winner'] = winner
    return result


def preview_rows(result, kind):
    """Small JSON-safe rows (name + numbers) for a publish preview, in stored order."""
    mode = mode_for_kind(kind)
    keep = [k for k in METRICS[mode] if k != 'earliest_submission']

    def nums(src, names):
        out = {}
        for k in names:
            v = src.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[k] = v
        return out

    rows = []
    if mode in ('1v1', 'nplayer'):
        for e in result.get('leaderboard') or []:
            if isinstance(e, dict):
                rows.append({'name': str(e.get('name', '')), 'submitted_at': e.get('submitted_at')
                             if isinstance(e.get('submitted_at'), str) else None,
                             **nums(e, keep + ['draws'])})
        return rows
    homo = result.get('core_simulation_results') or {}
    for n, v in (homo.get('strategies') or {}).items():
        v = v or {}
        rows.append({'name': str(n), 'submitted_at': v.get('submitted_at')
                     if isinstance(v.get('submitted_at'), str) else None, **nums(v.get('avg') or {}, keep)})
    for r in (result.get('core_simulation_heterogeneous') or {}).get('results') or []:
        if isinstance(r, dict):
            row = {'name': ' + '.join((r.get('combination') or {}).keys())}
            for k in keep:
                v = _het_get(r, k)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    row[k] = v
            rows.append(row)
    return rows
