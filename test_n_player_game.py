"""
Tests for N-player game engine (Phase 3).

Tests the game_n() function with various player counts, strategies,
and backward compatibility with 3-arg legacy strategies.
"""

import random
import pytest
from payoff_models import PairwiseMatrixPayoff
from n_player_simulation import game_n, call_strategy


# Test strategies with 3-arg signature (legacy)
def always_cooperate_3arg(last_opponent_move, my_history, opponent_history):
    """Always cooperate - 3-arg signature"""
    return 'C'


def always_defect_3arg(last_opponent_move, my_history, opponent_history):
    """Always defect - 3-arg signature"""
    return 'D'


def tit_for_tat_3arg(last_opponent_move, my_history, opponent_history):
    """Tit-for-tat - 3-arg signature"""
    if not opponent_history:
        return 'C'
    return opponent_history[-1]


# Test strategies with 4-arg signature (N-player aware)
def always_cooperate_4arg(last_moves_by_opponent, my_history, opponents_histories, meta):
    """Always cooperate - 4-arg N-player signature"""
    return 'C'


def always_defect_4arg(last_moves_by_opponent, my_history, opponents_histories, meta):
    """Always defect - 4-arg N-player signature"""
    return 'D'


def majority_defector_4arg(last_moves_by_opponent, my_history, opponents_histories, meta):
    """Defect if majority of opponents defected last round - 4-arg signature"""
    if not last_moves_by_opponent:
        return 'C'
    defect_count = sum(1 for move in last_moves_by_opponent if move == 'D')
    return 'D' if defect_count > len(last_moves_by_opponent) / 2 else 'C'


def random_strategy_3arg(last_opponent_move, my_history, opponent_history):
    """Randomly cooperate or defect - 3-arg signature"""
    # Use the RNG from meta if available, else use random.choice
    return random.choice(['C', 'D'])


def random_strategy_4arg(last_moves_by_opponent, my_history, opponents_histories, meta):
    """Randomly cooperate or defect using RNG from meta - 4-arg signature"""
    rng = meta.get('rng', random.Random())
    return rng.choice(['C', 'D'])


class TestCallStrategy:
    """Tests for call_strategy signature negotiation."""
    
    def test_call_3arg_strategy_first_round(self):
        """Test calling 3-arg strategy on first round."""
        result = call_strategy(
            always_cooperate_3arg,
            last_moves=[],
            my_history=[],
            opponents_histories=[[], []],
            meta={}
        )
        assert result == 'C'
    
    def test_signature_negotiation_with_wrapped_callable(self):
        """Test signature negotiation with wrapped functions and lambdas."""
        # Test with lambda (3-arg style)
        lambda_strat_3 = lambda last_opp, my_hist, opp_hist: 'C'
        result = call_strategy(
            lambda_strat_3,
            last_moves=['C'],
            my_history=[],
            opponents_histories=[['C']],
            meta={}
        )
        assert result == 'C'
        
        # Test with lambda (4-arg style)
        lambda_strat_4 = lambda last, my, opps, meta: 'D'
        result = call_strategy(
            lambda_strat_4,
            last_moves=['C'],
            my_history=[],
            opponents_histories=[['C']],
            meta={}
        )
        assert result == 'D'
    
    def test_call_3arg_strategy_aggregated_view(self):
        """Test 3-arg strategy receives aggregated opponent view."""
        # Simulate round 2: opponents played ['C', 'D']
        result = call_strategy(
            tit_for_tat_3arg,
            last_moves=['C', 'D'],
            my_history=['C'],
            opponents_histories=[['C'], ['D']],
            meta={}
        )
        # Aggregated: last_opponent_move = 'D' (any defected)
        # opponent_history = ['D'] (any defected in round 1)
        # Tit-for-tat mirrors last move, so should return 'D'
        assert result == 'D'
    
    def test_call_3arg_strategy_all_coop(self):
        """Test 3-arg strategy when all opponents cooperated."""
        result = call_strategy(
            tit_for_tat_3arg,
            last_moves=['C', 'C'],
            my_history=['C'],
            opponents_histories=[['C'], ['C']],
            meta={}
        )
        # Aggregated: last_opponent_move = 'C' (no defect)
        # Should return 'C'
        assert result == 'C'
    
    def test_call_4arg_strategy(self):
        """Test calling 4-arg N-player aware strategy."""
        result = call_strategy(
            majority_defector_4arg,
            last_moves=['C', 'D', 'D'],
            my_history=['C'],
            opponents_histories=[['C'], ['D'], ['D']],
            meta={'n_players': 4}
        )
        # 2 out of 3 defected, so should return 'D'
        assert result == 'D'
    
    def test_call_4arg_strategy_first_round(self):
        """Test calling 4-arg strategy on first round."""
        result = call_strategy(
            always_cooperate_4arg,
            last_moves=[],
            my_history=[],
            opponents_histories=[[], []],
            meta={}
        )
        assert result == 'C'


