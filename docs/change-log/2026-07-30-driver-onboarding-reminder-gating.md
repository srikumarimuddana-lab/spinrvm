# Change Impact & Risk Log — Driver onboarding reminder gating

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-30 |
| Author | Claude Code (session-driven) |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | `9f404cc` |
| Related issue or gap ID | User report: "finish your vehicle info" push arrives daily whether logged in or out |

## 1. Issue / gap identified

The daily 08:00 driver-onboarding reminder loop pushed "Finish your vehicle info
so we can review your driver account" every morning, forever, to drivers who
could not act on it — including already-approved (`active`) drivers and
`rejected`/`suspended` ones.

## 2. Root cause

Two independent defects in `backend/utils/driver_onboarding_reminders.py`:

1. **Denylist instead of allowlist.** `should_skip_driver` skipped only
   `deleted_at` and `status == "banned"`. Every other status — `active`,
   `needs_review`, `rejected`, `suspended` — was eligible. `_has_vehicle`
   (`backend/onboarding_status.py:79`) ANDs four fields
   (`vehicle_make`, `vehicle_model`, `license_plate`, `vehicle_type_id`), so an
   approved driver missing only `vehicle_type_id` looked "incomplete" forever.
2. **No terminal condition.** The `driver_onboarding_reminder_log` claim row
   deduped *within* a local day but nothing counted across days, so the loop
   had no stopping point.

Aggravating factor (not changed here): `backend/routes/drivers/profile.py:187`
auto-creates a `pending` driver row on the first PATCH from the vehicle-info
screen, so riders who tap into that screen once enter the reminder population.

## 3. Fix / remediation

- Replaced the denylist with an allowlist — `pending` only. An unrecognised or
  newly-added status now defaults to silence rather than to a daily push.
- Capped reminders at 7 per driver per reminder type, counted from existing
  `driver_onboarding_reminder_log` rows via one batched read per 200-driver page.
- Both values overridable through `app_settings` without a redeploy.
- A failed claim-log read now leaves the send window incomplete instead of
  processing the page blind (mirrors the existing docs-read failure handling).

## 4. Risk & impact on existing functionality

**Blast radius: isolated to this loop.**

`should_skip_driver` and `reminder_cap_reached` are defined in
`driver_onboarding_reminder_rules.py` and imported by exactly one non-test
module — `driver_onboarding_reminders.py`. Verified by
`grep -rn "should_skip_driver" backend/ --include=*.py`: three hits, all inside
the rules module, the loop, and its test.

`_has_vehicle` **is** shared — `backend/onboarding_status.py:136` (driver
onboarding status derivation) and `backend/diagnose_driver_ws.py` mirror it.
It was **read, not modified**, so those consumers are unaffected.

New signature is backward compatible: `should_skip_driver(driver)` still works
(second arg defaults to `DEFAULT_REMINDABLE_STATUSES`).

Background-loop interaction: this is one of the 16 startup loops
(`backend/core/lifespan.py:259`). The new claim-log read runs per page inside
the once-per-day send window only, so per-tick cost outside 08:00 local is
unchanged (still a single `service_areas` read). Replay-safety is unchanged —
the DB claim log remains the cross-replica idempotency key.

**What could regress:** a genuinely-incomplete `pending` driver who ignores the
first 7 reminders now stops hearing from us. That is intended, but it does mean
onboarding-funnel drop-off is no longer chased indefinitely by push. No ride,
dispatch, payment, or insurance-period path is touched.

## 5. User-experience effect

- **Driver-facing.** Approved, rejected and suspended drivers stop receiving a
  daily push they could not act on. Pending drivers get at most 7 instead of
  unbounded.
- Not visible mid-session — no screen, flow, or in-app state changes. The
  in-app notification-inbox row is written by `send_push_notification`, so
  suppressed reminders also stop appearing in the Notifications list.
