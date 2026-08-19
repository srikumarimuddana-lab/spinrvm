# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Claude (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (local worktree commit — not pushed; see commit SHAs at bottom) |
| Related issue or gap ID | Ranked blocker #26 / NEW finding N14 — `docs/audit/2026-08-18-full-fleet-whole-app-audit.md` |

## 1. Issue / gap identified

The emailed HTML receipt and the attached PDF receipt — both part of the
rider's 7-year-retained legal/financial trip record (`regulatory-sk.md`
trip-log retention) — never showed surge as a real dollar line item. The
email receipt showed only a text footnote ("Surge pricing 1.50× was in
effect at booking time.") with no amount; the PDF receipt did not mention
surge at all. Meanwhile the in-app fare breakdown (`GET /rides/{id}`,
`GET /rides/history`, and the in-app receipt endpoint — all backed by
`routes/rides/_shared.py::_build_fare_breakdown`) already shows surge as a
real dollar amount. CLAUDE.md's "not a hidden-fee operator" section
requires "every charge on the receipt maps to a disclosed line item: base
fare, distance, time, booking fee, surge, tax, tip" — surge was the one
line item missing a number on the durable record.

## 2. Root cause

`utils/email_receipt.py::_build_fare_rows` and `utils/receipt_pdf.py::_fare_lines`
render `distance_fare` and `time_fare` directly from the persisted (already
surge-multiplied) ride columns, with no separate pre-surge column stored on
the `rides` table to subtract from. The original author's comment in
`email_receipt.py` explicitly chose the footnote-only approach to avoid
"round-tripping through floats" — a reasonable-sounding but incomplete
justification, since the in-app breakdown (`_build_fare_breakdown`) had
already solved this in pure `Decimal` without any float round-trip. The PDF
receipt simply never had surge disclosure added when the email receipt's
footnote was introduced.

## 3. Fix / remediation

Ported the exact `Decimal` surge-delta formula already used by the in-app
fare breakdown (`routes/rides/_shared.py::_build_fare_breakdown`) into both
receipt renderers:

```
surged_dt      = distance_fare + time_fare        # already-surged, persisted
unsurged_dt    = round(surged_dt / surge_multiplier)
surge_delta    = 0 if (minimum-fare floor already absorbed the uplift)
                 else round(surged_dt - unsurged_dt)
```

Both files now split the persisted (surged) `distance_fare` / `time_fare`
into pre-surge **display** amounts plus the `surge_delta`, and insert a new
`"Surge (X.XX×)"` row with the real dollar amount. The split is constructed
as a plug — `distance_display + time_display + surge_delta` always equals
the original `distance_fare + time_fare` exactly — so the printed Subtotal
and grand total are byte-for-byte unchanged; only the disclosure improves.
The existing footnote text is kept **alongside** the new line as
supplementary context (not removed), per the task's explicit instruction.
When `surge_multiplier > 1` but the minimum-fare floor already absorbed the
whole uplift, the Surge line still renders — at `$0.00` — matching the
in-app breakdown's own behavior on minimum-fare rides (it does not hide the
disclosure just because the dollar impact happened to be zero).

All new arithmetic uses `Decimal` exclusively (`_d()`/`_q()` in
`receipt_pdf.py`, `_d()`/`_q()` in `email_receipt.py`) — no float is
introduced at any point in the calculation.

## 4. Risk & impact on existing functionality

**Blast radius: isolated to the two receipt renderers and their single
production caller.**

Grepped every caller/reader of the touched functions:

- `_fare_lines` (`utils/receipt_pdf.py`) — called only from
  `generate_receipt_pdf` in the same file.
- `generate_receipt_pdf` — called only from
  `utils/email_receipt.py::send_receipt_email` (best-effort PDF attachment;
  a PDF-generation failure there is already caught and logged without
  blocking the email).
- `_build_fare_rows` (`utils/email_receipt.py`) — called from
  `generate_receipt_html` and `generate_receipt_text` in the same file.
- `generate_receipt_html` / `generate_receipt_text` — called only from
  `send_receipt_email` in the same file.
- `send_receipt_email` — called from exactly one production call site:
  `backend/services/payment_service.py:2111-2113` (fare-settlement flow,
  and the admin "resend receipt to a different email" path, which reuses
  the same function via `recipient_email`). No route file
  (`routes/rides/*`, `routes/admin/*`) calls any of these functions
  directly — confirmed via grep, zero matches.
