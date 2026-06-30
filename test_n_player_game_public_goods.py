"""
Tests for N-player game engine with PublicGoodsPayoff (Phase 3).

Tests game_n() function with PublicGoodsPayoff model to verify
correct payoff calculations in N-player scenarios.
"""

import random
import pytest
from payoff_models import PublicGoodsPayoff
from n_player_simulation import game_n


# Test strategies
def always_cooperate(last_opponent_move, my_history, opponent_history):
    """Always cooperate - 3-arg signature"""
    return 'C'


def always_defect(last_opponent_move, my_history, opponent_history):
    """Always defect - 3-arg signature"""
    return 'D'


def conditional_cooperator(last_opponent_move, my_history, opponent_history):
    """Cooperate if opponent cooperated last round - 3-arg signature"""
    if not opponent_history:
        return 'C'
    return 'C' if opponent_history[-1] == 'C' else 'D'


class TestGameNPublicGoods:
    """Tests for game_n with PublicGoodsPayoff."""
    
    def test_n3_all_cooperate(self):
        """Test N=3 with all cooperating, b=2, c=1."""
        strategies = [always_cooperate, always_cooperate, always_cooperate]
        payoff_model = PublicGoodsPayoff(b=2.0, c=1.0)
        
        result = game_n(
            strategies=strategies,
            rounds=10,
            payoff_model=payoff_model,
            seed=42
        )
        
        assert result['rounds_played'] == 10
        assert all(result['coop_counts_by_player'][i] == 10 for i in range(3))
        
        # All cooperate: k=3, B(k)=2*3=6, payoff=6-1=5 per round
        # Total for 10 rounds: 50
        assert all(result['total_points_by_player'][i] == 50.0 for i in range(3))
    
    def test_n3_all_defect(self):
        """Test N=3 with all defecting, b=2, c=1."""
        strategies = [always_defect, always_defect, always_defect]
        payoff_model = PublicGoodsPayoff(b=2.0, c=1.0)
        
        result = game_n(
            strategies=strategies,
            rounds=10,
            payoff_model=payoff_model,
            seed=42
        )
        
        assert result['rounds_played'] == 10
        assert all(result['defect_counts_by_player'][i] == 10 for i in range(3))
        
        # All defect: k=0, B(k)=2*0=0, payoff=0 per round
        assert all(result['total_points_by_player'][i] == 0.0 for i in range(3))
    
    def test_n3_mixed_ccd(self):
        """Test N=3 with [C,C,D], b=2, c=1."""
        strategies = [always_cooperate, always_cooperate, always_defect]
        payoff_model = PublicGoodsPayoff(b=2.0, c=1.0)
        
        result = game_n(
            strategies=strategies,
            rounds=10,
            payoff_model=payoff_model,
            seed=42
        )
        
        assert result['rounds_played'] == 10
        
        # k=2 cooperators per round
        # Player 0 (C): 2*2 - 1 = 3 per round → 30 total
        # Player 1 (C): 2*2 - 1 = 3 per round → 30 total  
        # Player 2 (D): 2*2 = 4 per round → 40 total
        assert result['total_points_by_player'][0] == 30.0
        assert result['total_points_by_player'][1] == 30.0
        assert result['total_points_by_player'][2] == 40.0
    
    def test_n5_mixed_strategies(self):
        """Test N=5 with mixed cooperation levels."""
        strategies = [
            always_cooperate,  # Player 0
            always_cooperate,  # Player 1
            always_cooperate,  # Player 2
            always_defect,     # Player 3
            always_defect      # Player 4
        ]
        payoff_model = PublicGoodsPayoff(b=2.0, c=1.0)
        
        result = game_n(
            strategies=strategies,
            rounds=10,
            payoff_model=payoff_model,
            seed=42
        )
        
        assert result['rounds_played'] == 10
        
        # k=3 cooperators per round
        # B(k) = 2*3 = 6
        # Cooperators: 6 - 1 = 5 per round → 50 total
        # Defectors: 6 per round → 60 total
        assert result['total_points_by_player'][0] == 50.0
        assert result['total_points_by_player'][1] == 50.0
        assert result['total_points_by_player'][2] == 50.0
        assert result['total_points_by_player'][3] == 60.0
        assert result['total_points_by_player'][4] == 60.0
    
    def test_n5_varying_b_and_c(self):
        """Test N=5 with different b and c values."""
        strategies = [always_cooperate] * 3 + [always_defect] * 2
        payoff_model = PublicGoodsPayoff(b=3.0, c=2.0)
        
        result = game_n(
            strategies=strategies,
            rounds=10,
            payoff_model=payoff_model,
            seed=42
        )
        
        # k=3 cooperators per round
        # B(k) = 3*3 = 9
        # Cooperators: 9 - 2 = 7 per round → 70 total
        # Defectors: 9 per round → 90 total
        assert result['total_points_by_player'][0] == 70.0
        assert result['total_points_by_player'][1] == 70.0
        assert result['total_points_by_player'][2] == 70.0
        assert result['total_points_by_player'][3] == 90.0
        assert result['total_points_by_player'][4] == 90.0
    
    def test_n4_single_cooperator(self):
        """Test N=4 with only one cooperator."""
        strategies = [always_cooperate, always_defect, always_defect, always_defect]
        payoff_model = PublicGoodsPayoff(b=2.0, c=1.0)
        
        result = game_n(
            strategies=strategies,
            rounds=10,
            payoff_model=payoff_model,
            seed=42
        )
        
        # k=1 cooperator per round
        # B(k) = 2*1 = 2
        # Cooperator: 2 - 1 = 1 per round → 10 total
        # Defectors: 2 per round → 20 total each
        assert result['total_points_by_player'][0] == 10.0
        assert result['total_points_by_player'][1] == 20.0
        assert result['total_points_by_player'][2] == 20.0
        assert result['total_points_by_player'][3] == 20.0
    
    def test_n2_public_goods(self):
        """Test N=2 with public goods payoff."""
        strategies = [always_cooperate, always_defect]
        payoff_model = PublicGoodsPayoff(b=2.5, c=1.0)
        
        result = game_n(
            strategies=strategies,
            rounds=10,
            payoff_model=payoff_model,
            seed=42
        )
        
        # k=1 cooperator per round
        # B(k) = 2.5*1 = 2.5
        # Cooperator: 2.5 - 1 = 1.5 per round → 15.0 total
        # Defector: 2.5 per round → 25.0 total
        assert result['total_points_by_player'][0] == 15.0
        assert result['total_points_by_player'][1] == 25.0
    
    def test_public_goods_determinism(self):
        """Test that public goods results are deterministic."""
        strategies = [always_cooperate, always_defect, always_cooperate]
        payoff_model = PublicGoodsPayoff(b=2.0, c=1.0)
        
        result1 = game_n(
            strategies=strategies,
            rounds=10,
            payoff_model=payoff_model,
            seed=999
        )
        
        result2 = game_n(
            strategies=strategies,
            rounds=10,
            payoff_model=payoff_model,
            seed=999
        )
        
        assert result1['total_points_by_player'] == result2['total_points_by_player']
        assert result1['coop_counts_by_player'] == result2['coop_counts_by_player']
    
    def test_conditional_cooperator_with_public_goods(self):
        """Test conditional cooperator strategy with public goods."""
        # Player 0: conditional (cooperates if opponents cooperated)
        # Player 1: always cooperate
        # Player 2: always defect
        strategies = [conditional_cooperator, always_cooperate, always_defect]
        payoff_model = PublicGoodsPayoff(b=2.0, c=1.0)
        
        result = game_n(
            strategies=strategies,
            rounds=5,
            payoff_model=payoff_model,
            seed=42
        )
        
        # Round 1: conditional starts with C, so [C,C,D], k=2
        # Round 2+: conditional sees 'D' in aggregated view (player 2 defects), so defects
        # So k=1 for rounds 2-5 (only player 1 cooperates)
        
        assert result['rounds_played'] == 5
        # Player 0 cooperates once, then defects
        assert result['coop_counts_by_player'][0] == 1
        assert result['defect_counts_by_player'][0] == 4
        # Player 1 always cooperates
        assert result['coop_counts_by_player'][1] == 5
        # Player 2 always defects
        assert result['defect_counts_by_player'][2] == 5
