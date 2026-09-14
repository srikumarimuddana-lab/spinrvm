# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-13 |
| Author | Claude Code (session `session_01Aq7xVA8zptrqFwExvqVrwN`) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `mvapps/dreamy-faraday-2wz914-c102-receipt-discount` |
| Related issue or gap ID | ACTION_ITEMS.md C102 (closed) |

## 1. Issue / gap identified

The emailed PDF receipt (`utils/receipt_pdf.py::_fare_lines`) and the emailed
HTML receipt (`utils/email_receipt.py::_build_fare_rows`) never rendered a
`Promo (CODE)`/`Promo discount` line when a ride had `discount_amount > 0`,
unlike the JSON receipt endpoint (`routes/rides/_shared.py::_build_fare_breakdown`,
used by the in-app receipt screen), which already discloses it correctly.

## 2. Root cause

`_build_fare_breakdown` was extended to disclose promo discounts at some
point after `_fare_lines`/`_build_fare_rows` were written, and the discount
logic was never ported to the other two line-builders. The rendered total
(`grand_total`) was always correct — the discount is already subtracted from
it at settlement — so this was a pure line-item-transparency gap (CLAUDE.md:
"every charge on the receipt maps to a disclosed line item"), not an
overcharge or a money-correctness bug. Found by `spinr-money-auditor`'s
adversarial review of an unrelated PR (R9, receipt-PDF-reconciliation) and
filed as C102, deliberately out of that PR's scope.

## 3. Fix / remediation

Added the same capped-discount line (`min(discount_amount, ride_fare +
min_fare_uplift)`, labelled `Promo (CODE)` or `Promo discount`) to both
`_fare_lines` and `_build_fare_rows`, positioned after tax lines and before
tip — matching `_build_fare_breakdown`'s exact cap formula and line order.