- No other surface (rider-app, driver-app, admin-dashboard) parses the
  HTML/PDF receipt body — they read `fare_breakdown` from the ride API
  (`_build_fare_breakdown`, untouched by this change) instead.

Because the total is provably unchanged (the surge split is a same-sum
decomposition, not an additive charge), `grand_total`, `_receipt_total()`
(used for the email subject line), and the persisted `rides.grand_total`
column are all unaffected — nothing downstream that reads the charged
total (Stripe settlement, payout ledger, wallet deltas) is touched by this
change. This is a **presentation-only** change to two rendering functions;
no DB write, no state transition, no money actually moved differently.

## 5. User-experience effect

Rider-facing only, and only on the **emailed receipt and its PDF
attachment** (not the in-app fare screens, which already showed the real
number). A rider who took a surged ride and later opens their emailed
receipt will now see a `"Surge (1.50×)"` line with a real dollar amount, in
addition to the existing footnote sentence. Not visible mid-session — the
receipt is generated only at trip completion (fare settlement), so there is
no in-progress ride whose display changes underfoot.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/receipt_pdf.py` | Added `_split_surge_delta()` helper; `_fare_lines()` now inserts a `"Surge (X.XX×)"` dollar row (plus a supplementary footnote note) whenever `surge_multiplier > 1`, using pre-surge display amounts for Distance/Time so the total is unchanged | PDF receipt previously had zero surge disclosure at all |
| `backend/utils/email_receipt.py` | `_build_fare_rows()` now computes the same surge split and inserts a `"Surge (X.XX×)"` dollar row before the existing footnote note; updated stale comments/module docstring | Email receipt previously showed only a text footnote, no dollar amount |
| `backend/tests/test_receipt_pdf.py` | Added 3 tests: real dollar line item + reconciliation, no line when `surge_multiplier == 1`, `$0.00` line when the minimum-fare floor absorbs the delta | Regression coverage for the fix |
| `backend/tests/test_receipt_line_items.py` | Added `TestSurgeDollarLineItem` (4 tests): real dollar amount + footnote coexist, no line at multiplier 1, `$0.00` on minimum-fare clamp, total still reconciles with tip | Regression coverage for the fix |

## 7. Before / after

```
# Before (utils/email_receipt.py — email receipt rows, surge_multiplier=1.5)
Base fare            $3.50
Distance (4.2 km)     $6.30   <- already includes surge, no breakout
Time (12 min)         $2.50   <- already includes surge, no breakout
Booking fee           $0.50
- - - (subtotal divider) - - -
GST (5%)              $0.64
PST (6%)              $0.77
Total                $14.21
[footnote] "Surge pricing 1.50× was in effect at booking time."   <- no $ amount anywhere
```

```
# After
Base fare            $3.50
Distance (4.2 km)     $4.20   <- pre-surge display amount
Time (12 min)         $1.67   <- pre-surge display amount
Booking fee           $0.50
Surge (1.50×)         $2.93   <- NEW real dollar line item
- - - (subtotal divider) - - -
GST (5%)              $0.64
PST (6%)              $0.77
Total                $14.21   <- unchanged
[footnote] "Surge pricing 1.50× was in effect at booking time."   <- kept, now supplementary
```

Reconciliation check: `3.50 + 4.20 + 1.67 + 2.93 + 0.50 = 12.80` — identical
to the pre-fix `3.50 + 6.30 + 2.50 + 0.50 = 12.80` component sum. `Total`
(`grand_total + tip`) is untouched by the split; only the decomposition
under it changed. Verified by
`test_total_still_reconciles_with_surge_line_added` and the PDF
`Subtotal`-reconciliation assertions in `test_receipt_pdf.py`.

The PDF receipt had no "before" surge line at all (see finding N14) — the
"after" state for `utils/receipt_pdf.py` is the same shape as the email
example above, newly added.

## 8. Rollback plan

This is a pure presentation change inside two pure functions
(`_fare_lines`, `_build_fare_rows`) with no schema change, no feature flag,
and no state mutation — `grand_total` and every persisted column are
unread by the new code. Rollback is a plain `git revert` of the two commits
below; there is no live data to reconcile (no Stripe charge, wallet delta,
or ride-state row is written by this code path — it only renders text/PDF
bytes for an already-completed, already-settled ride). No feature flag was
added because the change is additive-only and mathematically provable to
leave the charged total unchanged (see §7); a flag would add complexity
without a corresponding risk to gate.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_receipt_pdf.py
      backend/tests/test_receipt_line_items.py
      backend/tests/test_receipt_invariants.py
      backend/tests/test_surge_line_item.py backend/tests/test_admin_send_receipt_email.py
      backend/tests/test_branded_receipt_flag.py backend/tests/test_guest_corporate_receipt.py
      backend/tests/test_receipt_route_snapshot.py backend/tests/test_receipt_shell_snapshot.py
      -q --no-cov` — **all 105 passed** (`/tmp/spinr-venv/bin/pytest`, a real venv with
      `fpdf2` and the backend's actual `requirements.txt` installed — not skipped/mocked
      around).
