# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | Claude (session implementing PR #5085's hardening plan) |
| Surface(s) | backend (admin auth) |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/pr-5085-5079-hardening-5a2aj7` |
| Related issue or gap ID | F12, validated in PR #5085 (`docs/audit/2026-09-07-pr-5079-validation-and-hardening-plan.md`, "PR-6"); tracked as `ACTION_ITEMS.md` C81 |

## 1. Issue / gap identified

`_verify_admin_payload` (the admin JWT verification dependency, run on every authenticated admin request) performed an uncached `admin_staff` read plus an unconditional `update_one` write of `last_activity_at` on every single request — purely to serve a 30-minute idle-session check that only needs roughly-current staleness, not per-request precision.

## 2. Root cause

The write was never coalesced — every request re-stamped `last_activity_at` regardless of how recently it had last been stamped, even under normal admin-dashboard polling (every 5-60 seconds).

## 3. Fix / remediation

Added a 60-second coalescing window (`_ACTIVITY_TOUCH_INTERVAL_S`): the write is skipped when the existing, successfully-parsed `last_activity_at` is younger than that interval. A `NULL` or malformed timestamp always falls through to a write (unchanged from before), and the idle-timeout check itself (`_IDLE_SECONDS`, 30 minutes) is untouched — only the write frequency changes, not the timeout logic.

## 4. Risk & impact on existing functionality

- **Blast radius**: isolated to `_verify_admin_payload` in `backend/dependencies/__init__.py`. Grepped for other readers/writers of `admin_staff.last_activity_at` — only this function writes it; `routes/admin/auth.py`'s login flow resets it on login (untouched by this change) and is the only other writer.
- **Idle-detection precision degrades by up to 60 seconds** — a revoked/idle admin session could in the worst case go undetected up to 60s longer than before. Disclosed explicitly, not silently accepted: this trades a small amount of idle-detection slack for a large reduction in write volume under normal dashboard polling.
- No change to the 30-minute idle timeout itself, to `ERR_IDLE_TIMEOUT`/`ERR_SESSION_REVOKED`/`ERR_ACCOUNT_INACTIVE` rejection paths, or to `token_version` revocation — all still checked on every request, unaffected.

## 5. User-experience effect

None visible to an admin under normal use — the 30-minute idle timeout still fires within the same window (plus up to 60s of the disclosed slack above). Not visible mid-session in any way a user would notice (no UI change).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/dependencies/__init__.py` | `_verify_admin_payload` skips the `last_activity_at` write when the existing value is younger than 60s | Reduce write load; F12 |
| `backend/tests/test_dependencies_auth_gaps.py` | Added `…skips_activity_write_when_fresh` and `…writes_when_stale_but_not_idle` | Pin both edges of the new throttle |

## 7. Before / after

```python
# Before
_IDLE_SECONDS = 30 * 60
last_active_raw = staff.get("last_activity_at")
if last_active_raw:
    try:
        last_active = datetime.fromisoformat(last_active_raw.replace("Z", "+00:00"))
        if (datetime.now(timezone.utc) - last_active).total_seconds() > _IDLE_SECONDS:
            raise HTTPException(status_code=401, detail="ERR_IDLE_TIMEOUT")
    except HTTPException:
        raise
    except Exception as _ts_err:
        logger.warning(...)
await db_supabase.update_one("admin_staff", {"id": user_id}, {"last_activity_at": ...})
```

```python
# After
_IDLE_SECONDS = 30 * 60
_ACTIVITY_TOUCH_INTERVAL_S = 60
last_active_raw = staff.get("last_activity_at")
last_active_parsed: Optional[datetime] = None
if last_active_raw:
    try:
        last_active_parsed = datetime.fromisoformat(last_active_raw.replace("Z", "+00:00"))
        if (datetime.now(timezone.utc) - last_active_parsed).total_seconds() > _IDLE_SECONDS:
            raise HTTPException(status_code=401, detail="ERR_IDLE_TIMEOUT")
    except HTTPException:
        raise
    except Exception as _ts_err:
        logger.warning(...)
activity_is_fresh = last_active_parsed is not None and (
    datetime.now(timezone.utc) - last_active_parsed
).total_seconds() < _ACTIVITY_TOUCH_INTERVAL_S
if not activity_is_fresh:
    await db_supabase.update_one("admin_staff", {"id": user_id}, {"last_activity_at": ...})
```

## 8. Rollback plan

`git revert` — pure code change, no schema/flag/migration. Reverting restores the previous unconditional-write behavior exactly.

## 9. Verification performed

- [x] Automated tests: `pytest tests/test_dependencies_auth_gaps.py tests/test_admin_login_resets_idle_clock.py -q` — 25 passed, 0 failed.
- [x] `ruff check`/`ruff format --check` — clean.
- [x] Blast-radius grep: `admin_staff.last_activity_at` has exactly one other writer (`routes/admin/auth.py` login reset), unaffected by this change.

## What was NOT verified

Not tested against a live admin dashboard's real polling cadence — verified at the unit-test level (mocked timestamps at fixed offsets), not by observing actual request volume under a real session.
