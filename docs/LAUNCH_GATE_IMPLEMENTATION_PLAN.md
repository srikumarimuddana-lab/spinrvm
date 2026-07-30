# Launch-Gate Implementation Plan — External Review Remediation

**Created:** 2026-07-29 · **Branch:** `claude/critical-security-pipeda-breach-pn67ww`
**Source:** external read-only static review (conditional no-go for public launch)
**Status:** plan only — no production code changed by this document

---

## 0. How to read this

Every finding below was re-verified against **this** checkout before being planned.
The review was written against a different working copy (`spinrvm2`, Windows paths),
so its line numbers do not apply here; the paths and line numbers in this document do.

| Legend | Meaning |
|---|---|
| ✅ CONFIRMED | Reproduced in this checkout at the stated path/line |
| ⚠️ CONFIRMED, WORSE | Real, but the blast radius is larger than the review states |
| 🔀 PARTIALLY | Real in substance, but the mechanism or scope differs — fix is narrower or wider |
| 🟢 ALREADY BUILT | Substrate exists; the task is adoption, not construction |
| ⛔ N/A HERE | Artifact of the reviewer's local checkout, not present in this repo |

**Verdict on the verdict:** the conditional no-go is correct. Three of the four P0s are
genuine launch blockers. But two of the review's remediation estimates are wrong in a way
that changes the plan: the PIPEDA logging leak is **bigger** than "GPS in logs", and the
PostGIS workstream is **mostly already done**.

---

## 1. Verification of each finding

### P0-1 — PII in logs via the repository layer ⚠️ CONFIRMED, WORSE

**Review said:** driver GPS coordinates and full update results are logged by the generic
repository path.

**Reality is broader.** `backend/repositories/_base.py:798-821` contains a `[GO-ONLINE]`
debug-instrumentation block inside `update_one()`. It is gated only on
`if table == "drivers"` — **not** on an env flag, not on log level, not on the calling
route. It emits two `logger.info` lines on **every** write to the `drivers` table:

```python
# backend/repositories/_base.py:798
if table == "drivers":
    logger.info(
        f"[GO-ONLINE] db_supabase.update_one about to execute: "
        f"table={table} filters={filters} payload={update_data} upsert={upsert}"
    )
...
# backend/repositories/_base.py:811
if table == "drivers":
    raw_data = getattr(res, "data", None) if res else None
    logger.info(
        f"[GO-ONLINE] db_supabase.update_one executed: "
        f"res_type=... res_data={raw_data}"
    )
```

Two separate leaks, and the second is the serious one:

1. **`payload=` leaks the write.** On the location hot path
   (`backend/routes/drivers/location.py:418` and `:163`) the payload is
   `{"lat": ..., "lng": ..., "heading": ...}` → **raw GPS coordinates at INFO, every
   location batch, for every online driver.** That alone violates the CLAUDE.md rule
   ("Raw GPS coordinates (lat/lng) — log geohashed area at most").

2. **`res_data=` leaks the whole row.** PostgREST returns the full updated row by default,
   so this logs *every column of the drivers table*.

   > **Correction (2026-07-30).** This section originally said the leak exposed
   > `drivers.license_number` as a government ID. That is **wrong**:
   > `backend/migrations/32_encrypt_sensitive_fields.sql:11-15` and
   > `244_vehicle_vin_plaintext_at_rest.sql:3` establish that `license_number` holds a
   > `vault.secrets` UUID, not a plaintext licence number. The plaintext columns actually
   > exposed are **`name` (full legal name), `phone` (full phone), and `vehicle_vin`** —
   > VIN having been reverted to plaintext at rest by migration 244 — alongside raw
   > `lat`/`lng`. Still a P0 and still four never-log fields, but the legal
   > "real risk of significant harm" argument is *location + name + phone + VIN*, not
   > *location + government ID*. T4 must use the corrected list.

**Blast radius — this is the number that matters:** 28 `update_one("drivers", …)` call
sites across 15 files, every one of them currently logging a full driver row:

