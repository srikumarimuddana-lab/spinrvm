# P2 — Rider App Medium Priority: Fix Before Public Launch

These items must be resolved before the app goes live to the general public.
They cover reliability, UX polish, compliance, and features that real users will
hit under normal usage.

**Estimated total effort:** ~5–7 days

---

## R-P2-1 · Offline Queue Only Handles Ride Creation

**What's wrong:** `syncOfflineRequests()` in `rideStore.ts` only replays `create_ride`
requests queued while offline. If a rider cancels a ride, submits a rating, or pays a
tip while offline, those actions are silently dropped — no retry, no user feedback.

**File to fix:** `rider-app/store/rideStore.ts` — `syncOfflineRequests`

**How to fix:**
Extend the offline queue type to include: `cancel_ride`, `rate_ride`, `tip`, `emergency`
Each queue entry should have: `{ type, rideId, payload, retries, timestamp }`
Replay each on reconnect; clear after 3 failed retries with a user notification.

**Effort:** 3–4 hours

---

## R-P2-2 · Cold-Start Stale Ride Not Validated Before Routing

**What's wrong:** `hydrateActiveRide()` restores a ride from AsyncStorage on cold start
and immediately routes to a ride screen. If the ride completed or was cancelled while
the app was closed, the rider sees a ghost active-ride UI.

**Audit finding [10-5 MEDIUM] — confirmed with additional detail:** `fetchActiveRide()`
returns `null` when `/rides/active` returns 404, but it does NOT call `clearRide()` on a
null result. This means the state populated by `hydrateActiveRide()` is never cleared on a
404 — the stale ride persists in the store indefinitely until a screen-level status
transition occurs. The fix must also include clearing hydrated state when fetchActiveRide
returns null.

**File to fix:** `rider-app/store/rideStore.ts` — `hydrateActiveRide` and `fetchActiveRide`

**How to fix:**
```typescript
hydrateActiveRide: async () => {
  const stored = await AsyncStorage.getItem('@spinr:active_ride');
  if (!stored) return;
  const ride = JSON.parse(stored);
  // Validate against backend before routing:
  try {
    const live = await api.get('/rides/active');
    if (live.data?.id !== ride.id) {
      await AsyncStorage.removeItem('@spinr:active_ride');
      return; // stale — do not route
    }
    set({ currentRide: live.data });
  } catch {
    await AsyncStorage.removeItem('@spinr:active_ride');
  }
}
```

**Effort:** 2 hours

---

## R-P2-3 · Ride Status Magic Strings — No Central Constants

**What's wrong:** Ride status values (`'searching'`, `'driver_assigned'`, `'driver_arrived'`,
`'in_progress'`, `'completed'`, `'cancelled'`) are scattered as literal strings across
36 screens and the store. One typo causes a silent bug. The driver app had the same issue.

**File to fix:** Create `rider-app/constants/rideStatus.ts`

**How to fix:**
```typescript
// rider-app/constants/rideStatus.ts
export const RideStatus = {
  SEARCHING: 'searching',
  DRIVER_ASSIGNED: 'driver_assigned',
  DRIVER_ACCEPTED: 'driver_accepted',
  DRIVER_ARRIVED: 'driver_arrived',
  IN_PROGRESS: 'in_progress',
  COMPLETED: 'completed',
  CANCELLED: 'cancelled',
} as const;
export type RideStatusType = typeof RideStatus[keyof typeof RideStatus];
```
Replace all magic strings across `store/rideStore.ts`, `hooks/useRiderSocket.ts`,
and all ride screens with `RideStatus.X` references.

**Effort:** 3–4 hours

---

## R-P2-4 · TypeScript `any` Types — Store and API Responses

**What's wrong:** The stores and many screens use `: any` for API responses, component
props, and WebSocket message payloads. This disables TypeScript's protection against
field name typos and API contract changes.

**File to fix:** `rider-app/store/rideStore.ts`, `rider-app/hooks/useRiderSocket.ts`,
`rider-app/app/*.tsx` (any props typed as `any`)

**How to fix:** Use types from `shared/types/` for API responses. Add Zod schemas
for WebSocket message parsing:
```typescript
import { z } from 'zod';
const DriverLocationSchema = z.object({
  lat: z.number(), lng: z.number(),
  speed: z.number().optional(), heading: z.number().optional()
});
// Parse WS data:
const parsed = DriverLocationSchema.safeParse(data);
if (!parsed.success) return; // ignore malformed message
```

**Effort:** 4–6 hours

---

## R-P2-5 · Polling Not Suspended When WebSocket Is Connected

**What's wrong:** `driver-arriving.tsx` polls `/rides/{id}` every 3 seconds as a
fallback for when WebSocket is unavailable. But polling continues even when the
WebSocket is connected and delivering updates, wasting battery and bandwidth.

**File to fix:** `rider-app/app/driver-arriving.tsx` and `rider-app/app/ride-in-progress.tsx`

**How to fix:**
Expose a `wsConnected` state from `useRiderSocket`. Pause polling when `wsConnected === true`:
```typescript
const { wsConnected } = useRiderSocket();
useEffect(() => {
  if (wsConnected) return; // WS is healthy — skip poll
  const interval = setInterval(fetchRide, 3000);
  return () => clearInterval(interval);
}, [wsConnected]);
```

**Effort:** 2 hours

---

## R-P2-6 · Error States Missing on Key Screens

**What's wrong:** Several screens show a blank view or infinite spinner when an API
call fails. Users have no way to recover.

**Screens to fix:**
- `ride-options.tsx` — `fetchEstimates()` failure → show "Could not load fares. Tap to retry."
- `driver-arriving.tsx` — poll failure → show warning banner, do not blank the screen
- `(tabs)/activity.tsx` — history load failure → show "Could not load rides. Pull to refresh."
- `wallet.tsx` — balance load failure → show "Balance unavailable" not blank

**Effort:** 3–4 hours (all four screens)

---

## R-P2-7 · Rider PII in Driver-Facing API Responses — Verify and Strip

**What's wrong:** Based on the driver audit, driver-facing ride responses may include
rider fields that should be absent: phone number, email, home/work saved addresses,
payment method details.

**File to fix:** `backend/routes/rides.py` + `backend/routes/drivers.py`

**How to fix:** Add a `RiderPublicView` Pydantic model that whitelists only:
first name, profile photo URL, rating, and pickup/dropoff for the current ride.
Apply it to all driver-facing ride fetch responses.

**Effort:** 2–3 hours

---

## R-P2-8 · become-driver.tsx — Incomplete Handoff to Driver App

**What's wrong:** `become-driver.tsx` exists but the path to download or open the
driver app is unclear. A rider trying to become a driver may hit a dead end.

**File to fix:** `rider-app/app/become-driver.tsx`

**How to fix:**
- Show driver app store links (App Store + Play Store)
- If driver app is installed, use deep link `spinr-driver://onboard?phone={phone}`
- If not installed, redirect to store

**Effort:** 2 hours

---

## R-P2-9 · Touch Targets Below 44pt on Rating and Tip Buttons

**What's wrong:** The star rating buttons and tip preset buttons in `ride-completed.tsx`
are likely below the iOS Human Interface Guideline minimum of 44×44pt.

**File to fix:** `rider-app/app/ride-completed.tsx`

**How to fix:**
```tsx
// Ensure each star button is at minimum 44pt:
<TouchableOpacity
  style={{ width: 44, height: 44, alignItems: 'center', justifyContent: 'center' }}
  hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
  ...
>
```

**Effort:** 1 hour

---

## R-P2-10 · Corporate Ride Flow Has No UI Entry Point

**Audit finding [01-5 MEDIUM] — confirmed.** `rideStore.createRide()` does not include
`corporate_account_id` in its payload (rideStore.ts:319–352). `activity.tsx` has a
"Business" filter (line 151) implying corporate rides are expected, but there is no
booking path that sets this field.

**File to fix:** `rider-app/app/payment-confirm.tsx` + `rider-app/store/rideStore.ts`

**How to fix:** Add a "Bill to [Company Name]" toggle when rider has a corporate
account on their profile. Pass `corporate_account_id` in the ride creation payload.

**Effort:** 3–4 hours (frontend + backend integration)

---

