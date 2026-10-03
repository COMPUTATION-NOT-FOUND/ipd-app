# Security tests — sandbox hardening

This directory holds the regression tests for the **sandbox that runs untrusted student
strategy code** in this (local student) app. There is no authentication, Firebase, or
session layer to test here — `auth_utils.py`, `audit_utils.py` and `firebase_config.py` are
intentional no-op stubs in `ipd-app` (all of that lives in the separate website repo,
`ipd-hub`). The only thing worth penetration-testing in this repo is `is_safe_code()` and the
runtime guards around strategy execution.

## What's covered

**`test_sandbox_hardening.py`** (the only test file here) exercises the two in-process
static defenses in `is_safe_code()` (app.py). These exist because the PythonAnywhere free
tier can't spawn a subprocess for OS-level isolation, so the screen has to catch these
patterns statically:

- **String-based attribute traversal** — `str.format` / `str.format_map` and
  `string.Formatter` field syntax (`"{0.__class__.__init__.__globals__}".format(obj)`) can
  hide dunder names inside *string literals*, where the AST attribute check can't see them.
  These method-call forms are rejected; f-strings and ordinary (non-dunder) attribute access
  stay allowed.
- **Statically-detectable memory bombs** — huge integer/string literals, oversized sequence
  multiplication (`[0] * 10**9`), digit-exploding exponentiation (`10**10**8`), and
  `int()` / `range()` on a huge constant. Benign arithmetic such as a bare `2**32` scale
  factor or `[0] * 100` must still pass (there are explicit regression cases for real
  strategies that use these).

Each test follows the convention: **it PASSES when the dangerous construct is rejected and
benign code is still allowed.**

## Related defenses (tested elsewhere / by inspection)

- The runtime guards — `_guarded_getattr/_setattr/_delattr/_hasattr`, the restricted
  `_SANDBOX_BUILTINS`, the `__import__` allow-list, and the `run_with_limit` instruction
  cap — live in `app.py` / `core_simulation.py` / `n_player_simulation.py`.
- The optional **isolated run mode** (worker-process execution with a wall-clock timeout
  that flags strategies which hang or crash) is covered by `tests/security/test_isolation.py`.

## Running

```bash
RUN_TOURNAMENTS_SYNC=true FLASK_ENV=development FLASK_SECRET_KEY=test \
    python -m pytest tests/security -q
```
