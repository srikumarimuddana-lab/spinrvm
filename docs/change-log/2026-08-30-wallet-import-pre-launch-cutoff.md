# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-30 |
| Author | Claude Code (session), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/migration-batch-readiness-wicr1d` (same PR as the `wallet_type` column fix) |
| Related issue or gap ID | Phase 6 of `docs/migration/2026-08-27-legacy-data-full-migration-approach.md` §4 |

## 1. Issue / gap identified

Owner-confirmed while this PR was open (not yet merged): **Spinr's public launch was
2026-03-30.** Any legacy wallet entry dated before that is from the previous app's
pre-launch build/test period, not a real customer or driver — never money actually
owed, and a candidate for cleanup rather than migration. The importer had no notion
of this cutoff: it would have credited any legacy wallet row that matched a real
Spinr account by phone, regardless of when the entry was created.

Checked against the real `wallets.csv` export's `created_at` values (all 13 rows,
converted from epoch-ms): **10 of the 13 rows are pre-launch** (2026-01-27 to
2026-03-02) — all 8 rider-owned rows (the entire ~$900 rider-credit figure) plus 2
of the 4 matched driver-owned rows (Tristan +$10, Kiran +$10, both dated
2026-03-02). Only 3 rows are genuinely post-launch (2026-05-22 to 2026-05-26):
Gurpreet's +$40, and Aakash Arora's +$40/−$40 pair that nets to exactly $0. Without
this fix, the importer would have credited Tristan and Kiran $10 each of test money
from the pre-launch period as if it were real.

## 2. Root cause

The importer's original design (built 2026-08-24, before this owner confirmation
existed) had no launch-date concept — it matched every legacy wallet row to a live
account by phone alone. That was a reasonable design against an assumed premise
("every legacy entry is a real historical balance"); the actual premise turned out
to be narrower once the launch date was confirmed.

## 3. Fix / remediation

- Added `LAUNCH_DATE = datetime(2026, 3, 30, tzinfo=timezone.utc)` to
  `services/wallet_import_service.py`.
- Added `created_at` to `REQUIRED_WALLET_COLUMNS` (previously unused by this
  importer) and a duplicated `parse_legacy_epoch_ms` helper (matching this repo's
  per-module small-helper convention, mirroring `driver_import_service`'s own
  version of the same parser).
- In `build_plan`'s per-row loop, `created_at` is parsed and checked against
  `LAUNCH_DATE` **before** account matching (so a pre-launch row never even reaches
  the "no matching account" bucket, and is reported distinctly). A row before the
  cutoff is skipped with a new `skipped_pre_launch` counter and a warning (not an
  error — expected, not a data-shape problem, so it never blocks commit of
  legitimate post-launch rows in the same file). The boundary is strict
  less-than: a row timestamped exactly at `2026-03-30T00:00:00Z` is kept.
- A missing/unparseable `created_at` is a hard error (refuses commit), matching
  this module's existing never-guess-at-money posture for `wallet_type`/`status`.
- Docstring corrected to state the confirmed launch date and the real post-launch
  total ($40, not the previously-stated ~$960).
- 4 new tests in `test_wallet_import_service.py`: pre-launch skip even when
  matched, exact-boundary is kept (not skipped), missing `created_at` is an error,
  and a pre-launch + post-launch row in the same CSV don't block each other.
- `WALLET_ROW`/`_wallet_row()` fixtures in both test files given a default
  post-launch `created_at` so every pre-existing test keeps exercising the
  matching/type/status logic it was written for, undisturbed by the new gate.

**Verification against the real export:** re-ran the same `build_plan` simulation
used in the column-name fix, with this change applied. Result: 10 rows skip as
pre-launch (all 8 rider rows + Tristan + Kiran), 3 rows apply (Gurpreet +$40, Aakash
+$40/−$40 netting to $0) — **net $40**, all to one driver (Gurpreet). Full output
captured in this session's transcript.

## 4. Risk & impact on existing functionality

- **Blast radius: same as the column-name fix** — isolated to this one importer,
  one caller (`routes/admin/wallet_import.py`).
- **This narrows what the tool will do, not widens it.** Before this fix, a commit
  run would have credited $60 across 3 drivers (including $20 of pre-launch test
  money to Tristan and Kiran); after, it credits $40 to one driver (Gurpreet) only.
  Strictly more conservative — nothing this fix newly permits could have been
  wrongly credited by the pre-fix version.
- **No production write has happened with either version** — this narrows the
  scope of an already-unrun tool, so there is no prior "wrong" credit to reconcile
  or reverse.
- **The rider portion needed no separate exclusion logic** — all 8 rider rows were
  already excluded as unmatched (none of the 3 rider `customer_id`s resolve to a
  `customers.csv` row at all, confirmed in the prior fix's dry run). This change
  makes that exclusion doubly certain (pre-launch date, independent of the
  unrelated unmatched-account finding) and gives it an honest, permanent reason
  rather than relying on an accident of missing crosswalk data.

## 5. User-experience effect

None visible yet — same as the column-name fix, this only changes what an
already-live, not-yet-run internal admin tool will do once an operator runs it.
Gurpreet Singh will see a $40 wallet credit on next app load once run; Tristan and
Kiran will not be credited anything by this tool (their pre-launch test entries are
excluded, per the owner's cleanup guidance).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/wallet_import_service.py` | `LAUNCH_DATE` constant, `created_at` required column, `parse_legacy_epoch_ms` helper, pre-launch skip in `build_plan`, `skipped_pre_launch` stat + report line, docstring corrected | Enforce the owner-confirmed 2026-03-30 launch cutoff |
| `backend/tests/test_wallet_import_service.py` | 4 new tests; `_wallet_row()` fixture given a default post-launch `created_at` | Lock in the cutoff behavior; keep existing tests exercising what they were written for |
| `backend/tests/test_admin_wallet_import.py` | `WALLET_ROW` fixture given a default post-launch `created_at` | Match the now-required `created_at` column |

