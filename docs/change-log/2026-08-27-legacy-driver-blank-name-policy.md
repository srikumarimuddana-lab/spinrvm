# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | Claude Code session (spinr migration work) |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | #4633 (`claude/migration-batch-readiness-wicr1d`) |
| Related issue or gap ID | `docs/migration/2026-08-27-legacy-data-full-migration-approach.md` §4 Phase 1 |

## 1. Issue / gap identified

`backend/services/driver_import_service.py`'s Phase 1 Mongo driver importer
(`build_mongo_driver_import_plan`) treated a blank `name` field in the legacy `drivers.csv` export
as a row-level error. Since `commit_mongo_driver_import_plan` refuses to write anything while any
error is present, this blocked the entire batch's commit — not just the affected rows — and 588 of
925 real-export rows (63.6%) hit it.

## 2. Root cause

Not a parsing/export bug. Confirmed against the real 2026-08-22 export: every blank-name row has
`set_up_profile=false` (a 100% correlation both directions) plus `is_email_verify=false` and
`is_approved=''` — the old-app account verified its phone via OTP and never finished the in-app
profile-setup step. None of the 588 rows are ever referenced by any `bookings.csv` row's
`driver_id` (0/588, vs. 102/337 for named/completed-profile rows) — these accounts never drove a
trip. Full methodology and data:
`docs/migration/2026-08-27-legacy-driver-blank-name-root-cause.md`.

## 3. Fix / remediation

Blank `name` is now a warning, not an error. The row still imports, with a synthetic placeholder
name (`"Unnamed Legacy Driver {old_id[-6:]}"`, traceable back to the source `_id`) and a new
`legacy_import_metadata.incomplete_profile_in_source` boolean derived from `set_up_profile` (not
merely from the name being blank). Every imported row — named or placeholder — still lands
`status='needs_review'`, `is_verified=False`, `is_online=False`, `is_available=False`
unconditionally; this fix touches only which rows get *created*, never their eligibility.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one new code path.** `build_mongo_driver_import_plan`/
  `commit_mongo_driver_import_plan` (added this session, not yet wired to any admin route, never
  run against production) are the only callers. Grepped the module: the Saskatoon-CSV `build_plan`/
  `commit_plan`, the SIN/DOB backfill, and the vehicle-history backfill are separate functions in
  the same file and untouched by this change.
- No existing row is mutated — this only changes which *new* rows a not-yet-executed importer would
  create. No live ride, dispatch, payment, or insurance-period code path reads this importer's
  output today.
- Admin dashboard: `admin-dashboard/src/app/dashboard/drivers/page.tsx` already renders a generic
  badge for any non-empty `legacy_import_metadata` and a `needs_review` status badge — both apply
  to placeholder-named rows the same as named ones, confirmed by reading the page; no admin-side
  change was needed or made.
- A second, larger, **still-open** finding was surfaced while validating this fix (35.6% of the
  real export's phones already match an existing production account, which will also block a real
  `--apply` run under the existing "existing match = error" rule) — **not fixed here**, flagged for
  a separate decision. See the root-cause doc §3/§6 and
  `docs/runbooks/legacy-migration-playbook.md` item #11.

## 5. User-experience effect

