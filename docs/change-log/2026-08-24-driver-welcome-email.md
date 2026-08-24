# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-24 |
| Author | Claude Code (backend) |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | (branch: `claude/welcome-email-review-mwrjn6`) |
| Related issue or gap ID | D1, `docs/notification-channel-coverage.md` |

## 1. Issue / gap identified

Driver registration (`POST /drivers/register`, first-time create path) sends
no push and no email — a driver who just applied gets zero confirmation their
application was received and no orientation on what happens next.

## 2. Root cause

Never built. `docs/notification-channel-coverage.md` (D1) documents this as a
known, open gap: push is deliberately absent because the app itself shows the
next step in-session, but no email equivalent was ever added, unlike the
rider side (`send_welcome_email` in `utils/rider_emails.py`, fired on first
profile completion).

## 3. Fix / remediation

Added `send_driver_welcome_email()` in a new module, `utils/driver_emails.py`,
mirroring `rider_emails.py`'s pattern (renders through the shared branded
layout, routed through the existing `email_notifications` policy layer,
TRANSACTIONAL class). Wired into `routes/drivers/profile.py::register_driver`,
fired only on the create path (not on re-submission/update), backgrounded via
the existing `spawn()` helper so it cannot add latency or fail the
registration request.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** New module, new call site is purely additive
  (one `spawn()` call after the existing `insert_one`/response-building code,
  no existing statement touched or reordered). No existing sender, table
  schema, or background loop is modified.
- Grepped for other callers/importers of `register_driver` and of
  `utils/rider_emails.py`/`driver_status_notifications.py` (the two existing
  patterns this mirrors) — no other call site references the new module or
  function; nothing else can be affected by its addition.
- Reuses `email_notifications.send_lifecycle_email` — same policy layer every
  other lifecycle email goes through (kill switch, `can_email` gate,
  suppression handling). No changes made to that shared layer.
- `driver_status_notifications.py` (the status-transition-keyed sender) is
  untouched — this is a deliberately separate module because a driver has no
  status transition to key on at the moment of registration (`status` is set
  to `"pending"` on the same insert, not entered via a transition path).
- Failure mode: `send_driver_welcome_email` never raises (blanket `except`,
  logged at `warning`); a failure here cannot affect the driver row already
  committed by `insert_one` two lines above, matching every existing sender's
  contract in this file family.

## 5. User-experience effect

- **Driver-facing only.** A driver who registers now receives one email (if
  they have an email address on file — many won't yet, since it's optional at
  this stage; the existing `can_email` gate already handles that silently,
  logging at `info` rather than treating it as an error).
- Not visible mid-session to an existing user — this fires once, at account
  creation, for a brand-new driver row. No existing driver is affected
  retroactively (no backfill).
- Copy reviewed for tone in the prior review pass (see conversation): states
  what was received, what to upload next, the 0%-commission pitch, and a
  support contact — no placeholder/invented values shipped (the review had
  flagged an invented review-time SLA; that line was cut rather than shipped
  with a made-up number).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/driver_emails.py` | New file: `send_driver_welcome_email()` | New sender, mirrors `rider_emails.py`'s pattern |
| `backend/routes/drivers/profile.py` | Added a backgrounded call to the new sender after driver-row creation in `register_driver` | Wires the sender to the one place D1 identified as silent |
| `backend/tests/test_driver_welcome_email.py` | New file: unit tests | Coverage for the new sender (see §9) |

## 7. Before / after

```python
# Before (routes/drivers/profile.py, end of register_driver's create path)
    await db_supabase.insert_one("drivers", await _shared._encrypt_driver_pii(new_driver))
    if not current_user.get("is_driver"):
        ...
    return serialize_doc(new_driver)
```

```python
# After
    await db_supabase.insert_one("drivers", await _shared._encrypt_driver_pii(new_driver))
    if not current_user.get("is_driver"):
        ...
    try:
        from ...utils.background import spawn
        from ...utils.driver_emails import send_driver_welcome_email
    except ImportError:
        from utils.background import spawn  # type: ignore
        from utils.driver_emails import send_driver_welcome_email  # type: ignore
    spawn(send_driver_welcome_email(new_driver, current_user))
    return serialize_doc(new_driver)
```

## 8. Rollback plan

Purely additive, no data migration, no existing behavior changed — reverting
the two new files and the one added block (`git revert`) is a complete and
sufficient rollback. No feature flag was added because there is no existing
behavior to fall back to and no live data written by this change (no Stripe
charge, wallet delta, or ride/insurance state) — the only side effect is an
email send, which a `git revert` fully stops going forward. If a flag is
wanted anyway (e.g. to silence just this email without a deploy while keeping
the rest of the branch), the existing global kill switch
`app_settings.lifecycle_emails_enabled` already covers it, since this sender
is routed through the same `email_notifications` policy layer as every other
lifecycle email.

## 9. Verification performed

- [x] Automated tests run — unit: `pytest tests/test_driver_welcome_email.py
      tests/test_all_emails_are_branded.py tests/test_rider_emails_app_name.py`
      (24 passed) and the full existing driver-registration suites,
      `tests/test_drivers.py tests/test_drivers_extended.py` (125 passed, no
      regressions from the new call site).
- [x] `ruff check` on all three changed/new files — clean.
- [ ] Manual repro in staging — **not performed** (no staging access from this
      session).
- [x] Blast-radius grep performed — searched for other callers of
      `register_driver`, other importers of `rider_emails.py`/
      `driver_status_notifications.py` as pattern references, and confirmed
      `_UNBRANDED_BY_DESIGN`/`test_all_emails_are_branded.py`'s structural
      sweep passes without needing a new exemption entry.
- [x] Reviewed against CLAUDE.md conventions: dual-import pattern, TRANSACTIONAL
      email classing under CASL, best-effort/never-raise sender contract,
      surgical-diff principle (no unrelated lines touched).
- [ ] No feature flag added — justified in §8 as a pure-additive, no-existing-
      behavior change; the global `lifecycle_emails_enabled` switch already
      provides an off-ramp if needed.

## What was NOT verified

- No real SES/Resend send was exercised — `send_lifecycle_email` and
  `send_transactional_email` were mocked in tests, per this repo's existing
  test convention (`mock_supabase_client`/mocked provider, never a live DB or
  provider call in unit tests). Actual client rendering (Gmail/Apple
  Mail/Outlook) was not screenshotted — no visual-regression tooling exists
  for email in this repo (documented standing gap in
  `docs/notification-channel-coverage.md`).
- The exact required-document list named in the email body (licence,
  insurance, vehicle inspection, background check) was sourced from
  `routes/drivers/profile.py`'s `allowed` field set and
  `driver_onboarding_reminder_rules.py`, not from a single canonical
  document-requirements source — worth a second look from whoever owns driver
  onboarding copy before this is treated as exhaustive.
- No production build step applies here (backend-only Python change, no
  `admin-dashboard`/`rider-app`/`driver-app` frontend touched).
