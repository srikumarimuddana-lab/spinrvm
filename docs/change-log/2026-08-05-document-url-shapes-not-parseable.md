# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-05 |
| Author | Claude Code (session-assisted) |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers (document storage), admin |
| PR / commit link | branch `claude/sgi-driver-export-issue-nkgvgk` |
| Related issue or gap ID | Operator: "File / scan / image / PDF is selected but it's not exporting the data" |

## 1. Issue / gap identified

With the **File** box explicitly ticked, driver document exports still contained no scans. That rules out the default-value cause fixed earlier today — the bytes were being requested and not arriving.

## 2. Root cause

`documents._extract_storage_key` could not parse several `document_url` shapes this codebase itself writes. It returns the storage key that `storage.download()` needs; `None` means no bytes, which surfaces as a metadata-only row.

Its pattern required `/storage/v1/object/(sign|public)/driver-documents/`. Three shapes miss it:

1. **`storage://driver-documents/<key>`** — `services/driver_import_service.py` writes this while building an import plan (`signed_placeholder`). `commit_plan` overwrites it with a real signed URL on the happy path, but any row that didn't complete that path keeps a custom scheme no HTTP pattern will ever match.
2. **`/storage/v1/object/authenticated/…`** — Supabase serves RLS-scoped objects from this path.
3. **Percent-encoded keys.** The key travels through the URL encoded, but `storage.download()` needs it raw — handing it `%20` 404s. Bulk-import keys are built from spreadsheet values (`saskatoon-import/<batch>/<old_id>/<requirement>/…`), so one space in a source cell is enough.