Two follow-on fixes surfaced by adversarial review before commit (`spinr-money-auditor`,
CLAUDE.md gate #10), both in the same two files' **fallback** total
calculation (used only when a ride has no persisted `grand_total`):

- `_fare_lines` and `_build_fare_rows` each reconstruct a total from parts
  in that fallback case. Once the discount became a visible line, the
  fallback total had to start subtracting it too, or the visible rows would
  no longer sum to the printed total.
- `email_receipt.py::_receipt_total` (a *third*, independent helper used
  only for the email subject line, documented as needing to "match the
  body") has its own separate fallback formula and was missed by the first
  pass — it would have kept overstating the total by the discount amount in
  that fallback case, disagreeing with the now-fixed body by exactly the
  discount. Fixed by adding the same capped-discount subtraction to its
  existing formula (not by delegating to `_build_fare_rows`, which would
  have changed behavior for an existing, differently-shaped legacy test
  path — see Alternative considered below).

**Alternative considered:** have `_receipt_total` call `_build_fare_rows`
and reuse its returned total, eliminating the duplicate formula entirely.
**Rejected** because `_receipt_total`'s existing fallback sums the
persisted, rolled-up `total_fare`/`area_fees_total`/`tax_amount` fields,
while `_build_fare_rows`'s fallback reconstructs from the individual
`base_fare`/`distance_fare`/`time_fare`/`booking_fee` fields — these two
formulas already disagree whenever a ride's `total_fare` doesn't exactly
equal the sum of its components (an existing, out-of-scope inconsistency,
not introduced here). Delegating would have silently changed
`_receipt_total`'s output for that case and broken an existing test
(`test_legacy_path_sums_parts_when_grand_total_missing`) that relies on the
current formula. Adding the same discount subtraction to the existing
formula fixes the one real regression this diff introduced without
touching that separate, pre-existing tax-source inconsistency (left as-is;
worth its own future ticket, not blocking here).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated, single-surface (backend).** Three functions
  changed, all in the receipt-rendering path: `receipt_pdf.py::_fare_lines`,
  `email_receipt.py::_build_fare_rows`, `email_receipt.py::_receipt_total`.
- **Other readers of these functions' return values:** grepped both files
  and the wider `backend/` tree. `_fare_lines`'s `(label, amount)` tuples
  are only ever consumed generically (the PDF row-renderer prints any tuple,
  special-casing only the literal labels `"__rule__"`/`"__note__"`).
  `_build_fare_rows`'s HTML string is only ever re-parsed generically
  (regex over `<tr><td>label</td><td>amount</td></tr>`, no fixed row-count
  or positional-index assumption anywhere). No caller breaks from an extra
  row appearing.
- **Other consumers of `_receipt_total`:** only `send_receipt_email`'s
  subject-line builder and its log-fallback (both in `email_receipt.py`,
  same file). No other module calls it.
- **Does this change `grand_total` charged to a rider?** No. In the common
  case (persisted `grand_total`, which is the normal path for every
  completed ride), the new line is pure disclosure — the total returned is
  byte-for-byte identical to before this diff. Confirmed by reading every
  production write path for `ride.grand_total`
  (`routes/rides/booking.py:1129` and `:1368`, `services/fare_service.py:481`)
  — all persist a real value, so the no-persisted-total fallback (the only
  path whose arithmetic changed) is a legacy/incomplete-row edge case, not
  the normal flow.
- **Two pre-existing, out-of-scope issues surfaced during review, filed as
  follow-ups rather than fixed here** (adversarial review confirmed neither
  is introduced or worsened by this diff):
  1. The "gap"-based tax fallback in both files (`gap = persisted_grand -
     subtotal`, used only when `tax_breakdown` is empty) can silently omit
     the Tax line entirely when a large discount pushes the gap negative —
     a legacy-row-only path (pre migration-46 `tax_breakdown`).
  2. `routes/rides/_shared.py`'s stop-edit re-estimate path already has a
     documented residual (comment marked "N3") where its discount
     subtraction is uncapped, unlike every render-side cap (including this
     diff's two fixes) — pre-existing, unrelated to this change.
  Neither blocks this diff; both are candidates for their own ACTION_ITEMS
  entries if/when picked up.

## 5. User-experience effect

Rider-facing: a rider who used a promo code now sees a `Promo (CODE)` /
`Promo discount` line on their emailed PDF receipt and emailed HTML
receipt, matching what the in-app receipt screen already showed. The total
charged is unchanged — this only adds a disclosure line, never a new
charge. Not visible mid-session (receipts are generated after a ride
completes, not during an active ride).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/receipt_pdf.py` | `_fare_lines` now appends a capped promo/discount row; fallback total (no persisted `grand_total`) now subtracts it | Line-item parity with `_build_fare_breakdown`; keeps the fallback total consistent with the now-visible row |
| `backend/utils/email_receipt.py` | `_build_fare_rows` — same two changes as above, styled green (`#10b981`) matching rider-app's `colors.success` for `type: 'discount'`; `_receipt_total` — added the same capped-discount subtraction to its own independent fallback formula | Line-item parity; keeps the email subject-line total consistent with the receipt body in the same fallback case |
| `backend/tests/test_receipt_pdf.py` | 5 new tests: line appears/is pure disclosure, generic label, zero-discount (no row), capping, fallback-total math | Regression coverage for the PDF path |
| `backend/tests/test_receipt_line_items.py` | `TestPromoDiscountLineItem` (5 tests) + 1 new test in `TestReceiptTotal` for the subject-line fallback fix | Regression coverage for the HTML path and the `_receipt_total` fix found by adversarial review |

## 7. Before / after

```python
# Before (backend/utils/receipt_pdf.py::_fare_lines, tail)
    if tip > 0:
        rows.append(("Tip", _money(tip)))

    if persisted_grand not in (None, "", 0):
        grand = _q(_d(persisted_grand) + tip)
    else:
        grand = _q(subtotal + area_total + tax_total + tip)
    return rows, grand
```

```python
# After
    discount = _d(ride.get("discount_amount"))
    capped_discount = Decimal("0")
    if discount > 0:
        promo_label = f"Promo ({ride['promo_code']})" if ride.get("promo_code") else "Promo discount"
        ride_fare_for_cap = base + dist + time_ + min_fare_uplift
        capped_discount = min(discount, ride_fare_for_cap) if ride_fare_for_cap > 0 else discount
        rows.append((promo_label, f"-{_money(capped_discount)}"))

    if tip > 0:
        rows.append(("Tip", _money(tip)))

    if persisted_grand not in (None, "", 0):
        grand = _q(_d(persisted_grand) + tip)
    else:
        grand = _q(subtotal + area_total + tax_total + tip - capped_discount)
    return rows, grand
```

(`email_receipt.py`'s `_build_fare_rows` and `_receipt_total` changed the
same way — see the diff for the HTML-specific and subject-line-specific
versions.)

## 8. Rollback plan

`git-revert-safe` — a plain `git revert` of this commit restores the prior
(disclosure-incomplete but still money-correct) behavior exactly. No
migration, no config, no feature flag: `grand_total` was never wrong before
or after this change, so there is no live-data remediation needed either
way.

## 9. Verification performed

- [x] Automated tests run (unit): `test_receipt_pdf.py`,
      `test_receipt_line_items.py`, `test_receipt_invariants.py`,
      `test_ride_receipt_delivery.py`, `test_receipt_route_snapshot.py`,
      `test_receipt_shell_snapshot.py`, `test_receipt_distance_label.py`,
      `test_all_emails_are_branded.py`, `test_admin_send_receipt_email.py`,
      `test_email_snapshots.py`, `test_branded_receipt_flag.py`,
      `test_coverage_rides.py` — 321/321 passed.
- [ ] Manual repro in staging — not performed; no staging environment
      exists for this repo yet (ACTION_ITEMS.md E1, open).
- [x] Blast-radius grep performed: every call site of `_fare_lines`,
      `_build_fare_rows`, and `_receipt_total`; every production write path
      for `ride.grand_total`. See §4.
- [x] Reviewed against relevant CLAUDE.md conventions: Decimal-only money
      math (all new arithmetic uses `_d`/`Decimal`, never float); "every
      charge maps to a disclosed line item" (the principle this fix
      directly serves).
- [x] Adversarial review performed before commit per gate #10
      (`spinr-money-auditor`): found and this session fixed one real bug
      (`_receipt_total`'s missed fallback, above) before committing; two
      pre-existing, out-of-scope issues surfaced and documented in §4
      rather than silently ignored.
- [x] Feature-flag consideration: not flagged. This is a pure disclosure
      addition (an extra line item) with no behavior change to any amount
      actually charged, and receipts are not a shared, 3+-page component —
      CLAUDE.md's flagging bar (user-visible *and* non-trivial, or a
      shared component) isn't met.

## What was NOT verified

- No real end-to-end email/PDF delivery test against a live inbox — unit-
  level rendering assertions only (string/tuple content and Decimal totals),
  consistent with how this repo's existing receipt tests are scoped.
- The two pre-existing issues noted in §4 (gap-fallback tax omission under
  a large discount; the stop-edit path's uncapped discount) were confirmed
  present but not fixed — out of scope for this change, flagged for a
  separate ACTION_ITEMS entry if picked up later.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — plain `git revert`, no data
      remediation needed.
- [x] Blast radius is stated, not assumed — isolated to three functions in
      two files, all consumers traced.
- [x] No silent behavior change to an already-shipped flow — the only
      behavior change is an added disclosure line; every real (persisted-
      total) charge amount is byte-for-byte unchanged, confirmed by test
      and by tracing every production write path for `grand_total`.
