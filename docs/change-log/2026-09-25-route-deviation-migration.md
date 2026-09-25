# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (agent) |
| Surface(s) | backend |
| Domain (Sentry tag) | safety |
| PR / commit link | (branch `claude/fix-missing-route-deviation-migration`) |
| Related issue or gap ID | LIVE-004 (`docs/audit/clean-sheet/10-live-checks.md` §6.1) |

## 1. Issue / gap identified

Production's `settings` table already has `route_deviation_alert_enabled boolean NOT NULL DEFAULT false` (verified live), and production's `schema_migrations` tracks a file named `415_route_deviation_alert_enabled_setting.sql` — but no file under `backend/migrations/` actually creates that column; this repo's `415` slot is a different, unrelated migration (`415_directions_proxy_enabled_flag.sql`).

## 2. Root cause

The migration that added this column to production was apparently never committed to (or was later lost from) `backend/migrations/`, even though its tracking row landed in `schema_migrations`. Any database rebuilt from this repo's migration set alone (fresh env, disaster recovery, RLS/direct-pool test fixtures) would be missing the column entirely.

## 3. Fix / remediation

Added `backend/migrations/476_route_deviation_alert_enabled_setting.sql`, which does `ALTER TABLE public.settings ADD COLUMN IF NOT EXISTS route_deviation_alert_enabled boolean NOT NULL DEFAULT false` plus a `COMMENT ON COLUMN`. Numbered 476 rather than reusing 415, since that number is already taken in this repo and CI's CHECK B hard-fails a colliding/earlier prefix. (It was first 472, then moved to 476 because the owner's PR #5782 takes 472 and open PRs #5791, #5794 and the admin money-caps branch hold 473-475.)

## 4. Risk & impact on existing functionality

- **Reader**: `backend/utils/route_deviation_alerter.py`'s `_deviation_alert_enabled()` (~line 133) is the only code that reads this column, via `get_app_settings().get("route_deviation_alert_enabled")` with a `None`-safe fallback to `False` (fail-closed). No other route or loop reads or writes this column (grepped `route_deviation_alert_enabled` across the repo — only the alerter module, its own test, docs/context files, and the admin-dashboard settings UI reference it).
- **Writer**: the admin dashboard's settings page (`admin-dashboard/src/app/dashboard/settings/page.tsx`) can toggle it via the existing settings-update endpoint; that endpoint and its authorization are unchanged by this migration.
- **Blast radius: isolated.** Against production, `ADD COLUMN IF NOT EXISTS` is a pure no-op — the column already exists there with the same type/default/not-null shape, so this migration changes nothing about live behavior or data. Against any environment rebuilt from scratch (fresh dev DB, CI fixtures that build schema from migrations), it creates the column for the first time, which is strictly additive and defaults to `false` (alert stays off), matching current production behavior for any service area that hasn't explicitly enabled it.
- No interaction with the ride state machine, money/wallet deltas, or other background loops. Not a retention-sensitive table.

## 5. User-experience effect

None visible to riders or drivers. Internal-admin-facing only in the sense that the admin settings toggle for this flag will now actually persist correctly on any environment rebuilt from migrations (previously it would have failed with "column does not exist" on such an environment). Not visible mid-session to anyone; production itself is unaffected since the column is already live there.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/476_route_deviation_alert_enabled_setting.sql` | New migration: `ALTER TABLE public.settings ADD COLUMN IF NOT EXISTS route_deviation_alert_enabled boolean NOT NULL DEFAULT false` + column comment | Restores the missing source-of-truth migration for a column that already exists in production (LIVE-004) |
| `docs/change-log/2026-09-25-route-deviation-migration.md` | This log | Required for any change touching a live-tested (safety) surface |

## 7. Before / after

Purely additive (no existing behavior changes) — before/after snippet not applicable per template guidance.

## 8. Rollback plan

`ALTER TABLE public.settings DROP COLUMN IF EXISTS route_deviation_alert_enabled;` — safe because the reader path (`_deviation_alert_enabled()`) already fails closed (returns `False`/disabled) whenever the column is missing or unreadable, so dropping it just returns the alert to its pre-existing fail-closed state. No app redeploy required; this is a plain SQL statement, not a code path change.

## 9. Verification performed

- [x] Blast-radius grep performed: `route_deviation_alert_enabled` across the whole repo (only `route_deviation_alerter.py`, `test_route_deviation_alerter.py`, admin-dashboard settings page, and docs/context files reference it — no other reader/writer).
- [x] Reviewed against `backend/migrations/CLAUDE.md` conventions (naming/numbering via `ls backend/migrations | sort -V | tail`, append-only, forward-compatible `ADD COLUMN IF NOT EXISTS ... DEFAULT`, reversibility comment).
- [x] Self-reviewed against `.claude/agents/spinr-migration-reviewer.md` checklist (numbering OK, append-only OK, RLS N/A — no new table, forward-compat OK — constant default, no batching needed, reversibility OK, indexes N/A, money safety N/A, retention N/A).
- [ ] Not run against a real Postgres instance (no `TEST_DATABASE_URL`/`DATABASE_URL` available in this environment) — the migration was reviewed for syntax correctness and convention match only, not executed. `ruff` is not applicable (SQL, not Python).
- [ ] `backend/tests/direct_pool/conftest.py`'s `_MIGRATION_FILES` list was checked and intentionally **not** updated: that list is a narrow, hand-curated set of migrations needed specifically for `dispatch_claim_batch` RPC tests (its own docstring says so), not a "run every migration" list — this column is unrelated to that RPC's test surface.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (single `DROP COLUMN IF EXISTS` statement, fail-closed reader makes it safe).
- [x] Blast radius is stated: isolated to `route_deviation_alerter.py`'s kill-switch read; no-op against live production.
- [x] No silent behavior change to an already-shipped flow — production behavior is unchanged (column already exists there); only non-production environments gain the column for the first time, defaulting to the existing off/disabled behavior.