## 7. Before / after

```python
# Before
seen_old_ids.add(old_id)

cust_id = (r.get("customer_id") or "").strip()
drv_id = (r.get("driver_id") or "").strip()
```

```python
# After
seen_old_ids.add(old_id)

created_at = parse_legacy_epoch_ms(r.get("created_at", ""))
if created_at is None:
    plan.errors.append(ImportReportItem(idx, old_id, "created_at", "missing or unparseable created_at"))
    continue
if created_at < LAUNCH_DATE:
    plan.warnings.append(
        ImportReportItem(idx, old_id, "created_at", "pre-launch (before 2026-03-30); skipped as test data")
    )
    skipped_pre_launch += 1
    continue

cust_id = (r.get("customer_id") or "").strip()
drv_id = (r.get("driver_id") or "").strip()
```

## 8. Rollback plan

`git revert` — pure logic addition, no data migration, no schema change. Nothing
has been written to production by this tool yet.

## 9. Verification performed

- [x] `pytest tests/test_wallet_import_service.py tests/test_admin_wallet_import.py`
  — 39 passed (4 new), including the pre-launch cutoff tests.
- [x] `ruff check` / `ruff format --check` on all 3 touched Python files — clean.
- [x] Re-ran the real-export dry-run simulation (same method as the column-name
  fix's verification) with this change applied: 10/13 rows skip pre-launch, 3
  apply, net $40 to one driver. Matches the owner-confirmed cutoff exactly.
- [x] Blast-radius unchanged from the column-name fix (already confirmed isolated).

## What was NOT verified

- The actual production commit has still not been run — this remains the
  operator's action via Preview → Apply.
- This fix addresses only the wallet importer. The owner's broader guidance ("we
  should consider these for cleanup before go live") raises a wider question about
  every other legacy importer used earlier in this migration effort (rider/driver
  CSV import, SIN/DOB backfill, vehicle-history backfill, saved-address backfill,
  insurance-period corrections) — whether any already-migrated records trace back
  to pre-launch legacy timestamps and should be flagged for cleanup. That
  investigation is being done as a separate, explicit follow-up in this session and
  is not covered by this change.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`; no production data
  written yet)
- [x] Blast radius is stated, not assumed (same isolated caller as the column-name
  fix)
- [x] No silent behavior change to any existing flow — the tool was already
  not-yet-run against real data; this only narrows what it will credit once it is
