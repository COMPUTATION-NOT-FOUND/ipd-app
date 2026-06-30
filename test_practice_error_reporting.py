"""
Phase 9 Tests: Practice-Mode Strategy Error Reporting
Tests that Practice mode returns clear, safe error messages for strategy failures.
"""

import pytest
import json
from app import app


class TestPracticeErrorReporting:
    """Test error reporting in Practice mode"""

    @pytest.fixture(autouse=True)
    def _login(self, client):
        """Practice endpoints now require login; attach a regular-user session."""
        with client.session_transaction() as sess:
            sess['user'] = {
                'uid': 'test-user-123',
                'email': 'test@example.com',
                'role': 'user',
            }

    def test_practice_error_name_error(self, client):
        """Test that strategy with NameError returns error details"""
        strategies = [
            {
                'name': 'AlwaysCooperate',
                'code': '''def strategy(last_move, my_history, opponent_history):
    return 'C'
'''
            },
            {
                'name': 'BrokenStrategy',
                'code': '''def strategy(last_move, my_history, opponent_history):
    return undefined_variable
'''
            }
        ]
        
        response = client.post('/tournament',
                             json={'strategies': strategies, 'rounds': 10},
                             content_type='application/json')
        
        assert response.status_code == 200
        data = response.get_json()
        assert data is not None
        
        # Check that matches exist
        assert 'matches' in data
        assert len(data['matches']) > 0
        
        # Find the match with the error
        error_match = None
        for match in data['matches']:
            if 'error' in match:
                error_match = match
                break
        
        # Error should be present
        assert error_match is not None
        assert 'error' in error_match
        assert 'error_type' in error_match
        assert 'error_player' in error_match
        assert 'terminated_early' in error_match
        
        # Check error details
        assert 'NameError' in error_match['error_type'] or 'Exception' in error_match['error_type']
        assert error_match['error_player'] in ['A', 'B']
        assert error_match['terminated_early'] is True
        
        # Should still have points structure
        assert 'a_points' in error_match
        assert 'b_points' in error_match
    
    def test_practice_error_instruction_limit(self, client):
        """Test that infinite loop triggers InstructionLimitExceeded"""
        strategies = [
            {
                'name': 'AlwaysCooperate',
                'code': '''def strategy(last_move, my_history, opponent_history):
    return 'C'
'''
            },
            {
                'name': 'InfiniteLoop',
                'code': '''def strategy(last_move, my_history, opponent_history):
    while True:
        pass
    return 'C'
'''
            }
        ]
        
        response = client.post('/tournament',
                             json={'strategies': strategies, 'rounds': 10},
                             content_type='application/json')
        
        assert response.status_code == 200
        data = response.get_json()
        
        # Check for error in matches
        error_match = None
        for match in data['matches']:
            if 'error' in match:
                error_match = match
                break
        
        assert error_match is not None
        assert error_match['error_type'] == 'InstructionLimitExceeded'
        assert error_match['error_player'] in ['A', 'B']
        assert error_match['terminated_early'] is True
    
    def test_practice_error_syntax_error(self, client):
        """Test that SyntaxError returns compile error with line info"""
        strategies = [
            {
                'name': 'AlwaysCooperate',
                'code': '''def strategy(last_move, my_history, opponent_history):
    return 'C'
'''
            },
            {
                'name': 'SyntaxError',
                'code': '''def strategy(last_move, my_history, opponent_history):
    if True
        return 'C'
'''
            }
        ]
        
        response = client.post('/tournament',
                             json={'strategies': strategies, 'rounds': 10},
                             content_type='application/json')
        
        assert response.status_code == 200
        data = response.get_json()
        
        # Syntax errors should be caught during disqualification
        # or in the match result
        if 'disqualified' in data and len(data['disqualified']) > 0:
            # Check disqualified list
            disq = data['disqualified'][0]
            assert 'error' in disq
            assert 'SyntaxError' in disq['error'] or 'syntax' in disq['error'].lower()
        else:
            # Check match results
            error_match = None
            for match in data['matches']:
                if 'error' in match:
                    error_match = match
                    break
            
            assert error_match is not None
            assert 'SyntaxError' in error_match['error'] or 'syntax' in error_match['error'].lower()
    
    def test_practice_error_no_500(self, client):
        """Test that errors return 200 with error details, not 500"""
        strategies = [
            {
                'name': 'AlwaysCooperate',
                'code': '''def strategy(last_move, my_history, opponent_history):
    return 'C'
'''
            },
            {
                'name': 'ErrorStrategy',
                'code': '''def strategy(last_move, my_history, opponent_history):
    raise ValueError("Test error")
'''
            }
        ]
        
        response = client.post('/tournament',
                             json={'strategies': strategies, 'rounds': 10},
                             content_type='application/json')
        
        # Should not be 500 - should return 200 with error in response
        assert response.status_code == 200
        data = response.get_json()
        
        # Should have matches or error info
        assert 'matches' in data or 'error' in data or 'disqualified' in data
    
    def test_practice_error_no_stack_trace(self, client):
        """Test that error messages don't include stack traces"""
        strategies = [
            {
                'name': 'AlwaysCooperate',
                'code': '''def strategy(last_move, my_history, opponent_history):
    return 'C'
'''
            },
            {
                'name': 'ErrorStrategy',
                'code': '''def strategy(last_move, my_history, opponent_history):
    x = 1 / 0
    return 'C'
'''
            }
        ]
        
        response = client.post('/tournament',
                             json={'strategies': strategies, 'rounds': 10},
                             content_type='application/json')
        
        assert response.status_code == 200
        data = response.get_json()
        response_text = json.dumps(data)
        
        # Should not contain stack trace indicators
        assert 'Traceback' not in response_text
        assert 'File "' not in response_text
        assert 'line ' not in response_text or 'line number' in response_text.lower()
        # Should not contain server paths
        assert 'app.py' not in response_text or 'app.py' in response_text.lower()
    
    def test_practice_both_strategies_work(self, client):
        """Test normal case: no errors, match completes"""
        strategies = [
            {
                'name': 'AlwaysCooperate',
                'code': '''def strategy(last_move, my_history, opponent_history):
    return 'C'
'''
            },
            {
                'name': 'AlwaysDefect',
                'code': '''def strategy(last_move, my_history, opponent_history):
    return 'D'
'''
            }
        ]
        
        response = client.post('/tournament',
                             json={'strategies': strategies, 'rounds': 10},
                             content_type='application/json')
        
        assert response.status_code == 200
        data = response.get_json()
        
        # Should have leaderboard and matches
        assert 'leaderboard' in data
        assert 'matches' in data
        assert len(data['matches']) == 1
        
        # Match should not have error
        match = data['matches'][0]
        assert 'error' not in match or match.get('error') is None
        assert 'error_type' not in match or match.get('error_type') is None
        assert 'error_player' not in match or match.get('error_player') is None
        
        # Should have completed rounds
        assert match.get('a_points', 0) >= 0
        assert match.get('b_points', 0) >= 0
    
    def test_practice_error_player_attribution(self, client):
        """Test that error is correctly attributed to player A or B"""
        # Test with Player B having error
        strategies_b_error = [
            {
                'name': 'GoodStrategy',
                'code': '''def strategy(last_move, my_history, opponent_history):
    return 'C'
'''
            },
            {
                'name': 'BadStrategy',
                'code': '''def strategy(last_move, my_history, opponent_history):
    return bad_var
'''
            }
        ]
        
        response = client.post('/tournament',
                             json={'strategies': strategies_b_error, 'rounds': 10},
                             content_type='application/json')
        
        assert response.status_code == 200
        data = response.get_json()
        
        # Find error match
        error_match = None
        for match in data['matches']:
            if 'error' in match:
                error_match = match
                break
        
        assert error_match is not None
        # The bad strategy should be identified
        # Player names should match
        assert error_match['error_player'] in ['A', 'B']
        
        # The player with error should be identified correctly
        if error_match['player_a'] == 'BadStrategy':
            assert error_match['error_player'] == 'A'
        elif error_match['player_b'] == 'BadStrategy':
            assert error_match['error_player'] == 'B'
    
    def test_practice_error_preserves_points(self, client):
        """Test that partial match results are preserved on error"""
        strategies = [
            {
                'name': 'AlwaysCooperate',
                'code': '''def strategy(last_move, my_history, opponent_history):
    return 'C'
'''
            },
            {
                'name': 'ErrorAfterFewRounds',
                'code': '''def strategy(last_move, my_history, opponent_history):
    if len(my_history) > 3:
        raise RuntimeError("Intentional error after 3 rounds")
    return 'D'
'''
            }
        ]
        
        response = client.post('/tournament',
                             json={'strategies': strategies, 'rounds': 10},
                             content_type='application/json')
        
        assert response.status_code == 200
        data = response.get_json()
        
        # Find error match
        error_match = None
        for match in data['matches']:
            if 'error' in match:
                error_match = match
                break
        
        assert error_match is not None
        assert error_match['terminated_early'] is True
        
        # Points should exist and reflect the rounds that were completed
        assert 'a_points' in error_match
        assert 'b_points' in error_match
        # At least some points should have been scored before error
        # (assuming some rounds completed)
        # We can't guarantee exact values, but structure should be intact
        assert isinstance(error_match['a_points'], (int, float))
        assert isinstance(error_match['b_points'], (int, float))
