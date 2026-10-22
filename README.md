# Prisoner's Dilemma Practice App

This is the app you run on your own computer to write Prisoner's Dilemma strategies, test them, and enter them into the class competition. Everything runs on your machine. There is no account to create.

You will do three things.

1. Write a strategy, which is a short Python function.
2. Practise it against your own ideas and against your classmates' strategies.
3. Submit your best one to the class website.

The schedule, the settings for each round and a full FAQ (including everything in `meta`) are on the class website, under Announcements and FAQ.


## 1. Get the app

### Option A. Download it (easiest)

1. Open the Releases page of this repository on GitHub and download the file for your computer. That is `ipd-app-windows.exe`, `ipd-app-macos` or `ipd-app-linux`.
2. Double click it. Your browser opens at http://127.0.0.1:5000. Keep the black window open while you use the app. Close it to stop.
3. To see your classmates' strategies, open the Settings tab, type the website address and class token your instructor gave you, and press Connect. You only do this once. The app remembers it, even after you download a new version.

Some first-run notes.

* Windows may say "Windows protected your PC" because the file is not signed. Click More info, then Run anyway.
* On a Mac, run this once in Terminal from the folder with the file.
  `xattr -d com.apple.quarantine ipd-app-macos && chmod +x ipd-app-macos && ./ipd-app-macos`
  The Mac download only works on Apple Silicon (M1 or newer). On an older Intel Mac use Option B.
* On Linux, run `chmod +x ipd-app-linux && ./ipd-app-linux`.
* To update, download the new file and use it instead of the old one. Your settings and saved strategies stay.

### Option B. Run it from the source code

You need Python 3.10 or newer. Check with `python --version` (on Linux or Mac, `python3 --version`).

```bash
git clone <repo-url>
cd ipd-app
```

On Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

If PowerShell says running scripts is disabled, skip the second line and use the environment's Python directly.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

On Linux or Mac

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5000. Stop the app with Ctrl+C. If `pip install` times out on a slow connection, add `--default-timeout=100 --retries 10` to it.

The app only answers on your own computer. Other devices on your network cannot reach it.


## 2. Write your first strategy

A strategy is a Python function. The app calls it once every round, and it returns `'C'` to cooperate or `'D'` to defect. This is Tit-for-Tat. It cooperates first and then copies the opponent.

```python
def tit_for_tat(last_moves, my_history, opponents_histories, meta):
    if not last_moves:
        return 'C'
    return last_moves[0]
```

Your function always gets the same four inputs.

| Input | What it holds |
|---|---|
| `last_moves` | Each opponent's move from the last round, such as `['C']`. It is empty in the first round. |
| `my_history` | Every move you have made so far, oldest first. |
| `opponents_histories` | One list of moves for each opponent. |
| `meta` | Extra information about the game. See section 3. |

A few rules.

* Write one function with these four inputs. The function name can be anything.
* The strategy name you type in the app is just a label. It can have spaces, like "Grim Trigger", but it must be unique in a run.
* Always return exactly `'C'` or `'D'`. Returning `1`, `True`, `None` or `'c'` counts as a failure.
* If your strategy crashes, loops forever or returns something else, it is disqualified. It is removed from the ranking, its matches do not count for anyone, and the results tell you why.
* You can import a few safe modules such as `math`, `random` and `collections`. Files, the network, `exec`, `eval` and `open` are blocked.

Here are three more complete strategies you can paste into the app and play with.

```python
def grim_trigger(last_moves, my_history, opponents_histories, meta):
    # Cooperate until anyone defects once, then defect for the rest of the game.
    for history in opponents_histories:
        if 'D' in history:
            return 'D'
    return 'C'
```

```python
def pavlov(last_moves, my_history, opponents_histories, meta):
    # Win stay, lose shift. Keep your last move if the opponent matched it, otherwise switch.
    if not my_history:
        return 'C'
    if last_moves[0] == my_history[-1]:
        return my_history[-1]
    return 'D' if my_history[-1] == 'C' else 'C'
```

```python
def suspicious_tit_for_tat(last_moves, my_history, opponents_histories, meta):
    # Like Tit-for-Tat, but starts by defecting to test the opponent.
    if not last_moves:
        return 'D'
    return last_moves[0]
```


## 3. Using meta

`meta` is a dictionary with five things in it. Each one has its own small example below. Every example is a complete strategy that works in all three games.

### meta['round']

The round number, starting at 0. Use it when your behaviour should change over time.

