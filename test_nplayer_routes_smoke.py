"""
Tests for N-player tournament endpoint (Phase 4).

Tests the POST /nplayer/tournament endpoint with various configurations,
validates caps enforcement, and ensures proper leaderboard structure.
"""

import pytest
from app import app, limiter


class TestNPlayerRouteSmoke:
    """Smoke tests for N-player tournament endpoint"""

    @pytest.fixture
    def client(self):
        """Create test client"""
        app.config['TESTING'] = True
        # Disable rate limiting: these smoke tests make many rapid POSTs to the
        # same endpoint from one client and would otherwise trip the per-minute
        # limit added for production abuse protection.
        limiter.enabled = False
        with app.test_client() as client:
            # Simulation endpoints now require login; attach a regular-user session.
            with client.session_transaction() as sess:
                sess['user'] = {
                    'uid': 'test-user-123',
                    'email': 'test@example.com',
                    'role': 'user',
                }
            yield client
        limiter.enabled = True
    
    def test_nplayer_endpoint_exists(self, client):
        """POST /nplayer/tournament should exist and respond"""
        strategies = [
            {'name': 'AlwaysCooperate', 'code': 'def strategy(l,m,o,x): return "C"'},
            {'name': 'AlwaysDefect', 'code': 'def strategy(l,m,o,x): return "D"'},
            {'name': 'TitForTat', 'code': 'def strategy(l,m,o,x): return "C" if not l else ("D" if "D" in l else "C")'}
        ]
        
        response = client.post('/nplayer/tournament',
            json={
                'strategies': strategies,
                'rounds': 100,
                'seed': 42
            })
        
        assert response.status_code == 200
        
    def test_nplayer_returns_leaderboard_structure(self, client):
        """Endpoint should return leaderboard with required fields"""
        strategies = [
            {'name': 'Cooperator', 'code': 'def strategy(l,m,o,x): return "C"'},
            {'name': 'Defector', 'code': 'def strategy(l,m,o,x): return "D"'},
            {'name': 'Random', 'code': 'def strategy(l,m,o,x): return x["rng"].choice(["C", "D"])'}
        ]
        
        response = client.post('/nplayer/tournament',
            json={
                'strategies': strategies,
                'rounds': 50,
                'seed': 123
            })
        
        assert response.status_code == 200
        data = response.get_json()
        
        # Should have tournament result structure
        assert 'leaderboard' in data
        assert 'matches' in data
        assert 'tournament_info' in data
        
        # Leaderboard should have entries for each strategy
        assert len(data['leaderboard']) == 3
        
        # Each entry should have required fields for weighted scoring
        for entry in data['leaderboard']:
            assert 'name' in entry
            assert 'total_points' in entry
            assert 'cooperation_percentage' in entry
            assert 'wins' in entry
            assert 'draws' in entry
            assert 'losses' in entry

    def test_nplayer_accepts_modes_list(self, client):
        """Endpoint should accept `modes` list and reflect it in tournament_info."""
        strategies = [
            {'name': 'C', 'code': 'def strategy(l,m,o,x): return "C"'},
            {'name': 'D', 'code': 'def strategy(l,m,o,x): return "D"'},
        ]

        response = client.post(
            '/nplayer/tournament',
            json={
                'strategies': strategies,
                'rounds': 10,
                'seed': 123,
                'modes': ['standard', 'discounted'],
                'discount_factor': 0.9,
            },
        )

        assert response.status_code == 200
        data = response.get_json()
        assert 'tournament_info' in data

        # Backward compatible: may still include legacy 'mode'
        assert data['tournament_info'].get('modes') == ['standard', 'discounted']

    def test_nplayer_rejects_empty_modes_list(self, client):
        """modes=[] should be rejected (validation gap regression)."""
        strategies = [
            {'name': 'C', 'code': 'def strategy(l,m,o,x): return "C"'},
            {'name': 'D', 'code': 'def strategy(l,m,o,x): return "D"'},
        ]

        response = client.post(
            '/nplayer/tournament',
            json={
                'strategies': strategies,
                'rounds': 10,
                'seed': 1,
                'modes': [],
            },
        )

        assert response.status_code == 400

    def test_nplayer_rejects_duplicate_modes(self, client):
        """modes with duplicates should be rejected."""
        strategies = [
            {'name': 'C', 'code': 'def strategy(l,m,o,x): return "C"'},
            {'name': 'D', 'code': 'def strategy(l,m,o,x): return "D"'},
        ]

        response = client.post(
            '/nplayer/tournament',
            json={
                'strategies': strategies,
                'rounds': 10,
                'seed': 1,
                'modes': ['standard', 'standard'],
            },
        )

        assert response.status_code == 400

    def test_nplayer_null_mode_params_use_defaults(self, client):
        """Explicit null discount_factor/stochastic_prob should be treated as missing."""
        strategies = [
            {'name': 'C', 'code': 'def strategy(l,m,o,x): return "C"'},
            {'name': 'D', 'code': 'def strategy(l,m,o,x): return "D"'},
        ]

        response = client.post(
            '/nplayer/tournament',
            json={
                'strategies': strategies,
                'rounds': 10,
                'seed': 2,
                'modes': ['discounted', 'stochastic'],
                'discount_factor': None,
                'stochastic_prob': None,
            },
        )

        assert response.status_code == 200
    
    def test_nplayer_accepts_large_roster(self, client):
        """No strategy-count cap on the local app (runs on the user's own machine, like 1v1)."""
        strategies = [
            {'name': f'Strategy{i}', 'code': f'def Strategy{i}(l,m,o,x): return "C"'}
            for i in range(6)   # more than the old cap of 4
        ]

        response = client.post('/nplayer/tournament',
            json={'strategies': strategies, 'rounds': 20, 'seed': 42})

        assert response.status_code == 200
        data = response.get_json()
        assert 'leaderboard' in data
    
    def test_nplayer_rejects_too_many_rounds(self, client):
        """Should enforce MAX_NPLAYER_ROUNDS cap"""
        strategies = [
            {'name': 'A', 'code': 'def strategy(l,m,o,x): return "C"'},
            {'name': 'B', 'code': 'def strategy(l,m,o,x): return "D"'}
        ]
        
        response = client.post('/nplayer/tournament',
            json={'strategies': strategies, 'rounds': 2000, 'seed': 42})  # Exceeds cap of 1000
        
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert 'rounds' in data['error'].lower() or 'limit' in data['error'].lower()
    
    def test_nplayer_accepts_valid_within_caps(self, client):
        """Should accept valid requests within caps"""
        strategies = [
            {'name': 'Cooperator', 'code': 'def strategy(l,m,o,x): return "C"'},
            {'name': 'Defector', 'code': 'def strategy(l,m,o,x): return "D"'},
            {'name': 'Alternator', 'code': 'def strategy(l,m,o,x): return "D" if len(m) % 2 == 0 else "C"'}
        ]
        
        response = client.post('/nplayer/tournament',
            json={'strategies': strategies, 'rounds': 500, 'seed': 999})
        
        assert response.status_code == 200
        data = response.get_json()
        assert 'leaderboard' in data
    
    def test_nplayer_with_public_goods_payoff(self, client):
        """Should accept public goods payoff model configuration"""
        strategies = [
            {'name': 'C1', 'code': 'def strategy(l,m,o,x): return "C"'},
            {'name': 'C2', 'code': 'def strategy(l,m,o,x): return "C"'},
            {'name': 'D1', 'code': 'def strategy(l,m,o,x): return "D"'}
        ]
        
        response = client.post('/nplayer/tournament',
            json={
                'strategies': strategies,
                'rounds': 100,
                'seed': 42,
                'payoff_model': {
                    'type': 'public_goods',
                    'b': 2.0,
                    'c': 1.0,
                    'nonlinear': {'type': 'linear'}
                }
            })
        
        assert response.status_code == 200
        data = response.get_json()
        assert 'leaderboard' in data
    
    def test_nplayer_with_pairwise_matrix_payoff(self, client):
        """Should accept pairwise matrix payoff model"""
        strategies = [
            {'name': 'C', 'code': 'def strategy(l,m,o,x): return "C"'},
            {'name': 'D', 'code': 'def strategy(l,m,o,x): return "D"'}
        ]
        
        response = client.post('/nplayer/tournament',
            json={
                'strategies': strategies,
                'rounds': 100,
                'seed': 42,
                'payoff_model': {
                    'type': 'pairwise_matrix',
                    'payoff_matrix': {
                        'CC': [3, 3],
                        'CD': [0, 5],
                        'DC': [5, 0],
                        'DD': [1, 1]
                    },
                    'aggregate': 'sum'
                }
            })
        
        assert response.status_code == 200
        data = response.get_json()
        assert 'leaderboard' in data

    def test_nplayer_pairwise_matrix_aggregate_sum_vs_avg_changes_points(self, client):
        """pairwise_matrix payoff_model.aggregate must be honored (sum vs avg) for n>=3."""
        strategies = [
            {'name': 'D1', 'code': 'def strategy(l,m,o,x): return "D"'},
            {'name': 'D2', 'code': 'def strategy(l,m,o,x): return "D"'},
            {'name': 'D3', 'code': 'def strategy(l,m,o,x): return "D"'},
        ]

        rounds = 10
        seed = 123
        payoff_matrix = {
            'CC': [3, 3],
            'CD': [0, 5],
            'DC': [5, 0],
            'DD': [1, 1],
        }

        def points_by_name(payload):
            response = client.post('/nplayer/tournament', json=payload)
            assert response.status_code == 200
            data = response.get_json()
            return {row['name']: float(row['total_points']) for row in data['leaderboard']}

        payload_sum = {
            'strategies': strategies,
            'rounds': rounds,
            'seed': seed,
            'payoff_model': {
                'type': 'pairwise_matrix',
                'payoff_matrix': payoff_matrix,
                'aggregate': 'sum',
            },
        }
        payload_avg = {
            **payload_sum,
            'payoff_model': {
                **payload_sum['payoff_model'],
                'aggregate': 'avg',
            },
        }

        pts_sum = points_by_name(payload_sum)
        pts_avg = points_by_name(payload_avg)

        n_players = 3
        for name in ['D1', 'D2', 'D3']:
            # For all-D with DD=1: sum gives (n-1)*rounds, avg gives 1*rounds
            assert pts_sum[name] == pytest.approx((n_players - 1) * rounds, rel=1e-12)
            assert pts_avg[name] == pytest.approx(rounds, rel=1e-12)
            assert pts_sum[name] == pytest.approx((n_players - 1) * pts_avg[name], rel=1e-12)

    def test_nplayer_with_public_goods_payoff_linear_string(self, client):
        """Should accept public_goods payoff model where nonlinear is provided as a string."""
        strategies = [
            {'name': 'C1', 'code': 'def strategy(l,m,o,x): return "C"'},
            {'name': 'C2', 'code': 'def strategy(l,m,o,x): return "C"'},
            {'name': 'D1', 'code': 'def strategy(l,m,o,x): return "D"'},
        ]

        response = client.post(
            '/nplayer/tournament',
            json={
                'strategies': strategies,
                'rounds': 10,
                'seed': 42,
                'payoff_model': {
                    'type': 'public_goods',
                    'b': 2.0,
                    'c': 1.0,
                    'nonlinear': 'linear',
                },
            },
        )

        assert response.status_code == 200
        data = response.get_json()
        assert 'leaderboard' in data

    def test_nplayer_public_goods_power_alpha_top_level_wired(self, client):
        """Top-level alpha should be applied when nonlinear is shorthand 'power'."""
        strategies = [
            {'name': 'C1', 'code': 'def strategy(l,m,o,x): return "C"'},
            {'name': 'C2', 'code': 'def strategy(l,m,o,x): return "C"'},
            {'name': 'C3', 'code': 'def strategy(l,m,o,x): return "C"'},
        ]

        b = 2.0
        c = 1.0
        alpha = 2.0
        rounds = 1
        k = 3
        expected_points = (b * (k ** alpha) - c) * rounds

        response = client.post(
            '/nplayer/tournament',
            json={
                'strategies': strategies,
                'rounds': rounds,
                'seed': 123,
                'payoff_model': {
                    'type': 'public_goods',
                    'b': b,
                    'c': c,
                    'nonlinear': 'power',
                    'alpha': alpha,
                },
            },
        )

        assert response.status_code == 200
        data = response.get_json()
        assert 'leaderboard' in data
        assert len(data['leaderboard']) == 3
        for row in data['leaderboard']:
            assert float(row['total_points']) == pytest.approx(expected_points, rel=1e-12)

    def test_nplayer_with_group_size(self, client):
        """Should accept group_size parameter for multi-group tournaments"""
        # Practice is capped at 4 strategies, so use 4 in two groups of 2.
        strategies = [
            {'name': f'S{i}', 'code': 'def strategy(l,m,o,x): return "C"'}
            for i in range(4)
        ]

        response = client.post('/nplayer/tournament',
            json={
                'strategies': strategies,
                'rounds': 100,
                'seed': 42,
                'group_size': 2  # Create 2 groups of 2
            })

        assert response.status_code == 200
        data = response.get_json()
        assert 'leaderboard' in data
        assert len(data['leaderboard']) == 4
    
    def test_nplayer_deterministic_with_seed(self, client):
        """Results should be deterministic when seed is provided"""
        strategies = [
            {'name': 'Random1', 'code': 'def strategy(l,m,o,x): return x["rng"].choice(["C", "D"])'},
            {'name': 'Random2', 'code': 'def strategy(l,m,o,x): return x["rng"].choice(["C", "D"])'}
        ]
        
        payload = {
            'strategies': strategies,
            'rounds': 50,
            'seed': 777
        }
        
        # Run twice with same seed
        response1 = client.post('/nplayer/tournament', json=payload)
        response2 = client.post('/nplayer/tournament', json=payload)
        
        assert response1.status_code == 200
        assert response2.status_code == 200
        
        data1 = response1.get_json()
        data2 = response2.get_json()
        
        # Results should be identical
        assert data1['leaderboard'] == data2['leaderboard']
    
    def test_nplayer_validates_strategy_names(self, client):
        """Should reject invalid strategy names (XSS protection)"""
        strategies = [
            {'name': '<script>alert("xss")</script>', 'code': 'def strategy(l,m,o,x): return "C"'},
            {'name': 'ValidName', 'code': 'def strategy(l,m,o,x): return "D"'}
        ]
        
        response = client.post('/nplayer/tournament',
            json={'strategies': strategies, 'rounds': 100, 'seed': 42})
        
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
    
    def test_nplayer_with_custom_weights(self, client):
        """Should accept custom weights for leaderboard calculation"""
        strategies = [
            {'name': 'C', 'code': 'def strategy(l,m,o,x): return "C"'},
            {'name': 'D', 'code': 'def strategy(l,m,o,x): return "D"'}
        ]
        
        response = client.post('/nplayer/tournament',
            json={
                'strategies': strategies,
                'rounds': 100,
                'seed': 42,
                'weights': {
                    'cooperation': 0.5,
                    'win_rate': 0.3,
                    'points': 0.2
                }
            })
        
        assert response.status_code == 200
        data = response.get_json()
        assert 'leaderboard' in data
    
    def test_nplayer_rejects_invalid_json_body(self, client):
        """Should return 400/415 for invalid/missing JSON body (CRITICAL Issue #1)"""
        # Test with no content-type (Flask returns 415)
        response = client.post('/nplayer/tournament', data='not json')
        assert response.status_code in [400, 415]  # Flask returns 415 for unsupported media type
        
    def test_nplayer_rejects_missing_json_body(self, client):
        """Should return 400/415 when JSON body is completely missing"""
        response = client.post('/nplayer/tournament')
        assert response.status_code in [400, 415]  # Flask returns 415 for missing content-type
    
    def test_nplayer_rejects_zero_group_size(self, client):
        """Should reject group_size of 0 (CRITICAL Issue #2)"""
        strategies = [
            {'name': 'A', 'code': 'def strategy(l,m,o,x): return "C"'},
            {'name': 'B', 'code': 'def strategy(l,m,o,x): return "D"'}
        ]
        
        response = client.post('/nplayer/tournament',
            json={'strategies': strategies, 'rounds': 100, 'seed': 42, 'group_size': 0})
        
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert 'group_size' in data['error'].lower()
    
    def test_nplayer_rejects_negative_group_size(self, client):
        """Should reject negative group_size"""
        strategies = [
            {'name': 'A', 'code': 'def strategy(l,m,o,x): return "C"'},
            {'name': 'B', 'code': 'def strategy(l,m,o,x): return "D"'}
        ]
        
        response = client.post('/nplayer/tournament',
            json={'strategies': strategies, 'rounds': 100, 'seed': 42, 'group_size': -5})
        
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert 'group_size' in data['error'].lower()
    
    def test_nplayer_rejects_non_integer_group_size(self, client):
        """Should reject non-integer group_size"""
        strategies = [
            {'name': 'A', 'code': 'def strategy(l,m,o,x): return "C"'},
            {'name': 'B', 'code': 'def strategy(l,m,o,x): return "D"'}
        ]
        
        response = client.post('/nplayer/tournament',
            json={'strategies': strategies, 'rounds': 100, 'seed': 42, 'group_size': 'invalid'})
        
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert 'group_size' in data['error'].lower()
    
    def test_nplayer_rejects_invalid_payoff_model_type(self, client):
        """Should reject invalid payoff model type"""
        strategies = [
            {'name': 'A', 'code': 'def strategy(l,m,o,x): return "C"'},
            {'name': 'B', 'code': 'def strategy(l,m,o,x): return "D"'}
        ]
        
        response = client.post('/nplayer/tournament',
            json={
                'strategies': strategies,
                'rounds': 100,
                'seed': 42,
                'payoff_model': {'type': 'invalid_model'}
            })
        
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
    
    def test_nplayer_injects_tournament_info_context(self, client):
        """Should inject TOURNAMENT_INFO into strategy execution (MAJOR Issue #3)"""
        # Strategy that reads TOURNAMENT_INFO
        context_aware = """
def strategy(last_moves, my_history, others_histories, context):
    # Access TOURNAMENT_INFO added to globals
    if 'TOURNAMENT_INFO' in globals():
        weights = TOURNAMENT_INFO.get('weights')
        if weights and weights.get('cooperation', 0) > 0.5:
            return 'C'  # Cooperate if cooperation is weighted highly
    return 'D'
"""
        
        strategies = [
            {'name': 'ContextAware', 'code': context_aware},
            {'name': 'Defector', 'code': 'def strategy(l,m,o,x): return "D"'}
        ]
        
        response = client.post('/nplayer/tournament',
            json={
                'strategies': strategies,
                'rounds': 50,
                'seed': 42,
                'weights': {
                    'cooperation': 0.8,
                    'win_rate': 0.1,
                    'points': 0.1
                }
            })
        
        assert response.status_code == 200
        data = response.get_json()
        # Strategy should have executed successfully (not crashed)
        assert 'leaderboard' in data
        assert len(data['leaderboard']) == 2
    
    def test_nplayer_extracts_4arg_function(self, client):
        """Should extract 4-arg functions for N-player strategies (MAJOR Issue #4)"""
        # Define a strategy with 4 parameters (N-player signature)
        four_arg_strategy = """
def my_nplayer_strategy(last_moves, my_history, others_histories, context):
    # N-player strategy with 4 arguments
    return 'C'
"""
        
        strategies = [
            {'name': 'FourArg', 'code': four_arg_strategy},
            {'name': 'Simple', 'code': 'def strategy(l,m,o,x): return "D"'}
        ]
        
        response = client.post('/nplayer/tournament',
            json={'strategies': strategies, 'rounds': 50, 'seed': 42})
        
        assert response.status_code == 200
        data = response.get_json()
        assert 'leaderboard' in data
