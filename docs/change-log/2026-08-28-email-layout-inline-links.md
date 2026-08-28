# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 |
| Author | Claude (session, on request) |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/driver-rider-emails-messages-pdf-bio6az` |
| Related issue or gap ID | — (ad hoc request) |

## 1. Issue / gap identified

Two separate problems, one real and one in a dev tool:

1. **Production:** `training.spinr.ca` in the driver welcome email rendered as
   plain grey body text. `email_layout` escapes every paragraph, so no email
   in the product could contain an inline hyperlink at all — the only link
   slot was the single CTA button. An address written without a scheme is
   also the form several mail clients decline to auto-link, so the one action
   the email asks a new driver to take was not clickable.
2. **Dev tool:** `backend/scripts/preview_notification_templates.py` (the QA
   preview added earlier in this branch) hardcoded a **fabricated** street
   address — "123 2nd Ave N, Saskatoon, SK S7K 2C6" — in its stand-in for
   `CompanyDetails`. Production reads these from the admin `settings` row;
   the preview invented one because it has no DB access.

## 2. Root cause

1. `_esc_multiline` escapes paragraph text (correctly — admin-authored
   suspension reasons flow through these templates), and nothing offered a
   way to opt a known phrase back into markup.
2. The preview script needed *something* in the footer and I supplied a
   plausible-looking placeholder rather than the real fallback. A made-up
   address in a preview of a real email is worse than no address: it looks
   authoritative and is not.

## 3. Fix / remediation

**1. Inline links in `utils/email_layout.py` (additive, opt-in).**
New optional `links={visible phrase: url}` parameter on `render_email`,
applied by a new `_linkify()` **after** escaping. `driver_emails.py` passes
`{"training.spinr.ca": "https://training.spinr.ca"}`; the host is now a
module constant `_TRAINING_HOST` so the copy and the href cannot drift.

Link colour is a new `LINK_BLUE = "#1D4ED8"` token. This is **not** an
invented brand colour: it is the palette's Info blue (`#3B82F6`, per
`shared/theme/index.ts` / `.claude/context/brand-spinr.md`) darkened for
contrast, following the exact precedent of `BRAND_RED_CONTRAST`. `#3B82F6`
on white is 3.7:1 and fails WCAG AA for body text; `#1D4ED8` is 6.7:1 and
passes — which matters because customer-facing surfaces are held to
WCAG 2.1 AA (CLAUDE.md → Saskatchewan Regulatory → Accessibility).

