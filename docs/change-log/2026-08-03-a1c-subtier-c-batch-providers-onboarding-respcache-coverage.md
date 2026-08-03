# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-03 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | ai, drivers |
| PR / commit link | (branch: `claude/a1c-subtier-c-batch-providers-onboarding-respcache`) |
| Related issue or gap ID | `ACTION_ITEMS.md` A1c Sub-tier C — Batch 8 (`ai/providers/__init__.py`) + Batch 9 (`utils/driver_onboarding_reminder_rules.py`, `ai/response_cache.py`), per the 13-batch itemization (PR #3335) |

## 1. Issue / gap identified

Three files sat below the Sub-tier C 80% target:

- `backend/ai/providers/__init__.py` — 73.68% (38 stmts, the AI-provider
  adapter factory).
- `backend/utils/driver_onboarding_reminder_rules.py` — 74.00% (100 stmts,
  pure eligibility rules for driver onboarding reminder pushes).
- `backend/ai/response_cache.py` — 74.29% (35 stmts, FAQ response cache).

Note: this batch deliberately combines one file from Batch 8 with two files
from Batch 9, per the parent task's explicit scope, and excludes Batch 9's
third file (`services/zoho_desk_integration.py`) and Batch 8's other two
files (`ai/mcp_server.py`, `routes/disputes.py`) — those remain open for a
future batch. `ai/mcp_server.py` was independently closed by PR #3359
(`claude/a1c-subtier-c-batch-fares-fav-mcp`) since Batch 8 was itemized.

## 2. Root cause

All three gaps share the same shape: the *integration* behavior was already
well covered by existing tests, but each module's pure helper functions and
error-swallowing branches were only ever exercised indirectly (or not at
all) through a higher-level caller that always supplied the "happy path"
inputs.

- **`ai/providers/__init__.py`**: `test_ai_provider_factory.py` patches
  `_get_app_settings_fn` wholesale via `patch.object`, so the function's own
  body (its dual-import lazy resolution of `settings_loader.get_app_settings`)
  never ran. The `int(ai_max_output_tokens)` cast's `except (TypeError,
  ValueError)` fallback was never triggered because every existing test
  supplies a valid int or omits the key (which defaults to `1024` via
  `or 1024` before the cast even happens). The dual-path adapter-class
  loader's `except ImportError` fallback (relative `.module` import failing,
  retried as an absolute `ai.providers.module` import) was never forced.
- **`utils/driver_onboarding_reminder_rules.py`**: the only test file
  exercising this module (`test_driver_onboarding_reminders.py`) drives it
  *indirectly* through `utils/driver_onboarding_reminders.py`'s
  `check_driver_onboarding_reminders()` loop, whose fixture (`FakeReminderDB`)
  always supplies a valid IANA timezone, dict-shaped `required_documents`
  entries, a non-empty area-level document list, and (critically) an empty
  `docs` list — which short-circuits `missing_required_document_uploads`'s
  inner generator before `doc_matches_requirement` is ever called at all.
  None of the pure rule functions (`_zone`'s invalid-timezone fallback,
  `_load_list`'s string/JSON branches, `_pretty`, `mandatory_requirements`'s
  string-item and global-requirements-fallback branches,
  `doc_matches_requirement` itself, `parse_remindable_statuses`'s JSON-array
  branch) had a direct unit test.
- **`ai/response_cache.py`**: `test_ai_response_cache.py` is thorough on
  `cache_key`/`is_cacheable` (pure functions) and on the orchestrator wiring,
  but for the wiring tests it patches `orch.response_cache.get_cached` and
  `orch.response_cache.store_cached` entirely (`patch.object(orch.response_cache,
  "get_cached", get_mock)`) — so `get_cached`/`store_cached`'s own bodies
  (the `redis_get`/`redis_set` calls and their "never raises" try/except
  contract) had zero direct coverage anywhere in the repo.

## 3. Fix / remediation

Test-only change across three new files. No application code in any of the
three target files was modified.

- `backend/tests/test_ai_provider_factory_coverage.py` (5 tests) — invalid/
  `None` `ai_max_output_tokens` falling back to `1024` (both the `ValueError`
  and `TypeError` legs of the shared `except` clause), a direct unpatched
  call to `_get_app_settings_fn()` confirming it returns the real
  `settings_loader.get_app_settings` callable, and `_load_adapter_class`'s
  `ImportError` fallback forced via `patch("backend.ai.providers.importlib.import_module",
  side_effect=...)` (both a unit-level check on the loader function itself
  and an end-to-end `get_adapter()` call through the forced fallback path).
- `backend/tests/test_driver_onboarding_reminder_rules_coverage.py`
  (53 tests) — every pure function in the module called directly:
  `_zone`'s valid/`None`/invalid-timezone-with-warning-log paths,
  `driver_timezone`'s area-vs-driver-field-vs-default precedence,
  `local_date_for_send_window`/`open_send_windows`'s inside/outside-window
  branches, `parse_remindable_statuses`'s list/CSV/valid-JSON-array/invalid-
  JSON-array/non-str-non-list/all-blank/`None` inputs, `reminder_message`'s
  both message kinds plus the unknown-kind default, `_load_list`'s list/
  valid-JSON/invalid-JSON/non-list-JSON/blank-string/`None`/non-str-non-list
  inputs, `_pretty`'s formatting and falsy-default paths,
  `mandatory_requirements`'s string-item, dict-item (both `required` and
  `is_mandatory` gating keys), label-fallback, and the
  previously-fully-untested global-`global_reqs`-fallback-when-area-has-none
  branch, `doc_matches_requirement` directly (key match, requirement-id
  match with case-insensitive key fallback, document-type-equals-label,
  document-type-equals-key-with-spaces, normalized-substring match, no-match,
  empty-doc), and `missing_required_document_uploads`'s no-requirements/
  missing-upload/matched-upload/superseded-ignored/rejected-ignored/pending-
  counts-as-satisfying branches.
- `backend/tests/test_ai_response_cache_coverage.py` (5 tests) —
  `get_cached`'s success/miss/redis-exception-swallowed-to-`None` paths and
  `store_cached`'s success/redis-exception-swallowed-without-raising paths,
  both mocking `rc.redis_get`/`rc.redis_set` directly rather than the
  higher-level cache functions.

## 4. Risk & impact on existing functionality

**Blast radius: test-only, zero application code touched.** Every new test
file was run standalone (passing) and then combined with each module's
existing test file for a joint coverage run (see §9) — no collisions.

- **`ai/providers/__init__.py`** — `get_adapter()` is the sole entry point
  the AI orchestrator (`ai/orchestrator.py`) calls per chat turn; nothing in
  this pass changes its return value or error contract. The new
  `importlib.import_module` patch is scoped to a single `with` block per
  test and targets `backend.ai.providers.importlib.import_module`
  specifically (not the global `importlib` module), so it cannot leak into
  other tests or other modules' import machinery. Grepped for other callers
  of `_load_adapter_class`/`_get_app_settings_fn` — both are private
  (underscore-prefixed) and used only within this module; no other caller
  exists.
- **`utils/driver_onboarding_reminder_rules.py`** — this module is imported
  by exactly one caller, `utils/driver_onboarding_reminders.py`'s background
  loop (grepped `driver_onboarding_reminder_rules` across `backend/` —
  the only other hit besides this new test file and the existing
  `test_driver_onboarding_reminders.py` is the loop module itself). All new
  tests call the pure functions directly with plain dicts; none mutate
  module-level state, patch `logging`, or touch the loop's `db`/
  `send_push_notification` fixtures. The existing indirect-coverage test
  file (`test_driver_onboarding_reminders.py`) is untouched and still passes
  standalone.
- **`ai/response_cache.py`** — `get_cached`/`store_cached` are called from
  exactly one place, `ai/orchestrator.py`'s `run_chat_turn` (confirmed via
  grep — no other caller). The new tests patch `rc.redis_get`/`rc.redis_set`
  (the module's own bound names, matching this file's own import style: `from
  ..utils.redis_client import redis_get, redis_set`), so they exercise the
  real `get_cached`/`store_cached` bodies without touching a real Redis
  connection. This does not change the "never raises" contract those
  functions already implement — the new tests assert that contract holds,
  they do not alter it.
- No interaction with the ride state machine, wallet/allowance deltas,
  Stripe flows, or the 18 background loops in `backend/core/lifespan.py`
  (the driver-onboarding-reminder loop itself is a startup loop, but this
  pass adds no new loop and does not change its replay-safety properties —
  `test_driver_onboarding_reminders.py`'s claim/dedupe/cap tests, which
  cover that loop's actual replay-safety behavior, are unmodified).

## 5. User-experience effect

None — test-only change, no rider/driver/corporate-admin/internal-admin
facing behavior change of any kind.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_ai_provider_factory_coverage.py` | New file — 5 tests | Close the coverage gap on `ai/providers/__init__.py` (73.68% → 89.47%) |
| `backend/tests/test_driver_onboarding_reminder_rules_coverage.py` | New file — 53 tests | Close the coverage gap on `utils/driver_onboarding_reminder_rules.py` (74.00% → 100%) |
| `backend/tests/test_ai_response_cache_coverage.py` | New file — 5 tests | Close the coverage gap on `ai/response_cache.py` (74.29% → 100%) |
| `ACTION_ITEMS.md` | A1c Sub-tier C — added this batch's entry with before/after numbers | Track progress per the existing series format |
| `docs/change-log/2026-08-03-a1c-subtier-c-batch-providers-onboarding-respcache-coverage.md` | New file (this log) | Required per CLAUDE.md for any commit closing a gap |

