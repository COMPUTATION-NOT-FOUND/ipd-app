/*
 * Elimination bracket view, shared by the local app and the website (keep the two copies the
 * same). One column per round, one card per group. Players who move on are green, wildcards
 * carry a "WC" tag, disqualified players are struck through.
 *
 * Needs ranking.js (rankEsc). Input: the `rounds` list from an elimination result:
 * [{round, players, advance, final, groups: [{group_index, players: [{name, place, advanced,
 *   wildcard, disqualified, reason, ...numbers}]}]}]
 */

// opts.score(player) -> string shown on the right; opts.scoreLabel -> its column title.
function eliminationBracketHtml(rounds, opts) {
    if (!Array.isArray(rounds) || !rounds.length) return '';
    opts = opts || {};
    const score = opts.score || (() => '');
    const label = opts.scoreLabel || '';
    const esc = typeof rankEsc === 'function' ? rankEsc : (s => String(s ?? ''));

    const card = (g, final) => {
        const rows = (g.players || []).map(p => {
            if (p.disqualified) {
                return `<div class="elim-row elim-dq" title="${esc(p.reason || 'disqualified')}">
                    <span class="elim-place">DQ</span><span class="elim-name">${esc(p.name)}</span><span class="elim-score"></span></div>`;
            }
            const cls = p.advanced || (final && p.place === 1) ? ' elim-up' : '';
            const wc = p.wildcard ? ' <span class="elim-wc" title="Wildcard: one of the best next-placed players across groups">WC</span>' : '';
            return `<div class="elim-row${cls}">
                <span class="elim-place">${esc(p.place)}</span><span class="elim-name">${esc(p.name)}${wc}</span>
                <span class="elim-score">${esc(score(p))}</span></div>`;
        }).join('');
        const title = final ? 'Final' : `Group ${Number(g.group_index) + 1}`;
        return `<div class="elim-card"><div class="elim-card-title">${title}</div>${rows}</div>`;
    };

    const cols = rounds.map(rd => {
        const head = rd.final ? `Round ${esc(rd.round)}: final`
            : `Round ${esc(rd.round)}: ${esc(rd.players)} in, ${esc(rd.advance)} move on`;
        return `<div class="elim-col"><div class="elim-col-head">${head}</div>
            ${(rd.groups || []).map(g => card(g, rd.final)).join('')}</div>`;
    }).join('');

    return `<div class="elim-bracket-wrap">
        <style>
            .elim-bracket { display: flex; gap: 16px; overflow-x: auto; padding-bottom: 8px; align-items: flex-start; }
            .elim-col { flex: 0 0 240px; display: flex; flex-direction: column; gap: 10px; }
            .elim-col-head { font-size: 0.8em; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase;
                             color: var(--text-secondary); padding: 6px 10px; border: 1px solid var(--border-color); border-radius: 6px; }
            .elim-card { border: 1px solid var(--border-color); border-radius: 6px; background: var(--bg-card); overflow: hidden; }
            .elim-card-title { font-size: 0.75em; color: var(--text-secondary); padding: 4px 10px; border-bottom: 1px solid var(--border-color); }
            .elim-row { display: grid; grid-template-columns: 28px 1fr auto; gap: 6px; align-items: center;
                        padding: 6px 10px; border-bottom: 1px solid var(--border-color); font-size: 0.9em; }
            .elim-row:last-child { border-bottom: 0; }
            .elim-place { color: var(--text-secondary); font-size: 0.8em; }
            .elim-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
            .elim-score { font-variant-numeric: tabular-nums; }
            .elim-up .elim-name, .elim-up .elim-score { color: var(--success-color); font-weight: 700; }
            .elim-dq .elim-name { text-decoration: line-through; color: var(--danger-color, #dc3545); }
            .elim-wc { font-size: 0.7em; padding: 0 4px; border: 1px solid currentColor; border-radius: 3px; margin-left: 4px; }
        </style>
        ${label ? `<p style="margin: 0 0 8px; color: var(--text-secondary); font-size: 0.9em;">Number on the right: ${esc(label)}. Green = moved on.</p>` : ''}
        <div class="elim-bracket">${cols}</div></div>`;
}

// Plain one-line summary, e.g. "Elimination: 65 strategies, groups of 5, 5 rounds (13 -> 7 -> 4 -> 2 -> 1 groups)".
function eliminationSummary(rounds, groupSize, noun) {
    if (!Array.isArray(rounds) || !rounds.length) return '';
    const counts = rounds.map(r => (r.groups || []).length).join(' → ');
    return `Elimination: ${rounds[0].players} ${noun || 'strategies'}, groups of ${groupSize}, `
        + `${rounds.length} ${rounds.length === 1 ? 'round' : 'rounds'} (${counts} groups).`;
}
