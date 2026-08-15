# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-15 |
| Author | Claude (automated, on behalf of vikas@ngitservices.com) |
| Surface(s) | backend (new dry-run service + test only — **no live write path exists yet**) |
| Domain (Sentry tag) | payments |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | Follow-up to `docs/audit/2026-08-14-*` legacy-migration audits |

## 1. Issue / gap identified

`payments.csv` from the previous app records 35 real (Canadian, resolvable-to-a-real-booking) driver-payout rows with `pending_amount_status == 'due'` — money the old app itself says was never paid to the driver. `backend/services/booking_import_service.py`'s existing import pipeline assumes **every** imported ride was already settled in the old app and creates a full offsetting `payouts` row per driver accordingly. That assumption is false for these 35 rows.

## 2. Root cause

The importer's offsetting-payout logic (`build_plan()`) reads only `driverearnings.csv` — it has no awareness of `payments.csv`'s `pending_amount_status`/`payout_initiated` fields at all (confirmed by grep: zero references to `payments.csv` anywhere in `backend/`). Two separate legacy collections track two separate facts (what was earned vs. what was actually settled), and only the "earned" one feeds the importer.

## 3. Fix / remediation (this PR)

**This PR ships a dry-run plan builder only — `backend/services/legacy_payout_correction_service.py` — with no write path.** It cannot insert a `payouts` row, insert a `rides` row, or call Stripe; it only reads the 4 legacy CSVs + live `rides` (read-only) and prints a plan.

Verified output (see full dry-run run in the PR description / session transcript):
- **35 rows / 20 real drivers / $276.59 total**, exact filter chain: `pending_amount_status == 'due'` (158 rows) → booking resolvable in `bookings.csv` (50 rows) → excludes vendor test-tenant rows (country_code `91` / `@yopmail.com` email) (35 rows).
- **Group A — 27 rows already imported into `rides`** (already have a `legacy_import` offsetting payout that wrongly zeroed real owed money) — **$271.24**. Of these, **3 rows ($20.73) have no linked Spinr driver account** (`driver_id` is `NULL` on the imported ride — an unmatched party from the original import) and cannot be paid to anyone until/unless that old `driver_id` is matched to a real Spinr account.
- **Group B — 8 rows not yet imported at all** — **$5.35**, of which 6 rows are exactly `$0.00` (same driver, `pending_amount_status='due'` but zero `payout_amount` — flagged as `due` in the old app's own bookkeeping despite no money attached; carried through faithfully rather than silently dropped, since dropping rows that don't fit expectations is exactly the failure mode this whole exercise exists to catch).

**108 rows dropped at the booking-resolution step are not in this figure and are not claimed to be resolved** — their legacy ObjectIDs embed timestamps (2025-11-21 through 2026-01-29) that predate every other export file (2026-01-30 onward). They cannot be cross-referenced against `bookings.csv`/`drivers.csv`/`customers.csv` at all from the files on hand. This is a real, unresolved gap, not a false alarm — flagged for follow-up, not silently written off as test/junk data.

**Not shipped in this PR** (deliberately deferred to a follow-up, pending explicit sign-off on the specific approach):
- Any code that inserts a `payouts` row or a `rides` row
- Any code that calls Stripe
- Any live-data mutation of any kind

## 4. Risk & impact on existing functionality

- **Blast radius of the code that IS shipped**: one new, self-contained module with no callers anywhere else in the codebase yet (not wired into any route, CLI, or background loop). It imports `supabase_client` for a single read-only `.select()` query against `rides` — no write methods used, verified by reading the module's own source (only `.select()` appears; no `.insert()`/`.update()`/`.delete()`).
- **No risk to `booking_import_service.py`'s existing `legacy_import` offset rows** — this PR doesn't touch that file at all.
- **No risk to migrations 302/303's aggregate math** — nothing in this PR changes any `rides` or `payouts` row, so their existing sums are unaffected.

## 5. User-experience effect

None. No UI surface calls this module. No rider, driver, or admin sees any different behavior from this PR.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/legacy_payout_correction_service.py` | New file — dry-run plan builder, no write path | Reconcile `payments.csv`'s due-but-unpaid driver earnings against production, with a verifiable filter chain instead of an unverifiable one-off script |
| `backend/tests/test_legacy_payout_correction_service.py` | New file — pins the filter chain against small synthetic CSVs (never the real export, which is PII and stays out of git) | Coverage for new money-adjacent logic per CLAUDE.md testing conventions |
| `docs/change-log/2026-08-15-legacy-payout-correction-plan.md` | This file | Mandatory Change Impact Log for a live-tested-surface-adjacent change |

## 7. Before / after

Not applicable — additive new file, no existing behavior changed.

## 8. Rollback plan

`git revert` — this PR contains no data writes and no live-code wiring (nothing imports this module from a route, CLI entry point, or background loop), so there is nothing to roll back beyond the code itself.

**The rollback plan for the follow-up (write-path) PR, when it exists, is the one described in the session that produced this plan**: new `payouts` rows only, `status='pending'` (not `'completed'`) so the Stripe transfer is a distinct, later, explicitly-gated step; deterministic per-driver row IDs so the correction is idempotent on retry; delete-by-batch-tag is a clean rollback for any row that hasn't yet had its Stripe transfer settle. Once a transfer settles, no DB rollback undoes it — that step needs its own explicit go/no-go before this plan is executed for real, not folded into this PR.

## 9. Verification performed

- [x] Ran the dry-run builder against the real legacy CSVs (scratchpad-local, never committed) with the live-Supabase already-imported mapping substituted from a manually-verified query run earlier in the same session — output: 35 rows / $276.59 total, splitting 27/$271.24 (Group A) + 8/$5.35 (Group B), matching the verified figure exactly
- [x] Added and ran `pytest backend/tests/test_legacy_payout_correction_service.py` against small synthetic fixtures covering: due-filter, unresolved-booking drop, test-tenant drop, group A/B split, total-sum correctness
- [ ] Did not run the full backend test suite as part of this specific verification pass (new, self-contained, unwired module — recommend CI run it regardless)
- [ ] Not a `rider-app`/`driver-app`/`admin-dashboard` change — no `npm run build` applicable

## What was NOT verified

- **The 108 unresolved rows are not confirmed as either real unpaid money or junk/test data** — this is a genuine open question that needs either an export from the original source system covering the earlier date window, or a decision from whoever owns/owned the old system that those bookings are out of scope.
- **The 3 no-linked-driver rows within Group A ($20.73)** — whether the old `driver_id` on those rows can be matched to a real Spinr driver account wasn't investigated in this pass; until it is, that $20.73 has no destination account to pay.
- **Whether any of the 27 Group A drivers have since had their Spinr account closed/deactivated** — not checked; would need re-verification immediately before any real payout is built, since state may have changed between this dry run and execution.
- **No live Stripe/production write of any kind was performed or is enabled by this PR** — by design, per the deferred-fix decision above.
