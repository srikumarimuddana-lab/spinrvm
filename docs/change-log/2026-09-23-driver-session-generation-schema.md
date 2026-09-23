# Driver session generation schema

Date: 2026-09-23. Surface: backend. Domain: auth. PR: #5722, finding 6.

## Issue and root cause
The displaced phone retains a refresh credential and can acquire the newest
session identity. Refresh rows do not remember their issuing token generation.

## Remediation and impact
Migration 452 binds mobile refresh rows to a generation and adds a service-only
atomic driver-login RPC. It locks the user, advances the generation, and revokes
older mobile refresh rows. Admin credentials use a separate audience and table.
Existing signup, Firebase exchange, rotation, logout-all and reuse detection all
consume these rows; subsequent slices must preserve the captured generation.
No runtime calls this additive RPC yet. Existing RLS and lookup indexes remain.

## User experience and rollout
The default-off `driver_single_session_enabled` flag must remain off until every
API replica runs generation-aware refresh code. Once enabled, a driver sign-in
displaces ALL mobile sessions on the account, including rider sessions; rider
sign-in alone does not displace devices. Do not enable during an active trip.

## Files
- `backend/migrations/452_driver_session_generation.sql`: additive schema/RPC.
- `backend/tests/direct_pool/test_driver_session_generation.py`: native SQL behavior.
- This log: rollout and rollback boundary.

## Rollback
Set the flag false. Do not decrement generations or un-revoke credentials.
Users already displaced must authenticate again; schema can remain additive.

## Verification
Native tests first failed because migration 452 was absent. Native PostgreSQL
tests cover disabled rollout, generation revocation, access grants and concurrent
logins. No live database, staged ride or device tested.