## R-P2-11 · Legal/ToS Not Presented During Onboarding

**Audit finding [01-7 RECOMMENDATION].** `legal.tsx` exists and is accessible from
Account → Legal. New riders completing sign-up are never asked to accept the Terms of
Service. App Store Review Guideline 4.0 requires ToS acceptance before a user commits
to the service. PIPEDA also requires informed, explicit consent for data processing.

**File to fix:** `rider-app/app/profile-setup.tsx`

**How to fix:**
Add a "By continuing you agree to our Terms of Service and Privacy Policy" row
with a tap-to-view link before the submit button. Log acceptance with a timestamp
server-side.

**Effort:** 2 hours

---

## R-P2-12 · become-driver.tsx — Incomplete Handoff Description (Pre-P1-11)

**Note:** The routing crash was elevated to P1-11. This item tracks the UX polish
follow-up: deep-link to the Spinr Driver app if already installed on device.

**File to fix:** `rider-app/app/become-driver.tsx` (after P1-11 is resolved)

**How to fix:** Use `Linking.canOpenURL('spinr-driver://')` to check if driver app is
installed, then deep-link vs App Store.

**Effort:** 1 hour

---

## R-P2-13 · Access Token Persisted to SecureStore — Memory-Only Pattern Not Followed

**Audit finding [02-5 LOW].** `setTokens()` in `shared/store/authStore.ts:154` writes the
access token to `expo-secure-store` in addition to the in-memory `_inMemoryToken`. The standard
pattern is memory-only for access tokens (wiped on restart) with only the refresh token
persisted. SecureStore is hardware-backed (iOS Keychain / Android Keystore) so the risk is low,
but the access token survives app restarts, extending the effective session window beyond the
15-minute JWT TTL.

**File to fix:** `shared/store/authStore.ts` — `setTokens()`

**How to fix:**
Remove `storage.setItem('auth_token', token)` from `setTokens()`. In `initialize()`, if no
in-memory token is present, call `refreshTokens()` to obtain a new access token from the
persisted refresh token rather than reading the access token from disk.

**Effort:** 1 hour

---

## R-P2-14 · EAS Test/Preview Builds Point to Production Backend

**Audit finding [03-2 MEDIUM].** `rider-app/eas.json` test and preview build profiles
hardcode `"https://spinr-backend-production.up.railway.app"` as `EXPO_PUBLIC_BACKEND_URL`.
Internal testers running these builds read and write to the production database.

**File to fix:** `rider-app/eas.json:23, 32`

**How to fix:**
```json
"test": {
  "env": { "EXPO_PUBLIC_BACKEND_URL": "$SPINR_STAGING_BACKEND_URL" }
},
"preview": {
  "env": { "EXPO_PUBLIC_BACKEND_URL": "$SPINR_STAGING_BACKEND_URL" }
}
```
Set `SPINR_STAGING_BACKEND_URL` as an EAS secret once a staging environment exists.

**Effort:** 2 hours (staging env setup)

---

## R-P2-15 · TruffleHog CI Scans Only Last Commit with --only-verified

**Audit finding [03-3 MEDIUM].** The `security-scan` CI job uses
`trufflehog --only-verified --since-commit HEAD~1`. Only one commit is scanned per run,
and unverifiable secrets (rotated/expired keys) pass silently.

**File to fix:** `.github/workflows/ci.yml:428–429`

**How to fix:**
```yaml
- name: Secrets scan (all)
  run: |
    trufflehog git file://. \
      --since-commit origin/${{ github.base_ref }} \
      --fail
```
Remove `--only-verified` to catch unverified patterns too.

**Effort:** 30 minutes

---

## R-P2-16 · Rider-App CI Missing EXPO_PUBLIC_ Scan; play-service-account.json Not in .gitignore

**Audit finding [03-4 LOW + 03-5 LOW].** The rider-app CI job has no check for private
variables accidentally placed in `EXPO_PUBLIC_` namespace. The `rider-app/.gitignore` does not
exclude `play-service-account.json` referenced in `eas.json`.

**Files to fix:** `.github/workflows/ci.yml` (rider-app-test job); `rider-app/.gitignore`

**How to fix:**
1. Add to `rider-app/.gitignore`:
   ```
   play-service-account.json
   ```
2. Add a step to `rider-app-test` CI job:
   ```yaml
   - name: Check for private vars in EXPO_PUBLIC_
     run: |
       if grep -r "EXPO_PUBLIC_.*(SECRET|PRIVATE|SERVICE_ACCOUNT)" \
         rider-app/.env* rider-app/app.config.ts 2>/dev/null; then
         echo "ERROR: Private credential in EXPO_PUBLIC_ variable"; exit 1
       fi
   ```

**Effort:** 30 minutes

---

## R-P2-17 · Tip Endpoints Use Raw request.json() — NaN Bypasses Guards

**Audit finding [04-2 MEDIUM].** `add_tip` (rides.py:946) and `process_payment`
(rides.py:973) both read tip amount via `float(data.get(..., 0))` outside Pydantic.
In Python `float("nan") <= 0` and `float("nan") > 500` both evaluate to False, so
NaN bypasses every guard and reaches payment/database logic.

**File to fix:** `backend/routes/rides.py:946, 973`

**How to fix:**
```python
class TipRequest(BaseModel):
    amount: Decimal = Field(..., gt=0, le=500)

# In add_tip:
req = TipRequest(**await request.json())
tip_amount = req.amount
```

**Effort:** 1 hour

---

## R-P2-18 · stops Array — No Maximum Count or Coordinate Validation

**Audit finding [04-3 MEDIUM].** `CreateRideRequest.stops` (backend/schemas.py:273)
is `Optional[List[Dict[str, Any]]]` with no `max_length` and no lat/lng validators
on stop entries. An attacker can submit an arbitrarily long stops list or stops
with out-of-range coordinates (lat=999) that pass silently.

**File to fix:** `backend/schemas.py:273` (CreateRideRequest)

**How to fix:**
```python
stops: Optional[List[Dict[str, Any]]] = Field(default=[], max_length=5)

@validator('stops')
def validate_stops(cls, stops):
    for stop in stops:
        lat, lng = stop.get('lat'), stop.get('lng')
        if lat is None or lng is None:
            raise ValueError('Each stop must have lat and lng')
        if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
            raise ValueError(f'Stop coordinates out of range: {lat}, {lng}')
    return stops
```

**Effort:** 1 hour

---

## R-P2-19 · scheduled_time Accepts Past Timestamps

**Audit finding [04-4 MEDIUM].** `CreateRideRequest.scheduled_time` (schemas.py:275)
has no validator to reject past datetimes. A scheduled ride submitted with a
timestamp in the past (or epoch date) is accepted, causing undefined dispatch
behaviour.

**File to fix:** `backend/schemas.py:275` (CreateRideRequest)

**How to fix:**
```python
from datetime import datetime, timedelta

@validator('scheduled_time')
def validate_scheduled_time(cls, v):
    if v is not None and v < datetime.utcnow() + timedelta(minutes=5):
        raise ValueError('Scheduled time must be at least 5 minutes in the future')
    return v
```

**Effort:** 30 minutes

---

## R-P2-20 · Fare Split Phone Strings Have No Format Validation

**Audit finding [04-5 MEDIUM].** `CreateFareSplitRequest` enforces a max of 5
participants (PASS) but individual phone strings are unconstrained — any string
passes. The client-side filter (`p.trim().length >= 10`) is insufficient.

**File to fix:** `backend/routes/fare_split.py` (CreateFareSplitRequest)

**How to fix:**
```python
from pydantic import validator
import re

@validator('participant_phones', each_item=True)
def validate_phone(cls, v):
    if not re.match(r'^\+1\d{10}$', v):
        raise ValueError(f'Invalid phone number: {v}')
    return v
```

**Effort:** 30 minutes

---

## R-P2-21 · SavedAddressCreate — No Length Limit or Sanitization

**Audit finding [04-6 MEDIUM].** `SavedAddressCreate.name` and `.address`
(schemas.py:150–155) have no max_length. The existing `sanitize_string()` in
validators.py is not called by the create_saved_address handler in addresses.py.

**File to fix:** `backend/schemas.py:150–155` + `backend/routes/addresses.py`

