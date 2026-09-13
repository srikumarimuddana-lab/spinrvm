# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-13 |
| Author | Claude Code (session `session_013hMEsuEPVu7gwAk21XB4hF`) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | this change |
| Related issue or gap ID | `ACTION_ITEMS.md` C102 |

## 1. Issue / gap identified

The emailed HTML receipt (`utils/email_receipt.py::_build_fare_rows`) and the
PDF receipt (`utils/receipt_pdf.py::_fare_lines`, both the emailed attachment
and, as of R9, the rider-facing `GET /rides/{id}/receipt.pdf` download) never
render a `Promo (...)`/`Promo discount` line when `discount_amount > 0`. The
JSON receipt endpoint's equivalent builder
(`routes/rides/_shared.py::_build_fare_breakdown`, used by the in-app receipt
screen) already discloses this line correctly.

## 2. Root cause

`_fare_lines`/`_build_fare_rows` were written to itemise every *charge* (fare
components, fees, tax, tip) but never gained the one *credit* line type —
discount — that `_build_fare_breakdown` already had. Not a regression: the
deleted client-side `buildReceiptHtml` this replaced didn't render a discount
line either. The header/grand total was never wrong (`discount_amount` is
already subtracted into the persisted `grand_total` at settlement —
`services/fare_service.py::recalculate_ride_fare`:
`new_grand_total = new_total_fare + area_fees_total + tax_amount - discount`)
— this was a disclosure gap, not a money-correctness bug.

A second, related bug was found and fixed in the same functions while
grounding the fix: both files' "no `tax_breakdown` persisted" fallback
inferred tax as `persisted_grand_total - subtotal`. On a ride with a discount
but no `tax_breakdown`, that gap is actually `tax - discount`, not tax alone
— silently mislabeling the true tax figure, or (when discount ≥ tax)
producing a negative gap that the `> 0.005` guard discarded entirely,
dropping the Tax line. `email_receipt.py::_receipt_total`'s own no-grand-total
fallback had the identical omission (discount not subtracted at all).

## 3. Fix / remediation

Ported the same discount-line logic `_build_fare_breakdown` already has into
`_fare_lines` and `_build_fare_rows`: a `Promo ({promo_code})` /
`Promo discount` line, capped at the ride-fare component
(`base + distance + time + minimum-fare uplift` — never booking fee, airport
surcharge, or tax, matching the reference implementation's own comment on
why: promos apply to driver earnings only), inserted after the tax
line(s) and before tip (matching `_build_fare_breakdown`'s line order and the
`grand_total = subtotal + tax − discount` arithmetic). Also corrected the two
tax-gap-fallback formulas to add back the discount before treating the gap as
tax, and `_receipt_total`'s missing-grand-total fallback to subtract discount
too — same root cause, same fix, not scope creep.

