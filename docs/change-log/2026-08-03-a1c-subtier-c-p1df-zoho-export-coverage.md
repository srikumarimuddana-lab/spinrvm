# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-03 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate / ai (support tickets) |
| PR / commit link | (this branch: `claude/a1c-subtier-c-batch-p1df-zoho-export`) |
| Related issue or gap ID | ACTION_ITEMS.md A1c (Track 2, Sub-tier C) |

## 0. Scope note — one of the original three target files was dropped

This task was originally scoped to three files:
`utils/period1_distance_finalizer.py`, `services/zoho_desk_service.py`, and
`utils/data_export_purge.py`. Mid-task, a fresh `git fetch origin main` +
PR search turned up **open draft PR #3354**
(`claude/a1c-subtier-c-batch-5`, "close A1c Sub-tier C coverage gap on
period1_distance_finalizer.py, driver_online.py, presence_sweeper.py") —
not present when this task's initial duplicate-check ran (its
`is:open ... in:title,body` search returned only one unrelated
false-positive match at that time; #3354 was created afterward, mid-task —
the exact concurrent-agent race this task's instructions warned about).
PR #3354 already raises `period1_distance_finalizer.py` 64% → 88% with a
test file at the *same path* this session had independently written
(`backend/tests/test_period1_distance_finalizer_coverage.py`). Per this
task's explicit instruction ("if any of your 3 target files already has
one, STOP, do not duplicate"), this session's own
`test_period1_distance_finalizer_coverage.py` was **deleted before commit**
and `period1_distance_finalizer.py` is **out of scope for this PR** —
picking it up is left entirely to PR #3354. This log and the accompanying
commit cover only `services/zoho_desk_service.py` and
`utils/data_export_purge.py`, both confirmed collision-free (no open PR
matched either filename at time of check).

## 1. Issue / gap identified

Two Sub-tier C (60-80% band) files sat below the 80% coverage target:

- `backend/services/zoho_desk_service.py` — 65.84% (202 stmts). Zoho Desk
  (support ticketing) integration used by the admin support-ticket panel.
- `backend/utils/data_export_purge.py` — 68.42% (57 stmts). PIPEDA
  deletion-retention purge — the hourly loop that deletes expired DSAR
  export ZIPs and admin Data Transfer exports from Storage.

## 2. Root cause

For both files, the pure-function happy-path and top-level error branches
already had test coverage, but:

- `zoho_desk_service.py`: most of the individual Zoho Desk endpoint
  wrapper functions (`search_tickets`, `create_ticket`,
  `get_ticket_threads`, `get_thread`, `add_comment`, `update_ticket`'s
  success path, `add_tags`/`remove_tags`, `list_agents`,
  `list_departments`, `get_default_department_id`) had never been called
  directly by any existing test — they were only reached indirectly (and
  only on their success path) via `services/zoho_desk_integration.py`'s
  tests, which mock `zoho.create_ticket` itself rather than exercising the
  real function body. `_token_is_fresh`'s edge branches (non-string
  expiry, naive datetime, unparseable expiry) and `_refresh_access_token`'s/
  `_request`'s transport-error and malformed-response branches were
  likewise untested.
- `data_export_purge.py`: the outer `data_export_purge_loop` wrapper (both
  tables ticked per iteration, independent exception guards, heartbeat)
  and the `supabase is None` / missing-`storage_path`-or-`id` early-outs
  in `_tick` had no dedicated test.

## 3. Fix / remediation

Test-only change. No application code in either file was modified. Added
two new test files:

- `backend/tests/test_data_export_purge_loop_coverage.py` (6 tests) — the
  `supabase is None` early-out in `_tick`; a row missing `storage_path` or
  `id` being skipped without ever touching Storage or the DB (a PIPEDA
  purge must never guess at a path); and the outer
  `data_export_purge_loop` — both tables (`data_export_objects` and
  `data_transfer_export_jobs`) ticked every iteration with independent
  exception guards (a failure on either tick must not skip the other or
  the heartbeat), and the heartbeat recorded once per iteration.
- `backend/tests/test_zoho_desk_service_coverage.py` (32 tests) —
  `_require_connected`'s enabled-but-missing-fields 503; every branch of
  `_token_is_fresh` (no expiry, non-string/already-`datetime` expiry, naive
  datetime treated as UTC, unparseable expiry, expired); `_refresh_access_token`'s
  transport-error and non-JSON-response-body failure paths; `_request`'s
  transport error on the actual API call (distinct from the token-refresh
  transport error already covered), a 204 response, a non-JSON 4xx/5xx
  error body, and a non-JSON 2xx body; and direct tests of every
  previously-untested endpoint wrapper: `search_tickets` (numeric →
  `ticketNumber`, keyword → `_all` wildcard, blank query, all filter
  params), `get_default_department_id`, `create_ticket` (no-department
  400, explicit department override with `contact_id`, inline-contact
  last-name synthesis from email, blank-subject default), `get_ticket_threads`,
  `get_thread`, `add_comment`, `update_ticket`'s success path (allowed-field
  filtering, `None`-value dropping), `add_tags`/`remove_tags` (blank-name
  filtering), `list_agents` (limit clamping), `list_departments`, and
  `ticket_count`'s `int()`-conversion-failure → 0 branch.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to two new test files.** No application code
  changed in `data_export_purge.py` or `zoho_desk_service.py`. Grepped
  every real caller of the functions under test:
  - `data_export_purge_loop` — spawned exactly once as a background task
    from `backend/core/lifespan.py` (`_spawn("data_export_purge (1h)",
    data_export_purge_loop)`). Not called from any request path.
  - `zoho_desk_service` functions — imported and called by
    `services/zoho_desk_integration.py` (auto-ticket-creation for lost &
    found, disputes, safety incidents, support escalation, and reverse-close
    linking), `utils/zoho_desk_sync.py` (a separate, already-95%-covered
    sync helper), `routes/admin/support_tickets.py` (the admin ticket panel
    — list/search/get/create/reply/comment/update/tag endpoints), and
    `routes/support.py` (rider/driver-facing support-ticket creation). None
    of these call sites were modified; the new tests exercise the same
    function signatures they call, with the same mocking convention
    (`monkeypatch.setattr(zoho, "db_supabase", ...)` /
    `monkeypatch.setattr(zoho.httpx, "AsyncClient", ...)`) already
    established in `test_zoho_desk.py`.