**How to fix:**
```python
class SavedAddressCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    address: str = Field(..., min_length=5, max_length=300)
    lat: float
    lng: float
    icon: str = "location"
```
And in addresses.py handler: apply `sanitize_string()` to name and address
before creating the record.

**Effort:** 1 hour

---

## R-P2-22 · WalletPayRequest Has No Maximum Cap

**Audit finding [04-7 MEDIUM].** `WalletPayRequest.amount: float = Field(..., gt=0)`
has no upper bound (contrast: TopUpRequest correctly has `le=500`).

**File to fix:** `backend/routes/wallet.py:89–91`

**How to fix:** Add `le=500` or a configurable ceiling consistent with the
top-up limit.

**Effort:** 30 minutes

---

## R-P2-23 · Wallet Request Models Use float Instead of Decimal

**Audit finding [04-8 MEDIUM].** `TopUpRequest.amount` and `WalletPayRequest.amount`
are typed as `float`. While the handler wraps with `_d()` (Decimal rounding),
IEEE 754 representation errors occur before that conversion.

**File to fix:** `backend/routes/wallet.py:86, 91`

**How to fix:**
```python
from decimal import Decimal
class TopUpRequest(BaseModel):
    amount: Decimal = Field(..., gt=0, le=500)

class WalletPayRequest(BaseModel):
    amount: Decimal = Field(..., gt=0, le=500)
```

**Effort:** 30 minutes

---

## R-P2-24 · search-destination.tsx — No KeyboardAvoidingView

**Audit finding [05-2 MEDIUM].** The "Search Ride" primary action button
(search-destination.tsx:615) sits below the FlatList and is hidden behind the
keyboard when any text input is focused. No KeyboardAvoidingView wraps the screen.
Users who type both addresses manually without selecting autocomplete predictions
cannot see or tap the button without manually dismissing the keyboard.

**File to fix:** `rider-app/app/search-destination.tsx`

**How to fix:**
```tsx
import { KeyboardAvoidingView, Platform } from 'react-native';

return (
  <SafeAreaView style={styles.container} edges={['top']}>
    <KeyboardAvoidingView
      style={{ flex: 1 }}
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
    >
      {/* existing content */}
    </KeyboardAvoidingView>
  </SafeAreaView>
);
```

**Effort:** 30 minutes

---

## R-P2-25 · allowFontScaling Not Set — Layout Breaks at Large System Font Sizes

**Audit finding [05-8 MEDIUM].** Zero usages of `allowFontScaling` found across
all 36 rider-app screens and shared components. Fixed-height containers (status
pills, bottom sheet rows, map overlay buttons, driver card) will overflow or clip
text when system font size is set to 200%+ (iOS Accessibility → Larger Text).
Safety-critical labels (ETA countdown, driver name, SOS) are directly affected.

**File to fix:** All screens with fixed-height Text containers — priority screens:
`driver-arriving.tsx`, `ride-in-progress.tsx`, `driver-arrived.tsx`

**How to fix:** Add `allowFontScaling={false}` to Text inside fixed-height
containers (buttons, pills, badges). Allow scaling on free-flow content.

**Effort:** 2–3 hours (audit all fixed-height containers across active ride screens)

---

## R-P2-26 · ride-in-progress.tsx — No Error State for Ride Load Failure

**Audit finding [05-6 MEDIUM].** When `currentRide` is null (fetchRide failed),
the screen shows "Loading Map..." indefinitely with no error recovery path.
Compare: driver-arriving.tsx has a full error state with retry (lines 295–302).

**File to fix:** `rider-app/app/ride-in-progress.tsx`

**How to fix:** Add `isLoading` / `error` state tracking to the mapContainer
conditional; when error, show alert icon + "Could not load ride" + retry button.

**Effort:** 1 hour

---

## R-P2-27 · driver-arrived.tsx — Blank Screen When Ride Data Unavailable

**Audit finding [05-7 MEDIUM].** `{currentRide ? (<MapView ...>) : null}` at
driver-arrived.tsx:126 renders a blank white area when currentRide is null.
The screen shows the bottom sheet shell but no map content, no error, no retry.

**File to fix:** `rider-app/app/driver-arrived.tsx`

**How to fix:** Replace `null` with an error/loading view + retry button.

**Effort:** 30 minutes

---

## R-P2-28 · Home Screen Zoom Buttons Below 44pt Touch Target

**Audit finding [05-9 MEDIUM].** `mapControlButton: { width: 40, height: 40 }` in
index.tsx. Both zoom-in and zoom-out buttons are 40×40pt with no `hitSlop`,
4pt below the iOS HIG 44pt minimum. Difficult to tap reliably in cold weather.

**File to fix:** `rider-app/app/(tabs)/index.tsx` — mapControlButton style

**How to fix:**
```tsx
mapControlButton: { width: 44, height: 44, justifyContent: 'center', alignItems: 'center' }
```

**Effort:** 15 minutes

---

## R-P2-29 · Poll Result Overwrites More-Recent WS Driver Position (Race Condition)

**Audit finding [06-3 MEDIUM].** `fetchRide()` in `rideStore.ts:364` unconditionally
overwrites `currentDriver` with the REST response, which contains GPS coordinates as of
the last backend persist cycle. When a poll response arrives after a `driver_location_update`
WS push, it sets stale coordinates — the map marker visibly jumps backwards.

At 50 km/h city speeds with the 3 s poll in driver-arriving.tsx, the marker can jump
back ~42 m on each poll cycle, making the driver's position appear erratic.

**File to fix:** `rider-app/store/rideStore.ts` — `fetchRide` action (line 364)

**How to fix:**
```typescript
// In fetchRide(), preserve WS-updated coordinates:
fetchRide: async (rideId) => {
  const response = await api.get(`/rides/${rideId}`);
  const ride = response.data;
  const freshDriver = ride.driver || null;
  const currentDriver = get().currentDriver;
  // If WS has already updated lat/lng, keep the WS values (they are always fresher)
  const mergedDriver = freshDriver && currentDriver
    ? { ...freshDriver, lat: currentDriver.lat, lng: currentDriver.lng }
    : freshDriver;
  set({ currentRide: ride, currentDriver: mergedDriver, isLoading: false });
  _persistRide(ride, mergedDriver);
},
```

**Effort:** 1 hour

---

## R-P2-30 · No Re-Permission Flow When Rider Denies Location Access

**Audit finding [06-6 MEDIUM].** When `Location.requestForegroundPermissionsAsync()`
returns `denied`, `index.tsx` silently returns (line 83). The map shows "Locating..."
indefinitely with no explanation, no alert, and no deep-link to Settings. The rider has
no feedback that location was denied and no way to re-enable it from within the app.

**File to fix:** `rider-app/app/(tabs)/index.tsx` (location permission effect, lines 82–83)

**How to fix:**
```typescript
let { status } = await Location.requestForegroundPermissionsAsync();
if (status !== 'granted') {
  Alert.alert(
    'Location Required',
    'Spinr needs your location to show nearby drivers and confirm your pickup. Please enable location in Settings.',
    [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Open Settings', onPress: () => Linking.openSettings() },
    ]
  );
  return;
}
```

**Effort:** 30 minutes

---

## R-P2-31 · FreeCancelTimer Shows Expired but Does Not Disable Cancel Button

**Audit finding [07-5 MEDIUM].** `FreeCancelTimer` in
`rider-app/components/FreeCancelTimer.tsx` is a display-only component: when the
free-cancel window expires it changes the label to "Cancellation fee: $X" but emits
no callback. The Cancel button in `driver-arriving.tsx` remains enabled and shows
no visual change when the window expires while the ride is in `driver_accepted`
state. A rider who misses the passive label change may tap Cancel believing it is
still free.

**Files to fix:**
- `rider-app/components/FreeCancelTimer.tsx` — add `onExpire` prop
- `rider-app/app/driver-arriving.tsx` — fire fee warning on expiry + update handleBack

