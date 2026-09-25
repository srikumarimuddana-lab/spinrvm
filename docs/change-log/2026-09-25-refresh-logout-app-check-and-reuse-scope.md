# Change Impact & Risk Log: refresh App Check exemption and reuse scope

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (session requested by repo owner) |
| Surface(s) | backend |
| Domain (Sentry tag) | auth |
| PR / commit link | branch `claude/sleepy-ptolemy-yta2i1` (commits `87ed9ba`, `b69e56c`); companion client PR #5777 |
| Related issue or gap ID | Riders/drivers logged out after the app sat in the background, and on several devices at once |

## 1. Issue / gap identified

Riders and drivers are signed out long before their 30-day refresh token expires, sometimes on every device at once. Two backend causes are confirmed:

- **App Check on refresh.** A refresh with no App Check token gets `401 {"detail":"App Check token required"}` before the refresh token is examined. Both apps read any refresh 401 as a dead login and delete the session. Production enforcement came back on around 2026-09-19 (see `2026-09-21-admin-quests-app-check-logout.md`). The 2026-08-17 Android Auto log describes the same chain.
- **Reuse cascade after a lost refresh response.** Read-only production query of `audit_logs` (`action = 'refresh_token_reuse_detected'`), last 30 days: rider-audience replays of **rotated** tokens 12 min, 15-60 min, 1-6 h and 6-24 h after rotation, from `okhttp` (Android rider), `Spinr/25` and `SpinrDriver/20..34` (iOS) clients. The 14-day count is 28 events; one event revoked 11 sessions. These are the user's own devices replaying a token whose rotation response they never received. Outside the 600 s grace window, each one ran the all-sessions cascade.

## 2. Root cause

1. `/api/v1/auth/refresh` was not in `_APP_CHECK_EXEMPT_PREFIXES`, while `send-otp`/`verify-otp` were. App Check tokens are often unavailable right after resume or in a headless context, and the open iOS App Check 403 issue in Sentry adds to that.
2. `_handle_refresh_token_reuse` treats every rotated-token replay past the grace window as theft and revokes every session. The clients do not send `proposed_refresh_token`, so the X8 committed-replay recovery (`refresh_successor_commitment_enabled`, currently `false` in production) cannot help yet.

## 3. Fix / remediation

1. `/api/v1/auth/refresh` is exempt from App Check. Refresh still requires a 384-bit rotating refresh token, keeps its 20/min rate limit, rotation and reuse detection.
2. New `app_settings` flag `refresh_reuse_chain_scope_enabled` (default **off**). When on, a replayed **rotated** rider/driver token revokes only the tokens rotated forward from it (its `replaced_by` chain), records them in the audit row for the existing dedupe, and leaves the user's other devices signed in. Everything else keeps the all-sessions cascade: admin tokens, tokens revoked without rotation, an unreadable flag, a successor missing from the scan, a cycle, or any revoke failure.

## 4. Risk & impact on existing functionality

Blast-radius grep:
- `_APP_CHECK_EXEMPT_PREFIXES` is prefix-matched. The only route under `/api/v1/auth/refresh` is `POST /refresh` (`routes/auth.py`). Pinning tests: `test_appcheck_portal_exempt.py` (updated), plus `test_appcheck_public_tracking_exempt.py`, `test_appcheck_webhooks_exempt.py`, `test_admin_surge_auto_mount.py` and `test_branding_route.py`, which only check their own entries.
- `_handle_refresh_token_reuse` has one caller, `lookup_refresh_token`, which is used by `routes/auth.py` (mobile refresh) and `routes/admin/auth.py` (admin refresh). The chain scope never applies to the admin audience.
- `audit_logs` rows for this action are read by `_reuse_already_handled`. The chain-scope row uses the same `replayed_row_id` / `cascade_revoked_row_ids` / `cascade_ok` keys and adds `scope: "rotation_chain"`.
- Clients (`shared/api/client.ts`, `shared/store/authStore.ts`, `driver-app/utils/backgroundAuth.ts`, `driver-app/lib/androidAuto/`) are unchanged by this PR. They simply stop receiving App Check 401s on refresh.

