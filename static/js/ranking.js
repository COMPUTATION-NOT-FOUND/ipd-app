/*
 * Ranking: which number decides the order of a results table, and how ties are broken.
 * Browser twin of ranking.py (same catalog, same defaults, same rules). Keep the two in step.
 *
 * A spec: { metric, direction: 'asc'|'desc', tiebreak: [metric, ...], shared_rank: bool }.
 * Sort by `metric`; on a tie try each `tiebreak` metric in its own natural direction; if still
 * tied keep the original order. With shared_rank, entries equal on `metric` share a place.
 *
 * Defaults follow the tournament itinerary: 1v1 and N-Player by highest total points, the OS
 * simulation by lowest average response time.
 */

const RANK_TOLERANCE = 1e-9;

const RANK_PD_METRICS = {
    total_points: ['Total points', 'desc'],
    wins: ['Wins', 'desc'],
    win_rate: ['Win rate', 'desc'],
    losses: ['Losses', 'asc'],
    cooperation_percentage: ['Cooperation %', 'desc'],
    points_percentage: ['Points % of maximum', 'desc'],
    weighted_score: ['Weighted score', 'desc'],
    earliest_submission: ['Earliest submission', 'asc'],
};
const RANK_OS_METRICS = {
    avg_response: ['Average response time', 'asc'],
    avg_waiting: ['Average waiting time', 'asc'],
    avg_turnaround: ['Average turnaround time', 'asc'],
    throughput: ['Throughput', 'desc'],
    makespan: ['Makespan', 'asc'],
    avg_cache_misses: ['Average cache misses', 'asc'],
    coop_rate: ['Cooperation %', 'desc'],
    earliest_submission: ['Earliest submission', 'asc'],
};
const RANK_METRICS = { '1v1': RANK_PD_METRICS, nplayer: RANK_PD_METRICS, os: RANK_OS_METRICS };

// Weighted-score weights; the default is all points (Sprint 1).
const RANK_DEFAULT_WEIGHTS = { points: 1.0, cooperation: 0.0, win_rate: 0.0 };
const RANK_WEIGHT_LABELS = { points: 'points', cooperation: 'cooperation', win_rate: 'win rate' };

const RANK_DEFAULTS = {
    '1v1': { metric: 'total_points', direction: 'desc',
             tiebreak: ['cooperation_percentage', 'win_rate', 'earliest_submission'], shared_rank: false,
             weights: Object.assign({}, RANK_DEFAULT_WEIGHTS) },
    nplayer: { metric: 'total_points', direction: 'desc',
               tiebreak: ['cooperation_percentage', 'win_rate', 'earliest_submission'], shared_rank: false,
               weights: Object.assign({}, RANK_DEFAULT_WEIGHTS) },
    os: { metric: 'avg_response', direction: 'asc',
          tiebreak: ['avg_waiting', 'throughput', 'earliest_submission'], shared_rank: false },
};

function rankModeOf(kind) {
    return kind === 'os' ? 'os' : (kind === 'nplayer' ? 'nplayer' : '1v1');
}

function normalizeRanking(spec, mode) {
    const def = RANK_DEFAULTS[mode];
    const cat = RANK_METRICS[mode];
    spec = (spec && typeof spec === 'object') ? spec : {};
    let metric = spec.metric, direction;
    if (!Object.prototype.hasOwnProperty.call(cat, metric) || metric === 'earliest_submission') {
        metric = def.metric;
        direction = def.direction;
    } else {
        direction = (spec.direction === 'asc' || spec.direction === 'desc') ? spec.direction : cat[metric][1];
    }
    const tb = Array.isArray(spec.tiebreak) ? spec.tiebreak : def.tiebreak;
    const clean = [];
    tb.forEach(t => {
        if (typeof t === 'string' && Object.prototype.hasOwnProperty.call(cat, t)
            && t !== metric && !clean.includes(t)) clean.push(t);
    });
    const shared = (spec.shared_rank === undefined) ? def.shared_rank : !!spec.shared_rank;
    const out = { metric, direction, tiebreak: clean.slice(0, 6), shared_rank: shared };
    if (mode !== 'os') out.weights = normalizeWeights(spec.weights);
    return out;
}