**How to fix:**
```typescript
// FreeCancelTimer.tsx — add prop and call it:
interface FreeCancelTimerProps {
  onExpire?: () => void;
  // ... existing props
}
// In the setInterval callback when remaining === 0:
if (remaining === 0) {
  clearInterval(interval);
  onExpire?.();
}

// driver-arriving.tsx — wire the callback:
<FreeCancelTimer
  driverAcceptedAt={(currentRide as any)?.driver_accepted_at}
  freeCancelWindowSeconds={freeCancelWindowSeconds}
  cancellationFee={cancellationFee}
  onExpire={() =>
    // Show a brief toast or banner: "Free cancel window expired — fee now applies"
    setAlertState({ visible: true, title: 'Free Cancel Expired',
      message: `A $${cancellationFee.toFixed(2)} fee now applies if you cancel.`,
      variant: 'warning', buttons: [{ text: 'OK' }] })
  }
/>
```

Also update `handleBack()`: when `status === 'driver_accepted'` AND
`(currentRide as any).free_cancel_seconds_remaining === 0`, show the fee-warning
dialog rather than the "free cancel" dialog.

**Effort:** 1–2 hours

---

## R-P2-32 · Book Button Disabled State Not Tied to isSubmitting Ref

**Audit finding [08-4 MEDIUM].** `payment-confirm.tsx` uses an `isSubmitting` ref (line 51)
to prevent double-taps, but the Book button's `disabled` prop is tied to `isLoading` (the
Zustand store state, line 322) — not to `isSubmitting.current`. Between the tap and the
first async suspension point in `createRide()`, both guards are false simultaneously. The
button also shows no visual disabled state (no spinner, no greyed-out appearance) during
the brief window before `isLoading` is set, giving the user no feedback that the request
is in-flight.

**File to fix:** `rider-app/app/payment-confirm.tsx`

**How to fix:**
```typescript
const [isSubmitting, setIsSubmitting] = useState(false);
const handleBookRide = async () => {
  if (isSubmitting) return;
  setIsSubmitting(true);
  try {
    // ... existing logic
  } finally {
    setIsSubmitting(false);
  }
};
// Button:
<TouchableOpacity disabled={isSubmitting || isLoading} ...>
  {isSubmitting || isLoading ? <ActivityIndicator color="#FFF" /> : <Text>Book</Text>}
</TouchableOpacity>
```

**Effort:** 30 minutes

---

## R-P2-33 · Promo Discount Uses Client-Supplied Fare — Minimum Fare Eligibility Bypassed

**Audit finding [08-5 MEDIUM].** `POST /promo/validate` accepts `ride_fare` from the client
(promotions.py:31). The backend uses this value to check minimum-fare eligibility
(`if req.ride_fare < min_fare: raise 400`) and to calculate percentage discounts. A
client can supply any `ride_fare` value — inflating it passes the minimum-fare check for
promos that require a minimum spend. The discount calculation is also skewed by the
fake fare value.

**File to fix:** `backend/routes/promotions.py` — `validate_promo`

**How to fix:**
Change `ValidatePromoRequest` to accept a `ride_id` instead of `ride_fare`:
```python
class ValidatePromoRequest(BaseModel):
    code: str
    ride_id: Optional[str] = None  # fetch fare server-side; ride must belong to caller
```
Inside `validate_promo`, fetch `total_fare` from the ride record when `ride_id` is provided.
Also wire `createRide()` to pass the applied `promo_id` so the discount is applied during
ride creation, not just displayed on the client.

**Effort:** 2–3 hours

---

## R-P2-34 · Fare-Split Payment Has TOCTOU Race — Participant Can Double-Pay

**Audit finding [08-6 MEDIUM].** `pay_split_share` in `backend/routes/fare_split.py:257`
reads the participant status and wallet balance in separate operations with no atomic lock.
Two concurrent requests from the same participant both pass the `status == "accepted"` check
simultaneously, then both deduct from the wallet. The result: one share amount is
effectively deducted twice (wallet drained by 2× share) while the participant is only
credited once.

**File to fix:** `backend/routes/fare_split.py` — `pay_split_share` handler

**How to fix:**
```python
# Atomic claim: only succeeds if status is still "accepted"
guard = await db.update_one(
    "fare_split_participants",
    {"id": participant_id, "status": "accepted"},
    {"$set": {"status": "processing"}},
)
if not getattr(guard, "modified_count", 1) == 0:
    return {"status": "paid", "already_paid": True}  # already paid or processing
```

**Effort:** 1 hour

---

## R-P2-35 · Store Unit Tests Missing for Fare Split, Scheduled Rides, Promo, Offline Queue

**Audit finding [09-5 MEDIUM].** Four store subsystems have zero unit-test coverage:
scheduled rides (`fetchScheduledRides`, `cancelScheduledRide`), promo validation
(`applyPromo`, `fetchAvailablePromos`), offline queue replay (`syncOfflineRequests`),
and fare-split participant payment. The D09 framework requires all state transitions
and error paths to be tested.

**Files to fix:**
- `rider-app/store/__tests__/rideStore.test.ts` — add scheduled rides, promo, offline queue tests
- `rider-app/store/__tests__/walletStore.test.ts` — add fare-split payment confirmation test

**How to fix:** See finding [09-5] in the audit for specific test case descriptions.

**Effort:** 3–4 hours

---

## R-P2-36 · No Component-Level Tests — useRiderSocket and Critical UI Panels Untested

**Audit finding [09-9 MEDIUM].** Only three test files exist, all in `store/__tests__/`.
No component or hook tests exist for `useRiderSocket`, `RideOfferPanel`,
`ActiveRidePanel`, or `TripCompletedPanel`. A regression in WebSocket event dispatching
would be invisible until a manual E2E run or production failure.

**Files to fix:** Create new test files in `rider-app/hooks/__tests__/` and
`rider-app/components/__tests__/`

**How to fix:**
1. Add `rider-app/hooks/__tests__/useRiderSocket.test.ts` using a mock WebSocket class:
   verify `driver_location_update`, `driver_timeout`, and `ride_cancelled` events
   each dispatch the correct store action.
2. Add component tests for the three ride-panel components using
   `@testing-library/react-native` — verify key text and interactive elements render.

**Effort:** 1–2 days

---

## R-P2-37 · ride-options.tsx Shows Blank Screen on fetchEstimates() Failure — No Retry UI

**Audit finding [10-1 MEDIUM].** When `fetchEstimates()` fails, the store sets
`isLoading=false` and `error=error.message` but `ride-options.tsx` never reads
the `error` field. The screen renders an empty ScrollView with no vehicle cards,
no error message, and no retry button. The "Confirm" footer is hidden because
`estimates.length === 0`. The rider sees a blank options area with no explanation
and no recovery path except pressing back and restarting the flow.

**File to fix:** `rider-app/app/ride-options.tsx` (vehicle options render, lines 406–495)

**How to fix:**
```tsx
// Add error + clearError to the store destructure:
const { ..., error, clearError } = useRideStore();

// Clear error on mount:
useEffect(() => { clearError(); }, []);

// In the vehicle options render area, after the isLoading check:
{!isLoading && estimates.length === 0 && error && (
  <View style={styles.errorContainer}>
    <Ionicons name="cloud-offline-outline" size={40} color={colors.textDim} />
    <Text style={styles.errorText}>Could not load ride options</Text>
    <Text style={styles.errorSubtext}>{error}</Text>
    <TouchableOpacity style={styles.retryButton} onPress={() => fetchEstimates()}>
      <Text style={styles.retryText}>Retry</Text>
    </TouchableOpacity>
  </View>
)}
```

**Effort:** 1 hour

---

## R-P2-38 · ErrorBoundary at Root Only — driver-arriving, ride-in-progress, ride-completed Unprotected

**Audit finding [10-7 MEDIUM].** `ErrorBoundary` is applied only at the root layout
level (`_layout.tsx:329`). No individual active-ride screen wraps its content in
`ErrorBoundary`. A JavaScript render error inside `driver-arriving.tsx` or
`ride-in-progress.tsx` (e.g. null driver coordinates causing a map crash) triggers
the root boundary, which replaces the entire navigation stack with "Something went
wrong" — the rider loses all navigation context mid-trip.

The root boundary's "Try Again" resets boundary state but re-mounts the full app,
which may lose in-progress UI state (animation refs, bottom sheet position, etc.).

