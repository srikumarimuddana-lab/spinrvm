# P1 — Rider App High Priority: Fix Before Beta Testing

These items must be resolved before sending the app to real beta testers.
They cover broken flows, security risks, and UX failures that would make
testing unreliable or unsafe.

**Estimated total effort:** ~3–4 days

---

## R-P1-1 · Rider Can Cancel After Driver Has Arrived — No Cancellation Fee

**What's wrong:** There is no backend guard preventing a rider from cancelling once the
driver is in `driver_arrived` state. The driver loses the job and travel time with no
compensation.

**File to fix:** `backend/routes/rides.py` — rider cancel endpoint

**How to fix:**
```python
CANCEL_FREE_STATES = {'searching', 'driver_assigned', 'driver_accepted'}
if ride.status not in CANCEL_FREE_STATES:
    if ride.status == 'driver_arrived':
        # Charge cancellation fee — calculate and apply
        raise RideStateError("Cancellation fee applies after driver has arrived")
    raise RideStateError(f"Cannot cancel from state: {ride.status}")
```

Also update `driver-arriving.tsx` UI: FreeCancelTimer must disable the Cancel button
(or show a fee warning) after the timer expires.

**Effort:** 2–3 hours

---

## R-P1-2 · Chat Messages Already on WebSocket — Verify Driver-Side Delivery

**Audit finding [01-9 PASS]:** `chat-driver.tsx` does NOT poll. Messages arrive via
`chatMessages` in rideStore populated by `useRiderSocket`. History is loaded once on
mount via `GET /rides/{id}/messages`.

**Remaining risk:** Confirm that `useRiderSocket.ts` actually dispatches incoming
`chat_message` WS events to `addChatMessage()` — if the WS handler for chat messages
is missing, new driver messages will not render in real time.

**File to verify:** `rider-app/hooks/useRiderSocket.ts`

**How to fix if missing:**
```typescript
case 'chat_message':
  useRideStore.getState().addChatMessage(data.message);
  break;
```

**Effort:** 1 hour (verify + patch if handler is absent)

---

## R-P1-3 · Fare Split Entry Point Confirmed — Only Accessible Pre-Ride, Not During Ride

**Audit finding [01-8 PASS]:** `fare-split.tsx` IS reachable — from `payment-confirm.tsx:278`
(`router.push('/fare-split')`). The screen works correctly pre-booking.

**Remaining gap:** fare-split can only be initiated at payment-confirm time, before the ride
starts. A rider who decides mid-ride to split the fare has no path to `fare-split.tsx`
from `ride-in-progress.tsx`.

**File to fix:** `rider-app/app/ride-in-progress.tsx`
Add a "Split Fare" menu option in the ride in-progress screen that pushes to `/fare-split`.

**Effort:** 1 hour

---

## R-P1-4 · rate-ride.tsx Is Orphaned — ride-completed.tsx Handles Rating Inline

**Audit finding [01-6 LOW]:** `ride-completed.tsx` has its own inline rating section
(lines 323–397) and calls `rateRide()` directly on submit (line 127). It then navigates
to `/(tabs)`. No screen navigates to `rate-ride.tsx` — it is dead code.

Both screens call `rateRide()` with different tip option sets ($2/$5/$10 vs $1/$3/$5).

**File to remove:** `rider-app/app/rate-ride.tsx`
Also remove the `<Stack.Screen name="rate-ride" />` entry in `_layout.tsx:370`.

**Effort:** 30 minutes (file deletion + cleanup)

---

## R-P1-5 · Scheduled Rides Not Listed in Activity Tab

**Audit finding [01-4 MEDIUM] — confirmed.** `activity.tsx` calls only `GET /rides/history`
(completed/cancelled). Scheduled rides are accessible via Account → Scheduled Rides
(account.tsx:183) but not from the Activity tab.

**File to fix:** `rider-app/app/(tabs)/activity.tsx`
Add an "Upcoming" tab using the existing `scheduledRides` store state and
`fetchScheduledRides()` — no new API endpoints needed.

**Effort:** 2–3 hours

---

## R-P1-6 · PIPEDA: Data Export and Account Deletion Are UI Stubs — No API Call

**Audit finding [01-2 HIGH] — confirmed.** Both buttons exist in `privacy-settings.tsx`
but neither makes an API call:
- "Download My Data" (line 104): shows a success alert only — no POST to backend.
- "Delete Account" (lines 29–47): confirmation → success alert only — no DELETE to backend.

PIPEDA requires data subjects the right to access and erasure. Presenting stubs that
falsely confirm these actions is a legal compliance violation.

**File to fix:** `rider-app/app/privacy-settings.tsx`

