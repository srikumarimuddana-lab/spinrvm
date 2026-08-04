# Change Impact & Risk Log — B16 driver discreet SOS (+ a false-confirmation bug on the driver SOS path)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-04 |
| Author | Claude Code (spinr agent) |
| Surface(s) | driver-app, shared |
| Domain (Sentry tag) | safety |
| PR / commit link | (this commit) |
| Related issue or gap ID | `ACTION_ITEMS.md` B16 |

## 1. Issue / gap identified

**Two things, one of them not in the original finding.**

**(a) B16 as filed.** Design sketch `011-driver-sos` asked *"can a driver call for help with one
hand while driving?"* and explicitly rejected the loud variant: *"a full-screen red alarm is
visible to any passenger in the back seat — the exact scenario where a driver most needs
help."* What shipped was the shared `SOSButton` for both apps: bright red `#DC2626` with a red
glow, a 1.2 s hold, a six-pulse `Vibration.vibrate([0,200,100,200,100,200])` that is audible in
a quiet car, and on success a native `Alert.alert('🚨 Emergency Alert Sent', …)`. That is
structurally the sketch's own **rejected** variant, on the surface the design process rejected
it for.

**(b) Found while wiring (a): the driver SOS reported success when it had failed.** The driver
call site wrapped `onTrigger` in its own `try/catch`:

```tsx
onTrigger={async (rideId, lat, lng) => {
  try { await api.post(`/rides/${rideId}/emergency`, {...}); } catch (err) { console.error('[index]', err); }
}}
```

`SOSButton` decides between "Alert Sent" and its persistent "Not Sent — call 911" state purely
on whether that promise rejects. Swallowing the error meant `backendOk` was **always** true, so
a driver whose SOS never reached the backend saw *"🚨 Emergency Alert Sent — Your location has
been shared with Spinr support and your emergency contacts."*

This is the exact false confirmation the component was designed to prevent — its own docstring
says *"Only shows success + 911 prompt AFTER backend confirms (fail-safe)"* — defeated at the
call site. **The rider path does this correctly**: `rideStore.triggerEmergency` ends with
`throw lastError` and a comment saying *"rethrow so SOSButton can set backendOk=false"*. Only
the driver site was wrong.

Severity note, calibrated: this is not "SOS silently does nothing" — the backend request is
still attempted 3× by the component. It is "when all three attempts fail, the driver is told
help is coming and it is not." The window is a backend/network outage lasting past ~4 s.

## 2. Root cause

**(a)** One shared component for two surfaces whose design docs deliberately diverged, with no
mechanism to express the divergence. Sketch 010 (rider) and 011 (driver) picked different
winners; the implementation collapsed both into 010's shape and nothing flagged the drift.

**(b)** A `try/catch` added at the call site for the ordinary reason (don't let an unhandled
rejection escape a JSX callback) by someone who did not know the component's contract depends
on that rejection. The contract was documented in the component and in the *rider* store, but
nothing enforced it — there were **no component tests at all** for `SOSButton`, only for the API
and store layers beneath it.

## 3. Fix / remediation

- **`discreet` prop on `SOSButton`, defaulting `false`.** Rider call sites pass nothing and are
  unchanged. Only the driver home screen opts in, and only when the flag is on.
- **Driver call site no longer swallows the error** — the promise propagates, so a failed SOS
  now shows the "Not Sent" state as designed.
- **Driver screen reads `driver_sos_discreet_enabled`** from the public `/settings` projection,
  once on mount.
- **Ten component tests added**, covering both the new behaviour and the pre-existing fail-safe
  properties that had never been pinned.

What discreet mode changes, per sketch 011: 3 s hold, thin fill bar instead of a pulse, muted
slate instead of red-with-glow, a single 40 ms haptic instead of the six-pulse buzz, no on-screen
hold hint, and a small dark toast instead of the native `Alert`.

**Not built:** the tap-opens-Safety-overlay half of sketch 011 (911 button, Share Live Trip Link,
per-contact "✓ Notified" list, discreet-mode toggle UI). The notified list needs a backend change
first — `trigger_emergency` returns `contacts_notified` as a **count**, not a list. Tracked as its
own `ACTION_ITEMS.md` entry rather than half-built here.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface — `shared/components/SOSButton.tsx` is imported by five screens.**
Enumerated by grep, not assumed:

