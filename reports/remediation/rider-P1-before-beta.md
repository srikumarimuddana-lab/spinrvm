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
