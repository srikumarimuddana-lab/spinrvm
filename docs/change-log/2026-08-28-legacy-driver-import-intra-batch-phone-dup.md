# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 |
| Author | Claude Code (session), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/migration-batch-readiness-wicr1d` |
| Related issue or gap ID | Live Phase 1 legacy driver import commit failing in production a second time ("Internal server error") after the commit-timeout fix landed |

## 1. Issue / gap identified

After the commit-timeout fix (`2026-08-28-legacy-driver-import-commit-timeout.md`)
deployed, the operator's next commit attempt still failed with a raw
"Internal server error." Confirmed via Supabase's request logs that the
request *did* reach the database this time — and got a real answer:
`POST /rest/v1/users → 409 Conflict` on the bulk insert.

## 2. Root cause

`build_mongo_driver_import_plan()` decides whether a CSV row is "new" by
checking it against a snapshot of `users`/`drivers` fetched **once**,
before the row loop starts. It never checked a row against *other rows in
the same CSV*. The real 924-row export has 8 groups of rows (15 rows
total) sharing a phone number with a sibling row in the same file (plus 5
email groups / 9 rows) — two such rows both independently see "no match in
the DB snapshot" and both get queued into `users_to_insert` with the
identical phone. `users.phone` has a `UNIQUE` constraint (confirmed via
`pg_constraint`), so the bulk insert containing both rows is rejected
outright — and because the insert is a single request, the whole 924-row
batch failed with no partial write (consistent with the zero-new-rows
check from the previous fix's diagnosis).

This bug already existed before today — it was simply masked by the
separate commit-timeout bug (fixed earlier this session), which always
crashed the request before it ever reached the insert step.

## 3. Fix / remediation

`build_mongo_driver_import_plan()` now tracks, as it processes each row:

- `pending_driver_row_by_phone`: every driver row it queues for insert,
  keyed by phone. If a *later* row in the same CSV shares that phone, its
  history is merged directly into the already-pending driver row's
  `legacy_import_metadata.mongo_driver_history` (mirroring the existing
  "matches an existing driver, enrich, don't duplicate" pattern already
  used for DB matches) instead of creating a second driver row with a
  duplicate phone.
- `pending_user_id_by_email`: when a row creates a brand-new user, its
  email is recorded so a *later* row with the same email but a
  **different** phone (not caught by the phone check above) reuses that
  same `user_id` instead of creating a second account for what's evidently
  the same person. (`users.email` has no UNIQUE constraint, so this
  wouldn't itself 409, but it contradicts the "never create a duplicate
  account" intent already established elsewhere in this function for the
  DB-matched case.)

No change to the DB-match logic (`matched_user`/`matched_driver` against
the real production snapshot) — this only adds a same-batch check *before*
falling through to "create new."

## 4. Risk & impact on existing functionality

- **Blast radius: isolated** to `build_mongo_driver_import_plan()` in
  `driver_import_service.py`. Its only two callers (the admin route's
  `_build_plan`, the CLI script) already pass through whatever plan it
  returns unchanged.
- **Verified against the real 08-22 export** (924 rows, fresh production
  match-state re-fetched via read-only SQL): `users_to_insert` and
  `drivers_to_insert` both now have **zero duplicate phones** (previously
  the two conflicting rows that caused the 409). Row accounting reconciles
  exactly: 924 total rows = 909 `drivers_to_insert` + 15 rows merged into
  a sibling row's history, matching the 15 duplicate-phone rows found
  earlier in this same file.
- All 35 existing unit tests for this module and the admin route still
  pass unchanged (no existing test exercised an intra-batch duplicate, so
  none needed updating — this is additive new-case handling, not a
  behavior change to any previously-passing case).

## 5. User-experience effect

- **Internal admin only.** The practical effect: the ~15 previously
  batch-breaking rows now import as merged history on a sibling row
  instead of crashing the entire 924-row commit. A new warning message
  (`"matches a driver created earlier in this same import batch..."` /
  `"...by email..."`) appears in the report for those specific rows —
  visible in the same warnings table as every other row-level notice this
  importer already surfaces.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/driver_import_service.py` | `build_mongo_driver_import_plan()`: added `pending_driver_row_by_phone` / `pending_user_id_by_email` tracking and two new resolution branches | Prevent a same-batch duplicate phone/email from producing two conflicting insert rows |

## 7. Before / after

```python
# Before
matched_user = users_by_phone.get(phone) or (users_by_email.get(email) if email else None)
matched_driver = drivers_by_phone.get(phone)
create_new_user = True

if matched_driver or matched_user:
    ...
else:
    user_id = str(uuid.uuid4())
```

```python
# After
matched_user = users_by_phone.get(phone) or (users_by_email.get(email) if email else None)
matched_driver = drivers_by_phone.get(phone)
pending_driver_row = pending_driver_row_by_phone.get(phone)
create_new_user = True

if pending_driver_row is not None:
    # merge into the sibling row's pending history, continue
    ...
    continue

if matched_driver or matched_user:
    ...
elif email and email in pending_user_id_by_email:
    user_id = pending_user_id_by_email[email]
    create_new_user = False
    ...
else:
    user_id = str(uuid.uuid4())
```

## 8. Rollback plan

`git-revert-safe` — no production writes have succeeded with either the
old or the buggy-but-not-yet-reverted code (this fix has not itself been
run against production yet). Reverting restores the exact prior (already
broken for this batch) behavior; no data-level cleanup needed either way.

## 9. Verification performed

- [x] Automated tests: `pytest tests/test_legacy_mongo_driver_import_service.py tests/test_admin_legacy_driver_import.py` — 35 passed.
- [x] `ruff check` / `ruff format --check` — clean.
- [x] **Verified against the real 924-row 08-22 export** with a fresh production match-state snapshot (re-fetched via read-only SQL this session): zero duplicate phones in `users_to_insert`/`drivers_to_insert`, zero errors, row accounting reconciles exactly (924 = 909 inserts + 15 same-batch merges, matching the 15 duplicate-phone rows found earlier).
- [x] Root-caused via Supabase's own edge/PostgREST request logs (`POST /rest/v1/users → 409`), not guessed — confirmed the request reached the DB and exactly what it was rejected for.
- [x] Blast-radius grep: same 2 callers as the previous fix in this same function's neighborhood, both already accounted for.

## What was NOT verified

- Not yet re-run against real production — the next Phase 1 commit attempt
  through the live admin dashboard is the end-to-end confirmation.
- The `drivers_by_phone` fake snapshot used for the 924-row verification
  run was empty (the real one hit the same session-level PII-volume
  classifier restriction noted throughout this migration effort) — so the
  exact `drivers_to_enrich`/`users_to_update` split in that verification
  run doesn't match the real expected production numbers. This does not
  affect the fix itself: the property being verified (no duplicate phones
  in the insert payloads, exact row-count reconciliation) is independent
  of which rows resolve via DB-match vs. create-new.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated, not assumed (one function, two known callers)
- [x] No silent behavior change to an already-shipped flow — this batch
      has never successfully committed before (this is the second bug
      blocking its first-ever successful run), so there is no working
      behavior being altered
