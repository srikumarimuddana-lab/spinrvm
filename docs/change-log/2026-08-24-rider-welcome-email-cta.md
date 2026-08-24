# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-24 |
| Author | Claude Code (backend) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | rides (rider-facing account/lifecycle email) |
| PR / commit link | (branch: `claude/welcome-email-review-mwrjn6`) |
| Related issue or gap ID | Content review requested in this session (not a filed issue) |

## 1. Issue / gap identified

The rider welcome email (`utils/rider_emails.py::send_welcome_email`, sent
once on first profile completion) stated a value proposition (0% commission,
transparent pricing) but never asked the rider to act on it — no CTA, no
orientation on how to actually book, and a subject line that repeated the
heading verbatim.

## 2. Root cause

Never designed as an activation email. It was originally built (2026-08-08
branch, R4 in `docs/notification-channel-coverage.md`) primarily to close a
different gap — no email ever confirmed a rider's address on file actually
worked — and inherited a receipt-style tone rather than an activation one.

## 3. Fix / remediation

- Added an optional `(label, url)` CTA parameter to `rider_emails._send()`,
  threaded through to `email_layout.render_email`'s existing (previously
  unused by this module) `cta` support.
- `send_welcome_email()` now renders a "Book your first ride" button when a
  new setting, `company_app_download_url`, is configured — and renders
  without one when it isn't, rather than shipping a placeholder/broken link.