**Files to fix:**
- `rider-app/app/driver-arriving.tsx` — wrap screen body in `<ErrorBoundary>`
- `rider-app/app/ride-in-progress.tsx` — wrap screen body in `<ErrorBoundary>`
- `rider-app/app/ride-completed.tsx` — wrap screen body in `<ErrorBoundary>`
- `rider-app/app/driver-arrived.tsx` — wrap screen body in `<ErrorBoundary>`

**How to fix:**
```tsx
import { ErrorBoundary } from '@shared/components/ErrorBoundary';

export default function DriverArrivingScreen() {
  return (
    <ErrorBoundary
      fallback={
        <RideScreenErrorFallback
          title="Map Error"
          message="The map failed to load. Your ride is still active."
          onGoHome={() => { clearRide(); router.replace('/(tabs)'); }}
        />
      }
    >
      {/* existing screen content */}
    </ErrorBoundary>
  );
}
```
Create a shared `RideScreenErrorFallback` component that always includes a
"Go Home" button that calls `clearRide()` + `router.replace('/(tabs)')`.

**Effort:** 2 hours

---

## R-P2-39 · /rides/{id}/cancel Missing Rate-Limit Decorator

**Audit finding [11-2 MEDIUM].** `POST /rides/{ride_id}/cancel` in `backend/routes/rides.py:1314`
has no SlowAPI rate-limit decorator. The create_ride endpoint (line 515) correctly applies
`@ride_request_limit`; the cancel endpoint does not. An authenticated client can hammer the
cancel endpoint without throttling, causing repeated DB reads, state-machine evaluations,
and potentially repeated Stripe cancellation-fee charge attempts.

**File to fix:** `backend/routes/rides.py` — `cancel_ride_rider` endpoint

**How to fix:**
```python
from utils.rate_limiter import ride_request_limit

@api_router.post("/{ride_id}/cancel")
@ride_request_limit
async def cancel_ride_rider(request: Request, ride_id: str, current_user: dict = Depends(get_current_user)):
    # Note: SlowAPI requires first param named `request` of type starlette.requests.Request
    ...
```

**Effort:** 30 minutes

---

## R-P2-40 · /promo/validate Not Rate-Limited — Promo Code Enumeration Possible

**Audit finding [11-3 MEDIUM].** `POST /promo/validate` in `backend/routes/promotions.py:60`
has no rate-limit decorator. The endpoint returns distinct error messages for each failure
case (404 for unknown code; 400 for expired, usage-exceeded, user-ineligible). An attacker
can submit sequential guesses to identify valid promo codes — a 404 confirms "code doesn't
exist"; a 400 confirms "code exists but not applicable." Short promo codes (e.g. "FALL10")
are enumerable in minutes at unlimited request rates.

**File to fix:** `backend/routes/promotions.py` — `validate_promo` endpoint

**How to fix:**
```python
from utils.rate_limiter import default_limiter
from fastapi import Request

promo_validate_limit = default_limiter.limit("10/minute")

@api_router.post("/validate")
@promo_validate_limit
async def validate_promo(request: Request, req: ValidatePromoRequest, current_user: dict = Depends(get_current_user)):
    ...
```
Consider also returning a generic "Promo code not found or not applicable" message for
both 404 and eligibility-failure cases to eliminate the timing/status oracle.

**Effort:** 30 minutes

---

## R-P2-41 · Referrer-Policy Value Should Be "no-referrer" per Checklist

**Audit finding [11-4 MEDIUM].** `_BASE_SECURITY_HEADERS` in
`backend/core/middleware.py:99` sets `Referrer-Policy: strict-origin-when-cross-origin`
instead of the checklist-required `no-referrer`. For a pure JSON API backend with no
HTML or redirects, "no-referrer" is strictly stronger and has zero functional cost.

**File to fix:** `backend/core/middleware.py:99`

**How to fix:**
```python
# Change:
"Referrer-Policy": "strict-origin-when-cross-origin",
# To:
"Referrer-Policy": "no-referrer",
```

**Effort:** 5 minutes

---

## R-P2-42 · Driver Can Query Own Rating Before Rating the Rider — No Blind-Rating Enforcement

**Audit finding [12-3 MEDIUM].** `GET /drivers/me` returns the driver's current average rating
before the driver has submitted their post-ride rating of the rider. No guard prevents a driver from:
1. Calling `GET /drivers/me` to see their current rating (e.g. 4.72).
2. Observing that the rider's rating of them changed their average (e.g. drops to 4.68).
3. Retaliating by submitting a 1-star rating for the rider.

Additionally, `POST /drivers/rides/{ride_id}/rate-rider` has no guards:
- Does not verify the ride is in "completed" state.
- Does not verify the caller is the ride's assigned driver.
- Does not prevent duplicate ratings of the same ride.

**File to fix:** `backend/routes/drivers.py` — `rate_rider` (line 2120) and `get_my_driver` (line 208)

**How to fix:**
```python
# rate_rider — add guards:
@api_router.post("/rides/{ride_id}/rate-rider")
async def rate_rider(ride_id: str, rating_data: RideRatingRequest, current_user: dict = Depends(get_current_user)):
    driver = ...  # existing
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("driver_id") != driver["id"]:
        raise HTTPException(status_code=403, detail="Not your ride")
    if ride.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Ride must be completed to rate rider")
    if ride.get("rider_rating") is not None:
        raise HTTPException(status_code=409, detail="Rider already rated for this ride")
    # ... existing update
```

For blind-rating: strip `rating` from the `GET /drivers/me` response in a post-ride window,
or implement a mutual-reveal pattern (both parties rate → ratings revealed after 30 min window).

**Effort:** 2–4 hours

---

## R-P2-43 · account.tsx Has No Direct Path to "Delete My Account" or "Export My Data"

**Audit finding [12-4 MEDIUM].** The Account tab (the expected destination for data-subject
rights under PIPEDA) has no "Delete My Account" or "Download My Data" menu items. These
options are buried two taps deep in Account → Privacy & Settings. PIPEDA and App Store
Review Guidelines expect data-subject rights to be readily discoverable.

Note: The underlying buttons in `privacy-settings.tsx` are already confirmed API stubs
(finding [01-2] / R-P1-6). This item is about surface-level discoverability.

**File to fix:** `rider-app/app/(tabs)/account.tsx` — Safety & Privacy section (~line 209)

**How to fix:**
Add two menu items directly in the Safety & Privacy section:
```tsx
<MenuItem
  icon="download-outline" iconColor="#6B7280" iconBg="#F3F4F6"
  title="Download My Data"
  subtitle="Request a copy of your personal data"
  onPress={() => router.push('/privacy-settings' as any)}
/>
<MenuItem
  icon="trash-outline" iconColor="#DC2626" iconBg="#FEE2E2"
  title="Delete My Account"
  subtitle="Permanently delete your account and data"
  onPress={() => router.push('/privacy-settings' as any)}
/>
```
Long-term: call the API directly from account.tsx rather than routing through privacy-settings.

**Effort:** 1 hour

---

## R-P2-44 · Privacy Policy Not Linked from Account Tab

**Audit finding [12-5 MEDIUM].** `account.tsx:241` links to `/legal?type=tos` (Terms of
Service only). The Privacy Policy is accessible via `/legal?type=privacy` but no navigation
path in the Account tab reaches it. PIPEDA requires the privacy policy to be "readily available."

**File to fix:** `rider-app/app/(tabs)/account.tsx` — Legal & Help section (~line 238)

**How to fix:**
```tsx
// Add a second menu item or update existing to offer a choice:
<MenuItem
  icon="eye-outline" iconColor="#6B7280" iconBg="#F3F4F6"
  title="Privacy Policy"
  subtitle="How we collect, use, and protect your data"
  onPress={() => router.push('/legal?type=privacy' as any)}
/>
```

**Effort:** 30 minutes

---

## R-P2-45 · No In-App Location Consent Explanation Before OS Permission Dialog

**Audit finding [12-6 MEDIUM].** `index.tsx` calls `Location.requestForegroundPermissionsAsync()`
immediately on mount without any prior in-app consent screen. PIPEDA requires informed consent
before collecting personal data. The OS permission dialog (backed by `NSLocationWhenInUseUsageDescription`
in `app.config.ts`) provides a system-level prompt but does not meet the "meaningful consent"
standard that explains the full data-collection purpose, retention, and sharing.