## 7. Before / after

Not applicable — purely additive test files; no existing application-code
behavior-changing diff to show.

## 8. Rollback plan

`git revert` — pure test/doc addition, no live-data footprint, no
application code touched, no migration, no feature flag needed.

## 9. Verification performed

- [x] New test files run alone:
  - `pytest tests/test_ai_provider_factory_coverage.py -q --no-cov` → **5 passed**.
  - `pytest tests/test_driver_onboarding_reminder_rules_coverage.py -q --no-cov` → **53 passed**.
  - `pytest tests/test_ai_response_cache_coverage.py -q --no-cov` → **5 passed**.
  - All three together: `pytest tests/test_ai_provider_factory_coverage.py
    tests/test_ai_response_cache_coverage.py
    tests/test_driver_onboarding_reminder_rules_coverage.py -q --no-cov` →
    **63 passed**.
- [x] Run together with each module's pre-existing test file, with coverage:
  - `pytest tests/test_ai_provider_factory.py
    tests/test_ai_provider_factory_coverage.py --cov=ai.providers
    --cov-report=term-missing` → **13 passed**, `ai/providers/__init__.py`
    **73.68% → 89.47%** (38 stmts, 10 → 4 missing).
  - `pytest tests/test_driver_onboarding_reminders.py
    tests/test_driver_onboarding_reminder_rules_coverage.py
    --cov=utils.driver_onboarding_reminder_rules --cov-report=term-missing`
    → **76 passed**, `utils/driver_onboarding_reminder_rules.py`
    **74.00% → 100%** (100 stmts, 26 → 0 missing).
  - `pytest tests/test_ai_response_cache.py
    tests/test_ai_response_cache_coverage.py --cov=ai.response_cache
    --cov-report=term-missing` → **26 passed**, `ai/response_cache.py`
    **74.29% → 100%** (35 stmts, 9 → 0 missing).