**How to fix:**
```typescript
// Download My Data (line 106 — replace setAlertState with API call):
const res = await api.post('/user/data-export');
// then show success alert

// Delete Account (lines 39–42 — replace setAlertState with API call):
await api.delete('/user/account');
await logout();
router.replace('/login');
```

Backend endpoints needed:
- `POST /user/data-export` — queue email with signed download link
- `DELETE /user/account` — soft-delete, 30-day grace period per PIPEDA

**Effort:** 4–6 hours (frontend + backend)

---

## R-P1-7 · Payment Idempotency — Double Charge on Network Retry

**What's wrong:** `createRide()` in `rideStore.ts` does not pass an idempotency key to
the backend. If the rider taps "Book" and the network drops mid-request, a retry could
create two rides and charge the card twice.

**File to fix:** `rider-app/store/rideStore.ts` + `backend/routes/rides.py`

**How to fix:**
```typescript
// rideStore.ts — generate key before the call:
const idempotencyKey = `ride-${userId}-${Date.now()}`;
await api.post('/rides', payload, {
  headers: { 'Idempotency-Key': idempotencyKey }
});
```
```python
# backend/routes/rides.py — honour the key:
idempotency_key = request.headers.get('Idempotency-Key')
if idempotency_key:
    existing = await check_idempotency_cache(idempotency_key)
    if existing:
        return existing
```

**Effort:** 2–3 hours

---

## R-P1-8 · Promo Discount Not Validated Server-Side Against Actual Fare

**What's wrong:** `applyPromo()` validates a promo code by sending the ride fare from
the client. A malicious user could send a manipulated fare amount (e.g., $1) to make
the discount appear proportionally larger.

**File to fix:** `backend/routes/promo.py` — validate discount against the server-stored
ride fare, not the client-supplied fare.

**Effort:** 2 hours

---

## R-P1-9 · Accessibility: SOS Button and Star Rating Missing Labels

**What's wrong:** The SOS button in `shared/components/SOSButton.tsx` and the 5 star
rating buttons in `ride-completed.tsx` likely lack `accessibilityLabel` and
`accessibilityRole`. VoiceOver users cannot operate these critical actions.

**File to fix:**
- `shared/components/SOSButton.tsx`
- `rider-app/app/ride-completed.tsx`

**How to fix:**
```tsx
// SOSButton:
<TouchableOpacity
  accessibilityLabel="Emergency SOS"
  accessibilityRole="button"
  accessibilityHint="Hold for 1.5 seconds to send an emergency alert"
  ...
>

// Star rating (for each star i = 1..5):
<TouchableOpacity
  accessibilityLabel={`Rate ${i} star${i > 1 ? 's' : ''}`}
  accessibilityRole="button"
  accessibilityState={{ selected: rating === i }}
  ...
>
```

**Effort:** 2 hours

---

## R-P1-10 · No i18n Library — All Strings Are Hardcoded English

**What's wrong:** There is no internationalisation library in the rider app. All
user-visible strings are hardcoded English in JSX. French (fr-CA) is legally required
for Canadian federal businesses under the Official Languages Act.

**File to fix:** This is an architectural gap requiring a full i18n setup.

**How to fix:**
1. Install `expo-localization` + `i18next` + `react-i18next`
2. Create `rider-app/i18n/en.json` and `rider-app/i18n/fr.json`
3. Wrap all user-visible strings with `t('key')` calls
4. Add language selection in `settings.tsx`

**Effort:** 3–5 days (full extraction of all strings across 36 screens)
This is the largest single item in the P1 sprint.

---

## R-P1-11 · become-driver.tsx Navigates to `/(driver)` — Route Does Not Exist

**Audit finding [01-3 HIGH].** After `registerDriver()` succeeds, the screen calls:
```typescript
router.replace('/(driver)' as any)   // become-driver.tsx:257
```
The rider app has no `(driver)` route group. Expo Router throws an Unmatched Route
error. The rider successfully completes a 5-step form and is then shown nothing.

**File to fix:** `rider-app/app/become-driver.tsx:257`

**How to fix:**
```typescript
// Replace:
router.replace('/(driver)' as any)

// With:
setAlertState({
  visible: true,
  title: 'Application Submitted!',
  message: 'Waiting for approval. To start driving, download the Spinr Driver app.',
  variant: 'success',
  buttons: [
    { text: 'Download Driver App', onPress: () => Linking.openURL('https://spinr.ca/driver-app') },
    { text: 'OK', onPress: () => router.replace('/(tabs)') },
  ],
});
```

**Effort:** 1 hour

---

## R-P1-12 · Firebase Token Does Not Enforce Rider App Audience

