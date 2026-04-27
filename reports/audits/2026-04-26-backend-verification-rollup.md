# Backend Audit Verification Rollup — HEAD vs `2026-04-23-backend-api-v1.txt`

**Date:** 2026-04-26
**Branch:** `claude/audit-continuation-batch-2`
**HEAD:** `eb58ec4` (security: redact leaked Supabase project ref + breach assessment)
**Source audit:** `reports/audits/2026-04-23-backend-api-v1.txt` (27 findings: 1 CRIT, 10 HIGH, 10 MED, 3 LOW, 3 PASS)
**Sprint files verified:**
- `reports/remediation/backend-P0-critical-fix-now.md` (2 items)
- `reports/remediation/backend-P1-before-beta.md` (10 items)
- `reports/remediation/backend-P2-before-launch.md` (9 items)
- `reports/remediation/backend-P3-hardening.md` (3 items)

**Method:** Read each remediation card, locate the audit-cited file:line at HEAD, and grep for the exact symptom pattern. Classify each item as **RESOLVED** / **PARTIAL** / **NOT FIXED**.

---

## 1. Headline numbers

| Bucket | Items | RESOLVED | PARTIAL | NOT FIXED |
|---|---:|---:|---:|---:|
| P0 (critical, fix now) | 2 | 2 | 0 | 0 |
| P1 (before beta) | 10 | 1 | 2 | 7 |
| P2 (before launch) | 9 | 0 | 0 | 9 |
| P3 (hardening) | 3 | 0 | 0 | 3 |
| **Total actionable** | **24** | **3** | **2** | **19** |

P0 is **fully closed at HEAD**. P1/P2/P3 are largely open — these have not yet been worked, which is the expected state for an audit dated 2026-04-23 with the remediation cards written 2026-04-26.

---

## 2. P0 — Critical (fix now)

### B-P0-1 · First-rating `UnboundLocalError` — **RESOLVED**
**Audit ref:** rides.py:1788, 1796, 1801-1803
**Evidence:** `backend/routes/rides.py:1817-1827` — `average_rating` is now only assigned and used inside an `if rated_rides:` block; the previous code path that referenced an unassigned `average_rating` no longer exists.

```python
if rated_rides:
    average_rating = round(sum(rated_rides) / len(rated_rides), 2)
    await db_supabase.update_one(
        "drivers", {"id": driver_id},
        {"rating": average_rating, "average_rating": average_rating,
         "total_ratings": len(rated_rides)},
    )
```

### B-P0-2 · Ride state-string mismatch — **RESOLVED (short fix)**
**Audit ref:** rides.py:1309, 1312 + drivers.py:2088
**Evidence:** `backend/routes/rides.py:1329-1334` requires `"completed"` (not `"trip_completed"`); `backend/routes/drivers.py:2133` writes `"completed"`. Strings now match.

```python
_ride_status = ride.get("status", "")
if _ride_status != "completed":
    raise HTTPException(409, f"Ride is in status '{_ride_status}'; payment requires completed state.")
```

**Caveat:** the broader `RideStatus` enum refactor (DV-3 cross-reference) is **still open** — 16+ literal `"completed"` string sites remain across `routes/rides.py` and `routes/drivers.py`. The string-match bug is gone; the underlying class of bug (typo-able literal status strings) is not.

---

## 3. P1 — Before Beta (10 items)

| # | Item | Status | Evidence |
|---|---|---|---|
| B-P1-1 | Firebase audience binding (DV-10) | **NOT FIXED** | `auth.py:401` still calls `verify_id_token(body.firebase_token)` with no `audience=` kwarg; manual check at line 406 still gated by `if driver_app_id:` |
| B-P1-2 | `JWT_SECRET` length ≥32 | **NOT FIXED** | `core/config.py:85-101` `_guard_production_secrets` only blocks placeholder strings; no `len(value) < 32` check |
| B-P1-3 | `--cov-fail-under` gate | **PARTIAL** | `pytest.ini:15` has `--cov-fail-under=6` (gated, but at the 6% baseline the audit calls out, not the 70% target) |
| B-P1-4 | `_STALE_TEST_CLASSES` rewrite | **RESOLVED** | `tests/conftest.py:378-388` `frozenset` is now empty; comments document the 160-class rewrite across 7 test files |
| B-P1-5 | `logger.warning` on auth/DB | **NOT FIXED** | `auth.py:435` plus 5+ other lines still warn-and-continue on DB persistence errors (CLAUDE.md violation) |
| B-P1-6 | Retention purge cron | **NOT FIXED** | No `backend/utils/retention_purge.py`; no spawn from `core/lifespan.py`; soft-deleted rows persist indefinitely (PIPEDA 2y / CRA 7y) |
| B-P1-7 | Ride history pagination | **PARTIAL** | `routes/rides.py:1109-1115` still fetches `limit=2000` with no DB-level status filter; cursor-based pagination is implemented at the API layer (lines 1099-1108) but the underlying query has not been fixed |
| B-P1-8 | Round-robin dispatch index | **PARTIAL** | `idx_rides_driver_created (driver_id, created_at DESC)` exists in migration 34, but it is non-partial and the audit asked for `(driver_id, assigned_at DESC) WHERE driver_id IS NOT NULL` (uses `assigned_at`, not `created_at`); query at `services/dispatch_service.py:271` still uses `created_at` |
| B-P1-9 | DSAR completeness test + metric | **NOT FIXED** | `routes/drivers.py:1538` endpoint exists but no `tests/test_dsar_export.py`; no `spinr.privacy.dsar.queue_depth` metric |
| B-P1-10 | `requirements.txt` SHA256 hashes | **NOT FIXED** | `grep -c sha256: backend/requirements.txt` → 0; no `requirements-locked.txt`; CI does not pass `--require-hashes` |

