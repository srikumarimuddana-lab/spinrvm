# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-20 |
| Author | Claude Code (agent), for ittalenthire.ca@gmail.com |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `fix/c114-driver-ride-reads-rate-limit` (not yet pushed/opened as a PR by this session) |
| Related issue or gap ID | ACTION_ITEMS.md C114 |

## 1. Issue / gap identified

None of `backend/routes/drivers/ride_reads.py`'s three read endpoints (`GET /rides/active`, `GET /rides/history`, `GET /rides/{ride_id}/offer`) carried any rate-limit decorator, and no `SlowAPIMiddleware` is mounted anywhere that would auto-cover an undecorated route — so this file family had zero rate limiting, unlike its rider-facing sibling `routes/rides/queries.py` (decorated with `@ride_read_limit` throughout).

## 2. Root cause

Found by `spinr-security-auditor`'s adversarial review of PR #5382 while auditing the new `get_ride_offer` endpoint added there — a pure GET that returns PII (precise GPS, rider name + rating) with no rate limiting, cheaper to hammer than a mutating accept/decline call. Investigating it surfaced that its two siblings in the same file had never had rate limiting either — not a regression, a pre-existing gap in this file's own history (it never had the decorator `routes/rides/queries.py` already established for the equivalent rider-facing reads).

## 3. Fix / remediation

