"""
Tests for payoff model abstraction (Phase 2 & Phase 5).

Tests PublicGoodsPayoff, PairwiseMatrixPayoff and KCooperatorTensorPayoff implementations.
"""

import random
import pytest
from payoff_models import PayoffModel, PublicGoodsPayoff, PairwiseMatrixPayoff, KCooperatorTensorPayoff


class TestPublicGoodsPayoff:
    """Tests for PublicGoodsPayoff implementation."""
    
    def test_public_goods_linear_ccd(self):
        """Test public goods with [C,C,D], b=2, c=1 → [3, 3, 4]"""
        model = PublicGoodsPayoff(b=2.0, c=1.0)
        rng = random.Random(42)
        moves = ['C', 'C', 'D']
        payoffs = model.compute_round_payoffs(moves, rng, {})
        
        # k=2 cooperators
        # Player 0 (C): 2*2 - 1 = 3
        # Player 1 (C): 2*2 - 1 = 3
        # Player 2 (D): 2*2 = 4
        assert payoffs == [3.0, 3.0, 4.0]
    
    def test_public_goods_all_cooperate(self):
        """Test when all players cooperate."""
        model = PublicGoodsPayoff(b=2.0, c=1.0)
        rng = random.Random(42)
        moves = ['C', 'C', 'C']
        payoffs = model.compute_round_payoffs(moves, rng, {})
        
        # k=3 cooperators
        # All cooperators: 2*3 - 1 = 5
        assert payoffs == [5.0, 5.0, 5.0]
    
    def test_public_goods_all_defect(self):
        """Test when all players defect."""
        model = PublicGoodsPayoff(b=2.0, c=1.0)
        rng = random.Random(42)
        moves = ['D', 'D', 'D']
        payoffs = model.compute_round_payoffs(moves, rng, {})
        
        # k=0 cooperators
        # All defectors: 2*0 = 0
        assert payoffs == [0.0, 0.0, 0.0]
    
    def test_public_goods_two_players(self):
        """Test public goods with 2 players."""
        model = PublicGoodsPayoff(b=2.5, c=1.0)
        rng = random.Random(42)
        moves = ['C', 'D']
        payoffs = model.compute_round_payoffs(moves, rng, {})
        
        # k=1 cooperator
        # Player 0 (C): 2.5*1 - 1 = 1.5
        # Player 1 (D): 2.5*1 = 2.5
        assert payoffs == [1.5, 2.5]
    
    def test_public_goods_single_player(self):
        """Test edge case with single player."""
        model = PublicGoodsPayoff(b=2.0, c=1.0)
        rng = random.Random(42)
        
        moves = ['C']
        payoffs = model.compute_round_payoffs(moves, rng, {})
        # k=1, Player 0 (C): 2*1 - 1 = 1
        assert payoffs == [1.0]
        
        moves = ['D']
        payoffs = model.compute_round_payoffs(moves, rng, {})
        # k=0, Player 0 (D): 2*0 = 0
        assert payoffs == [0.0]
    
    def test_public_goods_determinism(self):
        """Test that same inputs produce same outputs."""
        model = PublicGoodsPayoff(b=2.0, c=1.0)
        moves = ['C', 'D', 'C', 'D']
        
        rng1 = random.Random(123)
        payoffs1 = model.compute_round_payoffs(moves, rng1, {})
        
        rng2 = random.Random(123)
        payoffs2 = model.compute_round_payoffs(moves, rng2, {})
        
        assert payoffs1 == payoffs2
    
    def test_public_goods_nonlinear_power(self):
        """Test nonlinear benefit function with power law."""
        model = PublicGoodsPayoff(
            b=2.0, 
            c=1.0, 
            nonlinear={'type': 'power', 'alpha': 1.5}
        )
        rng = random.Random(42)
        moves = ['C', 'C', 'D']
        payoffs = model.compute_round_payoffs(moves, rng, {})
        
        # k=2 cooperators
        # B(k) = b * k^alpha = 2.0 * 2^1.5 = 2.0 * 2.828... ≈ 5.657
        # Player 0 (C): 5.657 - 1 ≈ 4.657
        # Player 1 (C): 5.657 - 1 ≈ 4.657
        # Player 2 (D): 5.657
        expected_benefit = 2.0 * (2 ** 1.5)
        assert abs(payoffs[0] - (expected_benefit - 1.0)) < 0.001
        assert abs(payoffs[1] - (expected_benefit - 1.0)) < 0.001
        assert abs(payoffs[2] - expected_benefit) < 0.001


