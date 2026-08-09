# Change Impact & Risk Log — Every email on the shared branded shell

Fifth and final entry in the email series that began 2026-08-08, after
`2026-08-08-driver-lifecycle-email-channel.md` (infrastructure + driver),
`2026-08-08-rider-lifecycle-emails.md` (rider),
`2026-08-08-email-company-identity-from-settings.md` (settings-driven identity)
and `2026-08-08-receipt-invoice-branding-retrofit.md` (receipt + invoice).

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-09 |
| Author | Claude Code (session-driven) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | this commit |
| Related issue or gap ID | User report: "Spinr Technologies Inc.." appeared on a receipt, and every email must carry the name and address from the admin dashboard Settings page |

## 1. Issue / gap identified

Two problems, one of which the user found in a rendered email:

1. **`Spinr Technologies Inc..`** — the receipt copy read
   `Thanks for riding with {name}.`, and the configured legal name already ends
   in a period. The first fix was local to the receipt, which was the wrong
   shape: the name is now settings-driven and reaches many templates, so the
   same double period was waiting in every one that ends a sentence with it.

2. **Nine senders still bypassed the branded layout.** The previous four
   entries branded the driver lifecycle, rider lifecycle, receipt and invoice.
   Left behind: the corporate sign-in code, the member invite, the KYB
   decision, the self-serve signup ops alert, the admin broadcast, the T4A/DSAR
   export emails, the driver earnings statements, the corporate low-balance
   alert and the safety-team alerts. Those went out as bare `<p>` tags or plain
   text — no logo, no configured company name, no mailing address.

A third defect surfaced while fixing the second: `render_from_text`'s docstring
claimed a single newline inside a paragraph "keeps its shape", and in the
plain-text alternative it did — but HTML collapses a newline to a space, so the
same paragraph rendered as one run-on line. Caught by carrying the DSAR file
manifest across, not by a test.

## 2. Root cause

1. Punctuation: the rule "strip before appending" was implemented at one call
   site as inline logic. Nothing carried it to the next template, and this is
   not the class of thing review catches.

2. Coverage: branding was applied sender-by-sender, so "is this one branded?"
   had no answer short of reading every file. Six of the nine reach recipients
   through `features.send_email` with a plain-text body only — a shape no
   per-sender retrofit was going to converge on.

3. Line breaks: `_esc()` escapes for HTML but says nothing about whitespace
   semantics, so a value that looked right in `.text` silently differed in
   `.html`.

## 3. Fix / remediation

**Punctuation** — `CompanyDetails.name_sentence` (`utils/company_details.py`):
`self.name.rstrip(".") + "."`. Templates that end a sentence with the name use
it instead of restating the rule. The two remaining bare-`name` uses (the logo
`alt` text, the invoice PDF header cell) are labels, not sentences, and
correctly keep whatever the admin configured.

**Coverage** — two mechanisms rather than nine edits:

- `email_layout.render_from_text(heading, body)` wraps an already-written
  plain-text body in the shared shell, splitting paragraphs on blank lines. It
  gives a sender the logo and the configured company details without editing a
  word of its copy.
- `features.send_email` calls it automatically when a caller passes `body` but
  no `html`. That brands the six indirect senders at once, and brands the next
  one added by default rather than by remembering.

The three senders whose copy justified real structure got `render_email`
directly, with a CTA button: the corporate member invite, the DSAR export, and
the corporate sign-in code.

**Line breaks and alignment** — `email_layout._esc_multiline` escapes first,
then converts `\n` to `<br>` and runs of spaces to `&nbsp;`, so the HTML matches
the text alternative. `<br>` rather than `white-space:pre-line` because
Outlook's Word rendering engine handles that property inconsistently, and a list
that un-wraps in one major client is the failure being avoided. Escaping runs
first, so caller text can forge neither.

The space-run half was found by checking what the newly-HTML-ified senders
actually contain: the safety-team incident alert is column-aligned
(`Ride ID:   …`) and the driver statement indents its totals. Both were read as
plain text before this commit, where the alignment held. Adding an HTML part
that collapsed it would have made those two emails *worse* than sending none —
the failure mode of branding something without reading what it says.

**Structural guard** — `tests/test_all_emails_are_branded.py` walks every `.py`
under `backend/` for an `await …send_email(` call and asserts each file either
renders through `utils/email_layout` or appears in an allowlist with a written
reason. A second test fails if an allowlist entry no longer sends email, so the
exemptions cannot quietly become fiction. Verified to bite: a probe file with an
unbranded send fails it.

## 4. Risk & impact on existing functionality

**`features.send_email` is the shared component here** — blast radius grepped
rather than assumed. Callers passing `body` and no `html`, all now branded:

