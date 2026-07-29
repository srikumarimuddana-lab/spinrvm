# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude (ACTION_ITEMS.md A1b, Track 1 item 2 follow-up — found while writing coverage tests for `safety_checkin_loop.py`) |
| Surface(s) | backend |
| Domain (Sentry tag) | safety |
| PR / commit link | branch `claude/a1b-safety-coverage`, commit `ea9bc1ee` |
| Related issue or gap ID | Follow-up to `docs/change-log/2026-07-29-a1b-safety-checkin-loop-coverage.md` §12 |

## 1. Issue / gap identified

`backend/utils/safety_checkin_loop.py`'s automated no-response escalation never actually notified the safety team, despite the safety-incident row and audit-log entry being written correctly.

## 2. Root cause

The module uses the repo's standard dual-import pattern (see `CLAUDE.md` → "Dual import pattern"):

```python
try:
    from ..db import db as _supabase_db
    from ..features import notify_safety_team, send_push_notification
    from ..socket_manager import manager as _ws_manager
    from .audit_logger import log_admin_action as _log_audit
except ImportError:
    from db import db as _supabase_db  # type: ignore
    from features import send_push_notification  # type: ignore
    from utils.audit_logger import log_admin_action as _log_audit  # type: ignore
```

The `except ImportError` fallback branch never imported `notify_safety_team`. This is the branch actually taken in production — confirmed by the existing test suite, which imports the module the same way `core/lifespan.py` does (`from utils.safety_checkin_loop import ...`, not `backend.utils...`) and triggers the fallback.

`_escalate()` calls `notify_safety_team(incident)` inside its own `try/except Exception`, so the resulting `NameError` was caught, logged as a generic "`notify_safety_team failed for incident ...`" line, and swallowed — the escalation appeared to succeed (incident row + audit log both write), but the safety team's WS broadcast, email, and PagerDuty-triggering log line never fired.

This bug predates this session; it was not introduced by any change in this sprint. It was found as a side effect of writing coverage tests, not by intentionally auditing this path.

## 3. Fix / remediation

Added `notify_safety_team` (and `_ws_manager`, for import-symmetry with the `try` branch, currently unused directly by this module but kept consistent with the mirrored import list) to the `except ImportError` fallback so both import paths bind the same names. One-line functional fix plus one import-symmetry addition.

Added a regression test, `test_escalate_calls_notify_safety_team` in `backend/tests/test_safety_checkin_loop.py`, that patches `notify_safety_team` and asserts `_escalate` awaits it. Verified directly:
- Against the pre-fix code (via `git stash` of only the production file): test fails with `AttributeError: ... does not have the attribute 'notify_safety_team'` — proving the bug is real and reproducible.
- Against the fix: test passes.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped for other importers/callers:
  - `backend/core/lifespan.py` — the only caller of `safety_checkin_loop()` (one of the 16 startup background loops). No signature or behavior change to the loop's public entry point.
  - `notify_safety_team` itself (`backend/features.py`) — already used identically by `backend/routes/safety.py`'s `POST /safety/report` handler (see `docs/change-log/2026-07-29-a1b-routes-safety-coverage.md`), so its own behavior/contract is untouched; this change only makes an existing call site actually reach it.
  - No other file imports from `utils/safety_checkin_loop.py`.
- **Could this regress a currently-working flow?** No currently-working flow depended on `notify_safety_team` *not* firing — the previous behavior was an unintended silent failure, not a designed no-op. Fixing it can only add a notification that should have always been sent, not remove one.
- **Interaction with the 16 background loops:** none beyond `safety_checkin_loop`'s own tick; no shared state, table, or Redis key touched by this change beyond what `_escalate` already wrote (incident row via `_supabase_db.insert_one`, `escalated_key` via `redis_set`) — both unchanged.
- **Money/wallet/ride-state-machine:** not touched.

## 5. User-experience effect

