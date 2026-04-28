# Driver App — New Issues Surfaced During Verification (2026-04-23)

**Source:** `reports/audits/2026-04-23-driver-P0-P4-verification.md` verification pass  
**Branch:** `claude/review-pending-audits-Pu1aP`  
**Total new issues:** 17 (DV-1 to DV-17)  
**These are NOT in any existing P0–P4 sprint file.** Triage into next sprint cycle.

---

## B1 · Backend / Dispatch (DV-1 to DV-4) — P1 Technical Debt

**Estimated total effort:** ~16 h · **Owner:** `backend`

---

### DV-1 · `find_nearby_drivers` Missing `status != 'suspended'` Filter

**Source finding:** Surfaced during P0-5 verification  
**What's wrong:** `backend/services/dispatch_service.py` — the `find_nearby_drivers` RPC
filters on `is_online=true AND is_available=true` but does NOT filter on
`status != 'suspended'`. A suspended driver whose `is_available` flag was not
atomically cleared could still receive ride offers.

**Why it matters:** Suspended drivers (expired documents, safety flag) could be
dispatched to riders — a regulatory and safety violation (SGI, SK-TNC).

**Blast radius:** org — affects every ride request

**File to fix:** `backend/services/dispatch_service.py` — the Supabase RPC call or
the underlying SQL function in `backend/migrations/`

**How to fix:**
```sql
-- In find_nearby_drivers RPC / migration:
WHERE d.is_online = true
  AND d.is_available = true
  AND d.status != 'suspended'   -- ADD THIS
```

**Effort:** 2 h  
**Owner:** `backend`  
**Regulations:** SAFE-CRC, SAFE-DRV, SK-TNC, SGI  
**Regression test:** Unit test: suspended driver with `is_available=true` excluded from dispatch results

---

### DV-2 · MongoDB-Style `{"$set": ...}` Wrapper on Supabase Update

**Source finding:** Surfaced during P0-5 verification  
**What's wrong:** `backend/utils/document_expiry.py:115` (and potentially other
helper calls) wraps the Supabase `.update()` payload in `{"$set": {...}}`.
Supabase-py is not MongoDB — this key is passed verbatim as a JSON column name
and the update silently writes nothing useful.

**Why it matters:** The document-expiry background loop may believe it is
suspending drivers with expired documents, but the DB row is not updated.
Expired-document drivers remain `is_available=true`.

**Blast radius:** org — document expiry compliance check is non-functional

**File to fix:** `backend/utils/document_expiry.py:115`

**How to fix:**
```python
# Before (broken):
await db.update("drivers", {"$set": {"status": "suspended"}}, ...)

# After:
await db.update("drivers", {"status": "suspended"}, ...)
```

**Effort:** 2 h (fix) + 2 h (grep all helpers for same pattern)  
**Owner:** `backend`  
**Regulations:** SAFE-DRV, SAFE-VEH, SGI, SK-TNC  
**Regression test:** Integration test: driver with expired document transitions to `suspended` after expiry loop runs

---

### DV-3 · Ride State String Mismatch — `COMPLETE_FROM_STATES` vs `trip_in_progress`

**Source finding:** Surfaced during driver-P2 verification  
**What's wrong:** `backend/routes/rides.py` uses `COMPLETE_FROM_STATES = ("in_progress",)`
but other parts of the codebase (and the WebSocket event emitter) use the string
`"trip_in_progress"`. The guard `_require_ride_in_state()` will never match
`"trip_in_progress"` against `"in_progress"`.

**Why it matters:** A driver attempting to complete a trip in `trip_in_progress`
state would receive a 409 or 400 "Invalid state" error. Trip cannot be completed
via the normal flow — potential fare loss and support escalation.

**Blast radius:** org — affects every trip completion

**File to fix:** `backend/routes/rides.py` — `COMPLETE_FROM_STATES` constant; also
grep `backend/` for all state string literals to build a canonical enum.

