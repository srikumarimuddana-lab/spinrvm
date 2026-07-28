# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude (B11/R-D follow-up on the Data Transfer PIA) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | (this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B11 / R-D (`docs/privacy/2026-07-28-pia-data-transfer-export.md`) |

## 1. Issue / gap identified

The PIA's R-D recommendation asked to shorten the Data Transfer export route's "initial 7-day signed URL TTL." Investigating it before implementing found the premise was inaccurate: `routes/admin/data_transfer_export.py`'s `_upload_bundle` minted a 7-day signed URL on every export inside the backgrounded task, but that value was immediately discarded (`_ = signed_url`, never persisted to the job row or returned to any caller) — the route responds `202` with only a `job_id` before the file even exists. There was no actual signed-URL exposure to shorten.

## 2. Root cause

The function's docstring/return-type claimed it returned `(signed_url, storage_path)`, matching the pattern in `routes/drivers/tax_exports.py`'s synchronous `_upload_export_zip`. When this route was refactored to be backgrounded (per its own module docstring: "replaced an earlier synchronous version... a real risk for the max 100-entity batch... tying up a request/response cycle"), the signed-URL generation was left in place even though backgrounding removed the only code path that could have used it — a leftover from the pre-refactor synchronous version, not a new design decision.

## 3. Fix / remediation

Removed the dead `create_signed_url` call from `_upload_bundle`; it now uploads and returns only `storage_path` (a plain string, not a tuple). Removed the now-unused `_extract_signed_url` import and the `_ = signed_url` discard line in `_run_export_job`. Renamed `_EXPORT_LINK_TTL_SECONDS` → `_EXPORT_RETENTION_SECONDS` since its only real remaining use is computing `expires_at` for the Storage-purge loop, not a URL TTL — the previous name was actively misleading about what the constant controlled.

## 4. Risk & impact on existing functionality

- **What else reads/writes the same table/endpoint?** `data_transfer_export_jobs` is also read by `data_transfer_jobs.py` (list/get/download endpoints, unchanged) and the purge loop (`utils/data_export_purge.py`, unchanged — still keyed off `expires_at`, whose computation is unchanged in value, only the constant's name changed).
- **Could this regress a working flow?** No — the removed value was never consumed by anything. Verified by grepping for `_upload_bundle` callers (only `_run_export_job`) and confirming no other code referenced the old tuple return or `_EXPORT_LINK_TTL_SECONDS` by name.
- **Blast radius:** isolated to `routes/admin/data_transfer_export.py`.
- **Money/wallet/ride-state-machine interaction:** none.

## 5. User-experience effect

None. No admin-visible behavior changes — the export flow's actual observable outputs (job status, download links via `/download`) are unchanged; this only removes an internal, never-surfaced computation. One minor operational improvement: exports complete slightly faster (one fewer Supabase Storage API round-trip per export) and Storage API usage drops correspondingly.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/data_transfer_export.py` | `_upload_bundle` no longer calls `create_signed_url`; returns `storage_path` only (was a `(signed_url, storage_path)` tuple); removed unused `_extract_signed_url` import and dead `_ = signed_url` line; renamed `_EXPORT_LINK_TTL_SECONDS` → `_EXPORT_RETENTION_SECONDS` | Close PIA recommendation R-D by fixing its actual root cause (wasted signed-URL generation), not a nonexistent exposure window |
| `backend/tests/test_data_transfer_export.py` (new) | 2 tests: confirms `_upload_bundle` no longer calls `create_signed_url` and returns a plain path string; confirms the existing "storage not configured" error path still raises | This route had zero dedicated tests before (39% file coverage per the earlier audit) — adds direct coverage for the changed function |
| `docs/privacy/2026-07-28-pia-data-transfer-export.md` | Corrected the data-flow description (§2) and R-004 to remove the inaccurate "7-day signed URL returned at export time" claim; marked R-D done with the real fix description | The original PIA text was wrong on this specific point; leaving it uncorrected would mislead a future reader about the module's actual exposure surface |

## 7. Before / after

```python
# Before
async def _upload_bundle(admin_id: str, file_bytes: bytes, ext: str, content_type: str) -> tuple[str, str]:
    ...
    res = await loop.run_in_executor(
        None,
        lambda: supabase.storage.from_(EXPORT_STORAGE_BUCKET).create_signed_url(storage_path, _EXPORT_LINK_TTL_SECONDS),
    )
    return _extract_signed_url(res), storage_path

# in _run_export_job:
signed_url, storage_path = await _upload_bundle(admin.get("id", "unknown"), file_bytes, ext, content_type)
...
# signed_url is intentionally not persisted — it's long-lived (7 days)
# but the Jobs tab always mints a fresh one on demand from storage_path
_ = signed_url
```

```python
# After
async def _upload_bundle(admin_id: str, file_bytes: bytes, ext: str, content_type: str) -> str:
    ...
    await loop.run_in_executor(
        None,
        lambda: supabase.storage.from_(EXPORT_STORAGE_BUCKET).upload(
            path=storage_path, file=file_bytes,
            file_options={"content-type": content_type, "upsert": "true"},
        ),
    )
    return storage_path

# in _run_export_job:
storage_path = await _upload_bundle(admin.get("id", "unknown"), file_bytes, ext, content_type)
```

## 8. Rollback plan

`git-revert-safe` — no data mutation, no migration, no schema change. Reverting restores the (unused) signed-URL generation with no functional consequence either way.

## 9. Verification performed

- [x] Automated tests: `backend/tests/test_data_transfer_export.py` (2/2 passing); full `pytest -k data_transfer` (25 passed, 1 skipped, 0 failed — no regressions).
- [x] `ruff check` clean on both changed/new files.
- [x] Blast-radius grep performed: confirmed `_upload_bundle` has exactly one caller (`_run_export_job`); confirmed no other file references `_EXPORT_LINK_TTL_SECONDS` or unpacks a 2-tuple from `_upload_bundle`.
- [ ] Manual repro in staging — not performed; verified via unit tests against mocked Supabase Storage client only.

## 10. What was NOT verified

- Did not verify against a real Supabase Storage bucket — only mocked `supabase.storage.from_(...)` calls.
- Did not measure the actual latency/API-call reduction from removing the `create_signed_url` call — expected to be small (one fewer network round-trip per export) but not benchmarked.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data dependency).
- [x] Blast radius is stated, not assumed (§4, grep-verified).
- [x] No silent behavior change — none occurred; the removed code path had no observable effect to begin with (§5).
