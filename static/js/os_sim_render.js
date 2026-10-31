/*
 * Shared OS (core scheduling) simulation renderer.
 *
 * Consumes the three blocks produced by the backend core simulation:
 *   { core_simulation_results, core_simulation_heterogeneous, core_simulation_config }
 * and renders per-strategy / ranked-mixture tables, comparison charts and a per-core
 * timeline (mini-Gantt). Used by the standalone OS Simulation tabs (practice + admin)
 * and the results page. Requires Chart.js to be loaded for the charts.
 */

// Private HTML-escape (self-contained so the file has no external dependency).
function osSimEsc(unsafe) {
    if (unsafe === null || unsafe === undefined) return '';
    return String(unsafe)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

// Chart instances are tracked so re-renders can dispose the previous charts.
let simChartInstances = [];

// Chart.js defaults to 12px text, which reads as tiny in these dense charts.
if (typeof Chart !== 'undefined' && Chart.defaults && Chart.defaults.font) Chart.defaults.font.size = 13;

// Long labels (e.g. a four-strategy mixture, or "Baseline · plain Round Robin") were clipped on
// the chart axis. Split them into lines of at most `maxLen` characters (Chart.js draws an array
// label as one line per element).
function osWrapLabel(label, maxLen) {
    maxLen = maxLen || 22;
    const s = String(label == null ? '' : label);
    if (s.length <= maxLen) return s;
    const lines = [];
    let cur = '';
    s.split(' ').forEach(tok => {
        while (tok.length > maxLen) {          // a single over-long token: hard-split it
            if (cur) { lines.push(cur); cur = ''; }
            lines.push(tok.slice(0, maxLen));
            tok = tok.slice(maxLen);
        }
        if (cur && (cur + ' ' + tok).length > maxLen) { lines.push(cur); cur = tok; }
        else cur = cur ? cur + ' ' + tok : tok;
    });
    if (cur) lines.push(cur);
    return lines;
}

// Auto-ranged axes zoom in on tiny differences: a throughput spread of 40.11 to 40.13 filled the
// whole axis and looked like a big gap. Keep at least `minRel` of the values' magnitude in view
// so small differences look small. Returns {} (auto range) when the spread is already wide.
function osAxisRange(values, minRel) {
    const v = values.filter(x => typeof x === 'number' && isFinite(x));
    if (!v.length) return {};
    const lo = Math.min(...v), hi = Math.max(...v);
    const minSpan = Math.max(Math.abs(lo), Math.abs(hi), 1e-9) * (minRel || 0.2);
    if (hi - lo >= minSpan) return {};
    let min = (lo + hi) / 2 - minSpan / 2, max = (lo + hi) / 2 + minSpan / 2;
    if (lo >= 0 && min < 0) { max -= min; min = 0; }
    return { min, max };
}

const OS_SIM_SCHEDULER_LABELS = {
    fcfs: 'FCFS', round_robin: 'Round Robin', sjf: 'SJF / SRTF',
    priority: 'Priority', mlfq: 'MLFQ', cfs: 'CFS', affinity: 'Affinity-aware'
};

const OS_WORKLOAD_PROFILES = ['Uniform', 'Poisson', 'Mixed', 'Bursty'];

// Pull the metrics for a given workload `profile` ('avg' for the averaged row) from a source
// dict, tolerating both the homogeneous (avg_*) and heterogeneous (wait_time/...) key shapes.
function osPickMetrics(src) {
    if (!src) return { throughput: 0, wait: 0, turnaround: 0, response: 0, cache: 0, makespan: 0, coop: 0 };
    return {
        throughput: src.throughput || 0,
        wait: (src.wait_time != null ? src.wait_time : src.avg_waiting) || 0,
        turnaround: (src.turnaround != null ? src.turnaround : src.avg_turnaround) || 0,
        response: (src.response != null ? src.response : src.avg_response) || 0,
        cache: (src.cache_misses != null ? src.cache_misses : src.avg_cache_misses) || 0,
        makespan: src.makespan || 0,
        coop: src.coop_rate || 0,
    };
}

// The 95% confidence-interval half-widths that come with averaged runs (absent for a single run
// or for results published before averaging existed), under the same short names as osPickMetrics.
function osPickCI(src) {
    const ci = (src && src.ci) || {};
    const pick = (a, b) => (ci[a] != null ? ci[a] : (ci[b] != null ? ci[b] : null));
    return {
        throughput: pick('throughput'), wait: pick('wait_time', 'avg_waiting'),
        turnaround: pick('turnaround', 'avg_turnaround'), response: pick('response', 'avg_response'),
        cache: pick('cache_misses', 'avg_cache_misses'), makespan: pick('makespan'), coop: pick('coop_rate'),
    };
}

// Heterogeneous result in elimination style (one row per strategy, plus a bracket).
function osIsElimination(sim) {
    const het = sim && sim.core_simulation_heterogeneous;
    return !!(het && het.style === 'elimination' && Array.isArray(het.leaderboard) && het.leaderboard.length);
}

// Build a uniform list of entries for a given `profile` ('avg' or a workload name) from either
// the heterogeneous combinations or the homogeneous per-strategy results.
function simEntries(sim, profile) {
    profile = profile || 'avg';
    const useProf = profile !== 'avg';
    const het = sim.core_simulation_heterogeneous;
    if (osIsElimination(sim)) {
        // One row per strategy, measured on its own core in the last group it played. The
        // round reached travels along so the ranking keeps the bracket order.
        const last = het.bracket_rounds || 0;
        return het.leaderboard.map(r => {
            const src = (useProf && r.workloads && r.workloads[profile]) ? r.workloads[profile] : r;
            return Object.assign({ label: r.name, ci: osPickCI(src), submitted_at: r.submitted_at,
                                   stage_reached: r.stage_reached, stage_order: r.stage_order,
                                   reached: r.stage_reached === last ? 'Final' : `Round ${r.stage_reached}` },
                                 osPickMetrics(src));
        });
    }
    if (het && Array.isArray(het.results) && het.results.length) {
        return het.results.map(r => {
            const src = (useProf && r.workloads && r.workloads[profile]) ? r.workloads[profile] : r;
            return Object.assign({ label: Object.keys(r.combination || {}).join(' + '), ci: osPickCI(src),
                                   combo: r.combination || {} }, osPickMetrics(src));
        });
    }
    const homo = sim.core_simulation_results;
    if (homo && homo.strategies) {
        return Object.entries(homo.strategies).map(([name, d]) => {
            const src = (useProf && d.workloads && d.workloads[profile]) ? d.workloads[profile] : (d.avg || {});
            return Object.assign({ label: name, ci: osPickCI(src), submitted_at: d.submitted_at }, osPickMetrics(src));
        });
    }
    return [];
}

// Ranking for OS results (static/js/ranking.js): the spec saved on the result, else the
// itinerary default (lowest average response time). Rows use short metric names.
const OS_RANK_KEYS = { avg_response: 'response', avg_waiting: 'wait', avg_turnaround: 'turnaround',
                       throughput: 'throughput', makespan: 'makespan', avg_cache_misses: 'cache',
                       coop_rate: 'coop', earliest_submission: 'submitted_at' };
function osRankSpec(sim) {
    const het = sim.core_simulation_heterogeneous || {}, homo = sim.core_simulation_results || {};
    return (typeof normalizeRanking === 'function')
        ? normalizeRanking(sim.ranking || het.ranking || homo.ranking, 'os') : null;
}
function osRankGet(e, metric) { return e[OS_RANK_KEYS[metric] || metric]; }
// Chart title for the ranking number, e.g. "Average response time (lower is better, decides the rank)".
function osRankTitle(sim) {
    const spec = osRankSpec(sim);
    const label = (spec && typeof RANK_METRICS !== 'undefined') ? RANK_METRICS.os[spec.metric][0] : 'Average response time';
    const dir = (spec ? spec.direction : 'asc') === 'asc' ? 'lower is better' : 'higher is better';
    return `${label} (${dir}, decides the rank)`;
}
function rankedSimEntries(sim, profile) {
    const entries = simEntries(sim, profile);
    const spec = osRankSpec(sim);
    if (spec) return rankEntries(entries, spec, 'os', osRankGet);
    entries.sort((a, b) => a.response - b.response);
    entries.forEach((e, i) => { e.rank = i + 1; e.rank_label = String(i + 1); });
    return entries;
}

// The baseline (vanilla selected scheduler) entry for a given profile, or null if absent.
function simBaselineEntry(sim, profile) {
    profile = profile || 'avg';
    const b = (sim.core_simulation_results && sim.core_simulation_results.baseline)
        || (sim.core_simulation_heterogeneous && sim.core_simulation_heterogeneous.baseline);
    if (!b) return null;
    const src = (profile !== 'avg' && b.workloads && b.workloads[profile]) ? b.workloads[profile] : (b.avg || {});
    const schedLabel = OS_SIM_SCHEDULER_LABELS[b.scheduler] || b.scheduler || 'scheduler';
    return Object.assign({ label: 'Baseline · plain ' + schedLabel, isBaseline: true, ci: osPickCI(src) }, osPickMetrics(src));
}

// Theme colors are read at render time via srToken/srPalette/srAlpha (static/js/theme.js),
// so these are functions rather than module-level consts: a const evaluated at load time
// would freeze whichever theme happened to be active on first paint.
function osBaselineColor() { return srToken('--chart-baseline', '#6b6e76'); }

function createSimulationComparisonChart(sim, profile) {
    simChartInstances.forEach(c => { try { c.destroy(); } catch (e) {} });
    simChartInstances = [];
    const entries = rankedSimEntries(sim, profile);
    if (!entries.length || typeof Chart === 'undefined') return;
    const baseline = simBaselineEntry(sim, profile);

    // 1) Bar of the number the ranking uses (default: average response time), in ranked order,
    //    with the baseline appended as a grey reference bar.
    const spec = osRankSpec(sim);
    const rankKey = spec ? (OS_RANK_KEYS[spec.metric] || 'response') : 'response';
    const rankLabel = (spec && typeof RANK_METRICS !== 'undefined') ? RANK_METRICS.os[spec.metric][0] : 'Average response time';
    const barCtx = document.getElementById('simThroughputChart');
    if (barCtx) {
        const barEntries = baseline ? entries.concat([baseline]) : entries;
        simChartInstances.push(new Chart(barCtx, {
            type: 'bar',
            data: { labels: barEntries.map(e => osWrapLabel(e.label)),
                datasets: [{ label: rankLabel, data: barEntries.map(e => e[rankKey]),
                    backgroundColor: barEntries.map(e => e.isBaseline ? osBaselineColor() : srAlpha(srToken('--chart-1', '#2dd4bf'), 60)) }] },
            // Bars start at 0 so their lengths compare honestly.
            options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false,
                scales: { x: { beginAtZero: true } },
                plugins: { legend: { display: false } } }
        }));
    }

    // 2) Throughput (y, higher better) vs Response (x, lower better) scatter + baseline marker.
    const scCtx = document.getElementById('simScatterChart');
    if (scCtx) {
        const datasets = [{ label: 'mixtures / strategies',
            data: entries.map(e => ({ x: e.response, y: e.throughput, label: e.label })),
            backgroundColor: srAlpha(srToken('--chart-1', '#2dd4bf'), 70) }];
        if (baseline) datasets.push({ label: 'baseline', pointStyle: 'rectRot', pointRadius: 8,
            data: [{ x: baseline.response, y: baseline.throughput, label: baseline.label }],
            backgroundColor: osBaselineColor() });
        const all = baseline ? entries.concat([baseline]) : entries;
        simChartInstances.push(new Chart(scCtx, {
            type: 'scatter', data: { datasets },
            options: { responsive: true, maintainAspectRatio: false,
                scales: { x: Object.assign({ title: { display: true, text: 'Avg Response (lower better)' } },
                                           osAxisRange(all.map(e => e.response))),
                          y: Object.assign({ title: { display: true, text: 'Throughput (higher better)' } },
                                           osAxisRange(all.map(e => e.throughput))) },
                plugins: { legend: { display: !!baseline },
                    tooltip: { callbacks: { label: (c) => `${c.raw.label}: tp ${c.raw.y.toFixed(1)}, resp ${c.raw.x.toFixed(1)}` } } } }
        }));
    }

    // 3) Cooperation scatter (x = throughput, y = cooperation %) — same shape as the PD modes'
    //    Avg-Points-vs-Cooperation chart, so OS strategies line up against 1v1 / N-Player.
    const coopCtx = document.getElementById('simCoopChart');
    if (coopCtx) simChartInstances.push(new Chart(coopCtx, {
        type: 'scatter',
        data: { datasets: [{ label: 'strategies',
            data: entries.map(e => ({ x: e.throughput, y: e.coop, label: e.label })),
            backgroundColor: srToken('--chart-1', '#2dd4bf') }] },
        options: { responsive: true, maintainAspectRatio: false,
            scales: { x: Object.assign({ title: { display: true, text: 'Throughput (higher better)' } },
                                       osAxisRange(entries.map(e => e.throughput))),
                      y: { min: 0, max: 100, title: { display: true, text: 'Cooperation %' } } },
            plugins: { legend: { display: false },
                tooltip: { callbacks: { label: (c) => `${c.raw.label}: tp ${c.raw.x.toFixed(1)}, coop ${c.raw.y.toFixed(1)}%` } } } }
    }));

    // 4) Multi-metric radar (normalised; throughput kept, others inverted so "outer = better").
    //    Baseline is included both in the scaling and as a dashed reference series.
    const rdCtx = document.getElementById('simRadarChart');
    if (rdCtx) {
        const axes = ['throughput', 'wait', 'turnaround', 'response', 'cache'];
        const scaleSet = baseline ? entries.concat([baseline]) : entries;
        const norm = {};
        axes.forEach(k => { const vals = scaleSet.map(e => e[k]); norm[k] = { min: Math.min(...vals), max: Math.max(...vals) }; });
        const scale = (k, v) => { const { min, max } = norm[k]; if (max === min) return 0.5;
            const t = (v - min) / (max - min); return k === 'throughput' ? t : 1 - t; };
        const palette = srPalette();
        const top = entries.slice(0, 5);
        const datasets = top.map((e, i) => ({ label: e.label,
            data: axes.map(k => scale(k, e[k])),
            borderColor: palette[i % palette.length],
            backgroundColor: srAlpha(palette[i % palette.length], 13) }));
        if (baseline) datasets.push({ label: baseline.label, data: axes.map(k => scale(k, baseline[k])),
            borderColor: osBaselineColor(), borderDash: [5, 4], backgroundColor: 'transparent' });
        simChartInstances.push(new Chart(rdCtx, {
            type: 'radar',
            data: { labels: ['Throughput', 'Wait', 'Turnaround', 'Response', 'Cache'], datasets },
            options: { responsive: true, maintainAspectRatio: false,
                scales: { r: { min: 0, max: 1, ticks: { display: false } } } }
        }));
    }
}

