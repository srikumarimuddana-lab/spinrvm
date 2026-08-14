# Change Impact & Risk Log

> Covers subtasks 5–11 of ACTION_ITEMS.md B16's approved implementation
> plan (the frontend half). Backend half (subtasks 1–4) already merged as
> PR #3596 — see `docs/change-log/2026-08-11-b16-driver-sos-discreet-shield-backend.md`.
> Subtask 12 (optional bottom-bar entry point) is deferred to a fast-follow,
> not included here — see ACTION_ITEMS.md's B16 entry for why it's
> non-blocking.

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude |
| Surface(s) | driver-app, shared |
| Domain (Sentry tag) | safety |
| PR / commit link | branch `claude/b16-driver-sos-frontend` |
| Related issue or gap ID | `ACTION_ITEMS.md` B16 |

## 1. Issue / gap identified

The shipped driver SOS button (`shared/components/SOSButton.tsx`, shared verbatim with rider-app) is the design sketch's own **rejected** variant — one persistent red button, 1.2s hold, interruptive `Alert.alert()` on success. Product confirmed (2026-08-01) the "Discreet Hold Shield" design is still wanted for the driver surface; this PR implements it.

## 2. Root cause

No driver-specific SOS UI ever existed — both apps always shared one component with no discreet path, no hold-vs-tap duality, and no Safety overlay.

## 3. Fix / remediation

New, additive shared components/hooks (`SafetyShield.tsx`, `SafetyOverlay.tsx`, `useHoldToConfirm`, `useEmergencyContacts`), two new driver-app-local hooks (`useDriverSafetyTrigger`, `useDriverDiscreetSosFlag`), and a flag-gated branch in `driver-app/app/driver/(tabs)/index.tsx`'s SOS render block. `SOSButton.tsx` itself is **not modified** — it remains rider-app's only SOS UI and the driver-app fallback when the flag is off.

Bundled fix: the flag-off legacy `onTrigger` in `index.tsx` previously swallowed its POST error (`try{...}catch(err){console.error}`), so `SOSButton`'s own retry/FAILED state could never activate for a real driver-side backend failure. Fixed to rethrow — the one deliberate exception to "flag off = zero behavior change," called out explicitly rather than hidden in the wiring commit.

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface (driver-app), gated behind a default-off flag.** Rider-app: zero files touched. `SOSButton.tsx`: zero lines changed.
- **Other consumers of the touched files:**
  - `shared/components/SOSButton.tsx` — 4 rider-app call sites, all unaffected (file untouched).
  - `driver-app/app/driver/(tabs)/index.tsx` — the only driver-app SOS call site; this is the one file with a real behavior change (the bug fix, flag-off default path).
  - New shared hooks/components have no other consumers yet (first and only use is this feature).
