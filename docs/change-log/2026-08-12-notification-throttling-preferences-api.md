# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Author | Claude Code (spinr platform) |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | (branch `claude/rideshare-app-analysis-blx3pn`, subtask 5/5) |
| Related issue or gap ID | subtask 5/5 of the notification-throttling feature |

## 1. Issue / gap identified

A future rider/driver settings screen has no way to learn the current global quiet-hours window or daily cap from `GET /notifications/preferences`.

## 2. Root cause / scope note (reads as a plan deviation — flagging explicitly)

The original 5-subtask plan (see #3719 and this branch's earlier commits) assumed subtask 5 would "expose quiet-hour fields in the notification-preferences API" as if they were per-user columns. They are not: subtask 1 deliberately implemented quiet-hours/cap as **global** `AppSettings` values (migration 304), not per-user `notification_preferences` columns, specifically to avoid a migration touching a live per-user table for a V1 with no settings UI. There is therefore nothing per-user to "expose" from that table.

Rather than silently dropping this subtask or bolting on unplanned per-user override columns (scope creep beyond what was asked), this commit implements the honest version: `GET /notifications/preferences` now also returns the **current global** throttling config, read-only, merged into the response — so a future settings screen can display "Quiet hours: 10 PM–7 AM" without implying per-user customization exists. `PUT /notifications/preferences` is unchanged — a rider/driver cannot set these fields from this endpoint (they aren't in `PreferencesUpdate`'s schema, so FastAPI/Pydantic silently drops any such field a client might send).

## 3. Fix / remediation

Added `_global_throttle_info()` helper in `routes/notifications.py`, called from `get_preferences()` and merged into both response branches (no-existing-row defaults, and existing-row passthrough).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Only `GET /notifications/preferences`'s response shape changed (four new keys added, nothing removed or renamed). `PUT /notifications/preferences` is untouched.
- **Who else reads this response:** rider-app and driver-app settings screens (not yet built to read these new fields, so they're inert extra keys from the client's perspective today — additive, not breaking, per standard REST client tolerance of unknown JSON fields).
- **Existing consumers of the old shape:** none broken — existing keys (`push_enabled`, etc.) are unchanged, tests asserting on those specific keys still pass unmodified.
- **Extra DB round trip:** one `get_app_settings()` call per `GET /notifications/preferences` request — this call is already cached in-process for 60s (`settings_loader.py`'s existing TTL cache), so this is not a new uncached Supabase round trip on a hot path.

## 5. User-experience effect

- **Who sees a difference:** nobody yet — no rider-app/driver-app screen reads these fields in this pass (explicitly out of scope, matching the original plan's "no rider-app/driver-app UI in this pass").
- **Visible mid-session?** No.
- **Copy/notification change:** none.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/notifications.py` | Added `_global_throttle_info()`; merged its output into both `get_preferences()` response branches | Gives a future settings screen the current global config without implying per-user control |
| `backend/tests/test_p3_push_notifications.py` | +3 tests: defaults-row includes throttle info, existing-row also gets it (merged correctly), missing-settings-keys fail open to documented defaults | Direct coverage; confirmed the two pre-existing preference tests (`test_default_prefs_returned_when_no_row`, `test_update_prefs_partial_only_sets_non_none`) still pass unmodified |

## 7. Before / after

```python
# Before
    if not prefs:
        return {"push_enabled": True, ...}
    return prefs
```

```python
# After
    throttle_info = await _global_throttle_info()
    if not prefs:
        return {"push_enabled": True, ..., **throttle_info}
    return {**prefs, **throttle_info}
```

## 8. Rollback plan

`git revert` is sufficient and safe — this is a read-only, additive response-field change with no schema, no migration, no state mutation. No feature flag needed for the exposure itself (the underlying `notification_throttling_enabled` flag it reports on already has its own kill switch from subtask 1).

## 9. Verification performed

- [x] Automated tests run — 3 new tests + full re-run of `test_p3_push_notifications.py` (43/43, including all tests from subtasks 3 and this one).
- [ ] Manual repro in staging — not performed (no live Supabase/staging access this session).
- [x] Blast-radius grep performed — confirmed `get_preferences`'s only caller is the `GET /notifications/preferences` route itself; no internal backend code depends on its exact response shape.
- [x] Reviewed against relevant `CLAUDE.md` conventions — dual-import pattern, no error-swallowing (a `get_app_settings()` failure here would propagate as an unhandled exception → 500, same as any other unguarded await in this handler; not silently masked).
- [x] No feature flag needed — additive, read-only, no behavior change to any existing field.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — plain `git revert`.
- [x] Blast radius is stated, not assumed — single route, single response shape, no other consumers.
- [x] No silent behavior change to an already-shipped flow — existing fields unchanged, only new keys added.
