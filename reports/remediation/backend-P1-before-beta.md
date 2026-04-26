# P1 — Backend Before-Beta: Fix Before First Closed-Beta Cohort

These 10 items are HIGH/MED severity gaps that must be closed before any external beta. They cover authentication hardening (Firebase audience, JWT secret length), CLAUDE.md error-handling violations, regulatory retention obligations (PIPEDA/CRA), dispatch + history hot paths, and supply-chain integrity.

Source audit: `reports/audits/2026-04-23-backend-api-v1.txt`
Branch: `claude/audit-continuation-batch-2`

**Estimated total effort:** ~50 hours across backend + devops.

---

## B-P1-1 · Firebase ID Token Verification Skips Audience Binding (DV-10)

**What's wrong:** `verify_id_token(body.firebase_token)` is called without an `audience=` parameter. The audience claim *is* checked manually but only inside `if driver_app_id:` — and `driver_app_id` is read from an env var. If the env var is unset (or empty in any deploy) the audience check is silently skipped, so a rider's Firebase token can authenticate on the driver auth path.

**File to fix:** `backend/routes/auth.py:392,403,407,408`

**How to fix:**
```python
audience = settings.FIREBASE_DRIVER_APP_ID  # raise at startup if missing in prod
decoded = firebase_auth.verify_id_token(body.firebase_token, audience=audience)
```
Add startup-time validation in `backend/core/config.py` so production fails fast when `FIREBASE_DRIVER_APP_ID` / `FIREBASE_RIDER_APP_ID` are unset.

**Regression test:** Rider Firebase token rejected on driver auth path; absent env var → startup `RuntimeError` in production mode.

**Why it matters:** Cross-app token acceptance lets a rider's stolen Firebase token claim a driver session — the audit-confirmed instance of DV-10.

**Effort:** 2 h · **Severity:** HIGH · **Risk score:** 24 · **Regulations:** PIPEDA · **Audit ref:** 02-1 (DUP DV-10)

---

## B-P1-2 · `JWT_SECRET` Length Not Enforced — HS256 Brute-Forceable

**What's wrong:** `_guard_production_secrets` in `backend/core/config.py:84-99` blocks placeholder strings (`"changeme"`, `"admin123"`, etc.) but never checks length. CLAUDE.md mandates ≥32 chars; the code does not enforce it. Production can boot with a 4-character `JWT_SECRET` and HS256 falls in seconds against any modern GPU.

**File to fix:** `backend/core/config.py:84-99`

**How to fix:**
```python
if env == "production" and len(value) < 32:
    raise ValueError(f"{name} must be ≥32 chars in production (got {len(value)})")
```

**Regression test:** `test_jwt_secret_length_enforced` — set `ENV=production`, `JWT_SECRET="short"`, expect startup raises.

**Why it matters:** Token forgery → full account takeover at any privilege level. CLAUDE.md ground rule.

**Effort:** 1 h · **Severity:** HIGH · **Risk score:** 28 · **Regulations:** PIPEDA, SOC2 · **Audit ref:** 03-1

---

## B-P1-3 · Coverage Reported But Never Gated (no `--cov-fail-under`)

**What's wrong:** `backend/pytest.ini:14-19` emits term/html/xml coverage reports but lacks `--cov-fail-under=N`. The file's own comment acknowledges a ~6% baseline. Coverage can drop to 0% in any module without CI failure.

**File to fix:** `backend/pytest.ini:14-19`

**How to fix:** Set a global gate at 70% and stricter per-route targets in CI (auth ≥95%, payments ≥90%, rides ≥85%). Gate via `--cov-fail-under=70` plus a separate per-package job that asserts the per-route minimums.

**Regression test:** Drop coverage below 70% locally → CI red.

**Why it matters:** Without a floor, refactors silently bury regressions; the coverage minimums in CLAUDE.md are aspirational only.

**Effort:** 4 h · **Severity:** MEDIUM · **Risk score:** 24 · **Regulations:** SK-CPPA · **Audit ref:** 09-1

---

## B-P1-4 · Large `_STALE_TEST_CLASSES` Frozenset — Critical Paths Skipped at Collection

**What's wrong:** `backend/tests/conftest.py:378-407` skips a sizable slate of test classes via `_STALE_TEST_CLASSES`. Comments mark them "0/N stale, needs rewrite for Supabase backend". Until rewritten, integration coverage on critical paths is nominal.

**File to fix:** `backend/tests/conftest.py:378-407`

**How to fix:** Quarterly audit; rewrite ≥10 classes/quarter; emit `tests_stale_count` and `days_since_last_rewrite` as metrics so the backlog is visible. Track each rewrite as its own PR.

**Regression test:** Metric on the dashboard; CI fails if `tests_stale_count` grows.

**Why it matters:** Every stale class is invisible technical debt that hides real regressions. Ratings, dispatch and payment paths are partially covered today.

