"""How results are ranked: which number decides the order, and how ties are broken.

A ranking spec looks like this (stored on every result, so the website shows the same order):

    {"metric": "total_points", "direction": "desc",
     "tiebreak": ["cooperation_percentage", "win_rate", "earliest_submission"],
     "shared_rank": False,
     "weights": {"points": 1.0, "cooperation": 0.0, "win_rate": 0.0}}

Entries are sorted by ``metric``; when two are equal, each ``tiebreak`` metric is tried in turn
(each in its own natural direction); if everything is still equal the original order is kept.
With ``shared_rank`` on, entries equal on the main metric share a place (1, =2, =2, 4).

``weights`` (1v1 and N-Player only) set the weighted score: (win rate x W + cooperation x W +
points x W) x 100, each part a fraction from 0 to 1. Ranking by ``weighted_score`` uses them;
strategies can read them as meta['tournament_info']['weights'].

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
    'win_rate': ('Win rate', 'desc'),
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

# Weighted-score weights; the default is all points (Sprint 1).
DEFAULT_WEIGHTS = {'points': 1.0, 'cooperation': 0.0, 'win_rate': 0.0}
WEIGHT_LABELS = {'points': 'points', 'cooperation': 'cooperation', 'win_rate': 'win rate'}

DEFAULT_RANKING = {
    '1v1': {'metric': 'total_points', 'direction': 'desc',
            'tiebreak': ['cooperation_percentage', 'win_rate', 'earliest_submission'],
            'shared_rank': False, 'weights': dict(DEFAULT_WEIGHTS)},
    'nplayer': {'metric': 'total_points', 'direction': 'desc',
                'tiebreak': ['cooperation_percentage', 'win_rate', 'earliest_submission'],
                'shared_rank': False, 'weights': dict(DEFAULT_WEIGHTS)},
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
    out = {'metric': metric, 'direction': direction, 'tiebreak': clean[:6],
           'shared_rank': bool(spec.get('shared_rank', default['shared_rank']))}
    if mode != 'os':
        out['weights'] = normalize_weights(spec.get('weights'))
    return out


def normalize_weights(weights):
    """{points, cooperation, win_rate} as fractions adding up to 1 (percentages are accepted).
    Anything unusable falls back to the default (all points)."""
    if not isinstance(weights, dict):
        return dict(DEFAULT_WEIGHTS)
    vals = {}
    for k in DEFAULT_WEIGHTS:
        v = weights.get(k, 0)
        vals[k] = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else 0.0
    total = sum(vals.values())
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)
    return {k: round(v / total, 6) for k, v in vals.items()}


def describe_weights(weights):
    """'points 60%, cooperation 20%, win rate 20%' (only the parts that count)."""
    w = normalize_weights(weights)
    parts = [f"{WEIGHT_LABELS[k]} {round(v * 100):g}%" for k, v in w.items() if v > 0]
    return ', '.join(parts)


def win_rate(entry):
    games = sum(entry.get(k, 0) or 0 for k in ('wins', 'draws', 'losses'))
    return (entry.get('wins', 0) or 0) / games if games > 0 else None


def weighted_score(entry, weights):
    """(win rate x W + cooperation x W + points x W) x 100, each part a fraction from 0 to 1.
    Cooperation uses the mode-balanced percentage when there is one; points use the share of the
    best possible points per round. Same formula as the browser (pd_results_render.js)."""
    w = normalize_weights(weights)
    wr = win_rate(entry) or 0.0
    coop = entry.get('norm_cooperation_percentage')
    if not isinstance(coop, (int, float)):
        coop = entry.get('cooperation_percentage') or 0
    pts = entry.get('points_percentage') or 0
    clamp = lambda x: max(0.0, min(1.0, x))   # noqa: E731
    return (clamp(wr) * w['win_rate'] + clamp(coop / 100) * w['cooperation']
            + clamp(pts / 100) * w['points']) * 100


def describe_ranking(spec, mode):
    """Plain-English line shown above a table, e.g.
    'Ranked by total points (highest first). Ties: wins, then cooperation %.'"""
    catalog = METRICS[mode]
    label = catalog[spec['metric']][0]
    first = 'lowest first' if spec['direction'] == 'asc' else 'highest first'
    text = f"Ranked by {label[0].lower() + label[1:]} ({first})."
    if spec['metric'] == 'weighted_score' and spec.get('weights'):
        text += f" Weights: {describe_weights(spec['weights'])}."
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
    if metric == 'win_rate' and 'win_rate' not in entry:
        return win_rate(entry)
    return entry.get(metric)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _stage_value(entry, key):
    v = entry.get(key) if isinstance(entry, dict) else None
    return v if _is_num(v) else None


ELIMINATION_NOTE = ("Elimination: ranked by the round reached, then by place in that round "
                    "(group place first, then score across groups).")


def is_elimination(entries):
    return any(isinstance(e, dict) and _is_num(e.get('stage_reached')) for e in entries or [])


def rank_entries(entries, spec, mode, get=None):
    """Sort ``entries`` (dicts) by ``spec`` and set ``rank`` (int) and ``rank_label`` ('=2') on
    each. Returns a new list; the input order is the last tie-break."""
    spec = normalize_ranking(spec, mode)
    get = get or _default_get
    catalog = METRICS[mode]
    if mode != 'os':
        # The weighted score always follows this ranking's weights, so it can't go stale.
        for e in entries:
            if isinstance(e, dict) and ('wins' in e or 'points_percentage' in e):
                e['weighted_score'] = weighted_score(e, spec['weights'])
                e['win_rate'] = win_rate(e)
    # Elimination results: the bracket decides the order (round reached, then place in that
    # round), so re-ranking a stored result can never contradict who actually went through.
    lead = []
    if is_elimination(entries):
        lead = [('stage_reached', 'desc'), ('stage_order', 'asc')]
    chain = lead + [(spec['metric'], spec['direction'])]
    if not spec['shared_rank']:
        chain += [(t, catalog[t][1]) for t in spec['tiebreak']]
    n_lead = len(lead) + 1   # values that must match for a shared place

    keyed = [(i, e, [_stage_value(e, m) if (m, d) in lead else _value(e, m, get) for m, d in chain])
             for i, e in enumerate(entries)]

    def cmp(x, y):
        for (m, d), va, vb in zip(chain, x[2], y[2]):
            c = _compare(va, vb, d)
            if c:
                return c
        return (x[0] > y[0]) - (x[0] < y[0])

    keyed.sort(key=cmp_to_key(cmp))

    out = []
    for pos, (_, e, vals) in enumerate(keyed):
        if spec['shared_rank'] and pos and all(
                _compare(vals[k], keyed[pos - 1][2][k], chain[k][1]) == 0 for k in range(n_lead)):
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
        if is_elimination(board):
            result['ranking_text'] = ELIMINATION_NOTE + ' ' + result['ranking_text']
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
    if isinstance(het, dict) and isinstance(het.get('leaderboard'), list):
        # Elimination: one row per strategy (its own core's numbers), ordered by the bracket.
        het['leaderboard'] = rank_entries([r for r in het['leaderboard'] if isinstance(r, dict)],
                                          spec, 'os')
        het['ranking'] = spec
        het['ranking_text'] = ELIMINATION_NOTE + ' ' + describe_ranking(spec, 'os')
        if het['leaderboard'] and winner is None:
            winner = het['leaderboard'][0].get('name')
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
                             **nums(e, keep + ['draws', 'stage_reached'])})
        return rows
    homo = result.get('core_simulation_results') or {}
    for n, v in (homo.get('strategies') or {}).items():
        v = v or {}
        rows.append({'name': str(n), 'submitted_at': v.get('submitted_at')
                     if isinstance(v.get('submitted_at'), str) else None, **nums(v.get('avg') or {}, keep)})
    for r in (result.get('core_simulation_heterogeneous') or {}).get('leaderboard') or []:
        if isinstance(r, dict):
            rows.append({'name': str(r.get('name', '')), 'submitted_at': r.get('submitted_at')
                         if isinstance(r.get('submitted_at'), str) else None,
                         **nums(r, keep + ['stage_reached'])})
    for r in (result.get('core_simulation_heterogeneous') or {}).get('results') or []:
        if isinstance(r, dict):
            row = {'name': ' + '.join((r.get('combination') or {}).keys())}
            for k in keep:
                v = _het_get(r, k)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    row[k] = v
            rows.append(row)
    return rows