**Audit finding [02-2 MEDIUM].** `get_current_user()` in `backend/dependencies/__init__.py`
calls `firebase_auth.verify_id_token(token)` with no rider app audience check. The driver
auth path enforces `FIREBASE_DRIVER_APP_ID`; the rider dependency has no equivalent. A valid
Firebase token from the driver app can authenticate to rider endpoints and trigger auto-creation
of a rider account without phone OTP.

**File to fix:** `backend/dependencies/__init__.py`

**How to fix:**
```python
# After verify_id_token(token) succeeds:
rider_app_id = getattr(settings, 'FIREBASE_RIDER_APP_ID', None)
if rider_app_id and payload.get('aud') != rider_app_id:
    raise HTTPException(status_code=401, detail="Invalid token audience")
```
Add `FIREBASE_RIDER_APP_ID` to `core/config.py` settings.

**Effort:** 1 hour

---

## R-P1-13 · Firebase-Authenticated Users Bypass Force-Logout-All

**Audit finding [02-3 MEDIUM].** The Firebase auth path in `get_current_user()` returns the
user without checking `token_version` or `session_id`. JWT-authenticated users are immediately
kicked out when `/auth/logout-all` bumps `token_version`. Firebase-authenticated users are not
— their sessions remain valid for up to 1 hour after a force-logout event.

**File to fix:** `backend/dependencies/__init__.py:167–172`

**How to fix:**
```python
if user:
    # Apply same revocation checks as JWT path
    if _token_version_mismatch({}, user):  # Firebase tokens have no token_version claim → treat as 0
        raise HTTPException(status_code=401, detail="Session revoked — please log in again.")
    token_session = payload.get("session_id")
    db_session = user.get("current_session_id")
    if db_session and token_session and token_session != db_session:
        raise HTTPException(status_code=401, detail="Session expired.")
```
Also call `firebase_auth.revoke_refresh_tokens(uid)` from the `/auth/logout-all` handler.

**Effort:** 2 hours

---

## R-P1-14 · OTP Comparison Not Constant-Time

**Audit finding [02-4 MEDIUM].** OTP verification queries `(phone, hash_otp(code))` — the
database does the equality check. Timing variations in a B-tree lookup can leak hash prefix
information under repeated timing measurements. Should use `hmac.compare_digest`.

**File to fix:** `backend/routes/auth.py` — OTP verify path

**How to fix:**
```python
import hmac
record = await get_otp_record_by_phone(phone)  # fetch by phone only
if not record or not hmac.compare_digest(record["otp_hash"], hash_otp(code)):
    await _record_otp_failure(phone)
    raise HTTPException(400, "Invalid OTP")
```

**Effort:** 30 minutes

---

## R-P1-15 · Backend Phone Schema Accepts Any Country Code

**Audit finding [04-1 HIGH].** `SendOTPRequest` and `VerifyOTPRequest` in
`backend/schemas.py:17, 21` use `pattern=r'^\+\d+$'` which accepts any E.164
phone number (UK, France, etc.). The Canada/US +1 enforcement only exists in
the login.tsx UI (line 77). A direct POST to `/auth/send-otp` with a non-Canadian
number bypasses the market restriction entirely.

**File to fix:** `backend/schemas.py:17, 21`

**How to fix:**
```python
# Change both SendOTPRequest and VerifyOTPRequest:
phone: str = Field(
    ...,
    min_length=12,
    max_length=12,
    pattern=r'^\+1\d{10}$',
    description="Canadian/US phone in E.164 format: +1XXXXXXXXXX"
)
```

**Effort:** 30 minutes

---

## R-P1-17 · /rides/{id}/start Accepts Rider Token — Bypasses OTP Verification

**Audit finding [07-1 HIGH].** `rider_start_ride` (POST `/rides/{id}/start`) in
`backend/routes/rides.py:1876–1890` is reachable by any authenticated rider. It
checks only that the caller is the ride's `rider_id`, then unconditionally flips
the ride to `in_progress` and records `ride_started_at` — no OTP check, no driver
role verification. The intended start path is the driver-side
`/drivers/rides/{id}/start` endpoint, which requires the 4-digit OTP. This
parallel path completely bypasses pickup OTP verification.

**File to fix:** `backend/routes/rides.py:1876–1890`

**How to fix:**
```python
# Option A — remove the endpoint entirely (driver-side path is sufficient).
# Option B — restrict to driver role only:
@api_router.post("/{ride_id}/start")
async def rider_start_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    driver = await db.find_one("drivers", {"user_id": current_user["id"]})
    ride = await db_supabase.get_ride(ride_id)
    if not ride or not driver or ride.get("driver_id") != driver["id"]:
        raise HTTPException(status_code=403, detail="Only the assigned driver can start the ride")
    if ride.get("status") not in ["driver_arrived"]:
        raise HTTPException(status_code=400, detail=f"Cannot start from state: {ride.get('status')}")
    await db_supabase.update_ride(ride_id, {"status": "in_progress", ...})
```

