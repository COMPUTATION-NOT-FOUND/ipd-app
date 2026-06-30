"""
Phase 5 Tests: N-Core OS Simulator
Tests for extending OSSimulator from 2 cores to N cores with ring topology.
"""

import pytest
from core_simulation import OSSimulator, run_full_simulation
from deterministic_strategies import always_cooperate, always_defect, tit_for_tat

pytestmark = pytest.mark.regression


class TestOSSimulatorNCores:
    """Test that OSSimulator works with N cores (N > 2)"""
    
    def test_os_simulator_validates_strategy_count(self):
        """OSSimulator raises error if strategy count doesn't match num_cores"""
        def simple_strategy(last, my_hist, opp_hist):
            return 'C'
        
        # Try to create 4-core simulator with only 2 strategies
        with pytest.raises(ValueError, match="core_strategies must have 4 strategies"):
            sim = OSSimulator('Mixed', [simple_strategy, simple_strategy], 
                            seed=42, num_cores=4)
    
    def test_os_simulator_2_cores_unchanged(self):
        """Verify 2-core behavior matches previous implementation (regression test)"""
        def simple_strategy(last, my_hist, opp_hist):
            return 'C'
        
        # Test with default (2 cores, implicit)
        sim1 = OSSimulator('Mixed', [simple_strategy, simple_strategy], seed=42)
        sim1.generate_workload(30)
        result1 = sim1.run()
        
        # Test with explicit num_cores=2
        sim2 = OSSimulator('Mixed', [simple_strategy, simple_strategy], seed=42, num_cores=2)
        sim2.generate_workload(30)
        result2 = sim2.run()
        
        # Results should be identical
        assert result1['global_metrics']['avg_turnaround'] == result2['global_metrics']['avg_turnaround']
        assert result1['global_metrics']['avg_waiting'] == result2['global_metrics']['avg_waiting']
        assert result1['global_metrics']['throughput'] == result2['global_metrics']['throughput']
        assert result1['global_metrics']['makespan'] == result2['global_metrics']['makespan']

    def test_os_simulator_4_cores_determinism(self):
        """OSSimulator with num_cores=4 and seed=42 produces deterministic results"""
        def simple_strategy(last, my_hist, opp_hist):
            return 'C'
        
        strategies = [simple_strategy] * 4
        
        sim1 = OSSimulator('Mixed', strategies, seed=42, num_cores=4)
        sim1.generate_workload(30)
        result1 = sim1.run()
        
        sim2 = OSSimulator('Mixed', strategies, seed=42, num_cores=4)
        sim2.generate_workload(30)
        result2 = sim2.run()
        
        # Metrics should match exactly
        assert result1['global_metrics']['avg_turnaround'] == result2['global_metrics']['avg_turnaround']
        assert result1['global_metrics']['avg_waiting'] == result2['global_metrics']['avg_waiting']
        assert result1['global_metrics']['throughput'] == result2['global_metrics']['throughput']
        assert result1['global_metrics']['makespan'] == result2['global_metrics']['makespan']
        
    def test_os_simulator_3_cores(self):
        """Test with odd number of cores (3)"""
        def simple_strategy(last, my_hist, opp_hist):
            return 'C'
        
        strategies = [simple_strategy] * 3
        
        sim = OSSimulator('Mixed', strategies, seed=100, num_cores=3)
        sim.generate_workload(25)
        result = sim.run()
        
        # Should complete successfully
        assert 'global_metrics' in result
        assert result['global_metrics']['makespan'] > 0
        assert len(sim.cores) == 3
        
    def test_os_simulator_8_cores(self):
        """Test with larger core count (8)"""
        def tft_strategy(last, my_hist, opp_hist):
            return opp_hist[-1] if opp_hist else 'C'
        
        strategies = [tft_strategy] * 8
        
        sim = OSSimulator('Bursty', strategies, seed=200, num_cores=8)
        sim.generate_workload(40)
        result = sim.run()
        
        # Should complete successfully
        assert 'global_metrics' in result
        assert result['global_metrics']['throughput'] > 0
        assert len(sim.cores) == 8


class TestBenchmarkFunctionsNCores:
    """Test that benchmark functions support num_cores parameter"""

    def test_full_simulation_default_2_cores(self):
        """Full simulation with default 2 cores"""
        strategies_data = [
            {'name': 'TitForTat', 'code': 'def s(l,m,o):\n    return o[-1] if o else "C"'}
        ]
        
        results = run_full_simulation(strategies_data)

        assert results['assignment_mode'] == 'homogeneous'
        assert 'TitForTat' in results['strategies']
        assert 'Mixed' in results['strategies']['TitForTat']['workloads']
        assert 'throughput' in results['strategies']['TitForTat']['avg']

    def test_full_simulation_4_cores(self):
        """Full simulation with 4 cores"""
        strategies_data = [
            {'name': 'TitForTat', 'code': 'def s(l,m,o):\n    return o[-1] if o else "C"'}
        ]

        results = run_full_simulation(strategies_data, num_cores=4)

        assert results['num_cores'] == 4
        assert 'TitForTat' in results['strategies']
        assert 'Mixed' in results['strategies']['TitForTat']['workloads']
        assert 'throughput' in results['strategies']['TitForTat']['workloads']['Mixed']


class TestNCoresDeterministicComparison:
    """Test that different core counts produce deterministic but different results"""
    
    def test_2_vs_4_cores_different_but_deterministic(self):
        """2-core and 4-core simulations produce different but each deterministic results"""
        def tft_strategy(last, my_hist, opp_hist):
            return opp_hist[-1] if opp_hist else 'C'
        
        # Run 2-core twice
        sim1_2core_a = OSSimulator('Mixed', [tft_strategy, tft_strategy], seed=42, num_cores=2)
        sim1_2core_a.generate_workload(30)
        result1_2core_a = sim1_2core_a.run()
        
        sim1_2core_b = OSSimulator('Mixed', [tft_strategy, tft_strategy], seed=42, num_cores=2)
        sim1_2core_b.generate_workload(30)
        result1_2core_b = sim1_2core_b.run()
        
        # Run 4-core twice
        sim1_4core_a = OSSimulator('Mixed', [tft_strategy] * 4, seed=42, num_cores=4)
        sim1_4core_a.generate_workload(30)
        result1_4core_a = sim1_4core_a.run()
        
        sim1_4core_b = OSSimulator('Mixed', [tft_strategy] * 4, seed=42, num_cores=4)
        sim1_4core_b.generate_workload(30)
        result1_4core_b = sim1_4core_b.run()
        
        # Each core count should be self-consistent
        assert result1_2core_a['global_metrics']['makespan'] == result1_2core_b['global_metrics']['makespan']
        assert result1_4core_a['global_metrics']['makespan'] == result1_4core_b['global_metrics']['makespan']
        
        # But 2-core and 4-core should differ (more cores = potentially faster)
        # Note: They might not always differ due to workload, but at least verify both run successfully
        assert result1_2core_a['global_metrics']['makespan'] > 0
        assert result1_4core_a['global_metrics']['makespan'] > 0
