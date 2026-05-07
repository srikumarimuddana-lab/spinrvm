# Driver App — P0 Remediation Verification

**Date:** 2026-04-23
**Branch:** `claude/review-pending-audits-Pu1aP`
**Sprint:** P0 — Critical: Fix Before Any Device Testing
**Source sprint file:** `reports/remediation/P0-critical-fix-now.md`
**Source audit:** `reports/audits/2026-04-18-driver-app-production-readiness-v4.txt`
**Items verified:** 7
**Method:** Static inspection at HEAD (no runtime probe).

---

### P0-1 · App Will Crash at Login — Wrong Database Field Name

**Status:** `DONE`
**Source finding:** `[2-3]`
**Evidence:**
- `backend/utils/refresh_tokens.py:131` — `if row.get("revoked_at"):`
- `backend/utils/refresh_tokens.py:167` — `if not row or row.get("revoked_at"):`
- `backend/utils/refresh_tokens.py:202` — `if row.get("revoked_at"):`
- `backend/utils/refresh_tokens.py:102, 173, 208` — writes `revoked_at` (not `revoked`).
- `backend/routes/auth.py:639` — `revoked = await revoke_all_for_user(...)` is a local Python variable, not a DB field.
- Grep for bare `"revoked"` as a DB key in rider-app/driver-app path: 0 hits.
**Reason:** Field is consistently read and written as `revoked_at`. No legacy `revoked` field reference remains.
**Owner:** —
**Confidence:** high
**Regulations:** PIPEDA (session management)
**Effort remaining:** 0 h

---

### P0-2 · App Will Crash — Broken `refresh_token` Function

**Status:** `DONE`
**Source finding:** `[2-4]`
**Evidence:**
- `backend/routes/auth.py` file length: 641 lines.
- `grep -n "_store_refresh_token" backend/routes/auth.py` → 0 hits.
- Only one refresh handler remains: `async def refresh_access_token(...)` at line 542.
- No duplicate `def refresh_token` anywhere in the file.
**Reason:** Dead old function and its call to the undefined `_store_refresh_token` helper have both been removed. Single-source refresh path.
**Owner:** —
**Confidence:** high
**Regulations:** PIPEDA
**Effort remaining:** 0 h

---

### P0-3 · Two Drivers Can Accept the Same Ride at the Same Time

**Status:** `DONE`
**Source finding:** `[7-1]` (state-machine / dispatch race)
**Evidence:**
- `backend/db_supabase.py:425-465` — `claim_ride_atomic(ride_id, driver_id)`:
  ```python
  supabase.table("rides").update({...})
    .eq("id", ride_id)
    .in_("status", ["searching", "driver_assigned"])
    .or_(...)
  ```
  Docstring (lines 426-439) explicitly describes the race-prevention contract:
  "two drivers racing to accept the same offer cannot both succeed — the
  loser's UPDATE matches zero rows and this function returns False."
- `backend/routes/drivers.py:1714` — `async def accept_ride(...)` invokes the atomic claim.
**Reason:** PostgREST conditional UPDATE evaluates all filters atomically in one SQL statement. Zero-row result → returns False → caller surfaces "ride already taken".
**Owner:** —
**Confidence:** high
**Regulations:** SK-CPPA (consumer fair dealing), PIPEDA
**Effort remaining:** 0 h
**Note:** No regression test was located that simulates two concurrent `accept_ride` calls. This is logged as an open gap under P3-2 ("No Test Proves Two Drivers Can't Accept the Same Ride") — verify separately in P3 sprint.

---

### P0-4 · Pickup Code Stored as Plain Text

**Status:** `DONE`
**Source finding:** `[3-1]`
**Evidence:**
- `backend/utils/crypto.py:9-22` — `hash_otp(code)` returns `hashlib.sha256(code.encode()).hexdigest()`.
- `backend/routes/rides.py:25, 40` — `from ..utils.crypto import hash_otp` (+ sibling fallback).
- `backend/routes/rides.py:872` — `pickup_otp=hash_otp(pickup_otp_plain)` on ride create.
- `backend/utils/crypto.py:29` — `verify_otp()` compares `hashlib.sha256(input_otp.encode()).hexdigest()` against stored value.
**Reason:** OTP is hashed on write, hashed on compare. Plaintext never persisted.
**Owner:** —
**Confidence:** high
**Regulations:** PIPEDA, PCI-DSS-adjacent (pickup code is auth factor)
**Effort remaining:** 0 h
**Note:** Constant-time comparison not verified — see rider-P1-14 ("OTP Comparison Not Constant-Time") for the related open finding.

---

### P0-5 · Driver With Expired Licence Can Keep Working — No Automatic Suspension

