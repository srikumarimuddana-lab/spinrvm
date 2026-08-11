# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude (automated, on behalf of vikas@ngitservices.com) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | P0-C, `docs/audit/2026-08-11-driver-rider-migration-audit.md` |

## 1. Issue / gap identified

The admin bulk rider-CSV importer matches CSV rows to existing `users` rows by
phone/email only, then repopulates any falsy PII field (email, is_rider,
stripe_customer_id) on the match. It never checks `users.status`, so a row
matching a `pending_deletion` or `deleted` account (PII already scrubbed by
migration 296's 30-day PIPEDA purge) gets its email silently refilled and
`is_rider` forced back to `True` — the practical equivalent of reactivating
the account, but with none of the real reactivation flow's OTP gate or audit
log.

## 2. Root cause

`rider_import_service._prefetch_existing()` never selected `status`, and
`build_plan()`'s update-field logic ("if falsy, fill it in") had no concept
of a protected/mid-deletion account. The falsy-is-safe-to-fill assumption is
correct for a genuinely incomplete profile but wrong for a field that is
falsy *because it was deliberately scrubbed*.

## 3. Fix / remediation

- `_prefetch_existing()` now selects `status` alongside the existing columns
  for both the phone-match and email-match queries.
- `build_plan()` checks the matched user's `status` against a new
  `_PII_PROTECTED_STATUSES = {"pending_deletion", "deleted"}` set. If matched,
  the row is skipped entirely (no `users_to_update` entry, no field touched),
  recorded as a `protected_skip` duplicate, and surfaced as a warning
  requiring manual admin review.
- `routes/admin/rider_import.py`'s report gains a `protected_skips` count.
- `admin-dashboard` types (`imports.ts`) and the Bulk Rider Import UI
  (`bulk-operations/page.tsx`) surface the new `protected_skip` match type
  with a distinct red "Skipped — needs review" badge, a dedicated "Needs
  review" stat tile, and a commit-summary clause — instead of silently
  falling into the "Existing rider" bucket.

## 4. Risk & impact on existing functionality

- Blast radius: isolated to `rider_import_service.py` / `routes/admin/rider_import.py`
  and their sole consumer, `admin-dashboard`'s Bulk Rider Import page
  (`bulk-operations/page.tsx` + `lib/api/imports.ts`). Grepped for all
  `match_type` readers/writers (see list above) — no other file reads or
  writes this field.
- `driver_import_service.py` is a separate module and is not touched by this
  change (it has its own provenance/status handling, out of scope here).
- No other code path reads `RiderImportPlan.duplicates` or the `/riders/import/*`
  endpoints.
- Behavior change is strictly narrowing (previously-silent writes are now
  skipped-and-flagged) — no previously-working import row becomes an error;
  `can_commit`/`errors` are untouched, so normal (non-protected) imports are
  unaffected.

## 5. User-experience effect

- Internal-admin-facing only (Bulk Rider Import page). Not visible to
  riders/drivers.
- Not a mid-session change — the importer is a manual, on-demand admin
  action, not a live/background flow.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/rider_import_service.py` | Added `_PII_PROTECTED_STATUSES`; `_prefetch_existing` now selects `status`; `build_plan` skips PII updates for protected-status matches | Prevent silent PII repopulation on PIPEDA-scrubbed accounts |
| `backend/routes/admin/rider_import.py` | Added `protected_skips` count to the report | Surface the new category to the admin UI |
| `backend/tests/test_admin_rider_import.py` | Added 2 tests: validate-skips-PII-update, commit-does-not-modify-PII | Regression coverage for the fix |
| `admin-dashboard/src/lib/api/imports.ts` | Widened `match_type` union to include `"protected_skip"`; added `protected_skips` to counts type | Keep frontend types honest about new backend response shape |
| `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx` | Distinct badge/stat/summary-copy for `protected_skip` rows | Make skipped-for-review rows visible to the admin instead of mislabeling them "Existing rider" |

## 7. Before / after

```python
# Before
existing_user = users_by_phone.get(phone)
...
if existing_user:
    ...
    update_fields: dict[str, Any] = {"id": existing_user["id"]}
    if email and not existing_user.get("email"):
        update_fields["email"] = email          # silently refills scrubbed PII
    if not existing_user.get("is_rider"):
        update_fields["is_rider"] = True         # silently "reactivates"
```

```python
# After
existing_status = existing_user.get("status")
if existing_status in _PII_PROTECTED_STATUSES:
    plan.duplicates.append({**dup_info, "match_type": "protected_skip"})
    plan.warnings.append(ImportReportItem(idx, "phone",
        f"SKIPPED — matched account status is '{existing_status}'; "
        "no fields were modified. Requires manual review before this "
        "row can be imported."))
    continue   # no update_fields built, no write happens
```

## 8. Rollback plan

Pure code change, no migration, no data mutation. Revert the commit (or the
PR) to restore the prior (unsafe) behavior — safe because this fix only
*prevents* writes that were previously happening silently; there is no data
to roll back, since protected-status rows are now skipped rather than
written.

## 9. Verification performed

- [x] Automated tests run — `pytest backend/tests/test_admin_rider_import.py -q --no-cov` → 17 passed (15 pre-existing + 2 new)
- [ ] Manual repro steps followed in staging — not available in this sandbox (no live Supabase access)
- [x] Blast-radius grep performed — searched all `match_type` read/write sites across `backend/` and `admin-dashboard/` (listed in §4)
- [x] Reviewed against relevant CLAUDE.md convention — PIPEDA §"User rights: Deletion", "Do not silently swallow errors" (this is the inverse: don't silently *perform* an unauthorized write)
- [ ] Feature-flagged — not applicable; this narrows existing behavior (skip instead of write) rather than adding new user-visible surface, and only an admin acting on stale CSVs against protected accounts would ever notice
- Frontend: `npx tsc --noEmit` clean; `npm run build` succeeded (full production build); `npx vitest run` — 1 pre-existing unrelated failure in `driver-statements-panel.test.tsx` (`lucide-react` mock gap), confirmed present on `origin/main` before this branch via `git stash` + re-run, not caused by this change

## What was NOT verified

- Not tested against a live Supabase instance — only the `mock_supabase_client`/in-memory `test_client` fixture used by `test_admin_rider_import.py`.
- No visual regression tooling exists in this repo for `admin-dashboard`; the new badge/stat colors were reasoned about (existing Tailwind token reuse, `Stat` component's documented `tone` prop) rather than screenshotted.
- P0-A (rider importer provenance) and P0-B (admin dashboard double-counting legacy earnings) from the same audit are separate, not addressed by this change.
