# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Claude Code (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | backend, rider-app, admin-dashboard, shared (component used by rider-app/driver-app) |
| Domain (Sentry tag) | payments (T4A/receipts/invoices are payment-adjacent financial documents) |
| PR / commit link | branch `worktree-agent-a9a8b425f2d5ae90e`, commits `d69e097..ff781e0` (not pushed / no PR opened yet) |
| Related issue or gap ID | ACTION_ITEMS.md A42 |

## 1. Issue / gap identified

The codebase used the wrong Spinr legal entity name — **"Spinr Technologies
Inc."** — in ~39 files, including CRA-filed T4A tax slips, rider-facing
receipts and subscription invoices, admin-generated compliance reports, and
every published legal/policy document (Terms of Service, Privacy Policy,
Corporate MSA, etc.). The confirmed, correct legal entity name is **"Spinr
Mobility Inc."**, confirmed directly by the product owner.

## 2. Root cause

Not fully knowable from the code alone, but the history is partially
recoverable from `docs/change-log/`: `backend/utils/report_branding.py`'s
`COMPANY_LINE` constant said "Spinr Mobility Inc." as recently as
2026-07-30, when a report-header-footer consistency pass
(`docs/change-log/2026-07-30-report-header-footer-consistency-pass.md`)
noticed it disagreed with `receipt_pdf.py`'s "Spinr Technologies Inc." and
"fixed" the *disagreement* by making `report_branding.py` match
`receipt_pdf.py` — i.e. by propagating the wrong name rather than
correcting it. That earlier pass correctly identified an inconsistency but
picked the wrong side to converge on, because nothing in the code marked
which name was authoritative. Where `receipt_pdf.py` itself first acquired
"Spinr Technologies Inc." is not established by any change-log entry found
in this session — it predates 2026-07-30 and was already the majority
spelling across the codebase by then. One thing the test suite does confirm
independently: `backend/tests/test_company_details.py` (left unmodified,
see §6) carries a comment recording that a receipt shipped to a real user
reading "Spinr Technologies Inc.." — i.e. this wrong name is not purely a
docs/code-comment problem, it reached production output at least once.

## 3. Fix / remediation

Replaced every occurrence of "Spinr Technologies Inc." (and the one
all-caps fallback "SPINR TECHNOLOGIES INC.") with "Spinr Mobility Inc." /
"SPINR MOBILITY INC." across 39 files: backend PDF/email generators and
their static fallback constants, `backend/schemas.py` doc comments,
`admin-dashboard`'s ride invoice PDF export, `rider-app`'s shareable
receipt HTML, the shared `SupportScreen` component, all `docs/legal/*.md`
policy documents, the Stripe payment-dispute-evidence runbook, and test
fixtures/golden snapshot files that hardcoded the old name.

`backend/utils/report_branding.py`'s `COMPANY_LINE` constant is corrected
back to "Spinr Mobility Inc." — **not** silently re-flipped a second time.
An inline comment now records the 2026-07-30 history (which direction was
wrong and why) so a future pass doesn't repeat the same mistake a third
time.

