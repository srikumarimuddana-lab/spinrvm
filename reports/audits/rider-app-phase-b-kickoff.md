# Phase B — Dimensions 05–08
## UI/UX · Real-Time · State Machine · Payments

---

## DIMENSION 05 — Android & iOS UI/UX Quality

### What to audit
36 screens across iOS + Android. Safe area, keyboard handling, map button z-index,
touch targets, BackHandler on Android, empty/error states everywhere.

### Rider-specific risks
- `driver-arriving.tsx`: FreeCancelTimer overlapping with map buttons on small screens
- `ride-in-progress.tsx`: map + driver overlay + ETA card — z-index stacking on Android
- `payment-confirm.tsx`: keyboard pushes up form; card selection and submit button visible?
- `search-destination.tsx`: autocomplete list hidden behind keyboard on small phones
- Android hardware back button on `driver-arriving`, `driver-arrived`, `ride-in-progress`
  — must be blocked (same P1-4 finding as driver app)
- `ride-completed.tsx`: star rating + tip buttons — touch targets ≥ 44pt?
- Safe area: all full-screen map screens (home, driver-arriving, ride-in-progress)
  must apply `useSafeAreaInsets` for top/bottom bars
- Empty states: activity list, saved places, scheduled rides, notifications

### Files to read
```
rider-app/app/(tabs)/index.tsx
rider-app/app/driver-arriving.tsx
rider-app/app/driver-arrived.tsx
rider-app/app/ride-in-progress.tsx
rider-app/app/ride-completed.tsx
rider-app/app/payment-confirm.tsx
rider-app/app/search-destination.tsx
rider-app/app/(tabs)/activity.tsx
rider-app/components/FreeCancelTimer.tsx
shared/components/OfflineBanner.tsx
```

### Kick-off prompt
```
You are auditing the Spinr rider app for Android & iOS UI/UX quality (Dimension 05).

Context:
- Framework: audit-framework/dimensions/05-ui-ux-android-ios.md
- Ground rules: audit-framework/ground-rules.md
- Scope: rider-app/app/ (all 36 screens) + rider-app/components/ + shared/components/

Your task: Work through every checklist item in dimension 05. Specific checks:
1. Safe area insets: do ALL map screens (index, driver-arriving, ride-in-progress) use
   useSafeAreaInsets? Is OfflineBanner above notch (P0-7 fix from driver audit — verify here)?
2. Android BackHandler: is it blocking back press on driver-arriving, driver-arrived,
   ride-in-progress screens? (Missing = HIGH — rider exits active trip accidentally)
3. Keyboard: does payment-confirm.tsx and search-destination.tsx use KeyboardAvoidingView?
   Are submit buttons visible when keyboard is open?
4. Touch targets: star rating buttons and tip amount buttons in ride-completed.tsx — are
   they ≥ 44×44pt?
5. FreeCancelTimer: does it overlap map controls on iPhone SE (375pt wide)?
6. Empty states: what does activity.tsx show when there are 0 rides? saved-places.tsx
   when 0 saved? notifications.tsx when 0 notifications?
7. Map buttons z-index: on ride-in-progress and driver-arriving, can the user tap the
   SOS and chat buttons, or does the map layer absorb the touches?
8. Error states: what does each screen show when the API call fails?

Write findings to: reports/audits/2026-04-19-rider-app-v1.txt under TASK 05.
```

---

## DIMENSION 06 — Real-Time Features (WebSocket & GPS)

### What to audit
`useRiderSocket` hook manages the rider WebSocket. Driver location updates must flow
reliably. Polling fallback must activate when WS fails. Race condition between WS updates
and poll responses must not regress ride state.

### Rider-specific risks
- WS reconnect: `useRiderSocket` has exponential backoff [1s,2s,5s,10s,30s] — verify jitter
- WS auth: first message must be `{type:'auth', token, client_type:'rider'}` — server rejects otherwise
- Race condition: `updateDriverLocation()` (WS) vs `fetchRide()` (poll) — if poll response
  arrives AFTER a newer WS update, it must not overwrite the newer driver position
- `driver_timeout` message: backend re-dispatching — does UI reflect "searching again" state?
- GPS on rider side: home screen requests location on load — is foreground permission
  requested before background? Is there a re-permission flow if denied?
