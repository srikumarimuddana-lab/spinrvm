# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude Code (agent session) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments (Stripe Connect payouts) / drivers |
| PR / commit link | see PR opened against `claude/n6-stripe-kyc-payout-block-notify` |
| Related issue or gap ID | ACTION_ITEMS.md N6 (D24) |

## 1. Issue / gap identified

`routes/webhooks.py`'s `account.updated` Stripe webhook handler calls
`services/stripe_kyc_sync.apply_account_update`, which persists the KYC
mirror columns (`stripe_payouts_enabled`, `stripe_details_submitted`,
`stripe_id_number_provided`, etc. — migration 92) onto the matching
`drivers` row and returns. Nothing told the driver. If Stripe blocked
payouts (rejected KYC document, ToS not accepted, verification failed,
requirement newly due, …), the driver found out only by manually opening
the app or by a payout silently failing — no push, no in-app signal at the
moment it actually happened.

## 2. Root cause

`apply_account_update` was written purely as a data-mirroring function —
"map Stripe's Account object onto our cache columns" — with no notion of
*change* detection or a notification side-effect. The webhook dispatch
branch in `routes/webhooks.py` (`elif event_type == "account.updated":`)
calls it and does nothing else, unlike every other driver-facing webhook
branch in the same file (e.g. the `customer.subscription.deleted` handler
immediately above it, which resolves `drivers.id → user_id` and fires a
push on cancellation).

## 3. Fix / remediation

Added transition-detection + notification directly inside
`apply_account_update`, after the mirror write commits successfully:

- **Design decision — detect the edge, not the level.** Stripe redelivers
  `account.updated` on retries/replays, and most deliveries don't change
  `payouts_enabled` at all. Firing on every delivery of an already-blocked
  account would spam a driver stuck blocked for days with a repeat push on
  every redelivery. `apply_account_update` already has both the pre-update
  `driver` row (fetched before the `db_supabase.update_one` call) and the
  freshly computed `updates` dict in scope, so the new
  `_notify_payouts_transition` helper compares
  `driver.get("stripe_payouts_enabled")` (pre-update) against
  `updates["stripe_payouts_enabled"]` (post-update, always a real `bool` —
  see `_kyc_mirror_fields`'s `bool(account.get("payouts_enabled"))`) and
  fires only on an explicit `True → False` edge (blocked) or `False → True`
  edge (recovered).
- **First-sync edge case, deliberately handled.** The pre-update value can
  genuinely be `None` — either a `drivers` row that predates this mirror
  column, or (much more commonly going forward) a driver's very first
  `account.updated` since starting Stripe onboarding. `None → False` and
  `None → True` are both explicitly excluded from firing anything: a brand
  new driver who starts out blocked has not been "newly blocked" (there is
  no prior enabled state to have transitioned from), and one whose account
  starts enabled has not "recovered" from anything. Only a `True`↔`False`
  edge on a row synced at least once before counts.
- **Recovery case — implemented, not deferred.** Item 4 of the task asked
  for a judgment call: also handle payouts re-enabled after being blocked.
  Implemented as a second, symmetric, separately-coded branch
  (`was_enabled is False and now_enabled is True`) rather than folding both
  directions into one "changed" condition, so each direction's copy/priority
  stays independently correct and easy to reason about in isolation.
- **Notification delivery.** `driver["user_id"]` (already on the `drivers`
  row already in scope — no extra lookup needed) is passed to
  `send_push_notification`, mirroring the exact idiom the
  `customer.subscription.deleted` handler uses immediately above the
  `account.updated` branch in the same file (resolve `users.id` from the
  driver row already in hand, then call `send_push_notification`, wrapped
  in try/except so a notification failure never raises).
- **Priority tier.** `priority="account"` for the blocked-transition push.
  `send_push_notification`'s own docstring explicitly scopes the `account`
  tier to "driver rejected/suspended/banned … a driver who can no longer
  earn must be told why rather than discovering it as a 403" — a
  payouts-block is the same shape of problem (a driver who can no longer
  get paid), so it gets the same guaranteed-delivery/opt-out-bypass
  treatment: bypasses the push-preference opt-out and falls back to the
  retry queue on immediate-delivery failure. The recovery push uses
  `priority="normal"` — good news, not something that needs to bypass a
  driver's opt-out or guarantee delivery via the retry queue.
- **Deeplink.** `/driver/payout` — confirmed by reading
  `driver-app/app/driver/payout.tsx`, which already renders the driver's
  live Stripe KYC/payouts status (`stripe_account_onboarded`,
  `stripe_id_number_provided`) and hosts the "Set up payouts" / resync
  flow. This is the same pattern the `subscription_cancelled` push above
  the target code uses (`deeplink: "/driver/subscription"`, a matching
  driver-app route).
- **Failure isolation.** The whole notify step is wrapped so a push failure
  (FCM down, import error, anything) is caught, logged, and never
  propagates — the mirror write has already committed by the time
  `_notify_payouts_transition` runs, and nothing about the webhook's own
  job (persisting the KYC mirror, returning 200 to Stripe) may be put at
  risk by a best-effort notification.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped every caller of
  `apply_account_update` in the repo: the single production caller is
  `routes/webhooks.py`'s `account.updated` branch (the one this fix
  targets). All other hits are the function's own tests
  (`backend/tests/test_stripe_kyc_sync_coverage.py`,
  `backend/tests/test_webhooks_coverage_gap.py`,
  `backend/tests/test_routes_webhooks_coverage.py`) and the docstring/
  ACTION_ITEMS.md references. No other caller relies on
  `apply_account_update` having no side effects beyond the DB write — it
  is not used as a "pure" helper anywhere.
