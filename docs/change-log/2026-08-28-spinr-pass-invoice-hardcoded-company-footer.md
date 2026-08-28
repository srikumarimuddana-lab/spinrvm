# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 |
| Author | Claude (session, on request) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/driver-rider-emails-messages-pdf-bio6az` |
| Related issue or gap ID | — (found while answering "why does the PDF say Saskatoon when settings say Regina") |

## 1. Issue / gap identified

The Spinr Pass invoice PDF names the issuing company in three places. Two of
them ignored the `company` argument entirely and printed hardcoded literals:

- the issuing-entity sentence — always "Spinr Mobility Inc."
- the centred footer — always "Spinr Mobility Inc. · Saskatoon, SK · support@spinr.ca"

So an operator who set the company address to Regina in admin Settings got an
invoice whose **header block read Regina** (that part does read settings)
while its **footer still read Saskatoon** — a tax document contradicting
itself on the issuer's address.

## 2. Root cause

`generate_subscription_invoice_pdf` takes `company` and its own docstring
states the intent plainly: *"An invoice is a tax document a driver may file,
so the issuing company's name has to be the configured one."* The header
block (`company.name`, `company.contact_line`, `company.address` at lines
81–105) honours that. The two closing lines were left as literals — an
oversight in the original retrofit, not a deliberate exception: nothing
documents them as fixed, and their content duplicates what `company` already
carries.

Note this is the *opposite* of `utils/report_branding.py`'s constants, which
**are** deliberately fixed (see `utils/company_details.py`'s module docstring
and the 2026-08-08 change log: admin report headers must not move under an
admin's keystroke because those documents have been filed with SGI and
airport authorities). That deliberate decision is untouched here.

## 3. Fix / remediation

Both lines now read from `company` when it is provided:

- issuing sentence → `company.name`
- footer → `company.identity_line · company.support_email`

`company is None` keeps the exact previous literals, so the
`branded_receipt_enabled=false` path renders byte-identically.

## 4. Risk & impact on existing functionality

- **Blast radius: two lines in one PDF generator.** Callers enumerated:
  `routes/drivers/subscriptions.py:1151` (driver's own invoice — passes
  `company`, and comments that its identity must match the email it arrives
  in) and `utils/subscription_invoice.py:124` (admin download / email-resend
  — does **not** pass `company`; see §10).
- Feature-flag interaction preserved: `company` is resolved by
  `_branded_invoice_company()`, which returns `None` when
  `branded_receipt_enabled` is false. The `None` branch is unchanged, so the
  flag still turns the whole retrofit off exactly as before.
- No money arithmetic, ride state, or DB write touched — presentation only.
  Amounts, taxes and totals are untouched.
- `to_latin1()` is applied to both new values, as the surrounding code already
  does, because fpdf2's core Helvetica is latin-1 only and
  `identity_line` contains an em dash (folded to `-`) — without it a
  configured address could raise and cost the driver their invoice.

## 5. User-experience effect

- **Driver-facing.** A Spinr Pass invoice now names the configured company and
  address consistently in the header, body and footer. For an operator whose
  settings say Regina, the footer stops saying Saskatoon.
- Not visible mid-session; invoices are generated per subscription charge.
- Existing already-issued PDFs are unchanged (they are generated at charge
  time, not re-rendered).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/subscription_invoice_pdf.py` | Issuing sentence and footer now read `company.name` / `company.identity_line` / `company.support_email`; literals kept only for the `company is None` path | Honour the function's documented contract; stop a tax document contradicting itself |

## 7. Before / after

```
# Before — ignores `company` even when it is supplied
"This invoice is issued by Spinr Mobility Inc. in connection with ..."
pdf.cell(0, 5, "Spinr Mobility Inc. · Saskatoon, SK · support@spinr.ca", align="C", ln=True)
```

```
# After — follows the configured identity, literals only as the None fallback
_issuer = to_latin1(company.name if company is not None else "Spinr Mobility Inc.")
f"This invoice is issued by {_issuer} in connection with ..."
to_latin1(f"{company.identity_line} · {company.support_email}"
          if company is not None
          else "Spinr Mobility Inc. · Saskatoon, SK · support@spinr.ca")
```

## 8. Rollback plan

`git revert` this commit. Presentation-only change to a PDF generator; no
migration, no persisted data, no money path. Already-issued invoices are
unaffected either way.

## 9. Verification performed

- [x] Compile check — `python3 -m py_compile`.
- [x] Blast-radius grep — both callers of `generate_subscription_invoice_pdf`
      enumerated and their `company` argument checked; confirmed the `None`
      branch preserves prior output exactly.
- [x] Read the flag path (`_branded_invoice_company`) to confirm the
      `branded_receipt_enabled=false` behaviour is untouched.
- [ ] **PDF not rendered.** `fpdf2` is not installed in this environment and
      cannot be (no PyPI access — pip fails, pypi.org returns 403), so the
      generated PDF was **not** produced or inspected. The change is textual
      substitution into existing `pdf.cell`/`pdf.multi_cell` calls with
      `to_latin1` applied as the surrounding code does, but it has not been
      executed. **CI/staging is the first place this actually renders.**
- [ ] Automated tests not run — same dependency limitation.

## 10. What was NOT verified / left open

- **`utils/subscription_invoice.py:124` does not pass `company` at all.** That
  path powers the admin invoice download and email-resend, so those invoices
  fall back to the hardcoded identity for the *whole* document, not just the
  footer — while the driver's own copy of the same invoice (via
  `routes/drivers/subscriptions.py`) uses the configured one. That directly
  contradicts `subscription_invoice.py`'s own docstring ("so the admin PDF
  download and the admin email-resend stay byte-for-byte consistent with the
  driver's own invoices"). **Not fixed here**: the fix needs a decision on
  where the `branded_receipt_enabled`-gated resolver should live (duplicate
  it into the util, or lift `_branded_invoice_company` out of the route into
  `utils/company_details.py` and have both import it). Raised for the owner
  rather than chosen unilaterally, since it edits a money route.
- **`subscription_invoice_pdf.py:105`** still falls back to the literal
  `"Saskatoon, SK, Canada"` when `company` is supplied but its address is
  *empty*. Harmless when an address is configured. Left alone because "what
  should a tax invoice show when no address is configured" is a product
  decision (blank vs. stale literal), not an obvious bug fix.
- No test was added. There is no existing test module for this generator, and
  a first test for it that cannot be executed here would be unverified
  scaffolding; better added alongside the §10 fix above, when it can be run.

## Sign-off

- [x] Rollback plan is concrete
- [x] Blast radius stated, not assumed — two lines, two callers, flag path checked
- [x] No silent behaviour change for the flag-off path (byte-identical)
- [x] Remaining related gaps named rather than quietly left
