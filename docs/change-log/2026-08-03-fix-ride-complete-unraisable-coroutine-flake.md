# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-03 |
| Author | Claude |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | (this branch) |
| Related issue or gap ID | Follow-up to A1c Sub-tier C Batch 5 (PR #3364) |

## 1. Issue / gap identified

`backend-test` CI intermittently fails with `pytest.PytestUnraisableExceptionWarning: Exception ignored in: <coroutine object send_driver_activated ...>`, attributed to `TestFireDriverActivated::test_spawn_failure_is_logged_not_raised`, whenever the full suite (or specific 4+ file combinations including `test_ride_complete_coverage.py`) runs. First observed on PR #3364's CI run (8931 passed, 1 error) after it had already passed locally in isolation.

## 2. Root cause

`test_both_import_paths_failing_is_swallowed` (the *other* test in the same class, which runs first) sets `sys.modules["backend.services.meta_conversions_service"] = None` and `sys.modules["services.meta_conversions_service"] = None`, intending to force both of `_fire_driver_activated`'s dual-import attempts to raise `ImportError` so the function's swallow-and-return branch is exercised. Empirically (confirmed via `tracemalloc` allocation tracing), this does not reliably block the import — `_meta.send_driver_activated(driver, user, ride)` still gets called, creating a real coroutine object. That test does **not** mock `ride_complete.spawn`, so the coroutine is handed to the real production `spawn()` implementation outside of any running event loop, where it is silently dropped rather than awaited or explicitly closed. Python's garbage collector eventually finalizes the orphaned coroutine — at a moment determined by allocation-count thresholds across the whole test run, not by test boundaries — and pytest's unraisable-exception hook attributes the resulting warning to whichever *other* test happens to be executing at that moment (in every reproduction, that landed on `test_spawn_failure_is_logged_not_raised`, later in the same file).

## 3. Fix / remediation

Added `monkeypatch.setattr(ride_complete, "spawn", _spawn_close)` to `test_both_import_paths_failing_is_swallowed`, using the same close-on-call double already used elsewhere in this test file. This guarantees any coroutine that reaches `spawn()` — whether or not the sys.modules mocking above actually blocks the import as intended — is explicitly closed immediately rather than left for the garbage collector. Test-only change; the underlying "why doesn't the sys.modules trick reliably block both import paths" question is not resolved (would need deeper investigation into which sys.modules key backend's relative-import resolution actually touches at collection time vs. runtime), but is no longer load-bearing for this test passing.

## 4. Risk & impact on existing functionality

- Test-only, single test method, isolated to `backend/tests/test_ride_complete_coverage.py`.
- No application code changed. `ride_complete.py`'s `_fire_driver_activated`/`spawn` production behavior is untouched.
- Blast radius: this test method only. No other test or production code path reads or depends on this mock.

## 5. User-experience effect

None — test-only, no user-facing behavior change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_ride_complete_coverage.py` | Added `spawn` mock to `test_both_import_paths_failing_is_swallowed` | Prevent an orphaned, un-awaited coroutine from leaking to the garbage collector and surfacing as a `PytestUnraisableExceptionWarning` on an unrelated later test |

## 7. Before / after

```python
# Before
def test_both_import_paths_failing_is_swallowed(self, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(sys.modules, "backend.services.meta_conversions_service", None)
    monkeypatch.setitem(sys.modules, "services.meta_conversions_service", None)
    # Must not raise.
    ride_complete._fire_driver_activated(_driver(), {"id": _USER_ID}, _ride())
```

```python
# After
def test_both_import_paths_failing_is_swallowed(self, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(sys.modules, "backend.services.meta_conversions_service", None)
    monkeypatch.setitem(sys.modules, "services.meta_conversions_service", None)
    monkeypatch.setattr(ride_complete, "spawn", _spawn_close)
    # Must not raise.
    ride_complete._fire_driver_activated(_driver(), {"id": _USER_ID}, _ride())
```

## 8. Rollback plan

Revert the commit — pure test-only change, no data or config footprint.

## 9. Verification performed

- [x] Reproduced the original failure locally with `tracemalloc` allocation tracing (`PYTHONTRACEMALLOC=25 python -X tracemalloc=25 -m pytest tests/test_meta_capi_transport_coverage.py tests/test_meta_conversions.py tests/test_redis_diag_coverage.py tests/test_redis_diag.py tests/test_ride_complete_coverage.py tests/test_rides.py tests/test_ride_completion_location.py -q --no-cov`), confirming the allocation site was `test_both_import_paths_failing_is_swallowed` → `_fire_driver_activated` → `spawn(_meta.send_driver_activated(...))`.
- [x] Same 7-file combination re-run after the fix: 206 passed, 0 errors, 1 (pre-existing, unrelated) warning — previously 205 passed, 1 error.
- [ ] Full backend suite not re-run for this standalone fix (single-test-method change, low risk; the CI run on this PR itself will exercise it against the full suite).

**What was NOT verified:** the deeper question of why the `sys.modules = None` poisoning doesn't reliably block both dual-import attempts was not root-caused further — only worked around defensively. A future session investigating the dual-import test-harness pattern more broadly may want to revisit this.