// {points, cooperation, win_rate} as fractions adding up to 1 (percentages are accepted).
function normalizeWeights(w) {
    if (!w || typeof w !== 'object') return Object.assign({}, RANK_DEFAULT_WEIGHTS);
    const vals = {};
    let total = 0;
    Object.keys(RANK_DEFAULT_WEIGHTS).forEach(k => {
        const v = w[k];
        vals[k] = (typeof v === 'number' && isFinite(v) && v > 0) ? v : 0;
        total += vals[k];
    });
    if (total <= 0) return Object.assign({}, RANK_DEFAULT_WEIGHTS);
    Object.keys(vals).forEach(k => { vals[k] = Math.round(vals[k] / total * 1e6) / 1e6; });
    return vals;
}

function describeWeights(w) {
    w = normalizeWeights(w);
    return Object.keys(w).filter(k => w[k] > 0)
        .map(k => `${RANK_WEIGHT_LABELS[k]} ${Math.round(w[k] * 100)}%`).join(', ');
}

function rankWinRate(e) {
    const games = (e.wins || 0) + (e.draws || 0) + (e.losses || 0);
    return games > 0 ? (e.wins || 0) / games : null;
}

// (win rate x W + cooperation x W + points x W) x 100, the same formula as ranking.py.
function rankWeightedScore(e, weights) {
    const w = normalizeWeights(weights);
    const clamp = x => Math.max(0, Math.min(1, x));
    const coop = (typeof e.norm_cooperation_percentage === 'number') ? e.norm_cooperation_percentage
        : (e.cooperation_percentage || 0);
    return (clamp(rankWinRate(e) || 0) * w.win_rate + clamp(coop / 100) * w.cooperation
            + clamp((e.points_percentage || 0) / 100) * w.points) * 100;
}

function rankLower(s) { return s.charAt(0).toLowerCase() + s.slice(1); }

function describeRanking(spec, mode) {
    const cat = RANK_METRICS[mode];
    let text = `Ranked by ${rankLower(cat[spec.metric][0])} (${spec.direction === 'asc' ? 'lowest' : 'highest'} first).`;
    if (spec.metric === 'weighted_score' && spec.weights) text += ` Weights: ${describeWeights(spec.weights)}.`;
    if (spec.shared_rank) text += ' Equal scores share a place.';
    else if (spec.tiebreak.length) text += ' Ties: ' + spec.tiebreak.map(t => rankLower(cat[t][0])).join(', then ') + '.';
    return text;
}

function rankDefaultGet(e, metric) {
    if (metric === 'earliest_submission') return e.submitted_at;
    if (metric === 'win_rate' && !('win_rate' in e)) return rankWinRate(e);
    return e[metric];
}

function rankValue(e, metric, get) {
    const v = get(e, metric);
    if (metric === 'earliest_submission') return (typeof v === 'string' && v) ? v : null;
    return (typeof v === 'number' && isFinite(v)) ? v : null;
}

function rankCompare(a, b, direction) {
    if (a === null && b === null) return 0;
    if (a === null) return 1;
    if (b === null) return -1;
    if (typeof a === 'number' && Math.abs(a - b) <= RANK_TOLERANCE) return 0;
    if (a === b) return 0;
    const better = direction === 'asc' ? a < b : a > b;
    return better ? -1 : 1;
}