```
backend/documents.py                                 (document verification)
backend/features.py
backend/routes/auth.py
backend/routes/users.py
backend/routes/admin/drivers.py
backend/routes/drivers/location.py                   ← raw GPS, hot path
backend/routes/drivers/profile.py                    ← address, phone
backend/routes/drivers/payouts.py                    ← payout identifiers
backend/routes/drivers/status.py
backend/services/stripe_kyc_sync.py                  ← KYC
backend/services/stripe_mapping_import_service.py
backend/services/data_transfer/entity_import_service.py
(+ 3 test files)
```

**Where it lands.** `backend/server.py:420` registers a single loguru sink to `sys.stderr`
with `serialize=True` and `level="INFO"`. Fly.io and Railway both capture stderr into their
platform log aggregators. `backend/utils/sentry_scrub.py` scrubs the Sentry path and
`backend/utils/pii.py` provides `geohash()`, `redact_phone()`, `redact_email()`,
`area_only()` — **none of which this code path calls.** The redaction helpers exist; this
block bypasses all of them.

**Why no guard caught it.** `.claude/hooks/pre-commit` step 3 nominally checks for "PII in
logs" but is a six-pattern source-text denylist that cannot match a runtime-interpolated
payload. Details and consequences in T2.

**Severity:** P0, and per CLAUDE.md's breach protocol this is a suspected PII exposure
event requiring a scope assessment inside 24h, not just a code fix. See T4.

**A second, broader leak in the same file (found 2026-07-30, not in the original review).**
`backend/repositories/_base.py`'s catch-all DB error line logged `str(exc)` verbatim for
**every table**, not just `drivers`. Postgres embeds column values in its error text: a unique
violation carries `Key (phone)=(+1306…)`, and a CHECK or NOT NULL violation carries
`Failing row contains (…)` — the entire row. The same string also rode into
`DatabaseError.details["original"]`, which CLAUDE.md instructs callers to log, so it escaped
the log sink as well. Fixed in the same commit as T1 via `_redact_pg_error()`.

**Status: T1 is done** — commit `cb6cc67`. Impact log:
`docs/change-log/2026-07-30-base-pii-logging.md`.

---

### P0-2 — Driver-location harvesting ✅ CONFIRMED (WebSocket path is the worse one)

Two endpoints return exact live driver coordinates to any authenticated caller who supplies
arbitrary `lat`/`lng`/`radius`:

**REST — `backend/routes/drivers/location.py:170` `GET /drivers/nearby`.** Returns
`{id, lat, lng, heading, vehicle_type_id, vehicle_type_name, marker_variant, vehicle_make,
vehicle_model}` per driver. Caller-supplied `radius` has **no upper bound** — `radius` is
`Query(None)` and only defaults to `app_settings.search_radius_km` when omitted; pass
`radius=500` and the bounding box is 500 km. It does at least geo-bound the DB fetch via
`dispatch_geo_bounds(lat, lng, radius)` and presence-filter the result.

**WebSocket — `backend/routes/websocket.py:1098` `get_nearby_drivers`.** Worse on three counts:
- **No geo bound at all** on the DB query — it fetches up to 100 online drivers
  *province-wide*, then filters by distance in Python. A caller can walk the map and, above
  100 online drivers, gets an arbitrary slice.
- **No presence filter**, so it also emits ghost cars the REST path would hide.
- `if lat and lng:` is a falsy check — `lat=0` silently no-ops (cosmetic, but it is a bug).

**Correction to the review:** it is not true that there is no rate limiting. Both paths
inherit `default_limiter` (`backend/utils/rate_limiter.py:111` — `100/minute`, `1000/hour`).
That is still a harvesting vector — 100 req/min × up to 100 drivers/response — but "no
rate limit" overstates it, and the fix is a *scoped* limit plus radius cap, not
introducing limiting from zero.

**Also unmentioned by the review:** `backend/routes/drivers/location.py:291` `GET /drivers`
returns `serialize_doc(drivers)` — **full, unredacted driver rows** — when `lat`/`lng` are
passed. It is `get_admin_user`-gated so it is not a public leak, but it is a full-row dump
including `license_number`, and its own inline comment concedes the implementation is a
placeholder ("Should rely on RPC or geospatial query"). Folding it into the same fix is cheap.

