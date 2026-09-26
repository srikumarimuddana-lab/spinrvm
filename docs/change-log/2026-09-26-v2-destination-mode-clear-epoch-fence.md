# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-26 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers (destination-mode / go-offline) |
| PR / commit link | (this branch, fix/5781-v2-destination-mode-race) |
| Related issue or gap ID | #5781 findings 3 and 4 |

## 1. Issue / gap identified

**Finding 3**: `backend/routes/drivers/status.py`'s `_clear_destination_mode_after_v2_offline()` runs as an unconditional, unfiltered write after the v2 go-offline transition (`_finish_v2_status`) succeeds, with no epoch/request-id check against a newer go-online. A driver who goes offline, then immediately back online and sets a fresh destination, could have that new destination silently wiped by this trailing write if it lands after the new destination is set.

**Finding 4**: `KNOWN_SETTINGS_COLUMNS` in `test_admin_settings_write_allowlist_drift.py` was missing migration 469's four `dispatch_expanded_radius_*` columns, which are live in schema and wired into `SettingsUpdateRequest`. The test's assertion is one-directional (checks snapshot columns are in the model, not the reverse), so this gap was silent rather than failing.

## 2. Root cause

**Finding 3**: the legacy go-offline path clears destination mode in the *same* atomic write as the offline flip (naturally fenced by that write's own filters). The v2 path's transition happens inside a separate RPC (`transition_driver_availability`), so the destination-clear has to be a second, later write — and that second write was never given the same epoch fencing the RPC itself already uses internally.

**Finding 4**: migration 469 added the four columns and wired them into `SettingsUpdateRequest` in the same PR, but the drift test's `KNOWN_SETTINGS_COLUMNS` snapshot (a maintained list, not a live scan, per the test file's own docstring) was not updated in that PR.

## 3. Fix / remediation

**Finding 3**: `_clear_destination_mode_after_v2_offline()` now takes the go-offline transition's own `online_epoch` and includes it in the write filter (`{"id": driver_id, "online_epoch": expected_epoch}`). `online_epoch` is bumped on every real availability transition (migration 457/486), so if the driver has already gone back online by the time this write runs, the filter matches zero rows and the fresh destination is left alone — logged at INFO, not treated as a failure. A missing/unparsable epoch falls back to the pre-fix unconditional write rather than silently never clearing.

**Finding 4**: added the four columns to `KNOWN_SETTINGS_COLUMNS`.

## 4. Risk & impact on existing functionality

- Blast radius: `_clear_destination_mode_after_v2_offline` has exactly one caller (`update_driver_status`'s v2 go-offline branch, `routes/drivers/status.py`). No other code reads or writes this function.
- The v2 availability path itself (`driver_availability_v2_enabled`) is default-off in production (confirmed via this session's earlier migration review of 486) — this fix changes behavior only within an already-dark feature path.
- Existing test suite (`test_go_offline_clears_destination.py`'s pre-existing v2 tests) mocks `_finish_v2_status` to return `{"ok": True}` with no `transition` key — under the new code this parses to `expected_epoch=None`, which falls back to the exact old unconditional-write behavior, so those tests pass unchanged (verified: 6/6 pass, plus 3 new tests for the fencing behavior itself, 9/9 total).
- `KNOWN_SETTINGS_COLUMNS` addition is test-only; no runtime behavior change.

## 5. User-experience effect

Driver-facing, v2-availability-path only (currently dark in production). Fixes a narrow race where a driver rapidly toggling offline→online could have a freshly-set "heading home" destination silently cleared. No effect on any rider-facing flow or on the legacy (currently-live) availability path.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/status.py` | `_clear_destination_mode_after_v2_offline` takes `expected_epoch` and fences its write on `online_epoch`; call site passes the transition's own epoch | Close the race where a newer go-online's destination could be wiped by a stale go-offline's trailing clear |
| `backend/tests/test_go_offline_clears_destination.py` | Added 3 tests: normal-case epoch match, epoch-advanced skip, unparsable-epoch fallback | Pin the new fencing behavior |
| `backend/tests/test_admin_settings_write_allowlist_drift.py` | Added `dispatch_expanded_radius_enabled`, `dispatch_expanded_radius_after_seconds`, `dispatch_expanded_radius_multiplier`, `dispatch_expanded_radius_max_km` to `KNOWN_SETTINGS_COLUMNS` | Close a silent snapshot-drift gap from migration 469 |

## 7. Before / after

```python
# Before
async def _clear_destination_mode_after_v2_offline(driver_id: str) -> None:
    try:
        await db_supabase.update_one("drivers", {"id": driver_id}, dict(_DESTINATION_MODE_CLEARED))
    except Exception as exc:
        ...

# call site
if v2_action == "go_offline":
    await _clear_destination_mode_after_v2_offline(driver_id)
```

```python
# After
async def _clear_destination_mode_after_v2_offline(driver_id: str, expected_epoch: int | None) -> None:
    filters: dict = {"id": driver_id}
    if expected_epoch is not None:
        filters["online_epoch"] = expected_epoch
    try:
        updated = await db_supabase.update_one("drivers", filters, dict(_DESTINATION_MODE_CLEARED))
        if expected_epoch is not None and not isinstance(updated, dict):
            logger.info("C136: skipped clearing destination mode ... online_epoch advanced past %s", expected_epoch)
    except Exception as exc:
        ...

# call site
if v2_action == "go_offline":
    _offline_transition = response.get("transition") or {}
    _offline_epoch_raw = _offline_transition.get("online_epoch")
    try:
        _offline_epoch = int(str(_offline_epoch_raw)) if _offline_epoch_raw is not None else None
    except (TypeError, ValueError):
        _offline_epoch = None
    await _clear_destination_mode_after_v2_offline(driver_id, _offline_epoch)
```

## 8. Rollback plan

`git revert` — pure code-path change, no schema/migration touched.

## 9. Verification performed

- `pytest tests/test_go_offline_clears_destination.py` — 9 passed (6 pre-existing + 3 new).
- `pytest tests/test_admin_settings_write_allowlist_drift.py` — 8 passed.
- `pytest tests/test_drivers_shared_status_profile_coverage.py tests/test_drivers_extended.py` — 239 passed total across all four files, confirming no regression in the broader driver-status surface.
- `ruff check` / `ruff format --check` clean on all three modified files.

## What was NOT verified

- Not tested against a live/staging backend — mocked-Supabase unit tests only.
- The v2 availability path is default-off in production, so this fix has no live-traffic exposure to verify against today.
- Did not attempt to fix the separately-tracked, self-documented residual go-offline race from commit `e8d4d8335` (finding 2) or run the push-token cleanup script (finding 1) — both are explicitly out of scope for this fix per the issue's own guidance (finding 2 needs live-DB verification before any further fix attempt; finding 1 requires human approval to run against production).
