# Change Impact & Risk Log — Email header rebuilt on the Uber receipt format

Follows `2026-08-09-all-emails-on-shared-branded-shell.md`, which put every
email on the shared layout. This fixes the layout itself.

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-09 |
| Author | Claude Code (session-driven) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | PR #3466 |
| Related issue or gap ID | User report: "the email alert header is not proper", with a reference Uber Eats receipt; and "the red on the background of this logo makes the circle in logo not visible as that is also in red color" |

## 1. Issue / gap identified

**The logo was unreadable in its own header.** `static/branding/spinr_logo.png`
is a charcoal wordmark (`#202020`) whose "o" is a **red spiral** (`#E04020`).
The header band was full-strength brand red `#FF3B30`. Two failures at once:

- the red spiral sat on a red ground and disappeared — the mark's one
  distinguishing element became invisible;
- the charcoal wordmark on saturated red went muddy and low-contrast.

Beyond the colour, the layout did not read like a transactional receipt. The
headline sat down in the body under a centred logo, so the email did not say
what it was until after the chrome. There was no date block, no responsive
rule, and no dark-mode handling.

## 2. Root cause

The layout was designed around the brand colour rather than around the asset.
Nobody opened the PNG. A red band is a reasonable default for a brand whose
mark is a white knockout; it is exactly wrong for one drawn in charcoal with a
red accent, and the file has been in the repo the whole time.

The supporting mistake: **the previous change log recorded "no real inbox
render" as a known gap and left it there.** Rendering it locally would have
caught this in a minute, and this session's earlier defects (the double
period, the dropped website) were all caught the same way. Reasoning about
appearance is not a substitute for looking at it.

## 3. Fix / remediation

Rebuilt the shell on the structure of the reference Uber receipt:

| Element | Before | After |
|---|---|---|
| Header band | `#FF3B30` brand red | `#F2F2F2` light neutral — the ground the mark is drawn for |
| Logo | centred, 160px | left-aligned, 132px |
| Brand colour | the whole band | a 4px rule above the band, plus the CTA button |
| Headline | in the body, 22px | in the header, 30px display |
| Eyebrow | none | small caps label above the headline ("RIDE RECEIPT") |
| Date/time | none | optional right-aligned `meta_lines` |
| Footer | centred grey text, one comma-joined line | dark band; legal name on its own line, address lines beneath, contact below |
| Responsive | none | `max-width:620px` — padding 40→24px, headline 30→24px |
| Dark mode | none | page/card/body invert; **header pinned light** |
| Width | 520px | 600px |

Two deliberate constraints, both driven by the same asset problem:

- **The header stays light even in dark mode.** A dark band would erase the
  charcoal wordmark — the same failure as the red band from the other side.
  Fixing it properly needs a light-on-dark variant of the logo, which is a
  design decision for a person, not something to ship silently.
- **The logo is not repeated in the dark footer**, for the same reason. The
  footer carries the company name as text instead.

`CompanyDetails` gained `address_lines` so the footer can print the street and
locality on separate lines. The comma-joined `identity_line` is kept for the
plain-text alternative and the PDF header, where a single line is correct.

## 4. Risk & impact on existing functionality

**Blast radius: every email.** `render_email` is the single shell, and
`header_html`/`footer_html` are consumed directly by two more call sites.
Grepped rather than assumed:

| Consumer | How it uses the layout |
|---|---|
| `utils/email_receipt.py` | `header_html(company, "Ride Receipt")` + `footer_html(company)` |
| `routes/drivers/subscriptions.py` | `header_html(company, "Subscription Invoice")` + `footer_html(company)` |
| every other sender | via `render_email` / `render_from_text` |

`header_html`'s signature keeps `subtitle` positional precisely so those two
keep working; everything new is keyword-only.

Risks considered:

- **This changes emails already in inboxes.** In practice it does not: nothing
  on this branch is deployed, migrations 286–288 are unapplied, and the
  receipt/invoice retrofit is behind `branded_receipt_enabled` whose
  off-position still renders the untouched pre-retrofit shell, pinned verbatim
  by `test_receipt_shell_snapshot.py`. The redesign lands before first
  delivery, not under recipients mid-session.
- **`address_lines` changes a NamedTuple.** Added last with a `()` default, so
  every existing construction still works — including the test fixtures that
  broke the last time a field was added. `footer_html` falls back to the joined
  `address` when lines are absent, so a `CompanyDetails` built by older code
  renders correctly rather than losing its address.
- **`<style>` in `<head>` is new.** Inline styles remain the base for every
  element; the stylesheet only *overrides* (responsive padding, dark mode). A
  client that strips `<style>` — the documented worst case — gets exactly the
  desktop light rendering, not a broken one.
