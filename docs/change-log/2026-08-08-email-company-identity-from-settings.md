# Change Impact & Risk Log — Email company identity from admin settings

Third in the 2026-08-08 email series, after
`2026-08-08-driver-lifecycle-email-channel.md` (infrastructure + driver) and
`2026-08-08-rider-lifecycle-emails.md` (rider).

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-08 |
| Author | Claude Code (session-driven) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | `e54754c`, `751736e`, `36e183f` |
| Related issue or gap ID | User request: company name/address in emails must come from the admin Settings page, and the logo must be sourced the same way |

## 1. Issue / gap identified

The branded email layout shipped earlier today hardcoded its footer from two
constants borrowed from `utils/report_branding.py`:

```
Spinr Technologies Inc. - Saskatoon, SK
support@spinr.ca - www.spinr.ca
```

That is defensible for a report PDF filed once, but wrong for email. The legal
name, mailing address, support address and website are exactly the fields that
change without a deploy — a move, a rebrand, a new support alias — and an email
carrying a stale mailing address is a compliance problem, not a cosmetic one.

Meanwhile the admin Settings page already had a "Company Info (shown in apps)"
card holding all of it, feeding `/api/company-info` to the rider and driver
apps. Emails were the one customer-facing surface ignoring it.

The logo was the one piece of that identity with no setting behind it at all:
it stayed a deploy-time decision while everything beside it was a
settings-page decision.

## 2. Root cause

The layout module was written to reuse `report_branding`'s constants
deliberately — the stated goal at the time was that email and report PDFs
"cannot drift". That reasoning was right about consistency and wrong about
which direction consistency should flow: it pinned email to a source that
requires a code change, rather than to the one an operator already edits.

## 3. Fix / remediation

New `backend/utils/company_details.py` — one loader, `load_company_details()`,
returning a resolved `CompanyDetails` (name, identity line, contact line,
support address, logo URL) read from the `settings` row.

- `email_layout.render_email` is now **async** and loads identity itself.
  Loading inside rather than asking each call site to pass it is deliberate: a
  caller that forgot would silently ship a stale footer, and nothing catches a
  missing keyword argument with a sensible default.
- The logo `<img>` and its `alt` text follow the configured name, so a rename
  does not leave "Spinr" behind in the one place nobody looks.
- **Body copy that names a support address now uses the same configured value
  as the footer**, so the two cannot disagree. On the driver side that meant
  templating the copy maps with `{support}` and substituting at send time,
  because `action_message` / `status_message` are synchronous and read by
  admin routes.
- Migration 287 adds `company_logo_url`, plus a field on the Settings page.
  Blank = the bundled asset, which is the normal state, not an unfilled one.

**Every field falls back to the previously-shipped constant.** An unconfigured
setting reproduces today's output byte-for-byte.

## 4. Risk & impact on existing functionality

**Blast radius: the shared email layout and its three senders. Report PDFs,
Excel/Word exports and the live ride receipt are untouched.**

Grepped before changing:

- `render_email` — 3 callers (`driver_status_notifications`,
  `document_expiry`, `rider_emails`), all already async, all now `await`ing.
  Verified by grep that no un-awaited call remains anywhere under `utils/`,
  `routes/`, `services/`.
- `report_branding.COMPANY_LINE` / `COMPANY_CONTACT_LINE` — still used by the
  PDF/XLSX/DOCX header helpers, and still the fallback here. **Not modified.**
- `SettingsUpdateRequest` — the admin update model is an explicit field list.
  Adding one optional field cannot affect existing saves (`exclude_none=True`
  means an untouched field is omitted).
- `marketing_email.py` has its own near-identical `_coalesce` /
  `_postal_address`. **Deliberately left alone** — it sits on a CASL
  consent-critical path, and consolidating it is a separate change with its own
  review. The duplication is noted in `company_details`' docstring.

**What could regress:**

- **A misconfigured settings row now reaches customers.** This is the real
  cost of the change: an admin who types a wrong address puts it on every
  email. That is the point of the feature, but it moves a class of error from
  code review to a settings page with no approval step.
