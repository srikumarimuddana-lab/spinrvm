# Change Impact & Risk Log — Auto-start navigation on driver assignment

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-22 |
| Author | Claude Code session (requested by @mkkreddy52) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/driver-assignment-google-maps-qd4aba` — commits `769bdaf`, `32c3f93`, `29fb86e` |
| Related issue or gap ID | none — direct product request |

## 1. Issue / gap identified

A driver who accepts a ride has to find and tap **Navigate** on the active-ride
panel before they can drive anywhere. It is a manual step at the one moment the
driver is trying to pull into traffic, repeated on every ride and again at the
dropoff.

Not a bug report — a product request to close the gap.

## 2. Root cause

Nothing is broken. `ActiveRidePanel`'s launcher has only ever been wired to an
`onPress`, so navigation starts only on an explicit tap. There was no preference
or code path for launching it on a state change.

## 3. Fix / remediation

Navigation now hands off to the driver's chosen maps app automatically at the two
transitions where they start driving somewhere new:

- **Accept** (`driver_accepted` → panel `navigating_to_pickup`) → pickup
- **Trip start** (`in_progress` → panel `trip_in_progress`) → dropoff

`arrived_at_pickup` deliberately does **not** fire — the driver is parked and
waiting, and a maps app over the OTP keypad is the opposite of helpful.

Three supporting decisions:

- **Trigger is accept, not assignment.** The literal request said "when the
  driver gets assigned", but in Spinr's state machine `driver_assigned` is the
  *offer* stage: the ~15s window before the driver taps Accept, which they may
  decline or lose to another driver's atomic claim. Launching there would throw
  a driver out of Spinr during an offer that isn't theirs yet, with the
  accept/decline countdown running behind Maps. Confirmed with the requester
  before implementing.
- **Pickup routes through `pickup_nav_lat/lng` first**, matching what the manual
  button already does in four places. That column (migration 133) is the pickup
  snapped to the nearest drivable road; a rider can pin the middle of a mall,
  and the raw pin sends the driver where no car can stop. Dropoff has no snapped
  variant and uses the plain coords.
- **One launch per leg, deduped durably.** See §4.

The launcher itself moved out of `ActiveRidePanel` into
`lib/navigation/launchNavigation.ts` **unchanged**, so the manual button and the
automatic hand-off run one implementation rather than two that drift.

### Alternative considered

Putting the trigger in `useDriverDashboard`'s existing `prevRideStateRef` effect,
which can see the real `ride_offered → navigating_to_pickup` transition and so
needs no dedupe marker at all. Rejected: `acceptRide` sets the ride state and
*then* fetches the ride, so at the moment of that transition `activeRide` is
still null and the pickup coordinates do not exist yet — the hook would have had
to wait for the payload, which reintroduces exactly the "have I already fired?"
bookkeeping it was supposed to avoid, in a file that does not otherwise hold the
nav preference. `ActiveRidePanel` already holds both the ride payload and
`navStore`, and is mounted for precisely the three states involved.

## 4. Risk & impact on existing functionality

**Blast radius: single-surface (driver-app), and narrow within it.** Greps run:

| Searched | Result |
|---|---|
| importers of `store/navStore` | `ActiveRidePanel.tsx`, `app/driver/settings.tsx`, and a type-only import in the new `launchNavigation.ts`. Nothing else. |
| importers/renderers of `ActiveRidePanel` | rendered in exactly one place, `app/driver/(tabs)/index.tsx`, via the `components/dashboard/index.ts` barrel. The pre-commit hook's "~14 other files" count is comment mentions, not imports. |
| `pickup_nav_lat` readers | `ActiveRidePanel.tsx`, `ride-detail.tsx`, `(tabs)/index.tsx` ×2, and backend dispatch/matching. The new effect now matches their fallback order. |
| `AsyncStorage.clear` / `multiRemove` in driver-app | none — nothing wipes the new marker key on logout. Harmless: a stale `rideId:leg` marker only suppresses a re-launch for that one past leg. |

**No backend, no API, no DB, no migration.** Nothing touches the ride state
machine, dispatch, insurance-period rows, or money. The driver-app reads ride
state here; it does not write it.

**The regression this was designed against — the cold-start re-launch loop.**
The feature's own success case is the driver spending the whole drive inside
Google Maps while Android reclaims the backgrounded Spinr process. An in-memory
ref dies with that process, so the cold start when the driver reopens Spinr to
tap **Arrived** would have re-fired the hand-off and bounced them straight back
out to Maps — at the exact moment they need the Spinr screen. Dedupe is therefore
a durable claim marker in AsyncStorage (`@spinr_auto_nav_launched`), not a ref.
One key covers both legs because the state machine has no path from
`in_progress` back to `driver_accepted`, so a dropoff marker can never need to
coexist with a pickup one.

**Two failure modes accepted, both biased toward launching:**

- AsyncStorage unavailable → `claimAutoNavLeg` returns true and launches. One
  extra launch after a cold start beats a feature that silently never fires.
- No maps handler at all → warns and does nothing; the manual Navigate button is
  untouched and still works.

**Risk to the existing manual button:** it now calls the extracted launcher.
Behaviour is byte-identical (same URLs, same `canOpenURL` fallback order) — the
only deliberate change is that the final web-URL fallback now catches its own
rejection instead of leaving an unhandled promise rejection.

**PIPEDA:** the first draft of that catch logged the rejection. React Native puts
the failing URL in the message, and that URL carries the ride's raw pickup or
dropoff coordinates — barred from logs by CLAUDE.md. Caught on self-review and
changed to log only the driver's app preference. (Sentry would not have received
it either way: `shared/services/errorReporting.ts` already drops the whole
console-breadcrumb category for this exact reason. Local logcat is still a log.)
The AsyncStorage marker stores a ride id and a leg name — no GPS, no name, no
contact details.

## 4b. Adversarial review findings (spinr-edge-case-reviewer, post-implementation)

CLAUDE.md gate #10 requires a reviewer agent against the actual diff. It returned
two blockers. Both were verified against the code before acting — an agent report
is a claim, not a finding.

**Blocker 1 — CONFIRMED, fixed in this branch.** The auto-launch had no
foreground gate. `lib/androidAuto/register.ts:550` calls
`useDriverStore.getState().acceptRide(rideId)` straight off the Android Auto head
unit, against the *same singleton store* `ActiveRidePanel` subscribes to, and
`app/driver/(tabs)/index.tsx` renders that panel purely on `rideState` with no
`AppState` gate. So a driver who opened the phone app earlier in the session
(the normal way to go online) and then accepted on the car screen would have had
their pocketed phone try to open Maps mid-drive. This is not hypothetical in this
file: `(tabs)/index.tsx:329-337` documents a live-testing production crash
(Sentry CRIMSON-SMOKE-7445-PV) caused by exactly this class of background store
write, whose fix was to park the side effect until the next `'active'`.

Fixed by spending the leg's claim *without* launching when
`AppState.currentState !== 'active'`. Skip rather than defer, deliberately: an
accept that reached the store while the phone was backgrounded came from the car,
and Android Auto has its own navigation surface — replaying it whenever the
driver next picks up the phone would pop Maps at a random later moment.

**Blocker 2 — CONFIRMED as a real bug, but pre-existing and NOT fixed here.**
`hooks/useDriverDashboard.ts:1632` zeroes `reconnectAttemptRef.current` before
calling `connectWebSocket()` at `:1645`, so when `auth_success` arrives
`wasReconnect` (`:1406`) is false on the ordinary foreground-resume path and the
`fetchActiveRide()` reconciliation at `:1418-1420` is skipped. A ride cancelled
while the driver is away leaves a stale, actionable-looking active-ride panel,
and `arriveAtPickup`/`startRide` (`driverStore.ts:691-697`, `:725-729`) only set
`error` on a 409 rather than reconciling like `cancelRide`/`completeRide` do.

None of that is this diff's code. What this diff does is change how often it is
reached: default-ON auto-launch takes "driver leaves the app for several minutes"
from an occasional manual choice to the norm on every ride. Escalated to the
requester rather than silently widened into this PR — it is WS/dispatch
reliability work on a live-tested surface with its own blast radius, and gate #9
says escalate rather than ship on a guess.

**Filed as #5696** (requester's call: keep this PR surgical). That issue carries
the exact line references, the repro, both candidate fixes, and the note that no
existing test covers the path. It is worth weighing against this feature's
rollout: the frequency increase lands the moment auto-navigation ships default-ON.

**Warnings acted on:**
- Cancelled-ride race in the claim's async gap → added an unmount guard
  (`navMountedRef`). Burning a cancelled ride's claim is free; the ride is over.
- Toggling auto-navigate ON mid-leg fired an immediate launch, contradicting the
  toggle's own copy ("when you accept a ride and when the trip starts") → the
  opted-out path now also spends the leg's claim, so the setting takes effect
  from the next transition.

**Warnings accepted, not fixed:**
- Persistent (not one-shot) AsyncStorage write failure degrades to relaunch on
  every cold start, not one extra. Bounded by the in-process guard within a
  session; a device that cannot write a 20-byte key has larger problems.
- Auto-navigate never fires on a car-only launch where the phone UI never mounts.
  Correct — Android Auto has its own nav surface — and now stated rather than
  implicit.
- Nav preferences and the claim marker are device-global, not user-scoped, and
  nothing clears them on logout, so a second driver on a shared device inherits
  the first's nav-app choice. Pre-existing for `NAV_APP_KEY`; not introduced here.

**Methodology gap worth keeping:** §4's blast-radius table greps importers of
`ActiveRidePanel` and `navStore`. That could never have surfaced blocker 1,
because Android Auto reaches this code through a *shared Zustand store write*
from a subsystem that imports neither. A component-import grep cannot see a
store-mediated interaction — for a shared-store change, grep the store's writers
too, not just the component's importers.

## 4c. Second review pass (/code-review, high effort)

A Codex-style line-anchored pass over the whole branch. Eleven findings, all
verified against the code before acting. Eight were real and are fixed here.

| # | Finding | Status |
|---|---|---|
| 1 | `deepLinkFor` emitted iOS-only schemes on both platforms — on Android a Waze-preferring driver was sent to Google Maps on every leg, and "Default" opened a route *preview* rather than turn-by-turn | **Fixed.** Per-platform URLs mirroring `carRoute.ts`'s `buildHandoffUrl`; `canOpenURL` consulted on iOS only |
| 2 | The foreground gate also caught the notification-Accept path, permanently killing the hand-off for that ride | **Fixed.** Defers instead of spending the claim unless `isCarSessionActive()` |
| 3 | `loadNavApp` lost its outer try/catch — a *synchronous* AsyncStorage throw left `isLoaded` false forever, disabling the feature for the session | **Fixed.** Whole body guarded again |
| 4 | `Number.isFinite(0)` is true, so the "bail rather than navigate to (0,0)" comment described a check that didn't exist | **Fixed.** `isPlausibleCoord` with an explicit Null Island and range check |
| 5 | The snapped-pickup fallback resolved per-axis, so a half-written pair produced a hybrid coordinate | **Fixed.** `hasSnappedPickup` resolves the pair atomically, matching `ride_flow.py:901` |
| 6 | `loadNavApp` re-read on every panel mount and could revert a just-made opt-out | **Fixed.** Hydrates once per session |
| 7 | The new tests set `AppState.currentState` by assignment; every sibling test mocks the module because assignment is unreliable | **Fixed.** Mocks the module |
| 8 | On Android `defaultUrl` and `googleWebUrl` were the same string, so the chain retried a URL that had just failed | **Fixed.** Candidates deduped |
| 9 | `openMapsNavigation` discarded `_label`; call site still cast `(ride as any)` for fields this diff had just typed | **Partly fixed.** Casts removed; the thin wrapper kept, as both call sites read better with it |
| 10 | Architectural: "spend the claim without launching" conflates *skip now* with *never again* | **Partly addressed.** Finding 2's fix separates them for the background case. The level-triggered claim stays, because `ActiveRidePanel` mounts already inside `navigating_to_pickup` and cannot see the accept transition — an edge-trigger would need the trigger to move up into `useDriverDashboard`, where the coordinates do not exist yet |
| 11 | §7's before/after snippet showed a superseded revision of the effect | **Fixed.** §7 rewritten against the shipped code |

Findings 1 and 2 are the ones that would have shipped a broken feature: 1 meant
auto-start navigation did not actually start navigation for most Android drivers
and silently overrode a Waze preference twice a ride; 2 meant the fix for the
Android Auto blocker had itself disabled the hand-off for every notification
accept.

Worth noting that finding 1 was pre-existing in the manual button — the
extraction carried it forward rather than introducing it. It only became urgent
because a tap the driver chose became a launch that happens on its own.

## 5. User-experience effect

**Driver-facing, and visible mid-session.** Per the product decision the toggle
ships **ON**, so this is a deliberate behaviour change for every existing driver
on their next app launch, not a dark rollout. A driver online today will find
that accepting their next ride sends them into Maps without asking.

A driver already mid-ride when they update is *not* interrupted: the effect only
fires on the accept and trip-start transitions, and a cold start into an
in-flight leg is suppressed by the marker.

Rider-facing: none. Corporate/admin: none.

New copy — Settings → Navigation → "Auto-Start Navigation" / "Open your maps app
when you accept a ride and when the trip starts". Plain, specific, names both
moments; translated to es/fr alongside en.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/store/navStore.ts` | Added `autoNavigate` (default ON) + `setAutoNavigate`; `loadNavApp` now hydrates both keys | The preference that gates the hand-off |
| `driver-app/lib/navigation/launchNavigation.ts` | **New.** The launcher, lifted out of `ActiveRidePanel` unchanged | One implementation behind both the manual button and the auto hand-off |
| `driver-app/lib/navigation/autoNavigate.ts` | **New.** `claimAutoNavLeg` — durable once-per-leg claim | Stops a cold start re-launching a leg already navigated |
| `driver-app/components/dashboard/ActiveRidePanel.tsx` | Inline launcher replaced with the shared call; added the auto-launch effect; typed `pickup_nav_lat/lng`; dropped the now-orphaned `Platform` import | The change itself |
| `driver-app/lib/androidAuto/carSession.ts` | Exported `isCarSessionActive()` | Lets the phone tell a head-unit accept from a backgrounded notification accept |
| `driver-app/app/driver/settings.tsx` | "Auto-Start Navigation" toggle in the Navigation card | Driver-facing off switch |
| `driver-app/i18n/{en,es,fr}.json` | `settings.autoNavigate`, `settings.autoNavigateDesc` | Copy for the toggle |
| `driver-app/__tests__/store/navStore.test.ts` | **New.** 9 cases | Default-ON, opt-out, corrupt-value, partial-storage-failure |
| `driver-app/__tests__/lib/autoNavigate.test.ts` | **New.** 8 cases | Claim semantics incl. the cold-start and concurrent cases |
| `driver-app/__tests__/lib/launchNavigation.test.ts` | **New.** 7 cases | Pins extracted behaviour against drift |
| `driver-app/__tests__/components/ActiveRidePanel.test.tsx` | Store mock filled in; 10 auto-launch cases appended | Both legs, both gates, both suppression paths |
| `driver-app/__tests__/app/driverSettingsScreen.test.tsx` | Store mock filled in; 3 toggle cases | Toggle reads and writes |
| `driver-app/__tests__/screens/settingsWavToggle.test.tsx` | Store mock filled in | Mock had gone out of sync with the store |

