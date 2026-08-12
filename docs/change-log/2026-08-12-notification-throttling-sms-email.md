# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Author | Claude Code (spinr platform) |
| Surface(s) | backend |
| Domain (Sentry tag) | rides (shared utility; primary callers are marketing) |
| PR / commit link | (branch `claude/rideshare-app-analysis-blx3pn`, subtask 4/5) |
| Related issue or gap ID | subtask 4/5 of the notification-throttling feature |

## 1. Issue / gap identified

Subtask 3 wired quiet-hours + daily-cap throttling into push notifications only. SMS and email marketing sends had no equivalent throttle.

## 2. Root cause

Same as subtask 3 — the feature simply didn't exist for any channel until this work started.

## 3. Fix / remediation

Added the same `should_throttle` gate to `utils/marketing_sms.py::send_marketing_sms` and `utils/marketing_email.py::send_marketing_email` — the two CASL-compliant marketing wrappers, **not** the low-level `sms_service.send_sms` / `email_provider.send_transactional_email` primitives underneath them.

**Why the wrappers and not the primitives — this was a deliberate scope decision, not an oversight:** `sms_service.send_sms` takes a raw phone number and Twilio credentials with no `user_id` and no concept of message priority/type at all. It's called directly by `routes/rides/safety.py` (SOS → emergency contacts) and `sms_service.send_otp_sms` (2FA) — both must never be throttled, and the primitive has no way to distinguish itself from a marketing send. `email_provider.send_transactional_email` is similarly used for receipts and `send_ops_alert_email`. Applying the gate at the marketing-wrapper layer (which already carries `user_id` and is unambiguously non-critical by definition — CASL regulates *commercial* electronic messages specifically) is the only place this can be added correctly without a real risk of silently swallowing an OTP code or an SOS alert to an emergency contact.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to marketing.** `send_marketing_sms` has real callers in `routes/admin/messaging.py` (admin broadcast tool) and the marketing broadcast service; `send_marketing_email` likewise. Neither is called from any transactional, safety, or dispatch path — confirmed via `grep -rl "send_marketing_sms(\|send_marketing_email("` excluding the definition files themselves.
- **What else reads/writes the same code path:** `send_sms` and `send_transactional_email` (the underlying primitives) are unmodified — SOS (`routes/rides/safety.py`), OTP (`sms_service.send_otp_sms`), receipts, and ops alerts (`email_provider.send_ops_alert_email`) are all structurally untouched by this change, not merely "expected to bypass" it.
- **Regression risk if the flag is ever flipped on without review:** a marketing SMS/email could be suppressed during quiet hours or past the cap — which is the intended, designed behavior, not a side effect. No other flow is affected.
- **Not interacting with:** ride state machine, money/wallet deltas, WS events, RLS, OTP/2FA, SOS/emergency-contact delivery.

## 5. User-experience effect

- **Who sees a difference:** riders/drivers who have opted into marketing SMS/email, only once `notification_throttling_enabled` is turned on.
- **Visible mid-session?** No — marketing sends are not tied to an active ride session.
- **Copy/notification change:** none — same CASL footer/unsubscribe/consent gates as before, unchanged; only a delivery-timing/frequency gate added in front of them.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/marketing_sms.py` | Added throttle check after the settings fetch, before sending | Already has `user_id` and `settings` in scope — zero extra DB round trips |
| `backend/utils/marketing_email.py` | Same, mirrored | Same reasoning |
| `backend/tests/test_marketing_sms.py` | +3 tests: flag-off bypass, throttled-suppresses, not-throttled-sends | Direct coverage of the new gate |
| `backend/tests/test_marketing_email.py` | +3 tests, same shape | Direct coverage of the new gate |

## 7. Before / after

```python
# Before (marketing_sms.py, after settings fetch)
    settings = await get_app_settings()

    body = message.rstrip() + _STOP_FOOTER
    result = await send_sms(...)
```

```python
# After
    settings = await get_app_settings()

    if settings.get("notification_throttling_enabled"):
        if await should_throttle(user_id, quiet_start, quiet_end, daily_cap):
            logger.info("[MARKETING] sms suppressed by notification_throttling log_id=%s", log_id)
            return False

    body = message.rstrip() + _STOP_FOOTER
    result = await send_sms(...)
```

(`marketing_email.py` mirrors this exactly, inserted before the CASL footer/unsubscribe-link construction.)

## 8. Rollback plan

Same as subtask 3: flip `notification_throttling_enabled` off via `PUT /admin/settings`, no deploy, no data cleanup. The flag is shared across all three channels (push/SMS/email) — there is no separate SMS/email-only kill switch, which is intentional (one operational lever, not three to keep in sync).

## 9. Verification performed

- [x] Automated tests run — unit: 6 new tests (3 SMS + 3 email) plus full re-run of `test_marketing_sms.py`, `test_marketing_email.py`, `test_marketing_broadcast.py`, `test_marketing_consent.py`, `test_marketing_preferences.py`, `test_marketing_push_coverage.py` — 39/39 pass.
- [ ] Manual repro in staging — not performed (no live Supabase/staging access this session).
- [x] Blast-radius grep performed — confirmed `send_sms`/`send_transactional_email` primitives (used by SOS/OTP/receipts/ops-alerts) are untouched; only the marketing wrappers changed.
- [x] Reviewed against relevant `CLAUDE.md` conventions — dual-import pattern, no silent error-swallowing introduced (the throttle call's own fail-open contract is unchanged from subtask 2/3), no money/state-machine/RLS surface touched.
- [x] Feature-flagged — same `notification_throttling_enabled` flag as subtask 3, still defaults `false`.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — same single flag flip as subtask 3.
- [x] Blast radius is stated, not assumed — confirmed the two marketing wrappers are the only callers changed, and the safety/OTP/receipt/ops-alert primitives beneath them are structurally untouched.
- [x] No silent behavior change to an already-shipped flow — flag defaults off.
