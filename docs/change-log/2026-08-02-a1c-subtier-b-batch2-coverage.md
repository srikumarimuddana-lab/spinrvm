# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | admin (support ticketing / Zoho Desk integration) |
| PR / commit link | (this branch: `claude/a1c-subtier-b-batch2`) |
| Related issue or gap ID | ACTION_ITEMS.md A1c Sub-tier B |

## 1. Issue / gap identified

Three files were assigned to this batch per ACTION_ITEMS.md A1c Sub-tier B:

- `backend/services/zoho_desk_db.py` (documented baseline 11.76%) — read
  layer for the local Zoho Desk ticket mirror (`zoho_desk_tickets`, migration
  123). No test file exercised its real logic; `test_admin_support_tickets_routes.py`
  stubs every one of its functions out (`monkeypatch.setattr(m.zoho_desk_db,
  "mirror_ready", AsyncMock(...))` etc.) to test the *routes*, giving this
  module itself zero real coverage.
- `backend/utils/zoho_desk_sync.py` (documented baseline 22.33%) — the
  Zoho Desk → Postgres mirror sync loop, one of the ~17 startup background
  loops mounted in `core/lifespan.py`. Same situation:
  `test_admin_support_tickets_routes.py` monkeypatches `zds.run_sync`
  wholesale to test the admin "sync now" endpoint; the loop's own pagination,
  cursor, closed-ticket-detection, and Redis-leader-lock logic were
  untested.
- `backend/utils/demand_forecast.py` (documented baseline 18.52%) — **fresh
  baseline measured at 91.36%**, not 18.52%. A different, unrelated
  concurrent session added `backend/tests/test_demand_forecast.py` earlier
  today (commit `13687c02972a18a8afdeeaeb845d59aa8e3bcc13`, 5 tests covering
  the `data_basis` relabeling fix) which already closes the vast majority of
  this module. Per this task's explicit instruction ("if already at or near
  100%... skip re-testing it, don't duplicate that work"), **this file was
  left untouched** — see section 9 for the exact remaining gap and why it
  wasn't chased.

## 2. Root cause

`zoho_desk_db.py` and `zoho_desk_sync.py` are both consumed almost
exclusively through the admin Help Desk routes, and the existing route-level
tests (correctly, for testing routes) mock these two modules out entirely
rather than exercising their real Supabase-fluent-chain / pagination /
cursor logic. No prior session had written a dedicated unit test file for
either module directly.

## 3. Fix / remediation

Test-only change. No application code modified in any of the three files.

- **`backend/tests/test_zoho_desk_db.py`** (26 tests, new file). Covers
  every public function — `mirror_ready`, `open_closed_counts`,
  `mirror_count`, `list_mirror`, `count_by_status`, `fetch_window` — for
  both the "supabase unconfigured → None" and "exception during query →
  None (log + swallow, caller falls back to live Zoho API)" contract each
  function shares, plus:
  - `list_mirror`'s sort-column mapping (`modifiedTime` → `modified_time`
    ascending vs. default `-createdTime` → `created_time` descending),
    all five `_apply_filters` predicates, the search `.or_()` clause
    construction, blank-search short-circuit, and the "row has no `raw`
    payload → dropped" filter.
  - `fetch_window`'s pagination loop: single short page, full-page-then-
    short-page (crosses the `_PAGE` boundary, exercises `offset += _PAGE`),
    and the `max_rows` safety cap stopping mid-page.