- **`render_email` became async** — a signature change on a module three
  senders depend on. Nothing outside those three uses it, but a future sync
  caller will now fail loudly rather than silently, which is the right failure.
- **`email_layout` is no longer a leaf module.** It now transitively imports
  `settings_loader` → `db_supabase` → `supabase_client`. Verified `import
  server` and all new importers still resolve with no circular import.
- **One settings read per rendered email**, cached 60 s in-process by
  `get_app_settings`, and senders that need the support address for copy pass
  the loaded object through so it is read once, not twice.
- **The logo URL is admin-supplied and lands in an `<img src>`** sent to riders
  and drivers. `_safe_logo_url` accepts only absolute `http(s)`; anything else
  — `javascript:`, `data:`, a relative path — logs a warning and falls back to
  the bundled asset. The value is also HTML-escaped like any other.
- **Admin-dashboard change** — one new input plus two helper paragraphs on the
  Settings page. No state or API shape change beyond the added field.

Not touched: ride state machine, dispatch, fare calculation, surge, money
movement, push notifications, or the receipt/invoice/statement email paths.

## 5. User-experience effect

**Rider- and driver-facing.** Every email rendered through the shared layout —
driver approval/rejection/suspension/ban, document expiry, and the eight rider
emails — now shows the company name, address, support address, website and
phone from the Settings page, and the logo from there if one is configured.
With settings unfilled the output is identical to before.

**Admin-facing.** Settings → Company Info gains an "Email logo URL" field, and
the Address field gains a line noting that name and address now appear in
every transactional email footer — a consequence previously invisible from the
page.

**Visible mid-session:** an admin edit propagates within the 60 s settings
cache, so an email sent a minute later carries the new details.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/company_details.py` | New — the loader, assembly and URL guard | One source for email identity |
| `backend/utils/email_layout.py` | `render_email` async; header/footer from settings; `render_text` takes an optional identity | Footer and logo follow the settings page |
| `backend/utils/rider_emails.py` | Each sender resolves identity, uses it for the support address, passes it through | Body copy matches the footer |
| `backend/utils/driver_status_notifications.py` | `{support}` placeholder in the copy maps, substituted at send time | Same, without making the sync lookups async |
| `backend/utils/document_expiry.py` | `await render_email` | Async signature |
| `backend/schemas.py` | `company_logo_url` on `AppSettings` | Default before the migration lands |
| `backend/migrations/287_settings_company_logo_url.sql` | New — the column | Change the logo without a redeploy |
| `backend/routes/admin/settings.py` | `company_logo_url` on `SettingsUpdateRequest` | Without it the UI field silently never saves |
| `admin-dashboard/src/app/dashboard/settings/page.tsx` | Logo URL field + two explanatory lines | Admin control |
| `backend/tests/test_company_details.py` | New — 17 tests | Fallbacks, assembly, URL guard |
| `backend/tests/test_admin_settings_company_logo.py` | New — 9 tests | The silent-drop failure mode |
| `backend/tests/test_email_layout.py` | Rewritten for async + settings-driven branding | — |

## 7. Before / after

```python
# Before — footer pinned to constants that need a deploy to change
def _footer_html() -> str:
    return f"""
        <tr><td ...>
          <p ...>{_esc(COMPANY_LINE)}</p>
          <p ...>{_esc(COMPANY_CONTACT_LINE)}</p>
        </td></tr>"""
```

```python
# After — from the admin Settings page, constants as fallback
def _footer_html(company: CompanyDetails) -> str:
    return f"""
        <tr><td ...>
          <p ...>{_esc(company.identity_line)}</p>
          <p ...>{_esc(company.contact_line)}</p>
        </td></tr>"""
