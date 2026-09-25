# Change Impact & Risk Log: exempt /auth/refresh from App Check

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (session requested by repo owner) |
| Surface(s) | backend |
| Domain (Sentry tag) | auth |
| PR / commit link | branch `claude/sleepy-ptolemy-yta2i1`; companion client PR #5777 |
| Related issue or gap ID | Riders/drivers logged out after the app sat in the background |

## 1. Issue / gap identified

Riders and drivers are signed out long before their 30-day refresh token expires. A refresh request with no App Check token gets `401 {"detail":"App Check token required"}` before the refresh token is examined. Both apps read any refresh 401 as a dead login and delete the session. Production enforcement came back on around 2026-09-19 (see `2026-09-21-admin-quests-app-check-logout.md`). The 2026-08-17 Android Auto log describes the same chain.

## 2. Root cause

`/api/v1/auth/refresh` was not in `_APP_CHECK_EXEMPT_PREFIXES`, while `send-otp`/`verify-otp` were. App Check tokens are often unavailable right after resume or in a headless context, and there is an open iOS App Check 403 issue in Sentry.

## 3. Fix / remediation

`/api/v1/auth/refresh` is exempt from App Check. Refresh still requires a 384-bit rotating refresh token and keeps its 20/min rate limit, rotation and reuse detection. CSRF handling is unchanged (the path is not in `_CSRF_EXEMPT_EXACT`).

## 4. Risk & impact on existing functionality

- Blast radius: backend auth; both mobile apps stop receiving App Check 401s on refresh. The admin dashboard uses `/api/admin/auth/refresh`, which was already exempt. No client code changes in this PR.
- Prefix match: `POST /auth/refresh` is the only route under the prefix. A comment in `middleware.py` warns future routes.
- Pinning tests: `test_appcheck_portal_exempt.py` (updated). Other App Check tests check only their own entries.
- Security trade-off (from `spinr-security-auditor`): App Check on refresh gave defense-in-depth against scripted replay of exfiltrated refresh tokens from outside the genuine app. After this change, rotation plus reuse detection is the control. No refresh-volume anomaly metric exists yet (suggested follow-up).
- No interaction with background loops, ride state, money, or insurance periods.

## 5. User-experience effect

Riders and drivers see fewer unexpected returns to the phone-number screen. This takes effect on the next backend deploy for all installed app versions. There is no copy change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/core/middleware.py` | Added `/api/v1/auth/refresh` to `_APP_CHECK_EXEMPT_PREFIXES` | App Check 401s were deleting valid sessions |
| `backend/tests/test_appcheck_portal_exempt.py` | Refresh asserted exempt; `/auth/me` still asserted enforced | Pin the new boundary |

## 7. Before / after

```python
# Before
    "/api/v1/auth/send-otp",
    "/api/v1/auth/verify-otp",
# After
    "/api/v1/auth/send-otp",
    "/api/v1/auth/verify-otp",
    "/api/v1/auth/refresh",
```

## 8. Rollback plan

There is no runtime toggle for a single exempt path. Rollback is a redeploy with the one line reverted, which is acceptable because the change writes no data. `APP_CHECK_ENFORCEMENT` stays the global switch.

## 9. Verification performed

- [x] `ruff check` / `ruff format` clean (pre-commit hook).
- [ ] pytest: not run. This container cannot reach PyPI (403 from the network policy). CI must run `tests/test_appcheck_*.py`.
- [x] Blast-radius grep (routes under the prefix, callers of refresh, other tests pinning the exempt list).
- [x] `spinr-security-auditor` review: exemption acceptable; collision comment added per its warning.

## 10. What was NOT verified

- Not run against a staging backend or a real device with a live Firebase App Check provider.
- The mid-session effect (fewer logouts) is expected from code reading and production `audit_logs`, not measured.

## Considered and withdrawn: scoping the refresh-token reuse cascade

Production `audit_logs` (read-only query) show ~28 `refresh_token_reuse_detected` cascades in 14 days. They are rider/driver clients replaying rotated tokens 12 min to 24 h after rotation (lost refresh responses), and one event signed out 11 sessions. A flagged change to revoke only the replayed token's rotation chain was written and then reverted in this PR, because the security review found a blocker. The WebSocket heartbeat (`routes/websocket.py` `heartbeat_task`) closes sockets only when `token_version` changes, so skipping the bump would leave an attacker's open socket alive indefinitely. The recommended fix is the client half of X8 (`proposed_refresh_token`, `refresh_successor_commitment_enabled`), which prevents the replay instead of weakening the theft response.