- Chat polling: `chat-driver.tsx` polls messages — messages can be missed between polls;
  no WS delivery confirmed — flag as MEDIUM/HIGH
- App foreground: WS reconnects on `AppState active` — verified in useRiderSocket?
- WS cleanup: is WebSocket closed when rider completes ride / logs out?

### Files to read
```
rider-app/hooks/useRiderSocket.ts
rider-app/app/driver-arriving.tsx       ← poll interval + WS interaction
rider-app/app/ride-in-progress.tsx      ← poll + WS driver location
rider-app/app/chat-driver.tsx           ← polling only?
rider-app/store/rideStore.ts            ← updateDriverLocation, applyRideStatusFromWS
rider-app/app/(tabs)/index.tsx          ← GPS permission request
```

### Kick-off prompt
```
You are auditing the Spinr rider app for real-time reliability (Dimension 06).

Context:
- Framework: audit-framework/dimensions/06-real-time.md
- Ground rules: audit-framework/ground-rules.md
- Scope: rider-app/hooks/useRiderSocket.ts + related screens + store

Your task: Work through every checklist item in dimension 06. Specific checks:
1. WS auth: is the auth message sent as the FIRST message before any data is consumed?
2. Reconnect: does useRiderSocket implement exponential backoff with jitter?
3. Race condition: if fetchRide() poll result arrives after a WS driver_location_update,
   does the poll overwrite the more recent driver position? Check rideStore.ts.
4. driver_timeout handling: when backend re-dispatches, does UI show "searching again"?
5. Chat: does chat-driver.tsx use WebSocket for delivery or polling only?
   If polling, what is the interval and what happens when app is backgrounded?
6. GPS permission: does index.tsx request foreground before background permission?
   Is there a re-permission flow shown when user denies location?
7. WS cleanup: is the socket closed on ride completion and on logout?
8. Heartbeat: does useRiderSocket reply to server ping with pong?
9. App background/foreground: does WS reconnect when app comes to foreground?

Write findings to: reports/audits/2026-04-19-rider-app-v1.txt under TASK 06.
```

---

## DIMENSION 07 — State Machine & Dispatch

### What to audit
Rider ride state machine: searching → driver_assigned → driver_accepted → driver_arrived
→ in_progress → completed. Every transition must be guarded server-side.
Client state machine must match server exactly.

### Rider-specific risks
- Cancellation after driver_arrived: rider must be shown a warning + cancellation fee
  (P1-1 from driver audit — verify the backend guard exists for RIDER cancel endpoint)
- Can rider call `/rides/{id}/start` directly? Should be driver-only
- Can rider call `/rides/{id}/complete` directly? Should be driver-only
- Double booking: can rider create two active rides simultaneously?
- Ride hydration on cold start: `hydrateActiveRide()` in rideStore — if stale ride in
  AsyncStorage, does it correctly validate against backend before showing ride screen?
- FreeCancelTimer: when timer reaches 0, does app auto-cancel or just stop the timer?
- `driver_timeout` → re-dispatch: client must reset to "searching" UI, not stay on
  "driver found" with stale driver card showing

### Files to read
```
rider-app/store/rideStore.ts            ← state machine, hydrateActiveRide
rider-app/app/driver-arriving.tsx       ← cancellation, FreeCancelTimer
rider-app/app/payment-confirm.tsx       ← createRide, double-booking guard
backend/routes/rides.py                ← state guard on every endpoint
```