- **`backend/tests/test_zoho_desk_sync.py`** (24 tests, new file). Covers:
  - `_parse` (None/empty, `Z`-suffix, naive-datetime UTC attachment,
    unparseable string), `_name`, `_map_ticket` (full shape + top-level
    `assigneeId`/`email` fallbacks when `assignee`/`contact` are absent),
    `_upsert_batch` (empty-rows no-op, unconfigured-supabase no-op, real
    upsert call).
  - `run_sync`: disabled/missing-config skip, a full seed backfill that
    pages to a short final page (`mirror_backfilled` flips `True`), an
    incremental run that correctly stops at the stored `sync_cursor`
    (excludes the at-cursor ticket), an empty-first-page immediate
    reached-end, both closed-ticket detection paths (`statusType == "closed"`
    and `"closed" in status.lower()`), that seeding **never** triggers the
    reverse-sync (`close_linked_records`) even with closed tickets present
    in the seed batch, and the `SEED_MAX_PAGES`/`INCREMENTAL_MAX_PAGES`
    safety cap stopping the loop even when no short page or cursor-stop
    ever fires.
  - `zoho_desk_sync_loop` — per the spinr-background-loop skill's
    replay-safety contract, **both** the Redis-leader-lock "acquired" (this
    replica calls `run_sync`) and "not acquired" (another replica already
    owns the lock this tick, `run_sync` must not be called) paths, plus
    `auto_sync_enabled=False` skipping the lock entirely, a `ZohoDeskError`
    being caught/logged as a warning without crashing the loop, and a
    generic exception being caught/logged as an error and the loop
    surviving to the next tick (same "tick failure doesn't crash the loop"
    convention as `test_reconciliation.py`'s `reconciliation_loop` tests).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to two new test files.** No application code
  changed in `zoho_desk_db.py`, `zoho_desk_sync.py`, or anywhere else.
  Grepped every real caller/consumer of both modules:
  - `zoho_desk_db.*` — imported and called only from
    `routes/admin/support_tickets.py` (list/count/dashboard/trends
    endpoints, all read-fallback-to-live-Zoho callers) per the module's
    own docstring and confirmed via `grep -rn "zoho_desk_db" routes/`.
    `test_admin_support_tickets_routes.py` continues to stub this module
    at the route layer exactly as before — unmodified, unaffected.
  - `zoho_desk_sync.run_sync` — called from the admin "Sync now" endpoint
    (`routes/admin/support_tickets.py`, mocked in its existing test) and
    from `zoho_desk_sync_loop` itself (the startup loop). Also imported (as
    `zds`, mocked wholesale) by `test_admin_support_tickets_routes.py`'s
    `test_trigger_sync_success` / `test_trigger_sync_zoho_error_mapped` —
    unmodified.
  - `zoho_desk_sync_loop` — spawned exactly once, as a background task,
    from `backend/core/lifespan.py`'s startup sequence (one of the ~17
    documented loops). Not called from any request path.
  - No other module imports either file.
- **Background-loop replay safety verified, not changed.** The loop's
  Redis leader-lock (`redis_set_nx` on `spinr:zoho:sync:leader`, TTL
  `SYNC_INTERVAL_SECONDS - 60`) and `run_sync`'s upsert-keyed-on-`zoho_id`
  idempotency are both pre-existing and were only *tested*, not modified —
  matching the task's "recipe and replay-safety contract... use whenever
  adding a new loop" framing extended here to *verifying* an existing one.
- **Admin/support-ticketing surface only.** Neither module touches rides,
  dispatch, payments, corporate wallets, or the insurance-period state
  machine. Not a live-tested surface in the rides/payments/auth/
  corporate/safety sense CLAUDE.md's gate list calls out, though it is a
  real admin-facing data path (ticket list/dashboard/trends can silently
  serve stale or wrong totals if this logic breaks) — hence still worth the
  coverage pass.
- **No production code touched** — nothing to regress in ride state,
  wallet/allowance deltas, dispatch, or Stripe flows.

## 5. User-experience effect