**Effort:** 24 h spread across multiple sprints · **Severity:** HIGH · **Risk score:** 32 · **Audit ref:** 09-2

---

## B-P1-5 · `logger.warning` on Firebase / DB Persistence Failures (CLAUDE.md violation)

**What's wrong:** `backend/routes/auth.py:96,100,437,443` logs `logger.warning(f"firebase_auth: could not persist user {uid}: {e}")` and continues. CLAUDE.md ground rule: DB / auth / payment errors must use `logger.error(..., exc_info=True)` and surface a clean `HTTPException` (503 for DB, 502 for upstream). Production log aggregation typically filters warnings — silent partial-state bugs (Firebase user created without DB row) hide.

**File to fix:** `backend/routes/auth.py:96,100,437,443`

**How to fix:**
```python
except DatabaseError as e:
    logger.error(
        "firebase_auth: could not persist user",
        extra={"uid": uid, "original": e.details.get("original")},
        exc_info=True,
    )
    raise HTTPException(503, "auth_persist_failed")
```
Audit-log the partial-state too: an entry in `auth_failures` so reconciliation can detect Firebase-only ghosts.

**Regression test:** Mock `db_supabase.upsert_user` to raise → assert logger.error captured + 503 returned + audit row written.

**Why it matters:** Direct CLAUDE.md violation; produces orphaned Firebase users that look authenticated but have no app-side identity. PIPEDA breach risk if those rows leak.

**Effort:** 2 h · **Severity:** HIGH · **Risk score:** 24 · **Regulations:** PIPEDA · **Audit ref:** 10-1

---

## B-P1-6 · Soft-Delete Columns Exist But No Retention-Purge Cron (DV-8)

**What's wrong:** `backend/migrations/33_soft_delete_columns.sql:1-13` adds `deleted_at` columns + indexes on `drivers`/`users`/`rides`, but no scheduled hard-delete job runs. Soft-deleted rows persist past the 2-year PIPEDA retention horizon (and the 7-year CRA horizon for tax records when applicable). Confirms DV-8 is still open.

**File to fix:** new `backend/utils/retention_purge.py` + spawn from `backend/core/lifespan.py`

**How to fix:**
1. Implement `retention_purge_loop()` per the background-loop recipe in CLAUDE.md (atomic-claim, replay-safe).
2. Run daily at 02:00 UTC; per-table thresholds:
   - `users`/`drivers`: hard-delete soft-deleted rows older than 2 years (PIPEDA), retain only fields the Saskatchewan Transportation Act mandates (driver/vehicle linkage at trip time, etc.).
   - `rides`: anonymize (null `user_id`, round coordinates to city) at 2 years; full hard-delete at 7 years.
3. Alert on row counts > N to detect runaway accumulation.
4. Log every purge to `audit_logs` with row counts (no PII).

**Regression test:** Insert a soft-deleted row dated 3 years ago; run loop tick; assert row deleted (or anonymized) and `audit_logs` entry written.

**Why it matters:** PIPEDA non-compliance is a regulatory P0 once data ages past the 2-year window. Soft delete without purge is the worst case — pretending to delete while retaining indefinitely.

**Effort:** 6 h · **Severity:** HIGH · **Risk score:** 28 · **Regulations:** PIPEDA, CRA · **Audit ref:** 12-1 (DUP DV-8)

---

## B-P1-7 · Ride-History Endpoint Fetches 2000 Rows on Every Page Click

**What's wrong:** `backend/routes/rides.py:1092,1097` calls `get_rows('rides', {'rider_id': id}, limit=2000)` and then filters/sorts in Python. There is no DB-level status filter and no pagination. Latency grows linearly with each rider's history; bandwidth burns the same 2000 rows per page click.

**File to fix:** `backend/routes/rides.py:1092,1097`

**How to fix:** Push `status` filter and pagination into the DB query.
```python
rides = await db_supabase.get_rows(
    'rides',
    {'rider_id': rider_id, 'status': {'$in': ('completed', 'cancelled')}},
    order='created_at', desc=True,
    limit=page_size, offset=cursor,
)
```
Cap `page_size` at 100. Return a `next_cursor` instead of an offset for stable pagination.

**Regression test:** Log SQL; verify `LIMIT 100` + `WHERE status IN (...)` applied; benchmark vs old.

**Why it matters:** P95 fare-display SLA (300 ms) breached as power riders' histories grow; CPU and bandwidth waste compounds across replicas.

**Effort:** 3 h · **Severity:** HIGH · **Risk score:** 32 · **Regulations:** SK-CPPA · **Audit ref:** 14-1

---

## B-P1-8 · Round-Robin Dispatch Scans Entire Rides Table for Last-Assigned Driver

**What's wrong:** `backend/services/dispatch_service.py:239` calls `get_rows('rides', {'driver_id': {'$ne': None}}, order='created_at', desc=True, limit=1)` to find the last-assigned driver. There is no supporting index, so each dispatch decision is an O(n) scan on `rides`. Past ~100k rides we breach the 2 s P95 dispatch SLA.

