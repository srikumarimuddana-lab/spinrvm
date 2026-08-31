# Legacy migration tool order — canonical reference

**Status: this is now the single source of truth for tool-by-tool sequencing.**
It supersedes the sequencing notes in `docs/migration/2026-08-27-legacy-data-full-migration-approach.md`
§7 (which named 4 of 17 tools and predates most of what exists today) and
fills a gap `docs/runbooks/legacy-migration-playbook.md` never covered (that
doc is a 5-stage *process* playbook — extract/gap-analysis/normalize/review/
apply — not a tool-by-tool order). Neither older doc is deleted; §7 gets a
pointer to this file instead of being rewritten in place.

**Live status**: the admin dashboard's Bulk Operations page has a Migration
Checklist panel at the top showing each tool's real current state (done /
partial / not started / needs a manual check), pulled from
`GET /api/admin/migration-status` (`backend/services/migration_status_service.py`).
Use this doc for *why* the order is what it is; use that panel for *where
you actually are right now*.

## The order, and why each step needs what comes before it

| # | Tool | Where | Depends on |
|---|---|---|---|
| 1 | Bulk Driver Import (Saskatoon CSV) | `/dashboard/drivers/import` | Nothing — creates net-new drivers |
| 2 | Legacy Driver Import (Mongo `drivers.csv`) | `/dashboard/drivers/legacy-import` | Nothing hard-required, but its create/link/enrich decision reads whatever `users`/`drivers` rows already exist (including #1's) — run after #1 |
| 3 | Bulk Rider Import | `/dashboard/bulk-operations` | Nothing — creates net-new riders |
| 4 | Legacy SIN/DOB Backfill | `/dashboard/drivers/legacy-sin-dob-backfill` | A driver whose `legacy_import_metadata` has a `source` key **or** a `mongo_driver_history` key — i.e. created by #1 or #2 |
| 5 | Legacy Vehicle-History Backfill | `/dashboard/drivers/legacy-vehicle-history-backfill` | Same guard as #4 |
| 6 | Fix Orphaned Legacy-Linked Accounts | `/dashboard/drivers/legacy-import` | One-time repair for a since-fixed bug in #2's link path — only has candidates once #2 has run |
| 7 | Fix Backfilled Driver Join Dates | `/dashboard/drivers/legacy-import` | Repairs the `created_at` stamp #6 itself leaves behind — run after #6 |
| 8 | Stripe Mapping Import | `/dashboard/bulk-operations` | drivers-kind's `old_driver_id` lookup only matches #1's marker specifically; riders-kind needs #3 (or organic) for a phone/email match to exist at all |
| 9 | Bulk Driver Tax-ID Import | `/dashboard/bulk-operations` (API-only, no dedicated page yet) | Matches any existing driver by phone — needs #1 or #2 to have created the row |
| 10 | Legacy Saved-Address Backfill | `/dashboard/riders/legacy-saved-address-backfill` | Needs a rider account (`is_rider=true`) by phone — i.e. #3 (or organic) |
| 11 | Legacy Booking Import | `/dashboard/bulk-operations` | Matches riders/drivers by phone against whatever accounts exist **at run time**, with no fallback creation — run after #1/#2/#3 or unmatched parties' ride history is silently skipped |
| 12 | Fix Rider Join Dates | `/dashboard/bulk-operations` | Repairs #3's `created_at` stamp — only meaningful once #3 has run |
| 13 | Legacy Wallet-Balance Import | `/dashboard/bulk-operations` | Matches by phone against any existing account — same "account population must exist" logic as #11, no hard code dependency on #11 itself |
| 14 | Route Map Snapshots | `/dashboard/bulk-operations` | Hard-requires `rides.legacy_import_metadata IS NOT NULL` — only #11 writes that |
| 15 | Route Backfill | `/dashboard/bulk-operations` | Same hard requirement as #14 |
| 16 | Pre-Launch Legacy Data Flagging | `/dashboard/bulk-operations` | Needs #1/#2's source markers on drivers **and** #11's written rides — run last so the full population exists before deciding what's dormant/pre-launch |
| 17 | Migration Data Quality Scan | `/dashboard/bulk-operations` | Needs #11's written rides (checks completed rows for a missing driver/rider, a placeholder address, or \$0 fare) — run last, as a final audit pass over everything the chain above produced, not a dependency any other step reads |
| 18 | Driver-Repair Pass | `/dashboard/bulk-operations` | Needs #17's `missing_driver` finding and the CURRENT `drivers` table (#1/#2, including any later batch) — re-checks rides #17 flagged against whoever exists in `drivers` *now*, so it must run after #2 (or any later driver import) adds the driver a delta booking import ran ahead of. Driver-side only — see `docs/runbooks/migration-driver-rider-repair-scope.md` for why there is no rider-side equivalent yet |

Not part of the ordered chain above (unwired or already one-shot):

- **Legacy GST Backfill** (`legacy_gst_backfill_service.py`) — plan-builder only, no commit path by design, no admin route. Would slot after #11 if it ever gets one (reads `rides.legacy_import_metadata`, same as #14/#15).
- **Duration-Estimated Marker Backfill** — has a commit path and a CLI script (`backend/scripts/backfill_legacy_ride_duration_estimated.py`) but no admin route/UI. Also reads/writes `rides.legacy_import_metadata` after #11.
- **Legacy Payout Correction** (`legacy_payout_correction_service.py`) — write path exists but "not wired into any route, CLI entry point, or background loop" per its own docstring. Phase 5 of the migration-approach doc, gated on live Stripe access this repo doesn't have.
- **Legacy Insurance-Period GPS Correction** — CLI-only, already run once against production (156 rows corrected). Not a repeatable step.

**If your workflow is "upload the export, run everything from the dashboard"**: the three unwired tools above can't participate yet — they need their own admin routes built first. Flagged, not silently worked around.

## Two known hazards, not covered by either older doc

1. **Route Map Snapshots and Route Backfill (#14/#15) write immediately with no dry-run preview** — the only two tools on the Bulk Operations page that don't follow its own stated "every tool is dry-run first" contract. A preview step was added 2026-08-31 (see `docs/change-log/2026-08-31-route-regen-preview-mode.md`).
2. **A shared-column concurrency hazard**: `rides.legacy_import_metadata` is written by four different tools/backfills (#11, #14, #15, plus the unwired GST and duration-estimated backfills) using a read-merge-write pattern. Only some of them (the GST/duration-estimated backfills) implement the whole-column optimistic-concurrency guard documented in `legacy_gst_backfill_service.py`'s module docstring — worth checking before adding a fifth writer to this column.

## Verification performed for this doc

Built from a full codebase inventory (grepped every `backend/routes/admin/*.py` and its matching service, plus the admin-dashboard pages that reach them) cross-checked against the module-level dependency guards actually written in each service's code — not inferred from either older doc's own claims. The 17-item order above matches exactly what `migration_status_service.py` computes live.

**Added 2026-08-31**: #17 (Migration Data Quality Scan) — see `docs/runbooks/migration-data-quality-strategy.md` for what it checks and why it's a read-and-tag audit pass, not a data-repair tool. It doesn't gate any step above; it exists to catch what the chain above didn't fully resolve.