Added the `ride_read_limit` decorator (`backend/utils/rate_limiter.py`'s pre-existing `120/minute` limiter, the same one every rider-facing read endpoint in `routes/rides/queries.py` already uses) to all three endpoints in `backend/routes/drivers/ride_reads.py`, not just the newest one — applying it consistently across the file rather than leaving an inconsistency. `ride_read_limit` was already defined in `utils/rate_limiter.py`; it just wasn't re-exported through `routes/drivers/_deps.py`'s dual-import block, so that block gained one new name. `AsyncLimiter.limit()` requires a `request: Request` parameter to exist on the decorated function's signature (it inspects the function at decoration time and raises `TypeError` if neither `request` nor `websocket` is present) — each endpoint gained `request: Request = None`, matching the exact pattern already used by every `routes/rides/queries.py` endpoint (`request: Request = None, current_user: dict = Depends(...)`).

## 4. Risk & impact on existing functionality

**Blast radius: isolated to `backend/routes/drivers/ride_reads.py` + `backend/routes/drivers/_deps.py`.** Grepped for every other consumer:
- Only importer of `ride_reads` is `backend/routes/drivers/__init__.py`'s router mount (`from .ride_reads import (...)`) — a bare re-export, unaffected by a decorator or an added default-`None` parameter.
- `routes/rides/matching.py` has one comment referencing `ride_reads.get_ride_offer` by name — not a call site.
- `backend/ai/tools_rides.py` defines its own, entirely separate `get_active_ride`/`get_ride_history` functions (different signature: `user: Dict[str, Any]`, no `request` param) for the AI assistant's tool-calling surface — confirmed by reading both files; these are not the same functions and are not affected by this change in any way.
- No other test file calls these three functions with positional arguments that would collide with the newly-inserted `request` parameter (checked `test_drivers_extended.py`, `test_driver_ride_flow_coverage.py`, `test_coverage_rides.py`, `test_p1_ws_reconnect.py` — all call with keyword arguments only).
- `backend/tests/conftest.py`'s `reset_rate_limiters` fixture (`autouse=True`) sets `default_limiter.enabled = False` before every test, which makes the decorator's wrapper skip the `Request`-type check entirely and call straight through — this is why every pre-existing test that calls these functions without a real `Request` object continues to pass unchanged.
- `backend/tests/test_rate_limit_decorator_order.py`'s existing repo-wide AST scan (which fails any endpoint whose limiter decorator sits above its `@router.get`/etc., and asserts a minimum count of rate-limited routes found) automatically covers these three new decorated endpoints — re-run and passing, count increased as expected.

No ride state machine, money, dispatch, or insurance-period code touched. No response shape, status code, or business logic changed for any of the three endpoints — only a new (defaulted, unused-by-the-handler-body) `request` parameter and a decorator.

## 5. User-experience effect

None under normal use — `120/minute` is generous for the three read patterns these endpoints serve (checking for an active ride, viewing history, polling an offer). A driver client that legitimately polls faster than 120 requests/minute against a single one of these endpoints would start seeing 429s; no such client behavior exists in this repo today (checked driver-app's own call sites — none poll at anywhere close to that rate). Not visible mid-session to a normal user; only a hypothetical abusive/broken client would ever observe a difference.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/ride_reads.py` | Added `@ride_read_limit` above `@router.get(...)` and a `request: Request = None` parameter to `get_active_ride`, `get_ride_offer`, `get_ride_history`; added `Request`/`ride_read_limit` to the `_deps` import. | Close the zero-rate-limiting gap C114 identified. |
| `backend/routes/drivers/_deps.py` | Added `ride_read_limit` to both branches of the dual-import block for `utils.rate_limiter`. | Make the existing rate limiter importable from this package the same way `dsar_export_limit`/`heatmap_read_limit`/etc. already are. |
| `backend/tests/test_driver_ride_reads_rate_limit.py` | New file: pins that all three endpoints are decorated, and a second test exercising a real `AsyncLimiter` instance built the same way to prove the underlying mechanism actually raises `RateLimitExceeded` once its limit is hit. | Regression coverage — this exact gap ("route exists, no decorator, no test would have caught it") is what let it go unnoticed. |

## 7. Before / after

```python
# Before
@router.get("/rides/active")
async def get_active_ride(current_user: dict = Depends(get_current_user)):
    ...

@router.get("/rides/{ride_id}/offer")
async def get_ride_offer(ride_id: str, current_user: dict = Depends(get_current_user)):
    ...

@router.get("/rides/history")
async def get_ride_history(
    limit: int = Query(20, ge=1, le=200),
    ...
    current_user: dict = Depends(get_current_user),
):
    ...
```

```python
# After
@router.get("/rides/active")
@ride_read_limit
async def get_active_ride(request: Request = None, current_user: dict = Depends(get_current_user)):
    ...

@router.get("/rides/{ride_id}/offer")
@ride_read_limit
async def get_ride_offer(ride_id: str, request: Request = None, current_user: dict = Depends(get_current_user)):
    ...

@router.get("/rides/history")
@ride_read_limit
async def get_ride_history(
    request: Request = None,
    limit: int = Query(20, ge=1, le=200),
    ...
    current_user: dict = Depends(get_current_user),
):
    ...
```

## 8. Rollback plan

`git-revert-safe` — this is an in-process decorator addition with no data-layer dependency, no migration, no config/secret change. A plain revert restores the prior (unrated) endpoints immediately, no second deploy step or data cleanup needed.

## 9. Verification performed

- [x] Automated tests: targeted — `test_drivers_extended.py`, `test_driver_ride_flow_coverage.py`, `test_coverage_rides.py`, `test_p1_ws_reconnect.py`, `test_rate_limit_decorator_order.py`, `test_async_limiter.py`, and the new `test_driver_ride_reads_rate_limit.py` — 436/436 pass. Full non-RLS backend suite also run (excluding `tests/rls/`, which needs a real Postgres per its own documented self-skip contract and isn't affected by this change) — see this log's own PR for the final pass/fail count once that run completes.
- [x] `ruff check`/`ruff format --check` clean on both modified files and the new test file.
- [x] Blast-radius grep performed: every importer of `ride_reads`, every call site of the three functions (app code and tests), and confirmed `backend/ai/tools_rides.py`'s same-named functions are a separate, unrelated definition (§4).
- [x] Reviewed against relevant CLAUDE.md convention: this is a security-hardening addition (rate limiting on a PII-bearing read path), following the exact existing pattern (`routes/rides/queries.py`'s `@ride_read_limit` + `request: Request = None`) rather than inventing a new one; the dual-import pattern in `_deps.py` was preserved, not simplified away.
- [ ] Manual repro / staging check — not performed; no staging environment reachable from this sandbox. Verified via unit tests with the real `AsyncLimiter` mechanism (not a mock of the rate limiter itself) plus the repo's own `reset_rate_limiters` fixture contract.
- [x] Feature-flagged if user-visible and non-trivial: not flagged — this restores parity with the rider-facing equivalent endpoints' existing protection; not a new feature or UX change, and CLAUDE.md's flag gate is for new/changed UX.

**What was NOT verified:** no real production/staging traffic was run against these endpoints to observe a live 429 — verification is via the same real `AsyncLimiter` class used in production, exercised directly in a unit test (not the shared module-level `ride_read_limit` object itself, to avoid mutating its real budget or interacting with other tests' shared state), plus the existing repo-wide AST-based decorator-order test.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-layer dependency).
- [x] Blast radius is stated, not assumed (§4 — isolated to two files, every consumer enumerated).
- [x] No silent behavior change to an already-shipped flow — the only observable change is a new 429 ceiling far above any real client's current usage; §5 states this explicitly.
