"""Payoff setups that are not a dilemma get a warning, never an error (review, Oct 2026: the
N-Player Public Goods default b=2, c=1 made cooperating always pay)."""
import pytest

import app as ipd

pytestmark = pytest.mark.regression

CLASSIC = {'CC': [3, 3], 'CD': [0, 5], 'DC': [5, 0], 'DD': [1, 1]}
COOP = "def s(last_moves, my_history, opponents_histories, meta):\n    return 'C'\n"
THREE = [{'name': n, 'code': COOP} for n in 'abc']


def test_classic_table_and_default_public_goods_are_fine():
    assert ipd.payoff_warnings(payoff_matrix=CLASSIC) == []
    assert ipd.payoff_warnings(payoff_model={'type': 'public_goods'}, group_size=5) == []
    assert ipd.parse_payoff_model(None).__class__.__name__ in ('PairwiseMatrixPayoff', 'PublicGoodsPayoff')
    assert ipd.parse_payoff_model({'type': 'public_goods'}).b == 0.4


@pytest.mark.parametrize('table, part', [
    ({'CC': [3, 3], 'CD': [0, 5], 'DC': [2, 0], 'DD': [1, 1]}, "not a Prisoner's Dilemma"),
    ({'CC': [3, 3], 'CD': [0, 9], 'DC': [9, 0], 'DD': [1, 1]}, 'taking turns'),
])
def test_bad_tables_warn(table, part):
    assert any(part in w for w in ipd.payoff_warnings(payoff_matrix=table))


def test_public_goods_checks_both_sides():
    assert any('always pays' in w for w in ipd.payoff_warnings(
        payoff_model={'type': 'public_goods', 'b': 2.0, 'c': 1.0}, group_size=5))
    assert any('no better than nobody' in w for w in ipd.payoff_warnings(
        payoff_model={'type': 'public_goods', 'b': 0.1, 'c': 1.0}, group_size=5))


def test_routes_return_warnings_but_still_run(client):
    r = client.post('/tournament', json={'strategies': THREE[:2], 'rounds': 5,
                                         'payoff_matrix': {'CC': [3, 3], 'CD': [0, 5], 'DC': [2, 0], 'DD': [1, 1]}})
    assert r.status_code == 200 and r.get_json()['warnings']
    r = client.post('/nplayer/tournament', json={'strategies': THREE, 'rounds': 5,
                                                 'payoff_model': {'type': 'public_goods', 'b': 2, 'c': 1}})
    assert r.status_code == 200 and r.get_json()['warnings']
