# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Author | Claude (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | (see PR description) |
| Related issue or gap ID | ACTION_ITEMS.md A29, sub-finding "`corporate_statement_pdf.py` GST/PST fallback risk" |

## 1. Issue / gap identified

`generate_corporate_statement_pdf` (`backend/utils/corporate_statement_pdf.py`)
silently falls back to a single combined "Tax (GST/PST)" line whenever
`summary["tax_by_type"]` is empty/missing, even when real tax money
(`tax_total` > 0) was collected that period. If that path is ever hit for a
period where the company actually paid both GST and PST, the resulting PDF
violates the separate-line-items rule in `regulatory-sk.md` ("Rider receipts
must show GST (5%) and PST (6% where applicable) as separate line items") —
and nothing today would tell anyone it happened.

## 2. Root cause

`tax_by_type` is populated by `_aggregate_rows` in
`backend/routes/corporate_company.py` by summing each ride row's
`tax_breakdown` dict (attached per-ride by `_attach_ride_tax`). If every ride
in the statement period is missing a populated `tax_breakdown` — e.g. rides
that predate the PST-cutover breakdown field, a future tax type the
aggregator doesn't yet bucket by label, or any other data gap upstream —
`tax_by_type` lands empty in the PDF generator even though `tax_amount` (and
therefore `tax_total`) is nonzero. The PDF generator has no way to tell the
difference between "genuinely $0 tax this period" and "tax was collected but
we lost the breakdown," so it collapsed both into the same silent fallback
line.

## 3. Fix / remediation

Added a `_log_combined_tax_fallback()` helper, called at the existing
fallback site, that:
- No-ops when `tax_total == 0` (a $0 combined line is not a regulatory risk
  — there's nothing to itemize — so we don't alert on the common "no
  rides"/"no tax" case; this avoids paging on a no-op).
- When `tax_total != 0` and `tax_by_type` is empty/missing, emits
  `logger.error(...)` with company id, statement month, the tax total, and
  the raw `tax_by_type` value, then attempts a best-effort
  `sentry_sdk.capture_message(..., level="error", tags={"domain":
  "corporate", "surface": "backend"})` (no-ops safely if `sentry_sdk` isn't
  installed/configured, matching the existing pattern in
  `services/ledger_service.py::escalate`).
- The statement is **still generated and returned** — no exception is
  raised, no PDF download is blocked. The combined "Tax (GST/PST)" line
  still renders exactly as before; only the internal logging/alerting
  behavior changed.

This follows CLAUDE.md's "do not silently swallow errors" convention:
degrade loudly, don't crash a live invoice download over an edge case, and
don't mask the underlying data gap. Deviation from the literal ACTION_ITEMS
wording ("remove the combined-line fallback (fail loudly instead)"): we did
not remove the fallback or raise, because failing loudly by crashing PDF
generation would deny a corporate customer their invoice entirely, which
CLAUDE.md and the task explicitly call out as a worse outcome than a
logged-but-still-served degrade path.

## 4. Risk & impact on existing functionality

**Blast radius: single-file, additive-only. No behavior change to the
rendered PDF except in the already-broken fallback edge case (which never
changes what's rendered, only what gets logged).**

Grepped every caller of `generate_corporate_statement_pdf` (the only
function touched):
- `backend/routes/corporate_company.py:1081` —
  `GET /billing/statements/{month}/pdf` (company-portal admin download).
  Unaffected: same inputs/outputs, only an internal log/Sentry call added on
  the fallback branch.
- `backend/routes/corporate_accounts.py:571` —
  `GET /{company_id}/billing/statements/{month}/pdf` (internal-admin mirror
  of the same download, explicitly documented to produce a byte-identical
  PDF to the company-portal route). Same: unaffected.
- `backend/tests/test_corporate_statement_pdf.py` — existing 8 tests
  unaffected (verified below); 3 new tests added.
- `backend/tests/test_corporate_statement_pdf_routes.py` — exercises both
  routes above via a patched/mocked `generate_corporate_statement_pdf`, so
  it never runs the real fallback branch; unaffected either way. Verified
  by running it (8/8 pass).
- `backend/tests/test_corporate_kyb_reverification_route.py` — matched the
  module name in a broader grep but does not call this function directly;
  confirmed by inspection it's an unrelated coincidental match (KYB
  re-verification reminder, not statement PDFs).

No other module imports `utils/corporate_statement_pdf`. No DB writes, no
wallet/ledger interaction, no ride-state interaction — this module is
presentation-only over numbers `_aggregate_rows` already computed elsewhere
(unchanged by this PR). No background loop touches this file.

## 5. User-experience effect

None visible. A corporate admin (company-portal or internal Spinr admin)
downloading a statement PDF gets the exact same document as before,
including in the fallback case — the combined "Tax (GST/PST)" line still
renders with the same total. The only change is that Spinr's own logs/Sentry
now record when that fallback fires with real tax money involved, so the
underlying data gap can be found and fixed before it ever produces a
statement with both GST and PST silently merged. Not visible mid-session to
anyone; this is a synchronous PDF-generation path with no state carried
between requests.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/corporate_statement_pdf.py` | Added `_log_combined_tax_fallback()` helper and a call to it at the existing combined-tax-line fallback site; added `logging`/`Decimal` imports | Surface the fallback loudly (error log + best-effort Sentry) instead of silently shipping a potentially-noncompliant tax line, per ACTION_ITEMS.md A29 |
| `backend/tests/test_corporate_statement_pdf.py` | Added 3 tests: fallback-with-zero-tax stays silent, fallback-with-nonzero-tax logs an error with expected context, fallback with `tax_by_type` key entirely absent also logs | Pin the new behavior; confirm the fallback still produces a valid PDF rather than raising |

## 7. Before / after

```python
# Before
    tax_by_type = summary.get("tax_by_type") or {}
    if isinstance(tax_by_type, dict) and tax_by_type:
        for label, amount in tax_by_type.items():
            line_item(pdf_safe(str(label)), str(amount))
    else:
        line_item("Tax (GST/PST)", money("tax_total"))
```

```python
# After
    tax_by_type = summary.get("tax_by_type") or {}
    if isinstance(tax_by_type, dict) and tax_by_type:
        for label, amount in tax_by_type.items():
            line_item(pdf_safe(str(label)), str(amount))
    else:
        tax_total_str = money("tax_total")
        _log_combined_tax_fallback(company, statement, tax_total_str, tax_by_type)
        line_item("Tax (GST/PST)", tax_total_str)
```

`_log_combined_tax_fallback` (new): no-ops when the parsed `tax_total` is
zero; otherwise `logger.error(...)` with company id / month / tax_total /
raw `tax_by_type`, then a best-effort `sentry_sdk.capture_message(...,
tags={"domain": "corporate", "surface": "backend"})` wrapped in its own
try/except so telemetry failures can never block a PDF download.

## 8. Rollback plan

`git revert` is a complete and sufficient rollback here — this change is
purely additive logging/telemetry around an existing fallback branch; it
writes no data, moves no money, and changes no rendered output in the
non-edge-case path. Reverting removes the new log/Sentry call and restores
the prior (silent) fallback behavior with zero data cleanup required. No
feature flag was introduced because there is nothing user-visible to flag —
the only observable change is in backend logs/Sentry, which is exactly the
class of change CLAUDE.md's rollback-plan section treats as safely
`git revert`-able (unlike anything touching Stripe charges, wallet deltas,
or ride state, none of which this PR touches).

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_corporate_statement_pdf.py -v` — 12/12 passed (9 pre-existing + 3 new). `pytest backend/tests/test_corporate_statement_pdf_routes.py -v` — 8/8 passed (pre-existing, unmodified, confirms both PDF-download routes still work end-to-end against a mocked `generate_corporate_statement_pdf`).
- [x] `ruff check` and `ruff format --check` on both modified files — clean.
- [ ] Manual repro steps followed in staging — **not performed**, see below.
- [x] Blast-radius grep performed: `grep -rn "generate_corporate_statement_pdf"` across `backend/` — all 3 real call sites named above (2 route handlers + this module's own tests), no others.
- [x] Reviewed against relevant CLAUDE.md conventions: "do not silently swallow errors" (this is the fix), observability conventions (Sentry `domain`/`surface` tags, `logger.error` not `logger.warning` for the actionable case, no PII in the logged context — only company id/month/decimal amounts).
- [x] Feature-flagged if user-visible and non-trivial — not applicable, nothing user-visible changed.

## 10. What was NOT verified

- **Not run against a real Supabase / live corporate account.** All verification is via the existing mocked-`fpdf2`/`pypdf`-round-trip unit tests (`test_corporate_statement_pdf.py`) and the route-level tests that patch `generate_corporate_statement_pdf` entirely (`test_corporate_statement_pdf_routes.py`). No live statement generation, no real Sentry event was fired/observed — the Sentry call path is exercised only insofar as `sentry_sdk` is (or isn't) installed in the test env; its actual delivery to Sentry was not confirmed.
- **No production build was run** — this is a backend-only Python change; there is no `admin-dashboard`/`rider-app`/`driver-app` build step applicable here, so that CLAUDE.md requirement doesn't apply to this PR.
- **Did not attempt to reproduce the actual upstream data gap** (a ride with `tax_amount` > 0 but no `tax_breakdown`) against `_aggregate_rows`/`_attach_ride_tax` in `routes/corporate_company.py` — those functions were read for context but are unmodified and out of scope per the task's "keep this to one logical change" constraint. The new tests construct the empty-`tax_by_type`-with-nonzero-`tax_total` condition directly at the PDF-generator boundary rather than by driving it through the real aggregator.
- **No load/perf testing** — this is a synchronous, in-request logging call on an already-slow (PDF-render) path; not expected to be SLA-relevant, but no explicit before/after timing was captured.

## 10b. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data cleanup)
- [x] Blast radius is stated, not assumed (3 real callers, all named and checked)
- [x] No silent behavior change to an already-shipped flow — the rendered PDF is byte-identical outside the fallback edge case, and even in that case only logging changed, not the rendered content
