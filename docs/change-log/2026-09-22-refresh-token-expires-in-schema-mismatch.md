# Change Impact & Risk Log — `/auth/refresh` client-side `expires_in` schema mismatch

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-22 |
| Author | Claude Code session (daily `/sentry-triage --severity-only` scan), on behalf of ittalenthire.ca@gmail.com |
| Surface(s) | backend, driver-app, rider-app (via `shared/store/authStore.ts`) |
| Domain (Sentry tag) | auth |
| PR / commit link | (this branch, `fix/refresh-token-expires-in-schema-mismatch`) |
| Related issue or gap ID | Sentry CRIMSON-SMOKE-7445-10F, -10Y, -SE |

## 1. Issue / gap identified

Every background token refresh on the driver app throws "Background token refresh returned invalid credentials" even though the backend successfully rotated a valid credential. The rejected (but already server-issued) token pair is discarded, and the client keeps retrying with a now-stale refresh token the backend has already marked replaced — risking the reuse-detection cascade (full token revocation, forced re-login) on a later retry.

Separately, the same bug exists in the *foreground* refresh path shared by both rider-app and driver-app (`shared/store/authStore.ts`), but produces no visible error: it silently computes `token_expires_at = NaN` and persists that to SecureStore on every foreground refresh.

## 2. Root cause

`backend/routes/auth.py`'s `RefreshResponse` model never included an `expires_in` field — only `access_expires_at`/`refresh_expires_at` as absolute ISO timestamps. (Contrast `AuthResponse`, used by login/OTP-verify, which does set `expires_in`.) Both client consumers of `/auth/refresh` — `driver-app/utils/backgroundAuth.ts` and `shared/store/authStore.ts`'s `refreshTokens()` — were written assuming the same response shape as `AuthResponse` and destructured a field that was always `undefined`:

- `backgroundAuth.ts` validated `!Number.isFinite(data.expires_in) || data.expires_in <= 0` before accepting the response — always true for `undefined`, so it threw on every call.
- `authStore.ts` destructured `expires_in` and passed it straight into `Date.now() + expiresIn * 1000` with no validation — silently producing `NaN`.

Introduced in `4b4b74308` ("fix(driver): renew expired credentials from background callbacks", 2026-09-15) for the driver-app side; the `authStore.ts` sibling bug has been live longer (not blamed to a single commit — the interface has always lacked `access_expires_at`).

## 3. Fix / remediation

- `backgroundAuth.ts`: validate and derive the access-token expiry from `data.access_expires_at` (an absolute ISO timestamp, `Date.parse()`) instead of a nonexistent relative `expires_in`.
- `shared/store/authStore.ts`: same fix in `refreshTokens()` — compute a relative duration from `access_expires_at` to match `publishTokensUnlocked`'s existing `expiresIn`-in-seconds contract (the same conversion the function already does elsewhere, line ~380, when reading a persisted absolute expiry back out of storage).
- `backend/routes/auth.py`: added `expires_in` to `RefreshResponse` as a belt-and-suspenders duplicate of `access_expires_at`, matching `AuthResponse`'s existing field — defense in depth for any other consumer that assumes the two response shapes match.
- **Adversarial review finding (`spinr-security-auditor`, fixed before this commit):** the first pass at the `authStore.ts` fix read `access_expires_at` off the response with no validation before persisting — the exact "silently compute NaN, report success" failure mode this PR exists to close, just on the foreground path instead of background. Added the same field/type/expiry validation `backgroundAuth.ts` already had; an invalid/missing shape now throws, which the existing catch treats as a transient failure (`return false`, session kept, no logout) rather than persisting corrupted state.

## 4. Risk & impact on existing functionality

- **Blast radius: multi-surface, isolated to `/auth/refresh`'s two client consumers.** Grepped the full repo for every construction of `RefreshResponse(` (one call site, updated) and every place that destructures a `/auth/refresh` JSON response (`backgroundAuth.ts`, `authStore.ts` — both fixed; admin-dashboard's separate `authStore.ts` already correctly reads `access_expires_at`, confirmed unaffected).
- No other reader/writer of the `refresh_tokens` table or the reuse-detection logic (`backend/utils/refresh_tokens.py`) was touched — this is a response-body field addition and two client bugfixes, not a change to token issuance/rotation/revocation logic.
- Adding `expires_in` to `RefreshResponse` is additive (new required field, but always populated by the single return statement in the same commit) — no existing consumer reads a field that could now be missing.

## 5. User-experience effect

Driver-facing (background) and rider+driver-facing (foreground, via shared `authStore.ts`): background location-tracking token renewal, which was silently failing on every attempt, now succeeds — a driver's live location/insurance-period tracking should stop degrading over a session. Not visible mid-session as a UI change; this is a background-reliability fix, not new UX. No copy/notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/auth.py` | `RefreshResponse` gains `expires_in`; return statement populates it | Belt-and-suspenders match to `AuthResponse`'s shape |
| `driver-app/utils/backgroundAuth.ts` | Validates/derives expiry from `access_expires_at`, not `expires_in` | Fix the always-throwing background refresh |
| `shared/store/authStore.ts` | `RefreshTokenResponse` interface + `refreshTokens()` derive expiry from `access_expires_at` | Fix the silent `NaN` foreground refresh (rider-app + driver-app) |
| `backend/tests/test_p1_token_refresh.py` | New test asserting `expires_in` is present and correct | Regression guardrail for the backend field |
| `driver-app/__tests__/utils/backgroundAuth.test.ts` | Mocks now send `access_expires_at`, not `expires_in`, matching the real backend contract | Close the "theater coverage" gap the Sentry investigation found — the old mocks asserted the code correctly handles a shape the backend never actually sends |
| `driver-app/__tests__/store/authStore.refreshRace.test.ts` | Same mock-shape fix (5 occurrences) + new test: malformed `/auth/refresh` response is rejected (`refreshTokens()` returns `false`, no state persisted) | Mock-shape fix, and a regression guardrail for the validation gap the security review found |
| `driver-app/__tests__/store/authStore.initialize.test.ts` | Same mock-shape fix, 2 occurrences | Same reason |