- **`refresh_driver_kyc`** (the admin "Refresh from Stripe" button) is a
  *separate* function in the same file that also writes the KYC mirror via
  its own `db_supabase.update_one` call, but does **not** go through
  `apply_account_update` and was deliberately left untouched — it was out
  of this task's stated scope (only `apply_account_update`/the webhook path
  was named as the gap), and firing a driver-facing push from an
  admin-initiated manual refresh click would be a different, unreviewed UX
  decision. Flagging this as a known follow-up rather than silently
  expanding scope: an admin manually refreshing a driver's KYC from Stripe
  currently still does not notify the driver of a payouts-blocked state
  found that way.
- **Existing `apply_account_update` tests unaffected.** The pre-existing
  `test_success_merges_driver_and_updates` test passes an account payload
  with no `payouts_enabled` key and a driver row with no
  `stripe_payouts_enabled` key — both resolve to the "first sync, no prior
  value" case, which is explicitly excluded from firing anything, so that
  test's assertions (and its lack of any push mock) are unaffected.
- **`send_push_notification` itself is unmodified** — this fix only adds a
  new call site; the function's opt-out/retry-queue/inbox-row behavior is
  shared with every other push in the codebase and was not touched.
- No ride state, wallet, or Stripe charge logic is touched. No migration.
  No schema change. Purely additive: a new notification fires under a
  narrowly-scoped, previously-impossible-to-trigger condition (there was no
  prior code path emitting any push here at all).

## 5. User-experience effect

- **Driver-facing.** A driver whose Stripe Connect payouts newly get
  blocked (KYC document rejected, ToS revoked, verification failed, a new
  requirement comes due, etc.) now receives a push notification —
  "Your account needs attention" — with a deeplink straight to the
  driver-app payout/KYC screen, plus an in-app notification-inbox row
  (written unconditionally by `send_push_notification` regardless of
  device-delivery success). A driver whose payouts get re-enabled after
  being blocked also receives a lighter-weight "Payouts are back on" push.
- **Visible mid-session?** Yes, potentially — this fires from a live Stripe
  webhook delivery, which can land at any time, including while the driver
  has the app open (e.g. mid-shift, between rides). It is a new,
  additive push notification; it does not alter any existing screen's
  behavior or any ride/dispatch state, so there is no risk of it
  interrupting an active ride flow — worst case is an extra push arriving.
- **No copy reviewed by product/support beyond this session's own
  drafting** — flagging per the mandatory field: "Your account needs
  attention" / "Stripe has paused payouts on your account pending
  verification. Open the app to see what's needed." and "Payouts are back
  on" / "Your Stripe verification is complete — payouts have resumed." were
  written to match this codebase's existing tone (see
  `utils/driver_status_notifications.py`'s suspended/banned copy) but were
  not run past a human copy reviewer.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/stripe_kyc_sync.py` | Added `_notify_payouts_transition` and `_send_payouts_notice`; `apply_account_update` now calls the former after the mirror write commits, before returning | Implements the N6 fix — detect the payouts-enabled edge and notify |
| `backend/tests/test_stripe_kyc_sync_coverage.py` | Added `TestApplyAccountUpdatePayoutsNotification` (9 new tests) | Regression coverage for the transition-detection logic, the redelivery-no-spam guard, the first-sync edge case (both directions), the recovery push, push-failure isolation, and the no-`user_id` guard |
| `ACTION_ITEMS.md` | Marked N6 done, in the style of neighboring closed N-items | Backlog hygiene per task instructions |
| `docs/change-log/2026-08-11-n6-stripe-kyc-payout-block-notify.md` | New file (this document) | Mandatory Change Impact & Risk Log for a payments-adjacent live-tested surface |

`backend/routes/webhooks.py` was **not** modified — the transition-detection
and notification logic lives entirely inside `apply_account_update`, which
already has both the pre-update driver row and the post-update mirror dict
in scope; no change to the webhook dispatch branch itself was needed.

## 7. Before / after

```python
# Before (services/stripe_kyc_sync.py, tail of apply_account_update)
    logger.info(
        "[STRIPE-KYC] mirrored driver=%s payouts_enabled=%s details_submitted=%s id_provided=%s",
        driver["id"],
        updates["stripe_payouts_enabled"],
        updates["stripe_details_submitted"],
        updates["stripe_id_number_provided"],
        extra={"event_id": event_id, "domain": "drivers"},
    )
    return {**driver, **updates}
