# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude (automated, on behalf of vikas@ngitservices.com) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | P0-A, `docs/audit/2026-08-11-driver-rider-migration-audit.md` |

## 1. Issue / gap identified

The admin bulk rider-CSV importer accepts a `batch` parameter but never
persists it anywhere. Every other importer in the codebase
(`driver_import_service.py`, `booking_import_service.py`,
`stripe_mapping_import_service.py`) stamps `legacy_import_metadata` on the
rows it touches so an admin can answer "which rows did import batch X
create/modify." Riders alone had no such trail: no batch-scoped audit
answer, no way to filter/rollback a specific bad batch, and a weaker PIPEDA
access/export answer ("show me everything imported about this user and
when").

## 2. Root cause

`users.legacy_import_metadata` (JSONB, migration 256) already exists —
added specifically to give users the same provenance surface drivers have —
but `rider_import_service.py`'s `user_row` construction (create path) and
`update_fields` construction (update path) never set it.

## 3. Fix / remediation

- New `IMPORT_SOURCE = "legacy_rider_csv_import"` constant.
- `_prefetch_existing()` now also selects `legacy_import_metadata` for
  matched users.
- Created rows: `legacy_import_metadata` is set to
  `{"rider_csv_import": {"batch": ..., "source": ..., "imported_at": ...}}`.
- Updated rows (when at least one other field is actually changing): merges
  the same `rider_csv_import` sub-key onto whatever metadata already exists
  on that row, instead of overwriting the column outright. This matters
  because `users.legacy_import_metadata` is a *shared* column —
  `stripe_mapping_import_service.py` writes its own `stripe_migration`
  sub-key onto the same rows for users who came through the separate Stripe
  customer-ID backfill. A user matched by both importers must keep both
  provenance records.
- No metadata write for rows where nothing else changed (mirrors
  `driver_import_service.py`'s resume behavior: a re-run that finds no
  actual delta doesn't re-stamp) and none for `protected_skip` rows (P0-C —
  those are never written to at all).

## 4. Risk & impact on existing functionality

- Blast radius: isolated to `rider_import_service.py`. Grepped all
  `legacy_import_metadata` readers/writers on `users` — the only other
  writer is `stripe_mapping_import_service.py`, which this fix explicitly
  merges with (tested — see below) rather than clobbers.
- `driver_import_service.py`'s `legacy_import_metadata` usage (on the
  `drivers` table, a different row/column) is untouched.
- No behavior change to which rows get created/updated or what
  user-visible fields they get — this only adds a metadata column write
  alongside writes that were already happening. A previously-skipped row
  (nothing to update) still writes nothing.

## 5. User-experience effect

None — `legacy_import_metadata` is never rendered in any UI; it's an
internal admin/compliance provenance field.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/rider_import_service.py` | Added `IMPORT_SOURCE`; `_prefetch_existing` selects `legacy_import_metadata`; `build_plan` stamps `{batch, source, imported_at}` under a `rider_csv_import` sub-key on create and on update-with-changes | Give the rider importer the same provenance trail every other importer has |
| `backend/tests/test_admin_rider_import.py` | 2 new tests: provenance stamped on create; provenance merges without clobbering an existing `stripe_migration` sub-key on update | Regression coverage |

## 7. Before / after

```python
# Before
plan.users_to_create.append(user_row)   # no legacy_import_metadata at all
```

```python
# After
user_row["legacy_import_metadata"] = {
    "rider_csv_import": {"batch": batch, "source": IMPORT_SOURCE, "imported_at": now_iso}
}
plan.users_to_create.append(user_row)

# update path merges rather than overwrites:
existing_meta = existing_user.get("legacy_import_metadata") or {}
update_fields["legacy_import_metadata"] = {
    **existing_meta,
    "rider_csv_import": {"batch": batch, "source": IMPORT_SOURCE, "imported_at": now_iso},
}
```

## 8. Rollback plan

Pure code change, no migration (column already exists from migration 256),
no destructive data mutation. `git revert` is sufficient — worst case is
future rows stop getting the new metadata key again; nothing already
written needs cleanup.

## 9. Verification performed

- [x] `pytest backend/tests/test_admin_rider_import.py -q --no-cov` → 19 passed (17 prior + 2 new)
- [x] `pytest backend/tests/test_admin_rider_import.py backend/tests/test_booking_import_service.py backend/tests/test_stripe_mapping_import_service.py -q --no-cov` → 82 passed, 1 skipped (cross-importer regression check, since they share the `users.legacy_import_metadata` column)
- [x] Blast-radius grep for all `legacy_import_metadata` read/write sites on `users`
- [ ] Manual repro in staging — not available in this sandbox

## What was NOT verified

- Not tested against a live Supabase instance — mocked `_FakeSupabase`
  fixture only.
- No visual regression tooling exists in this repo; this change has no UI
  surface at all (backend-only JSONB metadata), so nothing to screenshot.
- P0-B (admin dashboard double-counting legacy earnings) from the same
  audit is a separate, not-yet-addressed follow-up.
