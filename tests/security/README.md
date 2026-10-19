# Security tests — sandbox hardening

This directory holds the regression tests for the **sandbox that runs untrusted student
strategy code** in this (local student) app. There is no authentication, Firebase, or
session layer to test here — `auth_utils.py`, `audit_utils.py` and `firebase_config.py` are
intentional no-op stubs in `ipd-app` (all of that lives in the separate website repo,
`ipd-hub`). The only thing worth penetration-testing in this repo is `is_safe_code()` and the
runtime guards around strategy execution.

## What's covered

There are three test files here.

**`test_sandbox_hardening.py`** exercises the two in-process static defenses in
`is_safe_code()` (app.py). These exist because the PythonAnywhere free tier can't spawn a
subprocess for OS-level isolation, so the screen has to catch these patterns statically:

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

**`test_operator_guard.py`** covers the guarded `operator` module. `operator.attrgetter` /
`methodcaller` take attribute names as strings, which the AST screen can't see, so strategies
get a copy of `operator` that applies the guarded-`getattr` rule (no names starting with `_`).
It checks both sandboxes (1v1 / N-Player in `app.py`, the OS sim in `core_simulation.py`), both
import forms, that ordinary `operator` use still works, and that one strategy can't tamper with
another's copy. (Fix contributed by a student security review, Oct 2026.)

**`test_isolation.py`** covers **sandbox mode** ("Run in sandbox mode" on the 1v1, N-Player
and OS Simulation tabs): each strategy is test-run in a worker process with a wall-clock
timeout first, and ones that hang (e.g. catastrophic regex in C) or crash are flagged and left
out.

Each test follows the convention: **it PASSES when the dangerous construct is rejected and
benign code is still allowed.**

## Related defenses (tested elsewhere / by inspection)

- The runtime guards — `_guarded_getattr/_setattr/_delattr/_hasattr`, the restricted
  `_SANDBOX_BUILTINS`, the `__import__` allow-list (`core_simulation.guarded_import`), and the
  `run_with_limit` instruction cap — live in `app.py` / `core_simulation.py` /
  `n_player_simulation.py`.
- Strict move checking (`validate_move`) and forfeits on a crash, infinite loop or bad return
  are covered by `tests/regression/test_strict_moves_and_forfeit.py`.

## Running

Linux / macOS (bash):

```bash
RUN_TOURNAMENTS_SYNC=true FLASK_ENV=development FLASK_SECRET_KEY=test \
    python -m pytest tests/security -q
```

Windows (PowerShell):

```powershell
$env:RUN_TOURNAMENTS_SYNC='true'; $env:FLASK_ENV='development'; $env:FLASK_SECRET_KEY='test'
python -m pytest tests/security -q
```