What could regress:
- **App Check exemption:** a stolen refresh token can now be exchanged from a non-genuine client. The token is still single-use (rotation) and replay still triggers detection. Blast radius: backend auth, both mobile apps.
- **Chain scope (only once the flag is on):** when a real theft is detected, the attacker's current access token (≤15 min TTL) is no longer invalidated by a `token_version` bump. Their refresh chain is revoked. The user's other devices are not signed out.
- No interaction with background loops, ride state, money, or insurance periods.

## 5. User-experience effect

- Riders and drivers: fewer unexpected returns to the phone-number screen, effective on the next backend deploy for all installed app versions. No copy change.
- Visible mid-session: yes, as the absence of a logout. Nothing new is shown.
- The chain scope is invisible until the flag is turned on.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/core/middleware.py` | Added `/api/v1/auth/refresh` to `_APP_CHECK_EXEMPT_PREFIXES` | App Check 401s were deleting valid sessions |
| `backend/tests/test_appcheck_portal_exempt.py` | Refresh now asserted exempt; `/auth/me` still asserted enforced | Pin the new boundary |
| `backend/utils/refresh_tokens.py` | `_chain_scope_enabled`, `_revoke_rotation_chain`, `_insert_reuse_audit`; flagged branch in `_handle_refresh_token_reuse` | Stop one device's lost response logging out every device |
| `backend/tests/test_refresh_token_reuse_detection.py` | 4 new tests (6 cases) | Chain-only revoke, flag off, fallbacks, admin/unrotated exclusions |

## 7. Before / after

```python
# Before: middleware.py
    "/api/v1/auth/send-otp",
    "/api/v1/auth/verify-otp",
# After
    "/api/v1/auth/send-otp",
    "/api/v1/auth/verify-otp",
    "/api/v1/auth/refresh",
```

```python
# Before: any rotated replay past 600 s
await _handle_refresh_token_reuse(row)   # token_version+1, revoke ALL, kick ALL sockets
# After, with refresh_reuse_chain_scope_enabled = true and a rider/driver rotated row
chain_ids = await _revoke_rotation_chain(row)   # revoke only row.replaced_by -> ... -> head
# on any failure: falls through to the unchanged all-sessions cascade
```

Scenario: a rider has phone A and tablet B. Phone A refreshes at 09:00; the response is lost when the app is backgrounded. At 14:00 phone A replays the old token.
- Before: A and B are both logged out, and every refresh token for the user is revoked.
- After (flag on): the orphaned successor from 09:00 is revoked and A signs in again. B is untouched.

## 8. Rollback plan

- Chain scope: set `refresh_reuse_chain_scope_enabled` to `false` (or remove it) in the `settings` row `id = 'app_settings'`. It takes effect on the next replay, with no deploy.
- App Check exemption: no runtime toggle for a single path. Rollback is a redeploy with the one line reverted. This is acceptable because the change writes no data. `APP_CHECK_ENFORCEMENT` stays the global kill switch.

## 9. Verification performed

- [x] Automated tests: this container cannot reach PyPI (403 from the network policy), so pytest could not run. The 44 test cases in `test_refresh_token_reuse_detection.py` were run under a stdlib-only harness with stubbed `loguru`/`db`/config modules: all new cases pass, and all existing cascade cases pass. The 4 failures are identical on the pre-change code, caused by the missing `sentry_sdk` module and a stubbed `pg_error_code`. CI must run the real suite.
- [x] `ruff check` / `ruff format` clean on changed files (pre-commit hook).
- [x] Blast-radius grep performed (Section 4).
- [x] Reviewed against CLAUDE.md: loguru conventions, no PII in logs, errors logged at `error` and falling back to the conservative path.
- [x] Chain scope is feature-flagged, default off. The App Check exemption is not flagged; the reason is in Section 8.
- [x] `spinr-security-auditor` run against the diff (result recorded in the PR).

## 10. What was NOT verified

- Not run against real Supabase or a staging backend, and no real device was tested.
- The App Check exemption was not exercised end-to-end with a real Firebase App Check provider.
- The chain walk loads at most 1000 `refresh_tokens` rows per user. A user with more, and the chain beyond that limit, falls back to the full cascade. That is safe, but the fallback rate was not measured.
- Whether real theft ever appears in the 28 recent events was inferred from user agents and timing, not proven.