**2. Preview script reads real settings.** It now fetches the `settings` row
(`id='app_settings'`) directly from the Supabase REST API using only the
standard library (`urllib`), since it cannot import `settings_loader` without
the full backend dependency chain. With no `SUPABASE_URL` /
`SUPABASE_SERVICE_ROLE_KEY` it falls back to **exactly** what
`utils/company_details.py` falls back to, including rendering **no address
block at all** (matching `footer_html`'s `address_lines or (...)` fallback)
rather than substituting anything. The PDF cover page now states which of the
two it used, so a reader can never mistake fallback values for configured ones.

## 4. Risk & impact on existing functionality

- **`email_layout.py` is a shared module every transactional email renders
  through** — the highest-blast-radius file touched in this branch. The
  change is therefore strictly **additive and opt-in**: `links` defaults to
  `None`, and when falsy `_linkify` is never called, so the emitted HTML for
  every email that does not pass `links` is **byte-identical** to before.
  That is what preserves the whole-document snapshots in
  `tests/test_email_snapshots.py` (`layout_minimal`, `layout_full`,
  `render_from_text`) and `test_receipt_shell_snapshot.py` — none of them
  pass `links`, so none should move. CLAUDE.md prefers additive rollout over
  a flag for a shared component; an opt-in parameter already has the
  "off by default" property a flag would provide.
- Blast-radius grep performed: `_body_html` has exactly one caller
  (`render_email`), `_linkify` exactly one (`_body_html`), and
  `utils/driver_emails.py` is the only non-test module passing `links`.
- **Injection risk considered and closed.** `_linkify` runs *after*
  escaping (escaping afterwards would defeat it) and escapes both the phrase
  and the URL on the way into the anchor. Both halves are call-site
  constants, never recipient- or admin-authored input. A regression test
  (`test_linkify_cannot_inject_markup_from_paragraph_text`) pins that a
  `<script>` in paragraph text stays inert while linkification is active —
  worth having because these templates do carry admin-written free text
  (suspension/rejection reasons) elsewhere.
- Plain-text alternative unchanged by design: it already shows the address as
  written, so `render_text` takes no `links` and emits no markup.

## 5. User-experience effect

- **Driver-facing.** In the welcome email, `training.spinr.ca` is now a blue
  underlined link to `https://training.spinr.ca` instead of grey text. No
  other email changes. Not visible mid-session (one-time registration email).
- The link target is the same host the admin driver-training integration
  already points at (`lms_api_base_url`, see
  `tests/test_admin_driver_training.py`), so the address in the email and the
  system behind it are the same one — not a copy-only URL.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/email_layout.py` | Added `LINK_BLUE`, `_linkify()`, and an opt-in `links` param on `render_email`/`_body_html` | Enable inline hyperlinks without changing any existing email |
| `backend/utils/driver_emails.py` | Added `_TRAINING_HOST` constant; passes `links` for the training address | Make the training URL clickable and keep copy/href in sync |
| `backend/tests/test_email_layout.py` | 4 new tests: anchor rendering, opt-in no-op, colour token, injection safety | New behavior in a shared module needs a regression net |
| `backend/scripts/preview_notification_templates.py` | Reads the real `settings` row via stdlib HTTP; removed the fabricated address; mirrors `_linkify`; cover page states the data source | Preview must show real configured values, never invented ones |

## 7. Before / after

```
# Before — utils/email_layout.py, paragraph rendering
f'<p style="...">{_esc_multiline(para)}</p>'
```

```
# After — linkification only when the caller opts in
body = _esc_multiline(para)
if links:
    body = _linkify(body, links)
f'<p style="...">{body}</p>'
```

```
# Before — preview script, fabricated
address_lines = ("123 2nd Ave N", "Saskatoon, SK  S7K 2C6")
```

```
# After — read from the admin settings row, else production's own fallback
COMPANY = Company(_fetch_settings_row())   # {} when creds absent → no address block
```

## 8. Rollback plan

`git revert` this commit. Pure rendering/copy change: no migration, no data
written, no money or ride-state path touched. Reverting restores the
pre-existing paragraph rendering exactly, since the change is additive.

## 9. Verification performed

- [ ] Automated tests run — **not run.** No network access to install the
      backend dependency chain in this environment (confirmed earlier this
      session: pip install fails, pypi.org returns 403). The 4 new tests were
      written to match `test_email_layout.py`'s existing fixture/idiom and
      compile-checked, but have not been executed. **CI is the first place
      they actually run** — flagged plainly rather than implied.
- [x] Compile check — `python3 -m py_compile` on all four changed files.
- [x] Manual render verification — regenerated the preview and confirmed in
      the emitted HTML that: the fabricated address appears **0** times; the
      training anchor renders as
      `<a href="https://training.spinr.ca" style="color:#1D4ED8;...">`; and
      the document contains exactly **1** `<a href` in total, proving no
      other email picked up a link.
- [x] Blast-radius grep — `_body_html`, `_linkify`, and `links=` callers
      enumerated (see §4).
- [x] Contrast computed, not guessed — `#3B82F6` 3.7:1 (fails AA),
      `#1D4ED8` 6.7:1 (passes) against white.
- [x] Reviewed against CLAUDE.md — additive-over-destructive for a shared
      component; WCAG AA for a customer-facing surface; escaping discipline
      preserved.

## 10. What was NOT verified

- **Snapshot tests were not executed.** The additive design means they
  *should* be byte-identical, and that reasoning is stated above, but it was
  verified by construction and grep, not by a green test run. If CI shows a
  snapshot diff, that expectation was wrong and the diff is the thing to
  trust.
- **The settings-fetch path was never executed against a real database** —
  no credentials exist in this environment, so only the fallback branch ran.
  The URL shape (`/rest/v1/settings?id=eq.app_settings`) and the address-field
  merge were written to mirror `settings_loader.get_app_settings()` and
  `utils/address_format`, but the live HTTP call is unproven. It is failure-
  tolerant by construction (any exception → fallback + a stderr note), so the
  worst case is the preview quietly using fallbacks — which the cover page
  then says out loud.
- **Not rendered in a real mail client.** Link colour and underline were
  verified in headless Chromium only; Outlook's Word renderer in particular
  can restyle anchors, and no email-client testing tool exists in this repo.
- No visual-regression tooling covers email (ACTION_ITEMS.md N12 is closed by
  the snapshot tests, which are HTML-diff, not visual).

## Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — shared module, enumerated callers
- [x] No silent behavior change: opt-in parameter, existing emails byte-identical
