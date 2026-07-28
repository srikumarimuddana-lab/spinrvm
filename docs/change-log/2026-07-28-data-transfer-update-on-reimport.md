# Change Impact & Risk Log — Data Transfer module: update-on-reimport path

## Issue/gap identified
The Data Transfer bundle import always skipped an entity already imported
from the same source (resolution `existing_match`), with no way to pull a
refreshed export from the source environment into an already-onboarded
record. An admin re-importing a driver's bundle after the source
environment's profile changed (new documents, updated vehicle info, a
renewed licence) got no effect at all — the second import silently did
nothing for that entity.

## Root cause
`entity_import_service.commit_plan` only ever iterated `plan.to_create`
("new" resolution). The `existing_match` branch in `build_plan` recorded
the match for idempotency/reporting purposes only; nothing downstream
consumed it to actually update anything. This was a known, called-out gap
from the module's original build (flagged in the earlier critical review,
not previously requested to be fixed).

## Fix/remediation
Added an opt-in update path, off by default (fully backward compatible):

- `entity_import_service.py`:
  - `ImportPlan` gained `existing_rows` (entity_id -> the matched drivers/
    users row from `_find_existing_match`, so commit doesn't have to
    re-query) and a `to_update` property.
  - `commit_plan(plan, update_existing: bool = False)` — new optional
    parameter. When `True`, entities resolved `existing_match` get their
    `users`/`drivers` row fields overwritten from the bundle's current
    values (id, timestamps, and phone — the match key — are never
    touched; `legacy_import_metadata`/`user_id` on the drivers row are
    also excluded so the identity link and idempotency marker can't be
    clobbered), and `license_number` is re-encrypted against this
    environment's vault the same way the create path already does.
    Returns two new count fields, `updated_users`/`updated_drivers`.
- `bundle_document_uploader.py`: added `replay_new_documents` and
  `replay_new_insurance_periods` — dedup-aware wrappers around the
  existing `replay_documents`/`replay_insurance_periods`. Each fetches the
  target driver's existing rows first and skips any bundle entry whose
  natural key (`document_type`+`side` for documents, `period`+`started_at`
  for insurance periods) is already present, so re-running an "update"
  import on the same bundle doesn't pile up duplicate rows every time.
  Insurance-period replay is still strictly append-only (no
  update/delete of an existing row) — the dedup only prevents re-inserting
  an exact duplicate, it never touches a row already on file, matching the
  regulatory append-only convention in CLAUDE.md.
- `routes/admin/data_transfer_import.py`: `POST /data-transfer/import/commit`
  gained a `update_existing: bool = Form(False)` field, passed straight
  through to `commit_plan`. Also added to the `log_admin_action` audit
  payload so which mode a commit ran in is recoverable from the audit
  trail.

## Risk & impact on existing functionality
Blast radius: grepped every caller of `commit_plan`, `build_plan`,
`ImportPlan`, `replay_documents`, `replay_insurance_periods` across
`backend/` — the only caller of all of them is
`routes/admin/data_transfer_import.py` and the entity_import_service
module itself. (Other modules — `driver_import_service.py`,
`rider_import_service.py`, `stripe_mapping_import_service.py` — have
their own same-named `ImportPlan`/`build_plan`/`commit_plan` symbols but
are entirely separate classes/functions in separate files; nothing is
shared.) No other route, background loop, or service reads
`ImportPlan.existing_rows`/`to_update`, or calls the two new
`bundle_document_uploader` functions.

Default behavior is unchanged: `update_existing` defaults to `False`,
`commit_plan`'s `to_create` loop is untouched, and the `to_update` branch
only runs when a caller explicitly opts in. The one behavior-visible
change independent of the flag is the `_report()`/`commit_bundle_import`
response shape gaining `updated_users`/`updated_drivers` keys in the
returned counts dict (previously absent) — additive, not a rename or
removal, so no existing consumer of the response can break from a missing
key.

## User experience effect
Admin-facing only (Data Transfer import UI / API), not visible to
riders/drivers. Today the frontend Import tab (`ImportTab.tsx`) doesn't
yet expose an `update_existing` toggle — this PR wires the backend
contract only; a follow-up UI change would be needed to surface the
checkbox. Until then, `update_existing` is reachable via direct API call
only (e.g. an admin using the raw endpoint), which is the same exposure
level the underlying `/commit` endpoint has always had.