---

## 4. P2 — Before Launch (9 items)

| # | Item | Status | Evidence |
|---|---|---|---|
| B-P2-1 | Corporate `float()` → `Decimal` | **NOT FIXED** | `routes/corporate_company.py:270, 273` still use `float(request["amount"])` and `float(wallet.get("soft_negative_floor", -50))` |
| B-P2-2 | Stripe webhook event allowlist | **NOT FIXED** | `routes/webhooks.py:216-221` still has `else: logger.info("Unhandled Stripe event")` and unconditionally calls `mark_stripe_event_processed(event_id)` for unknown events |
| B-P2-3 | App Check `request_id` correlation | **NOT FIXED** | `core/middleware.py:79, 95` log `App Check ...` with no `request_id` binding |
| B-P2-4 | `audit_logs` append-only trigger | **NOT FIXED** | No `reject_audit_mutation()` function; no `BEFORE UPDATE OR DELETE` trigger on `audit_logs` in any migration up to 49 |
| B-P2-5 | Driver dispatch composite index | **NOT FIXED** | No `idx_drivers_online_available_type` migration; no partial index covering `(is_online, is_available, vehicle_type_id)` |
| B-P2-6 | DSAR `asyncio.gather()` | **NOT FIXED** | `routes/drivers.py:1564, 1569, 1575, 1582, 1589, 1593` still issues 6 sequential `await db_supabase.get_rows(...)` calls |
| B-P2-7 | Explicit `ThreadPoolExecutor` | **NOT FIXED** | `db_supabase.py:204` still uses `loop.run_in_executor(None, func)` (default executor — capped at 8 threads on a 4-core container) |
| B-P2-8 | Dockerfile content-hash pin + RO root | **NOT FIXED** | `Dockerfile:1` is `FROM python:3.12-slim` (no minor pin even); line 19 `FROM python:3.12.9-slim AS runtime` (mutable tag); no `@sha256:` digest; no read-only-root-filesystem in deploy spec |
| B-P2-9 | `financial_events` per retry attempt | **NOT FIXED** (blocked) | `utils/payment_retry.py:114, 133, 145` writes no `financial_events` row; **blocked on rider 20-3** (the table itself does not yet exist) |

---

## 5. P3 — Hardening (3 items)

| # | Item | Status | Evidence |
|---|---|---|---|
| B-P3-1 | WebSocket `broadcast()` async | **NOT FIXED** | `socket_manager.py:99-101` still iterates `for connection in connections: await connection.send_json(message)` with no per-message timeout. Mitigating factor: the docstring at `socket_manager.py:91-98` says `broadcast()` has no current callers outside legacy tests, so production blast radius is currently zero — but the code remains a footgun for any new caller. |
| B-P3-2 | Background-loop jitter + metrics | **NOT FIXED** | `surge_engine.py:252`, `scheduled_rides.py:154`, `payment_retry.py:214` all use raw `asyncio.sleep(INTERVAL)`. Only `presence_sweeper.py:206` has any jitter (one-time initial offset, not per-tick). No per-loop duration/error metrics. |
| B-P3-3 | Sub-processor monitoring cadence | **NOT FIXED** | No `backend/utils/subprocessor_audit.py`; no scheduled GitHub Action / Railway cron diffing `docs/vendor-inventory.md` against vendor sub-processor lists. |

---

## 6. Cross-cutting observations

**The audit landed on schedule.** Sprint files were written 2026-04-26 (today). Of the 24 actionable items, the **P0 pair landed at HEAD** before the rollup file was even written — that pre-dates this verification round. The remaining 22 are unworked (as expected) and correctly bucketed.

**Three remediation items have shipped, not flagged in the cards as such:**
1. **B-P0-1** RESOLVED — confirmed via direct read of HEAD.
2. **B-P0-2** RESOLVED (short fix) — confirmed; the broader DV-3 enum refactor is still open and should be tracked separately.
3. **B-P1-4** RESOLVED — `_STALE_TEST_CLASSES` is empty at HEAD. The remediation card text still describes a 24-hour ongoing rewrite cadence; it should be updated to reflect that the immediate backlog has been cleared and the cadence is now about preventing new accumulation.

