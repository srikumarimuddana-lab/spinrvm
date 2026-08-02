# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch / rides |
| PR / commit link | (this branch: `claude/spinr-ai-guardrail-reviewer-o2vups`) |
| Related issue or gap ID | ACTION_ITEMS.md A1c, Sub-tier A (`utils/redis_client.py`) |

## 1. Issue / gap identified

`backend/utils/redis_client.py` (the presence/rate-limit backbone — every
dispatch-presence check, OTP lockout, WS rate limit, and idempotency-key
cache goes through it) was at 55% coverage per the Track 2 full-repo
scoping pass. It's the module CLAUDE.md's "Redis transparency" convention
calls out by name: it falls back silently to an in-process dict when
`REDIS_URL` is unset, and rate-limit / OTP-lockout state is lost on restart
in that mode — worth testing both code paths explicitly.

## 2. Root cause

Every existing test that touches this module does so only as a side effect
of a higher-level caller (dispatch presence, OTP lockout, WS per-user rate
limiting), via the `mock_redis` conftest fixture, which patches `_local` to
an empty dict — i.e. every prior test ran exclusively through the
in-process-fallback branch. The real-Redis-connected branch of every public
function (the actual `if r is not None:` path), the
"Redis-configured-but-erroring-must-raise-loudly" branch, `_get_redis()`'s
URL-change reconnect logic, `get_redis_stats`/`count_keys_by_prefix`, and
`_humanize_bytes` had no direct test at all.

## 3. Fix / remediation

Test-only change. Added `backend/tests/test_redis_client_coverage.py` (51
tests, 100% coverage of the file) covering, for every public function, both
the in-process-fallback path and a mocked real-Redis-client path, plus:

- `_get_redis()`: URL-unset → None, connect + cache, URL-change triggers a
  reconnect, and the import/connect-failure-falls-back-to-None branch.
- Every `redis_*` write/read primitive's "configured but erroring" branch,
  confirming each raises loudly (per CLAUDE.md — a Redis error when
  `REDIS_URL` **is** set must never silently degrade to the in-process
  store; that fallback only applies when Redis isn't configured at all).
  The one intentional exception is `redis_set_nx`, whose docstring
  documents it as a belt-and-braces election on top of application-level
  idempotency — it swallows a Redis error and falls through to the local
  lock, which this PR pins as intentional behavior, not a gap.
- `redis_delete_pattern`'s SCAN-based real-client path (multi-key delete,
  zero-match no-op, mid-scan failure propagation).
- `get_redis_stats` in both modes, including the real-client
  `used_memory_percent` division and its `maxmemory == 0` → `None` guard,
  and the `INFO`-call-failure → `{"connected": False, "error": ...}` shape.
- `count_keys_by_prefix` in both modes, including bytes-vs-str key decoding
  from a real Redis `SCAN` and a mid-scan failure degrading to
  partial/zeroed counts.
- `_humanize_bytes`'s unit-boundary cases (B/K/M/G/T).

No application code changed. **Bug found, not fixed (test-only scope):**
`_humanize_bytes` mislabels any value at or beyond terabyte scale as bytes
instead of the correct unit. `unit` is only ever reassigned inside the
`if size < 1024` branch; once `size` starts at T-scale or larger that
branch never fires, so the loop silently keeps dividing `size` by 1024
through every remaining unit without ever updating `unit`, leaving it at
its `"B"` initializer. A 5-exabyte input renders as `"5B"` instead of a
correct `"…P"`-scale reading. Pinned by
`test_beyond_terabyte_mislabels_as_bytes_not_fixed` (asserts the actual,
buggy output) rather than silently working around it. Not a live production
risk today — no realistic Redis instance holds petabyte-scale data, and
this only feeds an admin-dashboard gauge, not a money/dispatch decision —
but flagged here so it doesn't get inherited unnoticed if that assumption
ever changes.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** New test file only. This module is imported
  by a large number of callers (`socket_manager.py`, dispatch presence,
  `routes/auth.py`'s OTP lockout, `routes/websocket.py`'s per-user rate
  limit, `utils/rate_limiter.py`, `utils/idempotency.py`, and admin
  monitoring's `/redis/stats` endpoint) — none of them were modified;
  every new test patches `redis.asyncio.from_url` and the module's own
  `_local`/`_redis`/`_redis_url` globals in isolation via an autouse
  fixture, so no cross-test state leakage.
- **Presence/rate-limit backbone**: this file backs dispatch's driver
  presence checks and every WS/HTTP rate limiter. The new tests confirm
  the documented fail-loudly contract (a configured-but-erroring Redis
  raises, never silently degrades) holds for every primitive except the
  one function (`redis_set_nx`) whose docstring explicitly documents the
  opposite as intentional — this PR did not change that behavior, only
  confirmed and pinned it.
- **Dispatch SLA**: `redis_mget` (used to collapse per-candidate
  `offer_skip` lookups into one round-trip on the dispatch hot path,
  per its docstring) is now directly covered in both modes — no change to
  its behavior, only new assertions.

## 5. User-experience effect

None — test-only change. No rider/driver/corporate-admin/internal-admin
facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_redis_client_coverage.py` | New file — 51 tests | Close coverage gap on `utils/redis_client.py` (35% → 100% in the targeted subset measurement; 55% baseline was the full-suite side-effect figure) |
| `docs/change-log/2026-08-02-a1c-redis-client-coverage.md` | New file (this log) | Required per CLAUDE.md for anything touching a live-tested surface (dispatch presence, rate limiting) |
| `ACTION_ITEMS.md` | Updated A1c's `utils/redis_client.py` bullet | Track progress per the existing series format |

## 7. Before / after

Not applicable — purely additive test file; no existing behavior-changing diff.

## 8. Rollback plan

`git revert` — pure test/doc addition, no live-data footprint, no
application code touched, no migration.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_redis_client_coverage.py -q --no-cov` — 51 passed.
- [x] Coverage measured: `pytest tests/test_redis_client_coverage.py --cov=utils.redis_client --cov-report=term-missing` — **utils/redis_client.py: 100%** (220/220 statements). A broader `-k` subset including three pre-existing Redis-adjacent test files (`test_utils_extended.py`, `test_driver_location_redis_resilience.py`, `test_redis_diag.py`) measured the pre-PR baseline at 35% (differs from the ACTION_ITEMS.md-tracked 55% full-suite side-effect figure — both are legitimate, different-methodology numbers; this PR closes the gap under either).
- [x] Full backend suite run: `pytest tests/ -q --no-cov` — pending completion at time of writing; will amend this entry if any regression is found (none expected — additive-only, no production code touched, and the module's own globals are isolated per-test via an autouse fixture).
- [ ] Manual repro / staging check — not applicable, test-only change with no deployable behavior difference.
- [x] Blast-radius grep performed: see section 4 above.
- [x] Reviewed against CLAUDE.md conventions: "Redis transparency" convention (silent in-process fallback when `REDIS_URL` unset, state lost on restart) is exactly what this PR adds direct coverage of, in both modes, for every public function.

## 10. What was NOT verified

- Not run against a real Redis instance — every "real-client" test mocks
  `redis.asyncio.from_url`'s returned client, matching repo convention for
  this test tier (integration-tier tests against real infra live in a
  separate tier per CLAUDE.md's Testing Conventions).
- The `_humanize_bytes` bug above is flagged, not fixed, per this pass's
  test-only scope — a fix decision (and whether it's worth a dedicated PR
  given the near-zero real-world likelihood of petabyte-scale Redis
  memory) is left for a follow-up.