| Screen | Passes `discreet`? |
|---|---|
| `driver-app/app/driver/(tabs)/index.tsx` | yes (flag-gated) |
| `rider-app/app/ride-in-progress.tsx` (×2 call sites) | no |
| `rider-app/app/driver-arriving.tsx` | no |
| `rider-app/app/driver-arrived.tsx` | no |
| `rider-app/app/(tabs)/index.tsx` | no |

`discreet` defaults `false` and every rider site omits it, so **rider behaviour is unchanged by
construction, not by inspection**. Two of the ten new tests are explicit rider regression pins
(success `Alert` and failure `Alert` both still fire on the default path) so a future edit that
leaks discretion into the rider surface fails CI.

**The `Animated` value split matters.** The fill bar uses a `width` interpolation, which cannot
use the native driver; the existing pulse is a `transform`, which does. They are kept as
**separate** `Animated.Value`s specifically so the discreet path does not force the rider's pulse
off the native driver. Sharing one value would have been a silent performance regression for
riders.

**Backend:** unchanged by this commit. No endpoint, payload, or `safety_incidents` field moved.
The driver app now calls `GET /settings` on mount, which it did not before — a public,
unauthenticated, 60 s-cached endpoint already served to the rider app. Negligible additional
load.

**Ride state machine / money:** untouched.

**Fix (b) changes what a driver sees during a backend outage.** Previously: a green "Alert Sent"
confirmation. Now: the amber "Not Sent — call 911 directly" state with retry. This is strictly
more truthful, but it **is** a visible behaviour change on a live-tested surface, and it is
deliberately **not** flag-gated — see §5.

## 5. User-experience effect

- **Driver, flag off:** one change only — a failed SOS now correctly reports failure instead of
  falsely reporting success. Everything else is identical to today.
- **Driver, flag on:** the SOS button is muted rather than red, needs a 3 s hold rather than
  1.2 s, buzzes once rather than six times, and confirms with a small dark toast rather than a
  full-screen dialog. **This is visible mid-session** to a driver who is online — the flag is
  read on mount, so a driver already on the home screen keeps their current mode until the screen
  remounts, and no one's UI changes underneath them mid-ride.
- **Rider:** no change, on any screen, in any state.
- **Internal admin:** the toggle in Settings → Safety alerts (shipped in the B15b change).

**On not flag-gating fix (b):** the flag gates *discretion*, not *correctness*. Leaving the
false-confirmation bug behind a flag would mean deliberately shipping a state where a driver in
an emergency is told help is coming when it is not. That is not a rollout risk worth managing —
it is a defect, and the corrected behaviour (amber "Not Sent", retry, 911 one tap away) is the
behaviour riders already get today.