---

### P0-3 — Security pipeline is advisory 🔀 PARTIALLY CONFIRMED (narrower than stated)

The review says the gates are "neutralized by `|| true` or `continue-on-error`". Half right —
`continue-on-error` has **already** been flipped to `false` on most jobs in
`.github/workflows/security-gates.yml`. The live neutralizer is `|| true` at the **step**
level, which makes `continue-on-error: false` decorative:

| Gate | Job-level | Step-level | Actually blocking? |
|---|---|---|---|
| G1 Bandit (`:35`) | `continue-on-error: false` | `\|\| true` (`:42`) | ❌ No |
| G2 ESLint (`:50`) | `continue-on-error: false` | `\|\| true` (`:83-84`) | ❌ No |
| G3 Semgrep (`:88`) | `continue-on-error: false` | `\|\| true` (`:105`) | ❌ No |
| G4a pip-audit (`:112`) | `continue-on-error: false` | `\|\| true` (`:121`) | ❌ No |
| G4b yarn audit (`:124`) | `continue-on-error: false` | explicit bitmask exit | ✅ **Yes** (HIGH+) |
| G4c npm audit admin (`:155`) | `continue-on-error: false` | bare `npm audit` | ✅ **Yes** (HIGH+) |
| G5a Gitleaks history (`:171`) | **`continue-on-error: true`** | — | ❌ No |
| G5b Gitleaks bundle (`:184`) | **`continue-on-error: true`** | — | ❌ No |

So the work is: strip four `|| true`s, flip two `continue-on-error: true`s, and — the part
that actually takes the time — **baseline the existing debt first**, because flipping these
cold will red-wall every PR. The header comment says "non-blocking for the first 2 weeks —
treat as baselining window"; that window was never closed.

Branch protection must also be checked: a gate that fails is worthless if the check is not
**required** on `main`. That is a GitHub settings change, not a code change, and needs the
repo owner.

---

### P2-4 — Spoofable audit attribution ✅ CONFIRMED

`backend/core/middleware.py:219`:

```python
payload = jwt.decode(token, options={"verify_signature": False})
uid = payload.get("user_id") or payload.get("sub")
```

`RequestIDMiddleware` binds that `user_id` into the loguru context for every log line in the
request. The review's characterisation is exactly right: **not** an authentication bypass
(real auth happens later in `get_current_user`), but anyone can forge an unsigned JWT and
attribute their entire request's log trail to another user's ID. That poisons audit and
incident forensics — which is precisely what you rely on during a breach investigation.

---

### Performance / architecture findings

| Finding | Status | Note |
|---|---|---|
| Nearby lookup should use PostGIS `ST_DWithin` + KNN | 🟢 **ALREADY BUILT** | `backend/migrations/170_drivers_location_geog_surge.sql` already adds `location_geog geography(Point,4326)`, a sync trigger, and a **CONCURRENTLY-built partial GIST index**. A `find_nearby_drivers` RPC already exists (`backend/repositories/driver_repo.py:107`). Neither `/drivers/nearby` nor the WS handler uses it. This is an **adoption** task. |
| 64-worker DB thread pool amplifies upstream slowness | 🔀 PARTIALLY | `_base.py:158` is indeed 64 workers, but a circuit breaker (`_CircuitBreaker`, `:60-147`), per-request deadlines (`:249`), retry-budget fail-open (`:193`), and a `spinr_db_thread_pool_queue_depth` gauge already exist. The gap is saturation **alerting**, not the mechanism. Downgrade to P2. |
| Dispatch kickoff in a process-local task | ✅ CONFIRMED | `backend/routes/rides/booking.py:1216` — `_deps.spawn(_prep_and_dispatch(...))`. A replica restart between ride insert and dispatch start leaves the ride in `searching` with no offer. Mitigated in practice by the stuck-ride sweeper loop; a real outbox is the durable fix. P2 for launch. |
| Superseded WebSockets not closed | ✅ CONFIRMED | `backend/socket_manager.py:65` — `connect()` does `self.active_connections[client_id] = websocket`, overwriting without `await old.close()`. Leaks the old socket and can duplicate command delivery. P2. |
| `graphify-out/GRAPH_REPORT.md` stale | ⛔ N/A HERE | Directory does not exist in this repo. |

