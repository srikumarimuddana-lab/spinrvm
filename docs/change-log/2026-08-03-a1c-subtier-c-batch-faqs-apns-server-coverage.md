# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-03 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | backend (app factory/router mounting, public FAQ read, push notifications) |
| PR / commit link | (branch: `claude/a1c-subtier-c-batch-faqs-apns-server`) |
| Related issue or gap ID | `ACTION_ITEMS.md` A1c Sub-tier C, Batch 12's `routes/faqs.py` entry + Batch 13 (`utils/apns_client.py`, `server.py`) of the itemization |

## 1. Issue / gap identified

Three files sat in the Sub-tier C 60–80% coverage band:

- `backend/routes/faqs.py` — 78.12% (32 stmts, public unauthenticated FAQ read
  endpoint; distinct from `routes/admin/faqs.py`'s already-closed CRUD).
- `backend/utils/apns_client.py` — 78.72% (141 stmts, Apple Live Activity
  push client).
- `backend/server.py` — 79.20% (250 stmts, the FastAPI app factory that
  mounts ~25 routers).

## 2. Root cause

`routes/faqs.py`'s existing coverage (`test_utils_extended.py::TestFaqsEndpoint`)
only ever called `get_public_faqs` with `service_area_id`/`lat`/`lng` all
`None`, which short-circuits `_resolve_area_scope` before it ever reaches the
lat/lng-to-service-area resolution branch or the resolve-failure exception
branch — both untouched.

`utils/apns_client.py`'s existing `test_apns_client.py` stubs `_get_client()`
entirely and always passes `use_sandbox=True/False` explicitly, so the real
`_get_client()`/`aclose()` lifecycle, `_load_apns_config`'s exception and
malformed-PEM branches, `_load_templates`'s real file I/O (success/missing/
malformed-JSON), the early httpx/jwt-unavailable and empty-token guards, the
`use_sandbox=None` settings-driven branch, the retry-still-fails branch, the
outer `except Exception` handler, and `_reason`'s own `json()`-raises branch
were all never exercised.

`server.py` had **no dedicated test file** covering `_db_ready()` or the
`/health` endpoint at all (confirmed via grep — zero hits for `_db_ready` or
`_health_cache` anywhere in `backend/tests/`). `test_metrics_auth.py` covers
`/metrics`'s auth gate (401/503/pass) but every scenario mocks
`get_redis_stats` to return `{"connected": False}`, so the entire
Redis-connected gauge-setting block and `get_redis_stats` raising were never
hit. `test_deprecated_route_admin_exempt.py` covers the `/api/`-prefixed
deprecated-path branch and the admin-exempt branch, but not the
`_DEPRECATED_ROOT_PREFIXES` branch (`/settings`, `/company-info`) — the only
branch that takes the non-`/api/`-prefixed canonical-path derivation
(`"/api/v1" + path`, line 102).

## 3. Fix / remediation

Test-only change across three new files:

- `backend/tests/test_faqs_coverage.py` (9 tests) — `_resolve_area_scope`'s
  explicit-`service_area_id`-wins path, the lat/lng-resolves-to-an-area path,
  the lat/lng-resolves-to-no-area path, the partial-coordinates
  (lat-without-lng) no-op path, and the outer exception-swallow path; plus
  `get_public_faqs`'s end-to-end area-scope filtering behavior (global FAQs
  always included, area-tagged FAQs included only on scope overlap, excluded
  with no location context, and the lat/lng query-param path end-to-end).
- `backend/tests/test_apns_client_coverage.py` (15 tests) —
  `_load_apns_config`'s settings-load-raises and malformed-PEM branches (plus
  the two happy/missing-keys branches for symmetry with the existing warn-once
  flags); `_load_templates`'s real file load, missing-file, and malformed-JSON
  branches (against the real bundled `voltra_templates.json` and `tmp_path`
  fixtures, not a monkeypatched-away function); the real `_get_client()`/
  `aclose()` lifecycle (create-once-reuse, close-and-clear, safe no-op when
  never opened); `send_apns_live_activity`'s httpx-unavailable,
  jwt-unavailable, and empty-token early guards; the `use_sandbox=None`
  settings-driven branch (both non-production and production); the
  retry-after-expired-token-still-fails branch; `_post()` raising being
  caught by the outer handler; and `_reason`'s own exception branch.
- `backend/tests/test_server_coverage.py` (18 tests) — `_db_ready`'s
  cache-hit, fresh-success (with the safe-field filter), non-dict-result,
  ping-raises, and ping-timeout branches; `/health`'s healthy/unhealthy
  response shapes; `_metrics_token()`'s real env-var read (unset and
  whitespace-stripped); `/metrics`'s Redis-connected gauge block (full field
  set, missing-optional-field defaults, and `get_redis_stats` raising);
  `/metrics`'s query-param token path (both correct and wrong token); and
  `DeprecatedRootPathMiddleware`'s root-prefix (`/settings`, `/company-info`)
  branch plus two not-flagged regression checks.

