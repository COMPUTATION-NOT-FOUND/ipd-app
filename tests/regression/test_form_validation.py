"""Bad Rounds and payoff values get a plain JSON 400 (review, Oct 2026: an empty Rounds box
crashed the request, the page then navigated to a 405 page and lost the editor code; a blank
payoff entry showed a raw Python error or scored silently wrong)."""
import pytest

import app as ipd

COOP = "def s(last_moves, my_history, opponents_histories, meta):\n    return 'C'\n"
TWO = [{'name': 'a', 'code': COOP}, {'name': 'b', 'code': COOP}]
GOOD = {'CC': [3, 3], 'CD': [0, 5], 'DC': [5, 0], 'DD': [1, 1]}


@pytest.mark.parametrize('rounds', ['', '-', 'abc', '0', -5, True])
def test_bad_rounds_is_a_json_400(client, rounds):
    r = client.post('/tournament', json={'strategies': TWO, 'rounds': rounds})
    assert r.status_code == 400 and r.is_json
    assert 'Rounds' in r.get_json()['error']


def test_rounds_as_text_number_still_works(client):
    r = client.post('/tournament', json={'strategies': TWO, 'rounds': '12'})
    assert r.status_code == 200, r.get_json()


@pytest.mark.parametrize('table', [
    {'CC': [3, 3], 'CD': [0, None], 'DC': [5, 0], 'DD': [1, 1]},
    {'CC': [3, 3]},
    {'CC': [3, 3], 'CD': [0, 'x'], 'DC': [5, 0], 'DD': [1, 1]},
    {'CC': [3, 3], 'CD': [0, float('nan')], 'DC': [5, 0], 'DD': [1, 1]},
    [1, 2, 3, 4],
])
def test_bad_payoff_table_is_a_json_400(client, table):
    r = client.post('/tournament', json={'strategies': TWO, 'rounds': 5, 'payoff_matrix': table})
    assert r.status_code == 400 and r.is_json
    assert 'Payoff' in r.get_json()['error']
    assert 'NoneType' not in r.get_json()['error']


def test_play_route_checks_too(client):
    body = {'player_a_code': COOP, 'player_a_name': 'a', 'player_b_code': COOP,
            'player_b_name': 'b', 'rounds': ''}
    assert client.post('/play', json=body).status_code == 400
    body.update(rounds=5, payoff_matrix={'CC': [3, 3]})
    assert client.post('/play', json=body).status_code == 400
    body.update(payoff_matrix=GOOD)
    assert client.post('/play', json=body).status_code == 200


@pytest.mark.parametrize('model, word', [
    ({'type': 'public_goods', 'b': 'x', 'c': 1}, '"b"'),
    ({'type': 'public_goods', 'b': 2, 'c': 1, 'nonlinear': {'type': 'power', 'alpha': 'x'}}, 'alpha'),
    ({'type': 'pairwise_matrix', 'payoff_matrix': {'CC': [3, 3]}}, 'Payoff'),
    ({'type': 'kcoop_tensor', 'u_C': [1, 'x'], 'u_D': [0, 1]}, 'u_C'),
    ('public_goods', 'Payoff model'),
])
def test_bad_nplayer_payoff_model_is_a_json_400(client, model, word):
    r = client.post('/nplayer/tournament', json={'strategies': TWO, 'rounds': 5, 'payoff_model': model})
    assert r.status_code == 400 and r.is_json
    err = r.get_json()['error']
    assert word in err and 'compile' not in err


def test_nplayer_bad_rounds_is_a_json_400(client):
    r = client.post('/nplayer/tournament', json={'strategies': TWO, 'rounds': ''})
    assert r.status_code == 400 and 'Rounds' in r.get_json()['error']


def test_unexpected_error_on_a_script_call_is_json(app_instance, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError('internal detail')
    monkeypatch.setattr(ipd, 'game', boom)
    app_instance.config['TESTING'] = False   # let the 500 handler run instead of re-raising
    try:
        body = {'player_a_code': COOP, 'player_a_name': 'a', 'player_b_code': COOP,
                'player_b_name': 'b', 'rounds': 5}
        r = app_instance.test_client().post('/play', json=body)
    finally:
        app_instance.config['TESTING'] = True
    assert r.status_code == 500 and r.is_json
    assert 'internal detail' not in r.get_json()['error']
