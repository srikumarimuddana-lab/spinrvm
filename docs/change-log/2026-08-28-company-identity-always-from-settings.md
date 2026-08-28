# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 |
| Author | Claude (session, on request) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/driver-rider-emails-messages-pdf-bio6az` |
| Related issue or gap ID | supersedes the "report branding stays fixed" decision (2026-08-08 change log, ADR 008) |

## 1. Issue / gap identified

Company identity (legal name, address, support email, website) was read from
admin Settings for **email**, but hardcoded for **generated documents**. With
Settings configured as `Regina,SK`, every PDF/Excel report footer still
printed `Spinr Mobility Inc. - Saskatoon, SK`.

Verified against the live database this session (`settings` row
`id='app_settings'`): `company_address = "Regina,SK"`,
`branded_receipt_enabled = true`. So the divergence was live, not theoretical.

## 2. Root cause

Two causes, one technical and one a policy that has now been reversed.

**Technical:** `fpdf2` and `openpyxl` are synchronous; `get_app_settings()` is
async. A sync render could not await a settings read, so the builders used
module constants. `render_branded_pdf_footer` accepted an optional
`company_lines` parameter for async callers to thread the identity through,
but only `driver_statement_pdf.py` actually passed it — every other report
silently fell back to the constants forever.

**Policy (reversed by the requester):** `utils/company_details.py`'s docstring
and the 2026-08-08 change log deliberately scoped settings-driven identity to
email only, on the reasoning that admin reports filed with SGI and airport
authorities should not shift under an admin's keystroke. The requester has
now directed the opposite: *"always the values should take from the admin
setting … if we change the address we don't want to touch these all email and
other things."* This entry records that reversal explicitly rather than
letting it look like an oversight — the earlier decision was deliberate and
is being overridden, not corrected.

## 3. Fix / remediation

A synchronous read path to the same settings, so no report needs its own
copy of the identity and no caller has to be edited when the address changes:

1. `settings_loader.get_cached_app_settings()` — sync, returns the existing
   60 s cache if warm, else `None`. **Never triggers a load and never
   blocks**: a sync render must not do I/O it cannot await.
2. `company_details.build_company_details(settings)` — the assembly logic
   extracted from `load_company_details()` as a pure function, and
   `load_company_details_cached()` — the sync entry point. Async and sync now
   **share one assembly**, so they cannot drift.
3. `report_branding._resolved_company_lines()` — used by both the PDF footer
   and the Excel footer. Imported lazily inside the function because
   `company_details` imports `report_branding` for the fallback constants; a
   module-level import would be circular.
4. `COMPANY_LINE` lost its city: `"Spinr Mobility Inc. - Saskatoon, SK"` →
   `"Spinr Mobility Inc."`. Reached only on a cold cache. A stale hardcoded
   city is *actively wrong* once the company moves; the legal name alone is
   merely incomplete.

## 4. Risk & impact on existing functionality

- **Blast radius: every generated PDF/Excel report footer**, which is the
  point of the change. Enumerated consumers of the footer helpers:
  `t4a_pdf.py`, `corporate_statement_pdf.py`, `dispute_evidence_pdf.py`,
  `driver_statement_pdf.py` (the one that already passed `company_lines` —
  unaffected, an explicitly-passed value still wins).
- **Staleness bound is 60 s**, identical to what every ride, fare and email
  already tolerates — this introduces no new staleness class.
- **Cold-cache path** (first render after a restart, before anything else has
  read settings) falls back to constants. Narrow in practice: settings are
  read by essentially every request path. Degrades to an incomplete-but-not-
  wrong footer.
- **Never raises.** `_resolved_company_lines` catches everything and falls
  back — a report that renders with a slightly stale footer beats a report
  that fails. Pinned by `test_resolution_never_raises`.
- **Latin-1 safety confirmed.** `identity_line` joins name and address with an
  em dash, which fpdf2's core fonts cannot encode. The footer already wraps
  its output in `pdf_safe()`, which folds `—` → `-`. Verified by running the
  real `pdf_safe` against the real settings row: output
  `Spinr Mobility Inc - Regina,SK  |  support@spinr.ca · https://spinr.ca`
  (the `·` is U+00B7, inside latin-1, so it survives).
- **Existing tests reference `COMPANY_LINE` by symbol, not by literal**
  (`test_company_details.py:31,64,101`, `test_email_layout.py:137,185`), so
  changing the constant's value does not break them. Checked before editing.

## 5. User-experience effect

- **Internal-admin and driver/corporate-facing documents.** Report, statement
  and T4A-adjacent PDF/Excel footers now show the configured company and
  address — Regina, per current Settings — instead of Saskatoon.