**Effort:** 1 hour

---

## R-P1-18 · cancel_ride Guard Uses Wrong Status String — In-Progress Rides Can Be Cancelled Free

**Audit finding [07-2 HIGH].** `cancel_ride_rider` in `backend/routes/rides.py:1341`
checks `ride.get("status") == 'trip_in_progress'` to block cancellation of live trips.
The actual status string is `'in_progress'` — `'trip_in_progress'` is never assigned
anywhere. This guard **never fires**. A rider can cancel a live in-progress trip and
receive zero cancellation fee, leaving the driver mid-journey with no payment.

**File to fix:** `backend/routes/rides.py:1341`

**How to fix:**
```python
# Change:
if ride.get("status") == 'trip_in_progress':
# To:
NON_CANCELLABLE = {'in_progress', 'completed', 'cancelled'}
if ride.get('status') in NON_CANCELLABLE:
    raise HTTPException(
        status_code=400,
        detail=f"Cannot cancel a ride that is {ride.get('status')}"
    )
```

**Effort:** 30 minutes

---

## R-P1-16 · driver_timeout Has No "Searching Again" UI Feedback

**Audit finding [06-4 MEDIUM].** When the backend re-dispatches after a driver fails
to accept, `useRiderSocket` receives a `driver_timeout` message and calls `fetchRide()`.
The ride status may return to `'searching'` but there is no rider-visible notification
that their original driver timed out and a new one is being found. The driver card
disappears silently, leaving the rider confused.

**File to fix:** `rider-app/hooks/useRiderSocket.ts` (driver_timeout case, line 113)

**How to fix:**
```typescript
case 'driver_timeout':
  Alert.alert(
    'Driver Not Available',
    'Your driver didn\'t respond in time. Looking for a new driver…',
    [{ text: 'OK' }]
  );
  if (rideId) fetchRide(rideId);
  break;
```

**Effort:** 30 minutes

---

## R-P1-19 · /wallet/pay Does Not Validate Amount Against Stored Ride Fare

**Audit finding [08-1 HIGH].** `WalletPayRequest` in `backend/routes/wallet.py:89–91`
accepts any amount > 0 from the client. The handler checks sufficient balance but never
fetches the ride record to verify the requested debit equals the stored fare. A client can
POST `/wallet/pay` with `amount=0.01` for any valid `ride_id` and the ride is marked as
fully paid while only $0.01 is debited. The Stripe card path correctly cross-checks amount
vs. stored fare (payments.py:72–85); the wallet path must do the same.

**File to fix:** `backend/routes/wallet.py` — `wallet_pay` handler

**How to fix:**
```python
async def wallet_pay(req: WalletPayRequest, current_user: dict = Depends(get_current_user)):
    ride = await db.find_one("rides", {"id": req.ride_id})
    if not ride or ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=404, detail="Ride not found")
    fare = _d(ride.get("total_fare", 0))
    if _d(req.amount) != fare:
        raise HTTPException(
            status_code=400,
            detail=f"Payment amount {req.amount} does not match ride fare {fare}",
        )
    # ... rest of handler unchanged
```

**Effort:** 1 hour

---

## R-P1-20 · Tip Endpoint Is Additive — Rider Can Submit Multiple Tips for the Same Ride

**Audit finding [08-2 HIGH].** `POST /rides/{ride_id}/tip` in `backend/routes/rides.py:964`
uses `new_tip = ride.get("tip_amount", 0) + tip_amount` — each call accumulates rather than
setting a one-time tip. The client-side `tipSent` flag (`ride-completed.tsx:109`) prevents
re-submission within one screen session, but is lost on navigation or app restart. Multiple
calls inflate `driver_earnings` by the tip amount each time.

**File to fix:** `backend/routes/rides.py` — `add_tip` handler (line 964)

**How to fix:**
```python
# Option A — one-time guard at the database level:
if ride.get("tip_amount", 0) > 0:
    raise HTTPException(status_code=409, detail="Tip already submitted for this ride")

# Option B — add a tip_paid boolean column; set True on first call.
```

**Effort:** 30 minutes

---

## R-P1-21 · createRide() Missing Idempotency Key — Double Charge on Network Retry

**Audit finding [08-3 HIGH].** (Confirms and supersedes R-P1-7.) Neither `createRide()` in
`rideStore.ts` nor the backend `create_ride` handler implements a request idempotency key.
A network timeout followed by a user retry creates two rides and initiates two payment
intents. The `isSubmitting` ref in `payment-confirm.tsx` catches same-session double-taps
but not retries or back-navigation re-submits.

