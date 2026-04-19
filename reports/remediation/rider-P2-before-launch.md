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

**What's wrong:** The backend has `corporate_rider.py` and `corporate_wallet.py`
routes, and the `Ride` type includes `corporate_account_id`. But no screen in
the rider app allows selecting a corporate account for billing.

**File to fix:** `rider-app/app/payment-confirm.tsx`

**How to fix:** Add a "Bill to [Company Name]" toggle when rider has a corporate
account on their profile. Pass `corporate_account_id` in the ride creation payload.

**Effort:** 3–4 hours (frontend + backend integration)

---

## R-P2-11 · Surge Pricing Not Clearly Shown Before Booking

**What's wrong:** The `RideEstimate` type includes `surge_multiplier` but there is
no confirmed UI element in `ride-options.tsx` that prominently displays an active
surge (e.g. "1.8x surge pricing in effect" banner). Riders must be clearly informed
of surge before committing — this is an informed-consent requirement.

**File to fix:** `rider-app/app/ride-options.tsx`

**How to fix:**
```tsx
{estimate.surge_multiplier > 1 && (
  <View style={styles.surgeBanner}>
    <Text style={styles.surgeText}>
      {estimate.surge_multiplier}x surge pricing in effect
    </Text>
  </View>
)}
```

**Effort:** 1 hour

---

## R-P2-12 · Masked Phone Call — Rider's Real Number Exposed to Driver

**What's wrong:** When a rider contacts the driver during an active ride, the app
likely opens a standard phone call with the rider's real number visible to the driver.
This is a safety and privacy risk — riders' personal numbers should never be exposed.

**File to fix:** `rider-app/app/driver-arriving.tsx` and `rider-app/app/driver-arrived.tsx`

**How to fix:** Use a telephony proxy service (Twilio, Vonage) that provides a
temporary masked number for the duration of the trip. On tap: call the masked number,
not the driver's real number. Backend endpoint: `POST /rides/{id}/call-driver` returns
a short-lived proxy number.

**Effort:** 4–6 hours (requires backend + telephony service setup)

---

## R-P2-13 · FlatList Keys Using Math.random() — Full Re-render on Every Update

**What's wrong:** If any FlatList in the rider app uses `Math.random()` as the
`keyExtractor` return value (same CRITICAL bug found in driver audit [14-7]), the
entire list re-renders on every state update. This causes visible flicker in the
activity list, saved places, and notifications.

**File to fix:** `rider-app/app/(tabs)/activity.tsx`, `rider-app/app/saved-places.tsx`,
`rider-app/app/notifications.tsx`

**How to fix:**
```tsx
// Wrong:
keyExtractor={() => Math.random().toString()}
// Correct:
keyExtractor={(item) => item.id}
```

**Effort:** 30 minutes per file

---

## R-P2-14 · Notification Preferences Lost on App Reinstall

**What's wrong:** Notification preferences (which ride events to receive push for)
are stored in local AsyncStorage only. When a user reinstalls the app, all preferences
reset to defaults. [Driver audit 13-4]

**File to fix:** Backend: `GET/PUT /notifications/preferences` endpoint.
Rider app: `settings.tsx` → sync preferences to backend on change.

**Effort:** 2–3 hours

---

## Checklist

- [ ] R-P2-1 Offline queue extended for cancel, rate, tip, emergency
- [ ] R-P2-2 Cold-start ride validated against backend before routing
- [ ] R-P2-3 RideStatus constants created and magic strings replaced
- [ ] R-P2-4 TypeScript `any` replaced with typed interfaces + Zod on WS
- [ ] R-P2-5 Polling suspended when WebSocket is connected
- [ ] R-P2-6 Error states with retry actions on all key screens
- [ ] R-P2-7 Rider PII stripped from driver-facing API responses
- [ ] R-P2-8 become-driver.tsx completes handoff with store links
- [ ] R-P2-9 Touch targets ≥ 44pt for stars and tip buttons
- [ ] R-P2-10 Corporate account billing selectable in payment-confirm
- [ ] R-P2-11 Surge multiplier displayed prominently before booking
- [ ] R-P2-12 Masked phone proxy for rider-to-driver calls
- [ ] R-P2-13 FlatList keyExtractor uses stable ride.id (not Math.random)
- [ ] R-P2-14 Notification preferences synced to backend (not local-only)
