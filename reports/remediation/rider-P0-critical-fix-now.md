# P0 — Rider App Critical: Fix Before Any Device Testing

These items are pre-populated from known findings (codebase exploration + driver audit learnings).
Each must be verified during the audit and updated with exact file/line references.
**Fix all of these before running the app on a real phone.**

**Estimated total effort:** ~1 day

---

## R-P0-1 · Emergency SOS Silently Fails on Network Error

**What's wrong:** `triggerEmergency()` in `rideStore.ts` catches errors and swallows them
silently. If the network is down or the backend is unreachable when a rider taps SOS,
nothing happens — no retry, no user notification, no fallback.

**Why it matters:** This is a safety feature. Silent failure during an emergency can put
the rider at risk. This is the highest-severity UX failure possible.

**File to fix:** `rider-app/store/rideStore.ts` — `triggerEmergency` action

**How to fix:**
```typescript
// After catch block — must NOT silently swallow:
triggerEmergency: async (rideId, lat, lng) => {
  try {
    await api.post(`/rides/${rideId}/emergency`, { lat, lng });
  } catch (err) {
    // Show alert to user — emergency may not have been sent
    Alert.alert(
      'Emergency Alert May Not Have Sent',
      'Could not reach the server. Please call 911 directly.',
      [{ text: 'Call 911', onPress: () => Linking.openURL('tel:911') }]
    );
    throw err; // re-throw so caller can handle
  }
}
```

**Effort:** 1 hour

---

## R-P0-2 · Offline Banner Hidden Behind Notch / Status Bar (Rider App)

**What's wrong:** `shared/components/OfflineBanner.tsx` uses `top: 0` which places the
banner behind the device notch or Android status bar. Rider never sees the "You are
offline" warning.

**Why it matters:** Rider taps "Book Ride" while offline — gets a confusing spinner
or error with no explanation that they're offline.

**File to fix:** `shared/components/OfflineBanner.tsx`

**How to fix:**
```tsx
import { useSafeAreaInsets } from 'react-native-safe-area-context';
const insets = useSafeAreaInsets();
// Change: style={{ top: 0 }}
// To:     style={{ top: insets.top }}
```

**Note:** This was P0-7 in the driver audit. Verify the fix was applied to the shared
component. If it was fixed for driver app, it should be fixed here too — confirm.

**Effort:** 30 minutes (verify only if driver fix already merged)

---

## R-P0-3 · Pickup OTP Stored as Plaintext — Verify Fix Covers Rider Rides

**What's wrong:** Driver audit P0-4 identified that pickup OTPs are stored as plaintext
in the database. The fix must be applied to the ride creation path used by the RIDER
app too (not just the driver-side code path).

**Why it matters:** Database breach exposes all active pickup codes.

**File to fix:** `backend/routes/rides.py` — ride creation endpoint (POST /rides)

**How to fix:** Confirm that OTP hashing (SHA-256) is applied when the ride is created
by the rider, not just when the driver verifies it. If the fix only touched the driver
accept path, it is incomplete.

**Effort:** 1 hour to verify + fix if incomplete

---

## R-P0-4 · Android Back Button Exits Active Ride Screens

**What's wrong:** On Android, pressing the hardware back button while on
`driver-arriving`, `driver-arrived`, or `ride-in-progress` screens exits the ride
flow entirely. The rider is taken back to home while the ride is still active — the
app state is now out of sync with the backend.

**Why it matters:** Rider loses visibility of their active ride. Driver shows up but
rider can't see them or contact them. Critical UX failure.

**File to fix:**
- `rider-app/app/driver-arriving.tsx`
- `rider-app/app/driver-arrived.tsx`
- `rider-app/app/ride-in-progress.tsx`

**How to fix:**
```tsx
import { BackHandler } from 'react-native';
useEffect(() => {
  const sub = BackHandler.addEventListener('hardwareBackPress', () => true);
  return () => sub.remove();
}, []);
```

**Effort:** 1 hour (3 files)

---

## R-P0-5 · Double Booking — Rider Can Create Two Active Rides

**What's wrong:** If `createRide()` is called twice (double-tap, network retry, or
navigation back-and-forward through payment-confirm), the rider may have two simultaneous
active rides in the backend.

**Why it matters:** Two drivers dispatched to the same rider. Confusing for all parties.
Potential double charge.

**File to fix:**
- `rider-app/app/payment-confirm.tsx` — submit button must be disabled after first tap
- `rider-app/store/rideStore.ts` — createRide() must check for existing active ride
- `backend/routes/rides.py` — server must reject ride creation if rider already has an active ride

**How to fix:**
```typescript
// rideStore.ts — guard before API call:
createRide: async (...) => {
  if (get().currentRide) {
    throw new Error('A ride is already active');
  }
  // ... proceed
}
```
```python
# backend/routes/rides.py:
existing = await db.fetchrow(
    "SELECT id FROM rides WHERE rider_id=$1 AND status NOT IN ('completed','cancelled')",
    user.id
)
if existing:
    raise HTTPException(409, "You already have an active ride")
```

**Effort:** 2–3 hours

---

## Checklist

- [ ] R-P0-1 Emergency SOS alerts user on network failure; offers 911 fallback
- [ ] R-P0-2 OfflineBanner uses safe area insets (verify shared fix from driver audit)
- [ ] R-P0-3 Pickup OTP hashing covers rider-created rides (not just driver verify path)
- [ ] R-P0-4 Android BackHandler added to driver-arriving, driver-arrived, ride-in-progress
- [ ] R-P0-5 Double booking blocked: button disable + store guard + backend 409