**File to fix:** `rider-app/app/(tabs)/index.tsx` — location permission useEffect (~line 72)

**How to fix:**
```typescript
// Before calling requestForegroundPermissionsAsync():
const AsyncStorage = require('@react-native-async-storage/async-storage').default;
const consentShown = await AsyncStorage.getItem('@spinr:location_consent_shown');
if (!consentShown) {
  // Show modal: "Spinr needs your location to find nearby drivers and navigate to your pickup.
  //   Your location is only shared with your assigned driver during an active trip."
  // On "Allow": set flag, then call requestForegroundPermissionsAsync()
  // On "Deny": set flag, show "Location access is required for ride matching"
  await AsyncStorage.setItem('@spinr:location_consent_shown', '1');
}
let { status } = await Location.requestForegroundPermissionsAsync();
```

**Effort:** 2–3 hours (modal UI + AsyncStorage flag + fallback copy)

---

## R-P2-46 · Rides Not Soft-Deleted or Anonymised on Account Closure

**Audit finding [12-8 MEDIUM].** `DELETE /users/profile` soft-deletes the user and driver rows
but leaves all ride records intact with `rider_id` pointing to the deleted user.
No retention policy, no anonymisation, no purge job exists. PIPEDA requires data not to be
retained longer than necessary for the purpose.

**File to fix:** `backend/routes/users.py` — `delete_account` handler + new purge job

**How to fix:**
```python
# In delete_account() — anonymise rides after marking user deleted:
await db_supabase.update_many(
    "rides",
    {"rider_id": user_id},
    {
        "rider_id": None,
        "pickup_address": "Address removed",
        "dropoff_address": "Address removed",
        "pickup_lat": None, "pickup_lng": None,
        "dropoff_lat": None, "dropoff_lng": None,
        "anonymised_at": now,
    }
)
```

Add a scheduled purge job that anonymises rides for users whose `deleted_at` was more than
N days ago (suggest 90 days for trip data; 7 years for financial/receipt data per CRA).

Also add `anonymised_at` column to the rides schema.

**Effort:** 1 day (schema migration + anonymisation logic + purge job + policy doc)

---

## R-P2-47 · Notification Centre: Tapping a Notification Doesn't Navigate — Mark-Read Only

**Audit finding [13-6 MEDIUM].** `renderNotification` `onPress` in
`rider-app/app/notifications.tsx:128` calls only `handleMarkRead(item)`. No navigation
to the relevant screen occurs. A rider who taps "Your driver has arrived" in the
notification centre is left on the notifications list. For time-sensitive events this
means the rider may miss the free-cancel window while looking at a list.

**File to fix:** `rider-app/app/notifications.tsx` — `renderNotification` onPress

**How to fix:**
```typescript
const handleTap = async (item: AppNotification) => {
  await handleMarkRead(item);
  switch (item.type) {
    case 'ride_update':
    case 'ride':
      // Navigate to the active ride screen or ride-details for past rides
      router.push('/(tabs)');
      break;
    case 'promotion':
      router.push('/promotions');
      break;
    default:
      break; // no navigation for generic alerts
  }
};
```
Long-term: include `ride_id` in the notification API response and route directly
to `/ride-details?id=${item.ride_id}` for completed ride notifications.

**Effort:** 2–3 hours

---

## R-P2-48 · Notification Centre Unread Badge Count Not Shown at Entry Point

**Audit finding [13-7 MEDIUM].** `unreadCount` is tracked inside the notifications screen
as local state. The tab bar (`(tabs)/_layout.tsx`) has no `tabBarBadge`, and the
account.tsx entry point button has no badge. Riders cannot see that they have
unread notifications without navigating to the notifications screen.

**File to fix:** Global store + `rider-app/app/(tabs)/account.tsx` (notifications entry)

**How to fix:**
1. Add an `unreadNotificationCount` field to a lightweight global store (or add to
   an existing store like `useAuthStore`). Fetch it once on auth and refresh on
   foreground resume.
2. In `account.tsx`, pass the count to the notifications `MenuItem`:
   ```tsx
   <MenuItem
     badge={unreadCount > 0 ? String(unreadCount) : undefined}
     title="Notifications"
     ...
   />
   ```

**Effort:** 2–3 hours

---

## R-P2-49 · Support Tickets Submitted Without Auth Token — Tickets Are Anonymous

**Audit finding [13-8 MEDIUM].** `support.tsx:43–48` uses raw `fetch()` with no
Authorization header. Support tickets arrive at the backend without rider identity.
Support agents cannot link a ticket to a ride or verify the complaint.

**File to fix:** `rider-app/app/support.tsx` — `handleSubmit`

**How to fix:**
```typescript
import api from '@shared/api/client';

// Replace the raw fetch() with:
await api.post('/support/tickets', {
  subject: 'App Support Request',
  message: issue,
  category: 'general',
});
```

**Effort:** 30 minutes

---

## R-P2-50 · No Structured Dispute / Complaint Flow — Support Is Free-Text Only

**Audit finding [13-9 MEDIUM].** The support screen offers a single free-text textarea
with no ride-linked dispute option, no category selection, no fare dispute path.
Frustrated riders with no structured dispute path are more likely to initiate a
card chargeback rather than in-app resolution.

**File to fix:** `rider-app/app/support.tsx` + ride-details screen

**How to fix:**
1. Add category selection (Fare Issue / Driver Behaviour / App Bug / Other).
2. Add a "Report issue with a trip" entry point from `ride-details.tsx` that pre-fills
   `ride_id` in the ticket payload.
3. For fare disputes, add an `amount_disputed` field and surface it to support agents.

**Effort:** 3–5 hours (UI + backend ticket schema update)

---

## R-P2-51 · ride-options.tsx — fetchEstimates + fetchNearbyDrivers Not Parallelised; No Debounce on Navigation

**Audit finding [14-2 MEDIUM].** `fetchEstimates()` and `fetchNearbyDrivers()` are called
sequentially in the same useEffect synchronous block (lines 74–75) instead of via
`Promise.all`. These are independent network calls and their total wall-clock time equals
sum rather than max. Additionally, no AbortController or isMounted guard is used, so
rapid back-and-forward navigation can leave in-flight stale requests that populate the
store after a newer request has already resolved.

**File to fix:** `rider-app/app/ride-options.tsx:71–84`

**How to fix:**
```typescript
useEffect(() => {
  if (!pickup || !dropoff) return;
  let cancelled = false;
  (async () => {
    await Promise.all([fetchEstimates(), fetchNearbyDrivers()]);
  })();
  const interval = setInterval(() => { if (!cancelled) fetchNearbyDrivers(); }, 10000);
  return () => { cancelled = true; clearInterval(interval); };
}, [pickup, dropoff]);
```
For in-flight cancellation, pass an AbortSignal through the store actions and pass it to
the underlying `fetch`/`axios` call.

**Effort:** 2 hours

---

## R-P2-52 · Activity Tab Uses ScrollView + Missing Pagination — No FlatList Virtualisation

**Audit finding [14-3 MEDIUM].** The Activity screen renders all ride history in a plain
`ScrollView` with no FlatList virtualisation. The backend supports pagination (limit/offset)
but the frontend always fetches the default first page and never requests more. As ride
history grows, all rendered ride cards stay in memory simultaneously with no recycling.

**File to fix:** `rider-app/app/(tabs)/activity.tsx:45–256`

**How to fix:**
1. Replace `ScrollView` with `FlatList`.
2. Add `keyExtractor={(item) => item.id}`, `initialNumToRender={10}`,
   `maxToRenderPerBatch={5}`, `windowSize={5}`, `removeClippedSubviews` (Android).
3. Since each ride card is fixed height, add `getItemLayout` for O(1) scroll calculations.
4. Implement `onEndReached` to append the next page from `/rides/history?limit=20&offset=N`.
5. Pass the group headers as `ListHeaderComponent` items or use a `SectionList`.

**Effort:** 4–6 hours

---

## R-P2-53 · CarMarker Missing React.memo — Re-Renders on Every 1 Hz WS Location Update

**Audit finding [14-4 MEDIUM].** `shared/components/CarMarker.tsx` is not wrapped in
`React.memo`. Every `driver_location_update` WebSocket event calls `set({ currentDriver })`
in the store, which triggers a re-render of any screen subscribed to `currentDriver`
(driver-arriving, ride-in-progress). The CarMarker re-renders every ~1 second even when
the marker coordinates have not changed between frames.