- [x] Blast-radius grep performed — see §4; every real caller of each
  touched module's public functions enumerated.
- [x] Reviewed against relevant `CLAUDE.md` conventions — dual-import
  pattern respected (the `ai/providers/__init__.py` fallback test forces the
  `ImportError` branch rather than working around it, and correctly compares
  against the absolute-path-imported class since the fallback creates a
  genuine duplicate module object, matching this repo's documented
  dual-import behavior); "do not silently swallow errors" — the new
  `response_cache` tests assert the *existing* swallow-and-log contract
  holds, they do not introduce a new swallow point or weaken the existing
  one; no money/Decimal, ride-state-machine, or RLS code touched.
- [ ] Manual repro against real Supabase/Stripe/Redis — not applicable;
  every DB/Redis/settings call in the new tests is mocked, consistent with
  this repo's existing convention for this test tier (unit, not
  integration).
- [ ] Feature-flagged — not applicable; test-only, no deployable behavior
  difference.
- **Per explicit batching instruction for this session: the full backend
  suite was NOT run.** Only the three new files (standalone, and combined
  with each target module's existing test file) were executed. Full-suite/
  CI verification across all in-flight A1c batches is deferred to a later
  consolidated pass, to conserve tokens across the many parallel batches
  currently running this backlog.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated, not assumed — test-only, every touched/added
  file enumerated in §6, every real caller of every touched module's public
  functions enumerated in §4
- [x] No silent behavior change to an already-shipped flow — zero
  application code modified in this pass

## What was NOT verified

- **The full backend test suite was not run** (see §9) — this is a deferred,
  explicitly-instructed trade-off for this batch, not an oversight. A later
  consolidated session is expected to run the full suite / CI gates across
  all in-flight A1c Sub-tier C batches at once.
- Not exercised against real Supabase, Stripe, Firebase, Redis, or Twilio —
  every test mocks the relevant client/SDK call, consistent with this repo's
  existing convention for this whole test tier (unit, not integration).
- `ai/providers/__init__.py`'s remaining 4 uncovered lines (18-19, 27-28)
  are the two dual-import `ImportError` fallback branches for the top-level
  `from .base import ...` and `_get_app_settings_fn`'s `from
  ...settings_loader import get_app_settings` — the same
  structurally-unreachable-without-breaking-`sys.modules`-import-machinery
  pattern documented in multiple prior Sub-tier B/C closures (e.g. the
  2026-08-02 Batch 11 log's `routes/webhooks.py` note). This repo's own
  `test_dual_import_parity.py` verifies these branches' *structural* parity
  via AST analysis rather than runtime line coverage, which is the
  established convention this pass follows rather than deviates from. (The
  third dual-import branch in this file, `_load_adapter_class`'s fallback,
  *was* made reachable and is covered — it does not depend on `sys.modules`
  import-machinery breakage the way the module-level ones do, since it is
  called at runtime rather than at module-import time, so
  `importlib.import_module` could be patched directly.)
- No visual/snapshot regression tooling exists in this repo for backend test
  files (not applicable to this surface — no UI change of any kind).