function osGanttPalette() { return srPalette(); }
const OS_GANTT_CELL = 4;  // px per tick

// Draw one trace's mini-Gantt (rows = cores, columns = ticks; filled = busy) into `el`,
// with a per-core legend and a tick scale so the strip is interpretable.
function drawSimGantt(el, trace) {
    if (!el || !trace || !Array.isArray(trace.ticks) || !trace.ticks.length) {
        if (el) el.innerHTML = '<div style="color:var(--text-secondary); font-size:0.85em;">No timeline for this layout.</div>';
        return;
    }
    const cores = trace.cores || [];
    const nCores = cores.length || (trace.ticks[0] ? trace.ticks[0].length : 0);
    const nTicks = trace.ticks.length;

    // Legend: color ↔ core personality.
    let legend = '<div style="display:flex; flex-wrap:wrap; gap:12px; margin-bottom:8px; font-size:0.82em;">';
    for (let c = 0; c < nCores; c++) {
        const color = osGanttPalette()[c % 8];
        legend += `<span style="display:inline-flex; align-items:center; gap:5px;">
            <span style="display:inline-block; width:11px; height:11px; background:${color}; border-radius:2px;"></span>
            Core ${c}: ${osSimEsc(cores[c] || ('Core ' + c))}</span>`;
    }
    legend += '<span style="display:inline-flex; align-items:center; gap:5px;"><span style="display:inline-block; width:11px; height:11px; background:var(--bg-card); border:1px solid var(--border-color); border-radius:2px;"></span>idle</span>';
    legend += '</div>';

    let html = legend + '<div style="overflow-x:auto;">';
    for (let core = 0; core < nCores; core++) {
        const color = osGanttPalette()[core % 8];
        let row = `<div style="display:flex; align-items:center; margin-bottom:3px;">
            <div style="width:140px; flex:none; font-size:0.8em; color:var(--text-secondary); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="${osSimEsc(cores[core] || ('Core ' + core))}">
                <span style="display:inline-block; width:10px; height:10px; background:${color}; border-radius:2px; margin-right:5px;"></span>${osSimEsc(cores[core] || ('Core ' + core))}
            </div>
            <div style="display:flex; gap:0;">`;
        trace.ticks.forEach(t => {
            const busy = t[core] !== null && t[core] !== undefined;
            row += `<div style="width:${OS_GANTT_CELL}px; height:16px; background:${busy ? color : 'var(--bg-card)'};" title="${busy ? 'pid ' + t[core] : 'idle'}"></div>`;
        });
        row += '</div></div>';
        html += row;
    }
    // Tick scale: a ruler under the rows with labels every ~50 ticks.
    let ruler = `<div style="display:flex; margin-top:2px;"><div style="width:140px; flex:none;"></div><div style="position:relative; height:16px; width:${nTicks * OS_GANTT_CELL}px; color:var(--text-secondary); font-size:0.7em;">`;
    const step = nTicks > 100 ? 50 : (nTicks > 40 ? 20 : 10);
    for (let tk = 0; tk <= nTicks; tk += step) {
        ruler += `<span style="position:absolute; left:${tk * OS_GANTT_CELL}px; border-left:1px solid var(--border-color); padding-left:2px;">${tk}</span>`;
    }
    ruler += '</div></div>';
    html += ruler + `<div style="color:var(--text-secondary); font-size:0.75em; margin-top:4px;">tick (${osSimEsc(trace.workload || 'Mixed')} workload)</div>`;
    html += '</div>';
    el.innerHTML = html;
}

