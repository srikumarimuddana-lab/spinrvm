# Driver App — P2 Remediation Verification

**Date:** 2026-04-23
**Branch:** `claude/review-pending-audits-Pu1aP`
**Sprint:** P2 — Important: Fix Before Public Launch
**Source sprint file:** `reports/remediation/P2-before-launch.md`
**Source audit:** `reports/audits/2026-04-18-driver-app-production-readiness-v4.txt`
**Items verified:** 10
**Method:** Static inspection at HEAD.

---

### P2-1 · Money Calculations Use `float` Instead of `Decimal`

**Status:** `DONE`
**Source finding:** `[8-1]`
**Evidence:**
- `backend/schemas.py:121, 158, 161, 255-261` — `platform_fee_percent`,
  `base_fare`, `minimum_fare`, `base_fare`, `distance_fare`, `time_fare`,
  `total_fare`, `tip_amount` all typed `Decimal`.
- `backend/validators.py:202-230` — `Decimal(str(amount))`, `.quantize(Decimal("0.01"))`, comparisons via `Decimal`.
**Reason:** All monetary fields are `Decimal`. The two surviving `float`
fields are `surge_multiplier` (lines 139, 259) — multipliers, not money —
which is acceptable per CLAUDE.md's convention.
**Owner:** —
**Confidence:** high
**Regulations:** CRA, SK-PST, PCI-DSS
**Effort remaining:** 0 h

---

### P2-2 · GPS (0, 0) Not Rejected — Null Island

**Status:** `DONE`
**Source finding:** `[4-3]`
**Evidence:**
- `backend/validators.py:170-174` —
  ```python
  if lat == 0 and lng == 0:
      if raise_exception:
          raise HTTPException(status_code=400, detail="Invalid GPS coordinates: null island (0, 0) rejected")
      return False, None
  ```
**Reason:** Exact match — 400 raised on (0, 0).
**Owner:** —
**Confidence:** high
**Regulations:** —
**Effort remaining:** 0 h

---

### P2-3 · WebSocket Broadcast Race Condition

**Status:** `DONE`
**Source finding:** `[6-3]`
**Evidence:**
- `backend/socket_manager.py:88-99` — `broadcast()` snapshots with
  `connections = list(self.active_connections.values())` before iterating.
- Comment at line 97 explicitly calls out the mutation-during-iteration
  anti-pattern.
**Reason:** Snapshot-then-iterate prevents "dictionary changed size" errors
on concurrent disconnect.
**Owner:** —
**Confidence:** high
**Regulations:** —
**Effort remaining:** 0 h
**Note:** `broadcast_to_admins()` at line 101-107 iterates `admin_keys` then
accesses `self.active_connections[key]` by key — tolerable because the key
list is copied first (list comp returns a new list), but consider unifying
with the `broadcast()` pattern for consistency.

---

### P2-4 · Rate Limiter Silent When Redis Is Down

**Status:** `DONE`
**Source finding:** `[11-4]`
**Evidence:**
- `backend/utils/rate_limiter.py:55-57` — startup-time Redis unavailable →
  `logger.warning("Redis unavailable — rate limiter using in-memory fallback...")`.
- `backend/utils/rate_limiter.py:307-320` — runtime Redis failure resets
  connection and falls back to in-memory counter with warning.
- `_local_buckets` in-memory bucket referenced at lines 320+.
**Reason:** Fallback counter active when Redis missing OR fails mid-operation;
warning is logged in both cases.
**Owner:** —
**Confidence:** high
**Regulations:** PIPEDA (brute-force defense)
**Effort remaining:** 0 h
**Note:** In-memory fallback is per-process — not counted across replicas.
Acceptable for degraded mode; alert the team if this warning fires in prod.

---

### P2-5 · Licence Numbers + VINs Stored Without Encryption

**Status:** `DONE`
**Source finding:** `[3-2]`
**Evidence:**
- `backend/migrations/32_encrypt_sensitive_fields.sql` — creates pgsodium key
  `drivers_pii_key`; `encrypt_driver_pii()` / `decrypt_driver_pii()` RPCs
  write to / read from `vault.secrets`; table columns keep TEXT type but
  store the secret UUID.
- `backend/migrations/32_encrypt_sensitive_fields.sql:99` —
  `REVOKE SELECT (license_number) ON TABLE drivers FROM anon, authenticated;`
- `backend/routes/drivers.py:53-84` — `_encrypt_pii_value()` / `_decrypt_pii_value()`
  wrap the RPCs.
- `backend/routes/drivers.py:92-101` — `_encrypt_driver_pii(payload)` /
  `_decrypt_driver_pii(driver)` transform dicts.
