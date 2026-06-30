/*
 * CSP event delegation (one-last-run security pass, item 3).
 *
 * The Content-Security-Policy no longer allows 'unsafe-inline' for script-src, so inline event
 * handler attributes (onclick="fn(...)", oninput=, onsubmit=, ...) can no longer execute. Those
 * attributes were replaced with declarative data-* hooks; this single delegated dispatcher reads
 * them and calls the (still globally-defined) handler functions. Loading this as an external file
 * keeps it out of the nonce flow — it's authorized by script-src 'self'.
 *
 * Markup contract on the triggering element:
 *   data-action="fnName"        global function to invoke (required)
 *   data-on="click|input|change|submit"   event type (default: "click")
 *   data-arg="..."              first positional argument (optional, string)
 *   data-arg2="..."             second positional argument (optional, string)
 *   data-arg3="..."             third positional argument (optional, string)
 *   data-pass-event             append the DOM event as the next argument
 *   data-pass-this              append the element itself as the next argument
 *
 * Examples:
 *   <button data-action="viewTournament" data-arg="abc123">          -> viewTournament("abc123")
 *   <form data-action="createBot" data-on="submit" data-pass-event>  -> createBot(event)
 *   <button data-action="copyToClipboard" data-arg="x" data-pass-this> -> copyToClipboard("x", el)
 *   <input data-action="updateWeights" data-on="input">              -> updateWeights()
 */
(function () {
    'use strict';

    function dispatch(event, type) {
        var el = event.target.closest ? event.target.closest('[data-action]') : null;
        if (!el) return;
        if ((el.dataset.on || 'click') !== type) return;
        var fn = window[el.dataset.action];
        if (typeof fn !== 'function') return;

        var args = [];
        if (el.dataset.arg !== undefined) args.push(el.dataset.arg);
        if (el.dataset.arg2 !== undefined) args.push(el.dataset.arg2);
        if (el.dataset.arg3 !== undefined) args.push(el.dataset.arg3);
        if (el.dataset.passEvent !== undefined) args.push(event);
        if (el.dataset.passThis !== undefined) args.push(el);

        fn.apply(el, args);
    }

    ['click', 'input', 'change', 'submit'].forEach(function (type) {
        document.addEventListener(type, function (event) {
            dispatch(event, type);
        }, false);
    });
})();
