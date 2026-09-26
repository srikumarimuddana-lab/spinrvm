# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-26 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | ai (Maps budget circuit breaker; not a rides/payments/auth/corporate/safety surface) |
| PR / commit link | (this branch, fix/maps-budget-dual-import-symmetry) |
| Related issue or gap ID | none — discovered via `backend-test`'s `test_dual_import_symmetry` failing on every open PR after PR #5853 merged to `main` |

## 1. Issue / gap identified

`backend/utils/maps_budget.py`'s dual-import block imports `from . import metrics` in the `try:` branch but not in the `except ImportError:` branch. `backend/tests/test_dual_import_symmetry.py` — which exists specifically to catch this class of bug — correctly failed, blocking `backend-test` CI on every PR to `main` (including two of this session's own open PRs, #5858 and #5860, neither of which touches this file).

## 2. Root cause

PR #5853 ("Add SLA-path latency/budget metrics") added a `metrics.set_gauge(...)` call to `maps_budget.py` and the corresponding `from . import metrics` import to the try-branch, but missed adding the mirror `from utils import metrics` to the except-branch. Under the except-branch's import mode (`python -m backend.server` style top-level import), calling the function that uses `metrics.set_gauge` would raise `NameError` at call time — not at import time — so it would have reached production silently were it not for this test.

## 3. Fix / remediation

Added `from utils import metrics  # type: ignore` to the except-branch, mirroring the try-branch's `from . import metrics`, matching the pattern already used for every other name in the same block.

## 4. Risk & impact on existing functionality

- Blast radius: **isolated to this one file's import block**. No behavior change — both branches now bind the identical set of names; only the previously-missing except-branch path is affected, and only when running in that import mode.
- No other caller of `maps_budget.py` is affected; this is a pure addition of a missing import, not a change to any function's logic.
- Not a live-tested-surface change (rides/dispatch/payments/auth/corporate/safety) — this is an internal Google Maps cost-control utility's import wiring.

## 5. User-experience effect

None. Internal import-wiring fix only; no rider/driver/admin-facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/maps_budget.py` | Added `from utils import metrics` to the except-branch of the dual-import block | Mirror the try-branch import, closing a latent `NameError` risk under the except-branch's import mode |

## 7. Before / after

```python
# Before
except ImportError:  # pragma: no cover - dual import path
    from utils.redis_client import (  # type: ignore
        ...
    )
```

```python
# After
except ImportError:  # pragma: no cover - dual import path
    from utils import metrics  # type: ignore
    from utils.redis_client import (  # type: ignore
        ...
    )
```

## 8. Rollback plan

`git revert` — pure import-line addition, no schema/migration/runtime-state touched.

## 9. Verification performed

- `pytest tests/test_dual_import_symmetry.py tests/test_maps_budget.py` — 16 passed (was 1 failed, 15 passed before this fix, per the failing `backend-test` run on PR #5860).
- `ruff check` and `ruff format --check` clean on the modified file.

## What was NOT verified

- Did not run the full backend suite locally (ran only the two directly-relevant test files) — this is a single-line import addition with no logic change, so the targeted run is sufficient evidence; the full suite will re-run in this PR's own CI.
- Not tested against a live/staging backend — no live-state dependency exists for this fix.
