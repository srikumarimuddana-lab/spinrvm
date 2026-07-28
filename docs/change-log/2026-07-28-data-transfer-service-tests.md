# Change Impact & Risk Log — Data Transfer module: service-layer test coverage + import-fallback bug fixes

## Issue/gap identified
Flagged repeatedly in the earlier critical review: the Data Transfer
module's service layer (`entity_export_service.py`, `entity_import_service.py`,
`bundle_zip_builder.py`, `tabular_writer.py`) had **zero** test coverage —
only the pure PDF-fill logic (`sgi_form_filler.py`) and search
filter-construction helpers had tests. CLAUDE.md sets a ≥70% minimum for
admin routes/utilities; this module was nowhere close.

While building a real test harness to exercise these modules (bypassing the
`backend` package's `__init__.py` import chain, which needs dependencies —
`httpx`, `fastapi` — not installed in this session, same constraint noted
throughout this module's history), **discovered four more instances of the
same bug class already fixed once in this module**: `except ImportError`
fallback branches (the non-package/CLI-style import path, e.g.
`python server.py` directly instead of `python -m backend.server`) missing
a symbol the `try` branch imports. Confirmed by literally trying to import
each module through its fallback path and hitting `ImportError`/`NameError`.

## Root cause
Coverage gap: no test forced these modules through their actual import
paths, so a missing symbol in the rarely-exercised `except ImportError`
branch went unnoticed at every prior commit. Each occurrence was introduced
when a *later* commit added a new import to the `try` branch without
updating the mirrored fallback branch — an easy omission with no test to
catch it (this exact class of bug was already found and fixed once, in
`bundle_document_uploader.py`, during Phase 2.2 — but the fix wasn't
generalized into a check across the rest of the module).

## Fix/remediation

**Bug fixes** (missing imports in `except ImportError` fallback branches):
- `entity_export_service.py`: fallback was missing `_decrypt_driver_pii`
  entirely — the module would raise `NameError` under the fallback import
  style the moment `gather_entity_bundle` tried to decrypt a driver's
  `license_number`.
- `entity_import_service.py`: fallback was missing `_vault_encrypt` — same
  class of failure in `commit_plan`'s re-encryption step.
- `data_transfer_export.py`, `data_transfer_import.py`, `sgi_forms.py`:
  fallback branches missing `observability` (added in the Sentry/Prometheus
  follow-up, but only to the `try` branch).

**New test files** (all genuinely executed against the real, unmodified
source during this session — not just written and assumed correct):
- `test_data_transfer_zip_builder.py` (5 tests) — pure-function coverage
  for `bundle_zip_builder.build_export_zip`: multi-entity folder structure,
  document-with-bytes vs. document-without-bytes (must not write a dangling
  empty file, per the module's own documented contract), and confirms the
  private `_content` key never leaks into the JSON manifest.
- `test_data_transfer_tabular_writer.py` (6 tests) — CSV/JSON/Excel writers:
  row-per-entity shape, empty-input edge case, CSV-formula-injection
  sanitization (the OWASP guard this module claims to mirror from the
  frontend), and a real `openpyxl` round-trip read-back of the generated
  workbook.
- `test_entity_export_service.py` (7 tests) — `gather_entity_bundle`/
  `gather_entity_bundles` with `db_supabase`/`_decrypt_driver_pii`/`supabase`
  monkeypatched (mirrors `test_driver_import_service.py`'s
  `monkeypatch.setattr(svc, ...)` pattern, applied to the higher-level
  `db_supabase.get_rows` wrapper this service calls): entity-not-found,
  unknown entity-type rejection, driver vs. rider bundle shape, doc-type
  filtering, a document-fetch failure leaving `_content=None` without
  aborting the bundle, and multi-entity `gather_entity_bundles` skipping one
  failing entity while keeping the rest.
- `test_entity_import_service.py` (11 tests) — `parse_bundle_zip` (pure:
  valid ZIP, invalid ZIP, a folder missing `raw_data.json`), `build_plan`'s
  four resolution paths (empty bundle, unknown entity type, missing phone,
  new/existing_match/conflict), and `commit_plan`'s full write path —
  including the specific regression this session's earlier fix targeted:
  **asserts `license_number` in the inserted row is the re-encrypted value,
  never the bundle's plaintext**, and that both `bundle_document_uploader`
  replay calls receive the same freshly-generated `driver_id` the driver
  row was actually inserted with.

## Risk & impact on existing functionality
Blast radius of the bug fixes: all five are additive-only within
already-dead code paths (the fallback branch only executes under an import
style this module has never actually been run with in this session's
history — `python -m backend.server` always takes the `try` branch). Fixing
them cannot change any currently-observed behavior; it only prevents a
latent crash if someone ever runs the backend the other way. Grepped for
every other consumer of the five files touched — no other file's behavior
changes (only import statements inside `except` blocks were edited).

Blast radius of the new tests: zero — test-only files, no production code
path is exercised differently by their presence.

## User experience effect
None.

## Files modified
| File | What changed | Why |
|---|---|---|
| `backend/tests/test_data_transfer_zip_builder.py` | New: 5 tests | Zero-to-first coverage on the ZIP builder |
| `backend/tests/test_data_transfer_tabular_writer.py` | New: 6 tests | Zero-to-first coverage on CSV/JSON/Excel writers |
| `backend/tests/test_entity_export_service.py` | New: 7 tests | Zero-to-first coverage on the export gather service |
| `backend/tests/test_entity_import_service.py` | New: 11 tests | Zero-to-first coverage on parse/build_plan/commit_plan |
| `backend/services/data_transfer/entity_export_service.py` | Fixed missing `_decrypt_driver_pii` in fallback import | Prevent a `NameError` under the CLI/non-package import style |
| `backend/services/data_transfer/entity_import_service.py` | Fixed missing `_vault_encrypt` in fallback import | Same |
| `backend/routes/admin/data_transfer_export.py` | Fixed missing `observability` in fallback import | Same |
| `backend/routes/admin/data_transfer_import.py` | Fixed missing `observability` in fallback import | Same |
| `backend/routes/admin/sgi_forms.py` | Fixed missing `observability` in fallback import | Same |

## Before/after snippet
```python
# entity_export_service.py, before:
except ImportError:
    import db_supabase
    from documents import _extract_storage_key
    from supabase_client import supabase   # _decrypt_driver_pii missing -> NameError later

# after:
except ImportError:
    import db_supabase
    from documents import _extract_storage_key
    from routes.drivers._shared import _decrypt_driver_pii
    from supabase_client import supabase
```

## Rollback plan
Test files: delete them (`git revert` is safe, no other code depends on
them). Import-fix files: revert to prior state (`git revert` is safe — the
fallback branch these touch is not the path this backend runs under in
production, per `server.py`'s dual-import-pattern convention documented in
CLAUDE.md, so there's no live behavior to preserve either way).

## Verification performed
- **Every single test in all four new files was actually executed against
  the real, unmodified source** — not just written and assumed correct.
  Since `pytest`'s normal collection requires `conftest.py` to import
  successfully (which needs `httpx`/`fastapi`, unavailable in this
  session), verification used a manual harness: a stubbed `backend.*`
  package tree providing lightweight stand-ins for the transitive
  dependencies each module's real `try` import branch pulls in
  (`db_supabase`, `documents`, `routes.drivers._shared`, `supabase_client`),
  then loaded the real service module and the real test file via
  `importlib.util` and called every `test_*` function directly (with a
  minimal `monkeypatch`-compatible shim supporting `.setattr()`/`.undo()`
  for the tests that need it). All 29 tests across the four files pass.
- This same verification process is what **found** the four import-fallback
  bugs fixed in this commit — building a harness that actually imports
  these modules (rather than assuming the `try` branch is the only one that
  matters) is what surfaced them.
- `python3 -m py_compile` on all nine touched/new files — passes.

## What was NOT verified
- Not run through the actual `pytest` CLI / `conftest.py` fixture chain —
  same constraint noted throughout this module's history. The manual
  harness runs the identical test-function bodies and asserts, but pytest's
  own collection, fixture injection (`monkeypatch`, `anyio` mode), and
  reporting were not exercised. When CI (which has the full dependency
  stack) runs `pytest`, this is the first time these tests execute inside
  the project's real harness.
- Route-level (HTTP-layer) tests — `TestClient` hitting the actual FastAPI
  endpoints — were not added. This commit covers the service layer the
  routes call into, which is where the actual business logic and the bugs
  found here live; the routes themselves are thin (parse request → call
  service → shape response) and lower-risk, but a `TestClient`-based test
  would additionally exercise FastAPI's request validation, dependency
  injection (`get_admin_user`), and the rate-limit decorator — none of
  that is covered by these tests.
- Coverage is not exhaustive even at the service layer:
  `bundle_document_uploader.py`'s `replay_documents`/
  `replay_insurance_periods` are exercised only indirectly (as mocks) by
  `test_entity_import_service.py`, not directly with their own test file.
  `data_transfer_search.py`'s route handler (vs. its filter-construction
  helpers, already tested) and `data_transfer_jobs.py` remain untested.