class TestGameNBasics:
    """Basic tests for game_n function."""
    
    def test_n2_all_cooperate(self):
        """Test N=2 with both players always cooperating."""
        strategies = [always_cooperate_3arg, always_cooperate_3arg]
        payoff_matrix = {
            'CC': [3, 3],
            'CD': [0, 5],
            'DC': [5, 0],
            'DD': [1, 1]
        }
        payoff_model = PairwiseMatrixPayoff(payoff_matrix)
        
        result = game_n(
            strategies=strategies,
            rounds=10,
            payoff_model=payoff_model,
            seed=42
        )
        
        assert result['rounds_played'] == 10
        assert result['match_complete'] is True
        assert len(result['players']) == 2
        assert result['coop_counts_by_player'][0] == 10
        assert result['coop_counts_by_player'][1] == 10
        assert result['defect_counts_by_player'][0] == 0
        assert result['defect_counts_by_player'][1] == 0
        # Both always cooperate: 3 points per round for 10 rounds
        assert result['total_points_by_player'][0] == 30.0
        assert result['total_points_by_player'][1] == 30.0
    
    def test_n2_all_defect(self):
        """Test N=2 with both players always defecting."""
        strategies = [always_defect_3arg, always_defect_3arg]
        payoff_matrix = {
            'CC': [3, 3],
            'CD': [0, 5],
            'DC': [5, 0],
            'DD': [1, 1]
        }
        payoff_model = PairwiseMatrixPayoff(payoff_matrix)
        
        result = game_n(
            strategies=strategies,
            rounds=10,
            payoff_model=payoff_model,
            seed=42
        )
        
        assert result['rounds_played'] == 10
        assert result['coop_counts_by_player'][0] == 0
        assert result['coop_counts_by_player'][1] == 0
        assert result['defect_counts_by_player'][0] == 10
        assert result['defect_counts_by_player'][1] == 10
        # Both always defect: 1 point per round for 10 rounds
        assert result['total_points_by_player'][0] == 10.0
        assert result['total_points_by_player'][1] == 10.0
    
    def test_n2_mixed_strategies(self):
        """Test N=2 with one cooperator and one defector."""
        strategies = [always_cooperate_3arg, always_defect_3arg]
        payoff_matrix = {
            'CC': [3, 3],
            'CD': [0, 5],
            'DC': [5, 0],
            'DD': [1, 1]
        }
        payoff_model = PairwiseMatrixPayoff(payoff_matrix)
        
        result = game_n(
            strategies=strategies,
            rounds=10,
            payoff_model=payoff_model,
            seed=42
        )
        
        assert result['rounds_played'] == 10
        assert result['coop_counts_by_player'][0] == 10
        assert result['coop_counts_by_player'][1] == 0
        assert result['defect_counts_by_player'][0] == 0
        assert result['defect_counts_by_player'][1] == 10
        # Player 0 (C) vs Player 1 (D): 0 points per round
        # Player 1 (D) vs Player 0 (C): 5 points per round
        assert result['total_points_by_player'][0] == 0.0
        assert result['total_points_by_player'][1] == 50.0
    
    def test_n3_all_cooperate(self):
        """Test N=3 with all players cooperating."""
        strategies = [always_cooperate_3arg, always_cooperate_3arg, always_cooperate_3arg]
        payoff_matrix = {
            'CC': [3, 3],
            'CD': [0, 5],
            'DC': [5, 0],
            'DD': [1, 1]
        }
        payoff_model = PairwiseMatrixPayoff(payoff_matrix)
        
        result = game_n(
            strategies=strategies,
            rounds=10,
            payoff_model=payoff_model,
            seed=42
        )
        
        assert result['rounds_played'] == 10
        assert len(result['players']) == 3
        assert all(result['coop_counts_by_player'][i] == 10 for i in range(3))
        assert all(result['defect_counts_by_player'][i] == 0 for i in range(3))
        # Each player plays against 2 others, both cooperate: 3+3=6 per round
        assert all(result['total_points_by_player'][i] == 60.0 for i in range(3))
    
    def test_n3_all_defect(self):
        """Test N=3 with all players defecting."""
        strategies = [always_defect_3arg, always_defect_3arg, always_defect_3arg]
        payoff_matrix = {
            'CC': [3, 3],
            'CD': [0, 5],
            'DC': [5, 0],
            'DD': [1, 1]
        }
        payoff_model = PairwiseMatrixPayoff(payoff_matrix)
        
        result = game_n(
            strategies=strategies,
            rounds=10,
            payoff_model=payoff_model,
            seed=42
        )
        
        assert result['rounds_played'] == 10
        assert all(result['coop_counts_by_player'][i] == 0 for i in range(3))
        assert all(result['defect_counts_by_player'][i] == 10 for i in range(3))
        # Each player plays against 2 others, both defect: 1+1=2 per round
        assert all(result['total_points_by_player'][i] == 20.0 for i in range(3))
    
    def test_n3_mixed_strategies(self):
        """Test N=3 with mixed strategies."""
        strategies = [
            always_cooperate_3arg,  # Player 0
            always_defect_3arg,     # Player 1
            always_cooperate_3arg   # Player 2
        ]
        payoff_matrix = {
            'CC': [3, 3],
            'CD': [0, 5],
            'DC': [5, 0],
            'DD': [1, 1]
        }
        payoff_model = PairwiseMatrixPayoff(payoff_matrix)
        
        result = game_n(
            strategies=strategies,
            rounds=10,
            payoff_model=payoff_model,
            seed=42
        )
        
        assert result['rounds_played'] == 10
        assert result['coop_counts_by_player'][0] == 10
        assert result['coop_counts_by_player'][1] == 0
        assert result['coop_counts_by_player'][2] == 10
        
        # Player 0 (C): plays vs Player 1 (D)=0 + Player 2 (C)=3 = 3 per round
        # Player 1 (D): plays vs Player 0 (C)=5 + Player 2 (C)=5 = 10 per round
        # Player 2 (C): plays vs Player 0 (C)=3 + Player 1 (D)=0 = 3 per round
        assert result['total_points_by_player'][0] == 30.0
        assert result['total_points_by_player'][1] == 100.0
        assert result['total_points_by_player'][2] == 30.0


