"""
Pytest Configuration and Shared Fixtures
Provides test client and mocked Firebase/Firestore fixtures for all tests.
"""

import os
# Ensure a stable secret key exists before app.py is imported: in production
# mode (the default) app.py now requires FLASK_SECRET_KEY and raises without it.
os.environ.setdefault('FLASK_SECRET_KEY', 'test-secret-key')

import pytest
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime


@pytest.fixture
def app_instance():
    """Flask app instance configured for testing"""
    from app import app as flask_app
    flask_app.config['TESTING'] = True
    flask_app.config['SECRET_KEY'] = 'test-secret-key'
    # Run tournaments synchronously in tests (production dispatches them to a
    # background worker thread and returns a job id instead).
    flask_app.config['RUN_TOURNAMENTS_SYNC'] = True
    # Disable CSRF for testing
    flask_app.config['WTF_CSRF_ENABLED'] = False
    # Disable rate limiting for tests
    flask_app.config['RATELIMIT_ENABLED'] = False
    return flask_app


@pytest.fixture
def client(app_instance):
    """Flask test client"""
    # Disable rate limiting for tests
    from app import limiter
    limiter.enabled = False
    
    with app_instance.test_client() as client:
        yield client
    
    # Re-enable after tests
    limiter.enabled = True


@pytest.fixture
def mock_firestore_collection():
    """Mock Firestore collection for testing"""
    mock_collection = MagicMock()
    mock_doc = MagicMock()
    mock_doc_ref = MagicMock()
    
    # Mock document operations
    mock_doc_ref.get.return_value = mock_doc
    mock_doc_ref.set.return_value = None
    mock_doc_ref.update.return_value = None
    mock_doc_ref.delete.return_value = None
    
    mock_collection.document.return_value = mock_doc_ref
    mock_collection.stream.return_value = []
    mock_collection.order_by.return_value = mock_collection
    
    return mock_collection


@pytest.fixture
def mock_db(mock_firestore_collection):
    """Mock Firestore database"""
    mock_database = MagicMock()
    mock_database.collection.return_value = mock_firestore_collection
    return mock_database


@pytest.fixture
def mock_firebase_auth():
    """Mock Firebase auth"""
    with patch('firebase_admin.auth') as mock_auth:
        mock_auth.verify_id_token.return_value = {
            'uid': 'test-user-123',
            'email': 'test@example.com',
            'name': 'Test User'
        }
        yield mock_auth


@pytest.fixture
def authenticated_session(client):
    """Session with authenticated regular user"""
    with client.session_transaction() as sess:
        sess['user'] = {
            'uid': 'test-user-123',
            'email': 'test@example.com',
            'displayName': 'Test User',
            'role': 'user'
        }
    return client


@pytest.fixture
def admin_session(client):
    """Session with authenticated admin user"""
    with client.session_transaction() as sess:
        sess['user'] = {
            'uid': 'admin-user-123',
            'email': 'admin@example.com',
            'displayName': 'Admin User',
            'role': 'admin'
        }
    return client


@pytest.fixture
def mock_tournament_collection_with_data(mock_firestore_collection):
    """Mock tournament collection with sample data"""
    # Create mock documents
    mock_docs = []
    
    for i in range(3):
        mock_doc = MagicMock()
        mock_doc.id = f'tournament-{i}'
        mock_doc.to_dict.return_value = {
            'name': f'Test Tournament {i}',
            'winner': f'Strategy {i}',
            'participant_count': 5,
            'total_matches': 10,
            'run_date': datetime(2026, 2, 10, 12, 0, 0),
            'run_by': 'admin-uid-123',
            'selected_ids': ['user1', 'user2', 'user3'],
            'participants': [
                {
                    'name': f'Strategy {i}A',
                    'code': 'return "C"',
                    'player_email': 'user1@example.com',
                    'player_name': 'User One',
                    'user_id': 'user1'
                },
                {
                    'name': f'Strategy {i}B',
                    'code': 'return "D"',
                    'player_email': 'user2@example.com',
                    'player_name': 'User Two',
                    'user_id': 'user2'
                }
            ],
            'leaderboard': [
                {
                    'name': f'Strategy {i}A',
                    'wins': 8,
                    'losses': 2,
                    'draws': 0,
                    'points': 100,
                    'player_email': 'user1@example.com',
                    'player_name': 'User One',
                    'user_id': 'user1'
                }
            ]
        }
        mock_docs.append(mock_doc)
    
    mock_firestore_collection.stream.return_value = mock_docs
    mock_firestore_collection.order_by.return_value = mock_firestore_collection
    
    # Also set up document() to return specific docs
    def get_doc(doc_id):
        mock_doc_ref = MagicMock()
        if doc_id.startswith('tournament-'):
            idx = int(doc_id.split('-')[1])
            if idx < 3:
                mock_doc_ref.get.return_value = mock_docs[idx]
                mock_docs[idx].exists = True
            else:
                mock_not_found = MagicMock()
                mock_not_found.exists = False
                mock_doc_ref.get.return_value = mock_not_found
        else:
            mock_not_found = MagicMock()
            mock_not_found.exists = False
            mock_doc_ref.get.return_value = mock_not_found
        return mock_doc_ref
    
    mock_firestore_collection.document.side_effect = get_doc
    
    return mock_firestore_collection


@pytest.fixture
def patch_db(mock_db):
    """Patch firebase_config.db for all tests"""
    with patch('firebase_config.db', mock_db):
        yield mock_db


@pytest.fixture
def patch_get_user_role():
    """Patch get_user_role to return appropriate roles"""
    def mock_get_role(uid):
        if uid == 'admin-user-123':
            return 'admin'
        return 'user'
    
    with patch('auth_utils.get_user_role', side_effect=mock_get_role):
        with patch('app.get_user_role', side_effect=mock_get_role):
            yield mock_get_role
