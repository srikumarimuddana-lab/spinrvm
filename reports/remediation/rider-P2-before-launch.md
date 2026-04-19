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

**File to fix:** `rider-app/store/rideStore.ts` — `hydrateActiveRide`

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
