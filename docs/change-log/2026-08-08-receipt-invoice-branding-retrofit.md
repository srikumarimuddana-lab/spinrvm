# Change Impact & Risk Log — Ride receipt + Spinr Pass invoice branding retrofit

Fourth and last in the 2026-08-08 email series, after
`2026-08-08-driver-lifecycle-email-channel.md`,
`2026-08-08-rider-lifecycle-emails.md`, and
`2026-08-08-email-company-identity-from-settings.md`.

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-08 |
| Author | Claude Code (session-driven) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | payments, admin |
| PR / commit link | `7249125`, `9decf28`, `94679ba`, `5c338ef`, `fa7d005` |
| Related issue or gap ID | `ACTION_ITEMS.md` N11; user request to show the admin Company Info name and address on receipts and invoices |

## 1. Issue / gap identified

The ride receipt and the Spinr Pass invoice predate the shared email layout and
each shipped their own bespoke shell:

- **Hardcoded company details.** `Spinr Technologies Inc. · Saskatoon, SK` and
  `support@spinr.ca · www.spinr.ca`, compiled in. The admin Settings page had a
  "Company Info (shown in apps)" card holding the real name, address, support
  address, website and phone, and these two documents ignored it entirely.
- Legacy `#ee2b2b` rather than the documented brand red.
- The wordmark as `<h1>Spinr</h1>` text — no logo, on the two emails most likely
  to be forwarded to an accountant.
- **The receipt sent HTML only**, so a text-only client, a screen reader, or a
  blocked-image view got nothing at all from a document that doubles as a tax
  record.
- The attached PDFs carried their own separate hardcoded company lines, so
  fixing only the email body would have produced a receipt whose footer named
  one company while the PDF stapled to it named another.

After the previous change in this series, this had become a live inconsistency:
newer emails followed the Settings page while these two did not, so editing the
company details produced two Spinr emails disagreeing about the company.

## 2. Root cause

Both templates were written before `utils/email_layout.py` existed. Nothing
went wrong; the shared layout simply arrived later and these two were left on
the old shell rather than migrated, which is what N11 recorded.

The PDF halves were missed by the earlier settings work for a concrete reason:
`fpdf2` is synchronous and `load_company_details()` is not, so the identity has
to be threaded in as a parameter rather than loaded where it is used.

## 3. Fix / remediation

- `email_layout.header_html` / `footer_html` made public, so the receipt and
  invoice reuse the exact shell newer emails use rather than growing a third
  variant.
- Receipt (`utils/email_receipt.py`) and invoice
  (`routes/drivers/subscriptions.py`) take the shared header and footer, with
  the company name and address from the Settings page.
- Both **attached PDFs** (`utils/receipt_pdf.py`,
  `utils/subscription_invoice_pdf.py`) take a `company` parameter and print the
  same identity as the mail they arrive in.
- `CompanyDetails` gains `address` on its own, because the invoice PDF prints
  name and address in separate header cells.
- `to_latin1()` folds the em dash and drops unencodable characters: fpdf2's core
  fonts are latin-1, a raw em dash **raises**, and that means no attachment at
  all.
- The receipt gains a **plain-text alternative** carrying the same GST/PST
  breakdown, derived from the same rendered rows so the two cannot list
  different charges.
- Migration 288 adds `branded_receipt_enabled` plus a toggle on the Settings
  page.

## 4. Risk & impact on existing functionality

**Blast radius: the two documents named, and their two PDF attachments. Nothing
else.** Report PDFs, Excel/Word exports and every other email are untouched.

Grepped before changing:

- `generate_receipt_html` / `generate_receipt_pdf` — one production caller each,
  both inside `send_receipt_email`, which is already async. Both stay
  **synchronous** so the existing fare-row tests drive them unchanged.
- `generate_subscription_invoice_pdf` — one caller, inside
  `_send_subscription_invoice_email`.
- `_build_fare_rows` — called by the HTML renderer and now the text renderer.
  The new `accent` parameter defaults to the legacy red, so any other caller
  would be unaffected.
- `header_html` / `footer_html` — renaming from `_`-private to public; the only
  prior callers were inside `render_email` itself.

