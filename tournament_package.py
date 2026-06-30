"""
Tournament Package Builder and Validator

Creates and validates tournament export packages with stable schema versioning,
hashing, and redaction support.

Schema Version: tournament_package_v1
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, Any, Tuple, List, Optional


def canonical_json(obj: Dict[Any, Any]) -> str:
    """
    Serialize dict to stable JSON with sorted keys for deterministic hashing.
    
    Args:
        obj: Dictionary to serialize
        
    Returns:
        JSON string with sorted keys at all levels
        
    Example:
        >>> canonical_json({'z': 1, 'a': 2})
        '{"a": 2, "z": 1}'
    """
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def sha256_text(text: str) -> str:
    """
    Compute SHA-256 hash of text string.
    
    Args:
        text: String to hash
        
    Returns:
        Hex digest (64 characters)
        
    Example:
        >>> sha256_text("hello")
        '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824'
    """
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def sha256_bytes(data: bytes) -> str:
    """
    Compute SHA-256 hash of bytes.
    
    Args:
        data: Bytes to hash
        
    Returns:
        Hex digest (64 characters)
        
    Example:
        >>> sha256_bytes(b"hello")
        '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824'
    """
    return hashlib.sha256(data).hexdigest()


def build_tournament_package(
    tournament_data: Dict[str, Any],
    *,
    tournament_id: Optional[str] = None,
    redact: bool = False,
    include_code: bool = True,
    include_pii: bool = False
) -> Dict[str, Any]:
    """
    Build tournament export package from tournament_results document.
    
    Args:
        tournament_data: Tournament results document from Firestore
        tournament_id: Optional tournament document ID for traceability
        redact: If True, removes code and PII (overrides include_code and include_pii)
        include_code: If True, includes strategy code (default True)
        include_pii: If True, includes email and display_name (default False)
        
    Returns:
        Tournament package dict conforming to tournament_package_v1 schema
        
    Example:
        >>> data = {'name': 'Test', 'rounds': 100, ...}
        >>> pkg = build_tournament_package(data, tournament_id='abc123')
        >>> pkg['schema_version']
        'tournament_package_v1'
    """
    # Handle redact flag which overrides individual flags
    if redact:
        include_code = False
        include_pii = False
    
    # Build participants with stable IDs
    # Map persisted field names to package schema names
    participants = []
    for p in tournament_data.get('participants', []):
        # Handle both 'label' (test fixtures) and 'name' (actual persisted format)
        label = p.get('label') or p.get('name', '')
        code = p.get('code', '')
        
        # Compute stable_id from label + code using canonical JSON to avoid collisions
        stable_id = sha256_text(canonical_json({'label': label, 'code': code}))
        code_sha256 = sha256_text(code)
        
        participant = {
            'label': label,
            'stable_id': stable_id,
            'code_sha256': code_sha256,
            'meta': {}
        }
        
        # Include code if requested
        if include_code:
            participant['code'] = code
        
        # Include PII if requested (user_id, email, display_name)
        # Map field names from persisted format - check ALL possible field name variants
        if include_pii:
            participant['meta']['user_id'] = p.get('user_id', '')
            # Check for player_email (actual persisted format), user_email, or email (legacy)
            participant['meta']['email'] = p.get('player_email') or p.get('user_email') or p.get('email', '')
            # Check for player_name (actual persisted format), user_display_name, or display_name (legacy)
            participant['meta']['display_name'] = p.get('player_name') or p.get('user_display_name') or p.get('display_name', '')
        
        participants.append(participant)
    
    # Build random_cv section
    # Map from persisted format: random_cv_enabled, random_cv_summary, random_cv_top_trials
    random_cv_enabled = tournament_data.get('random_cv_enabled', False)
    random_cv = {
        'enabled': random_cv_enabled,
        'summary': tournament_data.get('random_cv_summary') if random_cv_enabled else None,
        'top_trials': tournament_data.get('random_cv_top_trials') if random_cv_enabled else None
    }
    
    # Build core_sim section  
    # Map from persisted format: core_simulation_config, core_simulation_results
    core_sim_config = tournament_data.get('core_simulation_config')
    core_sim_results = tournament_data.get('core_simulation_results')
    core_sim_enabled = core_sim_config is not None
    core_sim = {
        'enabled': core_sim_enabled,
        'config': core_sim_config if core_sim_enabled else None,
        'results': core_sim_results if core_sim_enabled else None
    }
    
    # Detect tournament format (2-player vs N-player)
    # N-player tournaments have 'format' field and 'payoff_model' instead of 'payoff_matrix'
    tournament_format = tournament_data.get('format', '2-player')
    
    # Build package (without integrity first)
    package = {
        'schema_version': 'tournament_package_v1',
        'exported_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'source': {
            'app_version': os.environ.get('APP_VERSION', 'unknown'),
            'python_version': sys.version  
        },
        'tournament': {
            # Map 'name' (persisted) → 'name' (package schema)
            'name': tournament_data.get('name') or tournament_data.get('tournament_name', ''),
            'rounds': tournament_data.get('rounds', 0),
            'weights': tournament_data.get('weights', {}),
            'seed_info': tournament_data.get('seed_info'),
            'id': tournament_id  # Include tournament ID for traceability (None if not provided)
        },
        'participants': participants,
        'results': {
            'winner': tournament_data.get('winner', ''),
            'leaderboard': [],  # Will populate below with PII redaction
            'participant_count': tournament_data.get('participant_count', 0),
            'total_matches': tournament_data.get('total_matches', 0)
        }
    }
    
    # Add format-specific fields
    if tournament_format == 'n-player':
        # N-player tournament fields
        package['format'] = 'n-player'
        package['tournament']['payoff_model'] = tournament_data.get('payoff_model', {})
        package['tournament']['group_size'] = tournament_data.get('group_size')
        # N-player tournaments don't use modes, discount_factor, stochastic_prob, payoff_matrix
    else:
        # 2-player tournament fields (backward compatible - no explicit format field)
        package['tournament']['modes'] = tournament_data.get('modes', [])
        package['tournament']['discount_factor'] = tournament_data.get('discount_factor')
        package['tournament']['stochastic_prob'] = tournament_data.get('stochastic_prob')
        package['tournament']['payoff_matrix'] = tournament_data.get('payoff_matrix', {})
        
        # Only include random_cv and core_sim for 2-player tournaments
        package['random_cv'] = random_cv
        package['core_sim'] = core_sim
    
    # CRITICAL: Redact PII from leaderboard entries
    # Leaderboard may contain player_email, player_name, user_id
    for entry in tournament_data.get('leaderboard', []):
        entry_copy = entry.copy()
        
        # Remove PII fields if not including PII
        if not include_pii:
            entry_copy.pop('player_email', None)
            entry_copy.pop('player_name', None)
            entry_copy.pop('user_id', None)
        
        package['results']['leaderboard'].append(entry_copy)
    
    # Compute integrity hash (excluding integrity field itself)
    package_hash = sha256_text(canonical_json(package))
    package['integrity'] = {
        'package_sha256': package_hash
    }
    
    return package


def validate_tournament_package(pkg: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Validate tournament package conforms to schema.
    
    Args:
        pkg: Tournament package to validate
        
    Returns:
        Tuple of (success: bool, error_message: str)
        If success is True, error_message is empty string
        If success is False, error_message describes the validation error
        
    Example:
        >>> valid, error = validate_tournament_package(pkg)
        >>> if not valid:
        ...     print(f"Validation failed: {error}")
    """
    # Determine tournament format before field validation (N-player omits random_cv/core_sim)
    pkg_format = pkg.get('format', '2-player')

    # Check required top-level fields
    required_fields = [
        'schema_version',
        'exported_at',
        'source',
        'tournament',
        'participants',
        'results',
        'integrity'
    ]
    if pkg_format != 'n-player':
        required_fields += ['random_cv', 'core_sim']

    for field in required_fields:
        if field not in pkg:
            return False, f"Missing required field: {field}"
    
    # Check schema version
    if pkg['schema_version'] != 'tournament_package_v1':
        return False, f"Invalid schema_version: expected 'tournament_package_v1', got '{pkg['schema_version']}'"
    
    # Check participants is non-empty list
    if not isinstance(pkg['participants'], list):
        return False, "Field 'participants' must be a list"
    
    if len(pkg['participants']) == 0:
        return False, "Field 'participants' cannot be empty"
    
    # Check each participant has required fields
    for i, participant in enumerate(pkg['participants']):
        if 'stable_id' not in participant:
            return False, f"Participant {i} missing required field: stable_id"
        if 'code_sha256' not in participant:
            return False, f"Participant {i} missing required field: code_sha256"
        if 'label' not in participant:
            return False, f"Participant {i} missing required field: label"
    
    # --- ENHANCED VALIDATION: Check nested tournament fields (Code Review Issue #1) ---
    tournament = pkg.get('tournament', {})
    
    # Validate tournament.rounds
    if 'rounds' not in tournament:
        return False, "Missing required field: tournament.rounds"
    
    rounds = tournament['rounds']
    if not isinstance(rounds, int):
        return False, f"Field 'tournament.rounds' must be an integer, got {type(rounds).__name__}"
    
    if rounds <= 0:
        return False, f"Field 'tournament.rounds' must be greater than 0, got {rounds}"
    
    # Validate tournament.discount_factor (optional but if present must be valid)
    if 'discount_factor' in tournament and tournament['discount_factor'] is not None:
        discount_factor = tournament['discount_factor']
        if not isinstance(discount_factor, (int, float)):
            return False, f"Field 'tournament.discount_factor' must be a number, got {type(discount_factor).__name__}"
        if not (0 <= discount_factor <= 1):
            return False, f"Field 'tournament.discount_factor' must be between 0 and 1, got {discount_factor}"
    
    # Validate tournament.stochastic_prob (optional but if present must be valid)
    if 'stochastic_prob' in tournament and tournament['stochastic_prob'] is not None:
        stochastic_prob = tournament['stochastic_prob']
        if not isinstance(stochastic_prob, (int, float)):
            return False, f"Field 'tournament.stochastic_prob' must be a number, got {type(stochastic_prob).__name__}"
        if not (0 <= stochastic_prob <= 1):
            return False, f"Field 'tournament.stochastic_prob' must be between 0 and 1, got {stochastic_prob}"
    
    # Validate format-specific tournament fields
    if pkg_format == 'n-player':
        # N-player packages use payoff_model instead of payoff_matrix
        if 'payoff_model' not in tournament:
            return False, "Missing required field: tournament.payoff_model"
    else:
        # Validate tournament.payoff_matrix for 2-player packages
        if 'payoff_matrix' not in tournament:
            return False, "Missing required field: tournament.payoff_matrix"

        payoff_matrix = tournament['payoff_matrix']
        if not isinstance(payoff_matrix, dict):
            return False, f"Field 'tournament.payoff_matrix' must be a dict, got {type(payoff_matrix).__name__}"

        # Check required payoff matrix keys
        required_payoff_keys = ['CC', 'CD', 'DC', 'DD']
        for key in required_payoff_keys:
            if key not in payoff_matrix:
                return False, f"Field 'tournament.payoff_matrix' missing required key: {key}"
    
    # Validate tournament.weights
    if 'weights' not in tournament:
        return False, "Missing required field: tournament.weights"
    
    weights = tournament['weights']
    if not isinstance(weights, dict):
        return False, f"Field 'tournament.weights' must be a dict, got {type(weights).__name__}"
    
    # Validate seed_info if present
    if 'seed_info' in tournament and tournament['seed_info'] is not None:
        seed_info = tournament['seed_info']
        if not isinstance(seed_info, dict):
            return False, f"Field 'tournament.seed_info' must be a dict, got {type(seed_info).__name__}"
        
        # If seed_info exists, check for tournament_seed
        if 'tournament_seed' in seed_info and seed_info['tournament_seed'] is not None:
            tournament_seed = seed_info['tournament_seed']
            if not isinstance(tournament_seed, int):
                return False, f"Field 'tournament.seed_info.tournament_seed' must be an integer, got {type(tournament_seed).__name__}"
    
    # Check integrity section
    if 'package_sha256' not in pkg['integrity']:
        return False, "Missing required field: integrity.package_sha256"
    
    # Verify integrity hash
    stored_hash = pkg['integrity']['package_sha256']
    
    # Recompute hash from package (excluding integrity field)
    pkg_copy = {k: v for k, v in pkg.items() if k != 'integrity'}
    recomputed_hash = sha256_text(canonical_json(pkg_copy))
    
    if stored_hash != recomputed_hash:
        return False, f"Integrity hash mismatch: stored={stored_hash[:16]}..., computed={recomputed_hash[:16]}..."
    
    # All checks passed
    return True, ""


def extract_run_config_from_package(pkg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract configuration suitable for replaying tournament.
    
    Args:
        pkg: Tournament package
        
    Returns:
        Dictionary with tournament configuration and participant labels
        
    Example:
        >>> config = extract_run_config_from_package(pkg)
        >>> config['rounds']
        100
        >>> config['participants']
        ['AlwaysCooperate', 'AlwaysDefect']
    """
    tournament = pkg['tournament']
    
    # Extract participant labels
    participant_labels = [p['label'] for p in pkg['participants']]
    
    pkg_format = pkg.get('format', '2-player')

    config = {
        'name': tournament['name'],
        'rounds': tournament['rounds'],
        'weights': tournament['weights'],
        'seed_info': tournament.get('seed_info'),
        'participants': participant_labels
    }

    if pkg_format == 'n-player':
        config['payoff_model'] = tournament.get('payoff_model', {})
        config['group_size'] = tournament.get('group_size')
    else:
        config['modes'] = tournament.get('modes', [])
        config['discount_factor'] = tournament.get('discount_factor')
        config['stochastic_prob'] = tournament.get('stochastic_prob')
        config['payoff_matrix'] = tournament.get('payoff_matrix', {})

    return config
