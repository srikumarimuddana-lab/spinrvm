# P0 — Critical: Fix Before Any Device Testing

These 7 items must be resolved before you run the app on a real phone. They include a runtime crash, a data breach risk, a security flaw that lets two drivers steal the same ride, and a broken driver suspension system.

**Estimated total effort:** ~1 day

---

## P0-1 · App Will Crash at Login — Wrong Database Field Name

**What's wrong:** The code that checks if a user's login token has been revoked looks for a field called `revoked` in the database, but the actual field is called `revoked_at`. Every login attempt will crash with an error.

**File to fix:** `backend/routes/auth.py` — search for `revoked` and change to `revoked_at`

**Why it matters:** The app literally cannot log anyone in.

**Effort:** 15 minutes

---

## P0-2 · App Will Crash — Broken refresh_token Function

**What's wrong:** There are two functions that handle token refresh. The old one (line 347–401) calls a database helper function (`_store_refresh_token`) that doesn't exist. If that old function ever runs, the app crashes immediately.

**File to fix:** `backend/routes/auth.py` lines 347–401 — delete this entire old function, keep only the new one.

**Why it matters:** Random crashes during sessions, hard to debug.

**Effort:** 10 minutes

---

## P0-3 · Two Drivers Can Accept the Same Ride at the Same Time

**What's wrong:** When a ride is offered to multiple drivers and two tap "Accept" within milliseconds of each other, both can succeed. The second driver doesn't get an error — they both think they have the job. The rider ends up with two drivers.

**File to fix:** `backend/routes/rides.py` — the `accept_ride` endpoint needs to update the ride status in a single atomic database operation that only succeeds for the first driver. Use:
```sql
UPDATE rides SET driver_id = $driver_id, status = 'driver_assigned'
WHERE id = $ride_id AND status = 'searching'
```
Then check if 0 rows were updated — if so, the ride was already taken.

**Why it matters:** Riders get two drivers. Drivers argue. Trust destroyed.

**Effort:** 1–2 hours

---

## P0-4 · Pickup Code Stored as Plain Text — Anyone Who Reads the Database Sees All Codes

**What's wrong:** The 4-digit code drivers and riders use to confirm pickup is saved directly in the database as `1234` (or whatever the code is). If anyone ever gets read access to the database, they can see every active pickup code and impersonate drivers.

**File to fix:** `backend/routes/rides.py` — when saving the OTP, hash it first:
```python
import hashlib
hashed = hashlib.sha256(otp.encode()).hexdigest()
# Save hashed value to DB, compare hashed value at verification
```

**Why it matters:** A database breach exposes all active ride pickup codes.

**Effort:** 2–3 hours (generate + save hash, compare hash on verification)

---

## P0-5 · Driver With Expired Licence Can Keep Working — No Automatic Suspension

**What's wrong:** When a driver's licence, insurance, or background check expires, the nightly check runs — but it only sends a 7-day warning. It does **not** take the driver offline or block them from accepting rides. A driver with an expired licence can work indefinitely.

**File to fix:** `backend/utils/document_expiry.py` lines 24–130

Add these two steps after sending the expiry notification:
1. Set the driver's status to `suspended` in the database
2. Send them a WebSocket message to disconnect them from the dispatch queue

Also fix the loop condition — it currently skips documents that are already past their expiry date.

**Why it matters:** Legal liability. A crash involving an uninsured driver or someone who failed their background check is catastrophic.

**Effort:** 3–4 hours

---

## P0-6 · Driver Licence Photos Are Publicly Accessible — No Login Required

**What's wrong:** When a driver uploads their licence scan or insurance document, the app saves the file and returns a permanent public URL. Anyone with that URL can view the document — no login needed. The URL never expires.

**File to fix:** `backend/documents.py` — replace `get_public_url()` with Supabase's signed URL:
```python
# Instead of:
url = storage.get_public_url(path)

# Use:
url = storage.create_signed_url(path, expires_in=3600)  # 1-hour link
```

**Why it matters:** Driver licence scans contain full name, address, date of birth, and licence number. PIPEDA violation.

**Effort:** 1–2 hours

---

## P0-7 · "Offline" Banner Is Hidden Behind the Phone's Status Bar

**What's wrong:** When the driver has no internet, a red "You are offline" banner appears at the top of the screen — but it's positioned at `top: 0`, which places it behind the notch (iPhone) or status bar (Android). The driver never sees it.

**File to fix:** `shared/components/OfflineBanner.tsx` — change `top: 0` to use the safe area inset:
```tsx
import { useSafeAreaInsets } from 'react-native-safe-area-context';
const insets = useSafeAreaInsets();
// ...
style={{ top: insets.top }}
```

**Why it matters:** Driver keeps accepting rides without knowing they're offline. State gets out of sync.

**Effort:** 30 minutes

---

## Checklist

- [x] P0-1 Fix `revoked` → `revoked_at` in auth.py — PASS: auth.py:575 uses `revoked_at` correctly
- [x] P0-2 Delete dead refresh_token function (lines 347–401) — PASS: only one refresh handler exists; no `_store_refresh_token` reference found
- [x] P0-3 Add atomic UPDATE for ride acceptance (no double-accept) — PASS: drivers.py:1800–1818 uses atomic `AND status='searching'` conditional update; 0 rows → ride_taken WS event + 409
- [x] P0-4 Hash pickup OTP before saving to database — PASS: rides.py:887 `hash_otp(pickup_otp_plain)` (SHA-256) before INSERT
- [x] P0-5 Add driver suspension + disconnect when documents expire — PASS: document_expiry.py:110–128 sets `status=suspended`, clears presence, calls `manager.disconnect()`, sends push
- [x] P0-6 Replace permanent document URLs with 1-hour signed links — PASS: documents.py:263 uses `create_signed_url(filename, 3600)`
- [x] P0-7 Fix OfflineBanner to sit below the notch/status bar — PASS: OfflineBanner.tsx:171 uses `top: topInset` (from `useSafeAreaInsets()`)
