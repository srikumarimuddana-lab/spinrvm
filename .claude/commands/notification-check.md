# /notification-check — Notification & In-App Hint UX Audit

Runs `spinr-notification-ux-reviewer` against push/SMS/email notification
content and in-app toast/alert/hint copy — not whether a notification fires
or is delivered (that's `spinr-realtime-reliability-reviewer` /
`spinr-observability-reviewer`'s job), but whether what it *says* to a
rider, driver, or admin is clear, actionable, safe (no PII in the payload),
and not spammy (dedup/quiet-hours on any push-sending background loop).

## Usage

```
/notification-check                    # audits staged + unstaged changes
/notification-check backend/routes/notifications.py
/notification-check PR 123
```

## When to run it

- Any change to `backend/routes/notifications.py`, `backend/features.py`'s
  `send_push_notification`, or any of `backend/utils/marketing_push.py`,
  `driver_status_notifications.py`, `driver_onboarding_reminders.py`,
  `push_retry.py`, `spinr_pass.py`, `auto_payout.py`
- Any change to `backend/routes/rides/matching.py`'s FCM offer payload, or
  any new background loop in `core/lifespan.py` that sends a push
- Any new/changed `Alert.alert(`, `Toast.`, or `showToast(` call in
  rider-app or driver-app
- This is the check that would have caught `ACTION_ITEMS.md` C112/C113
  (an FCM payload leaking `rider_name` with no PII filtering, found ad hoc
  by two different sessions the same day) before merge — run it, don't wait
  to find the next instance the same way

## What it is not

- Not a delivery/reliability check — a notification that never fires, fires
  late, or breaks the WS/heartbeat contract is `spinr-realtime-reliability-reviewer`'s
  finding, not this one's
- Not a state-presence check — whether a loading/empty/error state exists at
  all is `spinr-design-consistency-reviewer`'s job; this command checks what
  the copy inside that state says
- Included in `/full-audit`'s roster — running that also runs this, so don't
  run both back-to-back on the same diff for no reason

See `.claude/agents/spinr-notification-ux-reviewer.md` for exactly what it
checks and its output format.