**Copy:** two new strings, both driver-facing and deliberately terse so they are readable at a
glance and unremarkable to a passenger: `"Silent alert sent · tap to call 911"` and
`"Not sent · tap to call 911"`.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/components/SOSButton.tsx` | `discreet` prop: 3 s hold, fill bar, muted styling, single haptic, toast in place of `Alert`; toast dials 911; failure toast persists | Sketch 011's chosen design, without losing the 911 affordance the `Alert` carried |
| `driver-app/app/driver/(tabs)/index.tsx` | stopped swallowing the `onTrigger` error; reads + passes the flag | (b) the false-confirmation bug; (a) opt in to discreet mode |
| `driver-app/__tests__/components/SOSButton.test.tsx` | **new** — 10 tests | Component had zero test coverage; two are rider regression pins |
| `.claude/context/domain-safety.md` | discreet-mode section; corrected 4 stale claims | Doc described a `/safety/sos` endpoint, a wrong Key-files entry, "in parallel" fan-out, and a `sos_acknowledged` event that exists nowhere |

## 7. Before / after

The bug (b), which is the part with real consequences:

```tsx
// Before — backendOk is always true. A failed SOS shows "Alert Sent".
onTrigger={async (rideId, lat, lng) => {
  try { await api.post(`/rides/${rideId}/emergency`, { latitude: lat, longitude: lng }); }
  catch (err) { console.error('[index]', err); }
}}
```

```tsx
// After — the rejection reaches SOSButton, which shows "Not Sent — call 911".
onTrigger={async (rideId, lat, lng) => {
  await api.post(`/rides/${rideId}/emergency`, { latitude: lat, longitude: lng });
}}
```

Discreet confirmation (a):

```tsx
// Before — one path for both apps; a passenger can read this over the driver's shoulder.
    } else if (backendOk) {
      setTriggered(true);
      showSuccessAlert();          // native Alert: '🚨 Emergency Alert Sent'
```

```tsx
// After — rider path byte-identical; driver path silent, 911 still one tap away.
    } else if (backendOk) {
      setTriggered(true);
      if (discreet) {
        showToast('sent', 'Silent alert sent · tap to call 911');
      } else {
        showSuccessAlert();
      }
```

## 8. Rollback plan

- **Discreet UX:** set `driver_sos_discreet_enabled = false` in `app_settings`. Effective within
  the 60 s settings TTL, no redeploy, no app update. Drivers fall back to the current shared
  button. This is the intended rollback and it is a single boolean.
- **Fix (b):** intentionally **not** behind the flag, so rolling back the flag does not restore
  it. Reverting it requires a `git revert` + mobile release — which is correct, because
  "restore the false confirmation" should not be a one-click operation.
- **No data-level remediation needed.** Nothing here writes to `safety_incidents`, wallets,
  Stripe, or ride state. The backend contract is unchanged, so a rolled-back client and a
  current backend interoperate fine in both directions.

## 9. Verification performed

- [x] **Automated tests.** New `SOSButton.test.tsx` — 10 passed. Full driver-app suite —
      **46 suites / 353 tests passed**. Rider `rideStore.sos.test.ts` — 3 passed.
- [x] **Typecheck both apps** — `tsc --noEmit` clean for driver-app and rider-app.
- [x] **Blast-radius grep** — `SOSButton` across `driver-app/`, `rider-app/`, `shared/`; all five
      call sites enumerated in §4 and each checked for whether it passes `discreet`.
- [x] **Rider non-regression pinned in code**, not just reasoned: two tests assert the default
      path still fires the native success and failure `Alert`s.
- [x] **Reviewed against `CLAUDE.md`** — feature-flagged shared component used by 3+ pages;
      additive prop over mutated behaviour; rollback stated before merge.
- [x] **A test was verified to fail for the right reason.** The failure-toast test initially
      failed because the toast auto-dismissed after 8 s — which turned out to be a **real gap**,
      not a test bug: in discreet mode the toast is the only 911 affordance, so auto-clearing it
      left a driver whose SOS failed with no route to 911. Failure toasts were made persistent
      and a test now pins that (and its complement, that success toasts still auto-clear).

## 9a. What was NOT verified

- **No production mobile build was run for driver-app.** `CLAUDE.md` asks for
  `npm run build`-equivalent on app changes; driver-app builds via EAS, which requires the
  `[build]` commit-message trigger and remote build infrastructure not available in this session.
  What *was* run: full Jest suite and a clean `tsc --noEmit`. **This is weaker than a production
  build and should not be treated as equivalent** — an EAS build must be produced and smoke-tested
  before this reaches drivers.
- **Never run on a real device or simulator.** Every assertion about the discreet UX — that the
  muted slate reads as unremarkable next to Call/Nav, that the fill bar is visible enough to
  confirm the hold registered, that a 40 ms haptic is perceptible through a phone mount, that
  the toast is legible at a glance while driving — is **reasoned from the sketch and verified
  only in a jsdom test renderer**. Colour, contrast, timing feel, and haptic strength cannot be
  validated this way. A real-device pass with the flag on is required before enabling it for any
  driver.
- **No visual/snapshot regression tooling exists** for these surfaces
  (`visual-regression-test` is `continue-on-error` with no committed baselines), so the styling
  changes were not diffed against a baseline. Standing gap, not specific to this change.
- **The false-confirmation bug (b) was not reproduced against a live backend.** It is proven by
  reading the contract (`backendOk` is set only in the `try`, the `catch` is empty) and by the
  new tests exercising the component with a rejecting `onTrigger`. No one induced a real backend
  outage mid-SOS to watch the old code show "Alert Sent".
- **The flag's mount-time read was not tested against a real `/settings` response** — the driver
  screen's fetch is straightforward and follows the existing `/company-info` precedent, but there
  is no test for it, and a screen-level test would need the whole dashboard harness.
- **Accessibility not audited beyond labels/hints.** `accessibilityLabel`, `accessibilityHint`,
  and `accessibilityRole` are set on both the button and the toast, but no screen-reader pass was
  done, and WCAG contrast on the muted slate `#374151` / `#E5E7EB` pairing was not measured.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — one boolean for the UX; the correctness fix is
      deliberately not rollback-able by flag, and §8 says why.
- [x] Blast radius is stated, not assumed — all five call sites enumerated, rider
      non-regression pinned by tests rather than by inspection.
- [x] No silent behaviour change: §5 covers both the flag-on discreet UX and the un-flagged
      change to what a driver sees when their SOS fails.