- `backend/routes/drivers.py:414, 529, 553` — insert/update paths call
  `_encrypt_driver_pii(...)` before DB write.
- `backend/routes/drivers.py:308, 416, 531, 2508` — read paths call
  `_decrypt_driver_pii(...)` before serialising to the client.
**Reason:** Full write-encrypt + read-decrypt path wired; RLS revoked
from anon/authenticated on the ciphertext column. Migration chose
vault-pointer pattern instead of native `vault.encrypted_text` (Supabase
removed that surface in 2024) — acceptable substitute.
**Owner:** —
**Confidence:** high
**Regulations:** PIPEDA, SAFE-DRV, SAFE-VEH
**Effort remaining:** 0 h
**Note:** Key-rotation plan not verified in this pass — recommend logging
the rotation cadence as part of SOC2 prep.

---

### P2-6 · Expiry Loop Skips Already-Expired Documents

**Status:** `DONE`
**Source finding:** `[12-3]` (same root as P0-5)
**Evidence:**
- `backend/utils/document_expiry.py:66-71` —
  ```python
  # P2-6: process docs that have already expired OR expire within the
  # warning window.
  if not (expiry_dt < now or expiry_dt < warning_cutoff):
      continue
  ```
- `backend/utils/document_expiry.py:73-76` — `if expiry_dt <= now: expired_docs.append(...)` branch.
- `backend/utils/document_expiry.py:91-98` — same gate applied to
  `driver_documents` table rows.
**Reason:** Loop now processes past-expiry docs and the suspension branch
fires on them.
**Owner:** —
**Confidence:** high
**Regulations:** SAFE-CRC, SAFE-DRV, SAFE-VEH, SGI, SK-TNC
**Effort remaining:** 0 h
**Note:** See P0-5 PARTIAL for the separate "$set" wrapper concern on the
suspension UPDATE — not a P2-6 issue but related to this file.

---

### P2-7 · Rate Limits Bypassed Behind Load Balancer / CDN

**Status:** `DONE`
**Source finding:** `[11-3]`
**Evidence:**
- `backend/utils/rate_limiter.py:21` — imports `get_ipaddr` from `slowapi.util`.
- `backend/utils/rate_limiter.py:61-64` — comment + config:
  `# Default limiter — reads the real client IP from X-Forwarded-For when the ... key_func=get_ipaddr`
- `backend/utils/rate_limiter.py:98-99, 111-112` — user+IP and org+IP fallbacks also use `get_ipaddr`.
**Reason:** `get_ipaddr` respects `X-Forwarded-For` when the ASGI server is
started with `--proxy-headers` or behind Starlette's `ProxyHeadersMiddleware`.
**Owner:** —
**Confidence:** med (depends on uvicorn `--proxy-headers` flag in deployment)
**Regulations:** PIPEDA (brute-force defense)
**Effort remaining:** 0 h
**Note:** Confirm uvicorn is launched with `--proxy-headers` (or
equivalent) on Railway/Render. Trusted-proxy IP list not configured in
rate_limiter.py — relies on the ASGI layer. Defense-in-depth gap to log
in infra audit.

---

### P2-8 · Double Charge on Payment Retry

**Status:** `DONE`
**Source finding:** `[8-2]`
**Evidence:**
- `backend/routes/payments.py:106-115` —
  ```python
  # a network retry after a timeout cannot create a second charge. (P2-8)
  idempotency_key = (
      f"ride-{body.ride_id}-{current_user['id']}"
      if body.ride_id
      else f"intent-{current_user['id']}-{amount}"
  )
  stripe.PaymentIntent.create(
      ...,
      api_key=stripe_secret,
      idempotency_key=idempotency_key,
  )
  ```
**Reason:** Idempotency key bound to ride_id + rider id (or intent+amount
fallback). Stripe rejects duplicate PaymentIntent creation under the same
key within 24 h.
**Owner:** —
**Confidence:** high
**Regulations:** PCI-DSS, SK-CPPA (billing fairness)
**Effort remaining:** 0 h
**Note:** Fallback key `intent-{user}-{amount}` is ambiguous if a user
makes two unrelated charges for the same amount within 24 h. Rare but
worth replacing with a UUID client token when `ride_id` is missing.

---

### P2-9 · Only One Expiry Warning (7 Days)

**Status:** `DONE`
**Source finding:** `[13-11]`
**Evidence:**
- `backend/utils/document_expiry.py:137-138` — picks the soonest-expiring
  doc, computes `days_left`.
- `backend/utils/document_expiry.py:141-144` — `days_left == 0`:
  "{label} expires today".
