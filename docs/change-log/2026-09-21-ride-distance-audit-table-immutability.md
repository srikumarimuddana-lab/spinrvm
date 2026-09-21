# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | ittalenthire.ca@gmail.com (via Claude Code) |
| Surface(s) | backend |
| Domain (Sentry tag) | safety (regulatory/dispute audit trail) |
| PR / commit link | (opened alongside this entry) |
| Related issue or gap ID | `ACTION_ITEMS.md` C118 (ride-distance-integrity immutability variant) |

## 1. Issue / gap identified

`ride_distance_integrity_events` (migration 246) and `ride_distance_recomputes`
(migration 242) both claim "event/audit rows are immutable" in their own
migration comments, but neither table has a DB-level trigger enforcing it —
`service_role` (the only role able to write to either table at all, since
RLS explicitly denies `anon`/`authenticated`) can UPDATE or DELETE a row
directly, contradicting the stated guarantee.

## 2. Root cause

When these two tables were created, the anti-tamper trigger pattern already
used for `audit_logs` (migrations 51/56) and `compliance_export_events`
(263/285) was not applied to them. The gap was proven, not assumed, by a
dedicated RLS test (`test_*_service_role_can_mutate_despite_immutable_comment`)
that reproduced the mutation succeeding — this session's own review of
`ACTION_ITEMS.md`'s P4 backlog surfaced it during a RICE-scoring pass.

## 3. Fix / remediation

New migration `435_ride_distance_integrity_immutability.sql` adds one
shared `BEFORE UPDATE`/`BEFORE DELETE` trigger function
(`block_mutation_on_immutable_table()`, parameterized via `TG_TABLE_NAME`/
`TG_OP` rather than one dedicated function per table) and wires it to both
tables — 4 triggers total. Unlike `audit_logs`, neither table has any
legitimate DELETE path (confirmed by grep: no route, service, or background
loop — including `retention_purge.py` — ever issues UPDATE/DELETE against
either table), so the block is unconditional with no session-flag carve-out.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped all of `backend/` for any UPDATE/DELETE
  against `ride_distance_integrity_events` or `ride_distance_recomputes` —
  zero production call sites found. The only matches anywhere in the repo
  are the RLS test's own cleanup/proof statements (updated by this change).
- **What else reads/writes these tables**: `utils/distance_integrity.py`
  (INSERT only, integrity-event writer) and `utils/route_finalizer.py`
  (INSERT only, recompute writer) — both confirmed INSERT-only by direct
  grep, unaffected by a trigger that only fires on UPDATE/DELETE.
- **No interaction** with the ride state machine, background-loop registry,
  or money/wallet deltas — these are detection/audit-only streams that
  "never affect a fare or displayed distance" per their own table comments.
- Real Postgres verification (see §9) proves no regression to `RLS`'s
  existing `anon`/`authenticated` deny-all behavior on the same two tables,
  nor to the unrelated third table (`ride_location_gap_events`) covered by
  the same test file, which retains its legitimate, unaffected DELETE path.

## 5. User-experience effect

None. Backend-only, no rider/driver/corporate-admin/internal-admin-facing
surface reads or writes these tables directly (confirmed: RLS denies direct
client access entirely; no admin route exists for either table).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/435_ride_distance_integrity_immutability.sql` | New migration: shared trigger function + 4 triggers (UPDATE/DELETE × 2 tables) | Close the immutability gap |
| `backend/tests/rls/conftest.py` | Added one line replaying the new migration in the RLS test harness's manually-curated migration list | So the trigger exists before the affected tests run |
| `backend/tests/rls/test_ride_distance_integrity_rls.py` | Renamed and flipped the two tests that previously proved the gap (`*_can_mutate_despite_immutable_comment` → `*_cannot_mutate_immutable_table`) to assert the fix instead; updated module docstring | The prior tests existed specifically to document this gap — they now document the fix |

## 7. Before / after

```
# Before (migration 246's policy comment claimed this, but nothing enforced it)
as_role(pg_cur, "service_role", None)
pg_cur.execute("DELETE FROM ride_distance_integrity_events WHERE id = %s", (event_id,))
assert pg_cur.rowcount == 1   # succeeded — the gap
```

```
# After
as_role(pg_cur, "service_role", None)
with pytest.raises(psycopg2.errors.CheckViolation):
    pg_cur.execute("DELETE FROM ride_distance_integrity_events WHERE id = %s", (event_id,))
# raises — enforced at the DB layer for every role, not just RLS-covered ones
```

## 8. Rollback plan

Migration rollback SQL (reversible on paper, stated in the migration's own
header comment): `DROP TRIGGER` on each of the 4 new triggers, then
`DROP FUNCTION public.block_mutation_on_immutable_table()`. No data is
touched by the migration itself (no columns, no backfill), so rollback is a
pure schema revert with zero data-loss risk.

## 9. Verification performed

- [x] Automated tests run: full `backend/tests/rls` suite (43 tests in the
  directly-affected file, 393 across the entire RLS suite) against a real
  local Postgres 16 instance (`pytest tests/rls -c /dev/null
  --confcutdir=tests/rls`) — **all 393 passed**, confirming both the fix
  works as intended and nothing else regressed.
- [x] `ruff check` run on both modified Python files — clean.
- [x] Blast-radius grep performed: searched all of `backend/` for
  `UPDATE`/`DELETE` statements against either table name; confirmed the
  only production writers (`distance_integrity.py`, `route_finalizer.py`)
  are INSERT-only, and `retention_purge.py` never references either table.
- [x] Reviewed against `backend/migrations/CLAUDE.md` conventions: append-only
  (new file, no edit to 242/246/51/56), naming (435 is next-free per
  `origin/main` at branch time), forward-compatible (catalog-only lock, no
  row rewrite), reversible-on-paper (rollback stated above).
- [ ] Not run: `pytest -m "not slow"` full backend suite (only the RLS tier
  is relevant to this change; not re-run here since no non-RLS code path
  touches these tables).
- [ ] Not verified against staging/production Supabase — only against a
  local scratch Postgres 16 instance. This migration has **not** been
  applied to production; it is committed, reviewed-ready, but unapplied,
  matching this repo's established "prepared, not applied" pattern for new
  migrations pending a separate, explicit apply step.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (4 `DROP TRIGGER` + 1
  `DROP FUNCTION`, no data touched)
- [x] Blast radius is stated, not assumed (isolated; confirmed via grep and
  a full passing test run, not just claimed)
- [x] No silent behavior change to an already-shipped flow — the only
  "behavior change" is that two previously-latent, never-exercised
  UPDATE/DELETE code paths that no production code actually uses would now
  fail loudly instead of silently succeeding, which is the intended fix
