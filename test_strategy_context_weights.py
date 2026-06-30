"""
Phase 1 Tests: Strategy Context Weights
Tests that strategies can access TOURNAMENT_INFO with weights and payoff_matrix
during tournament execution.
"""

import pytest
from app import game, round_robin_tournament


class TestStrategyContextWeights:
    """Test that strategies can read TOURNAMENT_INFO during execution"""
    
    def test_strategy_can_read_tournament_info_weights(self):
        """Strategy can access TOURNAMENT_INFO['weights'] during tournament"""
        # Strategy that reads TOURNAMENT_INFO weights
        weight_aware_strategy = """
def weight_strategy(last, my_hist, opp_hist):
    # Read weights from TOURNAMENT_INFO global
    weights = TOURNAMENT_INFO.get('weights') if 'TOURNAMENT_INFO' in globals() else None
    
    # If weights favor cooperation (cooperation > points), cooperate more
    if weights and weights.get('cooperation', 0) > weights.get('points', 1):
        return 'C'
    else:
        # Default to tit-for-tat
        if last is None:
            return 'C'
        return last
"""
        
        # Basic opponent
        tit_for_tat = """
def tft(last, my_hist, opp_hist):
    if last is None:
        return 'C'
    return last
"""
        
        # Define custom weights favoring cooperation
        # Using actual keys: win_rate, cooperation, points (not points_weight, coop_weight, defect_weight)
        weights = {
            'win_rate': 0.0,
            'cooperation': 0.7,
            'points': 0.3
        }
        
        # Run tournament with these weights
        strategies = [
            {'name': 'WeightAware', 'code': weight_aware_strategy, 'user_id': 'test1'},
            {'name': 'TitForTat', 'code': tit_for_tat, 'user_id': 'test2'}
        ]
        
        result = round_robin_tournament(strategies, weights=weights, rounds=50, seed=42)
        
        # Test should pass if TOURNAMENT_INFO is properly injected
        assert 'error' not in result
        assert 'leaderboard' in result
        assert len(result['leaderboard']) == 2
        
        # The weight-aware strategy should have received the weights
        # and biased toward cooperation (since coop_weight > points_weight)
        weight_aware_entry = next((e for e in result['leaderboard'] if e['name'] == 'WeightAware'), None)
        assert weight_aware_entry is not None
        # Should have cooperated frequently
        assert weight_aware_entry['cooperates'] > 0
    
    def test_strategy_can_read_tournament_info_payoff_matrix(self):
        """Strategy can access TOURNAMENT_INFO['payoff_matrix'] during tournament"""
        # Strategy that adapts based on payoff matrix
        payoff_aware_strategy = """
def payoff_strategy(last, my_hist, opp_hist):
    # Read payoff matrix from TOURNAMENT_INFO
    payoff = TOURNAMENT_INFO.get('payoff_matrix') if 'TOURNAMENT_INFO' in globals() else None
    
    if payoff:
        # Check if mutual cooperation gives high reward
        cc_reward = payoff.get('CC', [3, 3])[0]
        # If CC reward is >= 4, always cooperate
        if cc_reward >= 4:
            return 'C'
    
    # Default to defect
    return 'D'
"""
        
        opponent = """
def opp(last, my_hist, opp_hist):
    return 'C'
"""
        
        # Custom payoff matrix with high CC reward
        custom_payoff = {
            'CC': [5, 5],  # High reward for mutual cooperation
            'CD': [0, 6],
            'DC': [6, 0],
            'DD': [1, 1]
        }
        
        strategies = [
            {'name': 'PayoffAware', 'code': payoff_aware_strategy, 'user_id': 'test1'},
            {'name': 'AlwaysCoop', 'code': opponent, 'user_id': 'test2'}
        ]
        
        result = round_robin_tournament(
            strategies, 
            payoff_matrix=custom_payoff,
            rounds=50,
            seed=42
        )
        
        # Verify tournament ran successfully
        assert 'error' not in result
        assert 'leaderboard' in result
        
        # Strategy should have adapted based on high CC payoff and cooperated
        payoff_entry = next((e for e in result['leaderboard'] if e['name'] == 'PayoffAware'), None)
        assert payoff_entry is not None
        # With CC=5, strategy should cooperate (because cc_reward >= 4)
        assert payoff_entry['cooperates'] > 40  # Most rounds should be cooperation
    
    def test_tournament_info_contains_required_keys(self):
        """TOURNAMENT_INFO consistently contains weights and payoff_matrix"""
        # Strategy that validates TOURNAMENT_INFO structure
        validator_strategy = """
def validator(last, my_hist, opp_hist):
    # Validate TOURNAMENT_INFO exists and has required keys
    if 'TOURNAMENT_INFO' not in globals():
        raise ValueError("TOURNAMENT_INFO not in globals")
    
    info = TOURNAMENT_INFO
    if info is None:
        raise ValueError("TOURNAMENT_INFO is None")
    
    if 'weights' not in info:
        raise ValueError("weights not in TOURNAMENT_INFO")
    
    if 'payoff_matrix' not in info:
        raise ValueError("payoff_matrix not in TOURNAMENT_INFO")
    
    # Return valid move
    return 'C'
"""
        
        opponent = "def s(last, my_hist, opp_hist):\n    return 'C'"
        
        strategies = [
            {'name': 'Validator', 'code': validator_strategy, 'user_id': 'test1'},
            {'name': 'Simple', 'code': opponent, 'user_id': 'test2'}
        ]
        
        # Run tournament with default weights and payoff
        result = round_robin_tournament(strategies, rounds=10, seed=42)
        
        # If TOURNAMENT_INFO is properly set up, no errors should be raised
        assert 'error' not in result or result.get('error') is None
        assert len(result['leaderboard']) == 2
    
    def test_tournament_info_reserved_keys_for_future(self):
        """TOURNAMENT_INFO structure allows for future N-player extensions"""
        # This test just verifies the structure doesn't break with reserved keys
        strategy = """
def s(last, my_hist, opp_hist):
    # Access TOURNAMENT_INFO safely
    info = TOURNAMENT_INFO if 'TOURNAMENT_INFO' in globals() else {}
    
    # These keys are reserved for future use
    # format = info.get('format', '2-player')  # Future: '2-player', 'n-player', etc
    # n_players = info.get('n_players', 2)     # Future: number of players
    # payoff_model = info.get('payoff_model', 'standard')  # Future: payoff model type
    
    return 'C'
"""
        
        strategies = [
            {'name': 'S1', 'code': strategy, 'user_id': 'test1'},
            {'name': 'S2', 'code': strategy, 'user_id': 'test2'}
        ]
        
        result = round_robin_tournament(strategies, rounds=10, seed=42)
        
        # Should run without errors
        assert 'error' not in result
        assert len(result['leaderboard']) == 2