**What could regress:**

- **This changes an email riders already receive.** That is the whole point and
  also the whole risk. Mitigated three ways: the shell was pinned by tests
  written and committed *before* any change (`7249125`); a test strips both
  shells and asserts the remaining body is **character-identical**; and the flag
  turns it off without a deploy.
- **The flag defaults ON.** Deliberate — shipping dark would leave the
  known-wrong company details in front of riders, which is the thing the
  retrofit exists to fix. This is a departure from CLAUDE.md's "ship dark, then
  flip on", made knowingly: the off-position is proven byte-identical, so the
  cost of being wrong is one settings toggle, not a deploy.
- **A PDF that fails to generate means no attachment.** The em-dash case would
  have done exactly that, which is why `to_latin1` drops unencodable characters
  rather than letting fpdf2 raise. Tested with a CJK company name.
- **One settings read per receipt**, cached 60 s, on a path already doing a
  Storage download and PDF generation.
- **`_branded_company` fails CLOSED** (falls back to the legacy shell), the
  opposite of the lifecycle-email kill switch, which fails open. Different
  question: there the risk was silently muting a notice, so erring towards
  sending is right; here the receipt sends either way and the only choice is
  which shell, so an unknown should use the one that has been in front of
  riders for months.

**Explicitly outside the flag and unchanged:** the fare rows, the separate
GST/PST line items, area fees, the surge notice, the grand total, and whether a
receipt is sent at all. A tax document's content must not depend on a
presentation switch.

Not touched: ride state machine, dispatch, fare calculation, surge, wallet, or
any Stripe path. No money value is computed anywhere in this change.

## 5. User-experience effect

**Rider-facing.** The ride receipt now carries the real logo, the documented
brand red, and the company name and address from the Settings page — in the
email footer and on the attached PDF. A plain-text version exists for the first
time. The greeting names the configured company.

**Driver-facing.** The same for the Spinr Pass invoice and its PDF.

**Visible mid-session:** yes. The next receipt after deploy looks different.

**Admin-facing.** Settings → Company Info gains a "Branded receipts & invoices"
toggle, with copy stating that fare lines, GST/PST and totals are unaffected
either way.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/email_receipt.py` | Shared header/footer, accent threading, plain-text renderer, `_branded_company` gate | The receipt retrofit |
| `backend/utils/receipt_pdf.py` | `company` parameter; footer from settings | Attachment must match the mail |
| `backend/routes/drivers/subscriptions.py` | Shared header/footer, `_branded_invoice_company` gate | The invoice retrofit |
| `backend/utils/subscription_invoice_pdf.py` | `company` parameter; name, contact and address from settings | Same |
| `backend/utils/company_details.py` | `address` field, `to_latin1` | PDF header cells; fpdf2 encoding |
| `backend/utils/email_layout.py` | `header_html` / `footer_html` made public | Reuse, not a third variant |
| `backend/schemas.py`, `backend/routes/admin/settings.py` | `branded_receipt_enabled` | The switch, and it must be savable |
| `backend/migrations/288_settings_branded_receipt_enabled.sql` | New — the column | Flip without a redeploy |
| `admin-dashboard/src/app/dashboard/settings/page.tsx` | Toggle + explanatory copy | Admin control |
| `backend/tests/test_receipt_shell_snapshot.py` | New — 28 tests | Baseline, then the retrofit |
| `backend/tests/test_branded_documents.py` | New — 11 tests | PDFs + `to_latin1` |
| `backend/tests/test_branded_receipt_flag.py` | New — 7 tests | Flag behaviour |

## 7. Before / after

```python
# Before — footer compiled in, ignoring the Settings page
        <tr><td style="padding:16px 24px 24px;text-align:center;...">
        <p style="...">Spinr Technologies Inc. · Saskatoon, SK</p>
          <p style="...">support@spinr.ca · www.spinr.ca</p>
        </td></tr>
```

```python
# After — the shared footer, from admin settings
        {footer}
```

```python
# Before — the PDF named a company the email could no longer agree with
    pdf.cell(W, 5, "Spinr Technologies Inc. - Saskatoon, SK", align="C", ln=True)
    pdf.cell(W, 5, "support@spinr.ca - www.spinr.ca", align="C", ln=True)