**File to fix:** `shared/components/CarMarker.tsx`

**How to fix:**
```typescript
export const CarMarker = React.memo(CarMarkerBase,
  (prev, next) =>
    prev.coordinate.latitude === next.coordinate.latitude &&
    prev.coordinate.longitude === next.coordinate.longitude &&
    prev.heading === next.heading &&
    prev.size === next.size
);
```
Also consider resetting `tracksViewChanges` to `true` briefly when coordinates change
(rather than only on mount) so the Android Marker snapshot is refreshed on movement.

**Effort:** 1 hour

---

## R-P2-54 · driver-arriving.tsx Polls at 3 s Even When WebSocket Is Connected

**Audit finding [14-5 MEDIUM].** The polling interval in `driver-arriving.tsx` runs at 3 s
during the `searching`/`driver_assigned` phase regardless of WebSocket connection state.
`useRiderSocket` exports `connectionState` but `driver-arriving.tsx` does not call
`useRiderSocket()`. At scale this means ~20 redundant GET /rides/{id} requests per minute
per active rider screen, even when the WebSocket is delivering real-time updates.

**File to fix:** `rider-app/app/driver-arriving.tsx:78–90`

**How to fix:**
```typescript
const { connectionState } = useRiderSocket();
useEffect(() => {
  if (!rideId) return;
  fetchRide(rideId);
  const fastPoll = !currentRide || status === 'searching' || status === 'driver_assigned';
  // Suspend fast poll when WS is healthy; keep a 15 s fallback for stale connections
  const delay = connectionState === 'connected' ? 15000 : (fastPoll ? 3000 : 15000);
  const interval = setInterval(() => fetchRide(rideId), delay);
  return () => clearInterval(interval);
}, [rideId, currentRide?.status, connectionState]);
```

**Effort:** 1–2 hours

---

## R-P2-55 · Vehicle Type and Profile Avatar Images Use Plain RN Image — No expo-image Caching

**Audit finding [14-7 MEDIUM].** Two production image-loading sites use the plain React
Native `<Image>` component instead of `expo-image`, resulting in no memory or disk caching
for remote images:
- `rider-app/app/ride-options.tsx:430–436` — vehicle type images downloaded on every mount.
- `rider-app/app/(tabs)/index.tsx:147–150` — user profile photo downloaded on every mount.

**File to fix:** `rider-app/app/ride-options.tsx`; `rider-app/app/(tabs)/index.tsx`

**How to fix:**
```typescript
// Replace:
import { Image } from 'react-native';
// With:
import { Image } from 'expo-image';
// Change resizeMode="contain" prop to contentFit="contain"
// Optionally add: placeholder={blurhash} cachePolicy="memory-disk"
```
`expo-image` is already installed in `package.json` (`"expo-image": "~3.0.11"`).

**Effort:** 1 hour

---

## R-P2-56 · Tip Buttons Missing accessibilityLabel/accessibilityRole; Height Marginally
           Below 44pt Minimum

**Audit finding [15-2 MEDIUM].** The $2, $5, $10 tip `TouchableOpacity` elements in
`ride-completed.tsx:375–382` carry no `accessibilityLabel` and no `accessibilityRole`.
`tipBtn` style has `paddingVertical: 12` with ~19pt text height, giving a total tap target of
~43pt — 1pt below the iOS HIG 44pt minimum. The custom tip `View` container is not an
accessible button.

**File to fix:** `rider-app/app/ride-completed.tsx:375–396`

**How to fix:**
```tsx
<TouchableOpacity
  key={amt}
  accessibilityLabel={`Tip $${amt}`}
  accessibilityRole="button"
  accessibilityState={{ selected: selectedTip === amt }}
  style={[styles.tipBtn, selectedTip === amt && styles.tipBtnActive]}
  onPress={() => { setSelectedTip(amt); setCustomTip(''); }}
>
```
Increase `tipBtn.paddingVertical` from `12` to `13` to clear 44pt.
Add `accessibilityLabel="Custom tip amount"` to the custom TextInput.

**Effort:** 30 minutes

---

## R-P2-57 · FreeCancelTimer Countdown Is Silent to Screen Reader — No accessibilityLiveRegion

**Audit finding [15-4 MEDIUM].** `rider-app/components/FreeCancelTimer.tsx:70–91` — the
countdown text updates every second and the label changes when the free-cancel window expires,
but no `accessibilityLiveRegion` is set. A VoiceOver user not focused on the timer receives no
spoken announcement of the countdown or the fee-transition event.

**File to fix:** `rider-app/components/FreeCancelTimer.tsx`

**How to fix:**
```tsx
// On the timer Text, fire an assertive announcement only at key transitions:
<Text
  accessibilityLiveRegion={secondsLeft === 0 ? 'assertive' : 'polite'}
  accessibilityLabel={
    isWindowOpen
      ? `Free cancellation — ${timerLabel} remaining`
      : `Cancellation fee applies — $${cancellationFee.toFixed(2)}`
  }
  style={styles.freeTimer}
>
  {timerLabel}
</Text>
```
Keep per-second announcements silent (remove or conditionalize the live region to trigger
only at the 60 s mark and 0 s transition).

**Effort:** 1 hour

---

## R-P2-58 · OTP Entry Boxes Not Individually Labelled; Hidden TextInput Has No
           accessibilityLabel

**Audit finding [15-5 MEDIUM].** `rider-app/app/otp.tsx:234–277` — the single hidden
`TextInput` driving the 4-box OTP UI has no `accessibilityLabel` or `accessibilityHint`.
VoiceOver announces it as an unlabelled text field. The four visual `Animated.View` boxes are
non-interactive and invisible to the screen reader. Users receive no positional feedback
("2 of 4 digits entered").

**File to fix:** `rider-app/app/otp.tsx:237–244`

**How to fix:**
```tsx
<TextInput
  ref={inputRef}
  style={styles.hiddenInput}
  value={code}
  onChangeText={handleCodeChange}
  keyboardType="phone-pad"
  maxLength={codeLength}
  autoFocus
  accessibilityLabel="Enter 4-digit verification code"
  accessibilityHint={`${code.length} of 4 digits entered`}
/>
```

**Effort:** 30 minutes

---

## R-P2-59 · Search Autocomplete Suggestion Rows Have No accessibilityLabel or
           accessibilityRole

**Audit finding [15-6 MEDIUM].** All suggestion rows in `search-destination.tsx` —
including Google Places predictions (line 296–315), "Current Location" (line 445),
"Set location on map" (line 471), saved addresses, and recent searches — are
`TouchableOpacity` elements with only `Text` children and no accessibility props.
VoiceOver reads child text without role context. Core booking flow (entering destination)
is degraded for screen-reader users.

**File to fix:** `rider-app/app/search-destination.tsx:296–315, 445–605`

**How to fix:**
```tsx
const renderPrediction = ({ item }: { item: PlacePrediction }) => (
  <TouchableOpacity
    style={styles.predictionRow}
    accessibilityRole="button"
    accessibilityLabel={item.description}
    onPress={() => handleSelectPrediction(item)}
  >
```
Apply `accessibilityRole="button"` and `accessibilityLabel` to all static suggestion rows.

**Effort:** 1 hour

---

## R-P2-60 · Bottom Sheets in Ride Screens Missing accessibilityViewIsModal — VoiceOver
           Focus Not Trapped

**Audit finding [15-8 MEDIUM].** `@gorhom/bottom-sheet` is used in `driver-arriving.tsx`,
`driver-arrived.tsx`, and `ride-in-progress.tsx` without `accessibilityViewIsModal={true}`.
VoiceOver can traverse from sheet content to background map elements. Two RN `Modal`
elements in `ride-options.tsx` (date/time pickers, lines 532 and 568) also lack
`accessibilityViewIsModal`.

**File to fix:**
- `rider-app/app/driver-arriving.tsx:421`
- `rider-app/app/driver-arrived.tsx:225`
- `rider-app/app/ride-in-progress.tsx:319`
- `rider-app/app/ride-options.tsx:532, 568`