| Caller | Email |
|---|---|
| `utils/driver_statement_job.py` | weekly/monthly driver earnings statement |
| `routes/admin/driver_statements.py` | admin-triggered statement resend |
| `utils/corporate_low_balance.py` | corporate wallet low-balance nudge |
| `routes/drivers/tax_exports.py` (×4) | T4A summary, earnings export, statement, DSAR |
| `features.notify_safety_team` | safety-team incident alert, fanned out to `safety_alert_emails` |

Callers that already pass their own `html` are untouched — the branch is
`if html is None and body`. The ride receipt and Spinr Pass invoice build their
own HTML and are unaffected by this commit.

Risks considered:

- **A rendering failure could block an email that used to send.** Mitigated:
  `_branded_html_from_text` is wrapped in a blanket try/except returning `None`,
  and the caller then sends the plain-text body exactly as before. Several of
  these carry tax documents and safety alerts — a plain email beats no email.
- **`<br>` conversion changes existing output.** Only for paragraphs that
  actually contain a newline. No pre-existing sender passed one; the DSAR
  manifest added in this commit is the first.
- **The admin broadcast now escapes admin-authored text**, which the previous
  bare `<h2>{title}</h2><p>{description}</p>` did not. This is a behaviour
  change in the safe direction — a broadcast is the one place an admin's free
  text reaches every rider or driver at once — but an admin who was relying on
  embedding HTML in a broadcast will now see it as literal text. No evidence of
  that usage exists; flagged here because it is user-visible.
- **Marketing email is deliberately excluded.** `utils/marketing_email.py` has
  its own CASL footer (sender identity, physical mailing address, working
  unsubscribe) which is a legal requirement with its own shape; wrapping it in
  the transactional shell would duplicate or bury that footer. Recorded as an
  exemption with that reason, tracked as N16.
- **Dead code removed**: `_build_export_link_email_text` had no callers once
  the layout produced both representations. Left in place it would have been a
  second, unbranded builder sitting beside the live one — the exact confusion
  `utils/receipt_email.py` vs `utils/email_receipt.py` already causes (X4).

## 5. User experience effect

- **Riders and drivers**: the DSAR export, T4A summary, earnings export and
  earnings statement emails now arrive with the Spinr logo and the configured
  company name and mailing address. A PIPEDA access request answered by an
  unbranded email containing a link to "your personal data" is hard to
  distinguish from a phishing attempt; this fixes that.
- **Corporate users**: the sign-in code and member invite — both unsolicited
  emails containing sign-in links — now look like Spinr sent them.
- **Anyone who receives a receipt**: `Spinr Technologies Inc..` no longer
  appears. This is visible to riders mid-session, since receipts send at fare
  settlement.
- **Admins**: broadcast emails render on the branded shell.
- No push copy, timing, or delivery changed. No new email is sent that was not
  being sent before — this changes appearance only.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `backend/utils/company_details.py` | added `CompanyDetails.name_sentence` | one place holds the "don't double the period" rule |
| `backend/utils/email_receipt.py` | HTML + text use `name_sentence` | removes the inline rule and the shipped defect |
| `backend/utils/email_layout.py` | added `render_from_text`, `_esc_multiline` | bridge for plain-text senders; line breaks survive into HTML |
| `backend/features.py` | `send_email` renders `body` through the layout when no `html` | brands six senders at once, and the next one by default |
| `backend/routes/auth.py` | corporate OTP renders via `render_email` | a verification code must look trustworthy |
| `backend/routes/corporate_company.py` | member invite via `render_email` + CTA | unsolicited email with a sign-in link |
| `backend/routes/corporate_accounts.py` | KYB decision via `render_from_text` | adds `html` alongside existing `text` |
| `backend/routes/corporate_signup.py` | ops alert via `render_from_text` | consistency; internal recipient |
| `backend/routes/admin/messaging.py` | broadcast via `render_from_text` | branding **and** escaping of admin free text |
| `backend/routes/drivers/tax_exports.py` | `_build_export_link_email` replaces the split HTML/text builders; `_EXPORT_MANIFEST` extracted | one source for both representations; keeps the file manifest an access request should carry |
| `backend/routes/drivers/__init__.py` | re-exports follow the rename; dead text builder dropped | keeps `__all__` honest |
| `backend/tests/test_all_emails_are_branded.py` | **new** — structural guard | "all emails are branded" stops being a claim and becomes a check |
| `backend/tests/test_company_details.py` | `name_sentence` cases | the shipped defect gets a test |
| `backend/tests/test_email_layout.py` | `render_from_text` + line-break cases | new surface, and the HTML/text divergence |

## 7. Before / after

**Punctuation** (`utils/email_receipt.py`):