class TestGameNDeterminism:
    """Test deterministic behavior of game_n."""
    
    def test_same_seed_same_results(self):
        """Test that same seed produces same results."""
        strategies = [always_cooperate_3arg, always_defect_3arg]
        payoff_matrix = {
            'CC': [3, 3],
            'CD': [0, 5],
            'DC': [5, 0],
            'DD': [1, 1]
        }
        payoff_model = PairwiseMatrixPayoff(payoff_matrix)
        
        result1 = game_n(
            strategies=strategies,
            rounds=10,
            payoff_model=payoff_model,
            seed=123
        )
        
        result2 = game_n(
            strategies=strategies,
            rounds=10,
            payoff_model=payoff_model,
            seed=123
        )
        
        assert result1['total_points_by_player'] == result2['total_points_by_player']
        assert result1['coop_counts_by_player'] == result2['coop_counts_by_player']
        assert result1['rounds_played'] == result2['rounds_played']
    
    def test_determinism_with_random_strategy(self):
        """Test that strategies using randomness are deterministic with same seed."""
        strategies = [random_strategy_4arg, always_cooperate_3arg]
        payoff_matrix = {
            'CC': [3, 3],
            'CD': [0, 5],
            'DC': [5, 0],
            'DD': [1, 1]
        }
        payoff_model = PairwiseMatrixPayoff(payoff_matrix)
        
        result1 = game_n(
            strategies=strategies,
            rounds=20,
            payoff_model=payoff_model,
            seed=456
        )
        
        result2 = game_n(
            strategies=strategies,
            rounds=20,
            payoff_model=payoff_model,
            seed=456
        )
        
        # With same seed, random strategy should make identical choices
        assert result1['total_points_by_player'] == result2['total_points_by_player']
        assert result1['coop_counts_by_player'] == result2['coop_counts_by_player']
        assert result1['defect_counts_by_player'] == result2['defect_counts_by_player']
    
    def test_different_seed_different_results(self):
        """Test that different seeds produce different results with random strategies."""
        strategies = [random_strategy_4arg, always_cooperate_3arg]
        payoff_matrix = {
            'CC': [3, 3],
            'CD': [0, 5],
            'DC': [5, 0],
            'DD': [1, 1]
        }
        payoff_model = PairwiseMatrixPayoff(payoff_matrix)
        
        result1 = game_n(
            strategies=strategies,
            rounds=20,
            payoff_model=payoff_model,
            seed=111
        )
        
        result2 = game_n(
            strategies=strategies,
            rounds=20,
            payoff_model=payoff_model,
            seed=999
        )
        
        # With different seeds, results should likely differ
        # (not guaranteed but highly probable with 20 rounds)
        assert result1['coop_counts_by_player'][0] != result2['coop_counts_by_player'][0] or \
               result1['defect_counts_by_player'][0] != result2['defect_counts_by_player'][0]


