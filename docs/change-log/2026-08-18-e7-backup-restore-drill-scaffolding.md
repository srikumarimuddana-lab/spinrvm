# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | Claude (session), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | admin (ops/DR tooling; not a request-path change) |
| PR / commit link | (filled in on PR open) |
| Related issue or gap ID | ACTION_ITEMS.md E7 — Backup-restore drill |

## 1. Issue / gap identified

`docs/runbooks/pitr-restore.md`'s "Verify branch data" step for a Supabase
PITR restore has always been a vague, manual, ad-hoc instruction ("Run
validation query"), and the runbook's RTO figures have never been measured
against a real restore because the drill has never been run.

## 2. Root cause

The runbook was written as a checklist of intentions before anyone had a
concrete tool to execute the "verify row counts + a sample ride lifecycle"
step, and no one has since had the Supabase org/billing access + time to run
the drill for real and close the loop.

## 3. Fix / remediation

Added `backend/scripts/verify_restore.py`: a standalone, read-only, opt-in
script a human runs by hand against a restored Supabase branch's connection
string. It reports row counts for the core tables this repo's runbook/tests
care about (`users`, `drivers`, `rides`, `payouts`, `stripe_disputes`,
`driver_insurance_periods`, `financial_events` — verified against
`backend/migrations/`, not guessed), walks one sample `status='completed'`
ride's full lifecycle (ride row → `driver_insurance_periods` rows →
`financial_events` rows) with a pass/fail summary, prints elapsed wall-clock
time (feeds the RTO measurement), and exits non-zero on any check failure.
Updated `docs/runbooks/pitr-restore.md`'s "Verify branch data" step (Option A)
and the "Quarterly DR Drill" section to name this script as the concrete tool,
replacing the vague bullet. Updated ACTION_ITEMS.md E7 to record what was
done and what remains (the drill itself still requires a human with Supabase
org/billing access; this session did not run it).

This session did **not** trigger any real Supabase restore, create any real
scratch Supabase project, or connect to any real database — the script is
scaffolding only, exercised solely against a mocked connection in its test
suite.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** `backend/scripts/verify_restore.py` is a new
  file, never imported by any route, background loop (`backend/core/lifespan.py`),
  CI workflow, or other script. Grepped the repo for any existing reference
  to a `verify_restore` name or similar restore-verification tooling — none
  found; nothing else calls into this module. It is a standalone
  `python -m backend.scripts.verify_restore` entry point, run by hand only.
- It reads (never writes) `users`, `drivers`, `rides`, `payouts`,
  `stripe_disputes`, `driver_insurance_periods`, and `financial_events`.
  Every query is a `SELECT`; the session is additionally set
  `TRANSACTION READ ONLY` at the Postgres level as defense-in-depth, so even
  a bug in this script cannot mutate data. It never runs against production
  in this session — the guard in `resolve_database_url()` refuses to run if
  the resolved target URL equals the shell's `DATABASE_URL`.
  `docs/runbooks/pitr-restore.md` and `ACTION_ITEMS.md` are documentation-only
  edits (no migration, no append-only-rule concern).
- No interaction with the ride state machine, wallet/allowance deltas, or
  Stripe flows — the script never writes anything, so it cannot regress a
  live-tested flow.

## 5. User-experience effect

None. This is an internal ops/DR tool run by hand from a terminal by
engineering, never exposed through any app surface (rider/driver/corporate
admin/internal admin dashboard). No mid-session visibility to anyone using
the live app.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/scripts/verify_restore.py` | New file: read-only restore-verification script | ACTION_ITEMS.md E7 — make the runbook's "verify row counts + a sample ride lifecycle" step concrete |
| `backend/tests/test_verify_restore_script.py` | New file: 21 unit tests for the script's guard + check logic | Real application code needs real test coverage per CLAUDE.md Testing Conventions |
| `docs/runbooks/pitr-restore.md` | "Verify branch data" step and "Quarterly DR Drill" section now name `verify_restore.py` as the concrete tool, replacing "Run validation query" | Close the gap the E7 item calls out |
| `ACTION_ITEMS.md` | E7 entry updated (kept open) with what was done and what still requires a human with Supabase access | House style — record scaffolding progress without falsely closing the item |
| `docs/change-log/2026-08-18-e7-backup-restore-drill-scaffolding.md` | New file: this Change Impact Log | CLAUDE.md mandatory for any commit touching a live-tested-adjacent surface |

## 7. Before / after

Pure additive change — no existing behavior modified. `docs/runbooks/pitr-restore.md`'s
"Verify branch data" step changes from a vague instruction to a concrete
command; shown for reference:

```
# Before
- [ ] Run validation query: `SELECT count(*) FROM <affected_table> WHERE <condition>`
- [ ] Sample rows to confirm expected state

