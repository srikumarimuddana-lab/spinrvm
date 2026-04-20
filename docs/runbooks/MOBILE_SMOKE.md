# Mobile Smoke Runbook — Rider + Driver Apps

**Purpose:** The human-execution half of the staging smoke matrix. The
automated half is `scripts/smoke/full_stack_smoke.py` (stack-level
HTTP + WS) and `scripts/smoke/stripe_charge_smoke.py` (payments). This
runbook covers what only a human-with-a-device can verify: the mobile
bundle actually reaches the backend, native integrations (Firebase,
Stripe React Native, Google Maps, push) work, and the UI behaves
correctly through real network conditions.

**When to run:** before declaring a build production-ready. Not
per-commit. One operator walks the whole thing, ticks boxes, signs off.

**Sibling runbook:** `docs/scoping/P0-5_PHASE_E_RUNBOOK.md` covers the
Stripe test-card scenarios. Do that one separately; this runbook
assumes payments work and focuses on the rest of the app.

---

## 1. Prerequisites

### 1.1 Stack

- [ ] Staging backend deployed and green on
      `scripts/smoke/full_stack_smoke.py --backend $STAGING_URL`
- [ ] `EXPO_PUBLIC_BACKEND_URL` baked into the rider + driver builds
      points at staging (not prod!)
- [ ] Firebase project wired — App Check, Phone Auth enabled for both
      apps; Crashlytics connected so this walkthrough populates dashboards
- [ ] Stripe publishable key present in `GET /api/v1/settings`
      (verifies via `full_stack_smoke.py`)
- [ ] Google Maps API key restricted correctly — iOS + Android + Web
      bundle IDs all listed in the key's allowlist

### 1.2 Builds

- [ ] iOS: TestFlight internal build installed on a real iPhone (not
      simulator — simulators skip push / Stripe sheets)
- [ ] Android: Play Internal or APK sideload installed on a real
      Android device
- [ ] Web: `yarn build:web` + served from staging (same domain as the
      apps for Stripe's 3DS to work)

### 1.3 Test accounts

You need **two accounts, two devices**. The smoke covers cross-device
scenarios (rider on phone A, driver on phone B) that can't be faked on
one device.

- [ ] Rider test account: phone auth complete, profile complete, has
      a saved test card (`4242 4242 4242 4242` — see Phase E runbook
      for the manage-cards flow)
- [ ] Driver test account: phone auth complete, profile complete,
      vehicle info entered, documents uploaded + approved (onboarding
      status = `verified`)
- [ ] Both accounts have valid GPS permissions granted on their
      respective devices

### 1.4 Environment

- [ ] Physical test area (small geofence) configured in the admin
      dashboard as a service area
- [ ] Fares configured for at least one vehicle type in that area
- [ ] No surge multiplier active during the smoke (surge changes
      mid-walkthrough confound the fare assertions)

---

## 2. Boot + auth (per app, per platform)

Do this for each (app × platform) cell: rider-iOS, rider-Android,
rider-Web, driver-iOS, driver-Android, driver-Web. **6 rows total.**

### 2.1 Rider app — iOS

- [ ] App launches, splash screen shows, routes to /login within 3s
- [ ] Enter rider phone number → OTP arrives via Firebase within 30s
      (check Messages — if it does not arrive, Twilio or Firebase
      misconfigured)
- [ ] Enter OTP → lands on home (tabs) screen with a map
- [ ] No "Missing Firebase config" warnings in the console
- [ ] `/settings` call visible in backend logs on launch (Stripe key
      fetch)
- [ ] Pull-to-refresh on home tab does not crash
- [ ] Sign out from the settings screen → returns to /login
- [ ] Re-launch the app → auth state cleared (no auto-login with
      stale token)

Repeat the above table for **rider-Android** and **rider-Web**, and
for **driver-iOS**, **driver-Android**, **driver-Web**.

Driver-specific checks (replace rider lines above):
- [ ] After login, driver lands on `/driver` home (map + GO button),
      not `/(tabs)`
- [ ] Onboarding status banner is absent (driver is `verified`) OR
      banner text matches the status if it's any other value
- [ ] Go Online toggle is enabled

---

## 3. Ride lifecycle — cross-device

This is the highest-value piece of the smoke. **One rider phone, one
driver phone, same staging backend.** Watch the driver screen while
the rider acts, and vice versa.

### 3.1 Happy path (end-to-end)

**Set up**
- [ ] Driver: Go Online. Watch backend logs — a `/ws/driver/{id}`
      connection should open. Location should start streaming to
      `POST /api/v1/drivers/location-batch` every N seconds.

**Request**
- [ ] Rider: open home map, enter pickup (current location) and
      dropoff (a point 2-5 km away inside the service area)
- [ ] Fare estimate appears within 3s with at least one vehicle type
- [ ] Select vehicle, tap Confirm → `POST /api/v1/rides` fires, ride
      status = `searching`

**Match**
- [ ] Driver: receives a ride offer card within ~5s (push notification
      + in-app banner)
- [ ] Tap Accept → offer card dismisses, driver lands on an active-ride
      screen with pickup address + rider contact
- [ ] Rider: "Finding driver..." spinner flips to driver info panel
      **within 2 seconds** (NOT waiting for the 15s poll — WS event
      should trigger the transition)

**En route to pickup**
- [ ] Driver: map shows route to pickup, navigation button launches
      external maps app
- [ ] Rider: driver pin is visible and moves as driver moves (every
      few seconds)

**Arrive**
- [ ] Driver: when near pickup (within ~100m), tap Arrived → status
      updates to `driver_arrived`
- [ ] Rider: receives "Driver has arrived" push notification

**Start**
- [ ] Driver: enter the rider's OTP (shown on rider's screen) → ride
      status flips to `in_progress`
