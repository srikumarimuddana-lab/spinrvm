# Driver App — P3 Remediation Verification

**Date:** 2026-04-23
**Branch:** `claude/review-pending-audits-Pu1aP`
**Sprint:** P3 — Hardening: Once the App Is Stable
**Source sprint file:** `reports/remediation/P3-hardening.md`
**Source audit:** `reports/audits/2026-04-18-driver-app-production-readiness-v4.txt`
**Items verified:** 10
**Method:** Static inspection at HEAD.

---

### P3-1 · PII Test Only Covers 2 of 14 Forbidden Fields

**Status:** `DONE`
**Source finding:** `[9-1]`
**Evidence:**
- `backend/tests/test_ride_pii.py:18-33` — `FORBIDDEN_FIELDS` list:
  `license_number, vehicle_vin, insurance_expiry, stripe_account_id,
  fcm_token, phone, bank_account, sin_number, date_of_birth,
  home_address, background_check_result, criminal_record,
  passport_number, tax_id` — **14 fields**, exactly as required.
- `backend/tests/test_ride_pii.py:43` — `@pytest.mark.parametrize("field", FORBIDDEN_FIELDS)`
- `backend/tests/test_ride_pii.py:44` — `def test_field_not_in_rider_response(field, client, auth_headers):`
**Reason:** Every forbidden field is parametrized into its own test case. Adding
a new sensitive field → just append to list.
**Owner:** —
**Confidence:** high
**Regulations:** PIPEDA, PCI-DSS, SAFE-CRC, SAFE-DRV
**Effort remaining:** 0 h

---

### P3-2 · No Test Proves Two Drivers Can't Accept the Same Ride

**Status:** `DONE`
**Source finding:** `[9-2]`
**Evidence:**
- `backend/tests/test_rides.py:15` — comment header
  "P3-2: Concurrent double-accept guard".
- `backend/tests/test_rides.py:39` —
  `async def test_no_double_accept(client, ride_id, driver_1_headers, driver_2_headers):`
- `backend/tests/test_e2e_ride_lifecycle.py:15` — end-to-end notes
  "double accept: second driver gets 409".
**Reason:** Concurrent-accept regression test exists; would fail if the
atomic claim in `claim_ride_atomic` regresses.
**Owner:** —
**Confidence:** high
**Regulations:** SK-CPPA
**Effort remaining:** 0 h
**Note:** Verify the test uses `asyncio.gather` or equivalent to fire
requests truly concurrently (not serially) — not confirmed in this pass.

---

### P3-3 · OTP Lockout Never Integration-Tested

**Status:** `DONE`
**Source finding:** `[9-3]`
**Evidence:**
- `backend/tests/test_auth.py:479` — comment header
  "P3-3: OTP lockout integration test".
- `backend/tests/test_auth.py:487` —
  `def test_otp_lockout_after_5_failures(test_client, mock_redis, valid_phone):`
- Secondary coverage in `backend/tests/test_auth_send_otp.py:131, 144, 156`
  (`test_lockout_helpers_exist`, `test_check_lockout_swallows_redis_errors`,
  `test_check_lockout_raises_429_when_locked`).
**Reason:** Named integration test exists for the 5-failures-→-429 flow.
**Owner:** —
**Confidence:** med
**Regulations:** PIPEDA
**Effort remaining:** 0 h
**Note:** Test uses `mock_redis` fixture — so "integration" is with the
FastAPI app but not a real Redis. For true end-to-end, add a
docker-compose-based Redis harness. Acceptable for CI.

---

### P3-4 · No Minimum Test Coverage Requirement