Explicitly **not** touched, per task scope:
- `ACTION_ITEMS.md` (another process consolidates all parallel tracks' status into it)
- The 5 pre-existing `docs/change-log/*.md` entries that quote the wrong
  name in a before/after snippet or bug description — these are historical
  records of what the code/a user report actually said at the time; editing
  them would falsify history. (`2026-08-09-email-header-uber-format.md`,
  `2026-08-08-receipt-invoice-branding-retrofit.md`,
  `2026-08-09-all-emails-on-shared-branded-shell.md`,
  `2026-08-08-email-company-identity-from-settings.md`,
  `2026-07-30-report-header-footer-consistency-pass.md`)
- `backend/tests/test_company_details.py`'s parametrized
  sentence-final-name test cases and the `_load({"company_name": "Spinr
  Technologies Inc."})` case — these use the string as arbitrary example
  *input* an admin might type (proving `name_sentence`/`_coalesce` behavior
  generically), not as an assertion that it's the correct default, and one
  of them documents the real production incident referenced in §2.
- `backend/services/data_transfer/sgi_form_filler.py` — already correct,
  confirmed as the reference spelling, left untouched.
- Everything under the strict scope boundary in the task (compliance
  export, driver/booking import services, admin driver routes, driver-app,
  admin-dashboard outside `ride-invoice.tsx`) — not touched, by instruction.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface but shallow — every change is a literal
string replacement, no logic, schema, or type-shape change.**

- `backend/utils/report_branding.py`'s `COMPANY_LINE` / `COMPANY_CONTACT_LINE`
  are read by every "branded" report type in `REPORT_FORMAT_REGISTRY`
  (GST/PST remittance, DSAR lookup, T4A filer handoff, SGI/Knight Archer
  insurance billing, airport trips, driver roster) via
  `render_branded_pdf_footer()` and `_finalize_worksheet()`'s Excel footer.
  Grepped for all callers — all are report-generation call sites in
  `backend/services/data_transfer/` and admin report routes; none parse or
  branch on the footer string's content, they only render it. No caller was
  found that does anything but display the string.
- `backend/utils/company_details.py`'s `CompanyDetails.name_sentence`
  property is used by every template that ends a sentence with the company
  name (grepped: `email_receipt.py`, `email_layout.py` callers). Its
  docstring example string changed; the actual logic (`rstrip(".") + "."`)
  is untouched.
- `backend/utils/t4a_pdf.py`'s `"Payer / Issuer"` field and footer
  disclaimer are static strings baked into the PDF at generation time —
  read by nothing downstream except the rendered PDF bytes themselves (no
  DB column, no parser reads this string back).
- `backend/routes/drivers/subscriptions.py`'s `_LEGACY_INVOICE_FOOTER` and
  `backend/utils/subscription_invoice_pdf.py`'s equivalent are both
  **fallback-only** constants — `company.identity_line` /
  `company.name` (from `load_company_details()`, sourced from the
  `app_settings` admin-configured value) take precedence whenever an admin
  has configured company info; the hardcoded string only renders when
  nothing is configured. Same pattern for `email_receipt.py`'s
  `_LEGACY_FOOTER`.
- No ride state, wallet balance, Stripe charge, or DB row is written or
  read differently by any of these changes — this is purely a rendered/
  displayed string.

**Nothing else regresses.** No shared component/hook used by more than the
files listed here reads this string as data (only ever renders it as
literal display text).

## 5. User-experience effect

- **Rider-facing**: any newly generated receipt (PDF or email, from
  `receipt_pdf.py` / `email_receipt.py` / `rider-app/app/ride-details.tsx`'s
  shareable HTML) will now show "Spinr Mobility Inc." instead of "Spinr
  Technologies Inc." wherever the admin hasn't configured a custom company
  name in Settings. **Visible mid-session** to a rider generating/viewing a
  receipt for a ride they're actively completing, though it is cosmetic
  text only — no functional or pricing change.
- **Driver-facing**: subscription invoices (`routes/drivers/subscriptions.py`,
  `utils/subscription_invoice_pdf.py`) and the shared Support screen
  (`shared/components/SupportScreen.tsx`, used by both rider-app and
  driver-app) show the corrected name.
- **Corporate-admin / internal-admin facing**: the admin ride-invoice PDF
  export (`ride-invoice.tsx`) and every admin-generated compliance report
  (GST/PST remittance, insurance audits, airport trips, driver roster via
  `report_branding.py`) now print the corrected name.
- **All users**: every published legal document (`docs/legal/*.md`) that
  gets surfaced to end users (ToS, Privacy Policy, etc.) names the correct
  entity going forward.
- This is a pure copy correction — no new field, no new validation, no
  changed pricing/state behavior. No feature flag used; the change is not
  behind `app_settings` because the string is a hardcoded fallback that
  only fires in the *absence* of admin configuration, and the correction
  itself is not something that should be conditionally rolled out (a wrong
  legal name is unconditionally wrong).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/t4a_pdf.py` | "Payer / Issuer" field + footer disclaimer string | CRA-filed T4A slip legal name |
| `backend/utils/receipt_pdf.py` | fallback `identity` string | Rider receipt PDF footer |
| `backend/utils/email_receipt.py` | `_LEGACY_FOOTER` + fallback footer line | Rider receipt email footer |
| `backend/utils/subscription_invoice_pdf.py` | header name, disclaimer sentence, footer line, `_LEGACY_INVOICE_FOOTER` | Driver subscription invoice PDF |
| `backend/routes/drivers/subscriptions.py` | `_LEGACY_INVOICE_FOOTER` HTML | Driver subscription invoice email |
| `backend/utils/company_details.py` | 2 docstring examples | Doc accuracy only, no logic change |
| `backend/utils/report_branding.py` | `COMPANY_LINE` constant + explanatory comment | Every admin-generated report footer |
| `backend/schemas.py` | doc comment (2-line wrapped example) | Doc accuracy only |
| `admin-dashboard/.../ride-invoice.tsx` | `doc.text(...)` literal | Admin ride invoice PDF export |
| `rider-app/app/ride-details.tsx` | footer `<p>` literal in `buildReceiptHtml` | Rider shareable receipt HTML |
| `shared/components/SupportScreen.tsx` | fallback company name literal | Support screen (rider + driver apps) |
| `docs/legal/terms-of-service.md` | entity name | Legal document |
| `docs/legal/privacy-policy.md` | entity name | Legal document |
| `docs/legal/corporate-master-services-agreement.md` | entity name | Legal document |
| `docs/legal/independent-contractor-agreement.md` | entity name (2 occurrences) | Legal document |
| `docs/legal/website-terms-of-use.md` | entity name (2 occurrences) | Legal document |
| `docs/legal/careers-privacy-notice.md` | entity name | Legal document |
| `docs/legal/trademark-copyright-notice.md` | entity name (3 occurrences) | Legal document |
| `docs/legal/corporate-data-processing-addendum.md` | entity name | Legal document |
| `docs/legal/casl-marketing-consent-disclosure.md` | entity name (2 occurrences) | Legal document |
| `docs/legal/breach-notification-letter-template.md` | entity name (2 occurrences) | Legal template |
| `docs/runbooks/payment-dispute-evidence.md` | entity name | Stripe dispute-evidence runbook |
| `backend/tests/test_receipt_shell_snapshot.py` | assertions + `_COMPANY` fixture (6 occurrences) | Test now matches corrected fallback |
| `backend/tests/test_rider_emails_app_name.py` | docstring + fixture (3 occurrences) | Fixture consistency |
| `backend/tests/test_email_layout.py` | fixture `identity_line` | Fixture consistency |
| `backend/tests/test_admin_settings_company_app_name.py` | docstring example | Doc accuracy only |
| `backend/tests/test_document_expiry_app_name.py` | `_company()` fixture (2 occurrences) | Fixture consistency |
| `backend/tests/test_email_snapshots.py` | `_COMPANY` fixture (2 occurrences) | Fixture consistency |
| `backend/tests/test_tax_exports_app_name.py` | `_company()` fixture (2 occurrences) | Fixture consistency |
| `backend/tests/test_admin_support_tickets_routes.py` | fixture `identity_line` | Fixture consistency |
| `backend/tests/snapshots/email/*.{html,txt}` (8 files) | regenerated golden snapshots | Match corrected `test_email_snapshots.py` fixture |

## 7. Before / after

**T4A slip (`backend/utils/t4a_pdf.py`) — CRA-filed document:**
```python
# Before
label_value("Payer / Issuer", "Spinr Technologies Inc.")
...
"This slip is issued by Spinr Technologies Inc. for CRA reporting purposes.",
```
```python
# After
label_value("Payer / Issuer", "Spinr Mobility Inc.")
...
"This slip is issued by Spinr Mobility Inc. for CRA reporting purposes.",
```

**Rider receipt PDF (`backend/utils/receipt_pdf.py`):**
```python
# Before
identity = company.identity_line if company is not None else "Spinr Technologies Inc. - Saskatoon, SK"
```
```python
# After
identity = company.identity_line if company is not None else "Spinr Mobility Inc. - Saskatoon, SK"
```

**Rider receipt email (`backend/utils/email_receipt.py`):**
```python
# Before
'        <p style="color:#bbb;font-size:12px;margin:0;">Spinr Technologies Inc. · Saskatoon, SK</p>\n'
...
lines += ["Spinr Technologies Inc. · Saskatoon, SK", "support@spinr.ca · www.spinr.ca"]
```
```python
# After
'        <p style="color:#bbb;font-size:12px;margin:0;">Spinr Mobility Inc. · Saskatoon, SK</p>\n'
...
lines += ["Spinr Mobility Inc. · Saskatoon, SK", "support@spinr.ca · www.spinr.ca"]
```

**Report footer constant (`backend/utils/report_branding.py`) — the one
previously flipped backwards on 2026-07-30:**
```python
# Before
COMPANY_LINE = "Spinr Technologies Inc. - Saskatoon, SK"
```
```python
# After (with explanatory comment recording the 2026-07-30 history — see file)
COMPANY_LINE = "Spinr Mobility Inc. - Saskatoon, SK"
```

## 8. Rollback plan

No feature flag applies (see §5) — this is unconditional static text, not
a runtime-toggleable behavior. Rollback is a straight `git revert` of the
relevant commit(s) (all changes are pure string literals with no schema,
migration, or live-data mutation involved — `git revert` **is** sufficient
here, unlike money/state changes, because nothing on this diff touches
Stripe charges, wallet deltas, or ride state). If any T4A/receipt/invoice
has already been generated and delivered (emailed/downloaded) with the
wrong name before this fix, reverting the code does **not** un-send or
regenerate that already-issued document — see §9 for what was not
verified about that.

## 9. Verification performed

- [x] Automated tests run (unit): 365 tests across the touched-file test
  groups (T4A, receipts, subscription invoices, company_details,
  report_branding, email fixtures/snapshots, admin support tickets) — all
  pass. Commands used, per group, are recorded in the individual commit
  messages.
- [x] `ruff check` on every touched Python file — clean.
- [x] Email snapshot golden files regenerated via
  `SPINR_UPDATE_EMAIL_SNAPSHOTS=1 pytest tests/test_email_snapshots.py`
  and the diff manually inspected — confirmed only the entity-name string
  changed in each of the 8 files, no other markup drift.
- [x] `npx tsc --noEmit` run for `admin-dashboard` (after `npm ci`) —
  passes clean (0 errors) with `ride-invoice.tsx`'s change included.
- [ ] `npx tsc --noEmit` for `rider-app` (`ride-details.tsx`) — **NOT run**.
  `npm install` fails in this environment on a pre-existing, unrelated
  dependency-resolution error (`npm error code ETARGET — No matching
  version found for axios@1.18.1`, despite `package.json` pinning
  `^1.18.0`) before `node_modules` can be populated. Not caused by this
  change; not something this task should "fix" per CLAUDE.md's guidance
  against chasing pre-existing, unrelated CI/dependency failures.
- [ ] `shared/components/SupportScreen.tsx` — not independently
  type-checked for the same reason (no working `node_modules` for either
  consumer app in this environment); the edit is a single string-literal
  change with no type-shape impact.
- [ ] **No `npm run build` / production build was run** for
  `admin-dashboard`, `rider-app`, or any app — only `tsc --noEmit` for
  `admin-dashboard`. Per CLAUDE.md, a passing `tsc --noEmit` is explicitly
  not equivalent to a production build; this is a known verification gap
  for this change, not claimed as full coverage.
- [ ] No staging/production deploy check — not applicable, nothing was
  pushed or deployed; work is local commits in this worktree only.
- [x] Blast-radius grep performed: every reader of `report_branding.COMPANY_LINE`
  / `COMPANY_CONTACT_LINE`, every caller of `CompanyDetails.name_sentence`,
  and every other file containing "Spinr Technologies" (case-insensitive,
  repo-wide) — see §3/§4 for what was found and excluded.
- [x] Reviewed against CLAUDE.md convention: no money/state/RLS logic
  touched; this is a pure copy-only change so PIPEDA/observability
  conventions don't apply.
- [x] Not feature-flagged — justified in §5 (unconditional static text
  correction, not a toggleable behavior).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`; no live-data
  mutation involved).
- [x] Blast radius is stated, not assumed (§4) — cross-surface but
  shallow, every call site confirmed to only render/display the string.
- [x] No silent behavior change to an already-shipped flow — §5 states
  the UX effect explicitly (cosmetic text on receipts/invoices/reports,
  visible mid-session on receipt generation).

---

## Open question for the product owner — NOT decided here

**This fix does not determine whether any already-issued document needs
re-filing or re-notice.** Specifically:

1. **T4A slips**: if any T4A has already been filed with CRA or delivered
   to a driver bearing "Spinr Technologies Inc." as payer, this commit does
   **not** retroactively correct those already-issued slips, and whether a
   correction/amendment is legally required is a CRA-filing question this
   agent is not positioned to answer.
2. **Rider receipts**: `backend/tests/test_company_details.py`'s own
   comment records that a receipt shipped to a real rider reading "Spinr
   Technologies Inc.." (double-period bug, since fixed) — confirming the
   wrong name did reach at least one real production receipt at some point
   before 2026-08-09. Whether any population of already-sent receipts needs
   a corrective notice is not decided here.
3. **Legal documents** (`docs/legal/*.md`): if any of these were previously
   published/accepted by users under the "Spinr Technologies Inc." name,
   whether that constitutes a defect requiring re-consent or a corrective
   notice (versus a simple forward-fix) is a legal-sufficiency question,
   not a code question.

Flagging this explicitly rather than assuming silence means "no further
action needed."
