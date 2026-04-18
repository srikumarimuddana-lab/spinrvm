# P2 — Important: Fix Before Public Launch

These 10 items are security issues and data quality problems that must be resolved before the app goes live to real users. They won't crash the app but can cause data loss, incorrect charges, or compliance violations.

**Estimated total effort:** ~5–7 days

---

## P2-1 · Money Calculations Use the Wrong Number Type — Rounding Errors Possible

**What's wrong:** All fare amounts, earnings, and payout figures are stored as `float` numbers. Floats are inherently imprecise — for example `0.1 + 0.2` in a float equals `0.30000000000000004`, not `0.3`. Over many rides, pennies appear or disappear.

**File to fix:** `backend/schemas.py` and `backend/validators.py` — change all monetary fields from `float` to `Decimal`:
```python
from decimal import Decimal
fare: Decimal = Field(..., decimal_places=2, ge=0)
```

**Effort:** 3–4 hours

---

## P2-2 · GPS Coordinate (0, 0) Is Not Rejected — Drivers Appear in the Ocean

**What's wrong:** The GPS validator detects "null island" coordinates (latitude 0, longitude 0 — a spot in the ocean off the coast of Africa) but only logs a warning instead of rejecting the request. Drivers at coordinate 0,0 confuse the dispatch algorithm.

**File to fix:** `backend/validators.py` — change the null island check to raise a `400 Bad Request` error instead of logging.

**Effort:** 30 minutes

---

## P2-3 · Two Location Updates Arriving at the Same Time Can Crash the Server

**What's wrong:** The WebSocket manager keeps a dictionary of connected drivers. If two drivers disconnect at exactly the same moment, the code that loops through the dictionary to send messages can crash because the dictionary changed size while it was being looped.

**File to fix:** `backend/socket_manager.py` — `broadcast()` method. Take a snapshot before looping:
```python
connections = list(self.active_connections.values())  # snapshot
for connection in connections:
    ...
```

**Effort:** 30 minutes

---

## P2-4 · Rate Limiting Stops Working When Redis Is Unavailable

**What's wrong:** The OTP brute-force protection relies on Redis to count failed attempts. If Redis goes offline, the counter silently stops working — unlimited OTP guesses are allowed without any errors or warnings.

**File to fix:** `backend/utils/rate_limiter.py` — add an in-memory fallback counter that activates when Redis is unavailable. Log a warning so the team knows Redis is down.

**Effort:** 3–4 hours

---

## P2-5 · Driver Licence Numbers and VINs Are Stored Without Encryption

**What's wrong:** Driver licence numbers and vehicle identification numbers (VINs) are stored as plain text in the database. If the database is ever breached, every driver's personal information is exposed.

**File to fix:** `backend/migrations/` — use Supabase Vault (column-level encryption) for `license_number` and `vehicle_vin`:
```sql
ALTER TABLE vehicles ALTER COLUMN vin SET DATA TYPE vault.encrypted_text;
```

**Effort:** 1 day (schema migration + read/write path updates)

---

## P2-6 · Expired Documents Don't Trigger Suspension — Loop Is Broken

**What's wrong:** The nightly document check has a logic error: its loop condition reads "process documents that expire in the future" (`now < expiry`). Documents that have already expired in the past are silently skipped. No suspension happens.

**File to fix:** `backend/utils/document_expiry.py` — fix the loop condition to also process expired documents:
```python
# Process: expiring within 7 days OR already expired
if now < expiry_dt + timedelta(days=1) or expiry_dt < now:
```

**Effort:** 2 hours

---

## P2-7 · Rate Limits Can Be Bypassed When Using a Load Balancer or CDN

**What's wrong:** The rate limiter checks the IP address of the direct TCP connection. When the app runs behind Cloudflare, Fly.io, or Railway (which it will in production), every request looks like it comes from the same IP address — the load balancer. Rate limits stop working entirely.

**File to fix:** `backend/utils/rate_limiter.py` — configure SlowAPI to read the real IP from the `X-Forwarded-For` header:
```python
limiter = Limiter(key_func=get_ipaddr)  # reads X-Forwarded-For
```
Also add trusted proxy IPs to the configuration.

**Effort:** 2 hours

---

## P2-8 · A Network Glitch During Payment Can Charge the Rider Twice

**What's wrong:** When creating a payment, the code doesn't include an idempotency key. If the network drops after the payment is created but before the response arrives, the app might retry — creating a second charge for the same ride.

**File to fix:** `backend/routes/payments.py` — add an idempotency key using the ride ID:
```python
stripe.PaymentIntent.create(
    ...,
    idempotency_key=f"ride-{ride_id}-{driver_id}"
)
```

**Effort:** 1 hour

---

## P2-9 · Only One Warning Sent Before Licence Expires (7 Days) — Drivers Get Caught Off Guard

**What's wrong:** Drivers receive a single notification 7 days before their licence or insurance expires. No reminder is sent 1 day before, or on the day of expiry. Many drivers miss the 7-day warning and are surprised when suspended.

**File to fix:** `backend/utils/document_expiry.py` — add notification tiers:
- 7 days before: "Your licence expires in 7 days"
- 1 day before: "Your licence expires tomorrow — renew now or you'll be suspended"
- Day of: "Your licence has expired. Upload a new one to continue driving."

**Effort:** 2 hours

---

## P2-10 · Notification Settings Are Lost When Driver Reinstalls the App

**What's wrong:** Notification preferences (e.g. "don't send ride offer sounds at night") are saved only on the device. Reinstalling the app resets everything. The backend has an endpoint to save these preferences (`PUT /notifications/preferences`) but the app never calls it.

**File to fix:** `driver-app/app/driver/settings.tsx` — when the user changes a toggle, call the API to save it:
```ts
await apiClient.put('/notifications/preferences', { key: value });
```
Also load preferences from the API when the settings screen opens.

**Effort:** 2–3 hours

---

## Checklist

- [ ] P2-1 Change all monetary fields from `float` to `Decimal`
- [ ] P2-2 Reject GPS coordinate (0,0) with 400 error
- [ ] P2-3 Fix broadcast() race condition (snapshot the connections dict)
- [ ] P2-4 Add in-memory fallback when Redis is unavailable
- [ ] P2-5 Encrypt licence numbers and VINs using Supabase Vault
- [ ] P2-6 Fix document expiry loop to process already-expired documents
- [ ] P2-7 Configure rate limiter to trust X-Forwarded-For header
- [ ] P2-8 Add idempotency keys to PaymentIntent creation
- [ ] P2-9 Add 1-day and day-of expiry notification tiers
- [ ] P2-10 Sync notification preferences to backend API