None — test-only change. No rider/driver/corporate-admin/internal-admin
facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_zoho_desk_db.py` | New file — 26 tests | Close coverage gap on `services/zoho_desk_db.py` (11.76% → 100%) |
| `backend/tests/test_zoho_desk_sync.py` | New file — 24 tests | Close coverage gap on `utils/zoho_desk_sync.py` (22.33% → 96.12%) |
| `docs/change-log/2026-08-02-a1c-subtier-b-batch2-coverage.md` | New file (this log) | Required per CLAUDE.md for a Change Impact Log on any coverage-closing commit |
| `ACTION_ITEMS.md` | Updated A1c Sub-tier B bullet | Track progress per the existing series format; `utils/demand_forecast.py` marked closed (91.36%, pre-existing) with a note that this batch did not add to it |

`utils/demand_forecast.py` — **not modified**, no test file added or
changed. Listed here only because it was in scope to evaluate.

## 7. Before / after

Not applicable — purely additive test files; no existing behavior-changing
diff in any of the three target modules.

## 8. Rollback plan

`git revert` — pure test/doc addition, no live-data footprint, no
application code touched, no migration.

## 9. Verification performed

- [x] Fresh baseline measured before writing any tests (not the possibly-stale
  ACTION_ITEMS.md numbers):
  `pytest tests/ -q --cov=services.zoho_desk_db --cov=utils.demand_forecast --cov=utils.zoho_desk_sync --cov-report=term-missing --no-cov-on-fail`
  → `services/zoho_desk_db.py` **11.76%**, `utils/demand_forecast.py`
  **91.36%**, `utils/zoho_desk_sync.py` **22.33%** (confirmed via
  `coverage.xml` `line-rate` attributes, since the terminal capture of that
  long-running background command was truncated). Full-suite baseline at
  that point: **7573 passed, 8 skipped, 1 xfailed, 0 failed** (534s).
- [x] New test files run alone:
  `pytest tests/test_zoho_desk_db.py -q --no-cov` — **26 passed**.
  `pytest tests/test_zoho_desk_sync.py -q --no-cov` — **24 passed**.
- [x] Run together with the other existing test files touching these
  modules (`test_zoho_desk.py`, `test_zoho_ticket_service_area.py`,
  `test_admin_support_tickets_routes.py`, `test_demand_forecast.py`):
  `pytest tests/test_zoho_desk_db.py tests/test_zoho_desk_sync.py tests/test_demand_forecast.py tests/test_zoho_desk.py tests/test_zoho_ticket_service_area.py tests/test_admin_support_tickets_routes.py -q --cov=services.zoho_desk_db --cov=utils.demand_forecast --cov=utils.zoho_desk_sync --cov-report=term-missing --no-cov-on-fail`
  — **119 passed**, no collisions.
- [x] Coverage after (same measurement method): `services/zoho_desk_db.py`
  **100%** (up from 11.76%), `utils/zoho_desk_sync.py` **96.12%** (up from
  22.33%; remaining 4 missing lines are the bare-import `except ImportError:`
  fallback block, lines 28-31, unreachable without monkeypatching
  `sys.modules` — not attempted, judged not worth the fragility, same
  category of gap documented in the Sub-tier A subscriptions log).
  `utils/demand_forecast.py` unchanged at **91.36%** (not touched this
  batch — see section 1 and 10).
- [x] Full backend suite run twice: `pytest tests/ -q --no-cov`.
  - First run: **1 failed** (`test_routes_main_coverage.py::TestHealthCheckBareHappyPath::test_healthy_with_no_request`),
    7657 passed, 8 skipped, 1 xfailed (343s). Investigated: this test file
    is **untracked** in this branch (`git status --porcelain` shows `??`)
    — it belongs to a concurrent sibling session's Sub-tier B batch-1 work
    sitting in the shared working directory, not to this branch or to
    origin/main, and will not be part of this commit. The test's own
    docstring documents this exact failure mode as a known, already-
    identified cross-test-session flake (a process-global
    `utils.loop_monitor._heartbeats` dict polluted by other tests' real
    background-loop heartbeats, order/timing-dependent). Confirmed it is
    **not caused by this batch's diff**: (a) it passes in isolation
    (`pytest tests/test_routes_main_coverage.py::TestHealthCheckBareHappyPath::test_healthy_with_no_request -q --no-cov`
    → 1 passed); (b) a full-suite run with this batch's two new test files
    excluded (`--ignore=tests/test_zoho_desk_db.py --ignore=tests/test_zoho_desk_sync.py`)
    still ran the full 7609 tests cleanly (0 failed); (c) a second full run
    **with** this batch's files included passed cleanly with **0 failed**
    (7659 passed, 8 skipped, 1 xfailed, 347s) — confirming the first
    failure was the documented flake manifesting non-deterministically,
    not a regression from this diff.
  - Second (clean) run: **7659 passed, 8 skipped, 1 xfailed, 0 failed**
    (347s). Used as the reported after-state.
- [x] Blast-radius grep performed: see section 4 above, every real caller
  enumerated and confirmed unmodified.
- [x] PIPEDA logging check: grepped every `logger.*` call in both files.
  None log raw ticket content (subject, contact name/email) — only counts,
  cursor timestamps, ticket IDs, and exception messages. No PII-in-logs
  violation found.
- [ ] Manual repro / staging check — not applicable, test-only change with
  no deployable behavior difference.
- [ ] Feature-flagged — not applicable, test-only.

## 10. What was NOT verified

- Not run against real Supabase — mocked throughout via the repo's
  standard `mock_supabase_client` autouse fixture, matching convention for
  this test tier.
- `demand_forecast.py` was **not** re-verified line-by-line against its
  remaining 7 uncovered lines (17, 78-80, 88, 90, 94 — the bare-import
  fallback and a few historical-data edge-case skip branches in
  `_get_historical_hourly_demand`). At 91.36% with a dedicated, recently
  merged test file already covering the module's actual behavior-relevant
  logic (the `data_basis` labeling fix), closing the remaining 7 lines was
  judged out of scope for this batch per the task's explicit
  don't-duplicate-work instruction; flagging it as a small residual gap
  for a future pass rather than closing it here.
- The Redis leader-lock's real cross-replica mutual-exclusion behavior
  under concurrent replicas is not exercised (only that the loop correctly
  branches on `redis_set_nx`'s return value) — a real-or-fake-Redis
  integration test would be needed for that, out of scope for a unit-test
  coverage pass.
- `_upsert_batch`'s payload was checked for shape (serialized rows passed
  to `.upsert()`) but not for a real Postgres upsert-conflict/duplicate-key
  scenario — that's Supabase/Postgres behavior, not something this module's
  Python logic controls.

## 11. Not changing but considered (real finding, not fixed per task scope)

`list_mirror`'s search-term handling (`services/zoho_desk_db.py`, function
`list_mirror`) hand-rolls its own `.or_()` ILIKE clause instead of routing
through the shared `_escape_like`/`_postgrest_or_value` helpers in
`repositories/_base.py` that CLAUDE.md's "Query filters" section documents
as the required convention for exactly this kind of search-across-columns
`$or` build:

```python
s = (search or "").strip()
if s:
    esc = s.replace(",", " ")
    q = q.or_(
        f"ticket_number.ilike.%{esc}%,subject.ilike.%{esc}%,"
        f"contact_email.ilike.%{esc}%,contact_name.ilike.%{esc}%"
    )
```

Two consequences, neither a security issue but both real behavior quirks:
- The comma is silently **replaced with a space**, not escaped — a search
  for `"Doe, John"` silently becomes a search for `"Doe John"` rather than
  matching the literal comma. Not dangerous (a widened, not narrowed,
  match — the opposite direction of the `re.escape()` footgun CLAUDE.md
  warns about), just a minor correctness/precision gap: a ticket whose
  contact name is exactly `"Doe, John"` and nothing else containing "Doe"
  or "John" separately would still match, so this leans toward
  over-matching rather than under-matching or erroring.
- LIKE wildcard characters (`%`, `_`) in the search term are **not
  escaped** at all — a search containing a literal `%` or `_` would be
  interpreted as a SQL wildcard rather than a literal character, again
  widening the match rather than breaking it or leaking data.

Not fixed per this task's "test-only, do not modify the three files"
scope. Flagging for a future small fix (route through
`repositories._base._escape_like` the way `list_corporate_accounts_filtered`
already does) — low priority given the over-matching-only failure mode and
that this is an internal admin search box, not a public or
security-sensitive query path.