class TestPairwiseMatrixPayoff:
    """Tests for PairwiseMatrixPayoff implementation."""
    
    @pytest.fixture
    def standard_pd_matrix(self):
        """Standard Prisoner's Dilemma payoff matrix."""
        return {
            'CC': [3, 3],
            'CD': [0, 5],
            'DC': [5, 0],
            'DD': [1, 1]
        }
    
    def test_pairwise_two_players_cc(self, standard_pd_matrix):
        """Test 2-player case matches direct matrix lookup."""
        model = PairwiseMatrixPayoff(standard_pd_matrix)
        rng = random.Random(42)
        moves = ['C', 'C']
        payoffs = model.compute_round_payoffs(moves, rng, {})
        
        # Should match matrix['CC'] = [3, 3]
        assert payoffs == [3.0, 3.0]
    
    def test_pairwise_two_players_cd(self, standard_pd_matrix):
        """Test 2-player CD case."""
        model = PairwiseMatrixPayoff(standard_pd_matrix)
        rng = random.Random(42)
        moves = ['C', 'D']
        payoffs = model.compute_round_payoffs(moves, rng, {})
        
        # Should match matrix['CD'] = [0, 5]
        assert payoffs == [0.0, 5.0]
    
    def test_pairwise_two_players_dd(self, standard_pd_matrix):
        """Test 2-player DD case."""
        model = PairwiseMatrixPayoff(standard_pd_matrix)
        rng = random.Random(42)
        moves = ['D', 'D']
        payoffs = model.compute_round_payoffs(moves, rng, {})
        
        # Should match matrix['DD'] = [1, 1]
        assert payoffs == [1.0, 1.0]
    
    def test_pairwise_three_players_sum(self, standard_pd_matrix):
        """Test 3-player pairwise with sum aggregation."""
        model = PairwiseMatrixPayoff(standard_pd_matrix, aggregate='sum')
        rng = random.Random(42)
        moves = ['C', 'C', 'D']
        payoffs = model.compute_round_payoffs(moves, rng, {})
        
        # Player 0 (C) vs Player 1 (C): matrix['CC'][0] = 3
        # Player 0 (C) vs Player 2 (D): matrix['CD'][0] = 0
        # Player 0 total: 3 + 0 = 3
        
        # Player 1 (C) vs Player 0 (C): matrix['CC'][0] = 3
        # Player 1 (C) vs Player 2 (D): matrix['CD'][0] = 0
        # Player 1 total: 3 + 0 = 3
        
        # Player 2 (D) vs Player 0 (C): matrix['DC'][0] = 5
        # Player 2 (D) vs Player 1 (C): matrix['DC'][0] = 5
        # Player 2 total: 5 + 5 = 10
        
        assert payoffs == [3.0, 3.0, 10.0]
    
    def test_pairwise_three_players_avg(self, standard_pd_matrix):
        """Test 3-player pairwise with average aggregation."""
        model = PairwiseMatrixPayoff(standard_pd_matrix, aggregate='avg')
        rng = random.Random(42)
        moves = ['C', 'C', 'D']
        payoffs = model.compute_round_payoffs(moves, rng, {})
        
        # Same as sum test, but divided by (N-1) = 2
        # Player 0: (3 + 0) / 2 = 1.5
        # Player 1: (3 + 0) / 2 = 1.5
        # Player 2: (5 + 5) / 2 = 5.0
        
        assert payoffs == [1.5, 1.5, 5.0]
    
    def test_pairwise_four_players_mixed(self, standard_pd_matrix):
        """Test 4-player case with mixed moves."""
        model = PairwiseMatrixPayoff(standard_pd_matrix, aggregate='sum')
        rng = random.Random(42)
        moves = ['C', 'D', 'C', 'D']
        payoffs = model.compute_round_payoffs(moves, rng, {})
        
        # Player 0 (C) vs: 1(D)→0, 2(C)→3, 3(D)→0 = 3
        # Player 1 (D) vs: 0(C)→5, 2(C)→5, 3(D)→1 = 11
        # Player 2 (C) vs: 0(C)→3, 1(D)→0, 3(D)→0 = 3
        # Player 3 (D) vs: 0(C)→5, 1(D)→1, 2(C)→5 = 11
        
        assert payoffs == [3.0, 11.0, 3.0, 11.0]
    
    def test_pairwise_single_player(self, standard_pd_matrix):
        """Test edge case with single player."""
        model = PairwiseMatrixPayoff(standard_pd_matrix)
        rng = random.Random(42)
        moves = ['C']
        payoffs = model.compute_round_payoffs(moves, rng, {})
        
        # No opponents, payoff should be 0
        assert payoffs == [0.0]
    
    def test_pairwise_determinism(self, standard_pd_matrix):
        """Test that same inputs produce same outputs."""
        model = PairwiseMatrixPayoff(standard_pd_matrix)
        moves = ['C', 'D', 'C']

        rng1 = random.Random(456)
        payoffs1 = model.compute_round_payoffs(moves, rng1, {})

        rng2 = random.Random(456)
        payoffs2 = model.compute_round_payoffs(moves, rng2, {})

        assert payoffs1 == payoffs2