**How to fix:**
```python
# Audit: run
# grep -rn "trip_in_progress\|in_progress\|TRIP_IN_PROGRESS" backend/ --include="*.py"
# Consolidate to a single RideStatus enum; replace all string literals.

class RideStatus(str, Enum):
    SEARCHING = "searching"
    DRIVER_ASSIGNED = "driver_assigned"
    DRIVER_ARRIVING = "driver_arriving"
    TRIP_IN_PROGRESS = "trip_in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
```

**Effort:** 4 h  
**Owner:** `backend`  
**Regulations:** SK-CPPA  
**Regression test:** State-machine unit test covering all valid transitions using the enum

---

### DV-4 · Stripe Idempotency Key Collision on Same-Amount Retry

**Source finding:** Surfaced during driver-P2 verification  
**What's wrong:** Fallback Stripe idempotency key is constructed as
`"intent-{user_id}-{amount}"`. If a rider legitimately makes two separate rides
of the same fare within 24 hours, the second PaymentIntent creation is silently
de-duplicated by Stripe and returns the first intent — potentially with the
wrong ride metadata.

**Why it matters:** Second ride may be charged against the first ride's intent;
or Stripe rejects the second with a metadata mismatch. Either way the rider is
charged incorrectly.

**Blast radius:** self (per user), but PCI-DSS reporting scope

**File to fix:** `backend/routes/payments.py` — idempotency key generation

**How to fix:**
```python
# Before (collision-prone):
idempotency_key = f"intent-{user_id}-{amount}"

# After (UUID client token, passed from mobile on each new ride request):
idempotency_key = ride_request.client_idempotency_token  # UUID v4 from mobile
# Fallback if not provided:
idempotency_key = str(uuid.uuid4())
```

**Effort:** 3 h (backend) + 1 h (mobile sends UUID on ride request)  
**Owner:** `backend`  
**Regulations:** PCI-DSS  
**Regression test:** Two rides at same fare within 24 h produce distinct PaymentIntents

---

## B2 · Notifications / Rate Limiter (DV-5 to DV-6) — P3 / P2 Ops

**Estimated total effort:** ~5 h · **Owners:** `driver-app`, `devops`

---

### DV-5 · P2-10 Notification Preference Update Swallows 4xx/5xx

**Source finding:** Surfaced during driver-P2 verification (related to P2-10)  
**What's wrong:** `driver-app/` — the notification preference PUT call is fire-and-forget.
A 4xx or 5xx from the server is silently ignored, leaving the UI showing a
preference state that does not match the backend.

**Why it matters:** Driver believes their notification preference was saved; in
reality it was rejected. Next session the preference resets, causing driver
confusion and potential CASL unsubscribe violations.

**Blast radius:** self — per driver

**File to fix:** Notification settings screen / store action that calls
`PUT /drivers/notification-preferences`

**How to fix:**
```typescript
const res = await api.put('/drivers/notification-preferences', prefs);
if (!res.ok) {
  Toast.show({ type: 'error', text1: 'Settings not saved — please retry' });
  // Revert local state to previous value
}
```

**Effort:** 2 h  
**Owner:** `driver-app`  
**Regulations:** CASL  
**Regression test:** Mock 500 response → toast appears, local state reverted

---

### DV-6 · Rate Limiter In-Memory Fallback Is Per-Process; No SRE Alert

**Source finding:** Surfaced during driver-P2 verification (related to P2-4)  
**What's wrong:** `backend/utils/rate_limiter.py` — when Redis is unavailable the limiter
falls back to an in-process dict. On multi-replica deployments the effective rate
limit is `N × limit` across the fleet with no alert emitted.

**Why it matters:** OTP brute-force protection degrades silently. An attacker
hitting all replicas gets N× the intended attempt budget.

**Blast radius:** org — rate limits protect all users

**File to fix:** `backend/utils/rate_limiter.py`

**How to fix:**
1. On Redis fallback emit `logger.error("Redis unavailable — rate limiter degraded")` so SRE is paged.
2. Add a `/healthz` Redis check.
3. Optionally fail closed (return 429) if Redis is down > 30s.