None. This importer has no admin-dashboard execution path wired up yet and has never been run
against production — nothing user- or admin-visible changes as a result of this commit alone.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/driver_import_service.py` | Blank `name` in `build_mongo_driver_import_plan`: error→warning + placeholder name + `incomplete_profile_in_source` metadata flag; section-header comment updated | Root cause confirmed as abandoned onboarding with zero ride linkage, not a data-quality bug |
| `backend/tests/test_legacy_mongo_driver_import_service.py` | Replaced `test_missing_name_is_error` with two new tests for the placeholder-name path; added an `incomplete_profile_in_source` assertion to the happy-path test | Cover the new behavior and the derived-flag correctness |
| `docs/migration/2026-08-27-legacy-driver-blank-name-root-cause.md` | New — full root-cause writeup + the existing-match collision finding | Deep-dive requested; feeds the Oct 30 playbook |
| `docs/runbooks/legacy-migration-playbook.md` | New item #11 (Legacy driver-profile import), matching the file's existing dated-annotation convention | Phase 1 wasn't covered by items 1-10; the two findings need to be trackable on the canonical Oct 30 checklist |
| `docs/migration/2026-08-27-legacy-data-full-migration-approach.md` | Phase 1 section + decisions log updated with the resolved/open findings | Keep the parent plan doc in sync rather than only the new deep-dive doc |

## 7. Before / after

```python
# Before
name = (row.get("name") or "").strip()
if not name:
    plan.errors.append(ImportErrorItem(old_id, "name", "row has no name; needs manual review"))
    continue
first_name, last_name = split_name(name)
```

```python
# After
name = (row.get("name") or "").strip()
incomplete_profile_in_source = parse_bool(row.get("set_up_profile", "")) is False
if not name:
    name = f"Unnamed Legacy Driver {old_id[-6:]}"
    plan.warnings.append(
        ImportErrorItem(
            old_id, "name",
            "row has no name (abandoned onboarding in source app, set_up_profile=false; "
            "never linked to any ride); imported with placeholder name, forced needs_review",
        )
    )
first_name, last_name = split_name(name)
# ... legacy_import_metadata now also carries incomplete_profile_in_source
```

## 8. Rollback plan

No feature flag exists or is needed — this importer has never been run against production and is
not wired to any HTTP route, so there is no live data to roll back. Reverting the commit fully
restores the strict-error behavior with no data-level cleanup required. If this fix is later
`--apply`'d and needs reversal after the fact, the rollback is: delete the created `drivers`/`users`
rows by `legacy_import_metadata.source = 'legacy_mongo_driver_import'` and the batch id printed by
the CLI at commit time — no cascading state (no ride, payment, or insurance-period row) is created
by this importer, so that delete is safe and complete.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_legacy_mongo_driver_import_service.py` (23
      pass) and the full driver-import family (7 files, 178/178 pass, zero collateral breakage).
- [x] `ruff check` / `ruff format --check` clean on both changed Python files.
- [x] Re-ran the plan-build step against the real `drivers.csv` (925 rows, mocked empty Supabase):
      924/925 rows now build cleanly (587 warnings, 1 residual unrelated phone-format error).
- [x] Blast-radius grep performed: confirmed no other caller of `build_mongo_driver_import_plan`/
      `commit_mongo_driver_import_plan` exists in the repo; confirmed the admin-dashboard drivers
      page already renders the relevant badges generically.
- [x] Read-only production query (Supabase MCP, `soavhtdhefowwvforzwb`) to size the follow-on
      existing-match finding — informational only, no write.
- [ ] Manual repro in staging — not applicable, this importer has no execution path wired up yet
      (CLI-only, dry-run by default).
- [x] Reviewed against relevant CLAUDE.md conventions: PIPEDA (report/print functions carry only
      `old_driver_id`/`field`/`message`, never names/phones — unchanged by this fix), the
      migration-doc "escalate, don't silently resolve" principle (the existing-match finding was
      explicitly *not* resolved here, only documented and flagged).
- [x] Feature-flag question: not applicable — no user-visible surface exists yet for this importer.

## 10. Sign-off

- [x] Rollback plan is concrete: no live data exists yet, so revert-the-commit is complete; the
      future-`--apply` rollback path (delete by batch id / source) is stated for when that changes.
- [x] Blast radius stated: isolated to one new, not-yet-wired-up code path; grepped, not assumed.
- [x] No silent behavior change to an already-shipped flow — this importer has never shipped or run
      against production, so there is no existing behavior to change; the one still-open finding
      (existing-match collision rate) is explicitly flagged as undecided, not silently resolved.
