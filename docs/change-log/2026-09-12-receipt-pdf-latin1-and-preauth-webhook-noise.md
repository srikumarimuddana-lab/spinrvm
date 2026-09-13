# Change Impact & Risk Log — receipt PDFs failing on every ride; doomed pre-auth per booking

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| Related issue or gap ID | Sentry `CRIMSON-SMOKE-7445-TF`, `-T4`, `-T5`, `-T6`; rides `SPR-VWSR6C`, `SPR-4UG6AC` |

## 1. Issue / gap identified

Two independent payment-path defects, both firing on **every** ride on 2026-09-12:

1. **No rider received a PDF receipt.** `generate_receipt_pdf` raised
   `FPDFUnicodeEncodingException: Character "—" at index 0 …` at 18:37:19 and
   19:30:52 — immediately after each ride's payment capture.
2. **Every booking minted a PaymentIntent guaranteed to fail**, each producing an
   orphaned `payment_intent.payment_failed` webhook, an HTTP 500, and a Sentry
   error. Observed 4/4: rides `daf28c32`, `221e2e5b`, `8a6c8cbd`, `617e37b9`.

## 2. Root cause

**Receipt PDF.** fpdf2's `Helvetica` is a core font and latin-1 only. The
receipt used a literal em dash `"—"` as the empty-field placeholder
(`value or "—"`), so any ride with a missing address raised and **failed the
whole attachment**. A `to_latin1()` helper already existed in
`utils/company_details.py` and was applied consistently in the sibling
`utils/subscription_invoice_pdf.py` — but in `receipt_pdf.py` it was applied to
exactly one line, with a narrower `\.replace("—", "-")` patch on another. Every
other text path (addresses, driver name, fare labels) was unprotected.

Reproduced directly against the installed fpdf2:

```
FPDFUnicodeEncodingException: Character "—" at index 0 in text is outside
the range of characters supported by the font used: "helvetica".
```

— byte-for-byte the production message, including the index.