- **PIPEDA-relevant**: `data_export_purge.py` enforces the "User rights →
  Deletion" data-minimization policy documented in root `CLAUDE.md` — DSAR
  export ZIPs and admin Data Transfer exports must not accumulate in
  Storage past their signed-link TTL. The new tests assert what the module
  actually retains vs. scrubs: a row missing `storage_path`/`id` is
  skipped (never guessed at, never marked deleted), and both the primary
  DSAR-export table and the separate admin Data Transfer table are swept
  independently every hour with independent failure isolation (one
  table's tick failing must not skip the other, matching the existing
  per-table try/except in the loop). No change to what's retained/scrubbed
  or the TTL window — test-only verification of the existing contract
  (the per-row Storage-then-DB ordering and retry-on-failure behavior was
  already covered by the pre-existing `test_data_export_purge.py`).

## 5. User-experience effect

None — test-only change. No rider/driver/corporate-admin/internal-admin
facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_data_export_purge_loop_coverage.py` | New file — 6 tests | Close coverage gap on `utils/data_export_purge.py` (68.42% → 91%) |
| `backend/tests/test_zoho_desk_service_coverage.py` | New file — 32 tests | Close coverage gap on `services/zoho_desk_service.py` (65.84% → 100%) |
| `docs/change-log/2026-08-03-a1c-subtier-c-p1df-zoho-export-coverage.md` | New file (this log) | Required per CLAUDE.md for any commit touching a live-tested surface |
| `ACTION_ITEMS.md` | Added two closed entries under A1c Sub-tier C; noted `period1_distance_finalizer.py` deferred to PR #3354 | Track progress per the existing series format without duplicating in-flight work |

## 7. Before / after

Not applicable — purely additive test files; no existing behavior-changing
diff in either file under test.

## 8. Rollback plan

`git revert` — pure test/doc addition, no live-data footprint, no
application code touched, no migration.

## 9. Verification performed

- New test files run alone:
  `pytest tests/test_data_export_purge_loop_coverage.py tests/test_zoho_desk_service_coverage.py -q --no-cov`
  — **38 passed**.
- Run together with each file's existing test suite (no collisions):
  `pytest tests/test_data_export_purge.py tests/test_data_export_purge_loop_coverage.py tests/test_zoho_desk.py tests/test_zoho_desk_service_coverage.py -q --cov=services.zoho_desk_service --cov=utils.data_export_purge --cov-report=term-missing --no-cov-on-fail`
  — **44 passed**.
- Coverage measured (same command as above):
  - `utils/data_export_purge.py`: 68.42% → **91%** (57 stmts, 5 missing —
    was 18 missing). Remaining 5 lines (35-40) are the dual-import
    `ImportError` fallback for `loop_monitor.record_heartbeat`, documented
    in root CLAUDE.md as intentional ("do not simplify away") and
    structurally unreachable without `sys.modules` monkeypatching — same
    call prior Sub-tier B/C sessions made on this exact pattern.
  - `services/zoho_desk_service.py`: 65.84% → **100%** (202 stmts, 0
    missing).
- Full backend suite: `pytest tests/ -q --no-cov` — before (fresh baseline
  taken at session start on this branch) **8761 passed, 8 skipped, 1
  xfailed, 0 failed** → after **see PR description for the post-merge
  number** (this repo is under heavy concurrent-agent load today; the
  post-add run was taken after merging `origin/main` in, so its delta
  reflects both this branch's 38 new tests and other sessions' commits
  landing on `main` in between).
- Blast-radius grep performed: see section 4 above, every real caller
  enumerated and confirmed unmodified.
- Checked for open/recently-merged PRs before starting and again mid-task
  (see section 0) — `zoho_desk_service` and `data_export_purge` had zero
  open-PR matches at both checkpoints; `period1_distance_finalizer` did
  turn up a genuine collision (#3354) the second time and was dropped.

## 10. What was NOT verified

- Not run against real Supabase — mocked throughout (`db_supabase`,
  `httpx.AsyncClient`), matching repo convention for this test tier.
- `data_export_purge_loop`'s real hourly cross-replica behavior (multiple
  backend replicas racing the same expired row) is exercised only at the
  branch-coverage level (both tables ticked, exceptions isolated per
  table) — the pre-existing `test_data_export_purge.py` already covers the
  per-row idempotent-Storage-delete-then-mark-deleted replay safety in
  detail; this pass didn't add to that, only to the loop wrapper around it.
- No bugs found or fixed in either file during this pass — pure
  test-coverage exercise per the task instructions. Every exception branch
  in both files already behaved as documented (loud
  `logger.error(..., exc_info=True)`, no silent swallow of a DB/Storage/
  upstream error).
- The remaining 5 uncovered lines in `data_export_purge.py` are the
  documented dual-import `ImportError` fallback boilerplate — not pursued
  further, consistent with how every prior Sub-tier B/C session has
  treated this pattern.
- `period1_distance_finalizer.py` itself was not touched or improved by
  this session at all (see section 0) — its 64.38% baseline is unchanged
  by this PR; PR #3354 is the authoritative in-flight work on it.