### Error-handling findings

| Finding | Status | Note |
|---|---|---|
| Backend 5xx sanitisation is a strength | ✅ Agreed | `backend/utils/error_handling.py` + the contract in `docs/runbooks/error-responses.md`. |
| Admin UI renders raw `e.message` | ✅ CONFIRMED, wide | **102 occurrences** across `admin-dashboard/src`. Includes unauthenticated surfaces (`app/company-login/page.tsx:50,102`, `app/register/driver/page.tsx:92,123,204`). Backend/upstream technical strings reach operators and, on the public signup/login screens, prospective customers. |

---

## 2. Phase 1 — Launch blockers

Nothing in this phase is optional. Every task lists blast radius, verification, and rollback
per the CLAUDE.md pre-merge release gates. Each task is ≤3 files per the batch-size rule.

### T1 — Kill the `[GO-ONLINE]` instrumentation leak
**Priority:** P0 · **Files:** `backend/repositories/_base.py` (1 file)
**Effort:** S · **Blocks:** T4

Delete both `if table == "drivers"` logging blocks (`:798-802`, `:811-821`). Replace with a
single INFO line carrying **only** an allowlisted key set: `table`, the filter *keys* (not
values), the payload *keys* (not values), row-count of the result, and `geohash(lat, lng)`
from `backend/utils/pii.py` when the payload carries coordinates. Never `res_data`.

- **Why deletion, not redaction-in-place:** an allowlist is the only shape that stays safe
  when someone adds a new sensitive column to `drivers`. A denylist of "scrub lat/lng" would
  have missed `license_number` — which is exactly how this bug survived review.
- **Blast radius:** all 28 `update_one("drivers", …)` call sites listed in §1 P0-1 lose their
  verbose log lines. None of them *parse* logs, so no functional consumer breaks. The
  `[GO-ONLINE]` tag exists for go-online debugging (`backend/routes/drivers/status.py`); the
  replacement line keeps that traceability via filter/payload **keys**, which is what the
  debugging actually needed.
- **Also fix:** `_base.py:782` — `logger.warning("[GO-ONLINE] … supabase client is None!")`
  is a DB error logged at `warning` and swallowed with `return None`. CLAUDE.md forbids
  exactly this. Promote to `logger.error` and raise. *(Behaviour change — see T1 note in
  the Change Impact Log.)*
- **Verify:** unit test asserting a `drivers` update with `{"lat","lng","license_number"}`
  in payload and a full row in `res.data` emits **no** coordinate, no licence number, no
  `res_data`. Run `pytest -m "not slow"` full backend suite.
- **Rollback:** pure log-emission change, no data or state touched — `git revert` is a
  genuine rollback here.

### T2 — Log-sink PII tripwire (defence in depth)
**Priority:** P0 · **Files:** `backend/utils/log_guard.py` (new), `backend/server.py`,
`backend/tests/test_log_guard.py` (new) · **Effort:** M · **Depends on:** T1

T1 fixes the one known leak. T2 makes the *next* one impossible to ship silently.

