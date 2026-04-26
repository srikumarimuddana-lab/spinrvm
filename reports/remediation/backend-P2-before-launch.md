# P2 — Backend Before-Launch: Fix Before Public Launch

These 9 items are MED/LOW severity gaps that must be closed before opening the platform to the general public. They cover money-arithmetic safety in corporate billing, Stripe webhook robustness, audit-log tamper resistance, dispatch + DSAR performance, container hardening and reconciliation completeness.

Source audit: `reports/audits/2026-04-23-backend-api-v1.txt`
Branch: `claude/audit-continuation-batch-2`

**Estimated total effort:** ~21 hours.

---

## B-P2-1 · `float()` Used in Corporate Allowance Path — Cents Lost on Large Grants

**What's wrong:** `backend/routes/corporate_company.py:269,272` reads `amount = float(request['amount'])` and `floor = float(wallet.get('soft_negative_floor', -50))`. Money in this codebase must use `Decimal` only — the fare pre-commit hook enforces this, but the corporate paths are outside that hook's coverage. Grants in the thousands lose cents under repeated arithmetic.

**File to fix:** `backend/routes/corporate_company.py:269,272` (and any sibling under `backend/routes/corporate_*.py`)

**How to fix:**
```python
amount = Decimal(str(request['amount']))
floor = Decimal(str(wallet.get('soft_negative_floor', -50)))
```
Extend the pre-commit float-arithmetic hook to also scan `backend/routes/corporate_*.py` and `backend/services/corporate_*.py` so this regression cannot recur.

**Regression test:** `test_allowance_grant_preserves_cents` — grant `$1234.56`, settle, assert wallet balance is exactly that to the cent.
Static check: `grep -nE 'float\(' backend/routes/corporate_*.py` returns zero matches.

**Why it matters:** Same family as 20-1 on the rider wallet. Corporate ledgers drift in cents which become dollars across thousands of monthly grants → CRA tax-record discrepancies and chargebacks.

**Effort:** 2 h · **Severity:** MEDIUM · **Risk score:** 16 · **Regulations:** CRA, PCI-DSS · **Audit ref:** 04-1

---

## B-P2-2 · Stripe Webhook Has No Event-Type Allowlist — Unknown Events Silently Marked Processed

**What's wrong:** `backend/routes/webhooks.py:94,138,190,218-219` routes events through an if/elif chain on `event_type`; the `else` branch logs `"Unhandled event"` *and still calls* `mark_stripe_event_processed(event_id)`. Any future event type Stripe rolls out (refund, dispute, chargeback, payout.paid, account.updated, etc.) is silently accepted with no business logic and an incomplete audit trail.

**File to fix:** `backend/routes/webhooks.py:94,138,190,218-219`

**How to fix:**
```python
ALLOWED_EVENTS = {
    "payment_intent.succeeded",
    "payment_intent.payment_failed",
    "charge.refunded",
    "charge.dispute.created",
    # ... fully enumerate before launch
}

if event_type not in ALLOWED_EVENTS:
    logger.error("stripe_webhook: unknown event", extra={"event_type": event_type})
    raise HTTPException(400, "unknown_event")  # do NOT mark_processed
```
Pair with a Sentry alert on 400 events so the SRE on-call sees new event types and adds them deliberately.

**Regression test:** Send a `charge.refunded` event via the Stripe CLI; expect 400 + Sentry alert, *not* 200 + `processed_at`.

**Why it matters:** PCI-DSS audit and SOC2 CC7.2 both require explicit handling of payment events. Silent acceptance breaks reconciliation when Stripe adds new event types we should be acting on.

**Effort:** 2 h · **Severity:** MEDIUM · **Risk score:** 16 · **Regulations:** PCI-DSS, SOC2 · **Audit ref:** 08-1

---

## B-P2-3 · App Check Error Logs Missing `request_id` Correlation

**What's wrong:** `backend/core/middleware.py:78,94` logs App Check token failures at warning level *without* binding the `request_id` set by `RequestIDMiddleware`. Cross-request log correlation fails — investigating an App Check rejection requires manual timestamp matching across services.

**File to fix:** `backend/core/middleware.py:78,94`

**How to fix:**
```python
with logger.contextualize(request_id=request.state.request_id):
    logger.warning("app_check rejected", extra={"reason": reason})
```
Or attach `request_id` via `extra={"request_id": request.state.request_id}` on each log line.

**Regression test:** Trigger an App Check failure, capture the log entry, assert `X-Request-ID` value is present in the structured log.

