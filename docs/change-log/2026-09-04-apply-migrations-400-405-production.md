# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude Code (session 01Sspqro7zzjKdTbUh6D61wQ), at explicit user direction |
| Surface(s) | backend, database schema |
| Domain (Sentry tag) | dispatch, drivers, corporate n/a |
| PR / commit link | branch `claude/apply-migrations-400-405-production` |
| Related issue or gap ID | C44 (surfaced this pending list), C50 (401-404), C63 (405) |

## 1. Issue / gap identified

`C44`'s 2026-09-04 dry-run pass (see its own entry in `ACTION_ITEMS.md`) surfaced 7 merged-but-unapplied production migrations: `379`, `400`-`405`. At the user's explicit direction, applied 6 of them (`400`, `401`, `402`, `403`, `404`, `405`) directly to production via Supabase MCP `apply_migration` — the normal `run_migrations.py` path needs a `DATABASE_URL` this session doesn't have.

**`379` was explicitly excluded and NOT applied.** Its own file header states: *"PREPARED, NOT APPLIED... Do not remove this comment or apply this migration until a future session confirms the deferral has actually been lifted by the product owner."* This is C43 (RLS on 4 production tables including `settings`), deliberately deferred by the product owner (reconfirmed 2026-08-31) pending the A41 legacy-migration effort concluding. No confirmation that deferral is lifted was given in this session — 379 remains unapplied.

## 2. Root cause

These migrations were merged to `main` across several PRs (some from this session — `403`/`405`; `401`/`402`/`404` from the C50 dispatch-direct-pool effort; `400` a legal-doc seed) but never run against production because no session in this repo's agent integration has had a configured `DATABASE_URL` recently — the same class of gap `C44`/`C22` this session already closed for older migrations.

## 3. Fix / remediation

Before applying, got two separate review passes: a `spinr-migration-reviewer` pass covering `400`/`401`/`402`/`403`/`405` (verdict: SAFE TO APPLY, all 5), and a dedicated `spinr-dispatch-reviewer` pass on `404` specifically, since it's the one file in the batch with a live-traffic-affecting change (verdict: SAFE WITH A CAVEAT — no blockers). Independently re-verified every load-bearing claim from both reviews myself via direct read-only Postgres catalog queries and repo greps before applying anything (see §9) — one of the two review agent runs came back flagged by this session's own security classifier for an unspecified reason, so nothing in either review was trusted at face value.

