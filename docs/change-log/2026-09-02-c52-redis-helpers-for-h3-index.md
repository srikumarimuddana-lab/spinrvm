# 2026-09-02 — Implement 6 missing Redis helpers blocking `utils/h3_location_index.py` (C52)

## Issue/gap identified

`backend/utils/h3_location_index.py` imported 6 functions from
`utils/redis_client.py` — `redis_eval`, `redis_hgetall`, `redis_hset`,
`redis_zadd`, `redis_zrangebyscore_many`, `redis_zrem` — that were never
implemented there. The module could not import at all, in any environment,
since it was added in commit `fc6f922`.

## Root cause

`fc6f922` shipped the H3 dispatch-geo-index feature dark (behind
`dispatch_geo_provider=legacy`, the default) alongside a transactional
outbox worker. Per `docs/audit/2026-09-02-pgbouncer-direct-pool-migration-plan.md`
(§7), it "landed dark on `main`... behind default-off flags" and was never
independently verified after landing — its own change-log
(`docs/change-log/2026-09-01-h3-dispatch-heatmap.md`) never mentions running
`pytest tests/test_h3_location_index.py`. The import chain was first broken
by the separate, also-pre-existing C51 gap (`h3` package unpinned), which
masked this second error until C51 was fixed earlier today.

## Fix/remediation

Implemented all 6 functions in `utils/redis_client.py`, matching every
existing function's established pattern exactly:
- Real-Redis branch (delegates to `redis.asyncio`'s `hgetall`/`hset`/
  `zadd`/`zrem`/`eval`, plus a `pipeline(transaction=False)` batch for
  `redis_zrangebyscore_many`), raising loudly on a configured-but-erroring
  Redis (never silently degrading — matches the module's documented
  "Redis transparency" convention).
- In-process fallback branch for when `REDIS_URL` is unset — two new local
  stores, `_local_hashes` and `_local_zsets`, mirroring the existing
  `_local` dict's shape (`{value/fields/members, expires_at}`) and
  `time.monotonic()`-based best-effort TTL.
- `redis_eval` is the one exception: with no local Lua interpreter to fall
  back to, it raises `RuntimeError` when unconfigured — which is exactly
  the signal `h3_location_index.py`'s `upsert_driver()` already has an
  `except RuntimeError:` clause for, running an equivalent pure-Python
  path (`_upsert_python`) instead. No caller-side change was needed.

## Risk & impact on existing functionality

- **Blast radius:** isolated. `redis_client.py` is imported by many modules,
  but this change is purely additive — 6 new functions plus 2 new
  fallback-store dicts. No existing function's signature or behavior
  changed. Grepped for other callers of the 6 new function names: only
  `h3_location_index.py` (the intended caller) references any of them.
- The feature this unblocks stays dark: `dispatch_geo_provider` defaults to
  `legacy`, and `services/dispatch_candidates.py` (the only importer of
  `h3_location_index.py`) has zero callers anywhere in `routes/`/`services/`/
  `core/`. This fix makes already-shipped dark code importable and
  functional again — it does not activate it.
- Confirmed via full test run that other, previously-uncollectable test
  files in this same feature family now surface further pre-existing gaps
  once they can finally collect — tracked separately as ACTION_ITEMS.md C53,
  not touched here (see that item for why: two are wiring decisions that
  need per-piece judgment on stale-test-vs-build-it, and one, `outbox_worker.py`'s
  missing `payment_service.send_ride_receipt_result`, is a real unbuilt
  feature on the payments/receipts domain that needs its own design pass,
  not something to invent inside this fix).

## User experience effect

None. `dispatch_geo_provider=legacy` unchanged; no rider/driver/admin-facing
behavior changes. Purely a backend dependency/module-import fix.

## Files modified

| File | What changed | Why |
|---|---|---|
| `backend/utils/redis_client.py` | Added `redis_hgetall`, `redis_hset`, `redis_zadd`, `redis_zrem`, `redis_zrangebyscore_many`, `redis_eval`, and two new in-process fallback stores (`_local_hashes`, `_local_zsets`) | Implements the 6 missing functions `h3_location_index.py` requires |
| `backend/tests/test_redis_client_coverage.py` | Added 30 tests (local-fallback, real-client-delegates, real-client-raises-on-error for each of the 6 functions) | Test coverage for the fix, matching this file's existing per-function pattern |
| `ACTION_ITEMS.md` | Closed C52; added C53 for the newly-surfaced adjacent gaps | Backlog tracking |