// Sort `entries` by `spec`, setting `rank` and `rank_label` ('=2' when shared) on each.
// Returns a new array; the original order is the last tie-break.
function rankEntries(entries, spec, mode, get) {
    spec = normalizeRanking(spec, mode);
    get = get || rankDefaultGet;
    const cat = RANK_METRICS[mode];
    if (mode !== 'os') {
        // The weighted score always follows this ranking's weights, so it can't go stale.
        (entries || []).forEach(e => {
            if (e && ('wins' in e || 'points_percentage' in e)) {
                e.weighted_score = rankWeightedScore(e, spec.weights);
                e.win_rate = rankWinRate(e);
            }
        });
    }
    const chain = [[spec.metric, spec.direction]];
    if (!spec.shared_rank) spec.tiebreak.forEach(t => chain.push([t, cat[t][1]]));
    const keyed = (entries || []).map((e, i) => ({ i, e, v: chain.map(([m]) => rankValue(e, m, get)) }));
    keyed.sort((x, y) => {
        for (let k = 0; k < chain.length; k++) {
            const c = rankCompare(x.v[k], y.v[k], chain[k][1]);
            if (c) return c;
        }
        return x.i - y.i;
    });
    const out = [];
    keyed.forEach((k, pos) => {
        const same = spec.shared_rank && pos > 0
            && rankCompare(k.v[0], keyed[pos - 1].v[0], spec.direction) === 0;
        k.e.rank = same ? out[out.length - 1].rank : pos + 1;
        out.push(k.e);
    });
    out.forEach(e => {
        const shared = out.filter(o => o.rank === e.rank).length > 1;
        e.rank_label = (shared ? '=' : '') + e.rank;
    });
    return out;
}

function rankEsc(s) {
    if (s === null || s === undefined) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}

// Compact "Rank by / then by / shared places" controls. `prefix` keeps ids unique per panel;
// `action` is the global function the Apply button calls (via csp_delegation.js data-action).
function rankingControlsHtml(mode, spec, prefix, action) {
    spec = normalizeRanking(spec, mode);
    const cat = RANK_METRICS[mode];
    const keys = Object.keys(cat);
    const opt = (k, sel) => `<option value="${k}"${k === sel ? ' selected' : ''}>${rankEsc(cat[k][0])}</option>`;
    const metricOpts = keys.filter(k => k !== 'earliest_submission').map(k => opt(k, spec.metric)).join('');
    const tbSelect = (n) => {
        const cur = spec.tiebreak[n] || '';
        return `<select id="${prefix}_tb${n}" style="padding:5px; width:auto; display:inline-block;">
            <option value=""${cur ? '' : ' selected'}>(none)</option>${keys.map(k => opt(k, cur)).join('')}</select>`;
    };
    return `<div class="ranking-controls" style="background: rgba(0,0,0,0.2); padding: 12px 15px; border-radius: 8px; margin-bottom: 15px;">
        <h4 style="margin: 0 0 10px 0;">Ranking</h4>
        <div style="display:flex; flex-wrap:wrap; gap:10px; align-items:center;">
            <label>Rank by <select id="${prefix}_metric" style="padding:5px; width:auto; display:inline-block;">${metricOpts}</select></label>
            <select id="${prefix}_dir" style="padding:5px; width:auto; display:inline-block;">
                <option value="desc"${spec.direction === 'desc' ? ' selected' : ''}>highest first</option>
                <option value="asc"${spec.direction === 'asc' ? ' selected' : ''}>lowest first</option>
            </select>
            <label><input type="checkbox" id="${prefix}_shared"${spec.shared_rank ? ' checked' : ''}> equal scores share a place</label>
        </div>
        ${mode === 'os' ? '' : `<div style="display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin-top:8px;">
            <span>Weighted score weights (used when ranking by weighted score):</span>
            ${['points', 'cooperation', 'win_rate'].map(k => `<label>${rankEsc(RANK_WEIGHT_LABELS[k])}
                <input type="number" min="0" max="100" step="5" id="${prefix}_w_${k}" value="${Math.round((spec.weights || {})[k] * 100)}"
                       style="width:70px; padding:4px; display:inline-block;"> %</label>`).join('')}
        </div>`}
        <div style="display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin-top:8px;">
            <span>Ties broken by</span>${tbSelect(0)}<span>then</span>${tbSelect(1)}<span>then</span>${tbSelect(2)}
            <button type="button" data-action="${rankEsc(action)}" style="background: var(--primary-color); color: var(--color-paper);">
                <i class="fas fa-sort-amount-down"></i> Apply</button>
            <button type="button" data-action="${rankEsc(action)}" data-arg="reset" style="background: var(--warning-color); color: var(--color-paper);">
                <i class="fas fa-undo"></i> Reset to default</button>
        </div>
        <p style="margin:8px 0 0; font-size:0.85em; color: var(--text-secondary);">If everything is still equal, the earlier entry stays ahead.</p>
    </div>`;
}