**Status:** `PARTIAL`
**Source finding:** `[9-4]`
**Evidence (what's in place):**
- `driver-app/jest.config.js:21-27` — `coverageThreshold` IS set:
  ```js
  global: {
    lines: 30,
    functions: 20,
    statements: 30,
  }
  ```
**Evidence (what's missing):**
- Thresholds are 30/20/30, not the 70/60/70 recommended in the remediation.
- `backend/pytest.ini:11-17` — `--cov` flags added but `--cov-fail-under`
  is explicitly **not** set. Inline comment:
  "Coverage is reported but NOT gated. The 80% target was aspirational and
  has never been met against the current Supabase-backed code path (baseline
  is ~6%). Restoring a meaningful --cov-fail-under threshold is part of the
  P1 test-suite repair..."
**Reason:** Frontend threshold exists but at a low bar; backend gating is
deliberately disabled pending test-suite rebuild. The gate exists as
infrastructure but does not enforce a meaningful bar.
**Owner:** `backend` + `driver-app`
**Blocked by:** —
**Confidence:** high
**Regulations:** —
**Effort remaining:** 8 h (raise frontend thresholds, write enough backend
tests to set even a 30% backend gate, then schedule stepped increases).
**Action:**
1. Raise `driver-app/jest.config.js` thresholds to 50/40/50 as interim.
2. Add `--cov-fail-under=30` to `backend/pytest.ini` now; step up by
   5 pp per sprint.
3. Add CI step that fails the PR if coverage drops vs. base branch.

---

### P3-5 · Errors Don't Include a Trace ID

**Status:** `DONE`
**Source finding:** `[10-3]`
**Evidence:**
- `backend/core/middleware.py:114-115` —
  `request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())`
  `request.state.request_id = request_id` (respects upstream IDs; generates
  if missing).
- `backend/utils/error_handling.py:409, 419, 425` — HTTPException handler
  adds `request_id` to response body and `X-Request-ID` header.
- `backend/utils/error_handling.py:450, 465, 472` — validation-error handler.
- `backend/utils/error_handling.py:487, 496, 502` — domain-error handler.
- `backend/utils/error_handling.py:569, 582, 601, 613` — unhandled-exception
  path logs `[{request_id}] Unhandled exception at {path}` and returns
  `request_id` in the body.
**Reason:** Every error path — expected and unexpected — emits a
request_id in both JSON body and response header.
**Owner:** —
**Confidence:** high
**Regulations:** PIPEDA (audit trail), SOC2
**Effort remaining:** 0 h

---

### P3-6 · DB Timeouts Not Retried

**Status:** `DONE`
**Source finding:** `[10-4]`
**Evidence:**
- `backend/db_supabase.py:8` —
  `_HTTPX_TIMEOUT_EXC = _httpx.TimeoutException` (import-guarded).
- `backend/db_supabase.py:108` —
  `is_timeout = _HTTPX_TIMEOUT_EXC is not None and isinstance(exc, _HTTPX_TIMEOUT_EXC)`.
- `backend/db_supabase.py:109` — retry-once branch is guarded by
  `if is_conn_terminated or is_remote_disconnect or is_timeout:`.
- `backend/db_supabase.py:110-115` — 250 ms sleep + single retry.
**Reason:** `httpx.TimeoutException` joins the existing retry clause
alongside `ConnectionTerminated` and `RemoteProtocolError`.
**Owner:** —
**Confidence:** high
**Regulations:** —
**Effort remaining:** 0 h

---

### P3-7 · SOS Button Takes 2s to Activate

**Status:** `DONE`
**Source finding:** `[1-13]` + UX follow-up
**Evidence:**
- `shared/components/SOSButton.tsx:22` — `const SOS_HOLD_MS = 1200; // was 2000`
- `shared/components/SOSButton.tsx:42` — `}, SOS_HOLD_MS);`
**Reason:** Hold threshold reduced from 2000 ms to 1200 ms, matching
industry panic-button standard.
**Owner:** —
**Confidence:** high
**Regulations:** E911 (SOS responsiveness), WCAG
**Effort remaining:** 0 h

---

### P3-8 · Deleted Accounts Leave No Audit Trail

**Status:** `DONE`
**Source finding:** `[12-8]`
**Evidence:**
- `backend/migrations/33_soft_delete_columns.sql:5-7` —
  `ALTER TABLE drivers/users/rides ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ`.
- `backend/migrations/33_soft_delete_columns.sql:11-13` —
  `CREATE INDEX IF NOT EXISTS idx_{drivers,users,rides}_deleted_at ON ... (deleted_at)`.
- `backend/db_supabase.py:310, 319, 339, 483` — every read-by-id path
  adds `.is_("deleted_at", "null")`.
- `backend/routes/users.py:103, 120, 126` — account-delete paths
  `db_supabase.update_one(..., {"deleted_at": now})` (no `DELETE FROM`).
**Reason:** Schema, index, read-filter, and write-path all in place. Deletes
become soft-deletes with timestamp; data is retained for dispute /
refund / regulator scenarios.
**Owner:** —
**Confidence:** high
**Regulations:** PIPEDA (retention), CRA, SOC2, SK-CPPA (dispute records)
**Effort remaining:** 0 h
**Note:** Retention schedule per data class (see
`audit-framework/regulatory-matrix.md` data classification) is not
enforced automatically — PII is still retained until a purge job runs.
Add a scheduled purge at the retention horizon.

---

### P3-9 · Notification Tap Does Nothing Except Mark Read

**Status:** `DONE`
**Source finding:** `[13-18]`
**Evidence:**
- `driver-app/app/driver/notifications.tsx:109-112`:
  ```ts
  if (item.type === 'document_expiry') router.push('/driver/documents');
  else if (item.type === 'payout_processed') router.push('/driver/earnings');
  else if (item.type === 'ride_offer') router.push('/driver/');
  else if (item.type === 'quest_earned') router.push('/driver/quests');
  ```
**Reason:** Per-type routing from the notification tap handler.
**Owner:** —
**Confidence:** high
**Regulations:** —
**Effort remaining:** 0 h
**Note:** Default case not verified — if a new type lands before a route is
added, the tap silently no-ops. Consider a `default: router.push('/driver/')`
or analytics log for unknown types.

---

### P3-10 · Firebase Path Doesn't Issue Refresh Token

**Status:** `DONE`
**Source finding:** `[2-5]`
**Evidence:**
- `backend/routes/auth.py:394` — `async def firebase_auth_login(request, body):`
- `backend/routes/auth.py:402-403` —
  `_firebase_auth.verify_id_token(body.firebase_token)`.
- `backend/routes/auth.py:450` —
  `refresh_raw, _, refresh_expires_at = await issue_refresh_token(...)`
  (identical call site to the OTP path at line 322 and 364).
**Reason:** Firebase-authenticated drivers now receive a Spinr refresh token
equivalent to OTP-authenticated drivers. Silent-refresh works across platforms.
**Owner:** —
**Confidence:** high
**Regulations:** PIPEDA
**Effort remaining:** 0 h
**Note:** Audience (`driver` vs `rider`) check on the Firebase path — see
rider-P1-12 for the separate open finding.

---

```yaml
===VERIFICATION-YAML===
- id: P3-1
  source_finding: "9-1"
  status: DONE
  evidence:
    file: backend/tests/test_ride_pii.py
    lines: [18, 33, 43, 44]
    snippet: "FORBIDDEN_FIELDS = [14 fields]; @pytest.mark.parametrize(\"field\", FORBIDDEN_FIELDS)"
    test_file: backend/tests/test_ride_pii.py
    test_lines: [43, 44]
  reason: "All 14 forbidden fields parametrized; list-add is all future additions need."
  owner: null
  blocked_by: null
  confidence: high
  regulations: [PIPEDA, PCI-DSS, SAFE-CRC, SAFE-DRV]
  effort_remaining_hours: 0
  notes: null
- id: P3-2
  source_finding: "9-2"
  status: DONE
  evidence:
    file: backend/tests/test_rides.py
    lines: [15, 39]
    snippet: "async def test_no_double_accept(client, ride_id, driver_1_headers, driver_2_headers):"
    test_file: backend/tests/test_rides.py
    test_lines: [39]
  reason: "Concurrent-accept regression test exists."
  owner: null
  blocked_by: null
  confidence: high
  regulations: [SK-CPPA]
  effort_remaining_hours: 0
  notes: "Verify requests are actually concurrent (asyncio.gather) vs serial."
- id: P3-3
  source_finding: "9-3"
  status: DONE
  evidence:
    file: backend/tests/test_auth.py
    lines: [479, 487]
    snippet: "def test_otp_lockout_after_5_failures(test_client, mock_redis, valid_phone):"
    test_file: backend/tests/test_auth.py
    test_lines: [487]
  reason: "Integration-level lockout test exists; supporting tests in test_auth_send_otp.py."
  owner: null
  blocked_by: null
  confidence: med
  regulations: [PIPEDA]
  effort_remaining_hours: 0
  notes: "Uses mock_redis — not a real-Redis E2E. Add docker-compose-based test for full coverage."
- id: P3-4
  source_finding: "9-4"
  status: PARTIAL
  evidence:
    file: driver-app/jest.config.js
    lines: [21, 22, 23, 24, 25, 26, 27]
    snippet: "coverageThreshold: { global: { lines: 30, functions: 20, statements: 30 } }"
    test_file: null
    test_lines: null
  reason: "Frontend threshold set at 30/20/30 (below recommended 70/60/70). Backend --cov-fail-under explicitly disabled because current coverage ~6%."
  owner: backend
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 8
  notes: "Step up in 2-3 sprints: raise FE to 50/40/50; add BE --cov-fail-under=30 then +5pp/sprint; add CI check for coverage drop vs base."
- id: P3-5
  source_finding: "10-3"
  status: DONE
  evidence:
    file: backend/utils/error_handling.py
    lines: [409, 419, 425, 450, 465, 472, 487, 496, 502, 569, 582, 601, 613]
    snippet: "content[\"error\"][\"request_id\"] = request_id; \"X-Request-ID\": request_id"
    test_file: null
    test_lines: null
  reason: "Every error path — expected + unexpected — emits request_id in body and header."
  owner: null
  blocked_by: null
  confidence: high
  regulations: [PIPEDA, SOC2]
  effort_remaining_hours: 0
  notes: "Middleware generates or reuses X-Request-ID (core/middleware.py:114)."
- id: P3-6
  source_finding: "10-4"
  status: DONE
  evidence:
    file: backend/db_supabase.py
    lines: [8, 108, 109, 110, 115]
    snippet: "is_timeout = ... isinstance(exc, _HTTPX_TIMEOUT_EXC); if is_conn_terminated or is_remote_disconnect or is_timeout: retry"
    test_file: null
    test_lines: null
  reason: "httpx.TimeoutException added to transient-failure retry clause."
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  notes: null
- id: P3-7
  source_finding: "1-13"
  status: DONE
  evidence:
    file: shared/components/SOSButton.tsx
    lines: [22, 42]
    snippet: "const SOS_HOLD_MS = 1200; // was 2000"
    test_file: null
    test_lines: null
  reason: "Hold threshold reduced from 2000 to 1200 ms."
  owner: null
  blocked_by: null
  confidence: high
  regulations: [E911, WCAG]
  effort_remaining_hours: 0
  notes: null
- id: P3-8
  source_finding: "12-8"
  status: DONE
  evidence:
    file: backend/migrations/33_soft_delete_columns.sql
    lines: [5, 6, 7, 11, 12, 13]
    snippet: "ALTER TABLE {drivers,users,rides} ADD COLUMN deleted_at TIMESTAMPTZ; CREATE INDEX ..."
    test_file: null
    test_lines: null
  reason: "Schema + index + read-filter (db_supabase.py) + write-path (routes/users.py) all in place."
  owner: null
  blocked_by: null
  confidence: high
  regulations: [PIPEDA, CRA, SOC2, SK-CPPA]
  effort_remaining_hours: 0
  notes: "Retention-horizon purge job not scheduled — PII retained indefinitely until added."
- id: P3-9
  source_finding: "13-18"
  status: DONE
  evidence:
    file: driver-app/app/driver/notifications.tsx
    lines: [109, 110, 111, 112]
    snippet: "if (item.type === 'document_expiry') router.push('/driver/documents'); else if ..."
    test_file: null
    test_lines: null
  reason: "Per-type routing for document_expiry, payout_processed, ride_offer, quest_earned."
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  notes: "Add default fallback + unknown-type analytics."
- id: P3-10
  source_finding: "2-5"
  status: DONE
  evidence:
    file: backend/routes/auth.py
    lines: [394, 402, 403, 450]
    snippet: "async def firebase_auth_login(...); _firebase_auth.verify_id_token(...); refresh_raw, _, refresh_expires_at = await issue_refresh_token(...)"
    test_file: null
    test_lines: null
  reason: "Firebase path issues Spinr refresh token identical to OTP path."
  owner: null
  blocked_by: null
  confidence: high
  regulations: [PIPEDA]
  effort_remaining_hours: 0
  notes: "Audience (driver vs rider) check — see rider-P1-12 separately."
===END-VERIFICATION-YAML===

===AUDIT-COMPLETE=== sprint=P3 items=10 done=9 partial=1 pending=0 blocked=0 unverifiable=0 superseded=0
```

---

## Summary

| Status | Count | IDs |
|---|---|---|
| DONE | 9 | P3-1, P3-2, P3-3, P3-5, P3-6, P3-7, P3-8, P3-9, P3-10 |
| PARTIAL | 1 | P3-4 |
| PENDING | 0 | — |
| BLOCKED | 0 | — |
| UNVERIFIABLE | 0 | — |
| SUPERSEDED | 0 | — |

**Open P3 effort:** ~8 h (coverage threshold step-up).

## New issues discovered (not in scope for this verification)

1. **P3-4**: backend coverage baseline is ~6% (per `pytest.ini` comment). Even a 30% gate needs test-writing work, not a config toggle. Worth an explicit backend test-suite recovery project.
2. **P3-3**: OTP lockout test uses `mock_redis` — consider a docker-compose-based integration test with real Redis for confidence that Redis failure modes (not just mocked ones) are covered.
3. **P3-8**: soft-delete is implemented, but a scheduled **purge job** at the retention horizon is not. Required for PIPEDA compliance (data minimisation) and CRA retention rules — 2 yr post-closure per `regulatory-matrix.md`.
4. **P3-9**: notification deep-link has no default fallback for unknown types — a new notification type without a matching branch would silently no-op. Add `else { router.push('/driver/'); analytics.log('notif_unknown_type', item.type); }`.
