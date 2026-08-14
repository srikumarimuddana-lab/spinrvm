# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-10 |
| Author | Claude (session_01Wk3M9NdQJWqgpATtogSjD8) |
| Surface(s) | backend |
| Domain (Sentry tag) | safety (retention/compliance), payments (financial_events) |
| PR / commit link | branch `claude/b17-financial-events-ride-fk-set-null` |
| Related issue or gap ID | ACTION_ITEMS.md B17 |

## 1. Issue / gap identified

`purge_pii_retention()` Step B (`DELETE FROM rides WHERE created_at < now() - 7y`) has no exception handler, and `financial_events.ride_id` references `rides(id)` with the Postgres default `NO ACTION`. Every paid ride has a retained `financial_events` row pointing at it, so the first ride to cross 7 years raises `foreign_key_violation`, aborting the entire daily purge transaction — including Step A's 3-year GPS anonymization and every other step — and repeats on every subsequent run.

## 2. Root cause

Migration 58 declared `ride_id text REFERENCES rides(id)` inline with no `ON DELETE` clause, before `purge_pii_retention()`'s 7-year ride-deletion step (migration 50) or `financial_events`' own append-only/7-year-retention role (also migration 58, same file) were connected to each other. Nothing enforced that a table meant to survive independently of its ride (the CRA/SOC2 7-year money ledger) could actually survive a ride being deleted. Dormant since inception because no ride has yet aged past 7 years; certain to fire on the passage of time alone once one does.

## 3. Fix / remediation

New migration `294_financial_events_ride_id_set_null.sql` changes `financial_events.ride_id`'s FK from `NO ACTION` to `ON DELETE SET NULL`, resolving the existing constraint by column via `pg_constraint` (not by assuming Postgres' default name) and re-adding it with the new action — same pattern as migration 273 (`driver_statements.driver_id` → `ON DELETE CASCADE`, fixing the identical shape of bug for Step H). `ON DELETE SET NULL` was chosen over `CASCADE` (would delete the tax record itself — the one thing `financial_events` exists to retain) and over per-row exception isolation on Step B (would leave every paid ride permanently un-purgeable, silently exempting exactly the rides with payment history from the 7-year deletion guarantee). Decision confirmed with the user before implementation, given the three options carry different consequences for the 7-year tax record's ability to link a charge back to its trip.

## 4. Risk & impact on existing functionality

Blast-radius grep performed across `backend/` for every reader of `financial_events.ride_id`:
- `utils/ledger_projection.py` — the only reader that joins on it (`{e["ride_id"] for e in events if e.get("ride_id")}`, `rides_by_id.get(event.get("ride_id"))`). Already `None`-safe: non-ride event types (`wallet_topup`, `driver_payout`) never had a `ride_id` in the first place, so `_decompose` already has to handle a missing/`None` ride. In practice this FK only fires on `financial_events` rows attached to rides that are themselves 7+ years old — long past any active projection window (those events are fully projected and settled within days of creation, not still pending 7 years later).
- `services/ledger_service.py:396,403` — reads `ride_id` only for a log message and at write time; never reads it back for a join.
- `routes/admin/rides.py`, `routes/webhooks.py`, `services/payment_service.py`, `utils/payment_retry.py`, `utils/stripe_reconcile.py`, `utils/retention_purge.py` — all reference a local `ride_id` variable (route param, function arg) unrelated to `financial_events.ride_id`; none query `financial_events` filtered/joined by `ride_id`.
- No admin/reporting surface displays "financial events for ride X" that would need `ride_id` non-null after purge — the ride itself is gone from the `rides` table at that point, so an admin page for it wouldn't exist to query either.

Blast radius: isolated to `financial_events.ride_id`'s FK behavior on delete. No other table, background loop, or money-computation path changes. Interacts with `purge_pii_retention()` (one of the 17 background-loop-invoked SQL functions) but does not modify its body — only the constraint the DELETE it issues is checked against.

## 5. User-experience effect