## Before/after snippet

```python
# before — utils/h3_location_index.py could not import at all:
from .redis_client import (
    get_redis_stats, redis_delete, redis_eval, redis_get, redis_hgetall,
    redis_hset, redis_set, redis_set_nx, redis_zadd,
    redis_zrangebyscore_many, redis_zrem,
)
# ImportError: cannot import name 'redis_eval' from 'utils.redis_client'

# after — new functions added to redis_client.py, e.g.:
async def redis_eval(script: str, numkeys: int, *keys_and_args) -> Any:
    r = await _get_redis()
    if r is None:
        raise RuntimeError("redis_eval requires REDIS_URL to be set — no local Lua interpreter")
    try:
        return await r.eval(script, numkeys, *keys_and_args)
    except RuntimeError:
        raise
    except Exception as e:
        logger.error(f"[REDIS] redis_eval failed — Redis configured but unavailable: {e}")
        raise
```

## Rollback plan

`git revert` is safe — pure additive code (no migration, no data mutation,
no flag change). Reverting restores the pre-existing state (the module
already couldn't import before this fix), so a revert cannot regress
anything currently working.

## Verification performed

- `ruff check`/`ruff format --diff` on both changed files: clean.
- `pytest tests/test_redis_client_coverage.py -q` — 78/78 pass (48
  pre-existing + 30 new).
- `pytest tests/test_h3_location_index.py tests/test_dispatch_candidates.py
  tests/test_h3_cells.py tests/test_h3_heatmap.py -q` — 58/60 pass. The 2
  that don't fail are a **different**, unrelated pre-existing gap (C53
  findings 1 and 2 — a second SET-based helper trio nothing calls, and a
  presence→h3-index wiring test), not this fix's target and not regressed
  by it.
- `pytest tests/test_driver_location_redis_resilience.py tests/test_redis_diag.py
  tests/test_redis_diag_coverage.py tests/test_p3_background_location.py
  tests/test_rides_matching_coverage.py tests/test_offer_timeout.py -q` —
  86/87 pass, 1 pre-existing xfail — confirms no regression in the other
  modules that share `redis_client.py`.
- Scoped run (`-k "redis or h3 or dispatch or presence"`, unit marker) —
  203/208 pass; the 5 failures are all C53 findings (2 in
  `test_h3_location_index.py`, 1 in `test_h3_index_reconciler.py`, 2 in
  `test_outbox_worker.py`), confirmed pre-existing and unrelated by tracing
  each to a cause outside this diff (see C53).
- Checked C50's migration plan doc and the H3 change-log first, per this
  item's own note, to rule out "deliberately paused" before building —
  confirmed it was an unverified dark launch, not a deliberate pause.
- No production build applicable — backend-only change, no
  `admin-dashboard`/`rider-app`/`driver-app` files touched.

## What was NOT verified

- Not run against a real Redis instance — only against `redis.asyncio`
  mocks (matching this test file's existing convention for every other
  function) and the in-process fallback. The real-Redis branches
  (`r.hset(key, mapping=...)`, `r.zadd(key, mapping)`,
  `r.pipeline(transaction=False)`, `r.eval(script, numkeys, *args)`) are
  standard `redis-py` API calls but their exact behavior against a live
  Redis/Redis-compatible service (e.g. Upstash, referenced in
  `h3_location_index.py`'s own Lua-script comment "no cjson (Upstash)") was
  not integration-tested in this session.
- Did not attempt the full 13,744-test backend suite to completion — a
  background run was started but did not finish producing output in this
  session's window; relied instead on the scoped runs listed above, which
  this repo's own testing conventions treat as the standard fast local loop.
- C53's 4 findings are documented, not fixed, in this change — including
  the note that finding 4 (`outbox_worker.py`) is materially larger than
  the other 3 and needs a real design decision before anyone builds it.
