# P3 — Hardening: Once the App Is Stable

These 10 items improve the quality, safety, and reliability of the app. They don't block launch but should be done in the first sprint after going live.

**Estimated total effort:** ~5–7 days

---

## P3-1 · Security Tests Only Check 2 Out of 14 Protected Fields

**What's wrong:** There is a list of 14 sensitive driver fields (like licence number, bank account, VIN) that should never be shown to riders. The automated test only checks 2 of them. The other 12 could be leaking and the tests would pass.

**File to fix:** `backend/tests/test_ride_pii.py` — use `@pytest.mark.parametrize` to test all 14 fields automatically:
```python
@pytest.mark.parametrize("field", FORBIDDEN_FIELDS)
def test_field_not_in_response(field, ...):
    assert field not in response
```

**Effort:** 1 hour

---

## P3-2 · No Test Proves Two Drivers Can't Accept the Same Ride

**What's wrong:** Even after fixing the double-accept race condition (P0-3), there is no automated test to verify it works. Without a test, the fix could be accidentally broken in a future change.

**File to fix:** `backend/tests/test_rides.py` — add a concurrent acceptance test that fires two requests simultaneously and verifies only one succeeds.

**Effort:** 2 hours

---

## P3-3 · The OTP Lockout Has Never Been Integration-Tested

**What's wrong:** The test for "5 failed OTP attempts = 24-hour lockout" only mocks Redis — it doesn't actually count real failures and trigger the real lockout. The real mechanism could be broken.

**File to fix:** `backend/tests/test_auth.py` — write a test that makes 5 real OTP verification requests using incorrect codes and confirms the 6th gets a `429 Too Many Requests` response.

**Effort:** 2 hours

---

## P3-4 · No Minimum Test Coverage Requirement

**What's wrong:** The test suite has no minimum coverage threshold. A developer can delete 50 tests and the CI pipeline will still pass. Coverage could drop to zero without anyone noticing.

**File to fix:** `driver-app/jest.config.js` — add a coverage threshold:
```js
coverageThreshold: {
    global: { lines: 70, functions: 60, statements: 70 }
}
```
Do the same for the backend with `pytest --cov --cov-fail-under=70`.

**Effort:** 1 hour (plus writing tests to meet threshold)

---

## P3-5 · Errors Don't Include a Trace ID — Hard to Debug in Production

**What's wrong:** When an error occurs, the response doesn't include a `request_id` that links the error the driver sees to the log entry on the server. Support can't trace a specific complaint to a specific log line.

**File to fix:** `backend/utils/error_handling.py` — the `spinr_exception_handler` function. Add the request ID to every error response:
```python
return JSONResponse({"error": str(exc), "request_id": request.state.request_id})
```

**Effort:** 1 hour

---

## P3-6 · Database Timeouts Are Not Retried

**What's wrong:** The code retries database calls that fail due to network protocol errors (HTTP/2 crashes) but not regular network timeouts. If the database is slow and a request times out, it fails immediately with no retry.

**File to fix:** `backend/db_supabase.py` — `run_sync()` function. Add `httpx.TimeoutException` to the retry clause alongside the existing HTTP/2 error.

**Effort:** 30 minutes

---

## P3-7 · Emergency SOS Button Takes 2 Seconds to Activate — Too Long

**What's wrong:** The SOS button requires a 2-second press to activate. In a real emergency, 2 seconds feels like a very long time. Industry standard for panic buttons is 1–1.2 seconds.

**File to fix:** `shared/components/SOSButton.tsx` — change the press threshold:
```tsx
const SOS_HOLD_MS = 1200; // was 2000
```

**Effort:** 5 minutes

---

## P3-8 · Deleted Accounts Leave No Audit Trail

**What's wrong:** When a driver or ride record is deleted from the database, it's gone permanently. There is no way to investigate complaints, refund disputes, or meet privacy law requirements (which require knowing what data was held) after deletion.

**File to fix:** `backend/migrations/` — add a `deleted_at` timestamp column to `drivers`, `users`, and `rides`. When "deleting", set `deleted_at = NOW()` instead of using `DELETE FROM`.

**Effort:** 1 day (schema migration + update all delete operations)

---

## P3-9 · Tapping a Notification Does Nothing Except Mark It Read

**What's wrong:** When a driver taps a notification (e.g. "your document is expiring"), the app marks it as read but doesn't navigate anywhere. The driver has to find the relevant screen themselves.

**File to fix:** `driver-app/app/driver/notifications.tsx` — in the tap handler, add routing based on notification type:
```ts
if (notification.type === 'document_expiry') router.push('/driver/documents');
if (notification.type === 'payout_processed') router.push('/driver/earnings');
```

**Effort:** 2 hours

---

## P3-10 · Firebase-Authenticated Drivers Don't Get a Refresh Token

**What's wrong:** Drivers who sign in using Firebase (Google/Apple Sign-In) receive a session token but not a refresh token. When their session expires, they have to log in again from scratch instead of the app silently refreshing in the background.

**File to fix:** `backend/routes/auth.py` — the Firebase verification path. After verifying the Firebase token, issue a Spinr refresh token the same way the OTP path does.

**Effort:** 2 hours

---

## Checklist

- [ ] P3-1 Parametrize PII test to cover all 14 forbidden fields
- [ ] P3-2 Add concurrent double-accept integration test
- [ ] P3-3 Write real OTP lockout integration test (5 failures → 429)
- [ ] P3-4 Add Jest and pytest coverage thresholds (70% minimum)
- [ ] P3-5 Add request_id to all error responses
- [ ] P3-6 Retry database calls on httpx.TimeoutException
- [ ] P3-7 Reduce SOS long-press from 2000ms to 1200ms
- [ ] P3-8 Add soft-delete (deleted_at) columns to drivers, users, rides
- [ ] P3-9 Add deeplink navigation to notification tap handlers
- [ ] P3-10 Issue refresh token for Firebase-authenticated drivers
