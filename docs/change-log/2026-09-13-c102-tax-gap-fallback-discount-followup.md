# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-13 |
| Author | Claude Code (session `session_013hMEsuEPVu7gwAk21XB4hF`) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | this change |
| Related issue or gap ID | `ACTION_ITEMS.md` C102 (residual item #1, closed here) |

## 1. Issue / gap identified

While independently fixing C102 (receipt PDF/email never disclosed a promo-discount line item) in
this session, another concurrent session fixed the same underlying issue and merged it first as
`#5342`. Resolving the resulting merge conflict on this branch surfaced that `#5342`'s own
ACTION_ITEMS note explicitly deferred one residual: `receipt_pdf.py::_fare_lines`'s "no
`tax_breakdown` persisted" fallback infers tax as `persisted_grand_total − subtotal` — correct only
when no discount is present. Since `grand_total = subtotal + tax − discount`, that gap is actually
`tax − discount` whenever a discount coexists with a missing `tax_breakdown`, silently understating
the true tax figure (or, when discount ≥ tax, going negative and getting dropped entirely by the
`> 0.005` guard).

## 2. Root cause

The gap-inference formula predates any discount handling in this function; it was never revisited
when the discount line was added, in either independent fix (this session's original attempt, or
`#5342`). `#5342`'s own review caught and flagged it but scoped the fix to disclosure only,
correctly treating the arithmetic correction as a separable, riskier change worth its own look.

**Alternative considered:** leave it deferred, as `#5342` did, and file a fresh standalone
ACTION_ITEMS entry instead of fixing it now. Rejected — the fix already existed, reviewed, and
tested on this branch before the conflict (from this session's independent, parallel attempt at
C102), so re-deferring a fix already in hand would just be re-creating work already done.

## 3. Fix / remediation

- `receipt_pdf.py::_fare_lines`: moved the discount/`capped_discount` computation to before the
  tax-gap fallback block (previously computed only after it, purely for the disclosure line), and
  changed `gap = persisted_grand − subtotal` to `gap = persisted_grand − subtotal + capped_discount`.
- **Also found while resolving the merge, unrelated to the above:** git's 3-way merge of
  `email_receipt.py::_build_fare_rows` combined this session's and `#5342`'s independent
  one-line additions to the no-persisted-`grand_total` fallback total *without flagging a
  conflict*, because each patch targeted a different anchor line in the same expression. The
  result subtracted the discount **twice** (`... + tax_total - discount + tip - capped_discount`).
  Caught before commit by re-reading the merged file and confirmed by the pre-existing
  `test_fallback_total_without_persisted_grand_total_subtracts_discount` test, which asserts the
  exact total and would have failed loudly. Fixed by removing the redundant `- discount` term.
- `email_receipt.py::_receipt_total`'s conflict was resolved by keeping `#5342`'s version, which is
  more precise than this session's own independent fix: it reconstructs `min_fare_uplift` from the
  ride's individual fare components to cap the discount at the ride-fare portion only, whereas this
  session's version had capped at the full `total_fare` (including booking/airport fees) — a looser,
  less correct cap. No residual there.
- Test suites for both files were also auto-merged by git; de-duplicated by hand afterward (both
  sessions wrote near-identical coverage for the same disclosure fix independently) down to the one
  test that covers the actual residual: `test_tax_gap_fallback_correctly_separates_tax_from_discount`
  in `test_receipt_pdf.py`. Removed a same-named test from `test_receipt_line_items.py` that
  mischaracterized itself as covering the same bug — `email_receipt.py::_build_fare_rows`'s legacy
  tax fallback reads `ride.get("tax_amount")` directly (no gap arithmetic), so no such bug exists
  there to regress-test.

## 4. Risk & impact on existing functionality

- **Blast radius**: `receipt_pdf.py::_fare_lines` (1 production caller path: PDF receipt
  generation, both the emailed attachment and `GET /rides/{id}/receipt.pdf`) and
  `email_receipt.py::_build_fare_rows` (1 production caller: settlement-triggered emailed receipt).
  Same call sites already covered by `#5342`'s own blast-radius check.
- **Could this regress a working flow?** Only affects rides where `tax_breakdown` is empty/legacy
  AND `discount_amount > 0` AND `grand_total` is persisted — a narrow intersection. Every other
  case (the vast majority: `tax_breakdown` present, or no discount) is provably unchanged: the
  `capped_discount` computation was moved, not altered, and is `Decimal("0")` whenever
  `discount_amount` is absent, making `gap = persisted_grand - subtotal + 0` identical to before.
- **Money math**: Decimal-only throughout, same helpers already in use (`_d`, `_money`, `_q`). No
  Stripe, wallet, or ride-state code touched.

## 5. User-experience effect

Riders on a legacy ride (no `tax_breakdown` persisted) who also received a discount now see the
correct Tax figure on their PDF/email receipt instead of one silently inflated or deflated by the
discount amount, or dropped entirely. Not visible mid-ride — receipts render post-settlement only.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/receipt_pdf.py` | `_fare_lines`: moved discount calc earlier; tax-gap formula now accounts for discount | Residual from C102, deferred by `#5342` |
| `backend/utils/email_receipt.py` | Merge-artifact double-subtraction fixed; `_receipt_total` kept the more precise of two independent fixes | Merge-conflict resolution correctness |
| `backend/tests/test_receipt_pdf.py` | De-duplicated against `#5342`'s already-merged tests; kept the one new regression test for this residual | Avoid redundant coverage |
| `backend/tests/test_receipt_line_items.py` | Removed a duplicate, mischaracterized test (no such bug exists in this file) | Test-suite accuracy |
| `ACTION_ITEMS.md` | C102's residual item #1 marked fixed; corrected its "in both files" claim | Backlog accuracy |
| `docs/change-log/2026-09-13-c102-receipt-discount-line-item.md` | Deleted (superseded by `#5342`'s canonical `2026-09-13-c102-receipt-discount-line.md`) | Avoid duplicate documentation of the same shipped fix |

## 7. Before / after

```python
# Before (as merged in #5342)
persisted_grand = ride.get("grand_total")
if not had_tax and persisted_grand not in (None, "", 0):
    gap = _d(persisted_grand) - subtotal
    if gap > Decimal("0.005"):
        tax_total = gap
        rows.append(("Tax", _money(gap)))
# ... discount computed further down, only for the disclosure line
```

```python
# After
# discount/capped_discount now computed here, before the tax block
persisted_grand = ride.get("grand_total")
if not had_tax and persisted_grand not in (None, "", 0):
    gap = _d(persisted_grand) - subtotal + capped_discount
    if gap > Decimal("0.005"):
        tax_total = gap
        rows.append(("Tax", _money(gap)))
```

## 8. Rollback plan

`git revert` — pure presentation-layer arithmetic, no migration, no data write, no flag.

## 9. Verification performed

- [x] `cd backend && python3 -m pytest tests/test_receipt_line_items.py tests/test_receipt_pdf.py tests/test_receipt_invariants.py tests/test_receipt_shell_snapshot.py tests/test_ride_receipt_delivery.py tests/test_admin_send_receipt_email.py tests/test_guest_corporate_receipt.py tests/test_outbox_receipts.py tests/test_branded_receipt_flag.py tests/test_receipt_route_snapshot.py tests/test_receipt_distance_label.py -q --no-cov` — full suite passing after merge resolution and de-duplication.
- [x] `ruff check` + `ruff format --check` on all touched files — clean.
- [x] Manually confirmed the merge-artifact double-subtraction bug via direct code re-read before
  committing, then confirmed the pre-existing test that would have caught it
  (`test_fallback_total_without_persisted_grand_total_subtracts_discount`) passes against the fix.

**Not verified**: no real PDF/email was visually inspected by a human (no device/email-client
access from this environment) — same limitation as `#5342`'s own change-log.