- **Rider/driver-facing:** none directly — this is an internal safety-team paging path.
- **Internal admin (safety/trust-and-safety team) facing:** yes — this is the behavior change. Going forward, an auto-escalated "rider did not respond to safety check-in" incident will actually trigger the admin WS broadcast, email to the safety distribution list, and the CRITICAL log line used for on-call paging, instead of silently only writing the DB row.
- **Visible mid-session?** N/A — this fires from a backend background loop on a 90-second no-response timer, not from any user-facing screen; no rider/driver app state changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/safety_checkin_loop.py` | Added `notify_safety_team` (and `_ws_manager`) to the `except ImportError` fallback import list | Bug fix — mirror the `try` branch so the safety team is actually notified on auto-escalation |
| `backend/tests/test_safety_checkin_loop.py` | Added `test_escalate_calls_notify_safety_team` | Regression coverage; fails pre-fix, passes post-fix |

## 7. Before / after

```python
# Before
except ImportError:
    from db import db as _supabase_db  # type: ignore
    from features import send_push_notification  # type: ignore
    from utils.audit_logger import log_admin_action as _log_audit  # type: ignore
```

```python
# After
except ImportError:
    from db import db as _supabase_db  # type: ignore
    from features import notify_safety_team, send_push_notification  # type: ignore
    from socket_manager import manager as _ws_manager  # type: ignore # noqa: F401
    from utils.audit_logger import log_admin_action as _log_audit  # type: ignore
```

## 8. Rollback plan

`git revert` is sufficient and complete here — this fix touches only an import list and adds a test; it does not mutate any already-written data. Any incident rows or audit-log entries written before or after this fix are correct either way (the DB/audit side was never broken). Reverting simply returns the safety-team notification to its previous (broken, silently-swallowed) state — no data-level remediation needed in either direction.

## 9. Verification performed

- [x] Automated tests: `pytest tests/test_safety_checkin_loop.py` — 18 passed (was 17 before this change), 0 failed.
- [x] Regression test verified to fail against pre-fix code (via `git stash` isolating the test-file change against the unfixed production file) and pass against the fix — not just "tests pass," an actual before/after reproduction.
- [x] `ruff check backend/utils/safety_checkin_loop.py` — clean (no unused-import warning despite `_ws_manager` being unused, consistent with the `try` branch which has the same unused-but-mirrored import).
- [x] Blast-radius grep performed (§4) — one caller (`core/lifespan.py`), no other importers.
- [x] Reviewed against `CLAUDE.md`'s "Dual import pattern" and "Do not silently swallow errors" conventions — this fix directly closes a case of the latter.
- [x] Not feature-flagged: not applicable — this is a pure bug fix restoring already-intended, already-shipped-looking behavior (the `try` branch already had the correct import; only the fallback was missing it), not new user-visible functionality requiring staged rollout.

## 10. What was NOT verified

- Not run against a real Supabase DB, Redis, or the real `notify_safety_team` implementation (WS broadcast / email / PagerDuty log) — verified only that the function is now *reached and awaited*, per the repo's existing mocked-Supabase test convention. The correctness of `notify_safety_team`'s own internals is out of scope for this fix (it's exercised elsewhere, see `docs/change-log/2026-07-29-a1b-routes-safety-coverage.md`).
- No production Sentry/log history was checked to confirm how long this bug had been live or how many real escalations were affected — no access to live logs from this environment. The fix is justified by static analysis + direct reproduction of the import failure, not a confirmed incident count.
- No staging deploy was performed; this is a backend-only, non-flagged, low-risk fix per the sign-off rationale above.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-level remediation required).
- [x] Blast radius is stated, not assumed (§4) — one caller, no other importers, no shared-state interaction beyond what already existed.
- [x] No silent behavior change: this fix is itself the explicit, tested correction of a previously-silent behavior gap; it was surfaced (not fixed) in the original coverage PR per that PR's own "escalate, don't silently ship" note, and fixed here only after explicit user direction ("Fix it now in this PR").