// Build the OS-sim tables + chart canvases + Gantt container into `container`, then draw.
// Build the metrics table (per-strategy or ranked-mixture) for a given workload `profile`,
// including a Cooperation % column and a trailing baseline reference row.
function buildSimTable(sim, profile) {
    const isHet = !!(sim.core_simulation_heterogeneous
        && Array.isArray(sim.core_simulation_heterogeneous.results)
        && sim.core_simulation_heterogeneous.results.length);
    // Rows are ordered by the result's ranking (default: lowest average response time), for the
    // selected workload too. Each mixture row carries its own combination, so sorting is safe.
    const entries = rankedSimEntries(sim, profile);
    const spec = osRankSpec(sim);
    const baseline = simBaselineEntry(sim, profile);
    // A value with its +/- range (95% confidence interval) when the result averages several runs.
    const cell = (e, k, digits, suffix) => {
        const v = e[k].toFixed(digits) + (suffix || '');
        const ci = e.ci && e.ci[k];
        return ci != null ? `${v} <small style="color:var(--text-secondary); white-space:nowrap;">&plusmn;${ci.toFixed(digits)}</small>` : v;
    };
    const down = '<i class="fas fa-arrow-down" style="font-size:0.7em"></i>';
    const up = '<i class="fas fa-arrow-up" style="font-size:0.7em"></i>';

    let html = (spec && typeof rankingLineHtml === 'function') ? rankingLineHtml(spec, 'os') : '';
    const rankKey = spec ? (OS_RANK_KEYS[spec.metric] || 'response') : 'response';
    const cols = [['response', 'Response', down, 2], ['wait', 'Wait', down, 2], ['throughput', 'Throughput', up, 2],
                  ['turnaround', 'Turnaround', down, 2], ['makespan', 'Makespan', down, 2],
                  ['cache', 'Cache Misses', down, 1], ['coop', 'Coop %', up, 1]];
    // The ranking column goes first and is highlighted, so it is clear which number decides the rank.
    cols.sort((a, b) => (b[0] === rankKey) - (a[0] === rankKey));
    const head = cols.map(([k, label, arrow]) => k === rankKey
        ? `<th style="color:var(--primary-color);" title="This column decides the rank">${label} ${arrow} <i class="fas fa-trophy"></i></th>`
        : `<th>${label} ${arrow}</th>`).join('');
    const rowCells = (e, isBase) => cols.map(([k, , , d]) => {
        if (isBase && k === 'coop') return '<td>&mdash;</td>';
        const v = cell(e, k, d, k === 'coop' ? '%' : '');
        return (k === rankKey && !isBase) ? `<td><strong style="color:var(--primary-muted);">${v}</strong></td>` : `<td>${v}</td>`;
    }).join('');
    html += `<table class="leaderboard-table"><thead><tr>
        <th>Rank</th><th>${isHet ? 'Mixture' : 'Strategy'}</th>${head}</tr></thead><tbody>`;

    entries.forEach((e, idx) => {
        let nameCell;
        if (e.reached) {
            nameCell = `<strong>${osSimEsc(e.label)}</strong> <small style="color:var(--text-secondary);">${osSimEsc(e.reached)}</small>`;
        } else if (isHet) {
            const combo = e.combo || {};
            nameCell = Object.keys(combo)
                .map(n => `<span class="badge" style="background:var(--bg-card); border:1px solid var(--border-color); margin:1px;">${osSimEsc(n)}</span>`).join(' ');
        } else {
            nameCell = `<strong>${osSimEsc(e.label)}</strong>`;
        }
        html += `<tr class="${e.rank === 1 ? 'rank-1' : ''}">
            <td title="${String(e.rank_label).startsWith('=') ? 'Tied' : ''}"><strong>${osSimEsc(e.rank_label)}</strong></td>
            <td>${nameCell}</td>
            ${rowCells(e, false)}
        </tr>`;
    });
    if (baseline) {
        html += `<tr style="border-top:2px solid var(--border-color); color:var(--text-secondary);">
            <td></td><td><em>${osSimEsc(baseline.label)}</em></td>
            ${rowCells(baseline, true)}
        </tr>`;
    }
    html += `</tbody></table>`;
    const elimNote = 'Elimination: each strategy is measured on its own core (tasks it started), in the last group it played. '
        + 'Order follows the bracket: round reached first. ';
    html += `<p style="color:var(--text-secondary); font-size:0.85em; margin-top:8px;">${osIsElimination(sim) ? elimNote : (isHet ? 'Each mixture uses each strategy at most once. ' : '')}`
        + `The highlighted column decides the rank. The baseline row is the plain scheduler with no strategy, for comparison; it is not ranked.</p>`;
    return html;
}

