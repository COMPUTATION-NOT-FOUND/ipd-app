"""
Phase 4 Tests: Deterministic Tournament Runs
Tests for regression testing with fixed seeds and reproducible outputs.
"""

import pytest
import json
import random
from app import game, round_robin_tournament
from core_simulation import OSSimulator
from deterministic_strategies import always_cooperate, always_defect, tit_for_tat

pytestmark = pytest.mark.regression


class TestDeterministicGame:
    """Test that game() produces deterministic results with seed"""
    
    def test_game_with_seed_is_deterministic(self):
        """Running game with same seed produces identical results"""
        strategy_a = "def strategy_a(last, my_hist, opp_hist):\n    return 'C'"
        strategy_b = "def strategy_b(last, my_hist, opp_hist):\n    return 'D'"
        
        # Run game twice with same seed
        result1 = game(strategy_a, "PlayerA", strategy_b, "PlayerB", 
                      rounds=100, seed=42)
        result2 = game(strategy_a, "PlayerA", strategy_b, "PlayerB", 
                      rounds=100, seed=42)
        
        # Results should be identical
        assert result1['a_points'] == result2['a_points']
        assert result1['b_points'] == result2['b_points']
        assert result1['rounds'] == result2['rounds']
        assert result1['winner'] == result2['winner']
        assert result1['a_coops'] == result2['a_coops']
        assert result1['a_defects'] == result2['a_defects']
        
    def test_game_without_seed_is_nondeterministic(self):
        """Running game without seed can produce different results in stochastic mode"""
        strategy_a = "def strategy_a(last, my_hist, opp_hist):\n    return 'C'"
        strategy_b = "def strategy_b(last, my_hist, opp_hist):\n    return 'D'"
        
        # Run stochastic game multiple times without seed
        results = []
        for _ in range(5):
            result = game(strategy_a, "PlayerA", strategy_b, "PlayerB", 
                         mode='stochastic', seed=None)
            results.append(result['rounds'])
        
        # At least some should differ (very high probability in stochastic mode)
        # Since we're not seeding, different runs should have different round counts
        assert len(set(results)) > 1, "Expected variation in stochastic mode without seed"
    
    def test_game_random_mode_deterministic_with_seed(self):
        """Random mode with seed produces consistent round counts"""
        strategy_a = "def strategy_a(last, my_hist, opp_hist):\n    return 'C'"
        strategy_b = "def strategy_b(last, my_hist, opp_hist):\n    return 'D'"
        
        result1 = game(strategy_a, "PlayerA", strategy_b, "PlayerB", 
                      mode='random', seed=100)
        result2 = game(strategy_a, "PlayerA", strategy_b, "PlayerB", 
                      mode='random', seed=100)
        
        assert result1['rounds'] == result2['rounds']
        
    def test_game_stochastic_mode_deterministic_with_seed(self):
        """Stochastic mode with seed produces consistent outcomes"""
        strategy_a = "def strategy_a(last, my_hist, opp_hist):\n    return 'C'"
        strategy_b = "def strategy_b(last, my_hist, opp_hist):\n    return 'D'"
        
        result1 = game(strategy_a, "PlayerA", strategy_b, "PlayerB", 
                      mode='stochastic', seed=200)
        result2 = game(strategy_a, "PlayerA", strategy_b, "PlayerB", 
                      mode='stochastic', seed=200)
        
        assert result1['rounds'] == result2['rounds']
        assert result1['a_points'] == result2['a_points']


class TestStrategyExtraction:
    """Test that strategy extraction doesn't pick up builtins like randint"""
    
    def test_strategy_extraction_ignores_randint(self):
        """Strategy extraction should find actual strategy, not randint"""
        # This code has randint in globals but actual strategy is 'my_strategy'
        strategy_code = """
def my_strategy(last, my_hist, opp_hist):
    if len(opp_hist) > 0:
        return opp_hist[-1]
    return 'C'
"""
        
        result = game(strategy_code, "Player1", strategy_code, "Player2", 
                     rounds=10, seed=42)
        
        # Should not error out or pick wrong callable
        assert 'error' not in result or result.get('error') is None
        assert result['rounds'] == 10
        
    def test_strategy_with_helper_functions(self):
        """Strategy extraction finds main strategy when helper functions exist"""
        strategy_code = """
def helper(x):
    return x + 1

def main_strategy(last, my_hist, opp_hist):
    return 'C' if len(my_hist) < 5 else 'D'
"""
        
        result = game(strategy_code, "Player1", strategy_code, "Player2", 
                     rounds=10, seed=42)
        
        # Should successfully find and run main_strategy
        assert 'error' not in result or result.get('error') is None
        assert result['rounds'] == 10


class TestDeterministicStrategies:
    """Test reference deterministic strategies"""
    
    def test_always_cooperate(self):
        """Always Cooperate returns 'C' consistently"""
        assert always_cooperate(None, [], []) == 'C'
        assert always_cooperate('D', ['C'], ['D']) == 'C'
        assert always_cooperate('C', ['C', 'C'], ['D', 'D']) == 'C'
        
    def test_always_defect(self):
        """Always Defect returns 'D' consistently"""
        assert always_defect(None, [], []) == 'D'
        assert always_defect('C', ['D'], ['C']) == 'D'
        assert always_defect('D', ['D', 'D'], ['C', 'C']) == 'D'
        
    def test_tit_for_tat(self):
        """Tit for Tat mirrors opponent's last move"""
        assert tit_for_tat(None, [], []) == 'C'  # Start with cooperation
        assert tit_for_tat('C', ['C'], ['C']) == 'C'  # Mirror cooperation
        assert tit_for_tat('D', ['C'], ['D']) == 'D'  # Mirror defection
        assert tit_for_tat('C', ['C', 'D'], ['C', 'C']) == 'C'  # Mirror last move