class TestGameNModes:
    """Tests for N-player mode mechanics parity (standard/discounted/stochastic/random)."""

    def test_game_n_deterministic_across_runs_per_mode_seeded(self):
        """Same seed should yield identical results within each mode."""
        strategies = [random_strategy_4arg, always_cooperate_4arg]
        payoff_matrix = {
            'CC': [3, 3],
            'CD': [0, 5],
            'DC': [5, 0],
            'DD': [1, 1]
        }
        payoff_model = PairwiseMatrixPayoff(payoff_matrix)

        for mode in ["standard", "discounted", "stochastic", "random"]:
            kwargs = {}
            if mode == "random":
                kwargs["fixed_random_rounds"] = 15
            if mode == "discounted":
                kwargs["discount_factor"] = 0.9
            if mode == "stochastic":
                kwargs["stochastic_prob"] = 0.8

            result1 = game_n(
                strategies=strategies,
                rounds=50,
                payoff_model=payoff_model,
                seed=2024,
                mode=mode,
                **kwargs
            )
            result2 = game_n(
                strategies=strategies,
                rounds=50,
                payoff_model=payoff_model,
                seed=2024,
                mode=mode,
                **kwargs
            )

            assert result1['total_points_by_player'] == result2['total_points_by_player']
            assert result1['coop_counts_by_player'] == result2['coop_counts_by_player']
            assert result1['defect_counts_by_player'] == result2['defect_counts_by_player']
            assert result1['rounds_played'] == result2['rounds_played']

    def test_game_n_discounted_differs_from_standard(self):
        """Discounted mode should yield different totals than standard."""
        strategies = [always_cooperate_3arg, always_cooperate_3arg]
        payoff_matrix = {
            'CC': [3, 3],
            'CD': [0, 5],
            'DC': [5, 0],
            'DD': [1, 1]
        }
        payoff_model = PairwiseMatrixPayoff(payoff_matrix)

        standard = game_n(
            strategies=strategies,
            rounds=10,
            payoff_model=payoff_model,
            seed=7,
            mode="standard"
        )
        discounted = game_n(
            strategies=strategies,
            rounds=10,
            payoff_model=payoff_model,
            seed=7,
            mode="discounted",
            discount_factor=0.5
        )

        assert discounted['total_points_by_player'][0] != standard['total_points_by_player'][0]
        assert discounted['total_points_by_player'][0] < standard['total_points_by_player'][0]

    def test_game_n_stochastic_terminates_early_deterministically_with_seed(self):
        """Stochastic mode should terminate early in a seed-deterministic way."""
        strategies = [always_cooperate_3arg, always_cooperate_3arg]
        payoff_matrix = {
            'CC': [3, 3],
            'CD': [0, 5],
            'DC': [5, 0],
            'DD': [1, 1]
        }
        payoff_model = PairwiseMatrixPayoff(payoff_matrix)

        result = game_n(
            strategies=strategies,
            rounds=50,
            payoff_model=payoff_model,
            seed=123,
            mode="stochastic",
            stochastic_prob=0.0
        )

        assert result['rounds_played'] < 50
        assert result['rounds_played'] == 1

    def test_game_n_random_fixed_rounds_respected(self):
        """Random mode should respect fixed_random_rounds when provided."""
        strategies = [always_cooperate_3arg, always_cooperate_3arg]
        payoff_matrix = {
            'CC': [3, 3],
            'CD': [0, 5],
            'DC': [5, 0],
            'DD': [1, 1]
        }
        payoff_model = PairwiseMatrixPayoff(payoff_matrix)

        result = game_n(
            strategies=strategies,
            rounds=200,
            payoff_model=payoff_model,
            seed=42,
            mode="random",
            fixed_random_rounds=7
        )

        assert result['rounds_played'] == 7
        assert result['coop_counts_by_player'][0] == 7
        assert result['coop_counts_by_player'][1] == 7