**Files to fix:**
- `backend/migrations/NN_dispatch_round_robin_index.sql` (new — pick the next free `NN`, currently 38 if 37 is taken)
- `backend/services/dispatch_service.py:239`

**How to fix:**
```sql
CREATE INDEX CONCURRENTLY idx_rides_driver_assigned_at
  ON rides (driver_id, assigned_at DESC)
  WHERE driver_id IS NOT NULL;
```
Then change the query to ORDER BY `assigned_at` (more semantically correct than `created_at`) and use the index.

**Regression test:** `EXPLAIN` shows `Index Scan` not `Seq Scan`; benchmark dispatch latency against a fixture with 100k rides.

**Why it matters:** Dispatch latency is one of our two top SLAs. Linear growth with ride volume is the textbook pre-launch landmine.

**Effort:** 2 h · **Severity:** HIGH · **Risk score:** 28 · **Regulations:** SK-CPPA · **Audit ref:** 14-2

---

## B-P1-9 · DSAR Endpoint Has No SLA Test or Completeness Assertion (DV-17 family)

**What's wrong:** `backend/routes/drivers.py:1503` defines `/me/export-data` but no test asserts the export contains every required field across rides / drivers / users / payments / ratings / documents. PIPEDA s.9 mandates a 30-day DSAR response. No metric tracks queue depth or oldest-pending request.

**File to fix:** `backend/routes/drivers.py:1503` + new test in `backend/tests/test_dsar_export.py`

**How to fix:**
1. Define a canonical `DSAR_FIELDS` schema (one entry per data class).
2. Test asserts every key is present with a non-null value (or explicit "no records" sentinel).
3. Emit metric `spinr.privacy.dsar.queue_depth` and `spinr.privacy.dsar.oldest_pending_age_seconds`. Page if oldest > 25 days.

**Regression test:** Seed a driver with one ride/payment/rating; call `/me/export-data`; assert every field in `DSAR_FIELDS` is populated.

**Why it matters:** PIPEDA enforcement requires evidence of process. Without tests + metrics we cannot demonstrate compliance during an audit.

**Effort:** 4 h · **Severity:** MEDIUM · **Risk score:** 16 · **Regulations:** PIPEDA · **Audit ref:** 12-2 (DUP DV-17 family)

---

## B-P1-10 · `requirements.txt` Lacks SHA256 Hash Verification — Supply-Chain Window

**What's wrong:** `backend/requirements.txt:1-90` pins versions only (e.g. `redis[asyncio]>=5.0.0`). PyPI typo-squatters or a single compromised package version land in production undetected. `pip install` resolves whatever is current at build time without integrity verification.

**File to fix:** `backend/requirements.txt` + `backend/Dockerfile` + CI

**How to fix:**
```bash
pip-compile --generate-hashes requirements.in -o requirements-locked.txt
```
Commit the locked file. CI installs with `pip install --require-hashes -r requirements-locked.txt`. Builds fail on any hash mismatch.

**Regression test:** Modify a single hash byte in CI → build fails; revert → passes.

**Why it matters:** Single line of supply-chain defense for the backend. SOC2 evidence requirement.

**Effort:** 4 h · **Severity:** HIGH · **Risk score:** 32 · **Regulations:** SOC2 · **Audit ref:** 22-1B (Phase E new)

---

## Checklist

- [ ] B-P1-1 Pass `audience=` to Firebase verify; fail-fast on missing env in prod (02-1, DV-10)
- [ ] B-P1-2 Enforce `len(JWT_SECRET) ≥ 32` at startup in production (03-1)
- [ ] B-P1-3 Add `--cov-fail-under=70` (and per-route targets) to `pytest.ini` (09-1)
- [ ] B-P1-4 Quarterly stale-test rewrite cadence + `tests_stale_count` metric (09-2)
- [ ] B-P1-5 `logger.warning` → `logger.error(exc_info=True)` on auth/DB persistence; 503 on failure (10-1)
- [ ] B-P1-6 Implement retention purge loop; daily 02:00 UTC; per-table thresholds (12-1, DV-8)
- [ ] B-P1-7 DB-level pagination + status filter on ride history; cap page size at 100 (14-1)
- [ ] B-P1-8 Index `idx_rides_driver_assigned_at` + query change for round-robin dispatch (14-2)
- [ ] B-P1-9 DSAR completeness test + queue-depth metric (12-2, DV-17)
- [ ] B-P1-10 `pip-compile --generate-hashes`; CI requires `--require-hashes` (22-1B)

## After this file

- Move on to `backend-P2-before-launch.md` (9 items): float() in corporate wallet, Stripe event allowlist, App Check log correlation, audit-log append-only trigger, dispatch index, DSAR concurrency, thread-pool sizing, Dockerfile content-hash pin, payment-retry per-attempt audit.
