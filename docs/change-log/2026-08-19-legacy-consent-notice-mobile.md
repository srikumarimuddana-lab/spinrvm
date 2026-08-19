# Change Impact & Risk Log — Legacy/re-consent notice mechanism (mobile UI)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Session user (vikas@ngitservices.com), implemented by Claude Code |
| Surface(s) | rider-app, driver-app |
| Domain (Sentry tag) | auth |
| PR / commit link | (this branch) |
| Related issue or gap ID | `docs/change-log/2026-08-19-legacy-consent-notice.md` (backend), `ACTION_ITEMS.md` A41 |

## 1. Issue / gap identified

The backend mechanism (`GET/POST /consent/*`) exists but nothing in either app calls it — the
notice can't be shown to anyone yet, even once the flag is enabled.

## 2. Root cause

New capability, no prior client-side integration.

## 3. Fix / remediation

Added a new screen to each app (`app/legacy-consent-notice.tsx`, rider-app and driver-app),
mirroring the existing `reactivate-account.tsx` screen's structure/style exactly (same
icon-circle/title/body/primary-button/secondary-button layout, theme-aware via `useTheme()`).
Wired into the **one narrow, already-existing post-login redirect check** in each app's
`otp.tsx` — the same `useEffect` that already decides `/(tabs)` (rider) or `/driver` (driver-app)
vs. `/profile-setup` based on `profileComplete`. Extended only the `profileComplete === true`
branch to first call `GET /consent/status` and route to the new screen if `needs_notice: true`.

**Deliberately scoped down from the original plan** (see this session's conversation — user chose
"safe subset now" over full wiring): this covers the **fresh-login path only**. The **already-
logged-in / cold-start session-restore path** (someone who reopens the app without going through
OTP again) is NOT covered — that logic lives inside `shared/store/authStore.ts` (used by both
apps) and each app's much larger, more fragile `_layout.tsx`, which this session deliberately did
not touch blind, with no simulator/device available to verify a navigation change against either
live app. **This is a known, stated gap, not a silent one** — tracked below.

The new screen itself re-checks `/consent/status` on mount and self-heals (redirects straight to
the app) if reached with `needs_notice: false` — so even a future integration point that routes
here incorrectly fails safe.

## 4. Risk & impact on existing functionality

- **Blast radius of the new screens**: isolated — new files, no existing screen touched.
- **Blast radius of the `otp.tsx` change**: the touched `useEffect`'s existing two branches
  (`profileComplete` → tabs/driver home, else → profile-setup) are unchanged in the `else` branch;
  the `profileComplete` branch now makes one additional network call before its two existing
  possible outcomes, with the entire call wrapped fail-open (both success and error paths
  ultimately reach the same destination the old code would have, unless the notice is genuinely
  needed). No behavior change for any account with a current `consent_version` — the added
  `GET /consent/status` call reports `needs_notice: false` for them (or the flag is off, which
  reports `false` unconditionally regardless of account state).
- **A brand-new signup is structurally exempt**: `verify_otp`'s new-user branch already stamps a
  current `consent_version` at creation (today's earlier fix), so a first-time user reaching
  `profileComplete === true` right after signup already has `needs_notice: false` — this change
  only ever fires for a pre-existing account.
- **Known, stated scope gap**: an existing rider/driver whose `profile_complete` is `false`
  (reaches `/profile-setup` instead) is not checked by this integration — `profile-setup.tsx` was
  not touched. In practice this affects only pre-existing accounts with an incomplete profile
  (some legacy-imported riders may fall here, per the migration audit's ~65%-incomplete driver
  data finding — riders are less affected, most imported riders carry a name/phone at minimum).
  These users will not see the notice until a future session extends `profile-setup.tsx`'s own
  completion redirect the same way, or the cold-start path is built.
- **Not covered — cold start**: stated above and in ACTION_ITEMS. Nothing regresses because of
  this gap (the flag stays off), but it means the notice, once enabled, would only actually reach
  users at their next fresh login (OTP re-verification), not their next app open.
- No migration, no money, no ride state machine, no WebSocket event, no background loop.

## 5. User-experience effect

None yet — `legacy_consent_notice_enabled` is off, and the backend is deployed but not the mobile
build. Once both ship and the flag is on: a returning rider/driver whose account predates consent
tracking sees a one-time full-screen notice after their next fresh login, not mid-session, with a
link to the full policy and a single "I understand, continue" action.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app/legacy-consent-notice.tsx` | New screen | Notice UI |
| `rider-app/app/otp.tsx` | `profileComplete` branch now checks `/consent/status` before routing | Integration point |
| `driver-app/app/legacy-consent-notice.tsx` | New screen (mirrored) | Notice UI |
| `driver-app/app/otp.tsx` | Same integration, mirrored | Integration point |

## 7. Before / after

```tsx
// Before (both apps' otp.tsx, profileComplete branch)
if (profileComplete) {
  router.replace('/(tabs)' as any); // or '/driver' in driver-app
}

// After
if (profileComplete) {
  api.get('/consent/status').then(
    (res) => {
      if ((res.data as any)?.needs_notice) {
        router.replace('/legacy-consent-notice' as any);
      } else {
        router.replace('/(tabs)' as any); // or '/driver'
      }
    },
    () => router.replace('/(tabs)' as any), // fail open
  );
}
```

## 8. Rollback plan

- **Code**: `git revert` — the `otp.tsx` change is additive to one branch with a fail-open path;
  reverting restores the exact prior two-line redirect.
- **Live, without a redeploy**: the backend flag (`legacy_consent_notice_enabled = false`) already
  makes `/consent/status` report `needs_notice: false` unconditionally — the mobile code becomes a
  no-op extra network call with the flag off, so disabling it there is sufficient without an app
  store update even if the mobile build already shipped.

## 9. Verification performed

- [x] TypeScript: `tsc --noEmit` clean on both apps (full project, not just touched files) —
  **this is a real production-build-adjacent check, run explicitly, not skipped.**
- [x] ESLint clean on all 4 touched/new files (one `react/no-unescaped-entities` finding on
  `Canada's` → fixed to `Canada&apos;s`, matching this repo's own established convention seen in
  `confirm-pickup.tsx`/`login.tsx`/`loyalty.tsx`/`notifications.tsx`).
- [ ] **No visual verification performed — no simulator or device available this session.** This
  repo has no automated visual/snapshot regression tooling for either app (a standing, already-
  documented gap per CLAUDE.md's own guidance to state this explicitly rather than imply "no
  visible diff" was checked). The screen's actual rendered appearance, spacing, and dark/light
  theme behavior are unverified beyond matching an existing screen's code structure exactly.
- [ ] Manual repro / staging — not performed.
- [x] Blast-radius grep/read performed on the `otp.tsx` integration point in both apps (§4).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (flag flip covers the live case even post-shipment).
- [x] Blast radius is stated, not assumed — including the explicit cold-start scope gap.
- [x] No silent behavior change — flag is off, nothing is live yet, and the scope gap (profile-
  incomplete + cold-start paths) is stated plainly rather than implied covered.

**Not yet done, deliberately out of scope**: cold-start/session-restore integration
(`authStore.ts`/`_layout.tsx`), `profile-setup.tsx`'s own redirect, visual verification on a real
device, and flipping the flag on anywhere.