```python
def slow_start(last_moves, my_history, opponents_histories, meta):
    # Be friendly for the first 5 rounds, then copy the opponent.
    if meta['round'] < 5:
        return 'C'
    return last_moves[0]
```

### meta['rng']

A random number generator. Use it instead of `import random` whenever you want randomness. When a run has a seed, this generator gives the same numbers every time, so you can repeat a result exactly.

```python
def generous_tit_for_tat(last_moves, my_history, opponents_histories, meta):
    # Like Tit-for-Tat, but forgive a defection about 1 time in 10.
    if not last_moves or last_moves[0] == 'C':
        return 'C'
    if meta['rng'].random() < 0.1:
        return 'C'
    return 'D'
```

You can also call `meta['rng'].randint(0, 1)` or `meta['rng'].choice(['C', 'D'])`.

### meta['n_players']

How many players are in the game, counting you. It is 2 in a 1v1 match, the group size in N-Player, and the number of cores in the OS Simulation.

```python
def careful_in_crowds(last_moves, my_history, opponents_histories, meta):
    # In a big group one defector can hide, so only cooperate if nobody defected.
    if not last_moves:
        return 'C'
    if meta['n_players'] > 2:
        return 'C' if 'D' not in last_moves else 'D'
    return last_moves[0]
```

### meta['player_index']

Your seat in the game, starting at 0. Most strategies never need it. This example only shows how to read it.

```python
def seat_based_start(last_moves, my_history, opponents_histories, meta):
    # Even seats open by cooperating and odd seats open by defecting.
    if not last_moves:
        return 'C' if meta['player_index'] % 2 == 0 else 'D'
    return last_moves[0]
```

### meta['tournament_info']

A dictionary about the game you are in. The most useful key is `format`. It is `'2-player'` in 1v1, `'n_player'` in N-Player and `'os_simulation'` in the OS Simulation. So one function can play each game its own way.

```python
def adapts_to_the_game(last_moves, my_history, opponents_histories, meta):
    game = meta['tournament_info'].get('format')
    if not last_moves:
        return 'C'
    if game == 'os_simulation':
        # Here last_moves[0] is 'D' when the computer is busy. Give way when it is busy.
        return 'C' if last_moves[0] == 'D' else 'D'
    if game == 'n_player':
        # Follow the majority of the group.
        return 'C' if last_moves.count('C') * 2 >= len(last_moves) else 'D'
    return last_moves[0]
```

In 1v1 it also holds the payoff table, for example `{'CC': [3, 3], 'CD': [0, 5], 'DC': [5, 0], 'DD': [1, 1]}`. Each key is your move then the other player's move, and each value is `[your points, their points]`. So your score for a round is `table[my_move + their_move][0]`.

```python
def reads_the_payoffs(last_moves, my_history, opponents_histories, meta):
    # If defecting against a cooperator pays more than double what cooperating does, always defect.
    table = meta['tournament_info'].get('payoff_matrix')
    if table and table['DC'][0] > 2 * table['CC'][0]:
        return 'D'
    if not last_moves:
        return 'C'
    return last_moves[0]
```

It also tells you the match `mode`, the `modes` and `rounds` of the run, and `ranking`, how the run is ranked. In N-Player `payoff_model` holds the jar settings, such as `{'name': 'public_goods', 'b': 0.4, 'c': 1}`. In the OS Simulation `scheduler` names the scheduler. The website FAQ lists every key for each game.

### What last_moves means in each game

* In 1v1 it is one move, the opponent's, like `['D']`.
* In N-Player it is one move per other player, like `['C', 'D', 'C']`.
* In the OS Simulation there is no other player. `last_moves[0]` is the state of the computer. `'D'` means the system is busy and `'C'` means it is calm. Cooperating means giving up the CPU, and defecting means keeping it.


## 4. Practise

The app has three tabs, one for each game. In each one you add strategies, choose the settings and press the run button. Everything runs on your computer and there is no limit on how many strategies you add. Before a big run the app tells you roughly how long it will take.

### 1v1

Every strategy plays every other strategy, one pair at a time. The leaderboard ranks them by total points, highest first. Ties go to higher cooperation %, then higher win rate, then whoever submitted first. Use the Ranking box above the table to rank by something else and press Apply.

1. Open the 1v1 tab. Two example strategies are already filled in.
2. Change their code or press Add Strategy to add your own.
3. Tick classmates' strategies under Select Participants if you want to face them.
4. Press Start Round Robin Tournament.

### N-Player

Everyone plays in one group and moves at the same time. You pick how the group is paid. In Public Goods, cooperators pay into a shared pot that is multiplied and split. In Pairwise Matrix every pair in the group plays a normal match. In K-Cooperator your payoff depends on how many players cooperated.