**How to fix:**
```tsx
<BottomSheet
  ref={bottomSheetRef}
  accessibilityViewIsModal={true}
  ...
>
// And for RN Modal:
<Modal transparent animationType="slide" visible={showDatePicker}
       accessibilityViewIsModal={true}>
```

**Effort:** 1 hour

---

## R-P2-61 · Primary Colour (#FF3B30) on White Background Fails WCAG AA 4.5:1 Contrast
           Ratio for Normal-Weight Text

**Audit finding [15-9 MEDIUM].** Light-mode primary `#FF3B30` against `#FFFFFF` has a
contrast ratio of approximately 3.95:1, below the WCAG AA threshold of 4.5:1 for normal text.
Affected instances:
- `ride-completed.tsx:533` — `tipBtnTextActive` (16pt text, colour `colors.primary` on white
  surface): 3.95:1, FAILS.
- `ride-completed.tsx:554–557` — submit button: white `#FFF` text on `#FF3B30` background.
  At 17pt bold this does not meet the 18pt large-text threshold, so 4.5:1 is required:
  3.95:1, FAILS.

Dark-mode primary `#FF453A` on `#1C1C1E` is ≈ 4.63:1 — PASS.

**File to fix:** `shared/theme/index.ts` (lightColors.primary) and/or component-level overrides

**How to fix:**
Option A — Darken `lightColors.primary` to `#D93025` (contrast ≈ 4.55:1 against white).
Option B — Keep brand colour and use a darker override only for text-on-white contexts:
```tsx
// tipBtnTextActive — override to darker shade:
tipBtnTextActive: { color: '#C0392B' },  // contrast ~4.6:1
```
Document the design decision in the design system so future brand-colour updates account for
contrast compliance.

**Effort:** 2 hours (design decision + token update + regression check)

---

## Checklist

- [ ] R-P2-1 Offline queue extended for cancel, rate, tip, emergency
- [ ] R-P2-2 Cold-start ride validated against backend before routing
- [ ] R-P2-3 RideStatus constants created and magic strings replaced
- [ ] R-P2-4 TypeScript `any` replaced with typed interfaces + Zod on WS
- [ ] R-P2-5 Polling suspended when WebSocket is connected
- [ ] R-P2-6 Error states with retry actions on all key screens
- [ ] R-P2-7 Rider PII stripped from driver-facing API responses
- [ ] R-P2-8 become-driver.tsx completes handoff with store links [see P1-11 first]
- [ ] R-P2-9 Touch targets ≥ 44pt for stars and tip buttons
- [ ] R-P2-10 Corporate account billing selectable in payment-confirm
- [ ] R-P2-11 ToS acceptance step added to onboarding flow (App Store + PIPEDA)
- [ ] R-P2-12 become-driver.tsx deep-links to driver app if installed
- [ ] R-P2-13 Access token removed from SecureStore persistence (memory-only pattern)
- [ ] R-P2-14 eas.json test/preview profiles point to staging URL, not production
- [ ] R-P2-15 TruffleHog CI scans full PR diff; --only-verified removed
- [ ] R-P2-16 Rider-app CI adds EXPO_PUBLIC_ private-variable check; play-service-account.json added to .gitignore
- [ ] R-P2-17 Tip endpoints use Pydantic model; NaN bypass closed
- [ ] R-P2-18 stops array capped at 5; stop coordinates range-validated
- [ ] R-P2-19 scheduled_time validator rejects past timestamps
- [ ] R-P2-20 Fare split phone strings validated to +1XXXXXXXXXX format
- [ ] R-P2-21 SavedAddressCreate adds max_length; sanitize_string() called in handler
- [ ] R-P2-22 WalletPayRequest.amount gets maximum cap (le=500)
- [ ] R-P2-23 Wallet request models use Decimal instead of float
- [ ] R-P2-24 search-destination.tsx wrapped in KeyboardAvoidingView
- [ ] R-P2-25 allowFontScaling=false applied to fixed-height Text containers in ride screens
- [ ] R-P2-26 ride-in-progress.tsx adds error state with retry for ride load failure
- [ ] R-P2-27 driver-arrived.tsx replaces null map render with error/loading state
- [ ] R-P2-28 Home screen zoom buttons increased to 44×44pt
- [ ] R-P2-29 fetchRide() merges REST driver object but preserves WS-updated lat/lng (no marker jump-back)
- [ ] R-P2-30 Location denied shows Alert + "Open Settings" deep-link instead of silent early-return
- [ ] R-P2-31 FreeCancelTimer expiry fires onExpire callback; Cancel button shows fee warning for expired window
- [ ] R-P2-32 Book button disabled state tied to isSubmitting state (not only isLoading); spinner shown immediately
- [ ] R-P2-33 Promo validation uses ride_id to fetch fare server-side; minimum-fare bypass closed
- [ ] R-P2-34 Fare-split pay endpoint uses atomic status guard to prevent TOCTOU double-deduction
- [ ] R-P2-35 Store tests added for scheduled rides, promo, offline queue, fare-split payment confirmation
- [ ] R-P2-36 useRiderSocket hook test added; component tests for ride panel UI components added
- [ ] R-P2-37 ride-options.tsx shows error message + retry button when fetchEstimates() fails
- [ ] R-P2-38 ErrorBoundary added to driver-arriving, ride-in-progress, ride-completed, driver-arrived screens
- [ ] R-P2-39 /rides/{id}/cancel decorated with @ride_request_limit; request: Request param added
- [ ] R-P2-40 /promo/validate rate-limited at 10/minute; generic error message for unknown codes
- [ ] R-P2-41 Referrer-Policy changed from strict-origin-when-cross-origin to no-referrer
- [ ] R-P2-42 rate_rider endpoint guards: completed-state, driver ownership, no-re-rate; blind-rating enforced
- [ ] R-P2-43 account.tsx Safety & Privacy section links directly to Delete Account and Download My Data
- [ ] R-P2-44 account.tsx Legal section includes Privacy Policy link (/legal?type=privacy)
- [ ] R-P2-45 In-app location consent modal shown before requestForegroundPermissionsAsync()
- [ ] R-P2-46 Rides anonymised on account deletion; retention policy + purge job defined
- [ ] R-P2-47 Notification centre tap navigates to relevant screen (not just mark-read)
- [ ] R-P2-48 Unread notification badge count surfaced at notifications entry point in account.tsx
- [ ] R-P2-49 Support ticket submit uses authenticated api client (not raw fetch)
- [ ] R-P2-50 Structured dispute/complaint flow added with category selection and ride_id linkage
- [ ] R-P2-51 ride-options.tsx: fetchEstimates + fetchNearbyDrivers parallelised with Promise.all; AbortController added
- [ ] R-P2-52 Activity tab: ScrollView replaced with FlatList; pagination wired to /rides/history?limit=20&offset=N
- [ ] R-P2-53 CarMarker wrapped in React.memo with coordinate/heading equality comparator
- [ ] R-P2-54 driver-arriving.tsx: 3 s poll suspended when WebSocket connectionState === 'connected'
- [ ] R-P2-55 ride-options.tsx + index.tsx: plain RN Image replaced with expo-image for vehicle/avatar remote images
- [ ] R-P2-56 Tip buttons: accessibilityLabel + accessibilityRole; paddingVertical raised to 13 to meet 44pt; custom tip TextInput labelled
- [ ] R-P2-57 FreeCancelTimer: accessibilityLiveRegion fires at 60 s and 0 s transitions; fee-change announced assertively
- [ ] R-P2-58 OTP hidden TextInput: accessibilityLabel="Enter 4-digit verification code" + digit-count hint added
- [ ] R-P2-59 Search autocomplete suggestion rows: accessibilityRole="button" + accessibilityLabel on all rows
- [ ] R-P2-60 BottomSheet instances + RN Modals in ride screens: accessibilityViewIsModal={true} added
- [ ] R-P2-61 Primary colour contrast: tipBtnTextActive and submit button text meet WCAG AA 4.5:1 in light mode
- [ ] R-P2-62 Replace all fare/currency template literals (`$${value.toFixed(2)}`) with a shared formatCurrency(amount, locale) utility backed by Intl.NumberFormat('fr-CA',{style:'currency',currency:'CAD'}) (see [16-3])