class TestGameNLegacyCompatibility:
    """Test backward compatibility with 3-arg strategies."""
    
    def test_3arg_strategy_in_n_player(self):
        """Test that 3-arg strategies work in N-player matches."""
        # Use tit-for-tat which depends on opponent history
        strategies = [
            tit_for_tat_3arg,
            always_defect_3arg,
            always_cooperate_3arg
        ]
        payoff_matrix = {
            'CC': [3, 3],
            'CD': [0, 5],
            'DC': [5, 0],
            'DD': [1, 1]
        }
        payoff_model = PairwiseMatrixPayoff(payoff_matrix)
        
        result = game_n(
            strategies=strategies,
            rounds=5,
            payoff_model=payoff_model,
            seed=42
        )
        
        # Tit-for-tat should start with C, then see opponents as 'D'
        # (because player 1 defects), so should defect after round 1
        assert result['rounds_played'] == 5
        assert result['coop_counts_by_player'][0] == 1  # TFT: C then D
        assert result['defect_counts_by_player'][0] == 4
        assert result['coop_counts_by_player'][1] == 0  # Always D
        assert result['coop_counts_by_player'][2] == 5  # Always C
    
    def test_mixed_3arg_4arg_strategies(self):
        """Test mixing 3-arg and 4-arg strategies."""
        strategies = [
            always_cooperate_3arg,   # 3-arg
            always_defect_4arg,      # 4-arg
            majority_defector_4arg   # 4-arg
        ]
        payoff_matrix = {
            'CC': [3, 3],
            'CD': [0, 5],
            'DC': [5, 0],
            'DD': [1, 1]
        }
        payoff_model = PairwiseMatrixPayoff(payoff_matrix)
        
        result = game_n(
            strategies=strategies,
            rounds=10,
            payoff_model=payoff_model,
            seed=42
        )
        
        assert result['rounds_played'] == 10
        assert result['match_complete'] is True
        # Player 0 (3-arg always_cooperate) always cooperates
        assert result['coop_counts_by_player'][0] == 10
        assert result['defect_counts_by_player'][0] == 0
        # Player 1 (4-arg always_defect) always defects
        assert result['coop_counts_by_player'][1] == 0
        assert result['defect_counts_by_player'][1] == 10
        # Player 2 (4-arg majority_defector) starts with C, then sees:
        # - Player 0: C, Player 1: D -> 1 out of 2 defected (50%), not strict majority
        # Since majority_defector uses > not >=, it cooperates with 50-50 split
        assert result['coop_counts_by_player'][2] == 10  # Always cooperates (no strict majority)
        assert result['defect_counts_by_player'][2] == 0
    
    def test_mixed_3arg_4arg_with_actual_majority(self):
        """Test mixing 3-arg and 4-arg strategies where majority defector actually defects."""
        strategies = [
            always_cooperate_3arg,   # 3-arg Player 0
            always_defect_4arg,      # 4-arg Player 1
            always_defect_4arg,      # 4-arg Player 2
            majority_defector_4arg   # 4-arg Player 3
        ]
        payoff_matrix = {
            'CC': [3, 3],
            'CD': [0, 5],
            'DC': [5, 0],
            'DD': [1, 1]
        }
        payoff_model = PairwiseMatrixPayoff(payoff_matrix)
        
        result = game_n(
            strategies=strategies,
            rounds=10,
            payoff_model=payoff_model,
            seed=42
        )
        
        assert result['rounds_played'] == 10
        assert result['match_complete'] is True
        # Player 3 (majority_defector) starts with C in round 1, then sees:
        # - Player 0: C, Player 1: D, Player 2: D -> 2 out of 3 defected (>50%)
        # So should defect from round 2 onwards
        assert result['coop_counts_by_player'][3] == 1  # Only first round
        assert result['defect_counts_by_player'][3] == 9  # Remaining rounds


