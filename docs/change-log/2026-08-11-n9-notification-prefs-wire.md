# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude Code (backend agent, N9) |
| Surface(s) | backend |
| Domain (Sentry tag) | rides / drivers |
| PR / commit link | (filled on push) |
| Related issue or gap ID | ACTION_ITEMS.md N9 |

## 1. Issue / gap identified

Five `notification_preferences` columns (`sms_enabled`, `ride_updates`,
`promotions`, `safety_alerts`, `earnings_summary`) are persisted (rider/driver
settings screens write them via `PUT /notifications/preferences`) but were
read by nothing — toggling any of them in the app had zero effect.

## 2. Root cause

The preferences table and its PUT/GET endpoints (`routes/notifications.py`)
were built ahead of the send-side wiring; only `push_enabled` and
`email_enabled` ever got a corresponding read (`features.py`'s
`send_push_notification`, `utils/email_notifications.py`'s `_email_opt_in`).
The other five were added to the schema/UI but the wiring work was never
finished per-column.

## 3. Fix / remediation

Investigated every column's legitimate send path (full grep of every
`send_sms`/`send_push_notification`/`send_email` call site) and made an
explicit WIRE-or-REMOVE decision per column (full reasoning in the ACTION_ITEMS.md
N9 entry):

- **`earnings_summary` — WIRED.** `utils/driver_statement_job.py` now skips
  the PDF render + email for an opted-out driver, recording a new terminal
  `driver_statements.status = 'skipped_opted_out'` (documented via
  migration `300_driver_statements_skipped_opted_out_comment.sql`, comment-only —
  no schema change since `status` has no CHECK constraint).
- **`ride_updates` — WIRED**, centrally in `features.send_push_notification`,
  gating a narrow set of ride-lifecycle push types
  (`driver_accepted`/`driver_arrived`/`ride_started`/`ride_completed`/
  `ride_cancelled`/`ride_noshow`) — only for non-time-critical priority (the
  existing `dispatch`/`safety`/`account` bypass is untouched and runs first).