class TestGameFunctionTournamentInfo:
    """Test TOURNAMENT_INFO injection at game() level"""
    
    def test_game_function_receives_tournament_info(self):
        """game() function properly injects TOURNAMENT_INFO into strategy context"""
        strategy = """
def s(last, my_hist, opp_hist):
    # Try to read TOURNAMENT_INFO
    if 'TOURNAMENT_INFO' not in globals():
        # If not present, return 'D' to signal missing context
        return 'D'
    
    info = TOURNAMENT_INFO
    if info and 'weights' in info and 'payoff_matrix' in info:
        # If properly injected, cooperate
        return 'C'
    else:
        # If keys missing, defect
        return 'D'
"""
        
        # Create tournament_info matching what round_robin_tournament creates
        # Using actual keys: win_rate, cooperation, points
        tournament_info = {
            'weights': {'win_rate': 0.0, 'cooperation': 0.0, 'points': 1.0},
            'payoff_matrix': {
                'CC': [3, 3],
                'CD': [0, 5],
                'DC': [5, 0],
                'DD': [1, 1]
            }
        }
        
        result = game(
            strategy, "PlayerA",
            strategy, "PlayerB",
            rounds=10,
            tournament_info=tournament_info,
            seed=42
        )
        
        # If TOURNAMENT_INFO is properly injected, both players cooperate every round
        assert 'error' not in result
        assert result['a_coops'] == 10
        assert result['b_coops'] == 10
    def test_default_payoff_matrix_in_tournament_info(self):
        """When payoff_matrix=None (default), TOURNAMENT_INFO should contain resolved default matrix"""
        # Strategy that validates payoff_matrix is never None in TOURNAMENT_INFO
        validator_strategy = """
def validator(last, my_hist, opp_hist):
    # Validate TOURNAMENT_INFO has actual payoff_matrix, never None
    if 'TOURNAMENT_INFO' not in globals():
        raise ValueError("TOURNAMENT_INFO not in globals")
    
    info = TOURNAMENT_INFO
    payoff = info.get('payoff_matrix')
    
    if payoff is None:
        raise ValueError("payoff_matrix is None - should be resolved default!")
    
    # Verify it has expected keys
    if not all(k in payoff for k in ['CC', 'CD', 'DC', 'DD']):
        raise ValueError("payoff_matrix missing required keys")
    
    # Verify default values
    if payoff['CC'] != [3, 3]:
        raise ValueError(f"Expected default CC=[3,3], got {payoff['CC']}")
    
    return 'C'
"""
        
        opponent = "def s(last, my_hist, opp_hist):\n    return 'C'"
        
        strategies = [
            {'name': 'Validator', 'code': validator_strategy, 'user_id': 'test1'},
            {'name': 'Simple', 'code': opponent, 'user_id': 'test2'}
        ]
        
        # Run tournament WITHOUT specifying payoff_matrix (should use default)
        result = round_robin_tournament(strategies, rounds=10, seed=42)
        
        # If payoff_matrix is properly resolved, no errors should be raised
        assert 'error' not in result or result.get('error') is None
        assert len(result['leaderboard']) == 2