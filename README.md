# Prisoner's Dilemma — Practice App

Welcome! This is the app you run **on your own computer** to write Prisoner's Dilemma (PD)
strategies, test them, and enter them into the class competition. Everything runs locally on your
machine — there are **no accounts and no setup beyond installing it**.

You will:
1. **Write** a strategy (a short Python function).
2. **Practice** it — against your own ideas and against strategies your classmates have submitted.
3. **Submit** your best one to the class website to compete.

---

## 1. Set it up (one time)

**You need Python 3.10+.** Check with `python --version` (Windows: some installs only have `py --version`;
Linux/macOS: `python3 --version`). If you don't have it, install it from
[python.org](https://www.python.org/downloads/).

We install into a **virtual environment** (a `.venv` folder) so this app's packages stay isolated
from the rest of your system. Pick your OS below.

**Step 1 — get the app** (your instructor gives you the exact URL):
```bash
git clone <repo-url>
cd ipd-app
```

**Step 2 — create & activate the virtual environment, then install:**

<details open><summary><strong>Windows (PowerShell)</strong></summary>

```powershell
python -m venv .venv                # if 'python' isn't found, try: py -m venv .venv
.\.venv\Scripts\Activate.ps1        # your prompt now shows (.venv)
pip install -r requirements.txt
```
> **If `Activate.ps1` is blocked** ("running scripts is disabled on this system"), either run once
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, or — if your account isn't allowed to change
> that (common on university-managed PCs) — skip activation and call the venv's Python directly:
> `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` and later
> `.\.venv\Scripts\python.exe app.py`. In **cmd.exe** you can also activate with
> `.venv\Scripts\activate.bat`.
</details>

<details open><summary><strong>Linux / Ubuntu / macOS</strong></summary>

```bash
python3 -m venv .venv
source .venv/bin/activate          # your prompt now shows (.venv)
pip install -r requirements.txt
```
> On a fresh **Ubuntu/Debian** box you may first need the venv + pip packages:
> `sudo apt update && sudo apt install -y python3-venv python3-pip`
</details>

> **Slow or unreliable internet?** If `pip install` fails with a read timeout, retry with
> `pip install --default-timeout=100 --retries 10 -r requirements.txt`.

**Step 3 — connect to the class website (optional).** The app works fully offline. To see
classmates' strategies and submit your own, copy the example config and add the two values your
instructor gives you:

```bash
cp .env.example .env                # Windows (PowerShell): copy .env.example .env
```
```ini
HUB_BASE_URL=https://<your-class-site>.pythonanywhere.com
HUB_API_TOKEN=<the shared class token>
```
> Keep the token private: it lives only in your `.env` (which git ignores). Without these values the
> app still works — you just won't see the class gallery. You don't need to set anything else.

**Step 4 — run it** (same command on every OS, with the environment activated):
```bash
python app.py
```

Then open **http://127.0.0.1:5000** in your browser. That's it — the app is running. Stop it with
`Ctrl+C` in the terminal.

> The app only listens on your own computer (`127.0.0.1`); other devices on your network can't
> reach it. Each new terminal, re-activate the environment before running:
> `.\.venv\Scripts\Activate.ps1` (Windows) or `source .venv/bin/activate` (Linux/macOS).

---

## 2. Write your first strategy

A strategy is a Python function that, each round, returns **`'C'`** (cooperate) or **`'D'`**
(defect). Here's a complete one — classic **Tit‑for‑Tat** (cooperate first, then copy your
opponent's last move):

```python
def tit_for_tat(last_moves, my_history, opponents_histories, meta):
    if not last_moves:          # first round — nothing to copy yet
        return 'C'
    return last_moves[0]        # copy the opponent's last move
```

What the inputs mean:

| Argument | What it gives you |
|---|---|
| `last_moves` | Each opponent's **last move**, e.g. `['C']` in 1v1 (empty `[]` on round 1). |
| `my_history` | A list of all **your** past moves. |
| `opponents_histories` | A list of each opponent's full move history. |
| `meta` | Extra info: `meta['round']`, `meta['rng']` (a seeded random generator — use this instead of `random` so results are repeatable), `meta['n_players']`, and more. |

**Rules for a valid strategy:**
- Define **one** strategy function with the four parameters above. Its name is up to you.
- The **strategy name** you type in the app is just a display label (it can contain spaces, e.g.
  "Grim Trigger"). It does **not** have to match the function's name, but it must be unique within a run.
- It must **return exactly `'C'` or `'D'`**. Anything else (`1`, `True`, `None`, lowercase `'c'`)
  fails the strategy.
- You may use a few safe modules (e.g. `random`, `math`). File access, networking, and tricks like
  `exec`/`eval`/`open` are blocked by the sandbox.

A strategy that **crashes, loops forever, or returns anything other than `'C'`/`'D'` forfeits** the
match: its opponent gets the win, and the results show which strategy failed and why.

The same function works in **all three game modes** below — you only write it once.

---

## 3. Practice

The app has three modes (tabs). Pick strategies, choose game options, and click run — everything
computes on your CPU, with **no limit** on how many strategies you add (you'll get a quick
time estimate before a big run). N‑Player matches are capped at 1000 rounds.

- **1v1** — round‑robin: every strategy plays every other one, head‑to‑head. Ranked by a
  **Weighted Score** (win rate + cooperation + points; you can re‑weight it live with the sliders).
- **N‑Player** — one big group game where everyone moves at once, under different payoff rules
  (Public Goods, Pairwise Matrix, or K‑Cooperator). Also ranked by Weighted Score.
- **OS Simulation** — a fun twist: your strategy becomes a **CPU scheduler** (`Cooperate` = give up
  the CPU, `Defect` = keep it) and is scored on how well it runs a simulated multi‑core machine.
  The tab has a short glossary of the scheduler terms (FCFS, MLFQ, CFS, …).

**Choosing who competes.** Each run uses:
- **Your local players** — the editable boxes where you write/paste strategies (the quick‑fill
  buttons drop in a ready-made example), and
- **Select Participants** — a checklist of strategies fetched from the class site, split into
  **Players** (classmates' submissions), **Bots** (instructor‑made), **Practice** (instructor
  examples) and **Local** (ones you saved with "Save locally"; the × button deletes one). Tick the
  ones you want to face. Their code is read‑only — you compete against them, you don't edit them.

Press **Refresh Gallery** to pull the latest strategies from the website (it's cached, so it also
works offline after the first fetch). On the **Hub** tab you can import one strategy and press
**Open in Practice** to load it straight into an editor box.

**Run in sandbox mode** (the tick box under each Run button) first test‑runs every strategy in a
separate, time‑limited process and leaves out any that hang or crash, naming each one. Use it when
you run strategies you didn't write.

---

## 4. Submit your strategy to the competition

On the **Hub** tab, use **"Submit your strategy to the website"**:

1. Paste your strategy and click submit.
2. The app **screens and test‑runs it on your machine first** — if it has an illegal import,
   crashes, or returns something other than `'C'`/`'D'`, it tells you why so you can fix it before
   it ever reaches the website.
3. Once it passes, your browser opens the class website with your strategy **pre‑filled** — log in
   there and click **Submit**. You can update it any time before the tournament.

Login and submission happen **on the website**, not in this app. (Strategy names must be unique so
everyone's entries can be told apart.)

---

## Tips & common mistakes

- Forgetting to `return` `'C'` or `'D'` (e.g. returning `1`, `True`, or nothing) → your strategy
  fails and forfeits its matches. Always return one of the two strings.
- On round 1 `last_moves` is empty (`[]`) — handle that case (usually start by cooperating).
- Use `meta['rng']` for randomness, not `import random` + `random.random()`, if you want repeatable
  results.
- "Duplicate strategy name": two entries in the same run share a name — often an editor box and a
  ticked gallery/Local entry. Rename one.

---

## For instructors / admins

- **Run the official tournament locally:** practice in any mode with the real roster, then use
  **Submit results to website (instructors only)** — the app stages the result and opens the
  website's publish page (post now or schedule), where you log in. Your local players are
  registered there as bots. Students see this panel too; it only stages a draft, and nothing is
  published until an instructor logs in on the website and confirms.
- **Manage bots & practice strategies** on the website's admin dashboard.

## For developers

This is the **local** half of a two‑repo system; the website lives in **`ipd-hub`** (login,
submission, results, all the secrets). This app has **no Firebase and no keys** —
`firebase_config.py` / `auth_utils.py` / `audit_utils.py` are no‑op stubs here.

- **Backend:** `app.py` (routes, 1v1 engine, the `is_safe_code` sandbox + `run_with_limit`),
  `n_player_simulation.py` (N‑player engine, plus `validate_move` shared by every engine),
  `payoff_models.py`, `core_simulation.py`, `schedulers.py`, `isolation.py` (sandbox mode),
  `tournament_package.py`, `hub_client.py` (fetches from the website).
- **Frontend:** `templates/{index,hub,_game_info,navbar}.html`,
  `static/js/{pd_results_render,os_sim_render,csp_delegation}.js`, `static/css/style.css`.
- **Run the tests** (install the test tools once with `pip install -r requirements-dev.txt`):

  Linux / macOS (bash):
  ```bash
  RUN_TOURNAMENTS_SYNC=true FLASK_ENV=development FLASK_SECRET_KEY=test python -m pytest -q
  ```
  Windows (PowerShell):
  ```powershell
  $env:RUN_TOURNAMENTS_SYNC='true'; $env:FLASK_ENV='development'; $env:FLASK_SECRET_KEY='test'
  python -m pytest -q
  ```
- `python app.py` serves with Waitress on `127.0.0.1:5000`; set `FLASK_DEBUG=True` to use the
  Flask debug server instead.

> **Scoring:** `Score = (win_rate·W_win + cooperation·W_coop + points·W_points) × 100`, where
> win rate is per‑opponent and points are per‑round, so match length never biases the result. Every
> run is seeded and reproducible. **Safety:** all executed code is screened by `is_safe_code` and
> bounded by an instruction cap; the website re‑screens everything on submit (see
> `ipd-hub/SECURITY.md`). Distribute this app via `git clone` / `git archive`, **never a zip** (a zip
> can drag along a local `.env`).