## 7. Before / after

```tsx
// Before — ActiveRidePanel: launching was reachable only by tapping, and the
// launcher emitted the iOS scheme on both platforms.
<TouchableOpacity
  onPress={() => openMapsNavigation((ride as any).pickup_nav_lat ?? ride.pickup_lat, …)}
...
if (navApp === 'google') {
  await openWithFallback(`comgooglemaps://?daddr=${lat},${lng}&directionsmode=driving`);
}
```

```tsx
// After — the same launcher also runs on the two transitions, once per leg.
const pickupLat = hasSnappedPickup(ride) ? ride!.pickup_nav_lat : ride?.pickup_lat;
const navLeg = rideState === 'trip_in_progress' ? 'dropoff'
             : rideState === 'navigating_to_pickup' ? 'pickup' : null;
useEffect(() => {
  if (!navPrefsLoaded) return;
  if (!navLeg || !rideId) return;
  if (!isPlausibleCoord(navDestLat, navDestLng)) return;   // rejects (0,0) too

  // Opted out: spend the claim so turning the toggle on mid-ride doesn't
  // retroactively launch. It takes effect from the next transition.
  if (!autoNavigate) { claimAutoNavLeg(rideId, navLeg); return; }

  // Backgrounded: the car owns it (spend the claim) or a notification accept
  // is routing the driver into the app (defer — re-runs on appActiveTick).
  if (AppState.currentState !== 'active') {
    if (isCarSessionActive()) claimAutoNavLeg(rideId, navLeg);
    return;
  }

  claimAutoNavLeg(rideId, navLeg).then((claimed) => {
    if (claimed && navMountedRef.current) launchNavigation(navApp, navDestLat, navDestLng);
  });
}, [navPrefsLoaded, autoNavigate, navLeg, rideId, navDestLat, navDestLng, navApp, appActiveTick]);
```

```ts
// After — launchNavigation.ts, per-platform URLs (was iOS-only on both):
//   google  → iOS comgooglemaps://   | Android google.navigation:q=
//   waze    → iOS waze://            | Android https://waze.com/ul?  (universal link)
//   default → iOS Apple Maps         | Android google.navigation:q=
// canOpenURL is consulted on iOS only; on Android package-visibility filtering
// makes it lie, so the intent is opened directly and the rejection picks the
// next candidate.
```

Beyond `navMountedRef`, the effect takes no cleanup/cancel guard on purpose: the
claim is already spent by the time a cleanup could run, so cancelling on a mere
dependency change would burn a still-live leg and it would never navigate at all.

## 8. Rollback plan

**No deploy needed for an individual driver:** Settings → Navigation →
**Auto-Start Navigation** → off. Takes effect immediately and persists.

**No deploy needed for a single stuck driver:** the behaviour is per-device
AsyncStorage; clearing app data resets both the preference and the leg marker.

**Repo-wide, one line, no migration and no data remediation:** flip the store
default in `driver-app/store/navStore.ts` — `autoNavigate: true` → `false` in the
initial state, and invert the load rule so a missing key reads OFF. Nothing has
been written to a server, so there is no live data to unwind; a `git revert` of
the three commits is also safe and complete here, which is *not* generally true
but is true for this change because it touches no DB, no Stripe, and no ride
state.

There is deliberately no `app_settings` kill switch: that table is read by the
backend, and this feature never reaches the backend. Adding a server round-trip
purely for a remote off switch would put a network call in front of a hand-off
that must happen the instant a driver accepts.

## 9. Verification performed

- [x] Blast-radius grep performed — the four greps tabulated in §4
- [x] Reviewed against relevant `CLAUDE.md` conventions — ride state machine
      (trigger is `driver_accepted`, not `driver_assigned`; `in_progress` for the
      dropoff leg; no state is written), PIPEDA (§4, coordinates removed from the
      warn), observability (a recoverable degradation warns rather than erroring)
- [x] Adversarial self-review of the diff before commit — this is what caught the
      `pickup_nav_lat` omission, the cancel-guard that would burn a claim, the
      orphaned `Platform` import, and the coordinate-bearing log line
- [x] Syntax-checked every new `.ts` file with `node --experimental-strip-types --check`
- [ ] **Automated tests NOT run** — see §"What was NOT verified"
- [ ] Manual repro in staging — not possible from this environment
- [x] Feature-flagged — a driver-facing Settings toggle, per the CLAUDE.md gate #3
      pattern. Default-ON was an explicit product decision, so §5 documents the
      mid-session UX change rather than claiming a dark rollout.

## What was NOT verified

Stated plainly, because silence here would imply coverage that does not exist:

- **No test in this change has been executed.** `driver-app/node_modules` is not
  installed in this environment and `registry.npmjs.org` returns 403 through the
  session's egress proxy, so `jest`, `tsc --noEmit`, and ESLint could not run —
  not a passing run, not a failing one. The 37 test cases added across five files
  are **written but unproven**. They must be run before this merges.
- **No production build was run** (`npx expo export` / EAS). CLAUDE.md requires
  saying so explicitly for any driver-app change; a dev server would not have
  been equivalent anyway, and neither was available.
- **Nothing was exercised on a real device or simulator.** Every claim about what
  Google Maps, Waze, or Apple Maps does with these URLs rests on the existing,
  already-shipped launcher being lifted unmodified — not on a fresh observation.
  The one genuinely new runtime behaviour, *launching without a tap*, has never
  been seen run.
- **The cold-start suppression is covered only by a mocked AsyncStorage.** A real
  Android process death, the case the marker exists for, was not reproduced.
- **No visual regression tooling exists for driver-app at all** (CLAUDE.md gate
  #6 — rider-app and driver-app have none; only admin-dashboard has the seeded
  Playwright job). The new Settings toggle was reasoned about, not screenshotted.
- **`__tests__/app/driverDashboardScreen.test.tsx` stubs `ActiveRidePanel`
  entirely**, so it gives zero coverage of this change despite being the test
  that renders the dashboard — per CLAUDE.md gate #1, naming it rather than
  counting it.
- **Accessibility**: the toggle inherits `renderToggle`'s existing
  `accessibilityRole="switch"` + `accessibilityState`, so it is labelled and
  stateful by construction, but no screen-reader pass was run.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — per-driver toggle, or a one-line
      default flip; no live data to unwind
- [x] Blast radius is stated, not assumed — four greps tabulated in §4
- [x] No silent behaviour change — §5 states the default-ON, mid-session effect
      outright
- [ ] **Not ready to merge as-is**: the test suite must actually run first