**Effort:** 3 h  
**Owner:** `devops` + `backend`  
**Regulations:** PIPEDA  
**Regression test:** With Redis mocked-down, rate limiter degraded-mode alert fires within 1 request

---

## B3 · Secrets / Retention / Notifications (DV-7 to DV-9) — P2 Compliance / Ops

**Estimated total effort:** ~12 h · **Owners:** `infra`, `data`, `driver-app`

---

### DV-7 · pgsodium `drivers_pii_key` Rotation Cadence Undocumented

**Source finding:** Surfaced during driver-P3 verification  
**What's wrong:** Supabase pgsodium is used to encrypt driver PII columns, but there
is no documented key-rotation cadence, no runbook, and no evidence of a rotation
ever having occurred.

**Why it matters:** SOC2 Trust Services Criteria require documented key management
including rotation frequency and evidence of execution. Undocumented rotation also
means a compromised key could expose all historical PII with no detection timeline.

**Blast radius:** org — all encrypted driver PII at risk if key is compromised

**File to fix:** `docs/runbooks/pii-key-rotation.md` (create) + `backend/migrations/` (key
provisioning migration)

**How to fix:**
1. Document rotation cadence (recommendation: annual + on any suspected compromise).
2. Add a `key_version` column to `drivers` table so rows can be re-encrypted incrementally.
3. Create a runbook: `docs/runbooks/pii-key-rotation.md`.
4. Add a CI check that fails if `key_version < current_version` for > 5% of rows.

**Effort:** 4 h  
**Owner:** `infra` + `compliance`  
**Regulations:** SOC2, PIPEDA  
**Regression test:** Rotation runbook executed in staging without data loss

---

### DV-8 · Soft-Delete Has No Scheduled Hard-Delete at PIPEDA / CRA Horizon

**Source finding:** Surfaced during driver-P3 verification (related to P3-8)  
**What's wrong:** `backend/` — drivers and rides are soft-deleted (`deleted_at` set)
but there is no cron or background task to hard-delete rows after the PIPEDA
retention window (7 years for CRA T4A records; 2 years for non-financial personal data).

**Why it matters:** PIPEDA s.5(3) requires data to be destroyed when no longer
needed. Indefinite retention of rider/driver PII beyond the retention horizon
creates regulatory exposure.

**Blast radius:** regulator — PIPEDA / OPC investigation risk

**File to fix:** `backend/core/lifespan.py` (add purge loop) + new migration

**How to fix:**
```python
# New background loop in lifespan.py:
async def scheduled_pii_purge():
    """Hard-delete rows where deleted_at < now() - retention_window."""
    while True:
        await purge_expired_records()   # new DB helper
        await asyncio.sleep(86400)      # run daily
```

**Effort:** 5 h  
**Owner:** `data` + `backend`  
**Regulations:** PIPEDA, CRA  
**Regression test:** Row with `deleted_at` > retention horizon is hard-deleted; row within
horizon is not touched

---

### DV-9 · Notification Deep-Link Has No Fallback for Unknown Types

**Source finding:** Surfaced during driver-P3 verification (related to P3-9)  
**What's wrong:** `driver-app/` — the notification deep-link handler switches on
`notification.data.type` but has no `default` case. An unknown type (new backend
feature or typo) is silently ignored; driver taps the notification and nothing
happens.

**Why it matters:** Silent no-ops erode trust in the app. Worse, a safety
notification (SOS update, document expiry) may be delivered but not navigated to.

**Blast radius:** self — per driver

**File to fix:** `driver-app/app/_layout.tsx` or notification handler hook

**How to fix:**
```typescript
switch (notification.data.type) {
  case 'ride_offer': navigate('/driver/'); break;
  case 'document_expiry': navigate('/driver/profile'); break;
  // ...
  default:
    logger.warn('Unknown notification type', { type: notification.data.type });
    navigate('/driver/notifications');  // fallback to notifications list
}
```