- **Dark-mode overrides could wash out body copy.** `.card p` is repainted to
  `#B4B4B4`; the CTA keeps white-on-red. Verified by screenshot, not reasoning.

## 5. User experience effect

Visible to every rider, driver, corporate user and admin who receives any
Spinr email. The Spinr mark is legible for the first time — previously its
defining element was invisible in the one place it was meant to be seen. The
email now leads with what it is, carries a date, adapts to phone widths, and
does not glare in dark mode.

No copy changed. No email is sent that was not being sent before. No push,
timing, or delivery behaviour changed.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `backend/utils/email_layout.py` | rebuilt the shell: light header band, brand rule, header-level headline, eyebrow, meta lines, dark footer, responsive + dark-mode stylesheet | the logo was illegible on the old band and the layout did not read as a receipt |
| `backend/utils/company_details.py` | added `address_lines` + `_address_lines()` | the footer prints the address one line per part |
| `backend/tests/test_email_layout.py` | updated the footer-shape tests; added header-structure regression tests | the footer format changed on purpose; the header defect gets a test |

## 7. Before / after

```python
# before — the band that hid the logo's red spiral
return f'''
    <tr><td style="background:{BRAND_RED};padding:28px 24px;text-align:center;">
      <img src="{company.logo_url}" alt="{company.name}" width="160" .../>
'''

# after — light ground, logo left, brand red demoted to a 4px rule
<tr><td style="background:#FF3B30;height:4px;...">&nbsp;</td></tr>
<tr><td style="background:#F2F2F2;padding:28px 40px 0;" class="px">
  ... <img src="..." alt="..." width="132" style="display:block;..."/>
```

Footer, from one run-on line to the receipt shape:

```
Spinr Technologies Inc. — 230 22nd St E, Suite 300, Saskatoon SK S7K 0E9
```
```
Spinr Technologies Inc.
230 22nd St E, Suite 300
Saskatoon SK S7K 0E9

support@spinr.ca · www.spinr.ca
```

## 8. Rollback plan

Appearance-only; no schema, no data, no migration. `git revert` is complete
and sufficient.

For the receipt and invoice specifically there is still a no-deploy switch:
`branded_receipt_enabled = false` in `app_settings` returns both to the
pre-retrofit shell.

The rest has no flag, and deliberately so: the alternative to this layout is
the one that made the logo unreadable. A flag whose off-position is the known
defect is not a rollback, it is a way to ship the defect again.

## 9. Verification performed

- **Rendered and looked at it** — the check that was missing when this defect
  shipped. Three screenshots at `deviceScaleFactor: 2` with the real PNG
  inlined, driven by headless Chromium: desktop 700px, mobile 380px, and dark
  mode via `colorScheme: 'dark'`. The red spiral is legible on the light band
  in all three.
- **Caught a real defect this way**: in dark mode the wrapper table kept its
  inline light background while `<body>` inverted, so the area below the card
  went dark and the strip around it stayed light. Fixed by putting the `page`
  class on the wrapper table. No test would have found that.
- New regression tests pin the fix: full-strength brand red appears exactly
  once as a background and only as the 4px rule; the nearest background before
  the logo is the light band; the headline precedes body copy; the footer
  prints address lines separately and falls back when they are absent; the
  responsive and dark-mode rules ship.
- `pytest tests/test_email_layout.py tests/test_company_details.py` — 60 passed.
- Sweep `-k "email or receipt or invoice or branding or company_details or
  document or corporate"` — **1,680 passed, 3 skipped**. This is what confirms
  the receipt and invoice still render through the changed `header_html` /
  `footer_html`.
- `ruff check` / `ruff format` clean on all changed files.

## 10. What was NOT verified

- **Still no real inbox render**, and this entry exists because that gap has
  now produced a visible defect once. A browser screenshot is a much better
  proxy than reasoning, but it is not Outlook's Word engine. Specifically
  unverified: whether `<style>`-block media queries survive Outlook desktop
  (they do not, by design — it falls back to the inline desktop rendering),
  and whether Gmail's dark-mode treatment matches Chromium's
  `prefers-color-scheme`. Gmail is known to apply its own colour inversion
  that ignores the media query on some clients; the header being pinned light
  is the mitigation, not a guarantee. Standing gap N12.
- **Not run against live or staging Supabase.** Settings are stubbed.
- **The 600px width and 132px logo size are judgement, not measurement** — no
  device matrix was tested beyond the two viewports above.
- **No light-on-dark logo variant exists**, so dark mode keeps a light header
  band rather than a fully inverted email. That is a design call left open
  rather than decided here — filed as N18.