**Why it matters:** Log forensics gets exponentially harder without request correlation. Doesn't violate a regulation directly but makes incident response slower across the board.

**Effort:** 1 h · **Severity:** MEDIUM · **Risk score:** 12 · **Audit ref:** 10-2

---

## B-P2-4 · `audit_logs` Has RLS But No Append-Only Trigger (SOC2 CC6.2)

**What's wrong:** `backend/migrations/06_cloud_messaging.sql:70-72` adds RLS to `audit_logs` but no UPDATE/DELETE-blocking trigger. An admin (or compromised service-role key) with UPDATE could erase audit entries, defeating the tamper-evidence requirement that SOC2 CC6.2 audits expect.

**File to fix:** new `backend/migrations/NN_audit_logs_append_only.sql` (pick the next free `NN`)

**How to fix:**
```sql
CREATE OR REPLACE FUNCTION reject_audit_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs is append-only (no UPDATE/DELETE)';
END;
$$;

CREATE TRIGGER audit_logs_no_update
    BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation();
```
Document the rollback path in a top-of-file comment per the migration rules.

**Regression test:** `test_audit_logs_append_only` — INSERT succeeds; UPDATE raises; DELETE raises.

**Why it matters:** Without DB-level enforcement, every audit entry is only as trustworthy as the most-privileged identity that can reach the table. SOC2 evidence + non-repudiation.

**Effort:** 3 h · **Severity:** MEDIUM · **Risk score:** 20 · **Regulations:** SOC2 · **Audit ref:** 12-3

---

## B-P2-5 · Driver-Candidate Dispatch Filter Has No Composite Index

**What's wrong:** Driver-candidate selection filters on `(is_online, is_available, vehicle_type_id)` but `backend/migrations/34_rides_performance_indexes.sql` adds no composite index covering this predicate. Sequential scan on `drivers` once the fleet exceeds ~500 drivers; dispatch latency grows linearly.

**File to fix:** new `backend/migrations/NN_drivers_dispatch_composite_index.sql`

**How to fix:**
```sql
CREATE INDEX CONCURRENTLY idx_drivers_online_available_type
    ON drivers (is_online, is_available, vehicle_type_id)
    WHERE is_online = true AND is_available = true;
```
A partial index keeps the index tiny since the vast majority of `drivers` rows have one or both flags false.

**Regression test:** `EXPLAIN ANALYZE` of the candidate query shows `Index Scan` not `Seq Scan`.

**Why it matters:** Same SLA family as B-P1-8. Closes the second of two known dispatch hot-paths.

**Effort:** 2 h · **Severity:** MEDIUM · **Risk score:** 20 · **Audit ref:** 14-3

---

## B-P2-6 · DSAR Export Issues 6 Sequential `get_rows` Calls — Multiplies SLA Risk

**What's wrong:** `backend/routes/drivers.py:1529,1541` (`_build_and_email_data_export`) issues 6 awaits in series. Total latency is the sum of 6 round-trips even though there's no order dependency between them. Compounds the 30-day DSAR SLA risk tracked by 12-2 / DV-17.

**File to fix:** `backend/routes/drivers.py:1529-1541`

**How to fix:**
```python
results = await asyncio.gather(
    db.get_rows("rides", {"user_id": user_id}),
    db.get_rows("payments", {"user_id": user_id}),
    db.get_rows("ratings", {"user_id": user_id}),
    db.get_rows("documents", {"user_id": user_id}),
    db.get_rows("ride_offers", {"user_id": user_id}),
    db.get_rows("audit_logs", {"actor_id": user_id}),
    return_exceptions=False,  # surface DB failures loudly per CLAUDE.md
)
```

**Regression test:** Mock `get_rows`, assert all 6 issued within 50 ms of each other; full export under 200 ms with realistic data.

**Why it matters:** PIPEDA DSAR responsiveness; faster export reduces queue depth and oldest-pending age (the metric added in B-P1-9).

**Effort:** 2 h · **Severity:** MEDIUM · **Risk score:** 16 · **Regulations:** PIPEDA · **Audit ref:** 14-4

---

## B-P2-7 · `run_sync` Uses Default Thread Pool — Concurrent DB Calls Block Each Other

**What's wrong:** `backend/db_supabase.py:83,95` uses `loop.run_in_executor(None, ...)`. The default executor on a 4-core container is `min(32, cpu+4) = 8` threads. Under load, concurrent DB calls block each other; dispatch stalls during traffic spikes.

**File to fix:** `backend/db_supabase.py:83,95` + new `backend/core/db_pool.py` (optional)