**File to fix:** `rider-app/store/rideStore.ts` + `backend/routes/rides.py`

**See also:** R-P1-7 (same fix prescription — this finding confirms R-P1-7 as still open).

**Effort:** 2–3 hours

---

## R-P1-22 · Jest Coverage Threshold Missing — Coverage Can Drop to Zero

**Audit finding [09-1 HIGH].** `rider-app/jest.config.js` has no `coverageThreshold`
block. CI runs `yarn test --ci --coverage` but Jest will always exit 0 regardless of
coverage percentage. A PR that deletes all tests passes CI silently.

**File to fix:** `rider-app/jest.config.js`

**How to fix:**
```js
module.exports = {
  // ...existing config...
  coverageThreshold: {
    global: {
      lines: 70,
      functions: 60,
      branches: 60,
      statements: 70,
    },
  },
};
```

**Effort:** 15 minutes

---

## R-P1-23 · rideStore.test.ts Missing hydrateActiveRide, Double-Booking Guard,
           Cancel-After-driver_arrived Tests

**Audit finding [09-2 HIGH].** Three critical paths have no test coverage:
`hydrateActiveRide` (stale-ride cold-start bug), the double-booking concurrent
createRide guard, and cancelRide when `status === 'driver_arrived'` (confirmed
live bug R-P1-18 and R-P1-21 lack regression coverage).

**File to fix:** `rider-app/store/__tests__/rideStore.test.ts`

**How to fix:** Add describe blocks for each path — see finding [09-2] for
specific test cases required.

**Effort:** 3–4 hours

---

## R-P1-24 · rideStore.ws.test.ts Missing driver_timeout, ride_cancelled, WS/Poll Race

**Audit finding [09-3 HIGH].** Three WebSocket scenarios are untested:
`driver_timeout` re-dispatch (R-P1-16), `ride_cancelled` event (backend-initiated
cancellation), and the WS-vs-poll driver-position race (R-P2-29). These are paths
real users hit when a driver fails to respond or cancels mid-trip.

**File to fix:** `rider-app/store/__tests__/rideStore.ws.test.ts`

**How to fix:** Add three it() cases — see finding [09-3] for specific scenarios.

**Effort:** 2–3 hours

---

## R-P1-25 · walletStore.test.ts Missing payWithWallet and Tip Idempotency Tests

**Audit finding [09-4 HIGH].** `payWithWallet` is listed in the file header
("Covers: ... payWithWallet ...") but no test exists. Insufficient balance
rejection and amount-mismatch error paths are untested. `addTip` / `rateRide`
have no tests — the tip-accumulation bug (R-P1-20) has zero regression safety net.

**File to fix:** `rider-app/store/__tests__/walletStore.test.ts`

**How to fix:** Add describe blocks for `payWithWallet` (success, 400 balance,
400 mismatch) and `addTip` (success, 409 duplicate) — see finding [09-4].

**Effort:** 2 hours

---

## R-P1-26 · E2E ride-booking.spec.ts Missing Rating/Tip Steps and UI Assertions

**Audit finding [09-6 HIGH].** The E2E booking flow ends at 'completed' with no
rating or tip step. All stage assertions are `expect(page.locator('body')).toBeVisible()`
— no screen-specific element is verified. The test loop re-seeds auth per stage
rather than running a continuous session.

**File to fix:** `rider-app/e2e/ride-booking.spec.ts`

**How to fix:** Add rate and tip stages; replace body-visible assertion with
getByText/getByTestId assertions per stage; convert loop to a sequential
single-session test using `rideStatusSequence`. See finding [09-6] for details.

**Effort:** 4–6 hours

---

## R-P1-27 · spinr.app and spinr-track.app Not in CORS Allowlist

**Audit finding [11-1 HIGH].** Neither `https://spinr.app` nor `https://spinr-track.app`
is in the backend CORS allowlist. The hardcoded `always_allowed` list in
`backend/core/middleware.py:310` only contains `https://spinr-admin.vercel.app` and
localhost origins. `ALLOWED_ORIGINS` default (`core/config.py:47`) likewise omits both
domains. Any browser-side call from spinr.app or spinr-track.app (e.g. the public
live-tracking page calling `GET /api/v1/rides/track/{token}`) will be rejected with a
CORS error.

The associated domain declarations in `app.config.ts:37–39` confirm that both domains
are expected to deep-link into the app — a web component on these domains calling the
backend API is an anticipated use case.

**File to fix:** Deployment environment variables (not code)

