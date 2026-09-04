# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude Code (session 01Sspqro7zzjKdTbUh6D61wQ) |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | branch `claude/c70-matching-py-latent-dispatch-bugs` |
| Related issue or gap ID | Found while investigating a `backend-test` CI failure on 3 unrelated PRs (#4964/#4965/#4966); not itself an ACTION_ITEMS.md-tracked item — filed retroactively as **C70** |

## 1. Issue / gap identified

`backend-test` was failing on `main` itself (confirmed by running the failing tests against a clean clone of `origin/main`, unrelated to any pending PR's own diff) with two distinct defects, both in `backend/routes/rides/matching.py` — the dispatch hot path:

1. **Stale test mocks, not an application bug**: `tests/test_dispatch_db_errors.py` and `tests/test_dispatch_match_attempt_branches.py` each had exactly one `resolve_matching_config` mock still returning a 5-element tuple (`("nearest", 0, 10.0, 3, False)`), left over from before PR #4948 (Dispatch Engine) widened the real function's return signature to 6 elements (`..., max_candidate_pool`). Every other mock of the same function in both files (12 of 14 call sites) was already updated to 6 elements — these 2 were missed. `_match_driver_to_ride_attempt`'s unpack (`... = await resolve_matching_config(...)`) then raised `ValueError: not enough values to unpack (expected 6, got 5)` inside the mocked test, not in production (production always calls the real 6-tuple function).
2. **Real bug, same family as C60/C65/C69**: `matching.py`'s "unsafe pool config" warning (added by the same dispatch-geo-provider feature, after C65's repo-wide `%s`→`{}` sweep had already run) used `%s`/`%d` placeholders on a loguru-bound `logger.warning(...)` call. loguru formats with `str.format` — `%`-style placeholders are emitted literally and every argument (`ride_id`, `max_candidate_pool` ×2) is silently dropped. `test_loguru_call_conventions.py::test_no_percent_style_placeholders_in_loguru_calls` already covers this class of bug (added by C65/C69's work) and correctly caught it — it just hadn't been fixed yet in this specific, newer line.

## 2. Root cause

Both are the same shape of gap this session has repeatedly found today (C63/C64/C69 all trace to a feature landing after — or alongside — a repo-wide sweep that didn't/couldn't cover it): PR #4948 added `max_candidate_pool` to the matching-config tuple and a new warning log line, but its own test updates missed 2 of 14 mock call sites, and its new log line predates (or was written in parallel with) C65's loguru-placeholder sweep, so it was never caught by that pass.

## 3. Fix / remediation

- `backend/routes/rides/matching.py`: `%s`/`%d` → `{}` in the "unsafe pool config" warning (3 placeholders, 3 positional args — output-identical, same values in the same order).
- `backend/tests/test_dispatch_db_errors.py` (1 site) and `backend/tests/test_dispatch_match_attempt_branches.py` (1 site): appended the missing `, 500` to each stale 5-tuple mock, matching the other 12 already-correct call sites in the same files.

No production code path changed in behavior — the tuple-unpack bug only ever fired inside a test's mock, never against the real `resolve_matching_config`.

## 4. Risk & impact on existing functionality

- Blast radius: 3 files — 1 production log line (cosmetic/observability only, no control-flow change), 2 test-fixture mocks (test-only). Grepped for every other `resolve_matching_config` mock in both test files (14 total call sites) — confirmed the other 12 were already correct and untouched by this fix.
- No change to dispatch algorithm, offer logic, or claim/release behavior — this is strictly a log-formatting fix plus a test-fixture correction.
- Risk of regression: effectively none. `ruff check` clean; full `matching`/`dispatch`-filtered suite (492 passed, 4 skipped) and the 3 directly-affected files (30 passed) run clean after the fix.

## 5. User-experience effect

None. Backend-only, observability/test-fixture fix. No rider/driver/admin-facing behavior change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/matching.py` | `%s`/`%d` → `{}` in one `logger.warning(...)` call (3 placeholders) | loguru drops `%`-style args silently; same defect class as C60/C65/C69 |
| `backend/tests/test_dispatch_db_errors.py` | Appended missing 6th tuple element (`500`) to 1 stale `resolve_matching_config` mock | Mock predated PR #4948's 6-tuple return signature |
| `backend/tests/test_dispatch_match_attempt_branches.py` | Appended missing 6th tuple element (`500`) to 1 stale `resolve_matching_config` mock | Same as above |

## 7. Before / after

```python
# Before (backend/routes/rides/matching.py)
logger.warning(
    "[DISPATCH] unsafe pool config ride_id=%s: provider=legacy with "
    "max_candidate_pool=%d (<200). ...",
    ride_id,
    max_candidate_pool,
    max_candidate_pool,
)

# After
logger.warning(
    "[DISPATCH] unsafe pool config ride_id={}: provider=legacy with "
    "max_candidate_pool={} (<200). ...",
    ride_id,
    max_candidate_pool,
    max_candidate_pool,
)
```

```python
# Before (both test files)
AsyncMock(return_value=("nearest", 0, 10.0, 3, False)),

# After
AsyncMock(return_value=("nearest", 0, 10.0, 3, False, 500)),
```

## 8. Rollback plan

`git revert` — pure log-formatting + test-fixture change, no data, no migration, no config, no Stripe/wallet/ride-state involved.

## 9. Verification performed

- [x] Reproduced the original failure against a clean clone of `origin/main` first (not just trusted the CI log), confirming it predates and is unrelated to the 3 PRs (#4964/#4965/#4966) whose CI runs surfaced it.
- [x] `pytest tests/test_dispatch_db_errors.py tests/test_dispatch_match_attempt_branches.py tests/test_loguru_call_conventions.py --no-cov` → 30/30 passed (was 3 failed before the fix).
- [x] `pytest tests/ -k "matching or dispatch" --no-cov` → 492 passed, 4 skipped, 0 failed (broader regression sweep).
- [x] `ruff check` on all 3 touched files → clean.
- [x] Grepped both test files for every `resolve_matching_config` mock (14 total) to confirm only these 2 were stale.

## 10. What was NOT verified

- Not tested against a live Supabase/production dispatch flow — this is a mocked-unit-test and log-formatting fix only; no live dispatch traffic exists in this sandbox.
- Did not audit `matching.py` for any other newer `%`-style loguru calls beyond the one `test_no_percent_style_placeholders_in_loguru_calls` flagged — the test itself is the authoritative detector (proven non-vacuous by C65/C69's work) and it now passes clean, so no further manual sweep was performed.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no data involved)
- [x] Blast radius is stated, not assumed (3 files, all other mock call sites in both test files individually confirmed already-correct)
- [x] No silent behavior change to an already-shipped flow — the log-line fix is output-identical in content, only correctly formatted; the test-fixture fix makes 2 previously-broken-in-isolation tests pass, no assertion behavior changed