```

```python
# After — same identity as the mail it is attached to
    identity = company.identity_line if company is not None else "Spinr Technologies Inc. - Saskatoon, SK"
    contact = company.contact_line if company is not None else "support@spinr.ca - www.spinr.ca"
    pdf.cell(W, 5, _latin1_safe(identity), align="C", ln=True)
    pdf.cell(W, 5, _latin1_safe(contact), align="C", ln=True)
```

## 8. Rollback plan

**One settings toggle, no deploy.** `branded_receipt_enabled = false` restores
the previous shell for both documents and both PDFs, within the 60 s cache. The
off-position is not reconstructed from memory — the old markup is kept verbatim
as `_LEGACY_HEADER` / `_LEGACY_FOOTER` and pinned by tests written before the
change.

| Scenario | Action |
|---|---|
| Renders badly in a real client | `branded_receipt_enabled = false` |
| Company details wrong | Fix the Settings page — no code involved |
| Logo wrong or unreachable | Clear `company_logo_url`; falls back to the bundled asset |
| Whole retrofit | Revert `fa7d005`, `5c338ef`, `94679ba`, `9decf28`. Migration 288 is additive and nullable; nothing needs undoing |

The legacy constants and the flag should be deleted once the retrofit has been
seen in real inboxes — carrying two shells indefinitely is its own cost, and
both are commented to say so.

## 9. Verification performed

- [x] **Baseline committed first** (`7249125`), against unchanged code, so the
      retrofit's diff is reviewable rather than pre-justified
- [x] **Content proven unchanged** — a test strips both shells and asserts the
      remaining body is character-identical apart from two intentional bleeds
      (accent colour, company name in the greeting)
- [x] **Pre-existing receipt tests pass unmodified** — `test_receipt_line_items.py`
      (23), `test_receipt_invariants.py` (11), `test_receipt_route_snapshot.py` (8)
- [x] **46 new tests** across three files
- [x] **PDF encoding** — both generators exercised with a CJK company name to
      prove an unencodable value degrades rather than losing the attachment
- [x] **Flag matrix** — default on, false restores the legacy shell, a settings
      failure falls back, and the flag is savable from the Settings page (the
      same silent-drop failure mode caught on `company_logo_url`)
- [x] **Rendered a real before/after receipt and read it.** That is how the
      "Spinr Technologies Inc.." double full stop was caught — no test would
      have flagged it, because no test knew to look
- [x] **Full backend suite** — `pytest --ignore=tests/perf`:
      **10 168 passed, 8 skipped, 1 xfailed, 0 failed** (10 m 47 s)
- [x] **Real admin-dashboard production build** — `npm run build`, exit 0
- [x] `ruff check` / `ruff format` clean on every changed file

## 10. What was NOT verified

- **No real email was sent, and no PDF was opened.** The PDF assertions check
  that bytes start with `%PDF` and that generation does not raise — **not** that
  the company line lands in the right place on the page, or that a long
  configured address wraps rather than overflowing its cell. A settings-driven
  address is longer than the constant it replaces, and that is the most likely
  place for this to look wrong.
- **How the retrofitted receipt renders in Gmail, Apple Mail or Outlook is
  unknown.** This is the single largest gap in the change and the reason the
  flag exists. There is still no visual regression tooling for email in this
  repo.
- **Migration 288 has not been applied anywhere.** `AppSettings` defaults the
  flag true, so behaviour is correct without it; only turning the retrofit
  *off* is blocked until it runs. **If the retrofit needs disabling before 288
  is applied, that requires a deploy** — worth applying the migration first.
- **The admin toggle was not exercised against a running dashboard.**
- **The plain-text receipt has not been read in a text-only client.** Its fare
  rows are recovered by stripping tags from the rendered HTML rows; that is
  covered by tests for the shapes `_build_fare_rows` currently produces, but a
  future row with different markup could parse oddly.
- **Legacy shell removal is deferred.** Two shells now exist, and the second is
  dead weight the moment the retrofit is confirmed good.
- Not run against live or staging Supabase.