```python
# before — rule restated inline, at one call site
brand_name = company.name
brand_sentence = brand_name.rstrip(".") + "."

# after — rule lives on the type, every template gets it
brand_sentence = company.name_sentence
```

Rendered: `Thanks for riding with Spinr Technologies Inc.. Here's your receipt.`
→ `Thanks for riding with Spinr Technologies Inc. Here's your receipt.`

**Generic sender** (`backend/features.py`):

```python
# before — a plain-text body went out with no shell at all
return await send_transactional_email(to=to, subject=subject, html=html, text=body, ...)

# after
if html is None and body:
    html = await _branded_html_from_text(body, subject)
return await send_transactional_email(to=to, subject=subject, html=html, text=body, ...)
```

**Broadcast** (`routes/admin/messaging.py`):

```python
# before — unbranded, and admin free text interpolated raw into markup
html = f"<h2>{title}</h2><p>{description}</p>"

# after
rendered = await render_from_text(heading=title, body=description)
```

## 8. Rollback plan

Appearance-only, no data written and no schema touched, so a revert is clean
and needs no migration rollback.

- **Fastest partial rollback, no deploy**: `branded_receipt_enabled = false` in
  `app_settings` returns the receipt and invoice to their legacy shell,
  including the sentence this commit fixed.
- The rest has no flag, because there is no half-state worth having: the
  alternative to a branded email here is the bare `<p>` it replaced. A
  `git revert` of this commit restores that exactly — no live data has been
  shaped by it.
- The failure mode most worth naming: if `load_company_details()` were to start
  failing, it returns the previously-hardcoded constants rather than raising, so
  emails keep sending with a correct-if-stale footer.

## 9. Verification performed

- `pytest tests/test_all_emails_are_branded.py` — 4 passed. **Verified the
  guard actually fails**: a probe module with an unbranded `await
  send_transactional_email(` was added, the test failed naming it, probe removed.
- `pytest tests/test_email_layout.py` (28) and `tests/test_company_details.py`
  (23) — passed, including new `name_sentence`, `render_from_text` and
  line-break cases.
- Targeted sweep `-k "email or receipt or dsar or tax or export or layout or
  branding or company_details"` — 728 passed, 1 skipped.
- **Full backend suite: 10,187 passed, 8 skipped, 1 xfailed** (11m08s). It
  caught one real defect in this batch: `features.py` logs through loguru,
  which formats with `str.format`, and the `%s` written into the
  branding-fallback warning emitted the placeholder literally while dropping
  the exception — leaving the one signal that an email went out unbranded with
  no reason attached. Fixed to `logger.opt(exception=True)` and the suite's
  `test_loguru_call_conventions.py` re-run green.
- `ruff check` / `ruff format` clean on every file this branch changes. (31
  pre-existing ruff findings exist elsewhere in `backend/`; none in changed
  files, none introduced here.)
- **Manually rendered** the DSAR email against stubbed settings and read both
  the HTML and the text output — logo URL absolute, `alt` carrying the
  configured name, footer showing `Spinr Technologies Inc. — 230 22nd St E,
  Suite 300, Saskatoon SK S7K 0E9`, manifest keeping one item per line in both
  representations. This is the check that caught the original double period, so
  it is deliberately part of the process now rather than incidental.
- No `npm run build`: no frontend file is touched by this commit.

## 10. What was NOT verified

- **No real inbox render.** Gmail, Apple Mail and Outlook rendering is reasoned
  about, not observed. Specifically unverified: that `<br>` inside a `<p>`
  survives Outlook's Word engine as intended, and that the logo loads once a
  sender is trusted. There is still **no visual-regression tooling for email in
  this repo** — a standing gap, not something to re-discover
  next session (N12).
- **Not run against live or staging Supabase.** `get_app_settings` is stubbed
  throughout; a real settings row has never fed a real send in this branch.
- **The three settings migrations (286, 287, 288) remain unapplied** in every
  environment. Until they run, `load_company_details()` takes its fallback path
  and emails carry the previously-hardcoded footer — correct, but not
  settings-driven. Applying them is the step that makes the user's request take
  effect in production.
- **Product name in prose is still literal "Spinr"** — "Open the Spinr driver
  app", "your Spinr wallet". This is deliberate, not an oversight: the settings
  field holds the *legal entity* name, and "Open the Spinr Technologies Inc.
  driver app" reads badly. Settings drive the footer identity, the mailing
  address, the logo, the logo's alt text, and sentence-final legal-name usage.
  A rebrand of the product name itself would need a separate `company_app_name`
  setting and a copy sweep; filed as N17.
- **The admin-broadcast escaping change is untested against real admin usage** —
  no data exists on whether any admin has embedded HTML in a broadcast.