```

```python
# Before — logo and alt text hardcoded
<img src="{logo_url()}" alt="Spinr" ...>
```

```python
# After — both follow settings, so a rename carries everywhere
<img src="{_esc(company.logo_url)}" alt="{_esc(company.name)}" ...>
```

## 8. Rollback plan

**No flag of its own; the existing one still covers the emails.**

- `app_settings.lifecycle_emails_enabled = false` still suppresses every email
  routed through the policy layer, unchanged by this work.
- To revert only the *identity source* and go back to constants:
  `git revert 751736e` (layout + senders), leaving the loader, the migration
  and the admin field harmlessly in place.
- To remove the logo setting entirely: `git revert 36e183f e54754c`, then
  `ALTER TABLE public.settings DROP COLUMN IF EXISTS company_logo_url;`. Safe
  at any time — with the column gone, emails fall back to the bundled asset.

**The fastest operational fix needs no deploy at all:** a wrong footer is a
wrong settings row, so clearing the offending field restores the shipped
constant within 60 s. That is a better rollback than any revert here.

Migration 287 is additive and nullable, so nothing needs undoing to deploy or
to roll back the code.

## 9. Verification performed

- [x] **New tests** — 26 across 2 new files, plus `test_email_layout.py`
      rewritten (21 tests) for the async signature and settings-driven branding
- [x] **Fallback parity** — asserted that empty settings reproduce
      `COMPANY_LINE` and `COMPANY_CONTACT_LINE` exactly. **This caught a real
      regression**: seeding the contact line with the default support address
      made the list non-empty even with nothing configured, so the fallback
      never fired and `www.spinr.ca` silently disappeared from every footer
- [x] **Logo URL guard** — parametrised over `javascript:`, `data:`, relative
      paths, `ftp:` and whitespace; all fall back to the bundled asset
- [x] **Escaping** — company name and logo URL come from an admin-editable
      field, so a test drives markup through both
- [x] **Every `render_email` call awaited** — grep across `utils/`, `routes/`,
      `services/`
- [x] **Rendered both paths end to end** through a real sender
      (`send_welcome_email`) with the settings loader driven directly:

      UNCONFIGURED → logo `…/api/v1/branding/spinr-logo.png`, alt `Spinr`,
      body "support@spinr.ca", footer
      `Spinr Technologies Inc. - Saskatoon, SK` / `support@spinr.ca - www.spinr.ca`
      — byte-identical to what shipped this morning.

      CONFIGURED → logo `https://cdn.spinr.ca/brand/logo.png`, alt
      `Spinr Technologies Inc.`, body "help@spinr.ca", footer
      `Spinr Technologies Inc. — 220 3rd Ave S, Saskatoon SK S7K 1M1` /
      `help@spinr.ca · https://spinr.ca · +1 306 555 0100`. Confirms the body's
      support address and the footer's move together.
- [x] **No circular import** — `import server` plus every new importer resolves
- [x] **Targeted sweep** — `-k "email or notification or document or
      driver_status or admin_drivers or branding or receipt or rider"`:
      **1244 passed, 1 skipped, 0 failed**
- [x] **Full backend suite** — `pytest --ignore=tests/perf`:
      **10 125 passed, 8 skipped, 1 xfailed, 0 failed** (8 m 14 s)
- [x] **Real admin-dashboard production build** — `npm run build`, exit 0. Not
      `tsc --noEmit`
- [x] `ruff check` / `ruff format` clean on every changed file

## 10. What was NOT verified

- **No real email was sent**, and no mail client has fetched an
  admin-configured logo URL. Whether a third-party CDN image loads through
  Gmail's image proxy is untested — the bundled-asset path is the one that was
  verified end to end, and it remains the default.
- **Migration 287 has not been applied anywhere.** Low risk: `AppSettings`
  defaults the field, so the code behaves correctly without it; only saving a
  logo URL is blocked until it runs.
- **The admin field was not exercised against a running dashboard.** The build
  passes and the request model is unit-tested, but nobody typed a URL into the
  form and watched an email change. There is no visual/E2E tooling for the
  settings page in this repo.
- **`marketing_email.py` still carries its own copy of the address-assembly
  helpers.** Consolidating them would touch a CASL consent-critical path, so it
  is deliberately deferred rather than done quietly here.
- **The live ride receipt and Spinr Pass invoice still use their own hardcoded
  footers**, per the scoping decision — so until the N11 retrofit, two Spinr
  emails can legitimately show different company details if an admin edits the
  settings. That is a known, chosen inconsistency, not an oversight.
- **A misconfigured settings row now reaches customers with no approval step.**
  Worth a second pair of eyes on the Company Info card before the first send.
- Not run against live or staging Supabase.