- **`sms_enabled`, `safety_alerts`, `promotions` — determined dead**, no
  code change. Reasoning: every SMS/push send path that could plausibly
  match each name is either already governed by a *different*, correctly
  scoped mechanism (CASL `marketing_preferences` for `promotions`), is
  safety/transactional and must never be preference-gated (`safety_alerts`
  would have to gate the SOS confirmation push, one of the three
  guaranteed-delivery tiers — unsafe), or has no remaining candidate send
  path at all (`sms_enabled` — OTP, SOS-contact SMS, and guest-ride SMS are
  all transactional/safety; the one operational-broadcast SMS path
  (`routes/admin/messaging.py`) is documented in its own code comment as
  deliberately ungated because it may carry outage/safety content, and has
  no sub-classification to separate that from routine info). Frontend
  toggle removal for these 3 is flagged as follow-up in ACTION_ITEMS.md —
  not verified/edited this session (backend-only scope; three other agents
  are concurrently touching rider-app/driver-app-adjacent backend files
  this session, per the task's "what NOT to touch" list).

## 4. Risk & impact on existing functionality

**`features.send_push_notification`** is the single send path for ~25 call
sites across the backend (dispatch, ride lifecycle, corporate, disputes,
payments, loyalty, admin broadcasts, …). Every one of those callers is
affected by this change *in principle*, but the new gate only fires when
BOTH conditions hold: (a) priority is not `dispatch`/`safety`/`account`, and
(b) `data["type"]` is one of the 6 literal strings in
`_RIDE_UPDATE_PUSH_TYPES`. Grepped every current caller of these 6 type
strings to confirm none of them use a time-critical priority for a type in
the set (would be double-gated, not silently dropped, if it did — but
confirmed none do):
- `driver_accepted` → `routes/drivers/ride_flow.py:417` (default/normal priority)
- `driver_arrived` → `routes/drivers/ride_flow.py:627` (default/normal priority)
- `ride_started` → `routes/drivers/ride_flow.py:694,761`, `routes/rides/lifecycle.py:117` (normal)
- `ride_completed` → `routes/drivers/ride_complete.py:899`, `routes/rides/lifecycle.py:298` (`priority="normal"`)
- `ride_cancelled` → `routes/drivers/ride_cancel.py:178` (normal, rider-directed); `routes/rides/cancellation.py:373,429` (`priority="dispatch"`, driver-directed — bypasses this gate via the existing `time_critical` check, confirmed by a new regression test); `routes/admin/rides.py` (WS only, no push at this type currently)
- `ride_noshow` → `routes/drivers/ride_cancel.py:387` (normal)

No other caller passes these exact type strings, so the blast radius for
*new suppression* is exactly those 8 call sites, and only for a rider/driver
who has explicitly set `ride_updates=false` (default is `true`, matching the
GET-defaults in `routes/notifications.py`).

**`utils/driver_statement_job.py`** — `_process_driver` is called only from
`_ensure_period`, itself called only from `_tick`/`driver_statement_loop`
(the one 30-min background loop for this job; confirmed via grep, no other
caller). The new `earnings_summary` check sits between the existing
`has_activity` and `email` checks, so `skipped_inactive`/`skipped_no_email`
priority is unchanged (an inactive driver's row still says
`skipped_inactive`, not `skipped_opted_out`, even if they also opted out —
matches "what actually determined the outcome"). Replay-safety is
unaffected: the claim-insert (the idempotency guard) still happens first,
unconditionally, before this check.

**Shared table**: `notification_preferences` is also read by
`utils/email_notifications.py` (`email_enabled`) and written by
`routes/notifications.py`. Neither is touched by this change; the 5 columns
this PR interacts with were previously unread by anything, so there is no
existing reader whose behavior could regress.

## 5. User-experience effect

Visible, by design — that's the point of the fix, and CLAUDE.md requires
saying so explicitly: a rider/driver who previously toggled off "Ride
Updates" or "Earnings Summary" in Settings and saw no change will now
actually stop receiving those specific notifications. Not visible mid-ride
in the sense of a state-machine or fare change — a suppressed push is a
missing notification, not an incorrect one, and the in-app WebSocket event
+ notification-inbox row are both unaffected (only the FCM/Expo device push
is gated; a rider with the app open still sees the live status change).

`sms_enabled`/`safety_alerts`/`promotions` remain unchanged from the user's
perspective (still no-op toggles) — flagged as a known gap in ACTION_ITEMS.md
rather than silently left implying "handled."

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/features.py` | Added `_RIDE_UPDATE_PUSH_TYPES` constant + a second preference check (`ride_updates`) inside `send_push_notification`'s existing non-time-critical branch | Wire `ride_updates` centrally, one file, no per-call-site edits |
| `backend/utils/driver_statement_job.py` | Added `_earnings_summary_enabled()` + a check in `_process_driver` before the PDF render / email send, new `skipped_opted_out` status | Wire `earnings_summary` per N9's own stated scope |
| `backend/migrations/300_driver_statements_skipped_opted_out_comment.sql` | New migration, `COMMENT ON COLUMN` only | Document the new status value; append-only rule forbids editing migration 272 |
| `backend/tests/test_notification_preferences.py` | +4 tests for the `ride_updates` gate (suppress by type, no-suppress for unrelated type, dispatch-priority bypass, opted-in still sends) | Regression coverage for the new gating branch |
| `backend/tests/test_driver_statement_job.py` | +4 tests for `earnings_summary` gating + updated 3 existing tests to stub `db.get_rows` (now called on every non-inactive path) | Regression coverage; existing tests would otherwise `await` an unconfigured `MagicMock` and raise `TypeError` |
| `ACTION_ITEMS.md` | N9 marked closed with full per-column WIRE/REMOVE reasoning and file:line evidence | Required by the task; documents the 3 REMOVE decisions and follow-up |

## 7. Before / after

```python
# Before (features.py, send_push_notification, non-time-critical branch)
if not time_critical:
    try:
        pref_rows = await db.get_rows("notification_preferences", {"user_id": user_id}, limit=1)
        if pref_rows and pref_rows[0].get("push_enabled") is False:
            ...
            return False
    except Exception:
        ...
```

```python
# After
if not time_critical:
    try:
        pref_rows = await db.get_rows("notification_preferences", {"user_id": user_id}, limit=1)
        if pref_rows and pref_rows[0].get("push_enabled") is False:
            ...
            return False
        notif_type = (data or {}).get("type")
        if notif_type in _RIDE_UPDATE_PUSH_TYPES and pref_rows and pref_rows[0].get("ride_updates") is False:
            logger.info(...)
            return False
    except Exception:
        ...
```

```python
# Before (driver_statement_job.py, _process_driver)
if not stmt["has_activity"]:
    await _finish("skipped_inactive", {"totals": totals})
    return

email = ((user or {}).get("email") or "").strip()
```

```python
# After
if not stmt["has_activity"]:
    await _finish("skipped_inactive", {"totals": totals})
    return

if not await _earnings_summary_enabled(driver.get("user_id")):
    await _finish("skipped_opted_out", {"totals": totals})
    return

email = ((user or {}).get("email") or "").strip()
```

## 8. Rollback plan

No feature flag was added — deliberately: both gates fail **open** (a
preferences-table lookup error sends the notification, same posture as the
existing `push_enabled` check they sit next to), so the worst case of a bug
in either gate is "some users stop receiving a specific push/email they
opted out of," never a false suppression of something critical or a crash.
If either gate needs to be disabled quickly:
- **`ride_updates`**: revert the `features.py` diff (single function, no
  data migration involved — the preference column itself was already live
  and harmless before this change).
- **`earnings_summary`**: revert the `utils/driver_statement_job.py` diff.
  Any driver rows already written as `skipped_opted_out` during the bad
  window are not "wrong data" — the driver genuinely had the preference set
  to false at send time — so no data remediation is needed; a reverted
  `_process_driver` will still correctly produce the next period's statement
  on the following tick, since `skipped_opted_out` is terminal per-period,
  exactly like `skipped_no_email`/`skipped_inactive` already are (i.e. a
  driver isn't retroactively re-sent — this matches the existing,
  intentional non-retry design of this job, not a new gap).
- Migration `299` is comment-only; its own rollback SQL (restoring the
  original comment text) is in the migration file's header, though there is
  no functional reason to run it.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_notification_preferences.py tests/test_driver_statement_job.py -q --no-cov` — 28 passed (11 + 17).
- [x] `ruff check` and `ruff format --check` on every touched Python file — clean.
- [ ] Manual repro / staging check — not performed (no staging environment access from this session).
- [x] Blast-radius grep performed — every `send_sms`/`send_push_notification`/`notification_preferences` reader and writer in the backend was enumerated (listed in section 4 and in the ACTION_ITEMS.md N9 entry).
- [x] Reviewed against CLAUDE.md conventions: dual-import pattern preserved in `driver_statement_job.py` (unaffected, already there); "do not silently swallow errors" — both new gates use `logger.error(..., exc_info=True)` on lookup failure, not `warning`; migrations append-only rule followed (new file instead of editing 272).
- [x] Feature-flag: deliberately not added — see rollback plan for the fail-open reasoning.

## What was NOT verified

- Not tested against a live Supabase instance — only `mock_supabase_client`-style mocks (`unittest.mock.AsyncMock`) per this repo's unit-test convention. No integration test was added.
- No load/performance testing of the extra `notification_preferences` field read on `send_push_notification`'s hot path — the read was already happening (`push_enabled`) for every non-time-critical push, so this adds zero new round-trips, only an extra dict-key check on an already-fetched row; not benchmarked but architecturally a no-op cost-wise.
- Frontend (rider-app/driver-app) was **not** touched or inspected for this PR beyond a read-only grep of `rider-app/app/_layout.tsx` to confirm the "Ride Updates" toggle's own copy ("Status updates for your current ride") — used only to justify the `_RIDE_UPDATE_PUSH_TYPES` scope, not verified to compile or tested in the app. No frontend build was run (not applicable — no frontend files changed).
- The 3 REMOVE decisions (`sms_enabled`, `safety_alerts`, `promotions`) leave the settings-screen toggles as still-present but no-op in the UI; removing them is explicitly flagged as unstarted follow-up, not assumed done.
- Did not verify whether any admin-dashboard surface displays `notification_preferences` rows or the new `driver_statements.status = 'skipped_opted_out'` value in a way that assumes a fixed enum (e.g. a dropdown filter) — grepped `admin-dashboard/` was out of scope for a backend-only session; if such a filter exists it would show a statement as an unrecognized status rather than erroring (status is a free-text column), which is a cosmetic gap at worst.
