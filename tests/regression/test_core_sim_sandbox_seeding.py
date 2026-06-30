"""
Test sandbox seeding in core_simulation.py

Phase 4: Make strategy randomness inside the core simulator reproducible.
Tests that strategies using random.* functions are seeded properly.
"""

import pytest
from core_simulation import get_safe_globals, extract_strategy_func


class TestSandboxSecurity:
    """Test that sandbox security remains intact after adding seeding."""
    
    def test_sandbox_blocks_imports(self):
        """Strategies should not be able to import modules at runtime."""
        code = """
def malicious_strategy(opp_last_move, my_history, opp_history, context):
    import os
    return 'C'
"""
        func = extract_strategy_func(code, seed=42)
        # Function extracts successfully, but calling it should fail with ImportError
        assert func is not None
        
        # Calling the function should fail due to missing __import__
        with pytest.raises(ImportError):
            func('C', [], [], {})
    
    def test_sandbox_blocks_dangerous_builtins(self):
        """Strategies should not have access to dangerous builtins like open, eval."""
        code = """
def malicious_strategy(opp_last_move, my_history, opp_history, context):
    open('/etc/passwd', 'r')
    return 'C'
"""
        func = extract_strategy_func(code, seed=42)
        # The function may extract, but calling it should fail
        # For now, we check that dangerous builtins aren't in safe_globals
        safe_globals = get_safe_globals(seed=42)
        assert 'open' not in safe_globals['__builtins__']
        assert 'eval' not in safe_globals['__builtins__']
        assert 'exec' not in safe_globals['__builtins__']
        # A guarded __import__ is provided (mirrors the 1v1/N-Player sandbox) but rejects any
        # module outside the whitelist, so dangerous imports still fail.
        with pytest.raises(ImportError):
            safe_globals['__builtins__']['__import__']('os')
    
    def test_sandbox_provides_safe_builtins(self):
        """Strategies should have access to safe builtins."""
        safe_globals = get_safe_globals(seed=42)
        # Check key safe builtins are present
        assert 'max' in safe_globals['__builtins__']
        assert 'min' in safe_globals['__builtins__']
        assert 'len' in safe_globals['__builtins__']
        assert 'sum' in safe_globals['__builtins__']


class TestDeterministicRandomness:
    """Test that strategies using random functions are deterministic with seeding."""
    
    def test_random_strategy_deterministic_with_same_seed(self):
        """Strategy using random.random() should produce identical results with same seed."""
        code = """
def random_strategy(opp_last_move, my_history, opp_history, context):
    # random is provided in globals, no need to import
    if random.random() > 0.5:
        return 'C'
    else:
        return 'D'
"""
        # Extract strategy twice with same seed
        func1 = extract_strategy_func(code, seed=42)
        func2 = extract_strategy_func(code, seed=42)
        
        assert func1 is not None
        assert func2 is not None
        
        # Call each function multiple times and collect results
        results1 = []
        results2 = []
        for i in range(10):
            results1.append(func1('C', [], [], {}))
            results2.append(func2('C', [], [], {}))
        
        # Results should be identical
        assert results1 == results2
    
    def test_random_strategy_different_with_different_seeds(self):
        """Strategy using random.random() should produce different results with different seeds."""
        code = """
def random_strategy(opp_last_move, my_history, opp_history, context):
    # random is provided in globals, no need to import
    if random.random() > 0.5:
        return 'C'
    else:
        return 'D'
"""
        # Extract strategy with different seeds
        func1 = extract_strategy_func(code, seed=42)
        func2 = extract_strategy_func(code, seed=123)
        
        assert func1 is not None
        assert func2 is not None
        
        # Call each function multiple times and collect results
        results1 = []
        results2 = []
        for i in range(30):  # Use more iterations to ensure high probability of difference
            results1.append(func1('C', [], [], {}))
            results2.append(func2('C', [], [], {}))
        
        # Results should be different (with high probability)
        assert results1 != results2
    
    def test_random_choice_deterministic_with_same_seed(self):
        """Strategy using random.choice() should produce identical results with same seed."""
        code = """
def random_choice_strategy(opp_last_move, my_history, opp_history, context):
    # random is provided in globals, no need to import
    return random.choice(['C', 'D'])
"""
        # Extract strategy twice with same seed
        func1 = extract_strategy_func(code, seed=99)
        func2 = extract_strategy_func(code, seed=99)
        
        assert func1 is not None
        assert func2 is not None
        
        # Call each function multiple times and collect results
        results1 = []
        results2 = []
        for i in range(20):
            results1.append(func1('C', [], [], {}))
            results2.append(func2('C', [], [], {}))
        
        # Results should be identical
        assert results1 == results2
    
    def test_complex_random_strategy_deterministic(self):
        """Complex strategy using multiple random functions should be deterministic."""
        code = """
def complex_random_strategy(opp_last_move, my_history, opp_history, context):
    # random and math are provided in globals, no need to import
    
    # Use multiple random functions
    val1 = random.random()
    val2 = random.uniform(0, 1)
    choices = ['C', 'D', 'C', 'D']
    val3 = random.choice(choices)
    random.shuffle(choices)
    
    # Make decision based on combined random factors
    score = val1 + val2 + (1 if val3 == 'C' else 0)
    threshold = math.sqrt(2)
    
    return 'C' if score > threshold else 'D'
"""
        # Extract strategy twice with same seed
        func1 = extract_strategy_func(code, seed=777)
        func2 = extract_strategy_func(code, seed=777)
        
        assert func1 is not None
        assert func2 is not None
        
        # Call each function multiple times and collect results
        results1 = []
        results2 = []
        for i in range(15):
            results1.append(func1('D', ['C', 'D'], ['D', 'C'], {}))
            results2.append(func2('D', ['C', 'D'], ['D', 'C'], {}))
        
        # Results should be identical
        assert results1 == results2