- Not visible mid-session; documents are generated on demand.
- Already-generated documents are unchanged (rendered at generation time).
- **Operationally:** changing the company address is now a Settings edit
  alone. No deploy, no code change, for email *or* documents.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/settings_loader.py` | Added `get_cached_app_settings()` — sync, non-blocking, TTL-respecting | Give sync renderers a legitimate read path |
| `backend/utils/company_details.py` | Extracted `build_company_details()`; added `load_company_details_cached()` | One assembly shared by sync and async, so they cannot drift |
| `backend/utils/report_branding.py` | Added `_resolved_company_lines()`; PDF + Excel footers use it; `COMPANY_LINE` city removed | Reports follow Settings; stale city can no longer be printed |
| `backend/tests/test_company_details.py` | 3 tests: cold cache, sync/async parity, purity | New sync path needs a regression net |
| `backend/tests/test_report_branding.py` | 4 tests: warm resolution, cold fallback, no-location constant, never-raises | Pin the actual bug (Regina vs Saskatoon) |

## 7. Before / after

```
# Before — report footer, ignores Settings unless a caller threads it through
identity, contact = company_lines or (COMPANY_LINE, COMPANY_CONTACT_LINE)
COMPANY_LINE = "Spinr Mobility Inc. - Saskatoon, SK"
```

```
# After — Settings first, constants only on a cold cache
identity, contact = company_lines or _resolved_company_lines()
COMPANY_LINE = "Spinr Mobility Inc."     # last resort; deliberately no city
```

## 8. Rollback plan

`git revert` this commit. Presentation-only: no migration, no persisted data,
no money or ride-state path. Reverting restores the constants and the
parameter-only behaviour exactly. No data written that a revert would strand.

## 9. Verification performed

- [x] **Live database read** — confirmed the actual `settings` row via the
      Supabase connector, so the premise ("Settings says Regina") is verified
      fact, not assumption.
- [x] **Output verified against the real settings row** — ran the real
      `pdf_safe` transformation over the real values and confirmed the footer
      renders `Spinr Mobility Inc - Regina,SK …` with the em dash folded and
      no "Saskatoon" anywhere.
- [x] Compile check on all five changed files.
- [x] Blast-radius grep — every consumer of the footer helpers and every test
      referencing `COMPANY_LINE` enumerated (see §4).
- [x] Circular-import hazard identified and handled with a lazy import.
- [ ] **Automated tests not executed.** No network access to install the
      backend dependency chain (pip fails; pypi.org returns 403 — confirmed
      repeatedly this session). The 7 new tests were written to match each
      file's existing idiom and compile-check, but **CI runs them first.**

## 10. What was NOT verified / still outstanding

- **No PDF or Excel file was actually rendered.** `fpdf2`/`openpyxl` are not
  installable here. The footer *string* was verified against real settings;
  the rendered document was not. CI/staging is the first real render.
### Follow-up commit (same branch) — remaining live call sites

Now also settings-driven:

| File | Change |
|---|---|
| `backend/utils/t4a_pdf.py` | New `_payer_identity()`; payer name, payer address and support email all read Settings. No hardcoded identity remains in the file. |
| `backend/utils/subscription_invoice.py` | New `_invoice_company()`; the admin invoice download/resend now passes `company`, matching the driver's own copy. |

`t4a_pdf` falls back to the legal name with **no address** on a cold cache —
a blank-but-honest address beats printing a city the company may have left,
and this is a CRA slip. Values are `pdf_safe`d because Settings is free text
and fpdf2's core fonts are latin-1 only. **Note the trade-off this creates:**
the payer on a tax slip is now editable from the admin UI, so Settings must
be kept accurate — that is the direct consequence of the requested policy,
not an accident.

### Deliberately NOT changed

- **`utils/email_receipt.py`'s `_LEGACY_FOOTER` and
  `routes/drivers/subscriptions.py`'s `_LEGACY_INVOICE_FOOTER`.** These are
  the pre-retrofit shells reached only when `branded_receipt_enabled` is
  **false**; that flag is `true` in the live settings row (verified this
  session), so they are dormant. Their entire purpose is to reproduce the old
  output exactly, and they are pinned byte-for-byte by
  `test_receipt_shell_snapshot.py` (`assert normalised == legacy_body`) and
  `test_text_receipt_falls_back_to_the_legacy_footer_without_a_company`.
  Making them dynamic would break those tests and defeat what they exist for.
  The branded path they fall back *from* is fully settings-driven, so no live
  document is affected.
- **`services/data_transfer/sgi_form_filler.py` (`_COMPANY_NAME`).** Fills a
  regulator's own fixed-format document (SGI D00032/D00033). Making it
  dynamic is defensible, but a form submitted to a regulator is not somewhere
  to change behaviour without someone confirming SGI accepts it. **Needs a
  decision — flagged, not changed.**
- Assorted support-email mentions in AI prompt text and support FAQ copy
  (`ai/prompts.py`, `routes/support.py`, `ai/public_assistant.py`). These are
  conversational copy, not document identity; sweeping them is a separate,
  larger change.
- The cold-cache window is reasoned about, not measured. No test asserts how
  often it is actually hit in production.

## Sign-off

- [x] Rollback plan is concrete
- [x] Blast radius stated and enumerated, not assumed
- [x] Reversal of a prior deliberate decision recorded explicitly, with who
      directed it and why
- [x] Remaining hardcoded sites named rather than quietly left
