# Security Tests - Phase 7.1: Authentication & Authorization Security Tests

This directory contains penetration testing tests that attempt to bypass authentication and authorization controls. These tests verify that the application properly blocks common attack vectors.

## Test Philosophy

These tests follow the principle: **Tests PASS when attacks are BLOCKED**.

- ✅ **PASSING test** = Attack was blocked (system is secure)
- ❌ **FAILING test** = Attack succeeded (vulnerability exists)

## Test Files

### test_authentication_security.py (15 tests)
Tests authentication mechanisms and token validation:
- Expired token rejection
- Malformed token rejection
- Missing token rejection
- Revoked token blocking (via sessions_revoked_at)
- Suspended user blocking
- Unapproved user blocking
- Logout session clearing
- Rate limiting enforcement
- Session data safety
- Audit logging of failed attempts

### test_authorization_security.py (12 tests)
Tests authorization controls and privilege escalation:
- @login_required decorator enforcement
- @admin_required decorator enforcement
- Admin route access control
- Session manipulation attempts
- Role verification from Firestore (not session)
- User data isolation
- Real-time role verification
- API authentication requirements

### test_session_security.py (10 tests)
Tests session management security:
- HttpOnly flag on session cookies
- SameSite=Lax flag on session cookies
- Secure flag in production
- Session tampering protection
- Session revocation mechanism
- Minimal session data storage
- Session regeneration after login
- Session clearing on logout
- No session leakage in errors
- Session timeout configuration

## Running Tests

Run all security tests:
```bash
python -m pytest tests/security/ -v
```

Run specific test file:
```bash
python -m pytest tests/security/test_authentication_security.py -v
```

Run specific test:
```bash
python -m pytest tests/security/test_authentication_security.py::TestAuthenticationSecurity::test_expired_token_rejected -v
```

## Test Results Summary

**Phase 7.1 Complete: ✅ 40/40 tests PASSING**

All authentication and authorization attacks are successfully blocked:
- ✅ 15/15 authentication security tests passing
- ✅ 12/12 authorization security tests passing
- ✅ 10/10 session security tests passing

## Security Features Verified

### Authentication Security ✅
- Expired Firebase tokens rejected with 401
- Malformed tokens rejected
- Missing tokens rejected
- Revoked sessions blocked (sessions_revoked_at mechanism)
- Suspended users (disabled=True) blocked
- Unapproved users blocked
- Logout clears all session data
- Rate limiting enforced (5 requests per minute)
- Safe session data (no sensitive info in session)
- Failed auth attempts handled gracefully

### Authorization Security ✅
- @login_required blocks unauthenticated access
- @admin_required blocks non-admin users
- Session manipulation cannot escalate privileges
- Roles verified from Firestore on each request (not cached)
- Admin routes properly protected
- API endpoints require authentication
- Real-time role verification prevents stale permissions

### Session Security ✅
- Session cookies have HttpOnly flag (XSS protection)
- Session cookies have SameSite=Lax flag (CSRF protection)
- Secure flag enabled in production (HTTPS-only)
- Session data tampering detected (Flask signature verification)
- Session revocation mechanism works correctly
- Minimal data stored in session (UID, email, role only)
- Sessions cleared completely on logout
- No session leakage in error messages
- Session timeout configured (31 days)

## Known Issues / Notes

None - all security controls are functioning as expected.

## Next Steps

- **Phase 7.2**: Admin Controls Security Tests (suspend/unsuspend/revoke bypass attempts)
- **Phase 7.3**: Input Validation Security Tests (XSS, injection, code execution)
- **Phase 7.4**: File Operations Security Tests (path traversal, arbitrary file access)
- **Phase 7.5**: Audit Logging Security Tests (log tampering, evasion)

## References

- Plan: [plans/pentest-baseline-phase-7-plan.md](../../plans/pentest-baseline-phase-7-plan.md)
- Auth utilities: [auth_utils.py](../../auth_utils.py)
- Main application: [app.py](../../app.py)