**Status:** `PARTIAL`
**Source finding:** `[12-3]` (document expiry)
**Evidence (what's in place):**
- `backend/utils/document_expiry.py:115` — update payload contains
  `{"is_online": False, "is_available": False, "status": "suspended"}`.
- `backend/sql/01_postgis_schema.sql:60-61` — `find_nearby_drivers` RPC filters
  `WHERE is_online = true AND is_available = true`. Because the loop sets both
  flags to `False`, an expired driver IS excluded from dispatch via those flags.
- `backend/utils/document_expiry.py:124-125` — in-app notification sent.
**Evidence (what's missing):**
- Line 115 payload is wrapped in MongoDB-style `{"$set": {...}}` syntax. This is
  the same class of bug called out in `[2-2]` ("MongoDB-style DB calls will
  crash with AttributeError"). Supabase's Python client does not unwrap `$set`;
  the key is treated as a literal column name and the UPDATE either no-ops or
  errors. **Needs runtime confirmation.**
- No filter on `status = 'suspended'` in the nearby-drivers RPC. If any code
  path flips `is_online` back to `true` without clearing the suspension, the
  expired driver re-enters the dispatch pool. Defense-in-depth gap.
- The remediation item also asks to "fix the loop condition — it currently
  skips documents that are already past their expiry date." Loop logic at
  `document_expiry.py:24-130` was not re-examined for this specific
  window-comparison bug.
- No regression test found under `backend/tests/` that exercises the
  "expired-driver-cannot-be-dispatched" path.
**Reason:** Two of four sub-fixes landed (suspension flag + notification).
The Supabase-vs-Mongo syntax on the update may prevent the suspension from
ever actually persisting, and the RPC does not enforce `status != 'suspended'`.
No regression test.
**Owner:** `backend`
**Blocked by:** —
**Confidence:** high (static) · the Mongo-vs-Supabase syntax claim is low
confidence until a runtime probe confirms — see UNVERIFIED check below.
**Regulations:** SAFE-CRC, SAFE-DRV, SAFE-VEH, SGI, SK-TNC
**Effort remaining:** 3-4 h
**Action:**
1. Replace `{"$set": {...}}` with a flat dict on the Supabase `.update(...)` call.
2. Add `AND status != 'suspended'` (or equivalent) to `find_nearby_drivers`
   RPC for defense-in-depth.
3. Re-check loop bounds at lines 24-130 for the "already past expiry" skip bug.
4. Add `backend/tests/test_document_expiry.py::test_expired_driver_not_dispatched`.

---

### P0-6 · Driver Licence Photos Publicly Accessible — No Login Required

**Status:** `DONE`
**Source finding:** `[12-2]`
**Evidence:**
- `backend/documents.py:227-258` — `_extract_signed_url()` helper handles
  Supabase's `create_signed_url()` response shape variants.
- `backend/documents.py:263` — `supabase.storage.from_("driver-documents").create_signed_url(filename, 3600)`.
- `backend/documents.py:877, 883` — upload path also returns
  `_extract_signed_url(signed_res)` (1-hour signed URL).
- `grep get_public_url` → 0 hits for sensitive driver paths.
**Reason:** All driver-document URLs are 1-hour signed URLs. No permanent
public URL path remains for licence / insurance / background-check uploads.
**Owner:** —
**Confidence:** high
**Regulations:** PIPEDA (PII), SAFE-CRC, SAFE-DRV
**Effort remaining:** 0 h
**Note:** Verify in P1/P2 that the frontend re-fetches signed URLs on expiry
(1 h is tight for long admin review sessions).

---

### P0-7 · "Offline" Banner Hidden Behind Status Bar

**Status:** `DONE`
**Source finding:** `[5-1]`
**Evidence:**
- `shared/components/OfflineBanner.tsx:4` — `import { useSafeAreaInsets } from 'react-native-safe-area-context';`
- `shared/components/OfflineBanner.tsx:36` — `const insets = useSafeAreaInsets();`
- `shared/components/OfflineBanner.tsx:171` — `top: topInset`.
**Reason:** Banner position uses `insets.top`, so it renders below the notch /
status bar on both platforms.
**Owner:** —
**Confidence:** high
**Regulations:** WCAG (visibility)
**Effort remaining:** 0 h
**Note:** Carry-over PASS confirmed in rider-app-v1.txt baseline.

---

```yaml
===VERIFICATION-YAML===
- id: P0-1
  source_finding: "2-3"
  status: DONE
  evidence:
    file: backend/utils/refresh_tokens.py
    lines: [102, 131, 167, 173, 202, 208]
    snippet: "if row.get(\"revoked_at\"):"
    test_file: null
    test_lines: null
  reason: "Consistent revoked_at usage on read and write; no legacy revoked field."
  owner: null
  blocked_by: null
  confidence: high
  regulations: [PIPEDA]
  effort_remaining_hours: 0
  notes: null
- id: P0-2
  source_finding: "2-4"
  status: DONE
  evidence:
    file: backend/routes/auth.py
    lines: [542]
    snippet: "async def refresh_access_token(request: Request, body: RefreshRequest):"
    test_file: null
    test_lines: null
  reason: "Dead old function + _store_refresh_token removed; one handler remains."
  owner: null
  blocked_by: null
  confidence: high
  regulations: [PIPEDA]
  effort_remaining_hours: 0
  notes: "File length 641 lines; grep _store_refresh_token returns 0 hits."
- id: P0-3
  source_finding: "7-1"
  status: DONE
  evidence:
    file: backend/db_supabase.py
    lines: [425, 448, 460, 465]
    snippet: ".update({...}).eq(\"id\", ride_id).in_(\"status\", [\"searching\", \"driver_assigned\"])"
    test_file: null
    test_lines: null
  reason: "Conditional UPDATE with status filter — loser sees 0 rows updated."
  owner: null
  blocked_by: null
  confidence: high
  regulations: [SK-CPPA, PIPEDA]
  effort_remaining_hours: 0
  notes: "Regression test for concurrent accept is open (P3-2)."
- id: P0-4
  source_finding: "3-1"
  status: DONE
  evidence:
    file: backend/routes/rides.py
    lines: [25, 40, 872]
    snippet: "pickup_otp=hash_otp(pickup_otp_plain)"
    test_file: null
    test_lines: null
  reason: "SHA-256 hashed on write and on verify. Plaintext never persisted."
  owner: null
  blocked_by: null
  confidence: high
  regulations: [PIPEDA]
  effort_remaining_hours: 0
  notes: "Constant-time compare is a separate open item (rider-P1-14)."
- id: P0-5
  source_finding: "12-3"
  status: PARTIAL
  evidence:
    file: backend/utils/document_expiry.py
    lines: [115, 124]
    snippet: "{\"$set\": {\"is_online\": False, \"is_available\": False, \"status\": \"suspended\"}}"
    test_file: null
    test_lines: null
  reason: "Suspension flags set, but (a) MongoDB-style $set wrapping may not apply on Supabase; (b) RPC only filters is_online/is_available, not status='suspended'; (c) loop-condition bug and regression test still open."
  owner: backend
  blocked_by: null
  confidence: high
  regulations: [SAFE-CRC, SAFE-DRV, SAFE-VEH, SGI, SK-TNC]
  effort_remaining_hours: 4
  notes: "$set syntax needs runtime probe; add status filter to find_nearby_drivers RPC; add regression test."
- id: P0-6
  source_finding: "12-2"
  status: DONE
  evidence:
    file: backend/documents.py
    lines: [263, 877, 883]
    snippet: "create_signed_url(filename, 3600)"
    test_file: null
    test_lines: null
  reason: "All driver-document URLs are 1-hour signed; no get_public_url on sensitive paths."
  owner: null
  blocked_by: null
  confidence: high
  regulations: [PIPEDA, SAFE-CRC, SAFE-DRV]
  effort_remaining_hours: 0
  notes: "Verify client re-fetches on 1-hour expiry during long admin review."
- id: P0-7
  source_finding: "5-1"
  status: DONE
  evidence:
    file: shared/components/OfflineBanner.tsx
    lines: [4, 36, 171]
    snippet: "top: topInset"
    test_file: null
    test_lines: null
  reason: "useSafeAreaInsets applied for top position on both platforms."
  owner: null
  blocked_by: null
  confidence: high
  regulations: [WCAG]
  effort_remaining_hours: 0
  notes: null
===END-VERIFICATION-YAML===

===AUDIT-COMPLETE=== sprint=P0 items=7 done=6 partial=1 pending=0 blocked=0 unverifiable=0 superseded=0
```

---

## Summary

| Status | Count | IDs |
|---|---|---|
| DONE | 6 | P0-1, P0-2, P0-3, P0-4, P0-6, P0-7 |
| PARTIAL | 1 | P0-5 |
| PENDING | 0 | — |
| BLOCKED | 0 | — |
| UNVERIFIABLE | 0 | — |
| SUPERSEDED | 0 | — |

**Open P0 effort:** ~4 h, 1 owner (`backend`).
**Single action needed before device testing:** finish P0-5 — replace `$set` wrapper, add RPC status filter, fix loop condition, add regression test.

## New issues discovered (not in scope for this verification)

1. `find_nearby_drivers` RPC has no `status != 'suspended'` guard — it only filters `is_online` and `is_available`. Suspended drivers who re-flip those flags (via go-online endpoint path) would be dispatched. Defense-in-depth gap.
2. `backend/utils/document_expiry.py:115` uses MongoDB-style `{"$set": ...}` update syntax against Supabase client — same anti-pattern as source finding `[2-2]`. Worth a targeted grep across `backend/` for other `"$set"` wrappers.
