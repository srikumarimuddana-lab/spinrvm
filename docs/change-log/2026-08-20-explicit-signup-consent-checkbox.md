# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | Claude (agent session) |
| Surface(s) | backend / rider-app / driver-app |
| Domain (Sentry tag) | auth |
| PR / commit link | branch `claude/spinr-mongodb-migration-u9y6iz` (not yet a PR — see task instructions) |
| Related issue or gap ID | ACTION_ITEMS.md A41 (consent-basis gap, piece 3); `docs/runbooks/legacy-migration-playbook.md` item #1 |

## 1. Issue / gap identified

Today's rider-app/driver-app signup flow (`login.tsx`) shows passive text — "By continuing, you
agree to our Terms of Service and Privacy Policy" — with the phrase styled as a link but with no
confirmed tappable action and no explicit checkbox/opt-in gesture. The backend
(`backend/routes/auth.py`'s `verify_otp`) auto-stamps `consent_version`/`consent_accepted_at` on
every new account unconditionally, with no user action behind the stamp.

## 2. Root cause

This was the original (never-revisited) signup design: `consent_version` was added in migration
`334_users_consent_version.sql` as an interim placeholder — "until a real consent screen lands" —
and the interim state simply never got replaced. `docs/audit/2026-08-20-legacy-consent-legal-sufficiency-factsheet.md`
§8 independently confirmed this is the same class of gap the legal-sufficiency fact-finding found
in the *old* app (a stored policy document with no recorded acceptance event for any specific
user) — the current Spinr app narrowly avoided being the identical gap only by degree (it at least
has passive text and an auto-stamped version), not by having actual evidence of a user gesture.

## 3. Fix / remediation

- **Both apps' `login.tsx`**: replaced the passive terms text with a real, unchecked-by-default
  checkbox (`accessibilityRole="checkbox"`, icon-based checked/unchecked state — not color alone,
  per WCAG 2.1 AA) that the user must actively check before "Send Verification Code" is enabled.
  The label links to the actual in-app `/legal?type=tos` and `/legal?type=privacy` screens — the
  same destination `legacy-consent-notice.tsx`'s existing "View Policy" link already uses, not a
  new destination.
- **Both apps' `otp.tsx`**: the checked state is carried from `login.tsx` as a route param and sent
  as `consent_accepted` on the `POST /auth/verify-otp` call that actually creates the account.
- **Backend (`backend/schemas.py`, `backend/routes/auth.py`)**: `VerifyOTPRequest` gained a new,
  additive `consent_accepted: bool = False` field (mirrors the existing `client_app` field's
  "optional, defaults for backward compatibility" pattern). `verify_otp`'s new-user-creation branch
  now rejects the whole signup (400, `errors.auth.consent_required`) if `consent_accepted` is not
  `True` — no row is created and no `consent_version`/`consent_accepted_at` is stamped. The
  existing-user login branch is untouched and never reads this field.

## 4. Risk & impact on existing functionality

**Blast-radius grep performed** — every caller of `VerifyOTPRequest` and every caller of
`POST /auth/verify-otp` (or `/api/auth/verify-otp`) across the repo, `grep -rln "auth/verify-otp\|verify_otp\b"` over `backend/`, `rider-app/`, `driver-app/`, `admin-dashboard/`, `frontend/`, `loadtest/`, plus a direct search for `VerifyOTPRequest(`:

| Caller | Live/production? | Impact |
|---|---|---|
| `rider-app/app/otp.tsx` | Yes | Updated in this change — now sends `consent_accepted`. |
| `driver-app/app/otp.tsx` | Yes | Updated in this change — now sends `consent_accepted`. |
| `backend/tests/test_verify_otp_login_flow.py` | Test only | Updated — helper now defaults `consent_accepted=True` so unrelated tests (existing-user login, OTP validation branches) are unaffected; 3 new tests added for the gating behavior itself. |
| `backend/tests/test_auth_send_otp.py` (one `VerifyOTPRequest(...)` construction) | Test only | Hits the DB-error path before the new-user branch — unaffected, confirmed by running it (16/16 pass). |
| `backend/tests/test_auth.py`, `test_rate_limits.py` (raw HTTP POSTs with wrong/short OTP codes) | Test only | All hit the OTP-invalid/lockout/validation paths before the consent check — confirmed unaffected by running them; `test_rate_limits.py`'s `status_code in (200, 400)` assertion is robust either way since the new consent-required error is also a 400. |
| `admin-dashboard/src/app/register/driver/page.tsx` (`fetch('/api/auth/verify-otp')`) | **Already broken, pre-existing, unrelated to this change** — `next.config.ts`'s `/api/auth/:path*` rewrite is an identity rewrite expecting a Next.js route handler under `src/app/api/auth/verify-otp/`, but no such file exists (only `set-cookie/route.ts` does) — this call already 404s today. | None — cannot be broken further by this change; it never reaches the backend at all. |
| `admin-dashboard/src/lib/api/auth.ts`'s `loginAdmin()` (calls the same broken `/api/auth/verify-otp` path) | **Dead code** — re-exported from `lib/api.ts`'s barrel but grepped for zero callers anywhere in the admin-dashboard UI. | None — unused. |
| `frontend/app/otp.tsx` | **Deprecated directory** (`frontend/DEPRECATED.md`: "Do Not Use", scheduled for deletion, 42% screen parity with rider-app, zero unique features) — not part of any live surface. | None — out of scope, matches CLAUDE.md's scope-discipline rule not to touch dead/deprecated code. |
| `loadtest/locustfile.py`'s `auth:verify-otp` POST | Load-test tooling, not production. | **Already broken independent of this change** — it posts `{"phone": ..., "otp": BASE_OTP}`, but `VerifyOTPRequest.code` (required, no default) is the actual field name, not `otp` — this call already 422s on every run today, before ever reaching the consent check. Not touched (out of scope; pre-existing bug). |

No other production caller of this endpoint/schema was found. The only two live callers
(`rider-app`/`driver-app` `otp.tsx`) are both updated in this change.

**What else reads/writes `consent_version`/`consent_accepted_at`**: `backend/routes/legacy_consent.py`
(`GET/POST /consent/status|accept`) reads/writes these same columns for the *separate*
re-prompt-existing-users flow — untouched by this change (explicitly out of scope per the task).
`backend/routes/auth.py`'s `firebase_auth_login` also auto-stamps `consent_version` on its own
new-user branch — found during this review, **left untouched**: it has zero callers in either
mobile app today (grepped both apps for `auth/firebase`/`firebase_auth_login`, no matches), so it's
not part of the live signup surface this task targets; noted here per CLAUDE.md's "notice unrelated
issues, don't silently fix them" rule rather than expanding scope.

**Ride/dispatch/payments/corporate/safety**: none touched. This change only affects one write path
(`users.consent_version`/`users.consent_accepted_at` on brand-new account creation) that no other
domain reads synchronously at signup time.

## 5. User-experience effect

- **Rider and driver, both apps, every brand-new signup, going forward.** This is a deliberate
  friction increase, not a bug: signup now requires one extra explicit action (checking a box) it
  did not require before. It is not visible mid-session to anyone already using the app — it only
  appears on the phone-entry screen, before any account exists.
- **Existing/returning users**: no visible change at all. The existing-user login branch of
  `verify_otp` never reads `consent_accepted`, and this task deliberately did not touch
  `legacy-consent-notice.tsx` (the separate re-prompt flow for already-registered users) or
  `CONSENT_VERSION` itself.
- **Copy**: "I agree to Spinr's Terms of Service and Privacy Policy" (rider-app, plain text) /
  i18n keys `login.consentPrefix` + existing `login.termsOfService`/`login.and`/`login.privacyPolicy`
  (driver-app, en/fr; es.json already lacks the entire `login` i18n section pre-existing this
  change — not introduced here, falls back to English same as every other string in that section).
  New error copy: "Please agree to the Terms of Service and Privacy Policy to continue." — added to
  both apps' `i18n/en.json` under `errors.auth.consent_required`, following the existing
  `ErrorKeys`/`message_key` convention exactly.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/schemas.py` | `VerifyOTPRequest` gained `consent_accepted: bool = False` | Additive request field for the explicit-consent signal |
| `backend/routes/auth.py` | New-user branch of `verify_otp` rejects (400) unless `consent_accepted is True`, before any DB write | Enforce the gesture requirement; never stamp consent without it |
| `backend/utils/error_keys.py` | Added `AUTH_CONSENT_REQUIRED` | New i18n error key for the new error path |
| `backend/tests/test_verify_otp_login_flow.py` | Helper now takes `consent_accepted` (default `True`); 3 new tests (reject-if-false, reject-if-omitted, existing-user-branch-ignores-field) | Cover the new gating behavior; keep unrelated tests green |
| `rider-app/app/login.tsx` | Passive terms text → explicit checkbox gating the continue button; ToS/Privacy links now navigate to `/legal` | The actual UI fix |
| `rider-app/app/otp.tsx` | Reads `consentAccepted` route param, sends `consent_accepted` on verify-otp | Carries the gesture to the account-creation call |
| `rider-app/i18n/en.json` | Added `errors.auth.consent_required` | New error copy |
| `rider-app/__tests__/loginConsentCheckbox.test.tsx` | New file, 6 tests | Frontend gating coverage |
| `driver-app/app/login.tsx` | Same checkbox fix, i18n-driven copy | The actual UI fix |
| `driver-app/app/otp.tsx` | Same route-param → request-field wiring | Carries the gesture to the account-creation call |
| `driver-app/i18n/en.json`, `fr.json` | Added `login.consentPrefix`; `en.json` also gets `errors.auth.consent_required` | New copy (`es.json` has no `login` section to extend — pre-existing gap) |
| `docs/runbooks/legacy-migration-playbook.md` | New `[RE-VERIFIED ...]` annotation on item #1 | Records piece 3 of the re-consent decision as built |
| `ACTION_ITEMS.md` | New `[RE-VERIFIED ...]` annotation under A41 | Same, in the backlog log |

## 7. Before / after

```tsx
// Before (rider-app/app/login.tsx) — passive text, no gesture, no gate
<View style={[styles.terms, { paddingBottom: insets.bottom + 16 }]}>
  <Text style={styles.termsText}>
    By continuing, you agree to our{' '}
    <Text style={styles.termsLink}>Terms of Service</Text>
    {' '}and{' '}
    <Text style={styles.termsLink}>Privacy Policy</Text>
  </Text>
</View>
// handleSendCode: if (!isValid || loading) return;
```

```tsx
// After — explicit, unchecked-by-default checkbox gating continue
<TouchableOpacity
  style={styles.consentRow}
  onPress={() => setConsentAccepted((c) => !c)}
  accessibilityRole="checkbox"
  accessibilityState={{ checked: consentAccepted, disabled: loading }}
  accessibilityLabel="I agree to Spinr's Terms of Service and Privacy Policy"
>
  <Ionicons name={consentAccepted ? 'checkbox' : 'square-outline'} ... />
  <Text style={styles.termsText}>
    I agree to Spinr&apos;s{' '}
    <Text style={styles.termsLink} onPress={() => router.push({ pathname: '/legal', params: { type: 'tos' } })}>
      Terms of Service
    </Text>{' '}and{' '}
    <Text style={styles.termsLink} onPress={() => router.push({ pathname: '/legal', params: { type: 'privacy' } })}>
      Privacy Policy
    </Text>
  </Text>
</TouchableOpacity>
// handleSendCode: if (!isValid || !consentAccepted || loading) return;
```

```python
# Before (backend/routes/auth.py) — unconditional stamp
else:
    logger.info("Creating new user")
    ...
    new_user = {
        ...
        "consent_version": CONSENT_VERSION,
        "consent_accepted_at": now_iso,
    }

# After — gated on an explicit gesture, or reject the whole signup
else:
    if not body.consent_accepted:
        raise SpinrException(
            message="Please agree to the Terms of Service and Privacy Policy to continue.",
            error_code=ErrorCode.VALIDATION_MISSING_FIELD,
            status_code=400,
            message_key=ErrorKeys.AUTH_CONSENT_REQUIRED,
        )
    logger.info("Creating new user")
    ...
    new_user = {
        ...
        "consent_version": CONSENT_VERSION,
        "consent_accepted_at": now_iso,
    }
```

## 8. Rollback plan

No feature flag was added — the task explicitly required the backend to *reject* new signups
without the checkbox, which is inherently not something that can be dark-shipped behind a flag
without contradicting the ask (a flagged-off state would mean unconditionally stamping consent
again, reintroducing the exact gap being closed). Rollback path if this needs to be reverted after
deploy:
- **Backend**: revert `backend/routes/auth.py`'s consent-gate block and `backend/schemas.py`'s
  `consent_accepted` field (a plain code revert is sufficient here — the field is additive and no
  data was migrated or mutated; no `users` row this change creates needs any data-level fix, since a
  rejected signup simply never created a row in the first place).
- **Frontend**: revert `login.tsx`/`otp.tsx` in both apps to restore the passive text and drop the
  gating — ships on the next OTA/app-store update.
- **Coordination risk worth flagging explicitly**: backend and frontend must roll forward/back
  together. If the backend's reject-without-consent lands before an updated mobile build reaches
  users (via OTA or app-store review lag), any device still running the old JS bundle would send no
  `consent_accepted` field (defaults `False`) and **every new signup from that old build would be
  rejected** until it updates. This is the deliberate, intended behavior of the fix once both sides
  are live, but during the rollout window it is a real availability risk for new-user signups on
  stale clients — this should be deployed with both apps' updates going out together (or the mobile
  builds shipped first) rather than the backend landing alone first.

## 9. Verification performed

- [x] **Automated tests run**:
  - Backend: `pytest tests/test_verify_otp_login_flow.py tests/test_auth_send_otp.py tests/test_legacy_consent_notice.py tests/test_auth.py tests/test_auth_remaining_endpoints.py tests/test_auth_repo.py tests/test_schema_contract.py tests/test_p1_auth_hardening.py tests/test_marketing_consent.py` — **146/146 passed**. Full-suite run (`pytest -q`) launched; see final report for its result.
  - `ruff check` on every touched Python file (`schemas.py`, `routes/auth.py`, `utils/error_keys.py`, `tests/test_verify_otp_login_flow.py`) — clean.
  - rider-app: full Jest suite (`npx jest --no-coverage`) — **560/560 passed**, including the 6 new `loginConsentCheckbox.test.tsx` tests. `npx tsc --noEmit` — clean.
  - driver-app: full Jest suite — **657/657 passed**, including the 4 new `loginConsentCheckbox.test.tsx` tests. `npx tsc --noEmit` — clean.
- [x] **Blast-radius grep performed** — see §4 above; every other caller of `VerifyOTPRequest`/`/auth/verify-otp` found and dispositioned.
- [x] **Reviewed against relevant CLAUDE.md conventions**: additive-schema-field pattern (mirrors `client_app`), `Do not silently swallow errors` (reject loudly with a clear message_key, never silently proceed), PIPEDA consent framing, WCAG 2.1 AA (accessibilityRole/State, icon-based state, ≥44pt tap target via `minHeight: 44`).
- [ ] **Feature-flagged**: deliberately not flagged — see §8 for why, and the coordination risk that creates.
- [ ] **Manual repro in staging**: not performed — no staging environment access in this session.

## 10. Sign-off

- [x] Rollback plan is concrete (§8), including the coordination risk it does *not* fully eliminate.
- [x] Blast radius is stated, not assumed (§4) — every caller found is named and dispositioned, including three pre-existing-broken/dead callers left untouched.
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5).

## What was NOT verified

- **No real device or simulator visual verification** — this session has no simulator/device access,
  matching the same standing gap already recorded for `legacy-consent-notice.tsx`
  (`docs/change-log/2026-08-19-legacy-consent-notice-mobile.md`) and the cold-start/profile-setup
  follow-up. The checkbox's actual rendered appearance (icon size, tap target, spacing against the
  existing brand styling) was reasoned about from the JSX/styles and cross-checked against this
  codebase's own established checkbox pattern (`driver-app/app/crc-consent.tsx`), not screenshotted.
- **This repo has no automated visual/snapshot regression tooling at all** (a standing gap — see
  `ACTION_ITEMS.md`) — so there is no tool that could have caught a visually-invisible regression
  even if one existed. This is stated explicitly per CLAUDE.md's rule rather than implying a
  screenshot check happened.
- **Not tested against a live Supabase instance** — all backend tests use `mock_supabase_client`/
  patched repository functions, never a real database.
- **Full backend `pytest -q` suite result was not in hand at the time this document was written** —
  the targeted auth-surface run (146/146) is confirmed; the full-suite run's outcome is reported
  separately once it completes (this file will be superseded by the final session report, not
  edited after the fact to backfill a result).
- **The `admin-dashboard/register/driver/page.tsx` and `loadtest/locustfile.py` callers were
  confirmed already-broken by reading their code, not by actually executing them** — no live
  admin-dashboard or load-test run was performed to double-check that conclusion.
- **No manual QA pass in a staging environment** — verification is automated-test-only.
