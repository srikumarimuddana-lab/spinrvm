# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude (agent session) |
| Surface(s) | rider-app |
| Domain (Sentry tag) | auth |
| PR / commit link | see PR description |
| Related issue or gap ID | `ACTION_ITEMS.md` N14, gap (a) |

## 1. Issue / gap identified

N14's backend flow (`POST /users/verify-email/request` + `POST /users/verify-email/confirm`,
`backend/routes/users.py`) was built, tested, and merged with **no rider-app UI calling either
endpoint** — no "verify your email" entry point, banner, or screen anywhere in `rider-app/`.
The capability existed but was completely undiscoverable to a rider.

## 2. Root cause

The prior session (commit `6268f4b8e`, "N14 — self-serve rider email verification
(backend-only)") was scoped backend-only by design, per its own docstring in
`backend/routes/users.py`: *"no rider-app UI exists yet to call them... Follow-up."* This PR is
that follow-up, scoped to rider-app UI only per the task brief — the backend flow is
unmodified.

## 3. Fix / remediation

**Design decision: a dedicated screen (`rider-app/app/verify-email.tsx`), not an inline
banner/modal.** This mirrors `app/otp.tsx` (the existing phone-OTP screen) — same code-entry
UI shape (4–6 digit boxes, shake-on-error, resend cooldown), same navigation pattern (pushed as
a `Stack.Screen`), same reliance on `@shared/api/client`'s `api` default export and
`RateLimitError`. `otp.tsx` being a dedicated screen for "email/phone code entry" is the
stronger precedent per the task brief, and a settings-screen entry point (not a home-screen
banner) keeps this **purely additive and optional** — no nag, no gate, discoverable only from
Account → Personal Info.

**Entry point:** `rider-app/app/(tabs)/account.tsx`'s existing "Personal Info" card already
displays the account's email (`Email` row) — verification status was added directly to that
row (a small "Verify" pill when unverified, a green "Verified" pill once verified) rather than
creating a second, duplicate email row or touching `settings.tsx` (which doesn't display email
at all today).

**Flow:** `verify-email.tsx` calls `POST /users/verify-email/request` on mount (no body — the
backend sends a code to whatever email is already on file). Response handling:
- `already_verified: true` → skips code entry, shows a "your email is already verified"
  confirmation screen, merges `email_verified: true` into the store.
- Normal send → shows the code-entry UI, states the 5-minute expiry (`OTP_EXPIRY_MINUTES` read
  from `backend/dependencies/__init__.py`, mirrored as a literal per otp.tsx's own convention of
  hardcoding `CODE_LENGTH`), and lets the rider tap Resend once a 30s cooldown clears.
- `PROFILE_EMAIL_MISSING` (400), `SYSTEM_SERVICE_UNAVAILABLE` (503), and the outer rate limit
  (429, `rider_email_verify_request_limit`, 3/hour) each render a specific, non-technical toast.

Confirm (`POST /users/verify-email/confirm`, `{ code }`) handles `AUTH_OTP_INVALID` (400),
`AUTH_OTP_EXPIRED` (400), and the shared OTP lockout (429, 5 failures/hour, same shape as the
phone flow's `_check_otp_lockout`) the same way `otp.tsx` already handles its own lockout.

**Error copy plugs into the existing i18n system, not ad hoc strings.** The backend's
`message_key` values for `AUTH_OTP_INVALID` / `AUTH_OTP_EXPIRED` / `SYSTEM_SERVICE_UNAVAILABLE`
already have friendly English copy in `rider-app/i18n/en.json` (`errors.auth.otp_invalid`,
`errors.auth.otp_expired`, `errors.system.service_unavailable`) — reused via a small
`tKey(messageKey, fallback)` lookup (mirroring the existing but currently-unused
`rider-app/lib/alert.ts` resolution order: i18n key first, then the backend's own English
`message`, then a generic fallback). This was deliberately **not** wired through the more common
`notifyError`/`getApiErrorMessage` path used elsewhere in the app, because that path surfaces
the backend's raw `message` field verbatim — which for `AUTH_OTP_INVALID`/`AUTH_OTP_EXPIRED` is
the literal sentinel string `"ERR_OTP_INVALID"` / `"ERR_OTP_EXPIRED"` (see
`backend/routes/auth.py` and `backend/routes/users.py` — both flows share this pattern), not
user-facing text. Regression-tested directly (see §9) — `errors.profile.email_missing` and
`errors.profile.email_verification_stale` were added to `en.json` since no rider-app flow had
exercised `PROFILE_EMAIL_MISSING` before.

**Known limitation, called out explicitly rather than glossed over:** `GET /auth/me`'s response
schema (`backend/schemas.py`'s `UserProfile` Pydantic model, returned via
`UserProfile(**current_user)` in `backend/routes/auth.py`) does **not** include `email_verified`
— it was never added to that model. This is a backend file and out of scope for this PR. Two
consequences, both documented in code comments at the read sites:
1. The Account screen's verification badge can't be sourced from a normal `/auth/me` refresh —
   only from a locally-merged value: the *confirm* endpoint's own response body already carries
   `email_verified: true`, so `verify-email.tsx` merges that directly into the auth store on
   success. This is enough to satisfy "no stale UI after a successful confirm" within the
   session.
2. **A full app restart re-fetches `/auth/me`, which still won't carry `email_verified`**, so a
   genuinely-verified rider's badge will read "not verified" again after a cold app relaunch,
   until the backend schema gains the field (tracked in the ACTION_ITEMS.md N14 update below).
   Today this has zero real-world impact — the migration (`252_users_email_verified.sql`)
   defaults every existing row to `false`, and this is the *first* UI able to flip it — but it's
   a real gap for the next backend session to close, not silently absorbed here.

## 4. Risk & impact on existing functionality

**Blast radius: isolated to rider-app, two existing files touched non-destructively, one new
screen, one new route registration, four new i18n keys.**

- `rider-app/app/(tabs)/account.tsx` — grepped for other consumers: this is a leaf route
  (`app/(tabs)/account.tsx`), not imported by any other component. The only behavior change to
  an *existing* code path is the `useFocusEffect`'s `/auth/me` refresh: it used to fully
  **replace** `state.user` on every tab focus (`setState({ user: userRes.data })`); it now
  **merges** (`setState((state) => ({ user: { ...state.user, ...userRes.data } }))`). This was
  a necessary fix, not incidental — without it, navigating back from `verify-email.tsx` to the
  Account tab would immediately wipe the just-set `email_verified: true` the moment the focus
  effect refetches `/auth/me` (whose response omits the field entirely). The merge is strictly
  additive: every key `/auth/me` *does* return (including explicit `null`/`false` values, e.g.
  `driver_onboarding_status` transitioning to `null`) still overwrites exactly as before, since
  spread copies present keys regardless of value — only a key genuinely **absent** from the
  response is now preserved instead of dropped. No other screen or store reads this same
  `useFocusEffect` block; it's local to this one component.
- `rider-app/app/_layout.tsx` — added one `<Stack.Screen name="verify-email" />` registration,
  alongside 20+ existing sibling screens in the same `<Stack>`. Purely additive; no existing
  `<Stack.Screen>` entries were reordered or modified.
- `rider-app/i18n/en.json` — added `errors.profile.email_missing` and
  `errors.profile.email_verification_stale` under the existing `errors.profile` namespace (new
  namespace, doesn't touch `errors.auth`/`errors.system`/etc., which are read by `otp.tsx` and
  every other screen using the shared error-key system). Verified via
  `rider-app/__tests__/errorI18nCoverage.test.ts`, which pins a fixed list of 27 keys plus a
  "≥28 total keys" floor — my two additions only raise the count (now 31), the fixed-list test
  cases are untouched and still pass.
- `rider-app/app/verify-email.tsx` — brand new file, zero existing importers.
- **Nothing gates on `email_verified`.** Per N14(b) (unchanged by this PR — see ACTION_ITEMS.md
  edit below), no booking, signup, payout, or other flow reads the flag. This screen and the
  Account-screen badge are the only two places in rider-app that reference it at all.
- **No home-screen banner, no forced/blocking flow** — confirmed against the task's explicit
  scope boundary; the only entry point is the Account tab's Personal Info card, one tap away,
  never auto-surfaced.

## 5. User-experience effect

Rider-facing only (no driver/corporate-admin/internal-admin surface touched). Riders now see,
on the Account tab, a small "Verify" pill next to their email if it's unverified, or a
"Verified" pill if it is. Tapping "Verify" opens a new screen that sends and confirms a 4–6
digit code, exactly like the existing phone-verification flow. This is **not** visible
mid-session to someone already using the app in any disruptive way — it's a static row on a
screen the rider has to navigate to (Account tab), not a toast/banner/modal injected into an
active ride or any other in-progress flow. No existing screen's default behavior changed for a
rider who never opens Account → the row was already there for Phone; Email now optionally shows
a pill too.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app/verify-email.tsx` | New file — the verify-email screen (request + confirm flow, error handling, store update on success) | N14(a) entry point |
| `rider-app/app/(tabs)/account.tsx` | Added a Verify/Verified pill to the existing Email row; fixed the focus-effect's `/auth/me` refresh to merge instead of replace `state.user` | Discoverable entry point + prevents the merged flag from being wiped on tab refocus |
| `rider-app/app/_layout.tsx` | Registered `<Stack.Screen name="verify-email" />` | Required for `router.push('/verify-email')` to resolve |
| `rider-app/i18n/en.json` | Added `errors.profile.email_missing` and `errors.profile.email_verification_stale` | Plugs `PROFILE_EMAIL_MISSING` / `PROFILE_EMAIL_VERIFICATION_STALE` message_keys into the existing i18n error-copy system |
| `rider-app/__tests__/verifyEmailScreen.test.tsx` | New file — 10 tests | Request/confirm flow, all backend error cases, success state merge |
| `rider-app/__tests__/accountEmailVerification.test.tsx` | New file — 5 tests | Badge states, navigation, and the focus-refresh merge fix |

## 7. Before / after

The one behavior-changing (non-purely-additive) diff is the focus-effect merge in `account.tsx`:

```tsx
// Before
const userRes = await api.get<User>('/auth/me');
if (!cancelled && userRes.data) useAuthStore.setState({ user: userRes.data });
```

```tsx
// After
const userRes = await api.get<User>('/auth/me');
if (!cancelled && userRes.data) {
  useAuthStore.setState((state) => ({
    user: state.user ? { ...state.user, ...userRes.data } : userRes.data,
  }));
}
```

Every field the real response contains still overwrites identically; only a field the response
never sends at all is now preserved from the previous state rather than dropped.

## 8. Rollback plan

Purely additive feature, no migration, no data mutation, no flag needed:
- `git revert` this PR's commit(s). `verify-email.tsx` is a standalone new route; removing the
  `<Stack.Screen>` registration and the Account-screen pill fully removes the feature's surface
  area with no partial state to clean up (the backend's `rider_email_verification_otp` table and
  `users.email_verified`/`email_verified_at` columns are unaffected either way — they were
  already live from the prior backend-only PR).
- No Stripe charges, wallet deltas, or ride state involved — a plain code revert is a complete
  and sufficient rollback here, unlike money/ride-state changes elsewhere in this codebase.

## 9. Verification performed

- [x] Automated tests run — real `yarn test --ci --coverage --forceExit --reporters=default`
  (not just the new files): **455 passed, 0 failed, 54/54 suites** (one test,
  `bookingProposalCardPromo.test.tsx`, flaked with a timeout under full-suite parallel load on
  one run and passed standalone and on a full-suite re-run — confirmed via `git status` that
  this test file was not touched by this PR, and it is unrelated to auth/account/email).
  New/changed test files specifically: `verifyEmailScreen.test.tsx` (10/10 passed),
  `accountEmailVerification.test.tsx` (5/5 passed).
- [x] `yarn tsc --noEmit` — clean, no errors, matching CI's `rider-app-test` job exactly
  (`.github/workflows/ci.yml`).
- [x] **Real production build run**: `yarn build:web` (`expo export --platform web`) completed
  successfully (exit 0, `Web Bundled ... (2501 modules)`, bundles + assets written to `dist/`).
  This is a genuine production bundle, not just `tsc --noEmit` or the dev server, per root
  `CLAUDE.md`'s explicit requirement.
- [x] Blast-radius grep performed — see §4: `account.tsx` confirmed to have no other importers;
  `_layout.tsx`'s Stack.Screen list confirmed additive-only; `en.json`'s `errors.profile.*`
  namespace confirmed new (not shared with existing `errors.auth`/`errors.system` consumers);
  confirmed via reading `backend/schemas.py` that `email_verified` is genuinely absent from
  `UserProfile`, not just untyped client-side.
- [x] Reviewed against relevant CLAUDE.md conventions: PIPEDA (no PII logged — the screen never
  logs the email address itself, only shows it in UI, matching how `account.tsx` already
  displays it); "do not silently swallow errors" (every backend error case surfaces a specific
  toast, none are caught-and-ignored); scope discipline (no backend Python files touched, no
  other ACTION_ITEMS.md N-item touched).
- [x] Feature-flag: not applicable / not used — per CLAUDE.md's gate #3, a flag is for
  "user-visible and non-trivial" changes on a **shared component used by 3+ pages**. This is a
  single new, entirely optional, non-blocking entry point on one screen (Account), not gating or
  altering any existing shared flow — judged not to warrant a flag, consistent with how N9/N10's
  similarly-scoped additive UI changes were shipped unflagged.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-layer follow-up
  needed)
- [x] Blast radius is stated, not assumed (§4, with the one behavior-changing diff called out
  explicitly and justified)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5;
  the one behavior change — the focus-effect merge — is UI-observable only in that it *prevents*
  a would-be regression, not a new visible change to existing users)

## What was NOT verified

- **No real device/simulator visual check.** The screen was verified via `react-test-renderer`
  (structural/behavioral assertions only, no pixel/layout rendering) and a successful web
  production bundle. No screenshot or manual run on iOS/Android/Expo Go was performed in this
  session.
- **No real backend integration.** All API responses in tests are mocked to match the shapes
  documented in `backend/routes/users.py`'s N14 block (read directly, not guessed) — this was
  never exercised against a running backend or a real Supabase-backed OTP flow.
- **No automated visual/snapshot regression tooling exists for this repo's rider-app surface**
  (per CLAUDE.md's standing gap, `ACTION_ITEMS.md` N12/its RN-app analogue) — the new pill
  layout on the Account screen's email row was reasoned about (flex row, existing `cardInfo`
  `flex: 1` pushes the pill right) and covered by structural tests, but not screenshotted.
- **The `email_verified` staleness gap described in §3 is a real, acknowledged residual limit**,
  not something this PR resolves — a rider who verifies and then fully restarts the app will see
  the badge regress to "not verified" until a follow-up backend change adds `email_verified` to
  `UserProfile`. Flagged in the ACTION_ITEMS.md N14 update, not silently absorbed.
- **fr/es/zh translations for the two new `errors.profile.*` keys were not added** — matches
  this repo's existing, pre-existing incompleteness for the entire `errors.*` i18n tree (verified
  by reading `fr.json`/`es.json`/`zh.json`: their `errors` objects are already empty for every
  existing key, e.g. `errors.auth.otp_invalid`, not just mine); non-English users already fall
  back to the English copy or the raw key for every error in this system today, a pre-existing
  gap this PR doesn't widen or narrow.
