# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Scheduled Rides gap review — Finding #07; `ACTION_ITEMS.md` E5 |

## 1. Issue / gap identified

The scheduled-ride dispatcher loop had no way to pause it short of a full
redeploy — tracked as an open item in `ACTION_ITEMS.md` (E5), which lists
scheduled dispatch alongside the surge engine, promo redemption, and
corporate billing as risky subsystems missing a kill switch.

## 2. Root cause

The loop was never given an `app_settings` gate, unlike other risky
subsystems in this codebase (e.g. `ai_assistant_enabled`,
`corporate_inactive_company_blocks_booking`) that already follow the
flag-without-redeploy convention documented in `CLAUDE.md`.

## 3. Fix / remediation

- New `AppSettings.scheduled_dispatch_enabled: bool = True` field
  (`backend/schemas.py`) — default preserves current always-on behavior.
- `check_scheduled_rides()` checks this flag first (before even acquiring
  the Redis leader lock, to avoid unnecessary Redis calls when disabled) and
  returns `None` if disabled — the same "skipped, not a failure" signal
  used for the leader-lock-held case, so a deliberate pause never trips
  Finding #13's sustained-failure alert.
- A settings-lookup failure **fails open** (proceeds as if enabled, logs a
  warning) rather than accidentally disabling dispatch — consistent with
  this file's "never let a non-dispatch-critical failure block dispatch"
  convention already used for the pre-auth and WS-broadcast paths.
- No admin-dashboard UI toggle was added. `corporate_inactive_company_blocks_booking`
  (an existing, similarly-scoped ops flag) sets the precedent that not every
  `app_settings` field needs a dedicated UI control — this one is reachable
  via the same settings API/DB path any `app_settings` field already is. A
  UI toggle (mirroring the `ai_assistant_enabled` `Switch` component in
  `admin-dashboard/src/app/dashboard/settings/page.tsx`) would be a
  reasonable follow-up but is out of scope for closing the "no kill switch
  at all" gap this fix addresses.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `check_scheduled_rides()`'s entry point.**
  Disabling the flag stops the *entire* scheduled-ride pipeline for as long
  as it's off — reminders, dispatch claims, the defer-escalation tracking
  from Finding #03, all of it. This is intentional (a kill switch means
  "stop everything"), but it's worth being explicit: this is not a
  fine-grained per-feature flag.
- Already-scheduled rides are unaffected by toggling this off — they stay
  parked in `status='scheduled'` and dispatch normally the moment the flag
  is re-enabled. Nothing is cancelled, lost, or double-processed by a
  disable/re-enable cycle (verified by test: rows never leave `scheduled`
  status while disabled, since `check_scheduled_rides` returns before any
  DB read).
- Grepped for other readers of `scheduled_dispatch_enabled` — none; this is
  the only call site.
- No interaction with money or the ride state machine beyond "no rides move
  out of `scheduled` while this is off," which is the intended effect.

## 5. User-experience effect

None under normal operation (defaults to enabled, matching current
behavior). If an admin/on-call engineer flips it off during an incident:
riders with scheduled rides due during the outage window won't get their
10-minute reminder or have their ride dispatched until it's re-enabled —
this is the deliberate trade-off of an emergency kill switch, not a regression.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/schemas.py` | Added `scheduled_dispatch_enabled: bool = True` to `AppSettings` | Define the flag |
| `backend/utils/scheduled_rides.py` | `check_scheduled_rides()` checks the flag first, fails open on lookup error | Wire the flag into the loop |
| `backend/tests/test_scheduled_dispatch_cr.py` | New `TestDispatchKillSwitch` (disabled skips entirely + doesn't count as a failure; enabled/lookup-failure both proceed normally); fixed a pre-existing test (`test_check_queries_scheduled_status`) whose "first `get_rows` call" assumption broke once the settings lookup started sharing the same mocked `db.get_rows` | Pin the new behavior; keep the pre-existing test correct without weakening what it verifies |

## 7. Before / after

```python
# Before
async def check_scheduled_rides() -> Optional[bool]:
    try:
        if not await redis_set_nx("spinr:scheduled_rides:lock", "1", ttl=90):
            return None
    except Exception as _lock_err:
        logger.warning(...)
    ...
```

```python
# After
async def check_scheduled_rides() -> Optional[bool]:
    try:
        settings = await get_app_settings()
        if not settings.get("scheduled_dispatch_enabled", True):
            return None
    except Exception as settings_err:
        logger.warning(f"... app_settings lookup failed ({settings_err}), proceeding as enabled")

    try:
        if not await redis_set_nx("spinr:scheduled_rides:lock", "1", ttl=90):
            return None
    except Exception as _lock_err:
        logger.warning(...)
    ...
```

## 8. Rollback plan

This change *is* a rollback mechanism for the dispatcher itself. For the
change as a code diff: plain `git revert`, no migration (the `AppSettings`
field has a Pydantic default, no DB column/migration required — it's a
flat-JSON settings row, not a typed table column). To use the kill switch
in an actual incident: flip `scheduled_dispatch_enabled` to `false` via the
existing settings update path; effective within the 60s settings-cache TTL,
no redeploy.

## 9. Verification performed

- [x] Automated tests: `backend/tests/test_scheduled_dispatch_cr.py`, full
      file, 19 passed (15 prior + 4 new) via the session's venv.
- [x] `ruff check` on all three touched files — clean.
- [ ] Manual repro in staging — not performed, no staging access.
- [x] Blast-radius grep performed (see §4).
- [x] Reviewed against CLAUDE.md's `app_settings`-flag convention and the
      background-loop replay-safety contract — disabling/re-enabling is
      idempotent and loses no data, per the atomic-claim design already in
      place.
- [x] Feature-flagged — this change *is* the feature flag; N/A for itself.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — and is itself the point of
      this change
- [x] Blast radius is stated, not assumed — explicitly an all-or-nothing
      pause, not a partial one
- [x] No silent behavior change — default `true` preserves exact current
      behavior; the only new behavior is opt-in (an admin must act to change
      anything)

## What was NOT verified

No admin-dashboard UI toggle was built, so there is currently no
point-and-click way to flip this flag — only via direct settings-table
update or the app's generic settings API, if one exists (not confirmed in
this session). Flagging this as the honest current state of "how would
someone actually use this kill switch during an incident today" rather than
implying full self-service is done.