class TestKCooperatorTensorPayoff:
    """Tests for KCooperatorTensorPayoff implementation (Phase 5)."""

    def test_constructor_validation_length_mismatch(self):
        """Test that constructor rejects u_C and u_D with different lengths."""
        with pytest.raises(ValueError, match="u_C and u_D must have the same length"):
            KCooperatorTensorPayoff(u_C=[0, 1, 2], u_D=[0, 1])

    def test_constructor_validation_too_short(self):
        """Test that constructor rejects arrays that are too short."""
        with pytest.raises(ValueError, match="u_C and u_D must have at least 2 elements"):
            KCooperatorTensorPayoff(u_C=[0], u_D=[0])

    def test_compute_round_payoffs_all_cooperate(self):
        """Test payoffs when all players cooperate."""
        # 2 players: u_C[0]=0, u_C[1]=5, u_C[2]=8
        model = KCooperatorTensorPayoff(
            u_C=[0.0, 5.0, 8.0],
            u_D=[0.0, 6.0, 9.0]
        )
        rng = random.Random(42)
        moves = ['C', 'C']
        payoffs = model.compute_round_payoffs(moves, rng, {})

        # k=2 cooperators, both played C → u_C[2] = 8.0
        assert payoffs == [8.0, 8.0]

    def test_compute_round_payoffs_all_defect(self):
        """Test payoffs when all players defect."""
        model = KCooperatorTensorPayoff(
            u_C=[0.0, 5.0, 8.0],
            u_D=[0.0, 6.0, 9.0]
        )
        rng = random.Random(42)
        moves = ['D', 'D']
        payoffs = model.compute_round_payoffs(moves, rng, {})

        # k=0 cooperators, both played D → u_D[0] = 0.0
        assert payoffs == [0.0, 0.0]

    def test_compute_round_payoffs_mixed(self):
        """Test payoffs with mixed cooperation and defection."""
        model = KCooperatorTensorPayoff(
            u_C=[0.0, 5.0, 8.0],
            u_D=[0.0, 6.0, 9.0]
        )
        rng = random.Random(42)
        moves = ['C', 'D']
        payoffs = model.compute_round_payoffs(moves, rng, {})

        # k=1 cooperator
        # Player 0 (C) → u_C[1] = 5.0
        # Player 1 (D) → u_D[1] = 6.0
        assert payoffs == [5.0, 6.0]

    def test_compute_round_payoffs_three_players(self):
        """Test with 3 players for more complex scenario."""
        model = KCooperatorTensorPayoff(
            u_C=[0.0, 3.0, 7.0, 10.0],
            u_D=[0.0, 4.0, 8.0, 11.0]
        )
        rng = random.Random(42)
        moves = ['C', 'C', 'D']
        payoffs = model.compute_round_payoffs(moves, rng, {})

        # k=2 cooperators
        # Player 0 (C) → u_C[2] = 7.0
        # Player 1 (C) → u_C[2] = 7.0
        # Player 2 (D) → u_D[2] = 8.0
        assert payoffs == [7.0, 7.0, 8.0]

    def test_compute_round_payoffs_four_players_all_mixed(self):
        """Test with 4 players with various cooperation levels."""
        model = KCooperatorTensorPayoff(
            u_C=[0.0, 2.0, 5.0, 9.0, 12.0],
            u_D=[0.0, 3.0, 6.0, 10.0, 13.0]
        )
        rng = random.Random(42)
        moves = ['C', 'D', 'C', 'D']
        payoffs = model.compute_round_payoffs(moves, rng, {})

        # k=2 cooperators
        # Player 0 (C) → u_C[2] = 5.0
        # Player 1 (D) → u_D[2] = 6.0
        # Player 2 (C) → u_C[2] = 5.0
        # Player 3 (D) → u_D[2] = 6.0
        assert payoffs == [5.0, 6.0, 5.0, 6.0]

    def test_determinism(self):
        """Test that same inputs produce same outputs."""
        model = KCooperatorTensorPayoff(
            u_C=[0.0, 5.0, 8.0],
            u_D=[0.0, 6.0, 9.0]
        )
        moves = ['C', 'D']

        rng1 = random.Random(123)
        payoffs1 = model.compute_round_payoffs(moves, rng1, {})

        rng2 = random.Random(123)
        payoffs2 = model.compute_round_payoffs(moves, rng2, {})

        assert payoffs1 == payoffs2