// `sim` is { core_simulation_results, core_simulation_heterogeneous, core_simulation_config }.
function renderOSSimulation(container, sim, opts) {
    opts = opts || {};
    if (typeof container === 'string') container = document.getElementById(container);
    if (!container) return;
    sim = sim || {};
    const hetSim = sim.core_simulation_heterogeneous;
    const homoSim = sim.core_simulation_results;
    const isElim = osIsElimination(sim);
    const hasHetSim = isElim || !!(hetSim && Array.isArray(hetSim.results) && hetSim.results.length);
    const hasHomoSim = !!(homoSim && homoSim.strategies && Object.keys(homoSim.strategies).length);

    // Surface a backend error block if present.
    const errBlock = (homoSim && homoSim.error) ? homoSim.error : null;
    if (!hasHetSim && !hasHomoSim) {
        container.innerHTML = `<div class="results" style="color: var(--danger-color); padding: 16px;">
            <i class="fas fa-exclamation-circle"></i> ${errBlock ? osSimEsc(errBlock) : 'No simulation results were produced.'}
        </div>`;
        return;
    }

    const cfg = sim.core_simulation_config || {};
    const numCores = cfg.num_cores || (hasHetSim ? hetSim.num_cores : (homoSim && homoSim.num_cores)) || 2;
    const scheduler = cfg.scheduler || (hasHetSim ? hetSim.scheduler : (homoSim && homoSim.scheduler)) || 'round_robin';
    const schedLabel = OS_SIM_SCHEDULER_LABELS[scheduler] || scheduler;
    const isHet = hasHetSim;

    // How much of the C(n, cores) space was actually covered. `sampled` is authoritative when
    // present; results published before sampling existed have neither flag, so fall back to
    // comparing the two counts rather than mislabelling old data as a partial sweep.
    const legacyHet = isHet && !isElim;
    const hetEvaluated = legacyHet ? (hetSim.evaluated || 0) : 0;
    const hetTotal = legacyHet ? (hetSim.total_combinations || hetEvaluated) : 0;
    const isHetSampled = legacyHet && (typeof hetSim.sampled === 'boolean'
        ? hetSim.sampled
        : (hetTotal > 0 && hetEvaluated < hetTotal));
    const hetSeedNote = (hetSim && hetSim.seed !== null && hetSim.seed !== undefined)
        ? ` with seed ${hetSim.seed}` : '';
    // Not sampled but fewer ranked than the total: the missing mixtures held a disqualified
    // strategy, so they were seen and left out (not skipped by the time budget).
    const combinationsLabel = isHetSampled
        ? `${hetEvaluated.toLocaleString()} of ${hetTotal.toLocaleString()} (sampled)`
        : (hetEvaluated < hetTotal
            ? `${hetEvaluated.toLocaleString()} of ${hetTotal.toLocaleString()} (rest held a disqualified strategy)`
            : `${hetEvaluated.toLocaleString()} (all)`);
    const elimLabel = isElim && typeof eliminationSummary === 'function'
        ? eliminationSummary(hetSim.bracket, numCores) : '';

    // The header (title + config bar) is shown by default. When opts.header === false the caller
    // (e.g. the results modal) supplies its own shared header, so we skip ours to avoid duplication.
    // On the practice page, wrap in the same grey "results" card as 1v1 / N-Player; the modal
    // supplies its own container so we use a plain div there.
    const wrapAttrs = (opts.header !== false)
        ? ' class="results" style="border-left: 4px solid var(--primary-color); background: var(--bg-card);"'
        : '';
    let html = `<div${wrapAttrs}>`;
    if (opts.header !== false) {
        html += `
        <h3 style="margin-bottom: 12px; color: var(--primary-color) !important;"><i class="fas fa-microchip"></i> OS Simulation Results</h3>
        <div style="background: var(--bg-hover); padding: 12px 15px; border-radius: 8px; margin-bottom: 18px; border-left: 4px solid var(--info-color); display:flex; gap:24px; flex-wrap:wrap;">
            <span><span class="badge" style="background-color:${isHet ? 'var(--warning-color)' : 'var(--info-color)'};">${isElim ? 'HETEROGENEOUS · ELIMINATION' : (isHet ? 'HETEROGENEOUS · LEGACY' : 'HOMOGENEOUS')}</span></span>
            <span><strong>Scheduler:</strong> ${osSimEsc(schedLabel)}</span>
            <span><strong>Cores:</strong> ${numCores}</span>
            ${legacyHet ? `<span><strong>Combinations:</strong> ${osSimEsc(combinationsLabel)}</span>` : ''}
            ${isElim ? `<span>${osSimEsc(elimLabel)}</span>` : ''}
        </div>`;
    }

    // Sampling notice. Emitted outside the header block on purpose: a published result viewed in
    // the results modal (which supplies its own header) must carry the caveat too, otherwise a
    // sampled tournament reads as an exhaustive one on the website.
    if (isHetSampled) {
        html += `
        <div style="background: color-mix(in srgb, var(--warning-color) 12%, transparent); border: 1px solid color-mix(in srgb, var(--warning-color) 35%, transparent); color: var(--text-primary); padding: 12px 15px; border-radius: var(--radius-sm); margin-bottom: 18px;">
            <strong style="color: var(--warning-color);"><i class="fas fa-triangle-exclamation"></i> Partial sweep:</strong>
            ${hetEvaluated.toLocaleString()} of ${hetTotal.toLocaleString()} core assignments were evaluated
            (${((hetEvaluated / hetTotal) * 100).toFixed(1)}%), chosen at random${hetSeedNote}.
            The ranking below is over that sample, so <strong>the top mixture is not provably the best</strong>
            &mdash; an unevaluated assignment may beat it. Re-run with limits removed to sweep the whole space.
        </div>`;
    }

    // What the numbers were averaged over, and whether the setup overloads the cores.
    const runInfo = (isHet ? hetSim : homoSim) || {};
    if (runInfo.runs) {
        const seeds = Array.isArray(runInfo.seeds) ? runInfo.seeds : [];
        const seedText = seeds.length > 1 ? `seeds ${seeds[0]}–${seeds[seeds.length - 1]}`
                                          : (seeds.length ? `seed ${seeds[0]}` : '');
        const warm = runInfo.warmup ? `, first ${Math.round(runInfo.warmup * 100)}% of tasks left out as warm-up` : '';
        html += `<p style="color:var(--text-secondary); font-size:0.9em; margin: 0 0 14px;">
            <i class="fas fa-layer-group"></i>
            ${runInfo.runs > 1 ? `Averaged over ${runInfo.runs} runs (${osSimEsc(seedText)})` : `One run (${osSimEsc(seedText)})`},
            ${osSimEsc(runInfo.num_processes || 50)} tasks per workload${warm}.
            ${runInfo.runs > 1 ? '&plusmn; is a 95% confidence interval: the true average is very likely within that range, so rows whose ranges overlap are not clearly different.' : 'Average several runs for a fairer comparison.'}
        </p>`;
    }
    const over = Array.isArray(runInfo.overloaded_workloads) ? runInfo.overloaded_workloads : [];
    if (over.length) {
        const base = (runInfo.baseline && runInfo.baseline.workloads) || {};
        const loads = over.map(wl => `${osSimEsc(wl)}${base[wl] && base[wl].load != null ? ' (' + Math.round(base[wl].load * 100) + '%)' : ''}`).join(', ');
        html += `
        <div style="background: color-mix(in srgb, var(--warning-color) 12%, transparent); border: 1px solid color-mix(in srgb, var(--warning-color) 35%, transparent); color: var(--text-primary); padding: 12px 15px; border-radius: var(--radius-sm); margin-bottom: 18px;">
            <strong style="color: var(--warning-color);"><i class="fas fa-triangle-exclamation"></i> The cores can't keep up:</strong>
            in ${loads} the tasks need more CPU time than ${numCores} cores can give (load above 100%), so
            the queue keeps growing. Waiting and turnaround times then mostly reflect how many tasks you
            simulated rather than the scheduler. To compare normal running, add cores.
        </div>`;
    }

    // Workload-profile selector: switch the table + charts between the four datasets and the
    // averaged view, so it's clear how the cores did on each dataset.
    const profileOpts = ['avg'].concat(OS_WORKLOAD_PROFILES)
        .map(p => `<option value="${p}">${p === 'avg' ? 'Average (all profiles)' : osSimEsc(p)}</option>`).join('');
    const hasBaseline = !!simBaselineEntry(sim, 'avg');
    html += `<div style="margin-bottom: 18px;">
        <h4 style="border-bottom:1px solid var(--border-color); padding-bottom:8px;">${legacyHet ? 'Ranked Mixtures' : 'Per-Strategy Metrics'}</h4>
        <div style="margin:10px 0;">
            <label style="font-size:0.85em; color:var(--text-secondary); margin-right:8px;">Workload dataset:</label>
            <select id="simProfileSelect" style="padding:6px;">${profileOpts}</select>
            <span id="simProfileCaption" style="font-size:0.82em; color:var(--text-secondary); margin-left:10px;"></span>
        </div>
        <div id="simMetricsTable"></div>
    </div>`;

    if (isElim && typeof eliminationBracketHtml === 'function') {
        html += `<div style="margin-bottom: 18px;"><h4 style="border-bottom:1px solid var(--border-color); padding-bottom:8px;">Bracket</h4>
            ${eliminationBracketHtml(hetSim.bracket, {
                scoreLabel: 'average response time of the tasks that core started, in that group (lower is better)',
                score: p => (typeof p.avg_response === 'number') ? p.avg_response.toFixed(1) : '',
            })}</div>`;
    }

    // Only one way to place the strategies: e.g. 4 strategies on 4 cores, each used once.
    if (legacyHet && hetTotal === 1) {
        html += `<p style="color:var(--text-secondary); font-size:0.9em;"><i class="fas fa-circle-info"></i>
            With ${numCores} cores and ${numCores} strategies there is only one possible mixture (each strategy
            is used once), so there is nothing to rank against. Add strategies or lower the core count to
            compare mixtures.</p>`;
    }

    // Two charts per row on a normal screen. Chart.js sizes a responsive canvas from its parent,
    // which must be position:relative and contain only the canvas; `min-width:0` lets a grid cell
    // shrink below the canvas's previous width. Without these the charts rendered small.
    const nBars = simEntries(sim, 'avg').length + (simBaselineEntry(sim, 'avg') ? 1 : 0);
    const barHeight = Math.max(300, 34 * nBars + 60);
    html += `
        <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(420px, 1fr)); gap:24px; margin-top:10px;">
            <div style="min-width:0;"><h4>${osSimEsc(osRankTitle(sim))}</h4><div style="position:relative; height:${barHeight}px;"><canvas id="simThroughputChart"></canvas></div></div>
            <div style="min-width:0;"><h4>Throughput &uarr; vs Response &darr;</h4><div style="position:relative; height:320px;"><canvas id="simScatterChart"></canvas></div></div>
            <div style="min-width:0;"><h4>Cooperation &uarr; vs Throughput</h4><div style="position:relative; height:320px;"><canvas id="simCoopChart"></canvas></div></div>
            <div style="min-width:0;"><h4>Normalised metric profile <span style="font-weight:normal; color:var(--text-secondary); font-size:0.8em;">(outer = better)</span></h4><div style="position:relative; height:340px;"><canvas id="simRadarChart"></canvas></div></div>
        </div>`;

    // Per-core timeline(s): traces are tagged with {label = strategy/mixture, workload = dataset}.
    // A layout dropdown picks the strategy/mixture; the "Workload dataset" dropdown above picks the
    // dataset, so the timeline switches per profile (not always Mixed). Falls back to legacy cfg.trace.
    const traceList = (Array.isArray(cfg.traces) && cfg.traces.length)
        ? cfg.traces
        : (cfg.trace ? [cfg.trace] : []);
    const layoutLabels = [...new Set(traceList.map(t => t.label).filter(Boolean))];
    if (traceList.length) {
        let selectHtml = '';
        if (layoutLabels.length > 1) {
            const opts = layoutLabels.map(l => `<option value="${osSimEsc(l)}">${osSimEsc(l)}</option>`).join('');
            selectHtml = `<label style="font-size:0.85em; color:var(--text-secondary); margin-right:8px;">Show layout:</label>
                <select id="simTraceSelect" style="padding:6px; margin-bottom:10px;">${opts}</select>`;
        }
        html += `
        <div style="margin-top:24px;">
            <h4 style="margin-bottom:6px;">Per-core timeline <span style="font-weight:normal; color:var(--text-secondary); font-size:0.85em;">(filled = core busy that tick)</span></h4>
            ${selectHtml}
            <div id="simGanttCaption" style="font-size:0.82em; color:var(--text-secondary); margin-bottom:6px;"></div>
            <div id="simGantt" style="margin-top:8px;"></div>
        </div>`;
    }

    html += `</div>`;
    container.innerHTML = html;

    // Draw after the canvases exist in the DOM.
    setTimeout(() => {
        const tableEl = container.querySelector('#simMetricsTable');
        const captionEl = container.querySelector('#simProfileCaption');
        const profSel = container.querySelector('#simProfileSelect');
        const ganttEl = container.querySelector('#simGantt');
        const ganttCap = container.querySelector('#simGanttCaption');
        const layoutSel = container.querySelector('#simTraceSelect');
        let currentProfile = 'avg';

        // Pick the trace for a (profile, layout): the 'avg' view has no single dataset, so the
        // timeline falls back to Mixed as a representative strip.
        const pickTrace = (profile, label) => {
            const wl = (profile && profile !== 'avg') ? profile : 'Mixed';
            return traceList.find(t => t.workload === wl && (!label || t.label === label))
                || traceList.find(t => t.workload === wl)
                || traceList.find(t => !label || t.label === label)
                || traceList[0];
        };
        const drawTimeline = (profile) => {
            if (!ganttEl || !traceList.length) return;
            const label = layoutSel ? layoutSel.value : (layoutLabels[0] || null);
            drawSimGantt(ganttEl, pickTrace(profile, label));
            if (ganttCap) {
                const wl = (profile && profile !== 'avg') ? profile : 'Mixed';
                ganttCap.textContent = profile === 'avg'
                    ? `Timeline workload: ${wl}. The table above averages all four workloads, and an average has no `
                      + `single timeline, so one workload is shown. Pick a workload in "Workload dataset" to see its own.`
                    : `Timeline workload: ${wl} (same as the table).`;
            }
        };

        const renderProfile = (profile) => {
            currentProfile = profile;
            if (tableEl) tableEl.innerHTML = buildSimTable(sim, profile);
            createSimulationComparisonChart(sim, profile);
            if (captionEl) {
                const which = profile === 'avg' ? 'averaged across all four workload profiles' : `the ${profile} workload`;
                captionEl.textContent = `Showing ${which} on the ${schedLabel} scheduler${hasBaseline ? ' (baseline = plain ' + schedLabel + ')' : ''}.`;
            }
            drawTimeline(profile);
        };
        renderProfile('avg');
        if (profSel) profSel.addEventListener('change', () => renderProfile(profSel.value));
        if (layoutSel) layoutSel.addEventListener('change', () => drawTimeline(currentProfile));
    }, 50);
}


