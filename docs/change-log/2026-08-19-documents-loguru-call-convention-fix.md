# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Claude (agent session) |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | (this branch's PR) |
| Related issue or gap ID | Follow-up correction to this session's own PR #4253 (finding #13/#19) |

## 1. Issue / gap identified

PR #4253 (merged earlier this session) fixed `_supersede_and_flag_pending_review`'s
(`backend/documents.py`) silently-swallowed DB error by upgrading it to
`logger.error(..., exc_info=True)` with `%s`-style placeholders — but that
fix was itself broken: `documents.py` logs via **loguru**
(`from loguru import logger`), not stdlib `logging`. loguru formats with
`str.format` (`{}`), not `%`-style, and has no `exc_info` parameter at all.
The bug was caught immediately by this repo's existing
`tests/test_loguru_call_conventions.py` gate on the very next `backend-test`
CI run of a follow-up PR (#4255), before it could ship any further.

## 2. Root cause

The fix in PR #4253 was written by directly mirroring N13's pattern
(`routes/drivers/ride_cancel.py`'s `auth_status=released` write), which is
correct **there** because that module's logger comes from
`routes/drivers/_deps.py`'s `logging.getLogger(__name__)` (stdlib). The
mirror didn't account for `documents.py` using a different logging library
with a different call contract — a mistake this repo already has a
dedicated static-analysis test for (`test_loguru_call_conventions.py`,
which predates this session and was written specifically because this same
class of bug — `%s` placeholders and `exc_info=` — had shipped to
production before, in 55 and 112 call sites respectively).

## 3. Fix / remediation

Changed `logger.error("...%s...", args, exc_info=True)` to
`logger.opt(exception=True).error("...{}...", args)` — loguru's actual
convention for attaching a traceback (`logger.opt(exception=True)`) and
formatting arguments (`{}` positional placeholders), matching the pattern
already used elsewhere in this same file (`documents.py:596`, `:1256`,
`:1262`, `:1270`). Updated the PR #4253 regression test to assert on
`logger.opt(exception=True).error(...)` instead of `logger.error(...,
exc_info=True)`.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated** — same single function as PR #4253, same 3
  callers (2 in `documents.py`, 1 in `routes/admin/documents.py`), none of
  which depend on the log call's internals.
- This is strictly a correctness fix to an already-merged, not-yet-released
  change — the "before" state here (from #4253) was already broken (dropped
  arguments, no traceback), so this can only improve behavior, not regress it.
- No schema, no state-machine, no money path touched.

## 5. User-experience effect

None — internal observability only.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/documents.py` | `logger.error("...%s...", ..., exc_info=True)` → `logger.opt(exception=True).error("...{}...", ...)` | Use loguru's actual call convention; the prior `%s`/`exc_info=True` form silently drops every argument and never captures a traceback under loguru |
| `backend/tests/test_documents.py` | Updated the PR #4253 regression test to assert on `logger.opt(exception=True).error(...)` | Match the corrected call shape |

## 7. Before / after

```python
# Before (PR #4253, broken under loguru)
        logger.error(
            "Could not supersede prior docs for driver %s: %s%s",
            driver_id,
            e,
            f" — {_original}" if _original else "",
            exc_info=True,
        )
```

```python
# After
        logger.opt(exception=True).error(
            "Could not supersede prior docs for driver {}: {}{}",
            driver_id,
            e,
            f" — {_original}" if _original else "",
        )
```

## 8. Rollback plan

`git revert` is safe and sufficient — pure logging-call-shape change, no
data touched.

## 9. Verification performed

- `pytest tests/test_loguru_call_conventions.py tests/test_documents.py -q --no-cov` → 53 passed (was 1 failed under the broken form).
- `ruff check backend/documents.py backend/tests/test_documents.py` → clean.
- Full backend unit-marked suite: `pytest -q --no-cov -m unit` → **2874 passed, 1 skipped** — confirms no other regression introduced.

## 10. What was NOT verified

- Same standing gaps as PR #4253: no live Supabase check (mocked
  `update_one` only); this is a pure log-call-shape correction, not a new
  behavior needing its own live verification.