**Effort:** 2 h  
**Owner:** `driver-app`  
**Regulations:** —  
**Regression test:** Unknown notification type → navigates to `/driver/notifications`

---

## B4 · Auth / Frontend Layout Drift / Product-Decision Drift (DV-10 to DV-15) — Documentation Sync

**Estimated total effort:** ~8 h · **Owners:** `backend`, `driver-app`, `product`

---

### DV-10 · Firebase Token Does Not Enforce App Audience (Driver vs Rider)

**Source finding:** Surfaced during driver-P3 verification; shared with rider-P1-12  
**What's wrong:** `backend/routes/auth.py:402-403` — `_firebase_auth.verify_id_token(body.firebase_token)`
is called without an `audience=` parameter. A rider-issued Firebase ID token is
accepted by the driver auth path and vice versa.

**Why it matters:** Cross-app token acceptance allows a rider account to
authenticate as a driver (or vice versa), bypassing role separation.

**Blast radius:** org — auth trust boundary broken

**File to fix:** `backend/routes/auth.py:402`

**How to fix:**
```python
# Before:
decoded = _firebase_auth.verify_id_token(body.firebase_token)

# After — driver path:
decoded = _firebase_auth.verify_id_token(
    body.firebase_token,
    check_revoked=True,
    audience=settings.FIREBASE_DRIVER_APP_ID,
)
# Similarly, rider path uses settings.FIREBASE_RIDER_APP_ID
```

**Effort:** 4 h (backend + Firebase console config)  
**Owner:** `backend`  
**Regulations:** PIPEDA  
**Regression test:** Rider Firebase token rejected by driver auth endpoint (and vice versa)

---

### DV-11 · Stale Remediation Text — Panel Components Moved to `components/dashboard/`

**Source finding:** Surfaced during driver-P1/P2 verification  
**What's wrong:** Several driver-app remediation items reference
`components/panels/ActiveRidePanel.tsx` and `components/panels/TripCompletedPanel.tsx`
but these files have moved to `components/dashboard/`. The remediation text is
stale.

**Why it matters:** An engineer following the remediation item will look in the
wrong directory, fail to find the file, and either give up or make changes in the
wrong location.

**Blast radius:** self — documentation-only

**Files to fix:** `reports/remediation/P1-before-beta.md`, `reports/remediation/P2-before-launch.md`

**How to fix:**
Update any `components/panels/` references → `components/dashboard/` in both remediation files.
Add a note: "If a referenced file is not found at the listed path, search for it
with `find driver-app -name '<filename>'` — layout reorganisation may have moved it."

**Effort:** 1 h  
**Owner:** `driver-app`  
**Regulations:** —  
**Regression test:** —

---

### DV-12 · `report-safety.tsx` and `legal.tsx` at App Root — Nav Wiring Unconfirmed

**Source finding:** Surfaced during driver-P4 verification  
**What's wrong:** `driver-app/app/report-safety.tsx` and `driver-app/app/legal.tsx`
exist at the root `app/` level rather than under `app/driver/`. It is unclear
whether they are reachable from the driver dashboard navigation or are orphaned
screens.

**Why it matters:** `legal.tsx` is required for App Store submission (privacy policy
link must be in-app). `report-safety.tsx` is a safety-critical path. Both must be
navigable.

**Blast radius:** self — App Store compliance if legal.tsx is unreachable

**File to fix:** `driver-app/app/_layout.tsx` — confirm nav wiring

**How to fix:**
1. Grep for `report-safety` and `legal` in `driver-app/app/` to confirm they are
   linked from the bottom-tab or settings screen.
2. If orphaned, add a link from the settings/profile screen.

**Effort:** 1 h  
**Owner:** `driver-app`  
**Regulations:** PIPEDA (privacy policy reachability)  
**Regression test:** Navigation test: legal screen is reachable from settings

---

### DV-13 · P1-1 Cancellation — $5 Fee Implemented (Not Hard Block); Update Sprint File

