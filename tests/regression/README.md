# Regression Test Suite

This directory contains the consolidated regression test suite for the Prisoner's Dilemma Tournament Simulator.

## What Are Regression Tests?

Regression tests verify that:
- Core functionality remains stable across changes
- Determinism is preserved (same seed → same results)
- Security contracts (PII redaction, XSS protection) remain enforced
- Baseline behaviors and performance characteristics are maintained

## Test Files

### Security Tests
- `test_security_pii_redaction.py` - PII redaction in public APIs
- `test_security_xss_validation.py` - XSS validation and safe rendering
- `test_security_credential_hardening.py` - Credential loading and security

### Deterministic Tournament Tests
- `test_deterministic_tournament.py` - Deterministic tournament runs

### Core Simulation Tests
- `test_coresim_n_cores.py` - N-core OS simulator with baseline checks
- `test_coresim_learned_payoff_tensor.py` - Learned payoff tensor integration
- `test_coresim_cache_integration.py` - Cache hierarchy integration
- `test_coresim_pe_cores.py` - P/E core differentiation

### Cache Tests
- `test_cache_ui_toggles.py` - Cache UI toggle integration

### Admin Tests
- `test_admin_coresim_options.py` - Admin UI core simulation integration
- `test_admin_logs_contract.py` - Admin log viewer endpoint & UI patterns

### Practice/Error Tests
- `test_practice_error_contract.py` - Error handling & attribution patterns

### Core Determinism Tests
- `test_cache_model_determinism.py` - Cache behavior determinism with seeding

## Running Regression Tests

```bash
# Run all regression tests
python -m pytest -m regression

# Or run from this directory
python -m pytest tests/regression

# Run specific test file
python -m pytest tests/regression/test_coresim_n_cores.py -v

# Quick check (no verbose)
python -m pytest tests/regression -q
```

## Expected Counts

As of 2026-02-22:
- **136 regression tests** across all files
- All tests should pass deterministically
- Expected runtime: ~15-30 seconds

## Writing New Regression Tests

1. Add new test file to this directory
2. Add `pytestmark = pytest.mark.regression` after imports
3. Use deterministic strategies from `deterministic_strategies.py`
4. Use shared fixtures from `conftest.py` (don't redefine `client`, etc.)
5. For baseline checks, prefer asserting relationships (>, <) over exact floats unless truly stable

## Test Fixtures

Shared fixtures available from `conftest.py`:
- `app_instance` - Flask app configured for testing
- `client` - Flask test client
- `mock_firestore_collection` - Mock Firestore collection
- `mock_db` - Mock Firestore database
- `authenticated_session` - Client with regular user session
- `admin_session` - Client with admin user session

## Guidelines

- **Determinism First**: All regression tests must use fixed seeds (`seed=42`, `seed=100`, etc.)
- **No Network**: Tests must not require network/Firebase connectivity
- **Fast**: Individual tests should complete in <1 second
- **Independent**: Tests must not depend on execution order
- **Clear Failures**: Assertions should have descriptive messages