# After
- [ ] Run `python -m backend.scripts.verify_restore --database-url "<branch connection string>"`
  (or export it as `RESTORE_BRANCH_DATABASE_URL` first) — it reports row counts
  for the core tables ..., walks one sample completed ride's full lifecycle ...,
  and prints elapsed wall-clock time to feed the RTO measurement below.
- [ ] Sample rows to confirm expected state beyond what the script checks ...
```

## 8. Rollback plan

Revert the commit (`git revert`) — this is pure additive scaffolding (new
files + doc/backlog edits), never invoked by any running system, so no
data-level rollback is applicable. No feature flag needed: the script is not
reachable from any live code path, so there is nothing to "turn off" beyond
reverting the commit.

## 9. Verification performed

- [x] Automated tests run: `cd backend && pytest tests/test_verify_restore_script.py -q --no-cov` → 21 passed. (Full-suite `--cov-fail-under=60` gate is a pre-existing, unrelated whole-repo threshold that a single new test file cannot satisfy alone — not specific to this change; not chased per the "don't chase unrelated CI" guidance.)
- [x] `ruff check backend/scripts/verify_restore.py backend/tests/test_verify_restore_script.py` → clean (one `S608` false positive on a table name drawn only from the `CORE_TABLES` constant, silenced with `# noqa: S608` and documented inline).
- [x] `ruff format --check` on both new files → clean.
- [x] Blast-radius grep performed: searched the repo for any existing caller of `verify_restore` / restore-verification tooling — none found; confirmed the script is not imported by `backend/server.py`, `backend/core/lifespan.py`, or any CI workflow.
- [x] Reviewed against CLAUDE.md conventions: dual-import pattern (try `backend.utils.money` / except `utils.money` / except local fallback), money arithmetic (Decimal-only via `to_decimal`, no float, no summation of money columns), "don't silently swallow errors" (row-count failures are recorded as failed checks with the underlying exception message, not swallowed — though note: query failures inside `check_row_counts` are caught and turned into a failed check rather than re-raised, which is deliberate here so one bad table doesn't abort the rest of the drill's checks; this is a human-run diagnostic tool, not a request-path handler, so CLAUDE.md's "surface loudly, don't fall back" rule is satisfied by the non-zero exit code + printed failure detail rather than an uncaught exception).
- [x] Money-safety self-review (spinr-money-auditor's `Agent`-tool invocation was not available in this sub-agent session's toolset — performed the equivalent review directly instead of skipping it): confirmed no float arithmetic anywhere in the script, confirmed `financial_events.delta_cents` is only converted via `to_decimal()` for type-safety exercise and never summed/aggregated, confirmed every SQL statement is a `SELECT` (plus the session-level `SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY`), and confirmed the production-URL guard (string-equality against `DATABASE_URL`, normalized only by stripping whitespace/trailing slash) fails closed — no URL, or an ambiguous case, both raise rather than silently proceeding.

## 10. What was NOT verified

- **Not run against a real Supabase branch, or any real database.** All 21 tests exercise the script's logic against a mocked `psycopg`-style connection. `psycopg`/`psycopg2` availability, real Postgres error shapes, and real query behavior against actual restored data have not been exercised — that only happens when a human runs the actual drill (creates a scratch Supabase project, restores a PITR branch into it, and points the script at it), which this session explicitly did not do and was instructed not to do.
- **No real RTO measurement exists yet.** The script prints elapsed time, but no real number has been recorded anywhere — `docs/runbooks/pitr-restore.md`'s RTO target (4h) and Quarterly DR Drill's ≤2h wall-clock target remain unvalidated against reality.
- **No CI job runs this script** (by design — it's a human-run tool against a resource CI does not have), so there is no automated regression signal if a future migration renames one of the `CORE_TABLES` or changes `rides`/`driver_insurance_periods`/`financial_events` column names referenced in the SQL — a schema drift would only surface the next time a human actually runs the drill.
- **`psycopg` vs `psycopg2` compatibility of the exact cursor/connection calls used** (`SET SESSION CHARACTERISTICS`, parameterized `%s` queries, context-manager cursor usage) was reasoned about from `backend/scripts/run_migrations.py`'s existing pattern, not independently executed against both driver versions.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`; additive-only, not reachable from any live code path)
- [x] Blast radius is stated, not assumed: isolated, new standalone script, no other callers
- [x] No silent behavior change to an already-shipped flow — this is new scaffolding, not a modification to any existing script or endpoint