function readRankingControls(prefix, mode) {
    const val = id => { const el = document.getElementById(id); return el ? el.value : ''; };
    const shared = document.getElementById(`${prefix}_shared`);
    const weights = {};
    ['points', 'cooperation', 'win_rate'].forEach(k => {
        const el = document.getElementById(`${prefix}_w_${k}`);
        if (el) weights[k] = parseFloat(el.value) || 0;
    });
    return normalizeRanking({
        weights: Object.keys(weights).length ? weights : undefined,
        metric: val(`${prefix}_metric`),
        direction: val(`${prefix}_dir`),
        tiebreak: [val(`${prefix}_tb0`), val(`${prefix}_tb1`), val(`${prefix}_tb2`)].filter(Boolean),
        shared_rank: !!(shared && shared.checked),
    }, mode);
}

// Plain line shown above a ranked table.
function rankingLineHtml(spec, mode) {
    return `<p class="ranking-line" style="margin: 0 0 10px; color: var(--text-secondary);"><i class="fas fa-sort-amount-down"></i> ${rankEsc(describeRanking(normalizeRanking(spec, mode), mode))}</p>`;
}

// One clear box listing every disqualified strategy and why. `items`: [{name, reason|error, stage?, detail?}].
function disqualifiedHtml(items) {
    if (!Array.isArray(items) || !items.length) return '';
    const rows = items.map(it => {
        const reason = it.reason || it.error || '';
        const stage = it.stage ? ` <span style="color:var(--text-secondary);">(${rankEsc(it.stage)})</span>` : '';
        const detail = it.detail ? ` <span style="color:var(--text-secondary);">${rankEsc(it.detail)}</span>` : '';
        return `<li><strong>${rankEsc(it.name)}</strong>: ${rankEsc(reason)}${stage}${detail}</li>`;
    }).join('');
    const n = items.length;
    return `<div class="disqualified-box" style="border: 2px solid var(--danger-color, #dc3545); padding: 12px 15px; border-radius: 8px; margin-bottom: 16px;">
        <h4 style="margin: 0 0 6px 0; color: var(--danger-color, #dc3545);"><i class="fas fa-ban"></i> Disqualified: ${n} ${n === 1 ? 'strategy' : 'strategies'}</h4>
        <p style="margin: 0 0 8px 0;">Removed from the ranking. Matches against ${n === 1 ? 'it' : 'them'} do not count for anyone.</p>
        <ul style="margin: 0; padding-left: 20px;">${rows}</ul></div>`;
}

// Disqualified list for any result shape (1v1 / N-Player: result.disqualified; OS: errors).
function collectDisqualified(result) {
    if (!result) return [];
    const seen = new Set(), out = [];
    const add = list => (Array.isArray(list) ? list : []).forEach(d => {
        if (d && d.name && !seen.has(d.name)) { seen.add(d.name); out.push(d); }
    });
    add(result.disqualified);
    add(result.runtime_errors);
    add((result.core_simulation_results || {}).errors);
    add((result.core_simulation_heterogeneous || {}).errors);
    add((result.core_simulation_config || {}).disqualified);
    return out;
}