/* --------------------------------------------------------------------------
 * Redraw on theme switch. Same reasoning as pd_results_render.js: replay the
 * last draw rather than patch baked-in colors. The Gantt matters as much as the
 * charts here, because its busy cells carry a resolved palette color inline
 * (its idle cells use var(--bg-card) and follow the theme on their own).
 * ------------------------------------------------------------------------ */

let osLastSimChartArgs = null;
const createSimulationComparisonChartInner = createSimulationComparisonChart;

createSimulationComparisonChart = function (sim, profile) {
    osLastSimChartArgs = [sim, profile];
    return createSimulationComparisonChartInner(sim, profile);
};

const osLastGanttArgs = new Map();
const drawSimGanttInner = drawSimGantt;

drawSimGantt = function (el, trace) {
    if (el) osLastGanttArgs.set(el, trace);
    return drawSimGanttInner(el, trace);
};

document.addEventListener('themechange', function () {
    if (osLastSimChartArgs) {
        try { createSimulationComparisonChartInner.apply(null, osLastSimChartArgs); } catch (e) {}
    }
    osLastGanttArgs.forEach(function (trace, el) {
        if (!el.isConnected) {
            osLastGanttArgs.delete(el);
            return;
        }
        try { drawSimGanttInner(el, trace); } catch (e) {}
    });
});