**How to fix:**
```python
_db_executor = ThreadPoolExecutor(
    max_workers=int(os.environ.get("DB_THREAD_POOL_MAX", "16")),
    thread_name_prefix="supabase-",
)
# in run_sync:
return await loop.run_in_executor(_db_executor, ...)
```
Export queue depth + active threads as `spinr.db.thread_pool.queue_depth` / `.active_threads` metrics so we see saturation before customers do.

**Regression test:** Load test with 50 concurrent requests; assert P99 dispatch latency under budget; verify `max_workers=16` visible in metrics.

**Why it matters:** This is the upstream cause of every "dispatch felt slow" report under load. Independently of fixing it, we should be able to *see* it via metrics — which we can't today.

**Effort:** 3 h · **Severity:** MEDIUM · **Risk score:** 16 · **Audit ref:** 14-5

---

## B-P2-8 · Dockerfile Uses Mutable Base Tag; Writable Root FS; No Seccomp Profile

**What's wrong:** `backend/Dockerfile:35-42` uses `FROM python:3.12.9-slim` — version-pinned but tag is mutable (the registry can repoint it). Container runs as `spinr` user (good) but root FS is writable; no seccomp profile. Container-breakout + persistence would survive a restart.

**File to fix:** `backend/Dockerfile:35-42` + Railway/Render deploy config

**How to fix:**
1. Pin by content hash: `FROM python@sha256:<digest>` (record the digest in `docs/build-pinning.md`).
2. Mount root FS read-only in Railway (`readOnlyRootFilesystem: true` or platform equivalent).
3. Document a baseline seccomp profile (start from `runtime/default`).

**Regression test:** Image has `FROM python@sha256:`; deploy spec contains `read-only-root-filesystem: true`; runtime denies a write to `/`.

**Why it matters:** Defense in depth. A compromised dependency that needs to write a payload to disk simply cannot. SOC2 evidence.

**Effort:** 3 h · **Severity:** MEDIUM · **Risk score:** 20 · **Regulations:** SOC2 · **Audit ref:** 22-2B

---

## B-P2-9 · Payment-Retry Loop Lacks Per-Attempt Audit Events (blocked on rider 20-3)

**What's wrong:** `backend/utils/payment_retry.py:114,133,145` is correctly idempotent (Stripe key reused) but writes no `financial_events` row per attempt. Reconciliation cannot disambiguate "settled on first attempt" from "settled on retry attempt 3".

**File to fix:** `backend/utils/payment_retry.py:114,133,145`

**How to fix:** After each retry settles (success *or* failure), insert a row:
```python
await db.insert_row("financial_events", {
    "event_type": "payment_retried",
    "ride_id": ride_id,
    "attempt": attempt_no,
    "idempotency_key": stripe_idem_key,
    "outcome": outcome,
    "settled_at": datetime.utcnow(),
})
```

**Blocked on:** rider Phase E finding 20-3 (`financial_events` table creation). Sequence this fix immediately after that table lands.

**Regression test:** Trigger a retry; verify a `financial_events` row appears with the correct `attempt` count and `idempotency_key`.

**Why it matters:** Without per-attempt rows, chargeback investigations cannot tell which retry actually moved money. SOC2 + CRA evidence.

**Effort:** 2 h · **Severity:** LOW · **Risk score:** 12 · **Regulations:** SOC2, CRA · **Audit ref:** 20-1B (blocked on rider 20-3)

---

## Checklist

- [ ] B-P2-1 Replace `float()` with `Decimal(str(...))` in corporate paths; extend pre-commit hook (04-1)
- [ ] B-P2-2 Stripe webhook event-type allowlist; reject 400 on unknown (08-1)
- [ ] B-P2-3 Bind `request_id` to App Check log lines (10-2)
- [ ] B-P2-4 Append-only trigger on `audit_logs` (12-3)
- [ ] B-P2-5 Composite partial index on `drivers(is_online,is_available,vehicle_type_id)` (14-3)
- [ ] B-P2-6 `asyncio.gather()` the DSAR export (14-4)
- [ ] B-P2-7 Explicit ThreadPoolExecutor + queue-depth metric (14-5)
- [ ] B-P2-8 Pin Dockerfile base by content digest; read-only root FS; seccomp baseline (22-2B)
- [ ] B-P2-9 `financial_events` row per payment retry attempt (20-1B, blocked on rider 20-3)

## After this file

- Move on to `backend-P3-hardening.md` (3 items): synchronous broadcast on `socket_manager`, no jitter on background loops, sub-processor monitoring cadence.