**Pre-auth.** Stripe rejects the whole `PaymentIntent.create` when the *account*
is not enrolled for incremental authorization ("This account is not eligible for
the requested card features"). `authorize_ride` already detected this and retried
without the flag — correct, and the hold always succeeded. But it re-requested it
on **every subsequent booking**, so every ride minted one dead PaymentIntent.
Because the pre-auth runs *before* the ride row is inserted, that dead PI's
failure webhook can arrive before the row exists; the webhook handler then
`logger.error`s and returns 500 so Stripe will retry — correct in isolation, but
it made a guaranteed-per-booking 500 and Sentry error out of a self-inflicted
failure.

## 3. Fix / remediation

1. Apply the existing `to_latin1()` to every text path in `receipt_pdf.py`
   (addresses, driver name + detail, fare labels/amounts, route note), and
   replace the two em-dash placeholders with a plain `-`.
2. Cache the account-level incremental-auth refusal per process
   (`_account_incremental_auth_ineligible`) and omit the parameter once
   observed, so no later booking mints the doomed PaymentIntent. Deliberately a
   cache, not a setting: a restart re-probes.

## 4. Risk & impact on existing functionality

**Blast radius: two backend modules, no schema or data change.**

- `to_latin1` is pure and already used on this exact surface; it substitutes
  known punctuation and drops anything else unencodable. Worst case a receipt
  shows `-` where it showed `—`. It cannot fail a PDF that previously rendered.
- The pre-auth change alters only `payment_method_options.card`. **Amount,
  capture method, idempotency key, decline handling and the `requires_action`
  path are untouched**, so the dead-card-before-dispatch protection is
  unchanged. Callers of `authorize_ride` (`routes/rides/booking.py`) see an
  identical `ChargeOutcome`.
- `auth_incrementable` is recorded from `_reads_incremental_support(intent)` on
  the *successful* intent. With the request omitted, Stripe reports no
  incremental support, so the flag stays `False` — which is already what this
  account produces today. `services/payment_service.py` reads it only to decide
  whether a tip is added to the existing hold or charged separately; that
  decision is therefore unchanged for this account.
- The cache is per process, so replicas converge independently after their first
  booking; there is no shared state and no ordering requirement.

## 5. User-experience effect

- **Riders start receiving PDF receipts again** — today they receive none.
  A receipt whose address contains an em dash, accent or curly quote now renders
  with that character folded instead of the attachment being dropped entirely.
- No change to amounts charged, to the payment flow, or to any screen. Nothing
  is visible mid-session to a rider or driver.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/receipt_pdf.py` | `to_latin1()` on addresses/driver/labels/route note; em-dash placeholders → `-` | the crash that dropped every receipt |
| `backend/utils/stripe_charge.py` | cache account-level incremental-auth ineligibility; `_mark_…` / `_reset_…` helpers | stop minting a PI that is certain to fail |
| `backend/tests/test_receipt_pdf.py` | 4 new latin-1 regression tests | pin the exact production failure |
| `backend/tests/test_authorize_incremental_shape.py` | autouse cache reset + 3 new tests | pin the skip, and keep the suite order-independent |

## 7. Before / after

```python
# Before — a missing address becomes a bare em dash and raises, killing the PDF.
pdf.multi_cell(W, 5, value or "—")
```
```python
# After
pdf.multi_cell(W, 5, to_latin1(value) or "-")
```

```python
# Before — asked on every booking; refused every time on this account.
"payment_method_options": {"card": {"request_incremental_authorization": "if_available"}},
```
```python
# After
"payment_method_options": {
    "card": {} if _account_incremental_auth_ineligible else {"request_incremental_authorization": "if_available"}
},
```

## 8. Rollback plan

`git revert` is sufficient and complete: both changes are pure code on the
request-construction / rendering path, with no migration, no persisted state and
no already-written row altered. Reverting restores the previous behaviour on the
next deploy. No live data (Stripe charge, wallet delta, ride state) is touched by
either change — the amounts and capture semantics are identical before and after.

## 9. Verification performed

- [x] `pytest tests/test_receipt_pdf.py` — 14/14 (4 new).
- [x] `pytest tests/test_authorize_incremental_shape.py` — 15/15 (3 new).
- [x] Related payment/receipt suites together — **106 passed**
      (`test_ride_preauth_booking`, `test_settle_card_capture`,
      `test_receipt_pdf`, `test_receipt_line_items`, `test_receipt_invariants`,
      `test_receipt_route_snapshot`).
- [x] `ruff check` on all four changed files — clean.
- [x] **Proved the new receipt tests are not vacuous**: ran the em dash through
      the installed fpdf2 directly and confirmed it raises
      `FPDFUnicodeEncodingException` with the identical production message.
- [x] Blast-radius review: `to_latin1` consumers compared against
      `subscription_invoice_pdf.py`; `authorize_ride` callers and the
      `auth_incrementable` reader in `services/payment_service.py` checked.

## 10. What was NOT verified

- **Not run against real Stripe.** The pre-auth change is covered by mocked
  `PaymentIntent.create` only; that the live account still places a hold with the
  parameter omitted is inferred from the fact that this is exactly the fallback
  call the code already makes today and which succeeded on all four rides
  (e.g. `pi_3UEwNFFXFgLO2LdO1q4OjIM8`, status `succeeded`).
- **No visual check of the rendered PDF.** Tests assert a valid `%PDF` document
  is produced, not that the layout is unchanged where a character was folded.
- **The webhook 500 path itself is unchanged.** This removes the *cause* of the
  orphaned failures for this account; a genuinely unlinked
  `payment_intent.payment_failed` from any other source would still 500 and
  retry. Narrowing that handler is deliberately left as its own change.
- Not deployed or observed in production.

## 11. Sign-off

- [x] Rollback plan is concrete, and genuinely sufficient here (no data touched)
- [x] Blast radius enumerated, not assumed
- [x] No silent behaviour change to a shipped flow — the only user-visible delta
      is receipts arriving where they previously did not