class TestTournamentDeterminism:
    """Test that tournaments produce deterministic results with seed"""
    
    def test_tournament_deterministic_with_seed(self):
        """Tournament with seed produces identical results"""
        strategies = [
            {'name': 'Cooperator', 'code': 'def s(l,m,o):\n    return "C"'},
            {'name': 'Defector', 'code': 'def s(l,m,o):\n    return "D"'},
            {'name': 'TitForTat', 'code': 'def s(l,m,o):\n    return o[-1] if o else "C"'}
        ]
        
        result1 = round_robin_tournament(strategies, rounds=50, seed=42)
        result2 = round_robin_tournament(strategies, rounds=50, seed=42)
        
        # Extract leaderboard for comparison
        leaderboard1 = result1['leaderboard']
        leaderboard2 = result2['leaderboard']
        
        # Sort to ensure stable comparison
        sorted_lb1 = sorted(leaderboard1, key=lambda x: x['name'])
        sorted_lb2 = sorted(leaderboard2, key=lambda x: x['name'])
        
        for strat1, strat2 in zip(sorted_lb1, sorted_lb2):
            assert strat1['name'] == strat2['name']
            assert strat1['total_points'] == strat2['total_points']
            assert strat1['wins'] == strat2['wins']
            assert strat1['draws'] == strat2['draws']
            assert strat1['losses'] == strat2['losses']
    
    def test_tournament_stable_ordering(self):
        """Tournament orders strategies consistently"""
        strategies = [
            {'name': 'Charlie', 'code': 'def s(l,m,o):\n    return "C"', 'user_id': '3'},
            {'name': 'Alice', 'code': 'def s(l,m,o):\n    return "D"', 'user_id': '1'},
            {'name': 'Bob', 'code': 'def s(l,m,o):\n    return "C"', 'user_id': '2'}
        ]
        
        result = round_robin_tournament(strategies, rounds=50, seed=42)
        
        # Get match order from results
        match_pairs = [(m['player_a'], m['player_b']) for m in result['matches']]
        
        # Run again, should get same match order
        result2 = round_robin_tournament(strategies, rounds=50, seed=42)
        match_pairs2 = [(m['player_a'], m['player_b']) for m in result2['matches']]
        
        assert match_pairs == match_pairs2


class TestOSSimulationDeterminism:
    """Test that OS simulation produces deterministic results with seed"""
    
    def test_simulation_deterministic_with_seed(self):
        """OS simulation with seed produces identical results"""
        def simple_strategy(last, my_hist, opp_hist):
            return 'C'
        
        sim1 = OSSimulator('Mixed', [simple_strategy, simple_strategy], seed=42)
        sim1.generate_workload(30)
        result1 = sim1.run()
        
        sim2 = OSSimulator('Mixed', [simple_strategy, simple_strategy], seed=42)
        sim2.generate_workload(30)
        result2 = sim2.run()
        
        # Metrics should match exactly
        assert result1['global_metrics']['avg_turnaround'] == result2['global_metrics']['avg_turnaround']
        assert result1['global_metrics']['avg_waiting'] == result2['global_metrics']['avg_waiting']
        assert result1['global_metrics']['throughput'] == result2['global_metrics']['throughput']
        assert result1['global_metrics']['makespan'] == result2['global_metrics']['makespan']
    
    def test_simulation_workload_deterministic(self):
        """Workload generation with seed is deterministic"""
        def simple_strategy(last, my_hist, opp_hist):
            return 'C'
        
        sim1 = OSSimulator('Poisson', [simple_strategy, simple_strategy], seed=100)
        sim1.generate_workload(20)
        procs1 = [(p.pid, p.arrival_time, p.burst_time) for p in sim1.all_processes]
        
        sim2 = OSSimulator('Poisson', [simple_strategy, simple_strategy], seed=100)
        sim2.generate_workload(20)
        procs2 = [(p.pid, p.arrival_time, p.burst_time) for p in sim2.all_processes]
        
        # Process characteristics should match
        assert procs1 == procs2


class TestDeterministicReference:
    """Test deterministic reference tournament for regression testing"""
    
    def test_reference_tournament_output(self):
        """Reference tournament produces known stable output"""
        from deterministic_strategies import (
            always_cooperate_code, always_defect_code, tit_for_tat_code
        )
        
        strategies = [
            {'name': 'AlwaysCooperate', 'code': always_cooperate_code},
            {'name': 'AlwaysDefect', 'code': always_defect_code},
            {'name': 'TitForTat', 'code': tit_for_tat_code}
        ]
        
        result = round_robin_tournament(strategies, rounds=100, seed=42)
        
        # This creates a regression baseline
        # AlwaysDefect should beat AlwaysCooperate
        # TitForTat should do reasonably well
        leaderboard = {s['name']: s for s in result['leaderboard']}
        
        assert 'AlwaysCooperate' in leaderboard
        assert 'AlwaysDefect' in leaderboard
        assert 'TitForTat' in leaderboard
        
        # AlwaysDefect should have more points than AlwaysCooperate in head-to-head
        defector_points = leaderboard['AlwaysDefect']['total_points']
        cooperator_points = leaderboard['AlwaysCooperate']['total_points']
        
        assert defector_points > cooperator_points, "Defector should exploit cooperator"
        
        # Verify consistency - run again with same seed
        result2 = round_robin_tournament(strategies, rounds=100, seed=42)
        leaderboard2 = {s['name']: s for s in result2['leaderboard']}
        
        assert leaderboard['AlwaysDefect']['total_points'] == leaderboard2['AlwaysDefect']['total_points']
        assert leaderboard['TitForTat']['total_points'] == leaderboard2['TitForTat']['total_points']