- Added one orientation sentence ("set a pickup and drop-off, see the price
  before you confirm") and split the subject from the heading.
- New setting `company_app_download_url` (schemas.AppSettings, the admin
  settings update model, and a Settings page field) — an admin-configurable
  smart/universal link, validated the same way `company_logo_url` already is
  (absolute http(s) only; anything else — a typo, a `javascript:` paste —
  degrades to "no CTA", never an unsafe href).

## 4. Risk & impact on existing functionality

- **Blast radius: rider welcome email only.** `_send()`'s new `cta` parameter
  defaults to `None`, so every other sender in `rider_emails.py`
  (`send_email_changed_notice`, `send_account_deletion_notice`,
  `send_refund_email`, `send_wallet_topup_email`, `send_no_show_fee_email`,
  `send_payment_blocked_email`, `send_email_verification_code`) is
  unaffected — grepped every call site in the module and confirmed none of
  the others pass `cta`.
- `email_layout.render_email`'s `cta` parameter already existed (used by
  `routes/corporate_company.py`'s invite email and
  `routes/drivers/tax_exports.py`'s DSAR export email) — this change adds a
  third caller, no change to that shared function itself.
- `company_app_download_url` is a new, independent settings field with its
  own default (`""` → `None` after validation). Grepped for other readers of
  `company_details.CompanyDetails` — nothing else constructs it positionally
  (all existing call sites use keyword args or only read the fields they
  need), so adding a new field with a default does not break any existing
  caller.
- Until an admin configures the new setting, the email is functionally
  identical to today's button-less version except for the subject line and
  one added sentence — no regression risk from an unset field.
- No existing behavior is removed: the 0%-commission/GST-PST paragraph, the
  support-email line, and the TRANSACTIONAL classing are all unchanged.

## 5. User-experience effect

- **Rider-facing.** Every rider who completes profile setup now sees a
  "Book your first ride" button (once the URL setting is configured) and one
  additional orientation sentence in their welcome email. Not visible
  mid-session to an existing user — this is a one-time email sent once per
  account, at account creation.
- Subject line changed from "Welcome to Spinr" to "Welcome to Spinr —
  drivers keep 100% of your fare" — still legitimately describes the email,
  differentiates it in an inbox dominated by generic "Welcome to X" copy.
- Copy reviewed in this session's earlier content-review pass before
  implementation; the CTA URL itself was explicitly *not* invented — the
  user chose to add a real, admin-configurable setting rather than guess a
  deep-link scheme or hardcode a placeholder.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/schemas.py` | Added `company_app_download_url: str = ""` to `AppSettings` | New setting, defaults to unconfigured |
| `backend/utils/company_details.py` | Added `app_download_url: Optional[str] = None` field + `_safe_app_download_url()` validator, wired into `load_company_details()` | Resolves the setting with the same http(s)-only safety rule as the logo URL |
| `backend/routes/admin/settings.py` | Added `company_app_download_url: Optional[str] = None` to `SettingsUpdateRequest` | Without this the UI field silently fails to save (same failure class `test_admin_settings_company_logo.py` pins for the logo URL) |
| `admin-dashboard/src/app/dashboard/settings/page.tsx` | Added an input field under "Company Info (shown in apps)" | Lets an admin set/clear the CTA link |
| `backend/utils/rider_emails.py` | `_send()` gained an optional `cta` param; `send_welcome_email()` passes a CTA when configured, adds one orientation sentence, splits subject from heading | The actual fix |
| `backend/tests/test_company_details.py` | Added coverage for `app_download_url` (default None, configured, unsafe-URL fallback, whitespace) | Mirrors the existing logo-URL test block |
| `backend/tests/test_admin_settings_company_app_download_url.py` | New file, mirrors `test_admin_settings_company_logo.py` | Pins the request-model wiring so the setting is actually savable |
| `backend/tests/test_rider_emails_app_name.py` | Updated two subject-string assertions and one body-copy assertion for the new welcome-email text | Existing test exercised exact copy that changed |
| `backend/tests/test_rider_account_emails.py` | Added 3 tests: CTA absent when unconfigured, CTA present when configured, CTA omitted for an unsafe URL | New coverage for the CTA behavior itself |

## 7. Before / after

```python
# Before (utils/rider_emails.py::send_welcome_email)
    return await _send(
        company=company,
        user_id=user["id"],
        user=user,
        subject=f"Welcome to {company.app_name}",
        heading=f"Welcome to {company.app_name}",
        paragraphs=[
            f"Your account is ready. You can book a ride from the {company.app_name} app whenever you need one.",
            f"{company.app_name} is Saskatchewan-built and takes 0% commission — every dollar of the fare "
            "goes to your driver. You'll always see the full price before you confirm a ride, "
            "with GST and PST shown as separate line items on your receipt.",
            f"Questions any time: {company.support_email}",
        ],
        email_type="rider_welcome",
    )
```

```python
# After
    return await _send(
        company=company,
        user_id=user["id"],
        user=user,
        subject=f"Welcome to {company.app_name} — drivers keep 100% of your fare",
        heading=f"Welcome to {company.app_name}",
        paragraphs=[
            f"Your account is ready. Open the {company.app_name} app, set a pickup and drop-off, "
            "and you'll see the full price before you confirm — no surprises at drop-off.",
            f"{company.app_name} is Saskatchewan-built and takes 0% commission — every dollar of the fare "
            "goes to your driver. GST and PST are shown as separate line items on your receipt.",
            f"Questions any time: {company.support_email}",
        ],
        cta=("Book your first ride", company.app_download_url) if company.app_download_url else None,
        email_type="rider_welcome",
    )
```

## 8. Rollback plan

Purely additive at the schema/settings layer (new field, defaults to blank —
identical to today) and a single-file behavior change at the copy layer.
`git revert` fully reverts both. No feature flag added: until an admin sets
`company_app_download_url`, the email's only visible change is the subject
line and one sentence — there is no destructive or hard-to-undo state
(no migration, no data write beyond the email send itself). If the CTA
specifically needs to be turned off without a deploy, clearing the
`company_app_download_url` setting via the admin dashboard achieves that
immediately (email renders without a button, per §3/§4).

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_company_details.py
      tests/test_admin_settings_company_app_download_url.py
      tests/test_rider_emails_app_name.py tests/test_rider_account_emails.py
      tests/test_all_emails_are_branded.py tests/test_email_layout.py
      tests/test_receipt_shell_snapshot.py tests/test_driver_welcome_email.py`
      — 146 passed, 0 failed, 0 skipped.
- [x] `ruff check` and `ruff format --check` on all changed Python files —
      clean.
- [x] Blast-radius grep performed — confirmed no other `rider_emails.py`
      sender passes `cta`, no other reader of `CompanyDetails` breaks on the
      new field, and `render_email`'s existing `cta` support (already used by
      two other senders) is unmodified.
- [ ] Manual repro in staging / real inbox render — **not performed** (no
      staging access from this session). No automated visual-regression
      tooling exists for email in this repo (standing gap, documented in
      `docs/notification-channel-coverage.md`).
- [ ] `npm run build` for `admin-dashboard` — **not performed** from this
      session; the added Settings page field is a small, isolated JSX/Input
      addition following the exact pattern of the adjacent "Email logo URL"
      field, no new dependency or type change.

## What was NOT verified

- No real SES/Resend send was exercised — all tests mock the policy layer,
  per this repo's existing convention.
- The CTA button's actual click-through target (`company_app_download_url`)
  is unset by default; nobody has configured a real smart-link URL yet, so
  the button will not appear in production until an admin sets one. This is
  intentional (see §3) but means the CTA itself is unverified end-to-end
  against a real link.
- `admin-dashboard`'s production build was not run for the Settings page
  change — see checklist above.