**Source finding:** Product-decision drift discovered during driver-P1 verification  
**What's wrong:** `driver-app/` and `backend/` — the P1-1 remediation item specifies
a "hard block" on cancellation after driver arrival. The implementation uses a $5
cancellation fee instead. This is a valid product decision but the sprint file
does not reflect it.

**Why it matters:** The sprint file says DONE but describes a feature that was
not built as specified. Future auditors will be confused. Also, the $5 fee amount
should be configurable (not hard-coded) per SK-CPPA disclosure requirements.

**Blast radius:** self — documentation only; verify fee is configurable

**Files to fix:** `reports/remediation/P1-before-beta.md` (update description)

**How to fix:**
Update P1-1 to read: "Cancellation fee ($5) applied when driver has arrived —
hard block not implemented per product decision [date]."
Verify the $5 amount comes from `app_settings` table (not hard-coded).

**Effort:** 30 min  
**Owner:** `product`  
**Regulations:** SK-CPPA  
**Regression test:** —

---

### DV-14 · P1-10 Native Keypad in Use — Remove Custom-Keypad Checklist Item

**Source finding:** Product-decision drift discovered during driver-P1 verification  
**What's wrong:** P1-10 in the sprint file tracked "replace custom OTP keypad with
native keypad for accessibility". The native keypad is already in use. The item
should be marked SUPERSEDED with a note.

**Blast radius:** self — documentation only

**Files to fix:** `reports/remediation/P1-before-beta.md`

**How to fix:** Mark P1-10 as `SUPERSEDED — native keypad confirmed in use at HEAD`.

**Effort:** 15 min  
**Owner:** `product`  
**Regulations:** —  
**Regression test:** —

---

### DV-15 · P4-5 E2E Framework Changed — Update Sprint File (Maestro → Playwright-style)

**Source finding:** Product-decision drift discovered during driver-P4 verification  
**What's wrong:** `reports/remediation/P4-future-features.md` still references
Maestro as the E2E framework. The implementation uses a Playwright-style framework.

**Files to fix:** `reports/remediation/P4-future-features.md`

**How to fix:** Replace "Maestro" → "Playwright-style E2E (see `driver-app/e2e/`)"
in the P4-5 item.

**Effort:** 15 min  
**Owner:** `product`  
**Regulations:** —  
**Regression test:** —

---

## B5 · Compliance Follow-Ups (DV-16 to DV-17) — P2 Legal / Compliance

**Estimated total effort:** ~6 h · **Owners:** `legal`, `backend` + `compliance`

---

### DV-16 · Gemini Cross-Border Disclosure Missing from Privacy Policy

**Source finding:** Surfaced during driver-P4 verification (related to P4-1)  
**What's wrong:** The Spinr privacy policy does not list Google Gemini as a
sub-processor. Gemini processes user-provided trip/support text in the US. Under
PIPEDA, cross-border transfers to US processors must be disclosed.

**Why it matters:** PIPEDA s.4.1.3 and OPC guidance require disclosure of
cross-border data flows and the foreign country to which data is transferred.
Non-disclosure is an OPC complaint risk at launch.

**Blast radius:** regulator — OPC complaint risk

**File to fix:** Privacy policy document (external — `docs/legal/privacy-policy.md`
or hosted URL) + `docs/vendor-inventory.md` (add Gemini row)

**How to fix:**
1. Add Gemini to the sub-processors table in the privacy policy: "Google Gemini
   (Google LLC, United States) — AI text processing for in-app support features."