- [ ] Rider: sees "Trip in progress" screen with live route

**Complete**
- [ ] Driver: tap Complete when at dropoff → `/api/v1/drivers/rides/{id}/complete`
      fires → driver sees TripCompleted panel with fare breakdown
- [ ] Rider: routed to ride-completed screen with rating + tip prompt
- [ ] Rater: pick rating, add tip, tap Pay & Done → process-payment
      fires, Stripe charges (see Phase E runbook for the card-specific
      validation)
- [ ] Both apps return to home state

### 3.2 Rider-cancel path

Repeat the setup + request from §3.1, then before the driver accepts:

- [ ] Rider: tap Cancel → ride status → `cancelled`
- [ ] Driver: offer card disappears (if it was showing)
- [ ] No cancellation fee charged (pre-match cancel is free per policy)

Then re-run with driver having already accepted:

- [ ] Rider: tap Cancel after the driver accepts → cancellation fee
      modal appears with the configured fee
- [ ] Confirm → driver sees "Rider cancelled" notification + ride
      clears from driver screen
- [ ] Fee is reflected on the driver's earnings within 1 min

### 3.3 Driver-cancel path

- [ ] Driver: after accepting, tap Cancel → ride status → `cancelled`
- [ ] Rider: sees "Driver cancelled your ride" alert **within 2s** via WS
- [ ] Rider: returned to the home screen with a fresh state, can
      request a new ride

### 3.4 Two-drivers race

- [ ] Start with **two driver devices** online in the same area
- [ ] Rider requests a ride → both drivers receive the offer
- [ ] Both tap Accept at the same time
- [ ] Exactly one driver is confirmed (sees the active-ride screen);
      the other gets a "Ride already accepted" toast + offer clears
- [ ] Rider sees exactly one driver — not an oscillating UI

---

## 4. Resilience

### 4.1 Rider — WS drop mid-ride

- [ ] Mid-ride (any status from `driver_accepted` through `in_progress`),
      toggle the rider's phone to airplane mode for 10s
- [ ] Turn airplane mode off → WS reconnects (check logs:
      `/ws/rider/{id}` re-opens)
- [ ] The ride screen updates to the current backend status without
      needing to relaunch

### 4.2 Rider — kill app mid-ride

- [ ] Mid-ride, force-kill the rider app (swipe away from app switcher)
- [ ] Relaunch — the active ride restores from local AsyncStorage cache
      AND re-syncs to the backend's authoritative status
- [ ] No stale data (if the backend says status=`completed`, the rider
      lands on the ride-completed screen, not the in-progress screen)

### 4.3 Driver — WS drop mid-trip

- [ ] Same as rider: airplane mode for 10s while on an active trip
- [ ] Driver app reconnects automatically with exponential backoff
      (check logs for the reconnect attempts)
- [ ] Location streaming resumes
- [ ] The rider does NOT see a stale driver pin during the drop —
      either the pin pauses where it was, or a "reconnecting..." UI is shown

### 4.4 Driver — background / foreground

