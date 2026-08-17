# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-17 |
| Author | Claude (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | Follow-up to `docs/change-log/2026-08-15-legacy-payout-correction-plan.md` §3a and `docs/change-log/2026-08-16-gst-backfill-and-stripe-crosscheck.md` §1a |

## 1. Issue / gap identified

`docs/change-log/2026-08-15-legacy-payout-correction-plan.md` (PR #3946, merged) built a **dry-run-only** plan builder identifying 15 real Spinr drivers owed real, unpaid legacy-app earnings the old app itself recorded as `pending_amount_status='due'`. No write path existed — the debt was documented but nothing could act on it. Deliberately deferred pending an explicit go-ahead (money-movement decisions are not something to bundle into an analysis PR).

## 2. Root cause

Two prerequisites were missing before any write path could be built responsibly:
1. **No design decision on the balance/statement/T4A treatment** of a new payout type that is real money but doesn't correspond to any ride in this DB (the original plan doc explicitly flagged this as an open question, see its own §3a correction).
2. **No resolution of the Stripe cross-check's structural finding**: 10 of the 15 owed drivers have no Stripe Connect account on file at all (never onboarded to Stripe payouts), and 3 of the 15 are flagged ambiguous/likely-already-paid by a live Stripe-ledger cross-check — neither of which the original dry-run plan resolved into an actionable set.

## 3. Fix / remediation

Built the write path, scoped by three decisions the user made explicitly before any code was written (`AskUserQuestion`, this session):
- Drivers with no Stripe account: **held** (`status='awaiting_stripe_onboarding'`), not skipped — the debt is durably recorded now; `fire_ready_transfers` picks it up automatically once/if the account appears, no re-run needed.
- The 3 Stripe-cross-check-flagged buckets: **excluded entirely** — a human follow-up, not paid, not held.
- **No live Stripe execution this session** — `fire_ready_transfers` exists, is fully mocked-Stripe tested, but has never been invoked against production. `commit_write_plan` only ever writes `payouts` rows; the Stripe-Transfer step is a separate, explicit call.

### 3a. Where the full driver IDs and classifications came from (2026-08-17 live re-verification)

The original plan doc and the 2026-08-16 crosscheck doc only ever recorded **truncated** driver-id prefixes (`a569909e-c866-…`, etc. — never full UUIDs). Rather than guess or re-derive the "likely paid" / "ambiguous" classification with a new heuristic (the crosscheck doc's own words: *"a 'confirmed' verdict is not reachable for any bucket with this schema; 'likely' is the ceiling"* — an explicitly human judgment call), this session re-ran the exact filter chain live:

1. Re-ran `find_due_unpaid_rows` against the original CSVs (still present in this session's scratchpad — PII, never committed) — reproduced the same 35 kept rows / 108 unresolved, byte-for-byte matching the original dry run.
2. Queried production `rides` for `legacy_import_metadata->>'old_booking_id'` against all 27 Group-A `old_booking_id`s — resolved full `driver_id` UUIDs for all 15 real-driver buckets, confirmed they match the truncated prefixes recorded in both prior docs exactly (`a569909e-c866-4c5f-894e-e60489ca3593`, `350b5267-7a4d-4548-bf4e-770ee22cb416`, `93a899d5-431b-4743-afac-034cdf8c3d6c` for the 3 excluded buckets).
3. Queried `drivers.stripe_account_id` for the remaining 12 (15 minus the 3 excluded) — confirmed 2 have an account on file (`efa8c93d…`, `e2f16d17…`, $29.97 combined) and 10 do not ($155.34 combined). Sum of both = **$185.31**, matching the crosscheck doc's "genuinely-owed" figure exactly — full reconciliation, not an approximation.
4. Kept the 3 resolved UUIDs as a named, dated constant (`STRIPE_CROSSCHECK_EXCLUDED_DRIVER_IDS`) rather than building an automated matcher — same posture as migration 317's one named legacy exception (`audit_logs_no_update`): an explicit, reviewed, dated constant beats a clever re-derivation of a fuzzy human judgment call.

All 3 queries were `SELECT`-only against production Supabase (`soavhtdhefowwvforzwb`) — no writes performed during verification.

### 3b. Balance/statement/T4A design decision (supersedes the original plan doc's tentative suggestion)

The original plan doc's §3a guessed the write-path PR would "most likely" extend `is_legacy_offset_payout()` to fully drop the correction type everywhere `legacy_import` is dropped (statements included). **This session found that would be wrong** and did not do it: `legacy_import` is a synthetic $0-net offset that never represented real money, so hiding it everywhere is correct. `legacy_outstanding_correction` is a **real Stripe Transfer** — hiding it from a driver's own statement or from CRA T4A reporting would be worse than the original bug (silently underpaying tax-relevant income visibility). Instead, it is treated exactly like `stripe_sync` everywhere a driver or the CRA sees it — visible, labeled, included in previous-app-paid/legacy-income totals — but with one addition `stripe_sync` never needed: **explicit `status == 'completed'` gating**, because unlike `stripe_sync` (always `'completed'` by construction — it only ever materializes an already-settled transfer), a correction row starts `'awaiting_stripe_onboarding'` or `'ready_for_transfer'` and is not yet real money.

## 4. Risk & impact on existing functionality

**Blast radius: single-surface (backend), touching 5 existing money-adjacent read paths plus 1 new write-only module.**

- `backend/services/legacy_payout_correction_service.py` — new `build_write_plan`/`commit_write_plan`/`fire_ready_transfers`. Not imported or called from any route, CLI entry point, or background loop — grepped, zero other callers. `fire_ready_transfers` is the only function in this diff that can touch Stripe; it has never been invoked.
- `backend/routes/drivers/earnings.py` (`get_driver_balance`) — the single source of truth for a driver's live balance screen. Grepped for other callers: only the driver-app `/drivers/balance` route and `test_drivers_extended.py`/`test_previous_app_sunset.py`/`test_earnings_coverage.py`. The new exclusion set (`_not_balance_affecting_types`) is a strict superset of the old single-string check — `stripe_sync`'s existing behavior is unchanged, byte-identical, verified by the pre-existing tests still passing unmodified.
- `backend/utils/driver_statement.py` (`build_statement`) — used by the statement PDF/email job and the on-demand statement endpoint. Grepped: `driver_statement_pdf.py`, `driver_statement_job.py`, `driver_statement_email.py` all consume this function's dict output unchanged — no key removed, only `payouts_previous_app_total`'s composition widened and one new label added.
- `backend/routes/drivers/tax_exports.py` / `backend/utils/t4a_annual_job.py` — CRA-facing T4A slip + annual $500-threshold eligibility job. Grepped for other callers: only the driver-facing `/tax/*` routes and the 6h-cadence `t4a_annual_job` background loop (unaffected wiring — only its internal `_driver_annual_earnings` query changed). Both must stay consistent with each other (explicitly documented in both files' docstrings) — verified by mirrored test assertions in both `test_p2_payout_t4a.py` and `test_t4a_annual_job.py`.
- `backend/routes/admin/drivers.py` (`admin_get_driver_payouts_summary`) — internal-admin-only, read-only display endpoint. Grepped: no other route or service reads `balance_money_out`/`legacy_stripe_transfers` from this function.
- **No ride state machine, wallet, or Stripe-webhook interaction.** No migration — `payout_type` is a free-text column already used by 4+ other values (`standard`, `instant`, `legacy_import`, `stripe_sync`, `auto`), so a new string value needs no schema change.
- **A real money-safety property added, not just parity**: the `status == 'completed'` gate is a NEW correctness requirement that `stripe_sync` never needed (it's always `'completed'`). Missing this gate at any of the 5 call sites would have shown a driver, an operator, or the CRA money that had not actually moved yet — checked explicitly at every site via dedicated tests (see §9).

## 5. User-experience effect

- **Driver-facing**: none yet — no `legacy_outstanding_correction` row exists in production (this PR never calls Stripe or writes a row). Once real rows are committed and settled, a driver's balance screen's "Previously Paid" figure and their payout-history statement will show the amount with the label "Previous app payout (outstanding correction)" — additive, not a behavior change to any existing screen state.
- **Admin-facing**: same — the new "of which" breakdown (`legacy_stripe_transfers`) will include settled correction amounts once any exist; no visible change today.
- **Not visible mid-session to anyone** — this PR ships code with zero live callers.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/legacy_payout_correction_service.py` | Added `build_write_plan`/`commit_write_plan`/`fire_ready_transfers`/`print_write_report`, `PAYOUT_TYPE`, `STRIPE_CROSSCHECK_EXCLUDED_DRIVER_IDS` (3 named UUIDs, live-re-verified 2026-08-17) | The write path itself |
| `backend/routes/drivers/earnings.py` | `total_payouts` excludes `legacy_outstanding_correction` (any status); `previous_app_paid` includes it only when `status='completed'` | Balance math must never be perturbed by an unsettled or settled correction row |
| `backend/utils/driver_statement.py` | New `_PAYOUT_TYPE_LABELS` entry; `payouts_previous_app`/`payouts_total` include it only when `status='completed'` | Real money must stay visible to the driver, not silently dropped like `legacy_import` |
| `backend/routes/drivers/tax_exports.py` | Both T4A endpoints' `payouts` query widened to `payout_type: {"$in": [...]}` + explicit `status: "completed"` | CRA reporting must include settled correction income, never an unsettled promise |
| `backend/utils/t4a_annual_job.py` | Same widening in `_driver_annual_earnings` | Keep the $500 annual-eligibility job consistent with the driver-facing slip (explicitly required by both files' own docstrings) |
| `backend/routes/admin/drivers.py` | `_is_money_out`/`balance_money_out`/`legacy_stripe_transfers` all gain the same type + status handling | Admin summary must match the driver-facing balance and history exactly |
| `backend/tests/test_legacy_payout_correction_service.py` | 13 new tests for the write path | Regression coverage for build/commit/fire and the exclusion list |
| `backend/tests/test_drivers_extended.py`, `test_previous_app_sunset.py`, `test_driver_statement.py`, `test_p2_payout_t4a.py`, `test_t4a_annual_job.py`, `test_admin_drivers_coverage.py` | New tests + 2 pre-existing assertions updated for the widened query shape | One dedicated test per call site for the status-gating behavior |

## 7. Before / after

```python
# Before (backend/routes/drivers/earnings.py, total_payouts)
total_payouts = sum(
    (
        _d(p.get("amount") or 0)
        for p in payout_rows
        if str(p.get("status") or "").lower() not in _not_money_out and p.get("payout_type") != "stripe_sync"
    ),
    Decimal("0"),
)
```

```python
# After
_not_balance_affecting_types = {"stripe_sync", "legacy_outstanding_correction"}
total_payouts = sum(
    (
        _d(p.get("amount") or 0)
        for p in payout_rows
        if str(p.get("status") or "").lower() not in _not_money_out
        and p.get("payout_type") not in _not_balance_affecting_types
    ),
    Decimal("0"),
)
```

```python
# Before (backend/routes/drivers/tax_exports.py, T4A payouts query)
{"driver_id": driver["id"], "payout_type": "stripe_sync", "created_at": {...}}
```

```python
# After
{
    "driver_id": driver["id"],
    "payout_type": {"$in": ["stripe_sync", "legacy_outstanding_correction"]},
    "status": "completed",
    "created_at": {...},
}
```

## 8. Rollback plan

`git-revert-safe`. No migration, no schema change, no data written by anything in this diff (`fire_ready_transfers` — the only function that can touch Stripe or write `payouts` rows for real — has never been called). A `git revert` of this entire commit removes the write path and the 5 downstream call-site changes atomically; since no `legacy_outstanding_correction` row exists in production yet, there is nothing to reconcile or clean up.

Once this code is later actually invoked for real (a separate, explicit future action, not part of this PR): `commit_write_plan`'s rows are deterministic-ID and idempotent (safe to re-run); `fire_ready_transfers`'s Stripe calls are idempotency-keyed per row (safe to retry). Reverting the code AFTER real Stripe Transfers have fired would NOT undo those transfers — that's an accepted, explicitly-stated limit consistent with every other Stripe-Transfer-issuing code path in this repo (e.g. `routes/drivers/payouts.py`'s instant payout).

## 9. Verification performed

- [x] Unit tests: `pytest backend/tests/test_legacy_payout_correction_service.py backend/tests/test_drivers_extended.py backend/tests/test_previous_app_sunset.py backend/tests/test_earnings_coverage.py backend/tests/test_earnings_snapshot.py backend/tests/test_driver_statement.py backend/tests/test_driver_statement_pdf.py backend/tests/test_p2_payout_t4a.py backend/tests/test_t4a_annual_job.py backend/tests/test_tax_exports_app_name.py backend/tests/test_t4a_pdf_coverage.py backend/tests/test_t4a_email.py backend/tests/test_admin_drivers_coverage.py -q --no-cov` — 387/387 pass.
- [x] `ruff check` + `ruff format --check` on every touched file — clean (4 pre-existing `B904` findings in `routes/admin/drivers.py`, confirmed via `git stash` to predate this diff, far from any touched line, not fixed here per this repo's "don't drive-by-fix unrelated findings" convention).
- [x] Live re-verification against production Supabase (read-only `SELECT`s, `soavhtdhefowwvforzwb`) of all 15 driver UUIDs, the 3 exclusions, and Stripe-account presence for the remaining 12 — see §3a. Reconciled to the exact $185.31 figure from the 2026-08-16 crosscheck doc, not an approximation.
- [x] Blast-radius grep performed for every touched function's other callers — see §4.
- [ ] Not run: a real Stripe test-mode Transfer through `fire_ready_transfers`. Deliberately not exercised against even Stripe test mode in this session — the function is fully covered by mocked-Stripe unit tests (success, account-disappeared fallback, Stripe-error paths), and per the user's explicit decision this session builds/tests only, no live execution.
- [ ] Not a `rider-app`/`driver-app`/`admin-dashboard` change — no `npm run build` applicable.

## What was NOT verified

- **No real Stripe call, test-mode or live, was made** — `fire_ready_transfers` is unit-tested against a fully mocked `stripe` module only. The real Stripe Connect Transfer API surface (rate limits, real error shapes, actual idempotency-key collision behavior) has not been exercised.
- **The 10 held (no-Stripe-account) drivers' eventual onboarding is not tracked by any code in this diff** — `fire_ready_transfers` re-queries `status='ready_for_transfer'` fresh each call, so a held row only becomes payable once something else (this driver completing Stripe Connect onboarding) changes `drivers.stripe_account_id`; there is no notification or nudge wired up to alert anyone when that happens. Worth a follow-up ACTION_ITEMS.md entry if the go-ahead to actually run this is given later.
- **Whether any of the 15 drivers' Spinr accounts have since been closed/deactivated** — not re-checked in this pass (the original 2026-08-15 plan doc flagged this as unverified too); would need re-verification immediately before any real execution.
- **No visual/UI verification** — this PR is backend-only; the new statement label and admin "of which" breakdown have no rendering surface change to screenshot (not applicable, not a gap).