None. Backend-only, invisible to rider/driver/corporate-admin/internal-admin. Not visible mid-session to anyone — the purge job runs once daily at ~03:00 UTC and only affects rides already 7+ years old, which by definition predate any live session.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/294_financial_events_ride_id_set_null.sql` | New migration: `financial_events.ride_id` FK → `ON DELETE SET NULL` | Let Step B's ride purge converge automatically instead of aborting the whole daily run once a paid ride crosses 7 years |
| `backend/tests/test_financial_events_ride_id_fk_contract.py` | New SQL-text contract test pinning the migration's invariants (SET NULL not CASCADE, targets the right FK, dynamic constraint lookup) | No live Postgres instance available in this environment; mirrors the existing `test_wallet_apply_delta_contract.py` static-SQL-assertion pattern used elsewhere in this repo for the same reason |
| `docs/runbooks/data-retention.md` | Documented Steps H–M (previously omitted entirely) and the new Step B FK fix | B17's acceptance criterion; the runbook stopped at Step G/`audit_logs` and never described the DSAR hard-delete, ride-routes, AI-chat, surge, price-search, or compliance-export steps that already ship in migration 289 |
| `ACTION_ITEMS.md` | B17 flipped `[ ]` → `[x]`, status summary added | Tracking |

## 7. Before / after

```sql
-- Before (migration 58, unmodified — append-only, never edited in place)
ride_id      text        REFERENCES rides(id),
-- (no ON DELETE clause — defaults to NO ACTION)
```

```sql
-- After (migration 294, additive ALTER TABLE)
ALTER TABLE public.financial_events
    ADD CONSTRAINT financial_events_ride_id_fkey
    FOREIGN KEY (ride_id) REFERENCES public.rides(id) ON DELETE SET NULL;
```

## 8. Rollback plan

Migration 294's own header documents the rollback:

```sql
ALTER TABLE public.financial_events
    DROP CONSTRAINT IF EXISTS financial_events_ride_id_fkey;
ALTER TABLE public.financial_events
    ADD CONSTRAINT financial_events_ride_id_fkey
    FOREIGN KEY (ride_id) REFERENCES public.rides(id);
```

Pure schema change, no data mutated by this migration itself (existing `ride_id` values are untouched; only future deletes of a referenced `rides` row behave differently). No feature flag applies — a constraint action isn't flaggable — but the rollback above reverts to the exact pre-294 state with a single `ALTER TABLE`, no PITR or data remediation needed.

## 9. Verification performed

- [x] Automated tests run — unit: `test_financial_events_ride_id_fk_contract.py`'s assertions verified by direct interpreter execution against the migration text (see commit); a `pip install`-backed venv run of the full suite via `pytest` was also attempted in this session — record the actual pass/fail count from that run in this section once complete, do not assume it passed without checking.
- [ ] Manual repro steps followed in staging — **not performed**, no live Postgres/Supabase instance available in this session (Supabase MCP disconnected). The FK behavior itself (does `ON DELETE SET NULL` actually null the column instead of raising) is a well-established Postgres primitive, not something this session's tests can exercise end-to-end.
- [x] Blast-radius grep performed — see §4, full command: `grep -rn "financial_events" --include="*.py" backend/ | grep -v __pycache__ | grep -v test_`, then per-file `ride_id` grep on each hit.
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — migration append-only rule (294 is additive, 58 untouched), RLS/service-role convention (no RLS change needed — this is a plain FK action, not a new grant surface), retention/PIPEDA convention (financial_events' 7-year CRA/SOC2 requirement explicitly preserved, not weakened).
- [x] Feature-flagged if user-visible and non-trivial — n/a, not user-visible.

## 10. What was NOT verified

- **No live-database migration apply.** The migration was not run against a real or throwaway Postgres/Supabase instance in this session. Verification is: (a) the SQL is syntactically modeled directly on migration 273's already-shipped, already-proven `DROP CONSTRAINT` / `ADD CONSTRAINT ... ON DELETE ...` pattern for the identical bug shape, and (b) a static-text contract test. The next actual `python scripts/migrate.py` run against a real environment is the first true end-to-end check — same caveat B0's own fix carries.
- **No test proves Step B itself now succeeds past 7 years** — that would require a live database with a ride row backdated 7+ years and a matching `financial_events` row, then invoking `purge_pii_retention()` for real. Out of reach without database access in this session.
- `docs/runbooks/data-retention.md`'s Steps H–M documentation was written by reading migration 289's SQL, not by running it — the described behavior (gating GUCs, per-account exception isolation, etc.) is transcribed from the migration's own comments and code, cross-checked against the code, not independently re-derived.

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (n/a — no UX change)