- [ ] Mid-trip, background the driver app (home button, don't kill)
- [ ] Wait 60s
- [ ] Return to foreground — ride state restored, location continues
      streaming, no duplicate offers arrived while backgrounded

### 4.5 Token refresh mid-ride

This is the classic slow failure. Access tokens expire after 15 min
per `config.py:ACCESS_TOKEN_EXPIRE_MINUTES`.

- [ ] Start a ride, then wait 15+ minutes without interacting (or
      shorten the TTL on staging to 2 minutes and do the same)
- [ ] Without force-closing, continue the ride (tap any action)
- [ ] The API call succeeds (token refresh fires transparently in the
      client's axios interceptor; see `shared/api/client.ts`)
- [ ] No 401 toast shown to the user

---

## 5. Notifications + maps + Firebase

### 5.1 Push notifications

- [ ] Rider receives a "Ride complete" push when the driver completes
      (either foreground or backgrounded)
- [ ] Driver receives a "New ride offer" push when a ride is dispatched
      to them while the app is backgrounded
- [ ] Tapping a notification opens the app to the relevant screen
      (deep-link works on both platforms)

### 5.2 Maps

- [ ] Rider: the map tiles render (not a grey box — grey box = API key
      not whitelisted for the bundle ID)
- [ ] Both apps: the rendered route polyline matches what Google
      Directions would produce for the same origin+destination
- [ ] Location pin on each map is accurate (within 10m of actual
      location)

### 5.3 Firebase

- [ ] Crashlytics dashboard shows a session from each app during this
      smoke (confirms crash reporting connected)
- [ ] No `ERROR/FirebaseApp` entries in logcat / Console during the walkthrough

---

## 6. Contract parity sanity checks

Cross-cutting — run once, not per platform.

- [ ] During the happy-path ride, watch the DB `rides.status` column:
      it must progress through exactly `searching → driver_assigned →
      driver_accepted → driver_arrived → in_progress → completed`.
      No other values.
- [ ] The rider app and driver app display the same ride ID during
      the trip (both apps show `ride_xxx...` matching DB)
- [ ] The fare the rider sees on the ride-completed screen equals
      `rides.total_fare + rides.tip_amount` from the DB
- [ ] The driver earnings in the TripCompleted panel equal
      `rides.driver_earnings` from the DB (rider estimate ≈ driver
      payout + platform cut, no rounding drift >$0.01)

---

## 7. Sign-off

- [ ] All §§ 2-6 checkboxes ticked for each app × platform cell you
      committed to supporting this release
- [ ] No Crashlytics crashes logged from the smoke session
- [ ] No `payment_status = failed` rows from the smoke session (if
      any, they're Phase E failures — re-run the card scenarios)
- [ ] All "stuck ride" queries from `P0-5_PHASE_E_RUNBOOK.md §8`
      return empty

Signed off by: `__________`   Date: `__________`
Platforms covered: [ ] iOS  [ ] Android  [ ] Web

---

## 8. What to do if something fails

| Failure | Likely cause | First check |
|---|---|---|
| OTP never arrives | Twilio creds wrong, or Firebase Phone Auth not enabled | `full_stack_smoke.py --backend ...` OTP check |
| Map renders grey | Google Maps API key not whitelisted for the bundle ID | Google Cloud Console → API key restrictions |
| WS does not reconnect | Ingress dropping WS upgrades | Check nginx / LB websocket proxy config |
| Rider stuck on "Finding driver" after driver accepts | WS event not published to `rider_{id}` channel | Backend logs for the accept handler |
| Driver doesn't receive offer | `/ws/driver/{id}` not open, or push token missing | Driver's `/drivers/push-token` was called on login |
| Token refresh breaks at 15min | Axios interceptor not wired OR refresh endpoint broken | `full_stack_smoke.py` auth_gate + add a token-expired check |
| App crashes on boot | Missing env, bad Firebase config | Crashlytics stack trace |

---

## 9. Scope this runbook does NOT cover

- Payment scenarios (decline, 3DS, processor error) — see
  `docs/scoping/P0-5_PHASE_E_RUNBOOK.md`
- Native-layer automated E2E (Detox, Maestro) — P3 in
  `docs/E2E_TEST_GAP_ANALYSIS.md`; once wired, much of §§ 2-4 here
  can migrate from human-run to automated
- Load / performance — use `backend/tests/perf_baseline.py` and
  `perf_post_rides.py`; not a mobile-UI concern
- Security / pentest — separate exercise; not an inventory check

## 10. Maintenance

When someone adds a new user-visible flow to the apps, add it here.
The rule of thumb: if a bug in the flow would only be caught by a
human looking at the UI against a deployed stack, it belongs here.
Everything else goes into automated tests.