No application code in any of the three target files was modified. **No bugs
found** in any of the three files — every branch exercised behaves per its
own docstring's stated contract.

**Not attempted, documented rather than silently skipped:** `server.py`'s
Sentry-init module-level block (`if sentry_dsn: ...`, the production
`capture_message` boot event, and the production-without-`SENTRY_DSN` error
log) and the `if __name__ == "__main__":` uvicorn entrypoint only execute at
module *import* time, before any test can patch `settings.sentry_dsn` or
`settings.ENV`. Reproducing them would require reloading `backend.server`
mid-suite — re-running all ~25 `include_router` calls, the Firebase init, the
loguru sink setup, and (if `SENTRY_DSN` were set) a real `sentry_sdk.init()`
against the single shared `app`/`logger` singletons every other test file in
the suite imports. That risks re-registering routes/middleware or duplicating
loguru sinks and corrupting every other test that imports `backend.server` in
the same process — explicitly out of scope per this task's "test additively,
don't refactor" instruction for this file. Same class of
"structurally-near-impossible-to-reach-through-this-harness" gap already
accepted elsewhere in this backlog for dual-import `ImportError` fallback
lines (see e.g. the zoho/distrecon/obs and Batch-11 entries in
`ACTION_ITEMS.md`).

## 4. Risk & impact on existing functionality

**Blast radius: test-only, zero application code touched.** Before writing
tests: grepped `ACTION_ITEMS.md` for all three filenames (found the exact
Batch 12/13 itemization, still open) and ran `git branch -r | grep a1c-subtier-c`
plus `git log --all --oneline -- backend/routes/faqs.py backend/utils/apns_client.py
backend/server.py` — no concurrent branch has coverage work in flight against
any of these three files (the closest name collisions,
`a1c-subtier-c-batch-guest-driverimport-quest` /
`a1c-subtier-c-batch-p1df-zoho-export` / `a1c-subtier-c-batch-providers-onboarding-respcache`,
target disjoint files per their own branch names).