- `backend/utils/document_expiry.py:148-151` — `days_left == 1`:
  "{label} expires tomorrow — ...".
- `backend/utils/document_expiry.py:156` — general case:
  "Document expiring in {days_left} days".
- `backend/utils/document_expiry.py:164-167` — comment: "1-day and day-of
  warnings bypass the guard — these are urgent enough ... if days_left >= 2"
  → urgent tiers always send, general tier is throttled.
**Reason:** Three notification tiers (day-of, 1-day, multi-day) with
different urgency guarantees. Matches remediation spec.
**Owner:** —
**Confidence:** high
**Regulations:** SAFE-CRC, SAFE-DRV, SAFE-VEH, CASL (notification compliance)
**Effort remaining:** 0 h

---

### P2-10 · Notification Preferences Not Synced to Backend

**Status:** `DONE`
**Source finding:** `[13-14]`
**Evidence:**
- `driver-app/app/driver/settings.tsx:60` —
  `api.get('/notifications/preferences').then((prefs: any) => {...})`.
- `driver-app/app/driver/settings.tsx:72` —
  `api.put('/notifications/preferences', { [key]: value }).catch(() => {/* fire-and-forget */})`.
**Reason:** Load-on-mount + write-on-toggle against the existing
`/notifications/preferences` endpoint. Preferences survive reinstall.
**Owner:** —
**Confidence:** high
**Regulations:** CASL (consent ledger), PIPEDA (user preference retention)
**Effort remaining:** 0 h
**Note:** Fire-and-forget PUT swallows errors — if the save fails, the UI
still shows the new state but the server diverges. Consider surfacing a
toast on 4xx/5xx.

---

