# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 |
| Author | Claude (session, on request) |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/driver-rider-emails-messages-pdf-bio6az` |
| Related issue or gap ID | — (ad hoc copy request) |

## 1. Issue / gap identified

The driver welcome email (sent once, on registration) didn't mention driver
training (training.spinr.ca) or say anything about Spinr Pass subscription
status for the driver's area.

## 2. Root cause

Not a bug — a copy gap. The email was written before the training site and
before Spinr Pass's per-area kill switch existed.

## 3. Fix / remediation

Added two paragraphs to `send_driver_welcome_email`'s copy: a training
mention, and a **conditional** pointer to the in-app Subscription screen for
the driver's area-specific Spinr Pass status.

The subscription line is deliberately *not* an unconditional "your
subscription is free" claim. `service_areas.spinr_pass_enabled` defaults to
`TRUE` (migration `08_service_area_subregions.sql`) and is only `FALSE` where
an admin has explicitly disabled it — so "free" is true only in some areas,
not globally. Asserting it as a blanket fact in an email sent to every new
driver, everywhere, would be false for any driver in a Spinr-Pass-enabled
area. Confirmed the area-conditional framing with the requester before
writing it in (see conversation).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** `send_driver_welcome_email` in
  `utils/driver_emails.py` has exactly one caller path (driver registration)
  and no other reader of its paragraph copy. Grepped for other callers/tests:
  only `backend/tests/test_driver_welcome_email.py` exercises this function,
  and it asserts on `email_type`, `email_class`, `user_id`, and that the
  greeting name appears in the rendered HTML — it does not assert on
  paragraph count or exact copy, so the addition doesn't break it.
- No state, table, or money path touched — pure copy addition inside an
  existing `paragraphs=[...]` list. Still renders through the shared
  `utils/email_layout.render_email` shell (structural "every email is
  branded" test `test_all_emails_are_branded.py` is unaffected — it checks
  routing through the layout, not copy content).
- Also updated `backend/scripts/preview_notification_templates.py` (a
  standalone QA preview tool, not part of the production runtime) to keep
  its sample copy in sync, and fixed an unrelated inaccuracy found while
  editing it: the tool's suspended-driver sample showed both "Reason: ..."
  and "Contact support for details." together, which the real
  `driver_status_notifications._with_reason()` never produces (the latter
  only appears when no reason is given, and the suspend action always
  requires one).

## 5. User-experience effect

- **Driver-facing.** Every newly-registered driver's welcome email now has
  two additional paragraphs. Not visible mid-session (one-time email sent at
  registration, not a live screen).
- Copy reviewed for tone (specific, non-technical, actionable) and, per the
  root-cause note above, for factual accuracy against actual `app_settings`/
  `service_areas` behavior rather than treated as a marketing throwaway line.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/driver_emails.py` | Added training + area-conditional subscription paragraphs to `send_driver_welcome_email` | Requested content addition |
| `backend/scripts/preview_notification_templates.py` | Synced welcome-email sample copy; fixed suspended-driver sample to match real `_with_reason()` output | Keep QA preview tool accurate |

## 7. Before / after

```
# Before
"Upload your driver's licence, ... as soon as they've been reviewed.",
f"{company.app_name} takes 0% commission — ... No per-trip cut, ever.",
"While you wait, finish your vehicle details ...",
```

```
# After
"Upload your driver's licence, ... as soon as they've been reviewed.",
"Take a few minutes to complete driver training at training.spinr.ca "
"before your first ride — it covers pickups, safety, and rider communication.",
f"{company.app_name} takes 0% commission — ... No per-trip cut, ever.",
"Check the Subscription screen in the driver app for your area's current "
"Spinr Pass status — some areas have no subscription fee right now.",
"While you wait, finish your vehicle details ...",
```

## 8. Rollback plan

Pure copy change, no flag needed: `git revert` this commit. No data was
written, no migration involved, no Stripe/wallet/ride-state touched — a code
revert is a complete rollback here.

## 9. Verification performed

- [ ] Automated tests run — **not run**: this sandbox has no network access
      to install backend Python dependencies (pydantic-settings, bcrypt, the
      Supabase client, etc. — confirmed via failed pip install and a direct
      403 from pypi.org). Verified by reading, not running:
      `test_driver_welcome_email.py`'s four tests don't assert on paragraph
      content/count, so this addition can't break them.
- [ ] Manual repro in staging — not available in this environment.
- [x] Blast-radius grep performed — searched for other callers of
      `send_driver_welcome_email` and other readers of its paragraph copy;
      found none beyond the one test file above.
- [x] Reviewed against CLAUDE.md conventions — treated the subscription
      claim as money-adjacent and checked it against actual
      `service_areas.spinr_pass_enabled` default/behavior before wording it,
      per "Escalate, don't silently ship, when in doubt."
- [ ] Feature-flagged — not applicable; this is one-time onboarding copy,
      not a toggleable behavior, and the existing area-level
      `spinr_pass_enabled` flag already gates the underlying subscription
      state the new copy merely points at.

## 10. What was NOT verified

- The actual rendered email was **not** sent through a real SES/Resend
  provider or viewed in a real mail client (Gmail/Outlook rendering quirks
  aren't covered) — only rendered via headless Chromium against the shared
  HTML layout, which matches how the PDF preview shown to the requester was
  produced.
- No production test run (`pytest`) — see above; reasoned about test
  compatibility by reading the test file instead.
- Did not verify how many service areas currently have `spinr_pass_enabled =
  false` in the live database (no DB access from this session) — the copy is
  worded to be true regardless of that count ("some areas," not a number or
  a blanket claim).

## Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed (isolated, one caller, one test file)
- [x] No silent behavior change — UX field filled in above
