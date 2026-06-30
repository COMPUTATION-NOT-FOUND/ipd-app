/*
 * Shared Prisoner's-Dilemma tournament results renderer (1v1 + N-Player).
 *
 * Given a tournament object ({ leaderboard, matches, modes, weights, payoff_matrix }) it builds a
 * chart grid into a container and draws four visualizations, mirroring the OS-sim view so all three
 * modes look consistent:
 *   1. Weighted-score bar leaderboard          (analog of the OS throughput bar)
 *   2. Avg-Points vs Cooperation% scatter       (same axes idea as the OS cooperation scatter)
 *   3. Normalised metric radar (Win/Points/Coop, outer = better)
 *   4. Points-per-game-mode grouped bar
 * plus a head-to-head win matrix when pairwise (1v1) match data is present.
 * Requires Chart.js.
 */

function pdEsc(unsafe) {
    if (unsafe === null || unsafe === undefined) return '';
    return String(unsafe)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

// Chart.js instances tracked per container element. Keyed by container so the 1v1 and N-Player
// result panels (which reuse the same canvas IDs) don't destroy or collide with each other.
const pdChartInstancesByContainer = new WeakMap();
const PD_PALETTE = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#14b8a6', '#f97316'];

// Compute the per-strategy weighted-score breakdown from the stored leaderboard, mirroring the
// formula used on the results page (win_rate*Wwin + cooperation*Wcoop + points*Wpoints) * 100.
function pdComputeScores(tournament) {
    const leaderboard = Array.isArray(tournament.leaderboard) ? tournament.leaderboard : [];
    const w = tournament.weights || {};
    const wWin = w.win_rate ?? 0.33, wCoop = w.cooperation ?? 0.34, wPts = w.points ?? 0.33;
    let maxPayoff = 5;
    if (tournament.payoff_matrix) {
        let maxVal = 0;
        Object.values(tournament.payoff_matrix).forEach(pair => {
            const v = Array.isArray(pair) ? pair[0] : pair;
            if (typeof v === 'number' && v > maxVal) maxVal = v;
        });
        if (maxVal > 0) maxPayoff = maxVal;
    }
    const scored = leaderboard.map(e => {
        const totalGames = (e.wins || 0) + (e.draws || 0) + (e.losses || 0);
        const winRateScore = totalGames > 0 ? (e.wins || 0) / totalGames : 0;
        const coopScore = (e.norm_cooperation_percentage !== undefined
            ? e.norm_cooperation_percentage : (e.cooperation_percentage || 0)) / 100;
        let avgPPR = 0;
        if (e.total_raw_points !== undefined && e.total_moves > 0) avgPPR = e.total_raw_points / e.total_moves;
        else avgPPR = totalGames > 0 ? e.total_points / totalGames : 0;
        const pointsScore = maxPayoff > 0 ? Math.min(1, avgPPR / maxPayoff) : 0;
        const weightedScore = (winRateScore * wWin + coopScore * wCoop + pointsScore * wPts) * 100;
        return Object.assign({}, e, {
            weightedScore, avgPPR, winRateScore, pointsScore,
            coopScore: Math.min(1, coopScore),
            coopPct: e.cooperation_percentage != null ? e.cooperation_percentage : coopScore * 100,
        });
    }).sort((a, b) => b.weightedScore - a.weightedScore);
    return { scored, weights: { wWin, wCoop, wPts } };
}

// Build the head-to-head matrix HTML (row strategy vs column strategy) from pairwise matches.
function pdBuildH2H(el, names, matches) {
    if (!el) return;
    // Index results by ordered pair.
    const cell = {};  // cell["A|B"] = 'W'|'L'|'D' from A's perspective
    matches.forEach(m => {
        const a = m.player_a, b = m.player_b;
        if (!a || !b) return;
        let ra, rb;
        if (m.winner === 'A' || m.winner === a) { ra = 'W'; rb = 'L'; }
        else if (m.winner === 'B' || m.winner === b) { ra = 'L'; rb = 'W'; }
        else { ra = 'D'; rb = 'D'; }
        cell[a + '|' + b] = ra;
        cell[b + '|' + a] = rb;
    });
    const colour = { W: 'rgba(16,185,129,0.55)', L: 'rgba(239,68,68,0.45)', D: 'rgba(148,163,184,0.35)' };
    let html = '<table class="leaderboard-table" style="font-size:0.85em;"><thead><tr><th></th>';
    names.forEach(n => { html += `<th title="${pdEsc(n)}" style="max-width:90px; overflow:hidden; text-overflow:ellipsis;">${pdEsc(n)}</th>`; });
    html += '</tr></thead><tbody>';
    names.forEach(rowN => {
        html += `<tr><td style="font-weight:bold; white-space:nowrap;">${pdEsc(rowN)}</td>`;
        names.forEach(colN => {
            if (rowN === colN) { html += '<td style="text-align:center; color:var(--text-secondary);">&mdash;</td>'; return; }
            const r = cell[rowN + '|' + colN];
            html += `<td style="text-align:center; background:${r ? colour[r] : 'transparent'};">${r || ''}</td>`;
        });
        html += '</tr>';
    });
    html += '</tbody></table><p style="color:var(--text-secondary); font-size:0.8em; margin-top:6px;">Each cell is the row strategy\'s result against the column strategy (W/L/D).</p>';
    el.innerHTML = html;
}

function pdDrawCharts(container, scored, modes, matches) {
    if (!container) return;
    // Destroy only THIS container's previous charts, then look canvases up *within* the container
    // (not document-wide) so reused IDs across the 1v1 / N-Player panels resolve correctly.
    const prev = pdChartInstancesByContainer.get(container) || [];
    prev.forEach(c => { try { c.destroy(); } catch (e) {} });
    let pdChartInstances = [];
    pdChartInstancesByContainer.set(container, pdChartInstances);
    if (typeof Chart === 'undefined' || !scored.length) return;

    // 1) Weighted-score bar (horizontal)
    const barCtx = container.querySelector('#pdScoreBar');
    if (barCtx) pdChartInstances.push(new Chart(barCtx, {
        type: 'bar',
        data: { labels: scored.map(e => e.name),
            datasets: [{ label: 'Weighted Score', data: scored.map(e => e.weightedScore),
                backgroundColor: 'rgba(59,130,246,0.6)' }] },
        options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false,
            plugins: { legend: { display: false } } }
    }));

    // 2) Avg Points (x) vs Cooperation % (y) scatter — same axes idea as the OS coop scatter.
    const scCtx = container.querySelector('#pdScatter');
    if (scCtx) pdChartInstances.push(new Chart(scCtx, {
        type: 'scatter',
        data: { datasets: [{ label: 'strategies',
            data: scored.map(e => ({ x: e.avgPPR, y: e.coopPct, label: e.name })),
            backgroundColor: '#3b82f6' }] },
        options: { responsive: true, maintainAspectRatio: false,
            scales: { x: { title: { display: true, text: 'Avg Points (higher better)' } },
                      y: { min: 0, max: 100, title: { display: true, text: 'Cooperation %' } } },
            plugins: { legend: { display: false },
                tooltip: { callbacks: { label: (c) => `${c.raw.label}: pts ${c.raw.x.toFixed(2)}, coop ${c.raw.y.toFixed(1)}%` } } } }
    }));

    // 3) Normalised metric radar (Win rate / Points / Cooperation; already 0..1, outer = better).
    const rdCtx = container.querySelector('#pdRadar');
    if (rdCtx) {
        const top = scored.slice(0, 5);
        pdChartInstances.push(new Chart(rdCtx, {
            type: 'radar',
            data: { labels: ['Win rate', 'Points', 'Cooperation'],
                datasets: top.map((e, i) => ({ label: e.name,
                    data: [e.winRateScore, e.pointsScore, e.coopScore],
                    borderColor: PD_PALETTE[i % PD_PALETTE.length],
                    backgroundColor: PD_PALETTE[i % PD_PALETTE.length] + '22' })) },
            options: { responsive: true, maintainAspectRatio: false,
                scales: { r: { min: 0, max: 1, ticks: { display: false } } } }
        }));
    }

    // 4) Points-per-game-mode grouped bar (labels = strategies, one dataset per mode).
    const modeCtx = container.querySelector('#pdModeBar');
    if (modeCtx && modes.length) {
        const top = scored.slice(0, 8);
        pdChartInstances.push(new Chart(modeCtx, {
            type: 'bar',
            data: { labels: top.map(e => e.name),
                datasets: modes.map((m, i) => ({ label: m.charAt(0).toUpperCase() + m.slice(1),
                    data: top.map(e => (e.mode_points && e.mode_points[m]) || 0),
                    backgroundColor: PD_PALETTE[i % PD_PALETTE.length] })) },
            options: { responsive: true, maintainAspectRatio: false,
                scales: { y: { title: { display: true, text: 'Avg points / round' } } } }
        }));
    }

    // Head-to-head matrix (1v1 only).
    const pairwise = (matches || []).filter(m => m && m.player_a && m.player_b);
    if (pairwise.length) pdBuildH2H(container.querySelector('#pdH2H'), scored.map(e => e.name), pairwise);
}

