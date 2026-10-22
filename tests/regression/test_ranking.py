"""Ranking: what decides the order and how ties are broken (student report, Oct 2026: the
leaderboard order did not follow the itinerary, and there was no tie-break choice)."""
import pytest

from ranking import (DEFAULT_RANKING, normalize_ranking, rank_entries, describe_ranking)

pytestmark = pytest.mark.regression


def _names(rows):
    return [r['name'] for r in rows]


def test_defaults_follow_the_itinerary():
    assert DEFAULT_RANKING['1v1']['metric'] == 'total_points'
    assert DEFAULT_RANKING['nplayer']['metric'] == 'total_points'
    assert (DEFAULT_RANKING['os']['metric'], DEFAULT_RANKING['os']['direction']) == ('avg_response', 'asc')


def test_highest_points_first_by_default():
    rows = [{'name': 'a', 'total_points': 1}, {'name': 'b', 'total_points': 3},
            {'name': 'c', 'total_points': 2}]
    assert _names(rank_entries(rows, None, '1v1')) == ['b', 'c', 'a']


def test_os_lowest_response_time_first():
    rows = [{'name': 'a', 'avg_response': 9.0}, {'name': 'b', 'avg_response': 2.5}]
    assert _names(rank_entries(rows, None, 'os')) == ['b', 'a']


def test_tiebreak_chain_in_order():
    # Sprint 1: points, then cooperation %, then win rate, then earliest submission.
    rows = [
        {'name': 'late', 'total_points': 10, 'wins': 2, 'losses': 2, 'cooperation_percentage': 50, 'submitted_at': '2026-10-22T10:00:00Z'},
        {'name': 'early', 'total_points': 10, 'wins': 2, 'losses': 2, 'cooperation_percentage': 50, 'submitted_at': '2026-10-21T10:00:00Z'},
        {'name': 'winner', 'total_points': 10, 'wins': 4, 'losses': 0, 'cooperation_percentage': 50},
        {'name': 'coop', 'total_points': 10, 'wins': 0, 'losses': 4, 'cooperation_percentage': 80},
        {'name': 'top', 'total_points': 11, 'wins': 0, 'losses': 4},
    ]
    ranked = rank_entries(rows, None, '1v1')
    assert _names(ranked) == ['top', 'coop', 'winner', 'early', 'late']
    assert [r['rank'] for r in ranked] == [1, 2, 3, 4, 5]
    assert ranked[2]['win_rate'] == 1.0


def test_weighted_score_uses_the_ranking_weights():
    rows = [{'name': 'coop', 'wins': 0, 'losses': 2, 'cooperation_percentage': 100, 'points_percentage': 40},
            {'name': 'pts', 'wins': 2, 'losses': 0, 'cooperation_percentage': 0, 'points_percentage': 80}]
    all_points = rank_entries([dict(r) for r in rows], {'metric': 'weighted_score'}, '1v1')
    assert _names(all_points) == ['pts', 'coop'] and round(all_points[0]['weighted_score'], 6) == 80
    coop_heavy = rank_entries([dict(r) for r in rows],
                              {'metric': 'weighted_score', 'weights': {'cooperation': 70, 'points': 30}}, '1v1')
    assert _names(coop_heavy) == ['coop', 'pts']
    assert round(coop_heavy[0]['weighted_score'], 6) == 82      # 1.0*70 + 0.4*30


def test_weights_are_normalized():
    from ranking import normalize_weights, describe_weights
    assert normalize_weights(None) == {'points': 1.0, 'cooperation': 0.0, 'win_rate': 0.0}
    assert normalize_weights({'points': 60, 'cooperation': 20, 'win_rate': 20}) ==         {'points': 0.6, 'cooperation': 0.2, 'win_rate': 0.2}
    assert normalize_weights({'points': -5, 'cooperation': 'x'}) == normalize_weights(None)
    assert describe_weights({'points': 1}) == 'points 100%'


def test_everything_equal_keeps_entry_order():
    rows = [{'name': n, 'total_points': 5} for n in ('x', 'y', 'z')]
    assert _names(rank_entries(rows, None, '1v1')) == ['x', 'y', 'z']


def test_float_noise_counts_as_a_tie():
    rows = [{'name': 'a', 'total_points': 0.1 + 0.2, 'wins': 0},
            {'name': 'b', 'total_points': 0.3, 'wins': 1}]
    assert _names(rank_entries(rows, None, '1v1')) == ['b', 'a']


def test_shared_rank():
    rows = [{'name': 'a', 'total_points': 9}, {'name': 'b', 'total_points': 7, 'wins': 9},
            {'name': 'c', 'total_points': 7}, {'name': 'd', 'total_points': 1}]
    ranked = rank_entries(rows, {'shared_rank': True}, '1v1')
    assert [(r['name'], r['rank_label']) for r in ranked] == [('a', '1'), ('b', '=2'), ('c', '=2'), ('d', '4')]


def test_missing_values_rank_last_in_either_direction():
    rows = [{'name': 'none'}, {'name': 'low', 'avg_response': 1.0}]
    assert _names(rank_entries(rows, None, 'os')) == ['low', 'none']
    rows = [{'name': 'none'}, {'name': 'some', 'total_points': 0}]
    assert _names(rank_entries(rows, None, '1v1')) == ['some', 'none']


def test_chosen_metric_and_direction():
    rows = [{'name': 'a', 'throughput': 1, 'avg_response': 1},
            {'name': 'b', 'throughput': 2, 'avg_response': 2}]
    assert _names(rank_entries(rows, {'metric': 'throughput'}, 'os')) == ['b', 'a']
    assert _names(rank_entries(rows, {'metric': 'throughput', 'direction': 'asc'}, 'os')) == ['a', 'b']


def test_normalize_rejects_unknown_fields():
    spec = normalize_ranking({'metric': '__class__', 'direction': 'sideways',
                              'tiebreak': ['wins', 'code', 'wins', 7], 'shared_rank': 1}, '1v1')
    assert spec == {'metric': 'total_points', 'direction': 'desc', 'tiebreak': ['wins'],
                    'shared_rank': True, 'weights': {'points': 1.0, 'cooperation': 0.0, 'win_rate': 0.0}}
    assert normalize_ranking('junk', 'os') == DEFAULT_RANKING['os']


def test_description():
    assert describe_ranking(DEFAULT_RANKING['1v1'], '1v1') == (
        "Ranked by total points (highest first). Ties: cooperation %, then win rate, then earliest submission.")
    assert describe_ranking(DEFAULT_RANKING['os'], 'os').startswith(
        "Ranked by average response time (lowest first).")