**How to fix:**
Set the `ALLOWED_ORIGINS` environment variable on the production backend:
```
ALLOWED_ORIGINS=https://spinr.app,https://spinr-track.app,https://spinr-admin.vercel.app
```
Also update `backend/.env.example` to document the expected production value so future
operators know what to set:
```
ALLOWED_ORIGINS=https://spinr.app,https://spinr-track.app,https://spinr-admin.vercel.app
```
No code change required — the middleware already reads `ALLOWED_ORIGINS` from env.

**Effort:** 30 minutes

---

## R-P1-28 · Rider Phone, Email, and Stripe Customer ID Exposed in GET /drivers/rides/active

**Audit finding [12-1 HIGH].** `get_active_ride()` in `backend/routes/drivers.py:1617–1633`
returns `serialize_doc(rider)` where `rider = await db_supabase.get_user_by_id(ride["rider_id"])`.
`serialize_doc` is a no-op passthrough (line 201–202: `return doc`). The full users row — including
`phone`, `email`, `stripe_customer_id`, and `default_payment_method` — is sent to the driver.

This is the inverse of the driver-audit PII finding. The driver only needs the rider's first name,
profile photo, and star rating to complete the trip. Phone/email/payment token disclosure is a PIPEDA
violation.

**Note:** The dispatch WebSocket payload (`rides.py:264–278`) already limits rider data to
`rider_name` and `rider_rating`. The REST endpoint must apply an equivalent filter.
The existing `R-P2-7` checklist item describes this as "verify and strip" — this finding confirms
the leak is real and escalates it to P1 (HIGH).

**File to fix:** `backend/routes/drivers.py` — `get_active_ride()`, line 1630

**How to fix:**
```python
# Define a rider allowlist (only what the driver app needs):
_RIDER_PUBLIC_FIELDS = {"id", "first_name", "profile_image", "rating"}

# In get_active_ride(), replace:
#   "rider": serialize_doc(rider) if rider else None,
# With:
safe_rider = {k: v for k, v in rider.items() if k in _RIDER_PUBLIC_FIELDS} if rider else None
return {
    "ride": serialize_doc(ride),
    "rider": safe_rider,
    "vehicle_type": serialize_doc(vehicle_type) if vehicle_type else None,
}
```

Also audit any other driver-facing endpoints that call `get_user_by_id` and return the result
without field filtering (e.g. ride history, accept/arrive/complete responses).

**Effort:** 1–2 hours

---

## R-P1-29 · Notification Tap from Killed State Discards Payload — No Deep Link to Ride Screen

**Audit finding [13-4 HIGH].** Neither `_layout.tsx` nor `index.tsx` calls
`messaging().getInitialNotification()` (Firebase) or
`Notifications.getLastNotificationResponseAsync()` (Expo Notifications).
When the app is killed and the rider taps a push notification, the app launches
to the splash / home screen with the notification payload silently discarded.
The rider is not routed to the correct ride screen (driver-arrived, ride-in-progress, etc.).

**File to fix:** `rider-app/app/_layout.tsx` or `rider-app/app/index.tsx`

**How to fix:**
```typescript
// In _layout.tsx useEffect (after isAuthInitialized), add:
messaging().getInitialNotification().then(msg => {
  if (!msg?.data) return;
  routeFromNotificationData(msg.data, router);
});
// Also check Expo Notifications path:
Notifications.getLastNotificationResponseAsync().then(response => {
  if (!response) return;
  routeFromNotificationData(response.notification.request.content.data, router);
});

function routeFromNotificationData(data: Record<string, string>, router: Router) {
  const { type, ride_id } = data;
  switch (type) {
    case 'driver_accepted':   router.replace('/driver-arriving'); break;
    case 'driver_arrived':    router.replace('/driver-arrived'); break;
    case 'ride_started':      router.replace('/ride-in-progress'); break;
    case 'ride_completed':    router.replace('/ride-completed'); break;
    default:                  router.replace('/(tabs)'); break;
  }
}
```
Also requires the backend to include `data.type` and `data.ride_id` in FCM
payloads — see R-P1-30.

**Effort:** 3–4 hours (frontend routing + backend payload change)

---

## R-P1-30 · FCM Payloads Missing data Field — Foreground Handler Cannot Route by Type

**Audit finding [13-5 HIGH].** The foreground FCM handler comment in `_layout.tsx:258`
explicitly acknowledges: "Backend notifications currently carry only title/body (no
data field), so we can't route by event type." The foreground handler performs a
generic `fetchRide()` for all notification types regardless of what event occurred.
Consequences: non-ride notifications (promotions, payments) trigger unnecessary ride
refetches; killed-state deep linking (R-P1-29) is impossible without `data.type`
and `data.ride_id`.