```

```python
# After
    logger.info(
        "[STRIPE-KYC] mirrored driver=%s payouts_enabled=%s details_submitted=%s id_provided=%s",
        driver["id"],
        updates["stripe_payouts_enabled"],
        updates["stripe_details_submitted"],
        updates["stripe_id_number_provided"],
        extra={"event_id": event_id, "domain": "drivers"},
    )

    await _notify_payouts_transition(driver, updates, event_id=event_id)

    return {**driver, **updates}


async def _notify_payouts_transition(driver, updates, *, event_id=None) -> None:
    was_enabled = driver.get("stripe_payouts_enabled")
    now_enabled = updates["stripe_payouts_enabled"]

    if was_enabled is True and now_enabled is False:
        await _send_payouts_notice(driver, title="Your account needs attention",
            body="Stripe has paused payouts on your account pending verification. "
                 "Open the app to see what's needed.",
            data_type="stripe_payouts_blocked", priority="account", event_id=event_id)
    elif was_enabled is False and now_enabled is True:
        await _send_payouts_notice(driver, title="Payouts are back on",
            body="Your Stripe verification is complete — payouts have resumed.",
            data_type="stripe_payouts_recovered", priority="normal", event_id=event_id)
```

## 8. Rollback plan

Pure application-code addition — no migration, no schema change, no data
mutation. Rollback is a plain `git revert` of the
`stripe_kyc_sync.py` commit: it removes the notification call but leaves
the KYC mirror write (the pre-existing, load-bearing behavior) completely
untouched. There is no live-data state (no Stripe charge, no wallet delta,
no ride state) created or mutated by this change, so no data-level
remediation is needed on top of the code revert — unlike a money/ride-state
change, "revert the commit" is a complete rollback here.

If a partial rollback is ever wanted (keep the blocked-notification but
silence the recovery one, or vice versa), that's a one-line change: delete
or comment out the relevant `elif` branch in `_notify_payouts_transition` —
no flag exists for this today since the task scoped it as a straightforward
additive fix, not a risky/gradual rollout candidate (a notification, unlike
a ride-state or fare change, has no way to corrupt state if wrong — worst
case is a wrong/missing push, which is exactly today's status quo).

## 9. Verification performed

- [x] Automated tests run — unit only (`pytest -m unit`), see exact
  pass/fail counts reported in the PR/session output.
- [ ] Manual repro steps followed in staging — not performed, no staging
  environment available in this session.
- [x] Blast-radius grep performed — `grep -rn apply_account_update` across
  the whole repo; one production caller (`routes/webhooks.py`), rest are
  this function's own tests and docs.
- [x] Reviewed against relevant `CLAUDE.md` conventions — dual-import
  pattern followed (`try: from ..features import ... except ImportError:
  from features import ...`), best-effort-notification-never-raises
  pattern followed (matches the `subscription_cancelled` push and the
  observability guidance's "degraded-but-recovered → warning log, never
  Sentry" for the swallow), structured logging via `extra={...}` with
  `domain` tag.
- [ ] Feature-flagged — not flagged. Judgment call: this is a strictly
  additive notification with no path to corrupting ride/payment state if
  the logic were somehow wrong (worst case: a missing or duplicate push,
  which is a strict improvement over or equal to today's "notifies
  nobody"), so a DB-config flag was judged unnecessary overhead for this
  scope. If the team disagrees, gating behind a new `app_settings` boolean
  read in `_notify_payouts_transition` is a small follow-up.

## 10. What was NOT verified

- **No live Stripe webhook was actually fired.** All verification is via
  mocked unit tests (`mock`/`monkeypatch` on `db_supabase.get_rows` /
  `update_one` and `backend.features.send_push_notification`) — this was
  never exercised against Stripe's real `account.updated` payload shape
  end-to-end through the FastAPI route, nor against Stripe's actual
  event-redelivery/ordering behavior (e.g. out-of-order delivery of two
  `account.updated` events for the same account, which this fix's
  edge-detection logic assumes arrives in order — Stripe does not
  guarantee ordering across webhook deliveries, only within a single
  account timeline in practice; this was not empirically tested).
- **No real push delivery was verified.** `send_push_notification` was
  mocked in every new test; no real FCM/Expo token, no real device.
- **The transition-detection edge cases (first-sync `None`, redelivery,
  both directions) are unit-tested but not verified against a real Stripe
  account's actual field-population sequence** — i.e., whether Stripe ever
  sends an `account.updated` with `payouts_enabled` genuinely absent from
  the payload (vs. `False`) was reasoned about from `_kyc_mirror_fields`'s
  existing `bool(account.get("payouts_enabled"))` handling, not confirmed
  against a live Stripe test-mode account walkthrough.
- **No visual/product review of the push copy.** Per the User-experience
  section above.
- **`refresh_driver_kyc`'s admin-refresh path was not touched or tested**
  for a parallel notification — explicitly out of scope, noted above.
