# Preserve refresh generations through mobile auth routes

Date: 2026-09-23. Surface: backend. Domain: auth. PR #5722, finding 6.

## Issue, cause and remediation
The helper binding is insufficient if the route adopts `users.token_version`
after another device logs in. All mobile credential issuers now pass the version
captured for their access token. Rotation passes its parent's version and checks
it before rotation and before returning credentials. A child inserted during
displacement remains on the obsolete generation and cannot refresh again.

## Impact and user experience
Blast radius: OTP login/signup, email login, account reactivation, Firebase driver
exchange and mobile refresh. Admin routes are unchanged. Valid sessions keep
rotating; superseded phones receive 401 without revoking the replacement phone.
The extra post-rotation user read can add latency; production P95 is unverified.

Before: rotation adopted the newest user version. After: parent version is immutable.

## Files
- `backend/routes/auth.py`: pass/check the generation.
- `backend/tests/test_p1_token_refresh.py`: pre/post-rotation login races and updated helper signature.
- This log: rollout and limits.

## Rollback and verification
Keep `driver_single_session_enabled=false` until every replica has all auth fixes.
Disable it to stop future displacement; never decrement generations or restore
revoked credentials. New route tests failed all three cases before the fix.
After the fix, 29 auth/refresh/session-integrity tests passed; Ruff passed.
No production database, build, device or production latency measurement was run.