## 7. Before / after

```ts
// Before (driver-app/utils/backgroundAuth.ts)
if (typeof data.token !== 'string' || !data.token || typeof data.refresh_token !== 'string' ||
    !data.refresh_token || !Number.isFinite(data.expires_in) || data.expires_in <= 0) {
  throw new Error('Background token refresh returned invalid credentials');
}
// ...
await SecureStore.setItemAsync('token_expires_at', String(Date.now() + data.expires_in * 1000), sessionKeychainOptions);

// After
const accessExpiresAtMs = Date.parse(data.access_expires_at);
if (typeof data.token !== 'string' || !data.token || typeof data.refresh_token !== 'string' ||
    !data.refresh_token || !Number.isFinite(accessExpiresAtMs) || accessExpiresAtMs <= Date.now()) {
  throw new Error('Background token refresh returned invalid credentials');
}
// ...
await SecureStore.setItemAsync('token_expires_at', String(accessExpiresAtMs), sessionKeychainOptions);
```

```ts
// Before (shared/store/authStore.ts)
const { token, refresh_token: newRefresh, expires_in, csrf_token } = res.data as RefreshTokenResponse;
await publishTokensUnlocked(token, newRefresh, expires_in, csrf_token, false);

// After (as reviewed and fixed — see the adversarial-review note above)
const { token, refresh_token: newRefresh, access_expires_at, csrf_token } = res.data as RefreshTokenResponse;
const accessExpiresAtMs = Date.parse(access_expires_at);
if (typeof token !== 'string' || !token || typeof newRefresh !== 'string' || !newRefresh ||
    !Number.isFinite(accessExpiresAtMs) || accessExpiresAtMs <= Date.now()) {
  throw new Error('Token refresh returned invalid credentials');
}
const expiresIn = (accessExpiresAtMs - Date.now()) / 1000;
await publishTokensUnlocked(token, newRefresh, expiresIn, csrf_token, false);
```

## 8. Rollback plan

`git revert` is a complete rollback: no data migration, no config/flag, no Stripe/wallet/ride-state interaction. The backend field addition is additive and harmless to revert (older clients never read it); reverting the two client fixes returns to the prior (broken but not newly-broken) behavior.

## 9. Verification performed

- [x] Automated tests run — backend: `pytest tests/test_p1_token_refresh.py tests/test_admin_auth_coverage_gap.py tests/test_auth_session_id_integrity.py tests/test_appcheck_portal_exempt.py tests/test_verify_otp_login_flow.py` (62 passed); driver-app: `npx jest __tests__/utils/backgroundAuth.test.ts __tests__/store/authStore.refreshRace.test.ts __tests__/store/authStore.initialize.test.ts` (57 passed, 3 suites — includes the new malformed-response regression test)
- [ ] Manual repro steps followed in staging — not performed; no staging/live Supabase access in this environment
- [x] Blast-radius grep performed — every `RefreshResponse(` construction, every `/auth/refresh` response consumer across driver-app/rider-app/shared/admin-dashboard, and every test mock shaping an `/auth/refresh` response (see §6)
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — auth/JWT surface; dispatched `spinr-security-auditor` before commit
- [x] Feature-flagged if user-visible and non-trivial — not applicable; this is a bugfix restoring already-intended behavior (background refresh was never meant to fail every time), not new user-visible behavior

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in

## Not changing but considered

`spinr-security-auditor` flagged (as a non-blocking warning, not this PR's bug) that `CookieManager.set_auth_cookie(response, token, ttl_minutes=15)` in the same handler is a hardcoded literal, not `settings.ACCESS_TOKEN_EXPIRE_MINUTES` — pre-existing, unrelated to the `expires_in` schema mismatch, and would desync only if `ACCESS_TOKEN_EXPIRE_MINUTES` is ever changed from 15. Left out of scope per CLAUDE.md's "keep each fix minimal" — worth a follow-up but not bundled into this fix.

## What was NOT verified

- Not tested against a real Supabase/backend deploy — verified via the full existing + new mocked unit-test suite on both sides of the contract (Pydantic model in Python, TS interface + call sites in JS), not a live end-to-end refresh call.
- The CRIMSON-SMOKE-7445-9 (refresh-token-reuse-detected) and CRIMSON-SMOKE-7445-ZR (iOS Firebase App Check 403) issues surfaced by the same daily Sentry scan are **not** addressed by this change — the former needs no code fix (ACTION_ITEMS.md C2, an already-tracked Sentry alert-rule gap), the latter needs Firebase-console triage this session has no access to. Both reported separately, not folded into this PR.