- `routes/faqs.py`'s `get_public_faqs` is called only by the rider/driver
  apps' FAQ screens (no other backend caller); `_resolve_area_scope` calls
  `routes/fares.py`'s `resolve_area_scope`/`resolve_service_area_for_point`,
  which are also called directly by `routes/fares.py`'s own fare-estimate
  endpoints — new tests patch these two functions at the
  `backend.routes.fares.*` call site (the exact module they're imported
  from, per this repo's patch-target convention) and never touch
  `routes/fares.py` itself, so `fares.py`'s own callers are unaffected.
- `utils/apns_client.py`'s `send_apns_live_activity`/`aclose` are called from
  the Live Activity push dispatch path (rides pushing Voltra UI updates) and
  `core/lifespan.py`'s shutdown hook (`aclose`). New tests reset the module's
  mutable globals (`_config_warned`, `_pem_warned`, `_token_cache`,
  `_templates`, `_client`) in an `autouse` fixture with setup **and**
  teardown, so no test-order-dependent state leaks into
  `test_apns_client.py`'s existing tests or any other file that imports this
  module later in the same session.
- `server.py` is imported by effectively every backend test file (directly
  or via `conftest.py`'s preload) since it builds the single `app` instance
  the whole suite's `TestClient` fixtures use. New tests only *call* existing
  functions (`_db_ready`, `health`, `_metrics_token`, `metrics`,
  `DeprecatedRootPathMiddleware.dispatch`) with mocked dependencies — they do
  not mutate `app`, do not add/remove routes or middleware, and the
  `_health_cache` module-global is reset via an `autouse` fixture with
  setup+teardown so no cache state leaks between tests or into any other
  file's use of `/health`.
- **"Do not silently swallow errors" convention** — every new
  exception-swallow test (`_load_apns_config`'s settings-load failure,
  `_resolve_area_scope`'s outer catch, `send_apns_live_activity`'s outer
  catch, `_db_ready`'s ping-raises/timeout, `/metrics`'s `get_redis_stats`
  raising) asserts the *existing* code's already-documented best-effort
  degrade-and-log behavior; no new swallow point was introduced.

## 5. User-experience effect

None — test-only change, no rider/driver/corporate-admin/internal-admin
facing behavior change of any kind.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_faqs_coverage.py` | New file — 9 tests | Close the coverage gap on `routes/faqs.py` (78.12% → 94%) |
| `backend/tests/test_apns_client_coverage.py` | New file — 15 tests | Close the coverage gap on `utils/apns_client.py` (78.72% → 100%) |
| `backend/tests/test_server_coverage.py` | New file — 18 tests | Close the coverage gap on `server.py` (79.20% → 88%; remaining gap is the import-time-only Sentry-init block, documented above) |
| `ACTION_ITEMS.md` | A1c Sub-tier C — marked this batch closed with before/after numbers | Track progress per the existing series format |
| `docs/change-log/2026-08-03-a1c-subtier-c-batch-faqs-apns-server-coverage.md` | New file (this log) | Required per CLAUDE.md for anything touching a live-tested surface (`server.py` is the app boot path) |

## 7. Before / after

Not applicable — purely additive test files; no existing application-code
behavior-changing diff to show.

## 8. Rollback plan

`git revert` — pure test/doc addition, no live-data footprint, no
application code touched, no migration, no feature flag needed.

## 9. Verification performed

- [x] New test files run alone (per this batch's explicit instruction to
  defer full-suite verification to a later consolidated pass):
  `pytest tests/test_faqs_coverage.py tests/test_apns_client_coverage.py
  tests/test_server_coverage.py -o addopts="" -q` → **47 passed**.
- [x] Run together with every pre-existing test file touching each module,
  with real coverage measurement:
  `pytest tests/test_utils_extended.py tests/test_faqs_coverage.py
  tests/test_apns_client.py tests/test_apns_client_coverage.py
  tests/test_live_activity.py tests/test_p3_push_notifications.py
  tests/test_server_coverage.py tests/test_deprecated_route_admin_exempt.py
  tests/test_metrics_auth.py -o addopts="" --cov=routes.faqs
  --cov=utils.apns_client --cov=backend.server --cov-report=term-missing`
  → **290 passed, 1 skipped** (the skip is pre-existing, not introduced by
  this batch), combined coverage:
  - `routes/faqs.py`: **78.12% → 94%** (32 stmts, 2 missing — the dual-import
    `except ImportError` fallback lines 28-29, same
    structurally-near-impossible-to-reach class documented elsewhere in this
    backlog).
  - `utils/apns_client.py`: **78.72% → 100%** (141 stmts, 0 missing).
  - `server.py`: **79.20% → 88%** (253 stmts — 3 more than the 250 quoted in
    the itemization, minor drift since that number was taken; 31 missing —
    the Sentry-init block + `__main__` entrypoint documented in §3 above).
- [x] Blast-radius grep performed — see §4; every real caller of the three
  target modules enumerated; `git branch -r`/`git log --all` checked for
  concurrent in-flight work before starting.
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — dual-import
  pattern (respected, not simplified away), "do not silently swallow errors"
  (asserted existing intentional best-effort behavior only), patch-target
  convention (patched `backend.routes.fares.*` and module-level attributes
  on the module under test, not `backend.db_supabase`), `@pytest.mark.anyio`
  used for all async test classes.
- [ ] Full backend suite (`pytest tests/ -q`) — **explicitly deferred per
  this batch's task instructions**, which asked for standalone verification
  of the new files only, to conserve tokens across several concurrent
  coverage-backlog batches; a consolidated full-suite run across all
  in-flight batches is planned separately.
- [ ] Manual repro against real Supabase/APNs/Sentry — not applicable; every
  DB/APNs/Sentry call is mocked throughout, matching this test tier's
  existing convention for all three modules' pre-existing suites.
- [ ] Feature-flagged — not applicable; test-only, no deployable behavior
  difference.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated, not assumed — test-only, every touched/added
  file enumerated in §6, every real caller of each target module enumerated
  in §4
- [x] No silent behavior change to an already-shipped flow — zero
  application code modified in this pass

## What was NOT verified

- **The full backend test suite was not run for this batch** — per this
  batch's task instructions, only the three new test files (standalone, and
  combined with each module's pre-existing test files) were run. A
  consolidated full-suite pass across all in-flight A1c coverage batches is
  deferred to a later session, per instruction.
- Not exercised against real Supabase, the real Apple Push Notification
  service, or a real Sentry DSN — every test mocks the relevant client/DB
  call, consistent with this repo's existing convention for this whole test
  tier (unit, not integration).
- `server.py`'s Sentry-init block (`if sentry_dsn: ...`, ~69 statement lines)
  and `if __name__ == "__main__":` entrypoint remain uncovered — reasoned
  about (read line-by-line, cross-checked against `utils/sentry_scrub.py`'s
  own existing unit tests for the `pipeda_sentry_options()`/
  `tags_from_log_extra()` helpers it calls, which already have direct
  coverage) rather than exercised directly, per the risk explained in §3.
  This is a real, standing gap on this specific file, not a padding
  omission — flagged for anyone attempting to push `server.py` further past
  88% to budget for a subprocess-isolated import test rather than an
  in-process `importlib.reload`.
- No visual regression tooling is applicable here — this batch touches
  backend Python only, no frontend surface.
