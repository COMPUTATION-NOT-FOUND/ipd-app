"""
Tests for N-player tournament orchestrator (Phase 4).

Tests the group_tournament() function with various configurations,
including group sizing, leaderboard structure, and determinism.
"""

import pytest
import random
from payoff_models import PairwiseMatrixPayoff, PublicGoodsPayoff
from n_player_simulation import group_tournament


# Simple test strategies
def always_cooperate(last_moves, my_history, opponents_histories, meta):
    """Always cooperate"""
    return 'C'


def always_defect(last_moves, my_history, opponents_histories, meta):
    """Always defect"""
    return 'D'


def tit_for_tat(last_moves, my_history, opponents_histories, meta):
    """Cooperate unless any opponent defected last round"""
    if not last_moves:
        return 'C'
    return 'D' if 'D' in last_moves else 'C'


def random_strategy(last_moves, my_history, opponents_histories, meta):
    """Random with meta RNG"""
    rng = meta.get('rng', random.Random())
    return rng.choice(['C', 'D'])


class TestGroupTournament:
    """Tests for group_tournament function"""
    
    def test_single_group_all_players(self):
        """Test tournament with all players in one group (group_size=None)"""
        strategies = [
            {'name': 'Cooperator', 'code': '', 'func': always_cooperate},
            {'name': 'Defector', 'code': '', 'func': always_defect},
            {'name': 'TitForTat', 'code': '', 'func': tit_for_tat}
        ]
        
        payoff_model = PairwiseMatrixPayoff(
            payoff_matrix={'CC': [3, 3], 'CD': [0, 5], 'DC': [5, 0], 'DD': [1, 1]},
            aggregate='sum'
        )
        
        result = group_tournament(
            strategies=strategies,
            rounds=100,
            group_size=None,  # All in one group
            seed=42,
            payoff_model=payoff_model
        )
        
        # Should have basic structure
        assert 'leaderboard' in result
        assert 'matches' in result
        assert 'tournament_info' in result
        
        # Should have one match (all players together)
        assert len(result['matches']) == 1
        
        # Leaderboard should have all 3 players
        assert len(result['leaderboard']) == 3
        
        # Check required fields in leaderboard
        for entry in result['leaderboard']:
            assert 'name' in entry
            assert 'total_points' in entry
            assert 'cooperation_percentage' in entry
            assert 'wins' in entry
            assert 'draws' in entry
            assert 'losses' in entry
    
    def test_multiple_groups_with_group_size(self):
        """Test tournament split into multiple groups"""
        strategies = [
            {'name': f'Player{i}', 'code': '', 'func': always_cooperate if i % 2 == 0 else always_defect}
            for i in range(6)
        ]
        
        payoff_model = PairwiseMatrixPayoff(
            payoff_matrix={'CC': [3, 3], 'CD': [0, 5], 'DC': [5, 0], 'DD': [1, 1]},
            aggregate='sum'
        )
        
        result = group_tournament(
            strategies=strategies,
            rounds=50,
            group_size=3,  # 2 groups of 3
            seed=99,
            payoff_model=payoff_model
        )
        
        # Should have 2 matches (2 groups)
        assert len(result['matches']) == 2
        
        # Leaderboard should have all 6 players
        assert len(result['leaderboard']) == 6
    
    def test_leaderboard_fields_compatible_with_weighted_scoring(self):
        """Leaderboard should have fields compatible with determine_weighted_results()"""
        strategies = [
            {'name': 'C', 'code': '', 'func': always_cooperate},
            {'name': 'D', 'code': '', 'func': always_defect}
        ]
        
        payoff_model = PairwiseMatrixPayoff(
            payoff_matrix={'CC': [3, 3], 'CD': [0, 5], 'DC': [5, 0], 'DD': [1, 1]},
            aggregate='sum'
        )
        
        result = group_tournament(
            strategies=strategies,
            rounds=100,
            seed=42,
            payoff_model=payoff_model
        )
        
        leaderboard = result['leaderboard']
        assert len(leaderboard) == 2
        
        # Check all required fields for weighted scoring
        for entry in leaderboard:
            assert 'name' in entry
            assert 'total_points' in entry
            assert 'cooperation_percentage' in entry
            assert 'points_percentage' in entry
            assert 'wins' in entry
            assert 'draws' in entry
            assert 'losses' in entry
            
            # Values should be valid
            assert isinstance(entry['total_points'], (int, float))
            assert 0 <= entry['cooperation_percentage'] <= 100
            assert 0 <= entry['points_percentage'] <= 100
            assert entry['wins'] >= 0
            assert entry['draws'] >= 0
            assert entry['losses'] >= 0
    
    def test_win_draw_loss_counting(self):
        """Test that wins/draws/losses are counted correctly in N-player matches"""
        strategies = [
            {'name': 'Cooperator', 'code': '', 'func': always_cooperate},
            {'name': 'Defector', 'code': '', 'func': always_defect},
            {'name': 'TitForTat', 'code': '', 'func': tit_for_tat}
        ]
        
        payoff_model = PairwiseMatrixPayoff(
            payoff_matrix={'CC': [3, 3], 'CD': [0, 5], 'DC': [5, 0], 'DD': [1, 1]},
            aggregate='sum'
        )
        
        result = group_tournament(
            strategies=strategies,
            rounds=100,
            seed=42,
            payoff_model=payoff_model
        )
        
        leaderboard = result['leaderboard']
        
        # Total wins + draws + losses should equal number of matches played
        # With 1 match, top scorer gets 1 win, others get 1 loss
        # (or draws if tied)
        total_wins = sum(e['wins'] for e in leaderboard)
        total_draws = sum(e['draws'] for e in leaderboard)
        total_losses = sum(e['losses'] for e in leaderboard)
        
        # Each player participated in 1 match
        # so wins + draws + losses should equal 1 for each player
        for entry in leaderboard:
            assert entry['wins'] + entry['draws'] + entry['losses'] == 1
    
    def test_deterministic_with_seed(self):
        """Results should be deterministic when seed is provided"""
        strategies = [
            {'name': 'Random1', 'code': '', 'func': random_strategy},
            {'name': 'Random2', 'code': '', 'func': random_strategy},
            {'name': 'Random3', 'code': '', 'func': random_strategy}
        ]
        
        payoff_model = PairwiseMatrixPayoff(
            payoff_matrix={'CC': [3, 3], 'CD': [0, 5], 'DC': [5, 0], 'DD': [1, 1]},
            aggregate='sum'
        )
        
        result1 = group_tournament(
            strategies=strategies,
            rounds=50,
            seed=777,
            payoff_model=payoff_model
        )
        
        result2 = group_tournament(
            strategies=strategies,
            rounds=50,
            seed=777,
            payoff_model=payoff_model
        )
        
        # Results should be identical
        assert result1['leaderboard'] == result2['leaderboard']
    
    def test_public_goods_payoff_model(self):
        """Test tournament with public goods payoff model"""
        strategies = [
            {'name': 'C1', 'code': '', 'func': always_cooperate},
            {'name': 'C2', 'code': '', 'func': always_cooperate},
            {'name': 'D1', 'code': '', 'func': always_defect}
        ]
        
        payoff_model = PublicGoodsPayoff(b=2.0, c=1.0)
        
        result = group_tournament(
            strategies=strategies,
            rounds=100,
            seed=42,
            payoff_model=payoff_model
        )
        
        assert 'leaderboard' in result
        assert len(result['leaderboard']) == 3
        
        # Defector should have highest points in public goods game
        # (gets benefit without paying cost)
        leaderboard_sorted = sorted(result['leaderboard'], key=lambda x: x['total_points'], reverse=True)
        assert leaderboard_sorted[0]['name'] == 'D1'
    
    def test_group_size_larger_than_n_strategies(self):
        """If group_size >= n_strategies, should create one group"""
        strategies = [
            {'name': 'C', 'code': '', 'func': always_cooperate},
            {'name': 'D', 'code': '', 'func': always_defect}
        ]
        
        payoff_model = PairwiseMatrixPayoff(
            payoff_matrix={'CC': [3, 3], 'CD': [0, 5], 'DC': [5, 0], 'DD': [1, 1]},
            aggregate='sum'
        )
        
        result = group_tournament(
            strategies=strategies,
            rounds=50,
            group_size=10,  # Larger than n_strategies
            seed=42,
            payoff_model=payoff_model
        )
        
        # Should have only 1 match
        assert len(result['matches']) == 1
        assert len(result['leaderboard']) == 2

    def test_group_tournament_rejects_empty_modes(self):
        strategies = [
            {'name': 'C', 'code': '', 'func': always_cooperate},
            {'name': 'D', 'code': '', 'func': always_defect},
        ]

        payoff_model = PairwiseMatrixPayoff(
            payoff_matrix={'CC': [3, 3], 'CD': [0, 5], 'DC': [5, 0], 'DD': [1, 1]},
            aggregate='sum'
        )

        with pytest.raises(ValueError):
            group_tournament(
                strategies=strategies,
                rounds=10,
                seed=1,
                payoff_model=payoff_model,
                modes=[],
            )

    def test_group_tournament_rejects_duplicate_modes(self):
        strategies = [
            {'name': 'C', 'code': '', 'func': always_cooperate},
            {'name': 'D', 'code': '', 'func': always_defect},
        ]

        payoff_model = PairwiseMatrixPayoff(
            payoff_matrix={'CC': [3, 3], 'CD': [0, 5], 'DC': [5, 0], 'DD': [1, 1]},
            aggregate='sum'
        )

        with pytest.raises(ValueError):
            group_tournament(
                strategies=strategies,
                rounds=10,
                seed=1,
                payoff_model=payoff_model,
                modes=['standard', 'standard'],
            )
    
    def test_uneven_group_split(self):
        """Test that uneven splits are handled (e.g., 5 players, group_size=3)"""
        strategies = [
            {'name': f'P{i}', 'code': '', 'func': always_cooperate if i % 2 == 0 else always_defect}
            for i in range(5)
        ]
        
        payoff_model = PairwiseMatrixPayoff(
            payoff_matrix={'CC': [3, 3], 'CD': [0, 5], 'DC': [5, 0], 'DD': [1, 1]},
            aggregate='sum'
        )
        
        result = group_tournament(
            strategies=strategies,
            rounds=50,
            group_size=3,  # Will create groups of 3 and 2
            seed=42,
            payoff_model=payoff_model
        )
        
        # Should have 2 groups
        assert len(result['matches']) == 2
        
        # All 5 players should be in leaderboard
        assert len(result['leaderboard']) == 5
    
    def test_weights_passed_to_tournament_info(self):
        """Test that weights are passed through to tournament_info"""
        strategies = [
            {'name': 'C', 'code': '', 'func': always_cooperate},
            {'name': 'D', 'code': '', 'func': always_defect}
        ]
        
        payoff_model = PairwiseMatrixPayoff(
            payoff_matrix={'CC': [3, 3], 'CD': [0, 5], 'DC': [5, 0], 'DD': [1, 1]},
            aggregate='sum'
        )
        
        weights = {'cooperation': 0.5, 'win_rate': 0.3, 'points': 0.2}
        
        result = group_tournament(
            strategies=strategies,
            rounds=50,
            seed=42,
            payoff_model=payoff_model,
            weights=weights
        )
        
        assert 'tournament_info' in result
        assert 'weights' in result['tournament_info']
        assert result['tournament_info']['weights'] == weights
    
    def test_mode_parameter_accepted(self):
        """Test that mode parameter is accepted (currently only 'standard')"""
        strategies = [
            {'name': 'C', 'code': '', 'func': always_cooperate},
            {'name': 'D', 'code': '', 'func': always_defect}
        ]
        
        payoff_model = PairwiseMatrixPayoff(
            payoff_matrix={'CC': [3, 3], 'CD': [0, 5], 'DC': [5, 0], 'DD': [1, 1]},
            aggregate='sum'
        )
        
        result = group_tournament(
            strategies=strategies,
            rounds=50,
            seed=42,
            payoff_model=payoff_model,
            mode='standard'
        )
        
        assert 'tournament_info' in result
        assert result['tournament_info']['mode'] == 'standard'

    def test_multimode_standard_and_discounted_averages_total_points(self):
        """When modes are provided, total_points should be averaged across modes deterministically."""
        strategies = [
            {'name': 'C', 'code': '', 'func': always_cooperate},
            {'name': 'D', 'code': '', 'func': always_defect},
        ]

        payoff_model = PairwiseMatrixPayoff(
            payoff_matrix={'CC': [3, 3], 'CD': [0, 5], 'DC': [5, 0], 'DD': [1, 1]},
            aggregate='sum',
        )

        rounds = 10
        discount_factor = 0.9

        result = group_tournament(
            strategies=strategies,
            rounds=rounds,
            seed=42,
            payoff_model=payoff_model,
            modes=['standard', 'discounted'],
            discount_factor=discount_factor,
        )

        assert 'tournament_info' in result
        assert result['tournament_info'].get('modes') == ['standard', 'discounted']

        leaderboard = {e['name']: e for e in result['leaderboard']}

        # In each round: C gets 0, D gets 5.
        standard_points_d = 5 * rounds
        discounted_points_d = 5 * sum(discount_factor ** t for t in range(rounds))
        expected_avg_d = (standard_points_d + discounted_points_d) / 2

        assert leaderboard['C']['total_points'] == pytest.approx(0.0, abs=1e-9)
        assert leaderboard['D']['total_points'] == pytest.approx(expected_avg_d, rel=1e-9)

    def test_random_mode_fixed_random_rounds_none_is_tournament_level_and_deterministic(self):
        """Random mode with fixed_random_rounds=None should use one tournament-level fixed round count."""
        strategies = [
            {'name': f'R{i}', 'code': '', 'func': random_strategy}
            for i in range(6)
        ]

        payoff_model = PairwiseMatrixPayoff(
            payoff_matrix={'CC': [3, 3], 'CD': [0, 5], 'DC': [5, 0], 'DD': [1, 1]},
            aggregate='sum'
        )

        seed = 123
        expected_rounds = random.Random(seed).randint(100, 300)

        result1 = group_tournament(
            strategies=strategies,
            rounds=50,  # ignored in random mode when fixed_random_rounds is chosen
            group_size=3,
            seed=seed,
            payoff_model=payoff_model,
            mode='random',
            fixed_random_rounds=None,
        )

        result2 = group_tournament(
            strategies=strategies,
            rounds=50,
            group_size=3,
            seed=seed,
            payoff_model=payoff_model,
            mode='random',
            fixed_random_rounds=None,
        )

        # Deterministic overall
        assert result1['leaderboard'] == result2['leaderboard']

        # All group matches should play the same fixed number of rounds,
        # chosen once per tournament from the tournament seed.
        rounds_played_1 = [m['results']['rounds_played'] for m in result1['matches']]
        assert rounds_played_1
        assert all(r == expected_rounds for r in rounds_played_1)
        assert 100 <= expected_rounds <= 300