**Why the existing guard did not catch this.** `.claude/hooks/pre-commit` step 3 ("Checking
for PII in logs") is a **denylist of six source-text regexes** matched against the staged
diff:

```
print.*lat.*lng  ·  print.*latitude  ·  print.*phone
console\.log.*lat.*lng  ·  console\.log.*phoneNumber
logger\.(info|debug).*coordinates
```

None of them match the actual leak. The leaking line is
`logger.info(f"… payload={update_data} …")` — the words `lat`, `lng`, and `coordinates` never
appear in the source; the coordinates arrive at runtime inside an interpolated dict. This is
not a gap in the pattern list that a seventh regex would close: **source-text matching cannot
determine what a variable will contain at runtime**, and payload/row-shaped leaks are exactly
the dangerous case. The hook printed `✅ Clean` while committing this very plan, and would
print it again for a reintroduced `payload={update_data}`.

That makes the hook worse than absent — it supplies false assurance on the category of bug
that shipped. T2's runtime sink-level check is the only structurally sound place to enforce
this, because it inspects the **formatted** record rather than the source that produced it.
Keep the hook (it is a cheap catch for the naive `print(lat, lng)` case) but stop treating a
green step 3 as evidence of anything, and say so in its own output.

`backend/server.py:420` is the single loguru sink — one chokepoint. Add a `filter=` callable
that scans the formatted record for coordinate-shaped floats, E.164 phone numbers, email
addresses, and licence-number patterns.

- **Behaviour:** in `ENV != production`, **raise** so it fails a test run loudly. In
  production, redact-and-emit with a `spinr_log_pii_blocked_total` counter — never drop the
  line (losing an error log during an incident is its own outage) and never crash the
  request path.
- **Feature-flagged** via `app_settings` per CLAUDE.md gate 3, default ON in dev/staging,
  ON in production after one staging soak.
- **Blast radius:** every log line in the backend passes through this filter. Performance
  matters — pre-compile the regex set, short-circuit on a fast substring pre-check, and
  benchmark against `perf_baseline.py` before merge. A slow filter here breaches every
  latency SLA at once.
- **Verify:** parametrised tests for each pattern (raw lat/lng, `+1306…`, email,
  `license_number=`), plus a negative-control set asserting geohashes, `phone_last4`, UUIDs,
  and ride IDs pass through unblocked. False positives on user_ids would be as bad as the leak.
- **Rollback:** `app_settings` flag off, no redeploy.

### T3 — Constrain live-location disclosure
**Priority:** P0 · **Files:** `backend/routes/drivers/location.py`,
`backend/routes/websocket.py`, `backend/tests/test_nearby_authz.py` (new)
**Effort:** M

Four changes, one logical goal — a caller may only see driver positions relevant to booking
a ride, at a precision that does not enable tracking:

1. **Hard-cap `radius`** at `min(caller_radius, app_settings.search_radius_km * 2)` on both
   paths. Reject >cap with 422 rather than silently clamping (silent clamping hides
   scanner behaviour from the logs).
2. **Geo-bound the WebSocket query.** Apply the same `dispatch_geo_bounds(lat, lng, radius)`
   `$and` that the REST path already uses (`location.py:207`). This is the single highest-value
   line in the task — it turns a province-wide fetch into an indexed box scan.
3. **Presence-filter the WebSocket path** via `present_driver_ids_checked`, matching the REST
   path's three-case fallback (`location.py:228-240`). Removes ghost cars from the realtime map.
4. **Coarsen pre-match precision.** Round `lat`/`lng` to ~3 decimals (≈110 m) for callers with
   no assigned ride; return exact coordinates **only** to a rider whose `ride.driver_id`
   matches, in a state in `{driver_assigned, driver_accepted, driver_arrived, in_progress}`.

- **Deliberately NOT doing yet:** deriving map scope from a server-authorized quote/booking
  session, as the review recommends. That is the right end state but it is a rider-app +
  backend contract change, and shipping it under launch pressure risks breaking the map for
  every rider. The radius cap + coarsening closes the harvesting vector now; session-scoped
  map access is Phase 3 T13.
- **Blast radius (the real risk in this task):** the rider map consumes these coordinates for
  marker placement. 110 m rounding is **visible** — a car will appear up to a block off, and
  markers may visually overlap. This is a UX change to a live-tested surface, so per gates 3
  and 5 it ships behind an `app_settings` flag with the "User experience effect" field filled
  in. Check `rider-app` for any client-side ETA or distance calculation derived from these
  coordinates before merging — if the client computes "2 min away" from the marker position,
  rounding degrades it and the ETA must move server-side.
- **Also fold in:** replace the full-row `serialize_doc(drivers)` dump at `location.py:291`
  with the same allowlisted projection used by `/nearby`.
- **Verify:** authz tests for oversized radius, unauthenticated WS scan, rider with no
  active ride (coarse only), rider matched to driver (exact), rider matched to a *different*
  driver (coarse). Both REST and WS. Plus `npm run build` is **not** applicable here
  (backend-only), but a rider-app visual check of the map is — and there is **no automated
  visual regression tooling for the rider map** (standing gap, `ACTION_ITEMS.md`), so this
  will be a manual check, stated as such.
- **Rollback:** flag off restores exact coordinates and the uncapped radius.

### T4 — Historical exposure assessment (breach protocol)
**Priority:** P0 · **Files:** `docs/change-log/2026-07-29-gps-log-exposure.md` (new)
**Effort:** M · **Depends on:** T1 · **Not a code task**

CLAUDE.md: *"Any suspected PII exposure (wrong user saw another user's data, leaked logs, RLS
bypass) is a P0 incident. Within 24h: scope assessment, log capture, preserve evidence.
Within 72h: Privacy Commissioner notification if the breach poses 'real risk of significant
harm'."* Follow `docs/runbooks/data-breach.md`.