2. Add Gemini to `docs/vendor-inventory.md` with DPA status, data categories
   processed, and transfer mechanism (Google's SCCs / adequacy decision).

**Effort:** 2 h  
**Owner:** `legal`  
**Regulations:** PIPEDA, CPPA  
**Regression test:** Privacy policy URL live and contains "Gemini" before launch date

---

### DV-17 · DSAR Endpoint Exists but 30-Day SLA and Audit Log Not Enforced

**Source finding:** Surfaced during driver-P4 verification (related to P4-6)  
**What's wrong:** `backend/routes/` — a DSAR (Data Subject Access Request) endpoint
exists and is marked DONE in P4-6. However:
- No audit-log row is emitted when a DSAR is submitted.
- No workflow enforces the 30-day PIPEDA response SLA.
- No SRE/compliance alert fires if a request goes unanswered for 25 days.

**Why it matters:** PIPEDA s.9 requires response within 30 days. Missing an SLA
triggers regulatory consequences. Without an audit log, Spinr cannot prove
compliance to the OPC.

**Blast radius:** regulator — PIPEDA / OPC investigation risk

**File to fix:** DSAR endpoint in `backend/routes/` + `backend/utils/audit_logger.py`

**How to fix:**
1. On DSAR submission: `audit_logger.log("dsar_submitted", user_id=..., deadline=now()+30days)`.
2. Add a background check loop: DSARs approaching day 25 → email compliance team.
3. Add a `/admin/dsars` endpoint so compliance can track open requests.

**Effort:** 4 h  
**Owner:** `backend` + `compliance`  
**Regulations:** PIPEDA  
**Regression test:** DSAR submission creates audit-log row; day-25 alert fires in test harness

---

## Triage Checklist

| ID | Area | Recommended Sprint | Effort | Owner | Blocker for | Status |
|---|---|---|---:|---|---|---|
| DV-1 | Dispatch suspended filter | P1 | 2 h | backend | device test | ✅ Already fixed — `status: "active"` filter at dispatch_service.py:198 |
| DV-2 | `{"$set"}` Supabase wrapper | P1 | 4 h | backend | device test | ✅ Already handled — `db_supabase.update_one:1145` strips `$set` wrapper; `db.py` is a shim |
| DV-3 | Ride state string mismatch | P1 | 4 h | backend | beta | ✅ Fixed — drivers.py:2270 `"trip_in_progress"` → `RideStatus.IN_PROGRESS` (also blocks COMPLETED) |
| DV-4 | Stripe idempotency collision | P1 | 4 h | backend | launch | ⬜ Open |
| DV-5 | Notification pref swallows error | P3 | 2 h | driver-app | — | ⬜ Open |
| DV-6 | Rate limiter no SRE alert | P2 | 3 h | devops+backend | launch | ⬜ Open |
| DV-7 | PII key rotation undocumented | P2 | 4 h | infra+compliance | launch | ⬜ Open |
| DV-8 | No hard-delete at retention horizon | P2 | 5 h | data+backend | launch | ⬜ Open |
| DV-9 | Notification deep-link no fallback | P3 | 2 h | driver-app | — | ⬜ Open |
| DV-10 | Firebase audience not enforced | P1 | 4 h | backend | beta | ✅ Partially fixed — manual `aud` check already present (B-P1-1); added `check_revoked=True` to auth.py:415 |
| DV-11 | Stale panel component paths | P3 | 1 h | driver-app | — | ⬜ Open |
| DV-12 | report-safety / legal nav wiring | P2 | 1 h | driver-app | launch | ⬜ Open |
| DV-13 | P1-1 fee vs hard-block drift | P3 | 0.5 h | product | — | ⬜ Open |
| DV-14 | P1-10 native keypad drift | P3 | 0.25 h | product | — | ⬜ Open |
| DV-15 | P4-5 Maestro → Playwright drift | P3 | 0.25 h | product | — | ⬜ Open |
| DV-16 | Gemini sub-processor missing | P2 | 2 h | legal | launch |
| DV-17 | DSAR SLA + audit log | P2 | 4 h | backend+compliance | launch |

**Total estimated effort:** ~43 h  
**P1 blockers (beta):** DV-1, DV-2, DV-3, DV-10 — 14 h combined  
**P2 (launch):** DV-4, DV-6, DV-7, DV-8, DV-12, DV-16, DV-17 — 23 h combined

---