- **State-machine / money-path impact:** none — this is a pre-existing-endpoint UI change, no new backend writes beyond what the already-merged backend half (subtasks 1–4) added.
- **Dependency footprint:** `SafetyShield.tsx`/`SafetyOverlay.tsx` import `react-native-svg` and `expo-location`, both already driver-app dependencies (svg is *not* a rider-app dependency — both files' headers explicitly warn against importing them from rider-app). No new dependency added; a planned `expo-blur` usage was dropped in favor of a plain semi-transparent `View`, matching this codebase's own existing (if oddly named) `SafeBlurView` precedent in `dashboard/MapControls.tsx`, which is itself just a plain `View` — no file in either app actually imports real `expo-blur` today.
- **Test-infra finding, not a runtime risk:** `shared/**/__tests__` directories are not executed by any CI job (verified via `jest --listTests` from both `driver-app` and `rider-app` — neither picks up files outside its own `rootDir`; the "shared" entry in `security-gates.yml`'s matrix is a `yarn audit` dependency scan, not a test run). All new hook/component tests were placed under `driver-app/__tests__/` instead, which does run in CI (`driver-app-test` job).

## 5. User-experience effect

**Driver-facing**, gated behind `driver_discreet_sos_enabled` (default `False` — no visible change today).

- **Flag off (current production behavior):** identical UI, except a real backend SOS failure now correctly shows the amber FAILED/retry state (previously silently looked like success — this was unreachable before, so it's a pure correctness improvement, not a new failure mode).
- **Flag on (not yet enabled anywhere):** the driver's SOS button becomes a discreet shield — hold 3s sends a silent alert (small badge + brief toast, no interruptive dialog); a short tap opens a full Safety screen (911, alert-contacts, share-trip-link, per-contact notified list, close). Not visible mid-session change unless an admin flips the flag while a driver is actively on a trip, in which case the next render picks up the new component — no crash, no ride-state impact either way.

Not visible to riders, corporate admins, or internal admins in any way.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/hooks/useHoldToConfirm.ts` (new) | Hold-N-ms confirmation gesture | Shared by shield's own hold + overlay's hold button |
| `shared/hooks/useEmergencyContacts.ts` (new) | Wraps `GET /users/emergency-contacts` | Contact names for shield toast + overlay subtitle/list |
| `shared/components/SafetyShield.tsx` (new) | Floating discreet shield | Silent-hold + tap-to-overlay entry point |
| `shared/components/SafetyOverlay.tsx` (new) | Full Safety screen | 911 / alert-contacts / share-link / close |
| `driver-app/hooks/useDriverSafetyTrigger.ts` (new) | Retry-wrapped SOS POST | onTrigger contract for the new components only |
| `driver-app/hooks/useDriverDiscreetSosFlag.ts` (new) | Reads the flag, fails closed | Rollout gate |
| `driver-app/app/driver/(tabs)/index.tsx` | Flag-gated branch + bug fix | The one integration point |
| `driver-app/__tests__/hooks/*.test.ts`, `driver-app/__tests__/components/*.test.tsx`, `driver-app/hooks/__tests__/*.test.ts` (new) | Test coverage for all of the above | — |

## 7. Before / after

```tsx
// Before (driver-app/app/driver/(tabs)/index.tsx)
<SOSButton
  rideId={activeRide.ride.id}
  onTrigger={async (rideId, lat, lng) => {
    try { await api.post(`/rides/${rideId}/emergency`, { latitude: lat, longitude: lng }); } catch (err) { console.error('[index]', err); }
  }}
/>
```
```tsx
// After
{discreetSosEnabled ? (
  <>
    <SafetyShield rideId={activeRide.ride.id} onTrigger={safetyTrigger} onOpenOverlay={() => setSafetyOverlayOpen(true)} />
    <SafetyOverlay visible={safetyOverlayOpen} onClose={() => setSafetyOverlayOpen(false)} rideId={activeRide.ride.id} onTrigger={safetyTrigger} />
  </>
) : (
  <SOSButton
    rideId={activeRide.ride.id}
    onTrigger={async (rideId, lat, lng) => {
      await api.post(`/rides/${rideId}/emergency`, { latitude: lat, longitude: lng }); // rethrows now
    }}
  />
)}
```

## 8. Rollback plan

`git-revert-safe` for the code; **operationally**, since the flag already defaults off and has never been flipped on, no rollback action is needed at all unless an admin explicitly enables it first — in which case flipping `driver_discreet_sos_enabled` back to `False` via `PUT /api/admin/settings` instantly reverts driver-app to the SOSButton path (plus the retry-bug fix, which stays fixed either way).

## 9. Verification performed

- [x] Automated tests run: full driver-app suite via the local Jest binary (`./node_modules/.bin/jest --no-coverage`) — 50/51 suites, 358/358 tests pass. The one failing suite (`ActivityView.test.tsx`, "Cannot find module 'expo-router/react-navigation'") is pre-existing and unrelated — confirmed via `git stash` that it fails identically with every B16 frontend file removed.
- [x] `tsc --noEmit` clean on every touched/new file (pre-existing, unrelated errors remain in 4 other files this branch never touches).
- [ ] Manual repro on a real device/simulator — **not done**. Gesture timing, blur/toast rendering, and the actual flag-on visual experience are not exercised by any test in this pass.
- [x] Blast-radius grep performed: confirmed `SOSButton.tsx` has zero other importers affected by this change (rider-app's 4 call sites untouched); confirmed the new shared files have no dependents besides this feature.
- [x] Reviewed against relevant `CLAUDE.md` conventions: additive/dark-launch via the existing `app_settings` pattern, no PII added to logs, safety-domain rigor (never `Alert.alert` on a silent-alert failure — pinned by tests).
- [x] Feature-flagged: `driver_discreet_sos_enabled`, default off, verified fail-closed on fetch error too.

**What was NOT verified:**
- No manual/device QA of the new UI (gesture feel, visual fidelity to the design sketch, blur/toast rendering) — flagged as a required step before flipping the flag on anywhere.
- No visual/snapshot regression tooling exists anywhere in this repo (standing gap, not specific to this change).
- `driver-app/app/driver/(tabs)/index.tsx`'s SOS block has no automated test coverage before or after this change — the wiring subtask's correctness rests on `tsc` + manual review, not an automated assertion.
- The admin-dashboard toggle UI for the flag was not built — enabling it today requires a direct API call.
- Subtask 12 (bottom-bar Safety entry point) is deferred, not included in this PR.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (flag flip, or plain `git revert` since it was never enabled)
- [x] Blast radius is stated, not assumed (single-surface, gated, enumerated in §4)
- [x] No silent behavior change to an already-shipped flow — the one bug-fix behavior change (flag-off path) is called out explicitly in §3, not buried