1. Open the N-Player tab and choose a payoff model.
2. Add strategies, set the rounds (up to 1000) and press Start N-Player Tournament.

### OS Simulation

Your strategy becomes a CPU scheduler. It is scored on how well a simulated computer runs a set of tasks. The tab has a short glossary of terms like FCFS, MLFQ and CFS.

1. Open the OS Simulation tab and choose the number of cores and a scheduler.
2. Leave Runs to average at 10 for a fair comparison. Each run uses a different set of tasks and the results are averaged.
3. Press Run OS Simulation.

The results show a plus or minus range next to each number. If two strategies have overlapping ranges, neither is clearly better. If the results say the cores can't keep up, the tasks need more CPU time than the cores have, so add cores before comparing.

### Useful extras

* Refresh Gallery fetches the latest strategies from the class website. They are saved, so practice works offline afterwards.
* Save locally keeps one of your strategies on your computer. It then appears in the Local column, and the x button deletes it.
* On the Hub tab you can import a classmate's strategy and press Open in Practice to load it straight into an editor box.
* Tick Run in sandbox mode under a run button to test every strategy in a separate process first. Strategies that hang or crash are left out and named. Use it for code you did not write.


## 5. Submit your strategy

1. Open the Hub tab and paste your strategy under Submit your strategy to the website.
2. Press the button. The app first checks and test plays your strategy on your computer and tells you if anything is wrong.
3. If it passes, the class website opens with your strategy filled in. Log in there and press Submit. You can update it any time before the tournament.


## 6. Common mistakes

* Returning nothing. If one branch of your code does not return, the function returns `None` and fails. Make sure every path returns `'C'` or `'D'`.
* Forgetting the first round. `last_moves` is empty in round 0, so `last_moves[0]` crashes. Check `if not last_moves:` first.
* Using `random.random()`. It works, but the result changes every run. Use `meta['rng']` so seeded runs repeat.
* "Duplicate strategy name". Two entries in the same run share a name, often an editor box and a ticked gallery entry. Rename one.


## For instructors

Run the official tournament in the app with the real roster, then use Submit results to website (instructors only). The app sends the result to the website as a draft and opens the publish page, where you log in and post it now or schedule it. Students can see this panel too, but nothing is published without an instructor logging in. Bots and practice strategies are managed on the website's admin dashboard, which can also download every submitted strategy as a ZIP file.


## For developers

This is the local half of a two part system. The website lives in the `ipd-hub` repository and holds login, submissions, results and all secrets. This app has no Firebase and no keys. `firebase_config.py`, `auth_utils.py` and `audit_utils.py` here are empty stand-ins.

* `app.py` has the routes, the 1v1 engine and the `is_safe_code` sandbox.
* `n_player_simulation.py` has the N-Player engine and `validate_move`, which every engine uses.
* `core_simulation.py` and `schedulers.py` are the OS Simulation. `payoff_models.py` has the N-Player payoffs.
* `isolation.py` is sandbox mode and `hub_client.py` talks to the website.
* The pages are in `templates/` and `static/`.

Install the test tools with `pip install -r requirements-dev.txt`, then run the tests.

```bash
RUN_TOURNAMENTS_SYNC=true FLASK_ENV=development FLASK_SECRET_KEY=test python -m pytest -q
```

In PowerShell set the three variables first.

```powershell
$env:RUN_TOURNAMENTS_SYNC='true'; $env:FLASK_ENV='development'; $env:FLASK_SECRET_KEY='test'
python -m pytest -q
```

Every Python example in this README is run by `tests/regression/test_readme_examples.py`, so keep them working when you edit them.

`python app.py` serves with Waitress on 127.0.0.1:5000. Set `FLASK_DEBUG=True` to use the Flask debug server instead.

The download in Option A is built with PyInstaller from `launcher.py`. Run `pip install -r requirements-build.txt` and `python build_exe.py` to build it for your own system. Pushing a version tag such as `git tag v1.3.0 && git push origin v1.3.0` makes GitHub test and build all three systems and publish a release. The `.env` file is never bundled, because anything inside the app can be unpacked and the class token must stay private.

Rankings use `ranking.py` (and its browser twin `static/js/ranking.js`). The optional weighted score is `(win_rate * W_win + cooperation * W_coop + points * W_points) * 100`. Win rate is counted per opponent and points per round, so the length of a match never changes the ranking. All strategy code is checked by `is_safe_code` and limited by an instruction cap, and the website checks it again on submit.