```yaml
===VERIFICATION-YAML===
- id: P2-1
  source_finding: "8-1"
  status: DONE
  evidence:
    file: backend/schemas.py
    lines: [121, 158, 161, 255, 256, 257, 260, 261]
    snippet: "base_fare: Decimal; total_fare: Decimal; tip_amount: Decimal = Decimal(\"0.0\")"
    test_file: null
    test_lines: null
  reason: "All monetary fields are Decimal; only surge_multiplier (non-money) remains float."
  owner: null
  blocked_by: null
  confidence: high
  regulations: [CRA, SK-PST, PCI-DSS]
  effort_remaining_hours: 0
  notes: null
- id: P2-2
  source_finding: "4-3"
  status: DONE
  evidence:
    file: backend/validators.py
    lines: [170, 171, 172, 173, 174]
    snippet: "if lat == 0 and lng == 0: raise HTTPException(status_code=400, detail=\"null island...\")"
    test_file: null
    test_lines: null
  reason: "400 raised on (0, 0)."
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  notes: null
- id: P2-3
  source_finding: "6-3"
  status: DONE
  evidence:
    file: backend/socket_manager.py
    lines: [88, 97, 98, 99]
    snippet: "connections = list(self.active_connections.values())  # snapshot"
    test_file: null
    test_lines: null
  reason: "Snapshot-then-iterate prevents dict-size race."
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  notes: "broadcast_to_admins uses list comp for keys — consistent enough."
- id: P2-4
  source_finding: "11-4"
  status: DONE
  evidence:
    file: backend/utils/rate_limiter.py
    lines: [55, 57, 307, 308, 320]
    snippet: "Redis unavailable — rate limiter using in-memory fallback"
    test_file: null
    test_lines: null
  reason: "Fallback active at startup + mid-op; warnings logged both times."
  owner: null
  blocked_by: null
  confidence: high
  regulations: [PIPEDA]
  effort_remaining_hours: 0
  notes: "In-memory fallback is per-process — alert if this fires in prod."
- id: P2-5
  source_finding: "3-2"
  status: DONE
  evidence:
    file: backend/routes/drivers.py
    lines: [53, 64, 73, 84, 92, 101, 308, 414, 529, 553, 2508]
    snippet: "supabase.rpc(\"encrypt_driver_pii\", {...}); supabase.rpc(\"decrypt_driver_pii\", {...})"
    test_file: null
    test_lines: null
  reason: "Vault-pointer pattern: pgsodium key + vault.secrets + REVOKE SELECT + full read/write wiring."
  owner: null
  blocked_by: null
  confidence: high
  regulations: [PIPEDA, SAFE-DRV, SAFE-VEH]
  effort_remaining_hours: 0
  notes: "Key rotation cadence not documented (SOC2 follow-up)."
- id: P2-6
  source_finding: "12-3"
  status: DONE
  evidence:
    file: backend/utils/document_expiry.py
    lines: [66, 70, 73, 91, 93, 96]
    snippet: "if not (expiry_dt < now or expiry_dt < warning_cutoff): continue"
    test_file: null
    test_lines: null
  reason: "Past-expiry docs processed; suspension branch fires."
  owner: null
  blocked_by: null
  confidence: high
  regulations: [SAFE-CRC, SAFE-DRV, SAFE-VEH, SGI, SK-TNC]
  effort_remaining_hours: 0
  notes: "P0-5 still open on $set syntax — separate concern."
- id: P2-7
  source_finding: "11-3"
  status: DONE
  evidence:
    file: backend/utils/rate_limiter.py
    lines: [21, 61, 64, 98, 99, 111, 112]
    snippet: "from slowapi.util import get_ipaddr; key_func=get_ipaddr"
    test_file: null
    test_lines: null
  reason: "get_ipaddr respects X-Forwarded-For when ASGI is configured with --proxy-headers."
  owner: null
  blocked_by: null
  confidence: med
  regulations: [PIPEDA]
  effort_remaining_hours: 0
  notes: "Confirm uvicorn --proxy-headers on Railway/Render; no explicit trusted-proxy list."
- id: P2-8
  source_finding: "8-2"
  status: DONE
  evidence:
    file: backend/routes/payments.py
    lines: [106, 107, 108, 109, 110, 114, 115]
    snippet: "idempotency_key = f\"ride-{body.ride_id}-{current_user['id']}\"; stripe.PaymentIntent.create(..., idempotency_key=...)"
    test_file: null
    test_lines: null
  reason: "Idempotency key bound to ride+rider; Stripe rejects duplicates within 24 h."
  owner: null
  blocked_by: null
  confidence: high
  regulations: [PCI-DSS, SK-CPPA]
  effort_remaining_hours: 0
  notes: "Fallback key intent-{user}-{amount} is ambiguous — consider UUID client token."
- id: P2-9
  source_finding: "13-11"
  status: DONE
  evidence:
    file: backend/utils/document_expiry.py
    lines: [137, 141, 148, 156, 164, 167]
    snippet: "days_left == 0 (today) / == 1 (tomorrow) / general; urgent tiers bypass 24 h guard"
    test_file: null
    test_lines: null
  reason: "Three tiers (day-of, 1-day, multi-day) with urgency-aware throttling."
  owner: null
  blocked_by: null
  confidence: high
  regulations: [SAFE-CRC, SAFE-DRV, SAFE-VEH, CASL]
  effort_remaining_hours: 0
  notes: null
- id: P2-10
  source_finding: "13-14"
  status: DONE
  evidence:
    file: driver-app/app/driver/settings.tsx
    lines: [60, 72]
    snippet: "api.get('/notifications/preferences'); api.put('/notifications/preferences', { [key]: value })"
    test_file: null
    test_lines: null
  reason: "Load-on-mount + write-on-toggle against existing backend endpoint."
  owner: null
  blocked_by: null
  confidence: high
  regulations: [CASL, PIPEDA]
  effort_remaining_hours: 0
  notes: "Fire-and-forget PUT swallows errors — add toast on 4xx/5xx."
===END-VERIFICATION-YAML===

===AUDIT-COMPLETE=== sprint=P2 items=10 done=10 partial=0 pending=0 blocked=0 unverifiable=0 superseded=0
```

---

## Summary

| Status | Count | IDs |
|---|---|---|
| DONE | 10 | all |
| PARTIAL | 0 | — |
| PENDING | 0 | — |
| BLOCKED | 0 | — |
| UNVERIFIABLE | 0 | — |
| SUPERSEDED | 0 | — |

**Open P2 effort:** 0 h. **Launch-blocking items:** none.

## New issues discovered (not in scope for this verification)

1. **P2-7 depends on ASGI config** — rate_limiter.py correctly uses `get_ipaddr`, but the trust boundary depends on uvicorn being started with `--proxy-headers`. Confirm in Railway/Render startup config; add explicit trusted-proxy list for defense in depth.
2. **P2-8 fallback idempotency key ambiguous** — when `ride_id` is missing the key reduces to `intent-{user}-{amount}`, which would collide if a user legitimately retries a same-amount charge after 24 h. Swap to a UUID client token for the no-ride-id path.
3. **P2-10 fire-and-forget write** — `api.put(...).catch(() => {})` silently hides 4xx/5xx. UI state diverges from server. Surface a toast and optionally reconcile on next mount.
4. **P2-4 in-memory fallback is per-process** — add an alert when the "Redis unavailable" warning is emitted in prod so the degraded mode is visible to SRE.
5. **P2-5 key rotation** — pgsodium `drivers_pii_key` has no documented rotation cadence. Required for SOC2; worth a scheduled rotation runbook.
