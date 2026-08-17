# Change Impact & Risk Log

> ⚠️ **SUPERSEDED (2026-08-16)** — the $276.59 / 20-driver headline figure and
> the per-driver table in this doc were correct at time of writing but were
> revised the next day by the Stripe cross-check in
> `docs/change-log/2026-08-16-gst-backfill-and-stripe-crosscheck.md` §1a:
> revised range for the 15 real-driver buckets is **$185.31–$228.08** (down
> from $271.24) — 1 bucket ($22.43) has clean Stripe evidence of already
> being paid and should be **excluded**, and 2 buckets ($42.77 combined) are
> genuinely ambiguous pending a human call. This doc's filter chain and
> corrected write-path design (§3a) are still accurate; only the dollar
> totals are stale. **Do not execute a real payout off the $276.59 figure in
> this doc — read the crosscheck doc first.**

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

## 3a. Follow-up design — corrected after review, before any write code is built

An earlier version of this plan proposed the follow-up fix as "insert a new
`payouts` row, `status='pending'`." **That was wrong and is corrected here
before any of it gets built.**

`routes/drivers/earnings.py`'s balance formula is:

```
payable_balance = total_earnings + total_bonuses - total_payouts
```

Payout rows **subtract** from balance — inserting one would move a driver's
balance *down*, not credit them the missing money. Worse,
`utils/legacy_rides.py` confirms every legacy-imported ride's earnings are
**already unconditionally excluded** from `total_earnings` on the driver's
own balance screen, regardless of whether an offsetting payout exists at
all. So the other candidate ("just reduce the existing `legacy_import`
offset payout's amount") is also a dead end: `drop_legacy_offset_payouts()`
drops the entire `legacy_import` type from the sum by type, not by value —
changing its amount would have zero effect on `payable_balance`.

**Conclusion: these 35 rows cannot be fixed through the live in-app balance
mechanism at all — the rides were deliberately designed to sit outside it.**
The correct model mirrors how `stripe_sync` payouts already work in this
codebase: a payout row that's a pure historical record of a real transfer,
explicitly added to the same exclusion set `drop_legacy_offset_payouts()`
already uses for `legacy_import`/`stripe_sync`, so it never touches
`payable_balance`/`total_payouts` math (correct, since the underlying rides
were never part of that math either) but is written once the real Stripe
Transfer settles, for T4A/audit history.

This applies identically to Group A and Group B — the group split matters
for figuring out *whether the ride is imported yet*, not for *how the driver
gets paid*, which is the same mechanism either way.

> **Correction (flagged by money-audit review, 2026-08-17):** the phrase
> "the same exclusion set `drop_legacy_offset_payouts()` already uses for
> `legacy_import`/`stripe_sync`" above is imprecise — those two types are
> **not** excluded via the same code path today. `legacy_import` is dropped
> by `drop_legacy_offset_payouts()`/`is_legacy_offset_payout()`
> (`backend/utils/legacy_rides.py`), which removes it from **both**
> `total_payouts` and `pending_payouts`. `stripe_sync` is excluded only via
> an inline `payout_type != "stripe_sync"` filter inside `total_payouts`
> (`routes/drivers/earnings.py`), which does **not** exclude it from
> `pending_payouts`. The write-path PR that eventually builds
> `legacy_outstanding_correction` must pick one of these two mechanisms
> explicitly (most likely: extend `is_legacy_offset_payout()` to cover it,
> matching `legacy_import`'s stronger exclusion) rather than assuming "same
> as both" — they are not currently the same as each other.

## 3b. Current state → future state, all 20 driver buckets (verified)

15 of the 20 buckets resolve to a real Spinr driver account today; 5 do not
(`driver_id` is `NULL` on the imported/importable ride — an unmatched party
from the original booking import) and are **blocked** until re-linked to a
real account, independent of anything in this PR.

| # | Driver key | Rows | Group | Current: in live `payable_balance`? | Future: correction |
|---|---|---|---|---|---|
| 1 | `4a00ac19-bf2d-…` | 5 | A | No (legacy rides excluded regardless) | `legacy_outstanding_correction` payout, $43.59 |
| 2 | `350b5267-7a4d-…` | 2 | A | No | $33.32 |
| 3 | `2b228479-106c-…` | 3 | A | No | $25.98 |
| 4 | `a569909e-c866-…` | 2 | A | No | $22.43 |
| 5 | `e2f16d17-5e6c-…` | 2 | A | No | $19.34 |
| 6 | `1a7f6c28-b86c-…` | 1 | A | No | $13.27 |
| 7 | `9f129036-3468-…` | 1 | A | No | $12.54 |
| 8 | `2a216a28-ccd5-…` | 1 | A | No | $12.54 |
| 9 | `9a9e6f2c-8dbe-…` | 1 | A | No | $12.39 |
| 10 | `c1e19bec-0ce1-…` | 1 | A | No | $11.80 |
| 11 | `efa8c93d-1d6a-…` | 1 | A | No | $10.63 |
| 12 | `62bac274-9a63-…` | 1 | A | No | $10.05 |
| 13 | `93a899d5-431b-…` | 1 | A | No | $9.45 |
| 14 | `ac392c28-7a9a-…` | 1 | A | No | $7.54 |
| 15 | `26b80bf4-744a-…` | 1 | A | No | $5.64 |
| 16 | unmatched `69e6b56e…` | 1 | A | N/A — no Spinr account | **Blocked**: re-link before $11.22 payable |
| 17 | unmatched `6a3f41ab…` | 1 | A | N/A | **Blocked**: $5.49, same issue |
| 18 | unmatched `69f7be14…` | 1 | A | N/A | **Blocked**: $4.02, same issue |
| 19 | unmatched `69bed3b4…` | 2 | B | N/A — not imported yet | Import for history (optional) + $5.35 correction once linked |
| 20 | unmatched `6990a23a…` | 6 | B | N/A | $0.00 — `due` per old app, no money attached; no transfer needed |

**Totals**: 15 payable today (real Spinr account on file) = **$250.51**. 3
blocked in Group A = $20.73. Group B: $5.35 payable once linked, $0.00
no-op. **Grand total $276.59 / 20 buckets** — matches §3's figure exactly.

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

**The rollback plan for the follow-up (write-path) PR, when it exists, per §3a's corrected design**: new `payouts` rows only, `payout_type='legacy_outstanding_correction'`, added to the `total_payouts`/`payable_balance` exclusion set alongside `legacy_import`/`stripe_sync` so the row never perturbs any driver's live in-app number before or after it's written; deterministic per-driver row IDs so the correction is idempotent on retry; delete-by-batch-tag is a clean rollback for any row that hasn't yet had its Stripe transfer fire. Once a transfer settles, no DB rollback undoes it — that step needs its own explicit go/no-go before this plan is executed for real, not folded into this PR.

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
