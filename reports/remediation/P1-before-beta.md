# P1 — High Priority: Fix Before Beta Testing

These 10 items must be resolved before sending the app to real beta testers. They include broken ride flows, security risks, and UX issues that would make testing unreliable or unsafe.

**Estimated total effort:** ~3–4 days

---

## P1-1 · Rider Can Cancel a Ride After the Driver Has Already Arrived

**What's wrong:** There is no check to prevent a rider from cancelling a ride once the driver is already at the pickup location or the trip has started. The driver loses the job with no cancellation fee.

**File to fix:** `backend/routes/rides.py` — `cancel_ride_rider` endpoint. Add a state check:
```python
if ride.status in ('driver_arrived', 'trip_in_progress'):
    raise RideStateError("Cannot cancel after driver has arrived")
```

**Effort:** 1 hour

---

## P1-2 · Driver Can Cancel a Ride That Is Already In Progress

**What's wrong:** The driver cancel endpoint has no check to prevent cancellation once the trip has started. A driver could start the meter and then cancel, which skips the payout to them too.

**File to fix:** `backend/routes/rides.py` — `cancel_ride_driver` endpoint. Block cancellation if status is `trip_in_progress`.

**Effort:** 1 hour

---

## P1-3 · A Ride Can Be Marked "Completed" Without Going Through the Pickup Step

**What's wrong:** The "complete ride" endpoint accepts a completion request from any ride state — including before the driver has even started the trip. This means the pickup OTP step can be skipped entirely.

**File to fix:** `backend/routes/rides.py` — `complete_ride` endpoint. Add:
```python
COMPLETE_FROM_STATES = {'trip_in_progress'}
if ride.status not in COMPLETE_FROM_STATES:
    raise RideStateError(f"Cannot complete from state: {ride.status}")
```

**Effort:** 1 hour

---

## P1-4 · Android Back Button Exits the Ride Screen Mid-Trip

**What's wrong:** On Android, pressing the hardware back button while accepting or on a ride dismisses the screen and returns the driver to the home screen. This leaves the app in a broken state (driver thinks they have a job, app shows idle).

**File to fix:** `driver-app/components/panels/RideOfferPanel.tsx`, `ActiveRidePanel.tsx`, `TripCompletedPanel.tsx` — add:
```tsx
import { BackHandler } from 'react-native';
useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => true); // block
    return () => sub.remove();
}, []);
```

**Effort:** 1–2 hours

---

## P1-5 · GPS Tracking Stops When Driver Locks Their Phone

**What's wrong:** The app requests GPS permission when the driver goes online, but only requests "while using the app" (foreground) permission. As soon as the driver locks their phone or switches apps, location tracking stops. The rider sees the driver's position freeze on the map.

**File to fix:** `driver-app/hooks/useDriverDashboard.ts` — after the driver confirms they're online, request background location permission:
```ts
await Location.requestBackgroundPermissionsAsync();
```
This triggers the iOS system prompt: "Allow location access always?"

**Effort:** 2–3 hours (include permission rationale screen)

---

## P1-6 · File Uploads Are Read Entirely Into Memory Before Checking Size

**What's wrong:** When a driver uploads their licence photo, the server reads the entire file into RAM before checking if it's too large. A 100MB file would fill server memory before being rejected.

**File to fix:** `backend/documents.py` — check `Content-Length` header before reading:
```python
content_length = request.headers.get('content-length')
if content_length and int(content_length) > MAX_FILE_SIZE:
    raise FileTooLargeError()
```

**Effort:** 2 hours

---

## P1-7 · Payment Card Field Security Check Misses Common Variations

**What's wrong:** The system blocks raw card data by looking for field names like `card_number` and `cvv`, but misses common alternatives like `cardNumber`, `pan`, `security_code`, and `cvc`. An attacker could bypass the check by using a different field name.

**File to fix:** `backend/routes/payments.py` — expand `_RAW_CARD_FIELDS` set to include:
```python
_RAW_CARD_FIELDS = {
    'card_number', 'cardNumber', 'card_no', 'pan', 'primary_account_number',
    'cvv', 'cvv2', 'cvc', 'cvc2', 'security_code', 'card_security_code',
    'expiry', 'expiration_date', 'exp_month', 'exp_year',
}
```

**Effort:** 30 minutes

---

## P1-8 · OTP Verification Allows Too Many Attempts Per Minute

**What's wrong:** The intended security design allows 3 OTP sends per minute and 5 verification attempts per minute. The actual code is set to 5 sends and 10 verifications — double the allowed budget, giving an attacker more guessing room.

**File to fix:** `backend/routes/auth.py` — update the rate limiter decorators:
- OTP send: `"3/minute"` (currently 5)
- OTP verify: `"5/minute"` (currently 10)

**Effort:** 15 minutes

---

## P1-9 · OTP Comparison Is Vulnerable to Timing Attacks

**What's wrong:** The code compares the OTP the user entered against the stored hash using a normal `==` check. A sophisticated attacker can measure tiny differences in how long the comparison takes and use that to guess the correct code faster.

**File to fix:** `backend/utils/crypto.py` — use constant-time comparison:
```python
import hmac
is_valid = hmac.compare_digest(stored_hash, hashlib.sha256(input_otp.encode()).hexdigest())
```

**Effort:** 30 minutes

---

## P1-10 · OTP Keypad Buttons Are Too Small on iPhone SE

**What's wrong:** The number buttons on the OTP entry screen are too small for reliable tapping on an iPhone SE (4.7-inch screen). Each button needs to be at least 44×44 points per Apple's design guidelines.

**File to fix:** `driver-app/app/` — OTP input screen. Ensure each keypad button has:
```tsx
style={{ minWidth: 64, minHeight: 64 }}
hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
```

**Effort:** 1 hour

---

## Checklist

- [ ] P1-1 Block rider cancellation after driver arrived / trip in progress
- [ ] P1-2 Block driver cancellation once trip is in progress
- [ ] P1-3 Add state guard to complete_ride (only from trip_in_progress)
- [ ] P1-4 Register BackHandler in RideOfferPanel, ActiveRidePanel, TripCompletedPanel
- [ ] P1-5 Request background location after driver goes online
- [ ] P1-6 Check Content-Length before reading file into memory
- [ ] P1-7 Expand PCI card field list with camelCase variants
- [ ] P1-8 Set OTP send=3/min, verify=5/min
- [ ] P1-9 Use hmac.compare_digest for OTP comparison
- [ ] P1-10 Increase OTP keypad button size on small screens