Scope assessment must answer, with evidence:
1. **When did the leak start?** `git log -S "[GO-ONLINE] db_supabase.update_one" -- backend/repositories/_base.py`
   (and its pre-split path `backend/db_supabase.py`) gives the first-shipped commit and date.
   ⚠️ **Run `git fetch --unshallow` first.** The working checkout is a shallow clone (141
   commits) and the block sits on the grafted boundary, so `git log -S` and `git blame` both
   return the graft commit rather than the real one. Without unshallowing, this step silently
   produces a wrong date.
2. **What retention applies?** Fly.io and Railway log-retention windows, plus any log drain
   or third-party aggregator configured on either host. **Open question — needs the account
   owner; not answerable from the repo.**
3. **Who could read those logs?** Everyone with Fly/Railway dashboard access. Enumerate.
4. **How many drivers, over what period?** Bounded by driver count × online hours in the
   retention window.
5. **Real risk of significant harm?** Note honestly for legal: this is location history plus
   driver licence numbers for identifiable contractors. That combination is materially
   stronger than either alone. **This determination is legal's, not engineering's.**

**This is the item most likely to be skipped under launch pressure, and the one with a
statutory clock on it.** A code fix without the assessment leaves an unreported breach.

### T5 — Close the CI baselining window
**Priority:** P0 · **Files:** `.github/workflows/security-gates.yml`, `.semgrep/` baseline,
`docs/ci-security-gates.md` · **Effort:** M

1. **Measure first.** Run Bandit, Semgrep, pip-audit, and ESLint locally on `main` and record
   the finding counts. Flipping the gates before knowing the debt red-walls every PR.
2. **Baseline the known debt** — `.bandit` / `# nosec` with justification, `.semgrepignore`,
   pip-audit `--ignore-vuln` with a documented CR each. Baselines must be *itemised*, not
   blanket-suppressed.
3. **Strip the four `|| true`s** (`:42`, `:83-84`, `:105`, `:121`) and flip the two gitleaks
   jobs to `continue-on-error: false`.
4. **Prove the gate bites** — add intentionally-insecure fixtures (a hardcoded secret, an
   `eval`, a known-vulnerable pin) in a throwaway PR and confirm each job fails. A gate that
   has never failed has never been tested.
5. **Require the checks in branch protection on `main`.** ⚠️ **Needs repo-owner action** —
   this cannot be done from the working tree, and without it steps 1-4 are cosmetic.
6. Per CLAUDE.md gate 8: for anything red for reasons unrelated to a given diff, file a `[CR]`
   using `.github/ISSUE_TEMPLATE/ci_change_request.yml` rather than leaving a permanently-red
   gate unexplained. Do **not** force a dependency bump to green a check without running the
   affected build/lint/tests.

- **Rollback:** re-add `continue-on-error: true` — a workflow-only change, instant.