- No copy change; existing strings are untouched.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/driver_onboarding_reminder_rules.py` | Added `DEFAULT_REMINDABLE_STATUSES`, `DEFAULT_MAX_REMINDERS_PER_TYPE`, `driver_status`, `reminder_cap_reached`, `parse_remindable_statuses`; `should_skip_driver` now takes an allowlist | Eligibility + cap rules kept pure and unit-testable |
| `backend/utils/driver_onboarding_reminders.py` | Added `_reminder_settings`, `_prior_reminder_counts`; threaded allowlist/cap through `_scan_pages`; new `capped_skips` stat | Wire the rules in with a batched (non-N+1) log read |
| `backend/tests/test_driver_onboarding_reminders.py` | 8 new tests (status gating ×5 params, blank status, cap, settings overrides, log-read failure, settings-failure fallback) | Regression cover |

## 7. Before / after

```python
# Before — everyone except banned, forever
def should_skip_driver(driver):
    return bool(
        not driver.get("id") or not driver.get("user_id")
        or driver.get("deleted_at") or driver.get("status") == "banned"
    )

if not _has_vehicle_details(driver):
    await _send(driver, VEHICLE_DETAILS, local_date, now)
```

```python
# After — pending only, capped
def should_skip_driver(driver, remindable_statuses=None):
    statuses = DEFAULT_REMINDABLE_STATUSES if remindable_statuses is None else remindable_statuses
    return bool(
        not driver.get("id") or not driver.get("user_id")
        or driver.get("deleted_at") or driver_status(driver) not in statuses
    )

if not _has_vehicle_details(driver):
    if reminder_cap_reached(prior.get((driver_id, VEHICLE_DETAILS), 0), max_per_type):
        stats["capped_skips"] += 1
    else:
        await _send(driver, VEHICLE_DETAILS, local_date, now)
```

## 8. Rollback plan

**Config-only, no redeploy.** Two `app_settings` keys restore the old behaviour
exactly:

| Key | Set to | Effect |
|---|---|---|
| `driver_onboarding_reminder_statuses` | `"pending,active,needs_review,rejected,suspended"` | Restores the old audience (everything except `banned`) |
| `driver_onboarding_reminder_max_days` | `0` | Disables the cap entirely |

Setting both reproduces pre-fix behaviour without touching code. Verified by
`test_cap_can_be_disabled_via_settings` and
`test_status_allowlist_can_be_widened_via_settings`.

No data migration was applied, so there is nothing to un-apply. Suppressed
pushes are not recoverable, but no state was mutated by suppressing them.

## 9. Verification performed

- [x] Automated tests run — `pytest tests/test_driver_onboarding_reminders.py`
      **19 passed**; plus `tests/test_db_circuit_breaker.py` (other consumer of
      this loop) **22 passed** combined. Unit tier, `mock_supabase_client`-style
      fakes, no real DB.
- [ ] Manual repro steps followed in staging — **not done**, see §10
- [x] Blast-radius grep performed — `should_skip_driver`, `_has_vehicle`,
      `missing_required_document_uploads`, `driver_onboarding_reminder` across
      `backend/ --include=*.py`
- [x] Reviewed against `CLAUDE.md` conventions — background-loop replay safety,
      no-N+1 (batched `$in` read), no silent error swallowing (log-read failure
      leaves the window incomplete and logs at error level), no PII in logs
      (driver_id/user_id only)
- [x] Feature-flagged — via `app_settings`, per the flag-without-redeploy pattern
- [x] `ruff check` clean on all changed files

## 10. What was NOT verified

- **Not run against live or staging Supabase.** All coverage is against the
  in-test `FakeReminderDB`; the `driver_onboarding_reminder_log` `$in` filter
  compiles to PostgREST and was not exercised against a real PostgREST instance.
- **No production build run** — backend-only change, no frontend surface touched.
- **Real driver population not sampled.** I did not query how many `active`
  drivers currently have an incomplete `_has_vehicle`, so the size of the
  affected group is unknown. Worth checking before/after via the
  `onboarding reminders: {...}` info log, which now includes `capped_skips`.
- **The 7-reminder cap is a judgement call**, not a product decision — it was
  chosen to be conservative and is config-tunable. If product wants a different
  number, change `driver_onboarding_reminder_max_days` rather than the code.
- **Timezone edge cases unchanged and unretested here** — the existing 08:00
  send-window logic was not modified.