## Files modified
| File | What changed | Why |
|---|---|---|
| `backend/services/data_transfer/entity_import_service.py` | `ImportPlan.existing_rows`/`to_update`; `commit_plan` gains `update_existing` param and update branch | Actually consume the existing-match data build_plan already collected |
| `backend/services/data_transfer/bundle_document_uploader.py` | New `replay_new_documents`/`replay_new_insurance_periods` | Dedup-aware replay so a repeat "update" import doesn't duplicate documents/insurance periods |
| `backend/routes/admin/data_transfer_import.py` | `/commit` gains `update_existing` form field, passed to `commit_plan`; included in audit log payload | Expose the new capability through the route contract |
| `backend/tests/test_entity_import_service.py` | +4 tests: `existing_rows` population, update-path skip-when-flag-false, update-path field writes, plus fixed the `commit_plan` counts-equality assert for the two new keys | Cover the new branch; keep the existing create-path test accurate against the new return shape |
| `backend/tests/test_bundle_document_uploader.py` | New file, 3 tests | Zero-to-first coverage on `bundle_document_uploader.py` (a pre-existing gap noted in the prior PR's change log), focused on the new dedup functions |

## Before/after snippet
```python
# entity_import_service.py, before: existing_match was terminal — nothing
# ever read plan.resolutions["existing_match"] again after build_plan.
async def commit_plan(plan: ImportPlan) -> dict[str, int]:
    for entity in plan.to_create:
        ...  # existing_match entities: never touched, ever

# after: opt-in update path consumes the match build_plan already found.
async def commit_plan(plan: ImportPlan, update_existing: bool = False) -> dict[str, int]:
    for entity in plan.to_create:
        ...
    if update_existing:
        for entity in plan.to_update:
            existing = plan.existing_rows.get(entity.entity_id)
            # update users/drivers row fields (never id/created_at/phone/
            # user_id/legacy_import_metadata), re-encrypt license_number,
            # replay only new documents/insurance-periods
```

## Rollback plan
Revert the three source files (`git revert`) — the new behavior is
entirely gated behind `update_existing` defaulting to `False`, so a
revert restores the prior always-skip behavior with no data migration or
flag flip needed. No DB schema change, no migration, nothing to roll back
on already-written rows: `update_one` calls this PR adds only ever fire
when an admin explicitly passes `update_existing=true` on a real commit
call, so there is no accidental-write blast radius to unwind from normal
usage between now and a revert.

## Verification performed
- All 14 tests in `test_entity_import_service.py` and 3 in
  `test_bundle_document_uploader.py` (17 total, 4 new + 3 new file)
  executed against the real, unmodified source via the same manual
  `sys.modules`-stub harness used earlier in this session (pytest can't
  collect in this sandbox — no `httpx`/`fastapi`) — all pass.
- `python3 -m py_compile` on all 5 touched/new files — passes.
- Grepped every caller of the touched public symbols
  (`commit_plan`/`build_plan`/`ImportPlan`/`replay_documents`/
  `replay_insurance_periods`) to confirm isolated blast radius (see Risk
  section above) before writing the change.
- Manually traced the update-path field exclusions
  (`_UPDATE_EXCLUDED_USER_FIELDS`/`_UPDATE_EXCLUDED_DRIVER_FIELDS`)
  against the test assertions to confirm `phone`, `id`, `user_id`, and
  `legacy_import_metadata` can never be overwritten by an update commit.

## What was NOT verified
- Not run through the actual `pytest` CLI / `conftest.py` fixture chain —
  same standing constraint noted throughout this module's history; first
  real execution will be in CI.
- No frontend change: the Import tab UI doesn't yet expose an
  `update_existing` checkbox, so this capability is API-only until a
  follow-up UI change. Not tested against a real admin-dashboard flow for
  that reason — there is no UI flow to test yet.
- Not tested against live Supabase — `db_supabase.update_one`/`get_rows`
  are mocked in every test; the real Postgres round-trip (RLS policy
  coverage on `update_one` for `users`/`drivers` from this code path,
  actual column-type coercion) has not been exercised.
- No test covers a *rider* entity going through the update path end to
  end (the new tests use driver entities, matching the existing test
  file's convention) — the rider branch in `commit_plan`'s update loop is
  a straightforward subset (`users` table only, no driver_profile/
  documents/insurance-periods), reasoned about rather than separately
  tested.
