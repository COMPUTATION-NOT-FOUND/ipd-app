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

// Build a uniform list of entries for a given `profile` ('avg' or a workload name) from either
// the heterogeneous combinations or the homogeneous per-strategy results.
function simEntries(sim, profile) {
    profile = profile || 'avg';
    const useProf = profile !== 'avg';
    const het = sim.core_simulation_heterogeneous;
    if (het && Array.isArray(het.results) && het.results.length) {
        return het.results.map(r => {
            const src = (useProf && r.workloads && r.workloads[profile]) ? r.workloads[profile] : r;
            return Object.assign({ label: Object.keys(r.combination || {}).join('+') }, osPickMetrics(src));
        });
    }
    const homo = sim.core_simulation_results;
    if (homo && homo.strategies) {
        return Object.entries(homo.strategies).map(([name, d]) => {
            const src = (useProf && d.workloads && d.workloads[profile]) ? d.workloads[profile] : (d.avg || {});
            return Object.assign({ label: name }, osPickMetrics(src));
        });
    }
    return [];
}

// The baseline (vanilla selected scheduler) entry for a given profile, or null if absent.
function simBaselineEntry(sim, profile) {
    profile = profile || 'avg';
    const b = (sim.core_simulation_results && sim.core_simulation_results.baseline)
        || (sim.core_simulation_heterogeneous && sim.core_simulation_heterogeneous.baseline);
    if (!b) return null;
    const src = (profile !== 'avg' && b.workloads && b.workloads[profile]) ? b.workloads[profile] : (b.avg || {});
    const schedLabel = OS_SIM_SCHEDULER_LABELS[b.scheduler] || b.scheduler || 'scheduler';
    return Object.assign({ label: 'Baseline · plain ' + schedLabel, isBaseline: true }, osPickMetrics(src));
}

const OS_BASELINE_COLOR = '#9ca3af';