### Kick-off prompt
```
You are auditing the Spinr rider app for state machine correctness (Dimension 07).

Context:
- Framework: audit-framework/dimensions/07-state-machine.md
- Ground rules: audit-framework/ground-rules.md
- Scope: rider-app/store/rideStore.ts + rider-app/app/ ride screens + backend/routes/rides.py

Rider state machine: searching → driver_assigned → driver_accepted →
                     driver_arrived → in_progress → completed / cancelled

Your task: Work through every checklist item in dimension 07. Specific checks:
1. Can a rider cancel after driver_arrived without paying a cancellation fee?
   Check both backend/routes/rides.py (cancel_ride endpoint) AND driver-arriving.tsx UI.
2. Can a rider call /rides/{id}/start or /rides/{id}/complete directly via API?
   These should require driver role — verify in rides.py.
3. Double booking: does createRide() check for an existing active ride before creating?
   Both client (rideStore.ts) and server (rides.py)?
4. Cold start: does hydrateActiveRide() validate the stored ride against /rides/active
   before routing to a ride screen?
5. FreeCancelTimer: when timer hits 0, does it auto-cancel the ride or just show expired?
6. driver_timeout: does applyRideStatusFromWS() reset UI back to "searching" state?
7. Are ride status strings centralized as constants or scattered as magic strings?

Write findings to: reports/audits/2026-04-19-rider-app-v1.txt under TASK 07.
```

---

## DIMENSION 08 — Payments & Earnings

### What to audit
Rider payments: Stripe PaymentIntent for card payments + in-app wallet.
No raw card data must reach backend. Idempotency on booking. Webhook integrity.

### Rider-specific risks
- PaymentIntent idempotency: if rider taps "Book" twice (network delay), is there
  an idempotency key preventing double charge?
- Double-tap guard: `payment-confirm.tsx` — is the submit button disabled after first tap?
- Wallet pay: `payWithWallet(rideId, amount)` — is balance checked server-side before deducting?
- Tip flow: tip submitted after ride in `ride-completed.tsx` — separate PaymentIntent or
  added to original? Is tip idempotent (can't tip twice)?
- Card management: `manage-cards.tsx` — add/remove cards via Stripe — are SetupIntents used?
  Is customer ID derived server-side (not client-supplied)?
- Fare split payment: each participant pays their share — is each payment idempotent?
- Promo discount: is the discount validated server-side against the actual ride fare?
  Can a client send a manipulated promo to get 100% discount?
- Webhook: Stripe webhook signature verified? Idempotency on event processing?

### Files to read
```
rider-app/app/payment-confirm.tsx
rider-app/app/ride-completed.tsx        ← tip payment
rider-app/app/wallet.tsx
rider-app/app/manage-cards.tsx
rider-app/store/walletStore.ts
rider-app/store/rideStore.ts            ← createRide payment_method
backend/routes/payments.py
backend/routes/rides.py                ← fare validation
backend/routes/promo.py                ← discount server-side validation
```

### Kick-off prompt
```
You are auditing the Spinr rider app for payment safety and PCI compliance (Dimension 08).

Context:
- Framework: audit-framework/dimensions/08-payments.md
- Ground rules: audit-framework/ground-rules.md
  (Stripe test keys sk_test_* → LOW severity only. Stripe publishable keys pk_* in
  app.config.ts are public by design — not a finding.)
- Scope: rider-app payment screens + backend/routes/payments.py + rides.py + promo.py

Your task: Work through every checklist item in dimension 08. Specific checks:
1. Idempotency: does payment-confirm.tsx / createRide() pass an idempotency key to
   backend? Does backend pass it to Stripe PaymentIntent creation?
2. Double-tap: is the Book button disabled immediately after first tap?
3. Wallet balance: does /wallet/pay validate sufficient balance server-side?
4. Tip idempotency: can a rider submit a tip twice for the same ride?
5. Card add: does manage-cards.tsx use Stripe SetupIntent? Is customer_id server-derived?
6. Promo discount: does backend validate the discount against the actual stored fare
   (not the client-sent fare)? Can a client get 100% discount by manipulating the request?
7. Fare split payment: is each participant payment idempotent?
8. Webhook: does backend verify Stripe webhook HMAC signature?
9. Raw card data: does any screen collect card number, CVV, or expiry directly?
   (Should be handled entirely by Stripe SDK)

Write findings to: reports/audits/2026-04-19-rider-app-v1.txt under TASK 08.
```

---

## Phase B End-of-Phase Checkpoint

Before moving to Phase C, confirm:
- [ ] Findings written under TASK 05–08 in audit file
- [ ] All new CRITICAL findings escalated to P0 sprint
- [ ] All new HIGH findings escalated to P1 sprint
- [ ] Android BackHandler gaps noted in P1
- [ ] Payment idempotency status clear (PASS or finding logged)
