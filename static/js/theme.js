/* Theme switch for the Systems Research "Signal on Paper" palette.
 *
 * Served from static/ so `script-src 'self'` covers it: no CSP nonce needed
 * (unlike the templates' inline <script> blocks, which do carry one).
 *
 * navbar.html loads this render-blocking as the first thing in <body>, so the
 * data-theme attribute is stamped before any page content paints. Putting it in
 * <head> would be marginally earlier, but navbar.html is the one partial every
 * single template includes, and duplicating this across 16 heads to save a few
 * milliseconds is not a trade worth making.
 *
 * Dark is the default. The group site defaults to light, but this app is a code
 * editor first and students sit in it for hours.
 */
(function () {
    'use strict';

    var STORAGE_KEY = 'ipd-theme';

    function stored() {
        try {
            var v = localStorage.getItem(STORAGE_KEY);
            return (v === 'light' || v === 'dark') ? v : null;
        } catch (e) {
            // Private mode / blocked storage: fall through to the default.
            return null;
        }
    }

    function resolve() {
        // Dark unless the user has explicitly chosen otherwise *in this app*.
        // Deliberately does not consult prefers-color-scheme: most desktops
        // report light, and honoring it would make light the effective default
        // for nearly everyone, which is the opposite of what's wanted for a
        // tool people sit in and write code in for an hour at a time.
        return stored() || 'dark';
    }

    function apply(theme, announce) {
        var root = document.documentElement;
        if (theme === 'light') {
            root.setAttribute('data-theme', 'light');
        } else {
            root.removeAttribute('data-theme');  // :root defaults to dark
        }
        root.style.colorScheme = theme;
        if (announce) {
            // Chart.js caches its defaults, so they have to be refreshed BEFORE
            // the event goes out: the renderers' listeners were registered in
            // <head>, ahead of anything this file could register, and would
            // otherwise replay their draws against the previous theme's values.
            srApplyChartDefaults();
            // Charts read their series colors from CSS custom properties at
            // render time, so anything already drawn keeps the old palette until
            // it is re-rendered. Listeners on this event redraw live instances.
            document.dispatchEvent(new CustomEvent('themechange', { detail: { theme: theme } }));
        }
    }

    // Runs immediately, before first paint of the body.
    var current = resolve();
    apply(current, false);

    function syncButton(btn, theme) {
        var icon = btn.querySelector('i');
        if (icon) {
            icon.className = theme === 'light' ? 'fas fa-moon' : 'fas fa-sun';
        }
        btn.setAttribute('aria-pressed', theme === 'light' ? 'true' : 'false');
        btn.setAttribute('aria-label',
            theme === 'light' ? 'Switch to dark theme' : 'Switch to light theme');
    }

    document.addEventListener('DOMContentLoaded', function () {
        var btn = document.getElementById('themeToggle');
        if (!btn) return;
        syncButton(btn, current);
        btn.addEventListener('click', function () {
            current = (current === 'light') ? 'dark' : 'light';
            try {
                localStorage.setItem(STORAGE_KEY, current);
            } catch (e) {
                // Preference just won't persist; the flip still applies.
            }
            apply(current, true);
            syncButton(btn, current);
        });
    });
})();

/* --------------------------------------------------------------------------
 * Theme-aware color lookup, used by the Chart.js renderers.
 *
 * Global rather than module-scoped because os_sim_render.js and
 * pd_results_render.js are plain classic scripts loaded after this one, and
 * neither has a bundler to import from.
 * ------------------------------------------------------------------------ */

function srToken(name, fallback) {
    try {
        var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
        return v || fallback;
    } catch (e) {
        return fallback;
    }
}

function srPalette() {
    return [
        srToken('--chart-1', '#2dd4bf'), srToken('--chart-2', '#4ade80'),
        srToken('--chart-3', '#fbbf24'), srToken('--chart-4', '#fb7185'),
        srToken('--chart-5', '#a78bfa'), srToken('--chart-6', '#38bdf8'),
        srToken('--chart-7', '#f472b6'), srToken('--chart-8', '#fb923c')
    ];
}

/* Chart.js needs a concrete color string, so translucent fills are built with color-mix rather
   than the old trick of appending '22' to a hex literal, which breaks the moment a token stops
   being 6-digit hex. */
function srAlpha(color, pct) {
    return 'color-mix(in srgb, ' + color + ' ' + pct + '%, transparent)';
}

/* --------------------------------------------------------------------------
 * Chart.js global defaults.
 *
 * Chart.js ships a fixed dark grey for tick labels, legends and gridlines,
 * which is low-contrast on the dark theme and doesn't move when the theme does.
 * Setting the defaults here covers every chart in both renderers without
 * repeating a `scales`/`plugins` color block in each chart definition.
 *
 * Safe to run at this point: navbar.html loads theme.js inside <body>, after
 * the Chart.js <script> in each template's <head>, so Chart is already defined.
 * Guarded anyway for the pages that don't load Chart.js at all (login, signup).
 * ------------------------------------------------------------------------ */
function srApplyChartDefaults() {
    if (typeof Chart === 'undefined') return;
    Chart.defaults.color = srToken('--color-ink-muted', '#a3a6ad');
    Chart.defaults.borderColor = srToken('--color-hairline', '#2a2c33');
    Chart.defaults.font.family = srToken('--font-sans', 'Inter, system-ui, sans-serif');
    Chart.defaults.font.size = 12;
}

// Applied once at load, and again from apply() on every switch (see above).
srApplyChartDefaults();
