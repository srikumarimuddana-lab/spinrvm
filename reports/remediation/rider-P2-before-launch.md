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