### T6 — Verified-only audit attribution
**Priority:** P1 (P0 if T4 finds a reportable breach — forensic integrity)
**Files:** `backend/core/middleware.py`, `backend/tests/test_middleware_attribution.py` (new)
**Effort:** S

Remove `_extract_user_id`'s unverified `jwt.decode`. Bind `request_id` only in
`RequestIDMiddleware`; attach `user_id` to the loguru context **after** authentication
resolves it, from `request.state` set by the verified `get_current_user` dependency.

- **Blast radius:** log lines emitted *before* auth resolves lose their `user_id` field.
  That is correct — those lines never had a trustworthy one. Grep for any Sentry rule, alert,
  or dashboard that filters on `user_id` presence in early-request logs; if one exists it
  will go quiet and needs re-pointing.
- **Verify:** test that a forged unsigned JWT claiming another `user_id` produces log context
  with **no** `user_id`, and that a valid token produces the correct one.

---

## 3. Phase 2 — First post-launch release

Real gaps, not launch blockers. Ordered by risk-reduction per unit of effort.

### T7 — Adopt the existing PostGIS path for nearby lookups
**Files:** `backend/repositories/driver_repo.py`, `backend/routes/drivers/location.py`,
`backend/migrations/272_*.sql` (only if the RPC needs a signature change — **272 is the next
free number**; re-check with `ls backend/migrations | sort -V | tail -1` before claiming it)

The substrate is already shipped (migration 170: `location_geog` + partial GIST index + sync
trigger; `find_nearby_drivers` RPC at `driver_repo.py:107`). Swap the bounding-box +
Python-distance filter for `ST_DWithin` with KNN ordering and server-side limit.

- Follow migration 170's own deployment discipline: flag-gated, fall back to the Python scan
  on any error, and **run `EXPLAIN ANALYZE` against a real polygon to confirm the GIST index
  is used** before enabling. Verify driver-id type parity with
  `utils/driver_presence.present_driver_ids` — 170's header calls this out as a known trap.
- Order matters: **T3 first.** Do not optimise a query whose authorization is still wrong.

### T8 — One client error adapter for admin-dashboard
**Files:** `admin-dashboard/src/lib/error-adapter.ts` (new) + incremental migration of the
102 call sites

Map backend `error_code` → allowlisted user-safe copy + retry affordance + request ID.
Technical strings go to Sentry only. The backend contract already exists in
`docs/runbooks/error-responses.md` — this is the client half of it.

- **Sequence by exposure, not by file count.** The 5 unauthenticated sites first
  (`company-login`, `company-signup`, `register/driver`), then admin-only screens. A big-bang
  refactor of 102 sites in one PR is unreviewable and violates the ~200-line batch rule.
- **Requires a real `npm run build`** per CLAUDE.md, not just `tsc --noEmit`.

### T9 — WebSocket lifecycle
**Files:** `backend/socket_manager.py`, `backend/tests/test_socket_manager.py`

`await old.close()` on supersede at `socket_manager.py:65`, guarding against closing the new
socket (the existing disconnect guard already handles the eviction race — do not regress it).
Add a duplicate-connection concurrency test.

### T10 — DB pool saturation alerting
**Files:** `backend/utils/metrics.py`, alert config

The `spinr_db_thread_pool_queue_depth` gauge already exists (`_base.py:162`). Add a P95
saturation alert and a circuit-breaker-state metric. Mechanism is built; observability isn't.

### T11 — Transactional outbox for dispatch kickoff
**Files:** new migration, `backend/routes/rides/booking.py`, new worker loop

Replace `_deps.spawn(_prep_and_dispatch(...))` (`booking.py:1216`) with an outbox row +
durable worker, keyed idempotently on ride ID. Follow the `spinr-background-loop` skill's
replay-safety contract — this loop runs on every replica.

- Largest and riskiest item here. Touches the booking path, so it needs the
  `mock_supabase_client` dry run required by CLAUDE.md gate 4 plus a crash-recovery test
  (commit ride → kill before task runs → restart → assert exactly-once dispatch).