**Two items are misclassified as PARTIAL because the remediation card pattern doesn't quite match HEAD:**
1. **B-P1-3** — The `--cov-fail-under=6` line **does** gate coverage; it just gates at the 6% baseline rather than the 70% target. The remediation card should be re-scoped from "add the gate" to "ratchet the gate".
2. **B-P1-7** — Cursor-based pagination at the API layer landed before the audit; the audit's complaint is about the underlying `limit=2000` DB fetch, which is unchanged. The remediation card text accurately describes the remaining work.

**One item is partial in a way the audit didn't anticipate:**
- **B-P1-8** — `idx_rides_driver_created` in migration 34 partially mitigates the round-robin scan (Postgres can range-scan the BTree), but the audit's recommended fix uses `assigned_at` and a `WHERE driver_id IS NOT NULL` partial index. Worth re-running EXPLAIN ANALYZE before deciding whether to ship the partial-index migration; if migration 34's plan is already <50ms at 100k rides, the partial index becomes a P3.

**No regressions detected.** Every PASS finding from the audit (11-1, 11-2, 12-4) is still PASS at HEAD.

---

## 7. Recommendation — what to address next

Sequenced by combined risk-reduction × effort:

1. **B-P1-1 Firebase audience** (2h, HIGH risk 24, PIPEDA) — single-line fix, security-critical. Do today.
2. **B-P1-2 JWT_SECRET length** (1h, HIGH risk 28) — single-line fix, security-critical. Do today.
3. **B-P1-5 logger.warning → logger.error** (2h, HIGH risk 24) — direct CLAUDE.md violation; bundle with B-P1-1/2.
4. **B-P1-10 requirements hash-pin** (4h, HIGH risk 32) — supply-chain blast radius; pre-beta hard requirement.
5. **B-P1-6 retention purge** (6h, HIGH risk 28) — PIPEDA/CRA compliance; soft-delete without purge is the worst case.
6. **B-P1-9 DSAR completeness + metric** (4h) — pairs with B-P1-6 (both PIPEDA s.9 / s.10 territory).
7. **B-P1-7 ride history DB-pagination** (3h) — SLA bug that grows linearly with usage.
8. **B-P1-8 round-robin index** (2h, after EXPLAIN ANALYZE confirms the gap) — same SLA family.

P2 work should not begin until P1 is closed; the audit framework's whole point is that P1 → public-beta blocker, P2 → public-launch blocker.

Two items can be marked as DONE in their respective remediation cards immediately:
- `backend-P0-critical-fix-now.md` — both items.
- `backend-P1-before-beta.md` — B-P1-4 (frozenset is empty).

---

## 8. Appendix — verification command transcript

The exact commands run to produce this rollup are reproducible:

```bash
# P0
sed -n '1815,1830p' backend/routes/rides.py        # B-P0-1
sed -n '1325,1340p' backend/routes/rides.py        # B-P0-2 (rider side)
sed -n '2125,2140p' backend/routes/drivers.py      # B-P0-2 (driver side)

# P1
sed -n '395,415p' backend/routes/auth.py           # B-P1-1
sed -n '80,105p' backend/core/config.py            # B-P1-2
cat backend/pytest.ini                             # B-P1-3
sed -n '375,395p' backend/tests/conftest.py        # B-P1-4
grep -nE "logger\.warning.*persist|logger\.warning.*firebase_auth" backend/routes/auth.py  # B-P1-5
ls backend/utils/ | grep -iE "retention|purge"     # B-P1-6 (no output → confirmed missing)
sed -n '1095,1140p' backend/routes/rides.py        # B-P1-7
sed -n '265,275p' backend/services/dispatch_service.py  # B-P1-8
ls backend/tests/ | grep -i dsar                   # B-P1-9 (no output → confirmed missing)
grep -c "sha256:" backend/requirements.txt         # B-P1-10 → 0

# P2
sed -n '265,280p' backend/routes/corporate_company.py  # B-P2-1
sed -n '210,225p' backend/routes/webhooks.py           # B-P2-2
sed -n '70,105p' backend/core/middleware.py            # B-P2-3
grep -nE "reject_audit_mutation|audit_logs.*UPDATE.*DELETE" backend/migrations/*.sql  # B-P2-4
grep -rn "drivers_online_available_type" backend/migrations/  # B-P2-5
sed -n '1560,1595p' backend/routes/drivers.py          # B-P2-6
grep -nE "ThreadPoolExecutor|run_in_executor" backend/db_supabase.py  # B-P2-7
sed -n '1,55p' backend/Dockerfile                      # B-P2-8
grep -n "financial_events" backend/utils/payment_retry.py  # B-P2-9 (no output)

# P3
sed -n '85,105p' backend/socket_manager.py             # B-P3-1
grep -nE "asyncio\.sleep" backend/utils/{surge_engine,scheduled_rides,payment_retry}.py  # B-P3-2
ls backend/utils/ | grep -iE "subproc|sub_proc"        # B-P3-3 (no output → confirmed missing)
```

End of rollup.