Applied in numeric order (400 → 401 → 402 → 403 → 404 → 405) via `mcp__Supabase__apply_migration`, then manually inserted matching `schema_migrations` tracking rows (`filename`, `checksum` — `sha256sum` of each file, same algorithm `run_migrations.py`'s `_checksum()` uses) since the out-of-band apply path doesn't do this automatically.

### What each migration actually does, live, right now

- **400** — seeds one `legal_documents` row (driver-only "Deactivation and Appeals Policy"). Content is legal text, published at the product owner's prior explicit direction without counsel review (same accepted-risk pattern as the migration-361 batch). Idempotent insert, no behavior change to any code path — driver-app's `policies.tsx` has referenced this doc type since #4557 shipped and was showing "not yet added" until now.
- **401** — adds `settings.dispatch_direct_pool_enabled BOOLEAN NOT NULL DEFAULT FALSE`. **Also fixes a currently-live bug**: `schemas.AppSettings` already has this field on `main`, and `PUT /api/admin/settings` writes the full row with no column allowlist — so any admin settings save was 500ing (`PGRST204`, missing column) before this migration landed. No dispatch behavior change (flag is `FALSE`, matching the code's only current behavior).
- **402 / 403** — create `dispatch_claim_batch(...)`, the batch driver-claim RPC for the C50 direct-pool dispatch path (402: initial body; 403: `DROP`+`CREATE` with review fixes — `FOR UPDATE SKIP LOCKED`, `SECURITY INVOKER`, argument validation, `ON CONFLICT` handling, new `insurance_written` return column). **Dark**: `matching.py`/`dispatch_pool.py` only call this function when `dispatch_direct_pool_enabled` is `true` (still `FALSE` after 401) **and** `DISPATCH_POOL_DSN` is set (empty by default) — confirmed both gates independently via repo grep, not just trusting the migration comments. Locked to `service_role` EXECUTE only.
- **404** — the one live-behavior change in this batch. Adds `service_areas.max_candidate_pool`/`settings.max_candidate_pool` (both default 500, matching today's hardcoded literal — cannot make any existing dispatch read return fewer candidates than today). **Flips `settings.dispatch_geo_provider` from `legacy` to `postgis` for the single production `settings` row, and changes the column's `DEFAULT`** — this takes effect on the very next dispatch attempt, no flag gate. Verified before applying: the `drivers_nearby_location_geog` RPC (migration 398), the `location_geog` geography column, and its GiST index all exist live; `resolve_provider()`/`fetch_dispatch_candidates()` are genuinely wired into `matching.py`'s live candidate-read call sites (not dead/unwired code); `_postgis_or_fallback` catches every realistic raised failure mode and degrades per-dispatch to the legacy path, logging + metric-emitting + WS/Sentry-announcing the failover; a runtime kill-switch exists (`PUT /admin/settings`) with no redeploy needed, though the in-process settings cache means up to ~60s propagation per replica, not instant.
- **405** — adds nullable `drivers.license_issue_date DATE` + a partial index (this session's own C63 fix, already tested via 19 new/existing tests in PR #4964). No backfill, NULL-safe (`go_online` recheck treats NULL as "unknown, do not block").

## 4. Risk & impact on existing functionality

- **400, 401, 405**: isolated, additive, no other code path reads/writes these columns/rows today beyond what's already documented above.
- **402/403**: net-zero live risk — the function exists now but nothing calls it while both gating conditions are false/unset. Grants confirmed locked to `service_role` only (no `anon`/`authenticated` EXECUTE).
- **404 is the real blast-radius item**: it changes the candidate-read algorithm for **every production dispatch attempt** from this point forward — the P95<2s dispatch-offer SLA hot path. Blast radius: `backend/routes/rides/matching.py`'s primary and vehicle-cascade candidate reads (both call sites confirmed wired to `fetch_dispatch_candidates`), which is the only consumer of `settings.dispatch_geo_provider`/`max_candidate_pool`. No other backend surface reads these columns. Mitigated by: (a) automatic per-dispatch fallback to the pre-change (`legacy`) behavior on any Postgis-path error, (b) a runtime-editable kill-switch, (c) the candidate-pool cap staying at parity with today's hardcoded value.
- Production currently has effectively zero real driver rows (dev/pre-launch-scale data, confirmed via the post-apply `EXPLAIN ANALYZE` returning `rows=0`) — so this change has not yet been exercised against real dispatch volume. It will be exercised the next time this data-population reaches launch scale, not today.

## 5. User-experience effect

- **400**: driver-facing — drivers opening the "Deactivation & Appeals Policy" screen now see real content instead of "No policy has been added yet."
- **401, 402, 403, 405**: no user-visible effect (dark/inert/pure schema).
- **404**: no *intended* user-visible effect — the goal is identical or better dispatch matching (true nearest-N instead of an unordered bounding-box read), not a visible change. If the Postgis path has an undiscovered issue, the fallback means the worst case is today's exact `legacy` behavior, not a worse one, on a per-dispatch basis.

## 6. Files modified

No repository files changed — this is a live production database change only, applied via Supabase MCP `apply_migration` (not through a repo commit). This Change Impact Log and the `ACTION_ITEMS.md` update in this PR are the durable record.

## 7. Before / after

```sql
-- Before (production settings row)
dispatch_geo_provider = 'legacy'
dispatch_direct_pool_enabled -- column did not exist
max_candidate_pool           -- column did not exist

-- After
dispatch_geo_provider = 'postgis'
dispatch_direct_pool_enabled = false
max_candidate_pool = 500
```

## 8. Rollback plan

Each migration's own file header specifies its rollback SQL. For the one live-behavior change (404):
```sql
ALTER TABLE public.settings ALTER COLUMN dispatch_geo_provider SET DEFAULT 'legacy';
UPDATE public.settings SET dispatch_geo_provider = 'legacy' WHERE dispatch_geo_provider = 'postgis';
```
**Faster runtime rollback, no migration needed**: `PUT /admin/settings` with `dispatch_geo_provider: "legacy"` — takes effect within ~60s per replica (in-process settings cache TTL), no deploy. This is the primary rollback path; the SQL above is only needed if the admin API itself is unavailable.

For 400/401/402/403/405, each file's own header comment has the exact `DROP`/`ALTER ... DROP COLUMN` rollback SQL — not repeated here, not expected to be needed (all are additive/dark).

## 9. Verification performed

- [x] Read all 6 files in full before applying anything.
- [x] Two review passes obtained (`spinr-migration-reviewer` for 400/401/402/403/405; `spinr-dispatch-reviewer` for 404) — both came back with no blockers, but one run was flagged by this session's own security classifier for an unspecified reason, so **every load-bearing claim from both reviews was independently re-verified** via direct read-only queries before trusting any of it: `legal_documents_doc_type_check` CHECK includes `'deactivation-appeals'`; `legal_documents_audience_doc_type_key` UNIQUE constraint exists; `ride_offers_ride_driver_uq` UNIQUE constraint exists; `record_insurance_period_transition(text, smallint, text)` exists with `EXECUTE` granted only to `service_role`/`postgres`; `dispatch_direct_pool_enabled` did not yet exist pre-apply (confirming 401's necessity); and via direct repo `grep` — `matching.py`/`dispatch_pool.py`/`schemas.py`/`core/lifespan.py` on `main` genuinely already reference `dispatch_direct_pool_enabled` (confirming Phase 2 T13 wiring is real, not dead code).
- [x] `drivers_nearby_location_geog` RPC, `drivers.location_geog` column, and its GiST index (`idx_drivers_location_geog_available`) confirmed live before applying 404.
- [x] Applied all 6 in numeric order via `mcp__Supabase__apply_migration`; each call returned `{"success": true}`.
- [x] Post-apply verification, all via read-only queries: `legal_documents` row present (400); `settings.dispatch_geo_provider = 'postgis'`, `max_candidate_pool = 500`, `dispatch_direct_pool_enabled = false` (401/404); `dispatch_claim_batch` function present with the final 6-arg signature matching 403 (402/403); `drivers.license_issue_date` column present (405).
- [x] Ran `EXPLAIN ANALYZE` on the actual inner query `drivers_nearby_location_geog`'s function body executes (not just the opaque outer "Function Scan" wrapper) against a real Saskatoon coordinate — confirmed `Index Scan using idx_drivers_dispatch_ready`, not a sequential scan; 11ms execution time; 0 rows (no matching drivers in the current near-empty `drivers` table).
- [x] Recorded `schema_migrations` tracking rows for all 6 files (`filename`, `checksum` = real `sha256sum` of each file) so `run_migrations.py --status`/`--dry-run` correctly reports these as applied on the next session that has `DATABASE_URL`.

## 10. What was NOT verified

- **The GiST index's behavior under real production data volume.** Production currently holds essentially zero real driver rows — the post-apply `EXPLAIN ANALYZE` confirms the query plan is sound (indexed, not a seq scan) but cannot demonstrate performance/correctness at the 500-driver scale the dispatch SLA targets are written against. This is the dispatch reviewer's own recommended follow-up, not fully closeable until real traffic/data exists.
- **`spinr_dispatch_geo_failover_total{from="postgis"}` was not watched post-apply** for the recommended 30-60 minute canary window — there is currently no real dispatch traffic in this environment to generate that signal either way.
- **No staging/canary rehearsal** — applied directly to the single production environment, per this session's read-only-tools-only access (no staging environment exists yet per E1, a separate open item).
- **The exact reason one review agent's run was flagged by the security classifier** — not diagnosed; every substantive claim from that run was independently re-verified against primary sources instead, and none of it turned out to be inaccurate, but the root cause of the flag itself remains unknown.

## 11. Sign-off

- [x] Rollback plan is concrete and testable — a runtime admin-dashboard toggle for the one live-behavior change (404), plus per-file SQL rollback for the rest.
- [x] Blast radius is stated, not assumed — 404 named explicitly as the one file with live-traffic impact, scoped to `matching.py`'s two candidate-read call sites, both confirmed wired (not dead code).
- [x] No silent behavior change to an already-shipped flow — 404's provider flip is disclosed here in full, with before/after values, exactly per CLAUDE.md's release-gate requirements for a live-tested dispatch-surface change.