**File to fix:** Backend notification sending code + `rider-app/app/_layout.tsx`

**How to fix:**
```python
# Backend — add data dict to every FCM send call, e.g.:
messaging.send(Message(
    notification=Notification(title="Your driver has arrived", body="..."),
    data={"type": "driver_arrived", "ride_id": str(ride_id)},
    token=device_token,
))
```
```typescript
// Frontend — update foreground handler to route by type:
const unsubscribe = onForegroundMessage((msg) => {
  const { type, ride_id } = msg.data ?? {};
  if (RIDE_TYPES.includes(type) && ride_id) {
    useRideStore.getState().fetchRide(ride_id);
  }
  // other types handled separately
});
```

**Effort:** 2 hours (backend payload) + 2 hours (frontend handler update)

---

## R-P1-31 · Star Rating Buttons Missing accessibilityLabel and accessibilityRole

**Audit finding [15-1 HIGH].** The 5 star-rating `TouchableOpacity` buttons in
`ride-completed.tsx:339–347` have no `accessibilityLabel` and no `accessibilityRole`.
VoiceOver announces each as an unlabelled "button". A screen-reader user cannot identify
which star they are focused on, which is currently selected, or what value they are setting.
Rating the driver is a mandatory step — the rider cannot leave the screen without it.

**File to fix:** `rider-app/app/ride-completed.tsx:339–347`

**How to fix:**
```tsx
{[1, 2, 3, 4, 5].map((star) => (
  <TouchableOpacity
    key={star}
    onPress={() => setRating(star)}
    accessibilityLabel={`Rate ${star} star${star > 1 ? 's' : ''}`}
    accessibilityRole="button"
    accessibilityState={{ selected: rating === star }}
    style={styles.starBtn}
  >
    <Ionicons name={star <= rating ? 'star' : 'star-outline'} size={36}
              color={star <= rating ? '#FFB800' : '#DDD'} />
  </TouchableOpacity>
))}
```

**Effort:** 30 minutes

---

## R-P1-32 · SOSButton Missing accessibilityLabel, accessibilityRole, and
           accessibilityHint — VoiceOver Cannot Identify or Trigger It

**Audit finding [15-3 HIGH].** `shared/components/SOSButton.tsx:99–127` — the core
`TouchableOpacity` element carries no accessibility props. VoiceOver announces it as an
unnamed "button". The long-press gesture (1.2 s hold) is entirely undiscoverable. The button
is used in three screens: `driver-arriving.tsx:284`, `ride-in-progress.tsx:204`, and
`ride-in-progress.tsx:443`. In all three positions, the floating SafeAreaView overlay has
`pointerEvents` unset and no `accessible={true}`, meaning VoiceOver may skip the button
entirely when navigating linearly.

**File to fix:** `shared/components/SOSButton.tsx`

**How to fix:**
```tsx
<TouchableOpacity
  accessibilityLabel="Emergency SOS"
  accessibilityRole="button"
  accessibilityHint="Hold for 1.2 seconds to send an emergency alert"
  onPressIn={startPress}
  onPressOut={endPress}
  activeOpacity={0.9}
  style={[styles.btn, ...]}
>
```
Also add `accessible={true}` to the parent SafeAreaView overlay in `driver-arriving.tsx`
and `ride-in-progress.tsx`.

**Effort:** 1 hour

---

## R-P1-33 · Map Overlay Buttons (SOS, Chat, Share) Unreachable by VoiceOver
           Linear Navigation; Missing Labels on Chat and Share Buttons

**Audit finding [15-10 HIGH].** Three interactive map overlay elements in ride screens lack
accessibility attributes and may not be reachable via VoiceOver linear swipe:

- `driver-arriving.tsx` header overlay (position:absolute, zIndex:10): SOS button may be
  skipped when BottomSheet is foregrounded — no `importantForAccessibility` ordering.
- `ride-in-progress.tsx:550–553`: "Message" button (`TouchableOpacity` with Ionicons child)
  has no `accessibilityLabel` or `accessibilityRole`.
- `ride-in-progress.tsx:556–558`: "Share" button similarly has no label.

A VoiceOver user in an active ride cannot reliably reach the emergency SOS or the chat
button. Missing SOS during an emergency is a safety-critical accessibility failure.

**File to fix:**
- `shared/components/SOSButton.tsx` (see R-P1-32)
- `rider-app/app/ride-in-progress.tsx:550–558`
- `rider-app/app/driver-arriving.tsx:269` (headerSafeArea overlay)