function createSimulationComparisonChart(sim, profile) {
    simChartInstances.forEach(c => { try { c.destroy(); } catch (e) {} });
    simChartInstances = [];
    const entries = simEntries(sim, profile).slice().sort((a, b) => b.throughput - a.throughput);
    if (!entries.length || typeof Chart === 'undefined') return;
    const baseline = simBaselineEntry(sim, profile);

    // 1) Throughput bar leaderboard (horizontal) — baseline appended as a grey reference bar.
    const barCtx = document.getElementById('simThroughputChart');
    if (barCtx) {
        const barEntries = baseline ? entries.concat([baseline]) : entries;
        simChartInstances.push(new Chart(barCtx, {
            type: 'bar',
            data: { labels: barEntries.map(e => e.label),
                datasets: [{ label: 'Throughput', data: barEntries.map(e => e.throughput),
                    backgroundColor: barEntries.map(e => e.isBaseline ? OS_BASELINE_COLOR : 'rgba(59,130,246,0.6)') }] },
            options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false,
                plugins: { legend: { display: false } } }
        }));
    }

    // 2) Throughput (y, higher better) vs Response (x, lower better) scatter + baseline marker.
    const scCtx = document.getElementById('simScatterChart');
    if (scCtx) {
        const datasets = [{ label: 'mixtures / strategies',
            data: entries.map(e => ({ x: e.response, y: e.throughput, label: e.label })),
            backgroundColor: 'rgba(16,185,129,0.7)' }];
        if (baseline) datasets.push({ label: 'baseline', pointStyle: 'rectRot', pointRadius: 8,
            data: [{ x: baseline.response, y: baseline.throughput, label: baseline.label }],
            backgroundColor: OS_BASELINE_COLOR });
        simChartInstances.push(new Chart(scCtx, {
            type: 'scatter', data: { datasets },
            options: { responsive: true, maintainAspectRatio: false,
                scales: { x: { title: { display: true, text: 'Avg Response (lower better)' } },
                          y: { title: { display: true, text: 'Throughput (higher better)' } } },
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
            backgroundColor: '#3b82f6' }] },
        options: { responsive: true, maintainAspectRatio: false,
            scales: { x: { title: { display: true, text: 'Throughput (higher better)' } },
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
        const palette = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899'];
        const top = entries.slice(0, 5);
        const datasets = top.map((e, i) => ({ label: e.label,
            data: axes.map(k => scale(k, e[k])),
            borderColor: palette[i % palette.length],
            backgroundColor: palette[i % palette.length] + '22' }));
        if (baseline) datasets.push({ label: baseline.label, data: axes.map(k => scale(k, baseline[k])),
            borderColor: OS_BASELINE_COLOR, borderDash: [5, 4], backgroundColor: 'transparent' });
        simChartInstances.push(new Chart(rdCtx, {
            type: 'radar',
            data: { labels: ['Throughput', 'Wait', 'Turnaround', 'Response', 'Cache'], datasets },
            options: { responsive: true, maintainAspectRatio: false,
                scales: { r: { min: 0, max: 1, ticks: { display: false } } } }
        }));
    }
}

const OS_GANTT_PALETTE = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#14b8a6', '#f97316'];
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
        const color = OS_GANTT_PALETTE[c % OS_GANTT_PALETTE.length];
        legend += `<span style="display:inline-flex; align-items:center; gap:5px;">
            <span style="display:inline-block; width:11px; height:11px; background:${color}; border-radius:2px;"></span>
            Core ${c}: ${osSimEsc(cores[c] || ('Core ' + c))}</span>`;
    }
    legend += '<span style="display:inline-flex; align-items:center; gap:5px;"><span style="display:inline-block; width:11px; height:11px; background:var(--bg-card); border:1px solid var(--border-color); border-radius:2px;"></span>idle</span>';
    legend += '</div>';

    let html = legend + '<div style="overflow-x:auto;">';
    for (let core = 0; core < nCores; core++) {
        const color = OS_GANTT_PALETTE[core % OS_GANTT_PALETTE.length];
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
    // Per-strategy (homogeneous): rank by throughput descending so the table matches the
    // throughput leaderboard bar and the rank-1 highlight marks the actual top performer.
    // Heterogeneous: keep the server's ranked order — its row index aligns with hetResults[idx]
    // (the combination shown per row), so re-sorting would mismatch the mixture labels.
    const rawEntries = simEntries(sim, profile);
    const entries = isHet ? rawEntries : rawEntries.slice().sort((a, b) => b.throughput - a.throughput);
    const baseline = simBaselineEntry(sim, profile);
    const down = '<i class="fas fa-arrow-down" style="font-size:0.7em"></i>';
    const up = '<i class="fas fa-arrow-up" style="font-size:0.7em"></i>';

    let html = `<table class="leaderboard-table"><thead><tr>
        <th>${isHet ? '#' : ''}</th><th>${isHet ? 'Mixture' : 'Strategy'}</th>
        <th>Throughput ${up}</th><th>Wait ${down}</th><th>Turnaround ${down}</th>
        <th>Response ${down}</th><th>Makespan ${down}</th><th>Cache Misses ${down}</th>
        <th>Coop % ${up}</th></tr></thead><tbody>`;

    const hetResults = isHet ? sim.core_simulation_heterogeneous.results : [];
    entries.forEach((e, idx) => {
        let nameCell;
        if (isHet) {
            const combo = (hetResults[idx] && hetResults[idx].combination) || {};
            nameCell = Object.keys(combo)
                .map(n => `<span class="badge" style="background:var(--bg-card); border:1px solid var(--border-color); margin:1px;">${osSimEsc(n)}</span>`).join(' ');
        } else {
            nameCell = `<strong>${osSimEsc(e.label)}</strong>`;
        }
        html += `<tr class="${idx === 0 ? 'rank-1' : ''}">
            <td>${isHet ? '<strong>' + (idx + 1) + '</strong>' : ''}</td>
            <td>${nameCell}</td>
            <td><strong style="color:var(--primary-muted);">${e.throughput.toFixed(2)}</strong></td>
            <td>${e.wait.toFixed(2)}</td>
            <td>${e.turnaround.toFixed(2)}</td>
            <td>${e.response.toFixed(2)}</td>
            <td>${e.makespan.toFixed(2)}</td>
            <td>${e.cache.toFixed(1)}</td>
            <td>${e.coop.toFixed(1)}%</td>
        </tr>`;
    });
    if (baseline) {
        html += `<tr style="border-top:2px solid var(--border-color); color:var(--text-secondary);">
            <td></td><td><em>${osSimEsc(baseline.label)}</em></td>
            <td><strong>${baseline.throughput.toFixed(2)}</strong></td>
            <td>${baseline.wait.toFixed(2)}</td>
            <td>${baseline.turnaround.toFixed(2)}</td>
            <td>${baseline.response.toFixed(2)}</td>
            <td>${baseline.makespan.toFixed(2)}</td>
            <td>${baseline.cache.toFixed(1)}</td>
            <td>&mdash;</td>
        </tr>`;
    }
    html += `</tbody></table>`;
    if (isHet) html += `<p style="color:var(--text-secondary); font-size:0.85em; margin-top:8px;">Each mixture uses each strategy at most once. The top row is the best-performing combination found.</p>`;
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
    const hasHetSim = !!(hetSim && Array.isArray(hetSim.results) && hetSim.results.length);
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
            <span><span class="badge" style="background-color:${isHet ? 'var(--warning-color)' : 'var(--info-color)'};">${isHet ? 'HETEROGENEOUS' : 'HOMOGENEOUS'}</span></span>
            <span><strong>Scheduler:</strong> ${osSimEsc(schedLabel)}</span>
            <span><strong>Cores:</strong> ${numCores}</span>
            ${isHet ? `<span><strong>Combinations:</strong> ${hetSim.evaluated} (all)</span>` : ''}
        </div>`;
    }

    // Workload-profile selector: switch the table + charts between the four datasets and the
    // averaged view, so it's clear how the cores did on each dataset.
    const profileOpts = ['avg'].concat(OS_WORKLOAD_PROFILES)
        .map(p => `<option value="${p}">${p === 'avg' ? 'Average (all profiles)' : osSimEsc(p)}</option>`).join('');
    const hasBaseline = !!simBaselineEntry(sim, 'avg');
    html += `<div style="margin-bottom: 18px;">
        <h4 style="border-bottom:1px solid var(--border-color); padding-bottom:8px;">${isHet ? 'Ranked Mixtures' : 'Per-Strategy Metrics'}</h4>
        <div style="margin:10px 0;">
            <label style="font-size:0.85em; color:var(--text-secondary); margin-right:8px;">Workload dataset:</label>
            <select id="simProfileSelect" style="padding:6px;">${profileOpts}</select>
            <span id="simProfileCaption" style="font-size:0.82em; color:var(--text-secondary); margin-left:10px;"></span>
        </div>
        <div id="simMetricsTable"></div>
    </div>`;

    html += `
        <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(320px, 1fr)); gap:20px; margin-top:10px;">
            <div><h4>Throughput</h4><div style="height:300px;"><canvas id="simThroughputChart"></canvas></div></div>
            <div><h4>Throughput &uarr; vs Response &darr;</h4><div style="height:300px;"><canvas id="simScatterChart"></canvas></div></div>
            <div><h4>Cooperation &uarr; vs Throughput</h4><div style="height:300px;"><canvas id="simCoopChart"></canvas></div></div>
            <div style="grid-column:1/-1;"><h4>Normalised metric profile</h4><div style="height:340px;"><canvas id="simRadarChart"></canvas></div></div>
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
                    ? `Workload: ${wl} (representative — pick a dataset above to match the timeline to the metrics).`
                    : `Workload: ${wl}.`;
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
