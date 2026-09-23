# Refresh credentials retain their issuing generation

Date: 2026-09-23. Surface: backend. Domain: auth. PR #5722, finding 6.

## Issue, cause and fix
An old phone could exchange its retained refresh token for the current login's
generation, or replay a rotated token to revoke a newer login. Refresh issuance
now accepts the captured generation without rereading it. Mobile lookup compares
that binding against the user BEFORE reuse-cascade handling; legacy NULL means
generation zero. A generation-read database error returns retryable 503.

## Impact and user experience
Callers include mobile signup/login, Firebase exchange, rotation and admin auth.
Admin audiences keep their existing behavior. Old displaced mobile credentials
receive 401; transient database failures do not pretend that credentials expired.
The driver displacement RPC remains default-off until all replicas are updated.

Before: `lookup(old_refresh)` could return a credential from another generation.
After: old generation returns no credential and cannot cascade into the new one.

## Files
- `backend/utils/refresh_tokens.py`: immutable generation binding and validation.
- `backend/tests/test_refresh_generation_binding.py`: replay/race/error regressions.
- This log: deployment and verification boundaries.

## Rollback and verification
Disable `driver_single_session_enabled` to stop new displacement; never undo a
revocation or decrement a generation. Existing displaced users must sign in.
New tests first showed 5 failures and 2 passes. Final regression suite result is
recorded in the commit review. No production database or physical device tested.