// Public entry point. Injects the chart grid into `container` and draws everything.
function renderPDResults(container, tournament) {
    if (typeof container === 'string') container = document.getElementById(container);
    if (!container || !tournament) return;
    const { scored } = pdComputeScores(tournament);
    if (!scored.length) { container.innerHTML = ''; return; }
    let modes = Array.isArray(tournament.modes) ? tournament.modes
        : (tournament.tournament_info && tournament.tournament_info.modes) || [];
    if (!Array.isArray(modes)) modes = [];
    const matches = Array.isArray(tournament.matches) ? tournament.matches : [];
    const pairwise = matches.filter(m => m && m.player_a && m.player_b);

    let html = `<div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(320px, 1fr)); gap:20px; margin-top:10px;">
        <div><h4>Weighted score</h4><div style="height:300px;"><canvas id="pdScoreBar"></canvas></div></div>
        <div><h4>Avg Points &uarr; vs Cooperation</h4><div style="height:300px;"><canvas id="pdScatter"></canvas></div></div>
        <div><h4>Normalised profile <span style="font-weight:normal; color:var(--text-secondary); font-size:0.8em;">(outer = better)</span></h4><div style="height:320px;"><canvas id="pdRadar"></canvas></div></div>`;
    if (modes.length) html += `<div><h4>Points per game mode</h4><div style="height:300px;"><canvas id="pdModeBar"></canvas></div></div>`;
    html += `</div>`;
    if (pairwise.length) html += `<div style="margin-top:24px;"><h4>Head-to-head <span style="font-weight:normal; color:var(--text-secondary); font-size:0.85em;">(row vs column)</span></h4><div id="pdH2H" style="overflow-x:auto; margin-top:8px;"></div></div>`;
    container.innerHTML = html;

    setTimeout(() => pdDrawCharts(container, scored, modes, matches), 30);
}
