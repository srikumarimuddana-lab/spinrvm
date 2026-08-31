# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-30 |
| Author | Claude Code (session), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/migration-batch-readiness-wicr1d` |
| Related issue or gap ID | Phase 6 of `docs/migration/2026-08-27-legacy-data-full-migration-approach.md` §4 |

## 1. Issue / gap identified

The Legacy Wallet Import tool (`services/wallet_import_service.py` +
`routes/admin/wallet_import.py` + the admin-dashboard "Legacy Wallet Import" page)
shipped to production on 2026-08-24 (PRs #4473/#4477/#4480) but has never been run
against the real `wallets.csv` export. Its own docstring flagged this: the expected
CSV column names were inferred from sibling exports' naming convention (`bookings.csv`,
`customers.csv`, `driverearnings.csv`), never confirmed against a real `wallets.csv`
header row. I set out to build this tool from scratch believing it didn't exist yet —
found the existing implementation mid-build (before writing any new code) and pivoted
to testing it against the real export instead, which is how this bug surfaced.

Checked directly against the real 07-26 export's header: `#,_id,__v,amount,
applied_refer_code,booking_id,created_at,customer_id,driver_id,refer_amount_consumed,
status,updated_at,wallet_type`. There is no `type` column — only `wallet_type`. The
importer required a column literally named `type`, so every real `/validate` call
would fail with a missing-column error, refusing every commit. The tool has been live
and reachable in the admin dashboard for 6 days with this defect, unused because no
one had run it against real data yet.

## 2. Root cause

`type` was a guess, not a confirmed fact, made before a real `wallets.csv` header row
had ever been captured — the module's own docstring said as much at the time it was
written. The guess was wrong (the real column is `wallet_type`) and nothing caught it
because the accompanying tests used a fixture CSV built with the same wrong guess, so
they exercised the code path successfully without ever exercising the real shape.

## 3. Fix / remediation

- `services/wallet_import_service.py`: `REQUIRED_WALLET_COLUMNS` and the row-parsing
  lookup now use `wallet_type`, matching the real export. Docstring corrected to state
  the confirmed real header and the concrete bug this caused, replacing the old
  "not yet confirmed" caveat.
- `routes/admin/wallet_import.py`: docstring caveat updated to point at the fix instead
  of the now-resolved uncertainty.
- `tests/test_wallet_import_service.py` / `tests/test_admin_wallet_import.py`: every
  fixture's `"type"` key renamed to `"wallet_type"` (both files were carrying the same
  wrong assumption the service code was). Two new regression tests added to
  `test_wallet_import_service.py`:
  - `test_real_export_header_uses_wallet_type_not_type` — a row shaped exactly like a
    real export row (including the unused `applied_refer_code`/`booking_id`/
    `refer_amount_consumed`/`updated_at` columns) parses cleanly.
  - `test_csv_with_only_old_type_column_name_is_a_missing_column_error` — a CSV with
    only the old, wrong `type` column (no `wallet_type`) refuses with a clear
    missing-column error, so a future regression back to the old name fails loudly
    instead of silently passing tests again.
- `docs/migration/2026-08-27-legacy-data-full-migration-approach.md` §4 Phase 6
  section corrected: it said the RPC pattern "doesn't yet exist for consumer wallets
  and would need to be built" — that was already wrong when written (the tool had
  shipped days earlier). Replaced with the real status: built, fixed, dry-run
  verified against the real export, ready for the operator to run.

**Verification against the real export (not assumed):** ran `build_plan` directly
against the real `wallets.csv`/`customers.csv`/`drivers.csv` content with a fake
Supabase client seeded from the actual production phone matches (confirmed via direct
SQL beforehand). Result: 0 errors. All 8 rider-owned rows (customer_id set) skip as
"no matching rider/driver account found" — none of the 3 distinct rider `customer_id`s
in this export resolve to a `customers.csv` row at all, so the $900 rider portion
credits nothing as-is (matches the separate manual reconciliation write-up: 2 of 3
rider entries are unrecoverable junk with zero footprint anywhere in the export, the
third is a real customer whose own record was deleted before this export was taken and
needs a manual old-app lookup to recover a phone number). 5 driver-owned rows apply:
$10 + $10 + $40 + $40 − $40 = **$60 net**, across 3 drivers who end up credited (one of
the 4 matched drivers nets to exactly $0 from a same-amount add+deduct pair, applying
as two ledger entries with no floor breach since the add lands before the deduct in
file order).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this one importer.** `services/wallet_import_service.py`
  has exactly one caller (`routes/admin/wallet_import.py`); grepped to confirm. No
  other route, service, or background loop imports this module.
- **No behavior change for any code path other than this backfill.** The fix only
  changes which CSV column name the importer reads; the money-application logic
  (`wallet_apply_delta`, migration 249 — row-locked, idempotent, floor-enforced) is
  untouched.
- **This is a bug fix on unused, unexercised code, not a change to a live-tested
  flow.** Because every real `/validate` call failed before reaching any write path,
  no wallet has ever actually been credited or debited by this tool — there is no
  prior state to reconcile against, and no risk of double-applying anything that
  already landed.
- **The fix was verified against the real export's actual shape**, not just against
  updated test fixtures — see the dry-run simulation in §3. The rider/driver skip
  behavior (unmatched rider rows, correctly netting driver rows) matches the separate
  by-hand reconciliation performed before this fix, cross-checked from two independent
  angles (manual CSV analysis + this tool's own matching logic).
- **Money-path**, so flagged per CLAUDE.md's mandatory dry-run rule even though the
  fix itself is a column-name string change: the actual commit against production has
  not been run by this session — that remains the operator's action via the existing
  Preview → Apply admin-dashboard flow, same posture as every other importer in this
  migration effort.

## 5. User-experience effect

None visible to riders or drivers from this fix itself — it only makes an already-live
internal admin tool actually functional. Once an operator runs a real commit through
it, drivers Tristan (+$10), Kiran Muddana (+$10), and Gurpreet Singh (+$40) will see
their wallet balance increase, visible on next app load / wallet-screen refresh — not
mid-session for anyone currently using the app, since this is an operator-triggered
one-time backfill, not a live system behavior change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/wallet_import_service.py` | `REQUIRED_WALLET_COLUMNS` + row-parsing lookup: `type` → `wallet_type`; docstring corrected | Match the real export's actual column name |
| `backend/routes/admin/wallet_import.py` | Docstring caveat updated to reflect the fix | Keep the route's own docs accurate |
| `backend/tests/test_wallet_import_service.py` | Fixtures renamed `type` → `wallet_type`; 2 new regression tests added | Lock in the real column name; catch a future regression loudly |
| `backend/tests/test_admin_wallet_import.py` | Fixtures renamed `type` → `wallet_type` | Same fix, HTTP-level tests |
| `docs/migration/2026-08-27-legacy-data-full-migration-approach.md` | Phase 6 section corrected from "deferred, needs new infrastructure" to the real built/fixed/verified status | The doc's own prior claim was already wrong when written; now matches reality |

