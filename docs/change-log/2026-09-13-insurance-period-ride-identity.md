# Change Impact & Risk — insurance-period idempotency must include ride identity (F4)

**Date:** 2026-09-13
**Surfaces:** backend (safety/regulatory — `driver_insurance_periods`)
**Related:** F4 in `docs/audit/2026-09-13-driver-app-mid-ride-process-death.md`

## Issue/gap identified

On 2026-09-13, ride `294ed8e4…` (SPR-BCPJPV) completed with **no Period 2 interval of its own**. The
driver's open Period 2 row stayed attached to ride `ac38399b…` — a ride the rider had **cancelled** 18
seconds earlier. TNC Period 2 is "en route to pickup, TNC primary commercial coverage"; attributing it to
a cancelled ride is a regulatory-audit defect.

## Root cause

Migration 253's no-op check compared **`period` only** (`253:44`):

```sql
IF v_current_period IS NOT NULL AND v_current_period = p_new_period THEN
    RETURN jsonb_build_object('status', 'noop', ...);
```

The driver was already open on Period 2 for ride A, so the Period 2 claim for ride B
(`routes/rides/matching.py:1268`) returned `noop` and never inserted ride B's interval.

Note `routes/drivers/ride_flow.py:396-398` documents the *intended* behaviour — "no-ops if Period 2
**with this ride_id** is already open … re-confirms it's open with the right ride_id" — which the SQL never
implemented. The comment described a contract the function did not honour.

## Fix/remediation

Migration 421 adds a NULL-safe ride-identity comparison to the no-op check.
It was originally numbered 419; PR review found that main already contained 419
and 420. The unmerged, unapplied migration was renumbered to the next free slot.

## Before/after

```diff
- IF v_current_period IS NOT NULL AND v_current_period = p_new_period THEN
+ IF v_current_period IS NOT NULL AND v_current_period = p_new_period
+    AND v_current_ride_id IS NOT DISTINCT FROM p_ride_id THEN
      RETURN jsonb_build_object('status', 'noop', 'closed', 0, 'opened', false);
```

`IS NOT DISTINCT FROM`, not `=`: plain `=` yields NULL for a NULL `ride_id`, which is falsy, so every
repeated Period 0/1 tick would fall through to close+open and churn one row per call — and
`core/lifespan.py`'s background loops drive exactly that path.

## Risk & impact on existing functionality

Blast radius — all 30 Python call sites of `record_period_transition` plus the two SQL ones (migrations
402/403) were enumerated during review:

- `core/lifespan.py`, `routes/admin/rides.py`, `routes/rides/matching.py`,
  `routes/drivers/{profile,ride_cancel,ride_complete,ride_flow,status,subscriptions}.py`
- **No production path passes period 2 or 3 without a `ride_id`**, so no call site newly churns rows.
- Repeated P0/P1/P2/P3 calls with unchanged ride identity still return `noop` — verified by test.
- Signature is byte-identical → **no caller changes required**.
- Partial unique index and the `race` branch are untouched.
- The REVOKE/GRANT tail re-asserts service-role-only execute, so the final ACL matches 354's intent.

The one real behavioural change: a same-period, different-ride transition now **appends** a row where it
previously no-op'd. That is the fix, and it increases row count on `driver_insurance_periods` slightly —
correct for an append-only regulatory table.

**Append-only is preserved.** Closing ride A's interval sets `ended_at` only; migration 64's
`_driver_insurance_periods_immutable()` trigger enforces this at the DB level and the close passes it
cleanly. `period`, `ride_id`, and `started_at` on the historical row are untouched — asserted by test.

## User experience effect

**None.** No rider-, driver-, corporate-, or admin-facing behaviour changes. This is an audit-trail
correctness fix; the ride state machine, dispatch, and fares are untouched.

## Files modified

| File | What changed | Why |
|---|---|---|
| `backend/migrations/421_insurance_period_ride_identity.sql` | New migration; ride-identity-aware no-op + `migration-override-ok` annotation | Fix F4; annotation is required because it redefines 253's function and `ci-guardrails.yml`'s CREATE-OR-REPLACE gate hard-fails otherwise |
| `backend/tests/direct_pool/conftest.py` | Added 421 to `_MIGRATION_FILES` | Without it every CI run installs 253's buggy body and asserts against the bug |
| `backend/tests/direct_pool/test_insurance_period_ride_identity.py` | New, 7 tests | CI-collected regression coverage |
| `backend/tests/sql/insurance_period_ride_identity.sql` | Manual psql repro (from the original investigation) | **Superseded** by the pytest file above — reviewer may drop it; kept only as a hand-run repro |

## Rollback plan

Restore 253's function body via `CREATE OR REPLACE` (421's header references that migration;
it does not include the full rollback SQL), retaining 421's service-role-only grants —
this is code-only, so no data reconciliation is needed. **No historical rows are rewritten by this
migration**, so a rollback leaves the audit trail intact and simply resumes the old no-op behaviour. A
`git revert` alone is NOT sufficient once applied: the function must be explicitly replaced in the
database.

## Verification performed

- **Red/green proof against a real Postgres 15.19 container**, CI's exact invocation
  (`pytest tests/direct_pool -c /dev/null --confcutdir=tests/direct_pool`):
  - With 419: **36 passed, 1 skipped**.
  - With 419 removed from the fixture (253's body installed): **3 failed, 4 passed** — the 3 failures are
    exactly the F4 behaviours, and the 4 that still pass are 253's pre-existing guarantees 419 must not
    break. The test is a genuine regression test, not a tautology.
- CI gate simulated locally with the workflow's own regex → `migration-override-ok` matches → **PASS**.
- Independent PR review: PostgreSQL 17 direct-pool suite with migration 421:
  **36 passed, 1 skipped**. On Windows, set `PYTHONUTF8=1`,
  `PGCLIENTENCODING=UTF8`, and `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`; use a temporary
  pytest INI and a disposable local `TEST_DATABASE_URL`. The skip is the optional
  psycopg3 test. The initial run exposed a timezone-sensitive timestamp string
  assertion; comparing aware datetimes now verifies the same instant in Regina
  and UTC sessions. No SQL behavior changed during renumbering.
- Independent migration review reconfirmed preserved grants, retention trigger,
  function signature, null-safe retries, and unchanged unique-index race handling.

## What was NOT verified

- **Migration 421 has NOT been applied to production.** No production DB write of any kind was made; all
  production access during this investigation was read-only.
- **No historical correction** was made for the affected ride (`294ed8e4…`). Its Period 2 interval is still
  attributed to the cancelled ride. That requires a separately reviewed, append-only correction record —
  `driver_insurance_period_corrections` exists for this — and is deliberately out of scope here.
- Original implementation tested on **Postgres 15.19**; independent review tested
  a disposable **Postgres 17** cluster. Neither run exercises production traffic.
- The `race` branch (concurrent callers losing the unique-index race) is **not** exercised by these tests.
- Migration 421 does not install on a vanilla Postgres without the `anon`/`authenticated` roles present;
  the `direct_pool` fixture supplies them, but a hand-run against a bare cluster will fail at the REVOKE.
- The existing unique index prevents multiple open intervals, but does not order
  delayed competing calls. The reconciler's same-period/wrong-ride repair now works
  as intended; stale snapshots remain an inherited ordering limitation.