class TestBackwardCompatibility:
    """Test that unseeded behavior works (backward compatibility)."""
    
    def test_extract_strategy_func_without_seed(self):
        """extract_strategy_func should work without seed parameter."""
        code = """
def simple_strategy(opp_last_move, my_history, opp_history, context):
    return 'C' if opp_last_move == 'C' else 'D'
"""
        # Call without seed argument
        func = extract_strategy_func(code)
        assert func is not None
        assert func('C', [], [], {}) == 'C'
        assert func('D', [], [], {}) == 'D'
    
    def test_get_safe_globals_without_seed(self):
        """get_safe_globals should work without seed parameter."""
        # Call without seed argument
        safe_globals = get_safe_globals()
        assert safe_globals is not None
        assert '__builtins__' in safe_globals
        assert 'random' in safe_globals
        assert 'math' in safe_globals
    
    def test_unseeded_random_is_nondeterministic(self):
        """Without seed, random strategies should be non-deterministic (different runs)."""
        code = """
def random_strategy(opp_last_move, my_history, opp_history, context):
    # random is provided in globals, no need to import
    return random.choice(['C', 'D'])
"""
        # Extract strategy without seed twice
        func1 = extract_strategy_func(code)
        func2 = extract_strategy_func(code)
        
        assert func1 is not None
        assert func2 is not None
        
        # Call each function many times
        results1 = [func1('C', [], [], {}) for _ in range(50)]
        results2 = [func2('C', [], [], {}) for _ in range(50)]
        
        # With high probability, unseeded random should produce different sequences
        # (This test might occasionally pass even if working correctly, but has low probability)
        # We're testing that unseeded behavior allows non-determinism
        # Note: This test verifies unseeded behavior produces variation
        assert len(set(results1)) > 1  # Should have both C and D with high probability
        assert len(set(results2)) > 1  # Should have both C and D with high probability


class TestSeedingIntegration:
    """Test that seeding integrates correctly with safe_globals."""
    
    def test_seeded_random_is_independent_instance(self):
        """Seeded random should be independent Random instance, not global module."""
        safe_globals = get_safe_globals(seed=42)
        
        # The random in safe_globals should be a Random instance
        import random as global_random
        rng = safe_globals['random']
        
        # If properly seeded, it should be a Random instance
        assert isinstance(rng, global_random.Random)
    
    def test_different_seeds_produce_different_random_instances(self):
        """Different seeds should produce different Random instances."""
        sg1 = get_safe_globals(seed=10)
        sg2 = get_safe_globals(seed=20)
        
        # Get first random values from each
        val1 = sg1['random'].random()
        val2 = sg2['random'].random()
        
        # Should be different
        assert val1 != val2
    
    def test_same_seed_produces_consistent_random_sequence(self):
        """Same seed should produce consistent random sequences."""
        sg1 = get_safe_globals(seed=100)
        sg2 = get_safe_globals(seed=100)
        
        # Get sequence from each
        seq1 = [sg1['random'].random() for _ in range(5)]
        seq2 = [sg2['random'].random() for _ in range(5)]
        
        # Should be identical
        assert seq1 == seq2
    
    def test_unseeded_provides_global_random_module(self):
        """Without seed, should provide the global random module (backward compat)."""
        safe_globals = get_safe_globals()
        
        import random as global_random
        rng = safe_globals['random']
        
        # Without seed, should be the global random module itself
        assert rng is global_random