- [x] `ruff check` run on all 4 modified files (`utils/receipt_pdf.py`,
      `utils/email_receipt.py`, `tests/test_receipt_pdf.py`,
      `tests/test_receipt_line_items.py`) — all checks passed (after fixing
      5 `E741` ambiguous-variable-name findings in the new tests).
- [x] Blast-radius grep performed (see §4): every caller of
      `_fare_lines`, `generate_receipt_pdf`, `_build_fare_rows`,
      `generate_receipt_html`, `generate_receipt_text`, and
      `send_receipt_email` was enumerated.
- [x] Reviewed against CLAUDE.md conventions: Decimal-only money math
      (verified no `float()` introduced — see §11 below for the
      float-arithmetic-gate coverage gap this surfaced), "every charge maps
      to a disclosed line item," 7-year trip-log retention context.
- [ ] Manual repro in staging — **not performed**; this worktree has no
      staging/Supabase access. See §11.
- [ ] Feature-flagged — not applicable; see §8's rationale for why this is
      additive/non-flagged.

**No production build was run** — this is a backend-only Python change;
there is no `admin-dashboard`/`rider-app`/`driver-app` build step
applicable to `utils/receipt_pdf.py` or `utils/email_receipt.py`.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no live
      data touched)
- [x] Blast radius is stated, not assumed (single production call site
      traced end to end)
- [x] No silent behavior change to an already-shipped flow without the UX
      field filled in (§5 states the visible change explicitly)

## 11. What was NOT verified / standing gaps found

- **Not tested against a real inbox or PDF viewer.** Verified the PDF is
  valid bytes (`starts with b"%PDF"`) and that fpdf2 does not raise when
  rendering the `×` multiplication-sign character (U+00D7, within the
  Latin-1/cp1252 range fpdf2's core Helvetica font supports) — but the
  actual rendered layout (does the new row visually collide with anything,
  does an email client clip the line) was not screenshotted. No visual
  regression tooling exists in this repo for PDF/email output — flagging
  as a standing gap rather than re-discovering it next time (per CLAUDE.md
  §6 of the pre-merge gates).
- **Not exercised against live Supabase** — only against hand-built ride
  dict fixtures in unit tests (`mock`-free, pure-function tests; no DB
  layer is touched by `_fare_lines`/`_build_fare_rows` at all, so this is
  a lower-risk gap than for a DB-writing change, but stating it per
  CLAUDE.md's instruction not to let silence imply full coverage).
- **Money-arithmetic Semgrep gate (`spinr-no-float-in-money`,
  `.semgrep/spinr-rules.yml`) does not currently include either
  `backend/utils/receipt_pdf.py` or `backend/utils/email_receipt.py` in
  its `paths.include` list**, despite both being money-rendering receipt
  code. Verified by inspection that neither file introduces any float
  arithmetic in this change (both use `_d()`/`_q()` Decimal helpers
  throughout; `email_receipt.py`'s only two `float()` calls are GPS
  route-coverage-ratio percentages, not money, similar to the documented
  `drivers/_shared.py` exclusion already in the rule file). This gap
  predates this change and is not fixed here — flagging it explicitly per
  the task instructions rather than silently expanding an unrelated CI
  gate's scope inside a money-line-item PR. Recommend a follow-up PR adds
  both files to the `include` list (and, per the rule file's own comment,
  re-runs semgrep locally to confirm zero findings before merging that
  addition — a prior pass "declared clean on a careful read" and was wrong
  by three findings when actually run).
