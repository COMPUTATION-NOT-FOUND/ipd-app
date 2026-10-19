"""Every Python strategy example in the README must actually work.

Each block is pulled out of README.md, checked by the sandbox screen, and played in a short 1v1
match (with and without a seed), an N-Player game and an OS Simulation run. If an example is
edited into something broken, this fails, so the README can't drift away from the code.
"""
import os
import re

import pytest

from app import game, is_safe_code, get_safe_globals, extract_strategy_function
from n_player_simulation import group_tournament
from payoff_models import PublicGoodsPayoff
import core_simulation as cs

pytestmark = pytest.mark.regression

README = os.path.join(os.path.dirname(__file__), '..', '..', 'README.md')
TFT = "def tft(last_moves, my_history, opponents_histories, meta):\n    return last_moves[0] if last_moves else 'C'"


GUIDE = os.path.join(os.path.dirname(__file__), '..', '..', 'docs', 'guide', 'student-guide.html')


def _examples():
    """Strategy examples from the README and from the student guide PDF's source."""
    import html
    text = open(README, encoding='utf-8').read()
    blocks = re.findall(r"```python\n(.*?)```", text, re.S)
    guide = open(GUIDE, encoding='utf-8').read()
    blocks += [html.unescape(b) + '\n' for b in
               re.findall(r'<pre><code class="python">(.*?)</code></pre>', guide, re.S)]
    seen, out = set(), []
    for b in blocks:
        if b.lstrip().startswith('def ') and b not in seen:
            seen.add(b)
            out.append(b)
    return out


EXAMPLES = _examples()


def test_readme_has_examples_for_every_meta_field():
    joined = '\n'.join(EXAMPLES)
    for field in ('round', 'rng', 'n_players', 'player_index', 'tournament_info'):
        assert f"meta['{field}']" in joined
    assert len(EXAMPLES) >= 10


@pytest.mark.parametrize('code', EXAMPLES, ids=[re.match(r'\s*def (\w+)', c).group(1) for c in EXAMPLES])
def test_example_works_in_every_game(code):
    ok, why = is_safe_code(code)
    assert ok, why

    for seed in (None, 7):
        r = game(code, 'example', TFT, 'tft', rounds=30, seed=seed)
        assert r['winner'] != 'Error', r.get('error')

    def compiled(name, src):
        g = get_safe_globals()
        exec(src, g)
        return {'name': name, 'code': src, 'func': extract_strategy_function(src, g, prefer_n_args=4)}
    group = group_tournament([compiled('example', code), compiled('a', TFT), compiled('b', TFT)],
                             rounds=20, seed=3, payoff_model=PublicGoodsPayoff(b=2.0, c=1.0))
    assert group['runtime_errors'] == []

    func = cs.extract_strategy_func(code, seed=1)
    sim = cs.OSSimulator('Mixed', [func, func], seed=1, num_cores=2)
    sim.generate_workload(20)
    sim.run()      # raises StrategyRuntimeError if the example fails here
