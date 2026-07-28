# Change Impact & Risk Log — Data Transfer module: tabular writer + purge extension (Phase 1.3)

## Issue/gap identified
1. The export route only produces ZIPs; there's no CSV/Excel/JSON option for
   an admin who just wants a spreadsheet, not documents+history.
2. The new `data-transfer-exports` bucket / `data_transfer_export_jobs` table
   (Phase 1.1/1.2) have no purge mechanism — left alone, exported ZIPs
   accumulate in Storage forever (same PIPEDA data-minimization concern the
   existing `data_export_purge.py` loop was built to solve for DSAR exports).

## Root cause
Phased build — the export route shipped ZIP-only in Phase 1.2 by design; the
purge gap is a direct consequence of adding a new bucket/table in Phase 1.1
without also extending the one piece of infrastructure that expires them.

## Fix/remediation
- New `backend/services/data_transfer/tabular_writer.py`: `write_csv`,
  `write_json`, `write_excel` (using `openpyxl`, added in Phase 1.4) —
  flattens each entity bundle to one summary row (profile fields + counts of
  rides/documents/insurance-periods; full nested history stays in the
  ZIP/JSON export, not a spreadsheet row). Reuses the same CSV-injection
  sanitization convention as the frontend's `export-csv.ts` so a cell like
  `=cmd|...` round-trips identically through either export path.
- Modified `backend/utils/data_export_purge.py`: generalized the existing
  `_tick()` from a hardcoded `data_export_objects`/`data-exports` sweep to a
  `_tick(table, bucket)` parameterized function, called twice per hourly loop
  iteration — once for the existing DSAR table/bucket (unchanged behavior)
  and once for the new `data_transfer_export_jobs`/`data-transfer-exports`
  pair. Added `.not_.is_("expires_at", "null")` to the query since (unlike
  `data_export_objects.expires_at`, which is `NOT NULL`) a job that fails
  before upload never gets an `expires_at` and must not match the sweep.

## Risk & impact on existing functionality
Blast radius on `data_export_purge.py`: this is the **only** consumer of
`_tick()` (grepped — no other caller, it's a private module function called
solely from `data_export_purge_loop`). The refactor is behavior-preserving
for the existing DSAR sweep: same table, same bucket, same query shape, same
per-row error isolation — I only parameterized what was previously two
hardcoded module constants (`_BUCKET`, and the literal `"data_export_objects"`
table name inline in the query). The added `.not_.is_("expires_at", "null")`
filter is a no-op for the existing table (its `expires_at` is `NOT NULL`, so
every row already satisfies it) — verified against migration 200's schema.
`data_export_purge_loop` itself is spawned once at startup by
`core/lifespan.py` (unchanged — no signature change to the loop function).
`tabular_writer.py` has zero existing callers (new file); it will be wired
into the export route's format switch in a follow-up subtask (not yet — this
commit adds the writer functions but does not change `data_transfer_export.py`'s
"zip" contract, so `write_csv`/`write_json`/`write_excel` are currently
dead code, exercised by unit tests but not yet reachable via the route).

## User experience effect
None yet — not wired to the route in this commit.

## Files modified
| File | What changed | Why |
|---|---|---|
| `backend/services/data_transfer/tabular_writer.py` | New: CSV/Excel/JSON writers for entity bundles | Non-ZIP export formats |
| `backend/utils/data_export_purge.py` | Parameterized `_tick()`; loop now sweeps two table/bucket pairs | Purge the new export bucket/table on the same 7-day TTL convention |

## Before/after snippet
```python
# before
async def _tick() -> None:
    ...
    supabase.table("data_export_objects")...

# after
async def _tick(table: str, bucket: str) -> None:
    ...
    supabase.table(table)...

async def data_export_purge_loop() -> None:
    while True:
        await _tick("data_export_objects", _BUCKET)          # unchanged sweep
        await _tick(_DATA_TRANSFER_TABLE, _DATA_TRANSFER_BUCKET)  # new sweep
        ...
```

## Rollback plan
Revert `data_export_purge.py` to the single-table `_tick()` (git revert is
safe here — the change is additive/parameterizing, not destructive, and no
other code depends on the new signature). Delete `tabular_writer.py` — no
other file imports it yet.

## Verification performed
- `python3 -m py_compile` on both files — passes.
- Manually traced `_tick()`'s only caller (`data_export_purge_loop`) via grep
  to confirm no other code depends on the old zero-arg signature.
- Cross-checked `data_export_objects.expires_at` is `NOT NULL` in migration
  200 to confirm the added `expires_at IS NOT NULL` filter doesn't change
  behavior for the existing sweep.

## What was NOT verified
- No unit test added for `tabular_writer.py` yet (planned once it's wired
  into the export route's format parameter).
- Purge loop change not exercised against a running Supabase instance or the
  mock_supabase_client fixture — reasoned about via code reading only.
- `openpyxl` import in `write_excel` is untested (dependency added in the
  next subtask, 1.4) — deferred import (`from openpyxl import Workbook`
  inside the function) keeps this file importable before the dependency
  lands, but the Excel path itself is unexercised until 1.4 merges.
