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

**Audit update (D05):** `driver-arriving.tsx` (lines 192–198) and `driver-arrived.tsx`
(lines 67–70) already have BackHandler implemented correctly with cancel dialogs.
**Only `ride-in-progress.tsx` is still missing it.**

**File to fix:**
- `rider-app/app/ride-in-progress.tsx` ← remaining file (the other two are fixed)

**How to fix:**
```tsx
import { BackHandler } from 'react-native';
useEffect(() => {
  const sub = BackHandler.addEventListener('hardwareBackPress', () => {
    // Show "End ride early? Full fare applies" confirmation dialog
    setAlertState({ visible: true, title: 'End ride early?', ... });
    return true;
  });
  return () => sub.remove();
}, [currentRide?.status]);
```

**Effort:** 30 minutes (1 file remaining)

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

## R-P0-6 · Home Screen SOS Button Shows False Confirmation — No Backend Call Made

**What's wrong:** The SOS button on the home/map screen (`rider-app/app/(tabs)/index.tsx:236–252`)
is a plain `TouchableOpacity` whose `onPress` fires only `setAlertState()` with the
message **"Emergency services have been alerted. Stay calm and stay where you are."**
This message is factually false — no API call is made, no emergency contact is notified,
no 911 prompt appears.

The shared `SOSButton` component (`shared/components/SOSButton.tsx`), which calls the
backend AND prompts the rider to dial 911, is used correctly during active rides
(`ride-in-progress.tsx:204`) but is never used on the home screen.

A rider who taps SOS **outside of an active ride** (e.g. after a drop-off in an unsafe
area, or before a ride starts) receives a false "help is coming" message while nothing
actually happens.

**Why it matters:** This is a safety-critical false positive. A rider may believe help
has been sent and delay calling 911 themselves.

**File to fix:** `rider-app/app/(tabs)/index.tsx` — replace custom SOS button

**How to fix:**
```tsx
import { SOSButton } from '@shared/components/SOSButton';
import { Linking } from 'react-native';

// Replace the home-screen TouchableOpacity SOS with:
<SOSButton
  rideId={currentRide?.id}  // may be undefined pre-ride
  onTrigger={async (rideId, lat, lng) => {
    if (rideId) {
      await triggerEmergency(rideId, lat, lng);
    } else {
      // No active ride — open 911 directly
      Linking.openURL('tel:911');
    }
  }}
  size="small"
/>
```

**Note:** `triggerEmergency` still needs the fix from R-P0-1 (surface error to user).
Both fixes should land together.

**Effort:** 1.5 hours

---

## R-P0-7 · OTP Brute-Force Lockout Silently Bypassed When Redis Is Down

**Audit finding [02-1 HIGH].** The 4-digit OTP design (approved) relies on three compensating
controls: rate limit, brute-force lockout, and 5-minute expiry. If Redis is unavailable,
`_check_otp_lockout()` in `backend/routes/auth.py:75–77` silently returns without blocking — the
lockout control disappears. With only the SlowAPI rate limit remaining (5/min), the full 10,000
4-digit OTP keyspace is exhausted in ~33 minutes.

**Why it matters:** Removes a required compensating control for the 4-digit OTP scheme. During
a Redis outage (planned maintenance, OOM, failover) accounts are fully susceptible to
brute-force attack.

**File to fix:** `backend/routes/auth.py` — `_check_otp_lockout`

**How to fix:**
```python
async def _check_otp_lockout(phone: str) -> None:
    try:
        key = f"otp_lockout:{phone}"
        count = await redis_client.get(key)
        if count and int(count) >= MAX_OTP_ATTEMPTS:
            raise HTTPException(429, "Too many attempts — try again later")
    except HTTPException:
        raise  # re-raise our own 429
    except Exception as e:
        # Redis is down — fail closed: block the attempt
        logger.error(f"Redis unavailable in OTP lockout check: {e}")
        raise HTTPException(503, "Authentication service temporarily unavailable")
```

**Effort:** 1 hour

---

## R-P0-8 · Real Supabase Service-Role Key Committed in backend/.env.example

**Audit finding [03-1 CRITICAL].** `backend/.env.example` contains a live Supabase
service-role JWT (`eyJhbGci…`) pointing at project `dbbadhihiwztmnqnbdke.supabase.co`.
The `role: "service_role"` claim in the JWT payload bypasses all Row Level Security.
Anyone with repository access can use this key to read/write every table.

**Why it matters:** Immediate database compromise risk. All rider PII, ride history, payment
records, and driver data are accessible. The key expires 2036.

**File to fix:** `backend/.env.example:3`

**How to fix:**
1. **IMMEDIATE** — Rotate the key in Supabase Dashboard → Settings → API → Rotate service-role key.
2. Replace `.env.example` line with placeholder:
   ```
   SUPABASE_SERVICE_ROLE_KEY=replace-with-service-role-key-from-supabase-dashboard
   ```
3. Audit git history for the committed key: `trufflehog git file://. --no-verification`
4. If the key appears in prior commits, expunge with `git-filter-repo` and force-push after coordinating with the team.

**Effort:** 30 minutes (key rotation) + 1 hour (history audit/expunge if needed)

---

## Checklist

- [ ] R-P0-1 Emergency SOS alerts user on network failure; offers 911 fallback
- [ ] R-P0-2 OfflineBanner uses safe area insets (verify shared fix from driver audit)
- [ ] R-P0-3 Pickup OTP hashing covers rider-created rides (not just driver verify path)
- [ ] R-P0-4 Android BackHandler added to driver-arriving, driver-arrived, ride-in-progress
- [ ] R-P0-5 Double booking blocked: button disable + store guard + backend 409
- [ ] R-P0-6 Home screen SOS replaced with real SOSButton; 911 fallback when no active ride
- [ ] R-P0-7 OTP lockout fails closed on Redis error (not silently bypassed)
- [ ] R-P0-8 Supabase service-role key rotated; backend/.env.example placeholder replaced