### T12 — Regression tests for the P0 classes
**Files:** `backend/tests/test_pii_log_regression.py`, extend `test_nearby_authz.py`,
`.github/workflows/` gate-proving fixtures

Codify all four P0s as tests so they cannot silently return. Per CLAUDE.md, a verified
finding gets a regression test — these four are the highest-value tests in the repo right now.

---

## 4. Phase 3 — Scale readiness (post-launch, in order)

| Task | Note |
|---|---|
| T13 — Session-scoped map access | The proper fix behind T3's stopgap: derive map scope from a server-authorized quote/booking session; H3/geohash clusters pre-assignment, exact position only post-match. Rider-app contract change. |
| T14 — OpenTelemetry spans + SLO dashboards | `traceparent` propagation already exists in shared clients — preserve it. Instrument dispatch, fare, payment, WS fan-out, DB retries. **Never export raw GPS.** Overlaps open item D2. |
| T15 — Load / soak against the SLA table | Harness already built per E2. Target: 100+ concurrent location updates, high WS fan-out, nearby lookup, dispatch matching, payment completion. |
| T16 — Canadian-residency DR drill | Restore, failover, replay, Stripe webhook recovery. Overlaps open items C1, E7. |
| T17 — Bounded domain service extraction | Continue the god-file split into location / dispatch-offers / settlement / notification services. **Not a rewrite** — the repository façade is the right boundary to standardise return types, deadlines, redaction, retry policy, and telemetry once. |

**Explicitly deferred, and I agree with the review's reasoning:** ML dispatch. Keep the
heuristic until clean telemetry exists. Any future model launches in shadow mode with
fairness, WAV, service-animal, and cancellation-bias evaluation — not acceptance-rate
optimisation. And per "What Spinr Is NOT": no microservice split, no unbounded dynamic
pricing, no third-party ad SDKs.

---

## 5. Sequencing

```
T1 (log leak) ──┬──► T2 (sink tripwire) ──► T12 (regression tests)
                └──► T4 (breach assessment)  ⏰ 24h/72h statutory clock
T3 (location authz) ─────────────────────────► T7 (PostGIS adopt) ──► T13 (session scope)
T5 (CI gates) ── independent, needs repo-owner branch protection
T6 (attribution) ── independent; escalate to P0 if T4 finds a reportable breach
```

**Critical path to launch:** T1 → T2 → T4, in parallel with T3 and T5. T6 is small enough to
land alongside.

**Do not reorder T3 before T1.** T1 is one file and stops active daily leakage; T3 is a
flagged UX change needing a manual map check.

---

## 6. Open decisions — need answers before Phase 1 closes

1. **Fly.io / Railway log retention window and any configured log drains.** Blocks T4's scope
   assessment. Not answerable from the repo.
2. **Who makes the "real risk of significant harm" call?** Engineering can scope the
   exposure; the Privacy Commissioner notification decision is legal's.
3. **Rider-map coordinate precision.** Is ~110 m rounding acceptable pre-match, or does the
   product require exact markers? If exact, T3 must ship the session-scoped design (T13)
   instead of the stopgap — bigger, slower, but no UX regression.
4. **Branch-protection change on `main`** for T5 step 5. Requires repo-owner action.
5. **Launch date vs Phase 2.** T11 (outbox) is the one Phase 2 item whose absence has a
   plausible launch-week failure mode (replica restart mid-booking leaves a ride in
   `searching`). The stuck-ride sweeper mitigates it. Accept for launch, or pull T11 forward?

---

## 7. Scope of this verification

Static review of this checkout only. I did **not** run the backend test suite, execute
Bandit/Semgrep/pip-audit to measure the actual CI debt (T5 step 1), query production or
staging logs to confirm the leak is present in captured platform logs, or check Fly/Railway
retention settings. The T5 baseline counts and the T4 exposure window are therefore
**unmeasured** — both are first steps of their tasks, not conclusions of this document.

The review's own caveat applies to it as well: it was read-only, and did not execute the test
suites or live dependency/security scans it recommends.
