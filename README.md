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

**You need Python 3.10+.** Check with `python --version` (or `python3 --version`). If you don't
have it, install it from [python.org](https://www.python.org/downloads/).

```bash
# 1) Get the app (your instructor will give you the exact URL)
git clone <repo-url>
cd ipd-app

# 2) Install the dependencies
pip install -r requirements.txt

# 3) Create your config file
cp .env.example .env            # Windows (PowerShell):  copy .env.example .env

# 4) Run it
python app.py                   # Windows, if that hangs:  waitress-serve app:app
```

Then open **http://127.0.0.1:5000** in your browser. That's it — the app is running.

> Open `.env` in a text editor and set `FLASK_SECRET_KEY` to any random text. To connect to the
> class website (to see classmates' strategies and submit your own), also paste in the two values
> your instructor gives you:
>
> ```ini
> HUB_BASE_URL=https://<your-class-site>.pythonanywhere.com
> HUB_API_TOKEN=<the shared class token>
> ```
>
> Without these the app still works fully offline — you just won't see the class gallery.

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
- The **function name must match the strategy name** you give it in the app.
- It must **return exactly `'C'` or `'D'`**.
- You may use a few safe modules (e.g. `random`, `math`). File access, networking, and tricks like
  `exec`/`eval`/`open` are blocked by the sandbox.

The same function works in **all three game modes** below — you only write it once.

---

## 3. Practice

The app has three modes (tabs). Pick strategies, choose game options, and click run — everything
computes on your CPU, with **no limit** on how many strategies you add (you'll get a quick
time estimate before a big run).

- **1v1** — round‑robin: every strategy plays every other one, head‑to‑head. Ranked by a
  **Weighted Score** (win rate + cooperation + points; you can re‑weight it live with the sliders).
- **N‑Player** — one big group game where everyone moves at once, under different payoff rules
  (Public Goods, Pairwise Matrix, or K‑Cooperator). Also ranked by Weighted Score.
- **OS Simulation** — a fun twist: your strategy becomes a **CPU scheduler** (`Cooperate` = give up
  the CPU, `Defect` = keep it) and is scored on how well it runs a simulated multi‑core machine.

**Choosing who competes.** Each run uses:
- **Your local players** — the editable boxes where you write/paste strategies, and
- **Select Participants** — a checklist of strategies fetched from the class site, split into
  **Players** (classmates' submissions), **Bots** (instructor‑made), and **Practice** (instructor
  examples). Tick the ones you want to face. Their code is read‑only — you compete against them, you
  don't edit them.

Press **Refresh Gallery** to pull the latest strategies from the website (it's cached, so it also
works offline after the first fetch).

---

## 4. Submit your strategy to the competition

On the **Hub** tab, use **"Submit your strategy to the website"**:

1. Paste your strategy and click submit.
2. The app **screens and test‑runs it on your machine first** — if it has an illegal import or
   crashes, it tells you so you can fix it before it ever reaches the website.
3. Once it passes, your browser opens the class website with your strategy **pre‑filled** — log in
   there and click **Submit**. You can update it any time before the tournament.

Login and submission happen **on the website**, not in this app. (Strategy names must be unique so
everyone's entries can be told apart.)

---

## Tips & common mistakes

- Forgetting to `return` `'C'` or `'D'` (e.g. returning `1`, `True`, or nothing) → your strategy
  will be flagged. Always return one of the two strings.
- Name mismatch: if your strategy is called "Grudger", the function must be `def Grudger(...)`.
- Use `meta['rng']` for randomness, not `import random` + `random.random()`, if you want repeatable
  results.
- On round 1 `last_moves` is empty (`[]`) — handle that case (usually start by cooperating).

---

## For instructors / admins

- **Run the official tournament locally:** practice in any mode with the real roster, then use
  **Submit results to website** — the app stages the result and opens the website's publish page
  (post now or schedule). Your local players are registered there as bots.
- **Manage bots & practice strategies** on the website's admin dashboard.

## For developers

This is the **local** half of a two‑repo system; the website lives in **`ipd-hub`** (login,
submission, results, all the secrets). This app has **no Firebase and no keys** —
`firebase_config.py` / `auth_utils.py` / `audit_utils.py` are no‑op stubs here.

- **Backend:** `app.py` (routes, 1v1 engine, the `is_safe_code` sandbox + `run_with_limit`),
  `n_player_simulation.py`, `payoff_models.py`, `core_simulation.py`, `schedulers.py`,
  `tournament_package.py`, `hub_client.py` (fetches from the website).
- **Frontend:** `templates/{index,hub,_game_info,navbar}.html`,
  `static/js/{pd_results_render,os_sim_render,csp_delegation}.js`, `static/css/style.css`.
- **Run the tests:**
  ```bash
  RUN_TOURNAMENTS_SYNC=true FLASK_ENV=development FLASK_SECRET_KEY=test python -m pytest -q
  ```
  (One env‑dependent test, `test_strategy_with_helper_functions`, is a known standalone failure.)

> **Scoring:** `Score = (win_rate·W_win + cooperation·W_coop + points·W_points) × 100`, where
> win rate is per‑opponent and points are per‑round, so match length never biases the result. Every
> run is seeded and reproducible. **Safety:** all executed code is screened by `is_safe_code` and
> bounded by an instruction cap; the website re‑screens everything on submit (see
> `ipd-hub/SECURITY.md`). Distribute this app via `git clone` / `git archive`, **never a zip** (a zip
> can drag along a local `.env`).
# ipd-app
