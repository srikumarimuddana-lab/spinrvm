# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | Claude Code session (spinr migration work) |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | #4633 (`claude/migration-batch-readiness-wicr1d`) |
| Related issue or gap ID | `docs/migration/2026-08-27-legacy-driver-blank-name-root-cause.md` Finding 2; `docs/runbooks/legacy-migration-playbook.md` item #11 |

## 1. Issue / gap identified

`backend/services/driver_import_service.py`'s Phase 1 Mongo driver importer
(`build_mongo_driver_import_plan`) treated any CSV row whose phone/email already matched an
existing `users` or `drivers` row as a hard error. Since `commit_mongo_driver_import_plan` refuses
to write anything while any error is present, this meant the batch could never actually commit
against production once real accounts existed to collide with.

## 2. Root cause

Not a bug in the rule's original intent (matching `booking_import_service.py`'s own "never
silently merge" safety rule) — the problem was scale, only visible once checked against real data.
A read-only query against production (`soavhtdhefowwvforzwb`) found 324 of the real export's 910
unique driver phones (35.6%) already match an existing account — 212 of those already a real
`drivers` row, the remaining 112 an existing `users` row (almost all riders picked up by
`booking_import_service.py`'s own ride-time phone matching). Full data and methodology:
`docs/migration/2026-08-27-legacy-driver-blank-name-root-cause.md` §3.

## 3. Fix / remediation

An existing-account match is now linked/enriched instead of rejected, in two shapes:

- **Matches an existing `drivers` row** → enrich that driver's `legacy_import_metadata` with an
  additive `mongo_driver_history` entry (append-only list). No new driver row is created; no other
  field on the live driver (name, phone, status, vehicle, rating, `is_verified`/`is_online`/
  `is_available`) is ever touched.
- **Matches an existing `users` row with no driver yet** → the new driver row is still created as
  normal, but points at the *existing* `user_id` instead of a new one (`users.phone` is `UNIQUE`),
  and `is_driver=True` is set on that user if not already true.
- A driver/account already linked or enriched by a *previous* run of this importer for the same
  `old_driver_id` resumes (skip, warning) instead of double-recording — checked against both the
  original top-level `source`/`old_driver_id` shape and the new `mongo_driver_history` list shape.

Both merges follow the additive-merge-under-a-namespaced-key convention already established
elsewhere in this codebase (`stripe_mapping_import_service.py`'s `legacy_import_metadata.
stripe_migration`, `rider_import_service.py`'s `legacy_import_metadata.rider_csv_import`) rather
than inventing a new shape.

## 4. Risk & impact on existing functionality

- **Blast radius, widened from the previous change:** this fix, for the first time in Phase 1,
  writes to *existing* `users`/`drivers` rows (via `UPDATE`), not just new inserts. Checked before
  building: `drivers` has exactly one row-level trigger (`trg_drivers_location_geog`, migration
  170), fired only on `UPDATE OF lat, lng` — this fix never touches those columns, so it does not
  fire. No other trigger exists on either table. `_prefetch_existing`'s `users` `SELECT` was
  widened to add `is_driver`/`legacy_import_metadata` (needed for the merge); grepped every other
  caller of that function (the Saskatoon `build_plan`) and confirmed it only reads `id`/`phone`
  from the returned rows, so the two extra columns are inert there — no behavior change to the
  Saskatoon path.
- **Additive-only guarantee, and how it was checked, not assumed:** validated against real
  production `legacy_import_metadata` values (read-only Supabase query, phones only — no names, no
  full rows kept) that already carry unrelated prior-importer keys (`stripe_migration`,
  `rider_csv_import`, `address_present`). Confirmed the merge (`{**existing_meta, "mongo_driver_
  history": history}`) preserves all of them untouched, and the update payload never includes
  `status`/`role`/`phone`/vehicle fields for either an enriched driver or a linked user.
- No other reader of `legacy_import_metadata.mongo_driver_history` exists yet (new key, this
  commit) — nothing downstream currently branches on it, so there is no consumer to regress.
- Same as the blank-name fix: this importer has never been run against production and has no
  admin-dashboard execution path wired up, so nothing user- or admin-visible changes as a result of
  this commit alone.

## 5. User-experience effect

None yet — no execution path is wired up for this importer beyond the CLI.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/driver_import_service.py` | `_prefetch_existing`'s users `SELECT` widened; `MongoDriverImportPlan` gains `users_to_update`/`drivers_to_enrich`; new `_mongo_driver_already_linked` helper; the existing-match branch rewritten to link/enrich instead of erroring; `commit_mongo_driver_import_plan` and `print_mongo_driver_import_report` updated; section-header comment updated | Decided policy for Finding 2 |
| `backend/tests/test_legacy_mongo_driver_import_service.py` | Local fake `_FakeQuery` gained `.update()`; the 4 existing-match tests replaced with 6 covering both sub-populations, both resume shapes, and the different-old-id case | Cover the new behavior |
| `backend/scripts/import_legacy_mongo_drivers.py` | Docstring rollback section split into three shapes (new/linked/enriched); `--apply` guard and logging cover the new plan fields | Keep the CLI's own documentation and output honest about what it now does |
| `docs/migration/2026-08-27-legacy-driver-blank-name-root-cause.md` | §3/§6/§7 updated: Finding 2 marked decided and shipped, including a correction of this doc's own earlier "safe to skip" recommendation | Keep the deep-dive doc in sync with what was actually built |
| `docs/runbooks/legacy-migration-playbook.md` | Item #11 updated: Finding B marked resolved with the full decision record | Canonical Oct 30 checklist stays current |
| `docs/migration/2026-08-27-legacy-data-full-migration-approach.md` | Phase 1 status + decisions log updated | Keep the parent plan doc in sync |

## 7. Before / after

```python
# Before
matched_user = users_by_phone.get(phone) or (users_by_email.get(email) if email else None)
matched_driver = drivers_by_phone.get(phone)
if matched_user or matched_driver:
    meta = (matched_driver.get("legacy_import_metadata") or {}) if matched_driver else {}
    if matched_driver and meta.get("source") == MONGO_IMPORT_SOURCE and str(meta.get("old_driver_id")) == old_id:
        plan.warnings.append(...)  # resume
        continue
    plan.errors.append(ImportErrorItem(old_id, "phone/email", "matching user or driver already exists; handle manually before import"))
    continue
```

```python
# After (abridged -- see the file for the full comment/reasoning)
if matched_driver or matched_user:
    if already_linked:  # via top-level shape OR mongo_driver_history list
        plan.warnings.append(...)  # resume
        continue
    if matched_driver:
        plan.drivers_to_enrich.append({"id": matched_driver["id"], "legacy_import_metadata": {**driver_meta, "mongo_driver_history": [...]}})
        plan.warnings.append(...)
        continue
    plan.users_to_update.append({"id": matched_user["id"], "is_driver": True, "legacy_import_metadata": {**user_meta, "mongo_driver_history": [...]}})
    plan.warnings.append(...)
    user_id = matched_user["id"]
    create_new_user = False
# falls through to create the driver row either way (unless matched_driver, which continued above)
```

## 8. Rollback plan

No feature flag exists or is needed — this importer has never been run against production and is
not wired to any HTTP route, so reverting the commit fully restores the strict-error behavior with
no data-level cleanup required. If this ships and `--apply` is later run, rollback has three shapes
(documented in the CLI's own docstring, since they touch different rows):
- **New rows** (no existing-account match): delete the driver row and its now-orphaned user row.
- **Linked accounts**: delete the new driver row; remove this batch's entry from the existing
  user's `legacy_import_metadata.mongo_driver_history`; only clear `is_driver` back to False if it
  was False before this run AND no other driver row now references that user.
- **Enriched drivers**: remove this batch's entry from the existing driver's
  `legacy_import_metadata.mongo_driver_history` — no other field was touched.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_legacy_mongo_driver_import_service.py` (25
      pass) and the full driver-import family (7 files, 180/180 pass, zero collateral breakage).
- [x] `ruff check` / `ruff format --check` clean on all three changed Python files.
- [x] Regression check: re-ran the plan-build step against the real `drivers.csv` (925 rows) with
      an empty mocked Supabase — identical output to before this change (924/925 clean, 587
      warnings, 1 residual phone error) — confirms the no-match path is unaffected.
- [x] Real-shape validation: re-ran the plan-build step against the real `drivers.csv` with a store
      seeded from real production `legacy_import_metadata` values for two real phones (one
      driver-match, one user-only-match) — confirmed the additive merge preserves unrelated
      existing keys and never writes a live field; confirmed the resume check correctly handles the
      real export's phone-reuse across multiple old-app records (3 occurrences of each seeded
      phone in the real file).
- [x] Blast-radius grep performed: confirmed `drivers` has exactly one row-level trigger and it
      does not fire on this write; confirmed the widened `users` `SELECT` is inert for the
      Saskatoon `build_plan`'s own use of `_prefetch_existing`.
- [x] Read-only production query (Supabase MCP) used only to confirm real metadata shapes and size
      the finding — no write exercised against production from this session.
- [ ] Manual repro in staging — not applicable, no execution path is wired up yet.
- [x] Reviewed against relevant CLAUDE.md conventions: additive-over-destructive (explicit design
      goal of this whole fix), PIPEDA (no names/phones in reports, only ids), "escalate, don't
      silently ship" (the merge-policy decision was put to the product owner before building,
      including correcting this session's own earlier "skip" recommendation once reconsidered).

## 10. Sign-off

- [x] Rollback plan is concrete: no live data exists yet, so revert-the-commit is complete; the
      future-`--apply` rollback path (three shapes) is stated for when that changes.
- [x] Blast radius stated: the one relevant trigger and the one other `_prefetch_existing` caller
      were checked, not assumed.
- [x] No silent behavior change to an already-shipped flow — this importer has never shipped or run
      against production.