class TestGameNTournamentInfo:
    """Test that TOURNAMENT_INFO is properly provided to strategies."""
    
    def test_tournament_info_provided(self):
        """Test that tournament_info is available in result."""
        strategies = [always_cooperate_3arg, always_defect_3arg]
        payoff_matrix = {
            'CC': [3, 3],
            'CD': [0, 5],
            'DC': [5, 0],
            'DD': [1, 1]
        }
        payoff_model = PairwiseMatrixPayoff(payoff_matrix)
        
        tournament_info = {
            'weights': [1.0, 0.9],
            'test_key': 'test_value'
        }
        
        result = game_n(
            strategies=strategies,
            rounds=10,
            payoff_model=payoff_model,
            seed=42,
            tournament_info=tournament_info
        )
        
        # Tournament info should be in result
        assert 'tournament_info' in result
        assert result['tournament_info']['format'] == 'n_player'
        assert result['tournament_info']['n_players'] == 2
        assert 'test_key' in result['tournament_info']
    
    def test_tournament_info_not_mutated(self):
        """Test that input tournament_info dict is not mutated."""
        strategies = [always_cooperate_3arg, always_defect_3arg]
        payoff_matrix = {
            'CC': [3, 3],
            'CD': [0, 5],
            'DC': [5, 0],
            'DD': [1, 1]
        }
        payoff_model = PairwiseMatrixPayoff(payoff_matrix)
        
        tournament_info = {
            'weights': [1.0, 0.9],
            'test_key': 'test_value'
        }
        
        # Make a copy to check if original is mutated
        original_keys = set(tournament_info.keys())
        
        result = game_n(
            strategies=strategies,
            rounds=10,
            payoff_model=payoff_model,
            seed=42,
            tournament_info=tournament_info
        )
        
        # Original tournament_info should not have new keys added
        assert set(tournament_info.keys()) == original_keys
        assert 'format' not in tournament_info
        assert 'n_players' not in tournament_info
        
        # Result should have all the info
        assert result['tournament_info']['format'] == 'n_player'
        assert result['tournament_info']['n_players'] == 2
        assert result['tournament_info']['test_key'] == 'test_value'
