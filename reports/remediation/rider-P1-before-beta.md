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

## R-P1-2 · Chat Messages Delivered by Polling Only — Can Be Missed

**What's wrong:** `chat-driver.tsx` polls for messages at a fixed interval. Messages
sent while the app is backgrounded or between poll intervals are delayed or missed.
The WebSocket connection at `useRiderSocket` already exists — chat messages should
be delivered through it.

**File to fix:** `rider-app/hooks/useRiderSocket.ts` + `rider-app/app/chat-driver.tsx`

**How to fix:**
Add `chat_message` handling to useRiderSocket (it may already be in the message type
list — verify it actually updates the store):
```typescript
case 'chat_message':
  useRideStore.getState().addChatMessage(data.message);
  break;
```
In chat-driver.tsx: remove the polling interval when WebSocket is connected.

**Effort:** 2–3 hours

---

## R-P1-3 · Fare Split Has No Entry Point from Any Screen

**What's wrong:** `walletStore.ts` has complete fare split actions (`createFareSplit`,
`fetchFareSplitForRide`, etc.) and `fare-split.tsx` screen exists, but there is no
button or navigation path from any existing screen that opens fare split. The feature
is unreachable.

**File to fix:** `rider-app/app/ride-in-progress.tsx` or `rider-app/app/driver-arriving.tsx`
— add a "Split fare" button that navigates to `fare-split.tsx` with the current ride ID.

**Effort:** 2 hours

---

## R-P1-4 · Rate-Ride Flow Not Confirmed Wired to ride-completed.tsx

**What's wrong:** `rate-ride.tsx` exists as a screen but it is unclear whether
`ride-completed.tsx` navigates to it or contains its own inline rating UI. If both
exist independently, ratings could be submitted twice or the flow could deadlock.

**File to fix:** `rider-app/app/ride-completed.tsx` — verify it either:
(a) contains inline rating that calls `rateRide(rideId, rating, comment, tip)` directly, OR
(b) navigates to `rate-ride.tsx` after payment confirmation

Whichever is correct: make the other a dead file and delete it, or consolidate.

**Effort:** 1–2 hours

---

## R-P1-5 · Scheduled Rides Not Listed in Activity Tab

**What's wrong:** `activity.tsx` only shows completed and cancelled ride history.
Upcoming scheduled rides (`fetchScheduledRides()`) are accessible at `scheduled-rides.tsx`
but not surfaced in the activity tab. Users cannot see their upcoming bookings from
the main tab bar.

**File to fix:** `rider-app/app/(tabs)/activity.tsx` — add a "Upcoming" filter tab
alongside "All / Personal / Business" that calls `fetchScheduledRides()`.

**Effort:** 2–3 hours

---

## R-P1-6 · PIPEDA: No Data Export or Account Deletion in App

**What's wrong:** Canadian law (PIPEDA) requires that users can request a copy of their
personal data and request account deletion. Neither option exists in `account.tsx` or
`privacy-settings.tsx`.

**File to fix:** `rider-app/app/(tabs)/account.tsx` or `rider-app/app/privacy-settings.tsx`

**How to fix:**
Add two options:
1. "Export my data" → sends a backend request that emails the user a JSON/CSV of their account data
2. "Delete my account" → confirmation dialog → soft-delete with 30-day grace period

Backend endpoints needed:
- `POST /auth/request-data-export`
- `DELETE /auth/account`

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

## Checklist

- [ ] R-P1-1 Cancellation fee enforced after driver_arrived; Cancel button disabled
- [ ] R-P1-2 Chat messages delivered via WebSocket, not polling
- [ ] R-P1-3 Fare split reachable from ride-in-progress screen
- [ ] R-P1-4 Rate-ride flow consolidated — one path, no duplicate submission
- [ ] R-P1-5 Upcoming scheduled rides visible in activity tab
- [ ] R-P1-6 Data export + account deletion in app (PIPEDA)
- [ ] R-P1-7 Idempotency key on ride creation (no double charge)
- [ ] R-P1-8 Promo discount validated against server fare, not client fare
- [ ] R-P1-9 SOS and star rating accessibility labels
- [ ] R-P1-10 i18n library installed; French (fr-CA) translation prepared