**How to fix:**
```tsx
// Message button (ride-in-progress.tsx:550):
<TouchableOpacity
  style={styles.messageButton}
  onPress={handleMessage}
  accessibilityLabel="Message driver"
  accessibilityRole="button"
>

// Share button (ride-in-progress.tsx:556):
<TouchableOpacity
  style={styles.shareButton}
  onPress={handleShareTrip}
  accessibilityLabel="Share trip details"
  accessibilityRole="button"
>

// Header overlay in driver-arriving.tsx (line 269):
<View style={styles.header} accessible={true}
      importantForAccessibility="yes">
```

**Effort:** 1–2 hours

---

## Checklist

- [ ] R-P1-1 Cancellation fee enforced after driver_arrived; Cancel button disabled
- [ ] R-P1-2 Verify useRiderSocket dispatches chat_message events to addChatMessage
- [ ] R-P1-3 Fare split accessible from ride-in-progress screen (mid-ride split)
- [ ] R-P1-4 rate-ride.tsx deleted; _layout.tsx Stack.Screen entry removed
- [ ] R-P1-5 Upcoming scheduled rides visible in activity tab
- [ ] R-P1-6 Data export + account deletion call real API endpoints (PIPEDA)
- [ ] R-P1-7 Idempotency key on ride creation (no double charge)
- [ ] R-P1-8 Promo discount validated against server fare, not client fare
- [ ] R-P1-9 SOS and star rating accessibility labels
- [ ] R-P1-10 i18n library installed; French (fr-CA) translation prepared
- [ ] R-P1-11 become-driver.tsx post-submit routes to /(tabs) + driver app store link
- [ ] R-P1-12 Firebase audience check added to rider dependency (FIREBASE_RIDER_APP_ID)
- [ ] R-P1-13 Firebase-authed users subject to token_version + session_id revocation checks
- [ ] R-P1-14 OTP comparison uses hmac.compare_digest instead of DB equality lookup
- [ ] R-P1-15 Backend OTP phone schema restricted to +1XXXXXXXXXX (Canada/US only)
- [ ] R-P1-16 driver_timeout shows "Searching Again" alert before fetchRide
- [ ] R-P1-17 /rides/{id}/start restricted to driver role only (OTP bypass closed)
- [ ] R-P1-18 cancel_ride guard changed from 'trip_in_progress' to 'in_progress' (dead guard fixed)
- [ ] R-P1-19 /wallet/pay cross-checks debit amount against stored ride fare (no underpay exploit)
- [ ] R-P1-20 add_tip endpoint blocks duplicate tips; tip is one-time-only per ride
- [ ] R-P1-21 createRide() idempotency key confirmed present (see R-P1-7 fix)
- [ ] R-P1-22 Jest coverageThreshold block added (lines ≥ 70, functions ≥ 60)
- [ ] R-P1-23 rideStore.test.ts: hydrateActiveRide, double-booking, cancel-after-driver_arrived tests added
- [ ] R-P1-24 rideStore.ws.test.ts: driver_timeout, ride_cancelled, WS/poll race tests added
- [ ] R-P1-25 walletStore.test.ts: payWithWallet and addTip idempotency tests added
- [ ] R-P1-26 E2E ride-booking spec: rate/tip stages added; screen-specific UI assertions; single-session flow
- [ ] R-P1-27 ALLOWED_ORIGINS env var set to include spinr.app and spinr-track.app in production
- [ ] R-P1-28 Rider phone/email/stripe_customer_id stripped from GET /drivers/rides/active response
- [ ] R-P1-29 Killed-state notification tap routes to correct ride screen (getInitialNotification handler)
- [ ] R-P1-30 FCM payloads include data.type + data.ride_id on all backend sends; foreground handler routes by type
- [ ] R-P1-31 Star rating buttons: accessibilityLabel ("Rate N star/stars") + accessibilityRole="button" + accessibilityState.selected
- [ ] R-P1-32 SOSButton: accessibilityLabel="Emergency SOS", accessibilityRole="button", accessibilityHint for hold gesture; overlay accessible={true}
- [ ] R-P1-33 Chat and Share map overlay buttons labelled; headerSafeArea overlay reachable by VoiceOver
- [ ] R-P1-34 Install react-i18next + i18next + expo-localization; create en-CA and fr-CA locale JSON files; wire t('key') throughout all rider screens (Official Languages Act compliance — see [16-1])
- [ ] R-P1-35 Replace all hardcoded English string literals in JSX and alert payloads with i18n t('key') calls; audit full app beyond login.tsx and ride-completed.tsx (see [16-2])
- [ ] R-P1-36 Refactor backend HTTPException detail strings to machine-readable error codes or Accept-Language-aware localised messages; client must not display raw English API error strings to French-locale users (see [16-5])