No shared line-builder refactor was done (C102's own suggested fix
explicitly scopes that out as "a separate, larger change ... should be
scoped on its own merits, not bundled here").

## 4. Risk & impact on existing functionality

- **Blast radius**: grepped every caller of `generate_receipt_pdf` /
  `generate_receipt_html` / `_receipt_total` / `build_receipt_pdf_bytes`.
  Exactly 2 production call sites: `services/payment_service.py`
  (settlement-triggered emailed receipt) and `routes/rides/receipts.py`
  (rider-facing `GET /rides/{id}/receipt.pdf`, R9). Both are the intended
  beneficiaries of this fix — no other caller exists.
- **Could this regress a working flow?** Only when `discount_amount == 0`
  (the vast majority of rides — no promo applied) is behavior required to be
  byte-for-byte identical to before: confirmed via the full pre-existing
  148-test receipt suite passing unchanged, plus new tests asserting no
  `Promo` row appears and the total is unaffected when discount is absent.
  When `discount_amount > 0`, the header/grand total figure is **unchanged**
  (it already had the discount baked in) — only a new disclosure line is
  added and, in the no-`tax_breakdown` legacy-fallback path, the previously
  wrong/dropped Tax figure is corrected.
- **Money math**: Decimal-only throughout (both files already used
  `Decimal`/`_d()`/`_q()` helpers; the new code reuses them, no float
  introduced). No Stripe, wallet, or ride-state code touched — purely
  receipt line-item text and read-only Decimal arithmetic on already-settled
  ride data.
- **Interaction with background loops / state machine**: none — these
  functions run synchronously at receipt-render time (settlement email,
  on-demand PDF download), not from a background loop.

## 5. User-experience effect

Riders who received a promo/discount on a ride now see a `Promo (CODE)` or
`Promo discount` line (in red/green accent, matching the existing tip-line
styling convention) on both the emailed receipt and the on-demand PDF
download — matching what they already see on the in-app receipt screen. The
dollar total they were charged does not change. Not visible mid-ride (this
is a post-completion, already-settled document); a rider who already has an
old copy of a receipt without this line is unaffected — this only changes
receipts rendered from this point forward.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/receipt_pdf.py` | `_fare_lines`: added the capped Promo discount row; corrected the tax-gap fallback and the no-persisted-`grand_total` fallback to account for discount | C102 fix |
| `backend/utils/email_receipt.py` | `_build_fare_rows`: same Promo discount row + tax-gap-fallback fix; `_receipt_total`: fixed its own missing-grand_total fallback to subtract discount | C102 fix |
| `backend/tests/test_receipt_pdf.py` | 7 new tests: row rendered with/without promo code, capped at ride fare, no row when discount is 0, tax-gap-fallback correctness, grand-total fallback correctness, PDF still generates | Cover both directions of the fix |
| `backend/tests/test_receipt_line_items.py` | 5 new tests, same coverage shape, for the HTML/`_receipt_total` side | Cover both directions of the fix |
| `ACTION_ITEMS.md` | C102 closed | Backlog accuracy |

## 7. Before / after

```python
# Before (backend/utils/receipt_pdf.py::_fare_lines, abbreviated)
subtotal = total_fare_val or (core + area_total)
rows.append(("__rule__", ""))
rows.append(("Subtotal", _money(subtotal)))

tax_breakdown = ride.get("tax_breakdown") or {}
...
persisted_grand = ride.get("grand_total")
if not had_tax and persisted_grand not in (None, "", 0):
    gap = _d(persisted_grand) - subtotal
    if gap > Decimal("0.005"):
        rows.append(("Tax", _money(gap)))

if tip > 0:
    rows.append(("Tip", _money(tip)))
```

```python
# After
subtotal = total_fare_val or (core + area_total)
rows.append(("__rule__", ""))
rows.append(("Subtotal", _money(subtotal)))

raw_discount = _d(ride.get("discount_amount"))
ride_fare_for_discount_cap = base + dist + time_ + min_fare_uplift
capped_discount = (
    min(raw_discount, ride_fare_for_discount_cap) if ride_fare_for_discount_cap > 0 else raw_discount
)
promo_label = f"Promo ({ride['promo_code']})" if ride.get("promo_code") else "Promo discount"

tax_breakdown = ride.get("tax_breakdown") or {}
...
persisted_grand = ride.get("grand_total")
if not had_tax and persisted_grand not in (None, "", 0):
    gap = _d(persisted_grand) - subtotal + capped_discount  # was missing "+ capped_discount"
    if gap > Decimal("0.005"):
        rows.append(("Tax", _money(gap)))

if capped_discount > 0:
    rows.append((promo_label, f"-{_money(capped_discount)}"))  # new line

if tip > 0:
    rows.append(("Tip", _money(tip)))
```

`email_receipt.py::_build_fare_rows` and `_receipt_total` received the
symmetric change.

## 8. Rollback plan

`git revert` — pure presentation-layer code, no migration, no data write, no
flag. A revert restores exactly the prior (discount-invisible, but still
dollar-accurate) receipts. No feature flag was added — the fix is a strict
disclosure improvement with no behavior change when `discount_amount == 0`
(the common case), so there is nothing risky to gate; C102 itself frames this
as "not a money-correctness or overcharge bug."

## 9. Verification performed

- [x] Automated tests: `python3 -m pytest tests/test_receipt_line_items.py
  tests/test_receipt_pdf.py tests/test_receipt_invariants.py
  tests/test_receipt_shell_snapshot.py tests/test_ride_receipt_delivery.py
  tests/test_admin_send_receipt_email.py tests/test_guest_corporate_receipt.py
  tests/test_outbox_receipts.py tests/test_branded_receipt_flag.py
  tests/test_receipt_route_snapshot.py tests/test_receipt_distance_label.py`
  — **148 passed**, including 12 new tests covering the discount line and
  the two related fallback fixes, in both directions (present/absent,
  capped/uncapped, persisted-grand-total/fallback).
- [x] `ruff check` + `ruff format --check` on all touched files — clean.
- [x] Blast-radius grep performed: every caller of the touched functions
  identified (2 production call sites), both confirmed to be the intended
  beneficiaries.
- [ ] Manual repro / staging check — not performed this session (no live
  Supabase/staging access from this environment); reasoned about via the
  Decimal arithmetic and the existing, already-tested reference
  implementation (`_build_fare_breakdown`) instead.

**Not verified**: no real PDF/email was visually inspected by a human (no
device/email-client access from this environment) — the PDF's byte validity
(`%PDF` header) and row content were asserted programmatically, not
screenshotted; this repo has no visual-regression tooling for backend-
rendered PDFs/emails to begin with.