**Separately — and this one takes the whole bulk import down today:** `driver_import_service.storage_signed_url` read only `getattr(res, "data", None)`, the legacy response shape. The installed supabase-py returns a plain `dict` (verified by reading `storage3`'s `_make_signed_url`, which returns `{"signedURL": …, "signedUrl": …}`). A dict has no `.data`, so the function raised `create_signed_url returned no URL` on **every** call, failing `commit_plan` at the upload loop before any document row is written. `documents._extract_signed_url` was fixed for exactly this ("Railway was 500-ing with `'dict' object has no attribute 'data'` after supabase-py flipped the return type") and the fix was never propagated.

Whether the operator's specific rows hit cause 1, 2, 3, or a genuine storage fault is **not yet confirmed** — see "What was NOT verified". All four are real defects on the path regardless.

## 3. Fix / remediation

- `_extract_storage_key` now handles signed, public, **and authenticated** URLs (absolute or host-relative), the **`storage://`** scheme, and a bare key; and percent-**decodes** the result. A URL it genuinely cannot parse still returns `None` rather than fabricating a key from a hostname, so the caller reports `unavailable_no_storage_key` instead of silently downloading the wrong object.
- `storage_signed_url` delegates to `documents._extract_signed_url` — one implementation of response-shape handling instead of two that drift.

## 4. Risk & impact on existing functionality

**Blast radius: `_extract_storage_key` has three consumers**, all of which get strictly better:

| Consumer | Effect |
|---|---|
| `services/data_transfer/entity_export_service.py` | The reported bug — documents that produced no key now resolve one. |
| `documents.regenerate_signed_url` | Admin document preview for bulk-imported drivers was returning the unusable `storage://…` string as a fallback; those links now resolve. |
| `routes/admin/documents.py` (`_extract_storage_key` at line 85) | Same improvement on the admin document routes. |

The change is **widening only** — every URL that parsed before parses to the same key (pinned by parametrized tests for each pre-existing shape). The one new risk is the bare-key branch: a value with no `://` and no `/storage/v1/` is now treated as a key. That is guarded so a URL we failed to parse still yields `None`.

Percent-decoding changes the returned key for encoded URLs — that is the fix, not a side effect, but it means a key that previously "worked" by being wrong now differs. No object can have been fetched with an encoded key (it would have 404'd), so nothing that currently succeeds changes.

No schema, migration, background loop, or money/state-machine code involved.

## 5. User-experience effect

**Internal admin only.** No rider/driver/corporate-admin impact, nothing visible mid-session in the apps.

Two admin-facing improvements: document exports for bulk-imported drivers include their files, and document preview links for those drivers resolve instead of yielding an unusable `storage://` string.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/documents.py` | `_extract_storage_key` handles `authenticated`/`storage://`/bare keys and percent-decodes; `unquote` import | Parse every `document_url` shape the codebase writes |
| `backend/services/driver_import_service.py` | `storage_signed_url` delegates to `_extract_signed_url` | Current supabase-py returns a dict; the local copy only handled the legacy shape |
| `backend/tests/test_document_storage_key_extraction.py` | New file, 13 parametrized cases | No test covered this function; it is the single point of failure between a stored URL and its bytes |
| `backend/tests/test_driver_import_service.py` | 4 tests (dict shape, legacy shape, error names the key, import→export round-trip) | Pin the response-shape fix and the cross-module URL contract |

## 7. Before / after

```python
# Before — one pattern, no decoding
_STORAGE_KEY_RE = _re.compile(r"/storage/v1/object/(?:sign|public)/driver-documents/([^?#]+)")
m = _STORAGE_KEY_RE.search(stored_url or "")
return m.group(1) if m else None
# "storage://driver-documents/saskatoon-import/..."  -> None  (no bytes exported)
# ".../driver-documents/batch%201/x.pdf"             -> "batch%201/x.pdf"  (download 404s)

# After
m = _STORAGE_SCHEME_RE.match(raw)          # storage://driver-documents/<key>
if m: return _unquote(m.group(1))
m = _STORAGE_KEY_RE.search(raw)            # sign | public | authenticated
if m: return _unquote(m.group(1))
if "://" not in raw and "/storage/v1/" not in raw:
    return _unquote(raw)                   # already a bare key
return None                                # unparseable -> caller reports it
```

```python
# Before — dict has no .data, so this raised on every call
data = getattr(res, "data", None)
...
raise RuntimeError(f"create_signed_url returned no URL for {storage_key}")

# After
return _extract_signed_url(res)            # handles dict AND legacy object
```

## 8. Rollback plan

`git revert` is a complete rollback: no migration, no live-data mutation, no persisted state. The only outputs are on-demand signed URLs and ZIPs; already-generated ones are unaffected.

The two halves revert independently — reverting the `documents.py` hunk restores the old parser without touching the import fix, and vice versa.

## 9. Verification performed

- [x] **Automated tests run.** 686 passed across the document/storage/import/export selection; full backend suite run separately.
- [x] **Both fixes falsified against the old code**, not just asserted: reverting `storage_signed_url` to its previous body turns the new dict-shape test red (`1 failed, 16 passed`), then restored. The parser tests cover shapes the old regex provably could not match.
- [x] **Response shape confirmed by reading the installed library** (`storage3._sync.file_api._make_signed_url` returns a dict), not inferred from the symptom.
- [x] **Blast-radius grep performed** — all three `_extract_storage_key` consumers enumerated in §4.
- [x] **Backend lint/format** — `ruff check` on both changed files: "All checks passed"; `ruff format` clean.
- [x] **Test-placement error caught and corrected.** The 4 driver-import tests were first appended to a stray `tests/test_driver_import_service.py` at the repo root, so they never ran — the "13 passed" that followed was the original file's own tests. Moved into `backend/tests/`, confirmed the count went 13 → 17.

## What was NOT verified

- **The operator's specific failure is still unconfirmed.** These are four real defects on the path from a stored URL to its bytes, but nothing here proves which one (if any) their rows hit. The decisive evidence is the `file_export_status` column in their ZIP's `documents.csv`: `unavailable_no_storage_key` implicates the parser (fixed here), `unavailable_fetch_failed` implicates storage itself (not fixed here — that would be a missing or unreadable object), and `excluded_by_request` would mean the request never asked for the file.
- **Not tested against live Supabase.** No export was run against a real `driver-documents` bucket; all storage interaction is mocked. The percent-decoding fix in particular is reasoned from `storage.download()`'s contract, not observed against a real encoded key.
- **Whether `storage://` placeholders actually exist in the live `driver_documents` table is unknown.** `commit_plan` overwrites them on the happy path, so they persist only if that path didn't complete. Confirming would need a query against production — worth running: `select count(*) from driver_documents where document_url like 'storage://%'`.
- **No frontend involvement**, so no build was run for this change.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — all three consumers named
- [x] No silent behavior change: the parser only widens, and every pre-existing shape is pinned to its current result by test
