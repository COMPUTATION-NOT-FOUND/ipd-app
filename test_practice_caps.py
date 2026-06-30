"""Tests for practice mode caps enforcement (Phase 5)"""
import pytest
from app import app


class TestPracticeCaps:
    """Test server-side enforcement of practice mode limits"""
    
    @pytest.fixture
    def client(self):
        """Create test client"""
        app.config['TESTING'] = True
        with app.test_client() as client:
            # Practice endpoints now require login; attach a regular-user session.
            with client.session_transaction() as sess:
                sess['user'] = {
                    'uid': 'test-user-123',
                    'email': 'test@example.com',
                    'role': 'user',
                }
            yield client
    
    # The local app has NO strategy/round cap (it runs on the user's own machine), so the
    # former "too many strategies/rounds" rejection tests were removed.

    def test_tournament_rejects_too_few_strategies(self, client):
        """POST /tournament should reject < 2 strategies"""
        strategies = [
            {'name': 'OnlyOne', 'code': 'def strategy(e,u,o): return "C"'}
        ]
        
        response = client.post('/tournament',
            json={'strategies': strategies, 'rounds': 200})
        
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert 'at least 2' in data['error'].lower() or 'strategies required' in data['error'].lower()
    
    def test_tournament_rejects_invalid_strategy_name(self, client):
        """POST /tournament should reject XSS-like strategy names"""
        strategies = [
            {'name': '<script>alert("xss")</script>', 'code': 'def strategy(e,u,o): return "C"'},
            {'name': 'ValidStrategy', 'code': 'def strategy(e,u,o): return "D"'}
        ]
        
        response = client.post('/tournament',
            json={'strategies': strategies, 'rounds': 200})
        
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
    
    def test_tournament_accepts_valid_request(self, client):
        """POST /tournament should accept valid requests within limits"""
        strategies = [
            {'name': 'CooperateStrategy', 'code': 'def strategy(e,u,o): return "C"'},
            {'name': 'DefectStrategy', 'code': 'def strategy(e,u,o): return "D"'}
        ]
        
        response = client.post('/tournament',
            json={'strategies': strategies, 'rounds': 100})
        
        assert response.status_code == 200
        data = response.get_json()
        assert 'leaderboard' in data
        assert 'matches' in data