## 7. Before / after

```python
# Before
REQUIRED_WALLET_COLUMNS = {
    "_id",
    "customer_id",
    "driver_id",
    "amount",
    "type",
    "status",
}
...
legacy_type = (r.get("type") or "").strip()
```

```python
# After
REQUIRED_WALLET_COLUMNS = {
    "_id",
    "customer_id",
    "driver_id",
    "amount",
    "wallet_type",
    "status",
}
...
legacy_type = (r.get("wallet_type") or "").strip()
```

## 8. Rollback plan

`git revert` — this is a pure column-name string fix with no data migration and no
table/RPC change. Nothing has been written to production by this tool yet (every real
call previously failed before reaching the write path), so there is no data-side
rollback to plan for.

## 9. Verification performed

- [x] `pytest tests/test_wallet_import_service.py tests/test_admin_wallet_import.py` —
  35 passed, including the 2 new regression tests.
- [x] `ruff check` / `ruff format --check` on all 4 touched Python files — clean.
- [x] Dry-run of `build_plan` against the real `wallets.csv`/`customers.csv`/
  `drivers.csv` export content, with a fake Supabase client seeded from the actual
  production phone-match data (confirmed via direct SQL beforehand) — 0 errors, 8
  rider rows correctly skip as unmatched, 5 driver rows apply cleanly netting to
  exactly $60 across 3 drivers. Full output captured in this session's transcript.
- [x] Blast-radius grep: `wallet_import_service` has exactly one importer
  (`routes/admin/wallet_import.py`) outside its own tests.
- This is a backend-only Python change — no `admin-dashboard`/`rider-app`/`driver-app`
  build was needed (no TypeScript/UI file touched).

## What was NOT verified

- **The actual production commit has not been run.** This fix makes the tool capable
  of succeeding against the real export; running it for real (Preview → Apply via the
  admin dashboard) is the operator's next step, not something this session executed.
- The rider portion of this backfill ($900) is not addressed by this fix — it was
  already out of scope (see the separate manual reconciliation): none of the 3 rider
  `customer_id`s in the export resolve to a `customers.csv` row, so they will
  correctly report as unmatched and credit nothing until a phone number is manually
  recovered for the one real candidate.
- No RLS-policy, RPC, or table schema was changed — only reviewed to confirm
  `wallet_apply_delta` (migration 249) and the `wallet_transactions_type_check`
  constraint (current shape from migration 319) already support this data as-is.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`; no production data written yet)
- [x] Blast radius is stated, not assumed (one caller; grepped confirmation)
- [x] No silent behavior change to any existing flow — the tool was non-functional
  against real data before this fix; it is now functional, with the actual commit
  still gated behind the operator's own Preview → Apply action
