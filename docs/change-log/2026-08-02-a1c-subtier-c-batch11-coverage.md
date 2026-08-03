# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (branch: `claude/a1c-subtier-c-batch11`) |
| Related issue or gap ID | `ACTION_ITEMS.md` A1c Sub-tier C, Batch 11 of the 13-batch itemization (PR #3335) |

## 1. Issue / gap identified

Three files sat below the Sub-tier C 80% target:

- `backend/routes/webhooks.py` — 75.40% (748 stmts, the largest file in the
  entire Sub-tier C list, Stripe-webhook-adjacent).
- `backend/ai/embeddings.py` — 76.79% (56 stmts).
- `backend/core/config.py` — 76.86% (121 stmts, the `Settings` fail-fast
  production-secrets validator).

## 2. Root cause

`routes/webhooks.py` already has extensive pre-existing test coverage
(`test_webhooks_main.py` alone is 2019 lines, plus `test_corporate_webhook.py`,
`test_webhook_stripe_v15.py`, `test_orphan_refund.py`, `test_ses_webhook.py`,
`test_twilio_inbound.py`) — the gap was concentrated in branches those files
never reached: the entire `charge.dispute.created`/`charge.dispute.closed`
lifecycle handler had **zero** coverage anywhere in the repo; several
"integration" tests called an internal helper function directly
(`_record_orphan_refund`) rather than driving it through the actual webhook
dispatch, so the surrounding `elif` block itself was untested; and roughly a
dozen best-effort push/receipt/WS failure-swallow branches were always mocked
to succeed.

`ai/embeddings.py`'s existing test file covered `embed_texts`'s soft-fail
contract thoroughly but always patched `_embed_openai`/`_embed_gemini`
wholesale — the real `AsyncOpenAI`/`google.generativeai` call sites were
never executed.

`core/config.py`'s `_guard_production_secrets` fail-fast validator was
**partially** covered by `test_p1_auth_hardening.py` (JWT_SECRET length,
Firebase app IDs, SUPABASE_REGION) but missing the placeholder-value checks,
the missing-Supabase-credential guards, `_hash_admin_password`, and the
entire `review_login_map`/`_validate_review_accounts` App Store/Play
reviewer-OTP-allowlist parser (zero coverage).

## 3. Fix / remediation

Test-only change across three new files:

- `backend/tests/test_webhooks_coverage_gap.py` (56 tests) — the
  dispute lifecycle, `account.updated` dispatch, `charge.refunded`'s full
  route dispatch (both the ride-found and orphan paths), `checkout.session.completed`'s
  subscription-linking and stale-cancel branches, `customer.subscription.deleted`'s
  legacy customer-id fallback, `customer.subscription.updated`'s
  `past_due`/`active`/no-row branches, `invoice.payment_failed`'s no-row
  branch, `_extract_invoice_payment_intent`'s successful-retrieve fallback,
  the "matched allowlist but fell through dispatch" defensive guard, ~12
  best-effort failure-swallow branches (WS push, receipt email, driver/rider
  push notifications across five different event types), and several SES/
  Twilio helper edge cases.
- `backend/tests/test_ai_embeddings_coverage.py` (5 tests) — `_embed_openai`/
  `_embed_gemini`'s real bodies (mocking only the third-party SDK client),
  reachability through `embed_texts`, and the timeout-returns-None path.
- `backend/tests/test_core_config_coverage.py` (33 tests) — the placeholder-
  value / missing-credential guards, `_hash_admin_password`,
  `review_login_map`/`_validate_review_accounts`, and the small `SECRET_KEY`/
  `debug` properties. Every "must raise in production" test asserts the
  actual `pytest.raises(...)`, not just that a line executed.

No application code in any of the three target files was modified.

**Real finding, fixed (test infrastructure, not production code):** the full
backend suite (8500+ tests) exposed a pre-existing cross-test `sys.modules`
pollution bug — the same class of issue as the already-fixed A8 (leaked
un-awaited `AsyncMock` coroutines). Some other test elsewhere in the suite
leaves `sys.modules["openai"]` and/or `sys.modules["twilio"]` replaced by an
incomplete stand-in that persists for the rest of the pytest process. This
made 4 of this batch's new tests fail — but *only* when run as part of the
full suite, never standalone or combined with a handful of related files —
depending on collection/execution order:
- `TestEmbedOpenaiBody::test_calls_async_openai_client_and_extracts_embeddings`
  / `test_reachable_via_embed_texts_end_to_end`: `patch("openai.AsyncOpenAI",
  ...)` failed with `AttributeError: <module 'openai'> does not have the
  attribute 'AsyncOpenAI'` — the real installed package has it; the polluted
  `sys.modules` entry did not.
- `TestTwilioInboundSignedPath::test_invalid_signature_rejected_403`: got
  `200` instead of the expected `403` — the source's own `from
  twilio.request_validator import RequestValidator` (inside
  `routes/webhooks.py`) silently degraded, effectively skipping signature
  validation.
- `TestTwilioInboundSignedPath::test_valid_signature_accepted_and_processes_stop`:
  `ModuleNotFoundError: No module named 'twilio.request_validator'; 'twilio'
  is not a package` — direct proof `sys.modules["twilio"]` had been replaced
  by a non-package object.

Root cause of the *leak itself* was not tracked down — an exhaustive grep
across `tests/`, `utils/`, `ai/`, `services/`, `routes/` for every
`sys.modules[...] =`, `patch.dict(sys.modules, ...)`, `ModuleType("openai"`,
and every file importing `openai`/`AsyncOpenAI` found no obviously-unscoped
mutation (the one existing `patch.dict(sys.modules, {"openai": ...})` usage,
in `test_ai_adapters_alt.py`, is a properly-scoped `with` block, fully
awaited on every call site — reproducing it isolated with just that file
plus the new one did not trigger the failure). Bisecting the full ~300-file
suite to find the exact culprit was out of scope for a coverage pass.
Instead, fixed defensively at the point of impact: both new test files now
force a fresh, real import of the affected package (dropping any
possibly-polluted `sys.modules` cache entry first) immediately before
relying on it — `_ensure_real_openai_imported()` in
`test_ai_embeddings_coverage.py`, `_ensure_real_twilio_imported()` in
`test_webhooks_coverage_gap.py`. Verified this holds under the full suite
(re-ran twice after the fix — see §9). Flagging the *upstream* leak as a
standing gap for a future session to bisect and fix at its source, the same
way A8 itself was eventually run to ground — this pass only made its own
tests resilient to it, it did not close the leak.

## 4. Risk & impact on existing functionality

**Blast radius: test-only, zero application code touched.** Every new test
file was run standalone and then combined with every pre-existing test file
that touches the same module, confirming no collisions (see §9).

For `routes/webhooks.py` specifically — this is the busiest, highest-stakes
file in the batch (Stripe idempotency, ride payment settlement, driver
subscription billing, wallet top-ups, corporate top-ups, refunds, disputes,
payouts):

- **Stripe idempotency convention** (`claim_stripe_event` before processing,
  per root `CLAUDE.md`) — every new test that reaches the dispatch body
  patches `claim_stripe_event` to return `True` (a fresh, unclaimed event),
  matching the existing test suite's convention. No test bypasses the
  idempotency gate.
- **"Do not silently swallow errors" convention** — every new failure-swallow
  test (WS push, receipt email, driver/admin push notifications) asserts the
  *existing* code's behavior (log-and-continue for genuinely best-effort side
  channels) rather than introducing new swallow points. No swallow was
  *added*; several *pre-existing* swallow branches simply had no regression
  test locking their behavior in before this pass.
- **Prior related work this session explicitly checked against** (per task
  instructions) — `docs/change-log/2026-08-01-c10-stripe-events-reconciliation-sweep.md`
  and `docs/change-log/2026-08-01-fix-mark-stripe-event-processed-swallow.md`,
  both same-day work on this exact file. Neither is duplicated or
  contradicted here: this pass adds coverage for *dispatch branches*
  (dispute lifecycle, checkout linking, etc.), while those two fixes touched
  the *unhandled-event-type comment* and `mark_stripe_event_processed`'s
  error-logging level respectively — disjoint code regions. This pass's new
  `TestHandlerLogicGapGuard` test independently confirms the exact comment
  text those two fixes corrected is still accurate (the unhandled-event
  return path still says `utils/stripe_reconcile.py`'s daily sweep surfaces
  the row, not "will replay").
- **Money/Decimal arithmetic** — no money-arithmetic code was touched; new
  assertions (e.g. `refund_amount == "15.00"`) check against the existing
  `Decimal`-based computation's string output, no float comparisons
  introduced.
- **No interaction with the ride state machine directly** — `charge.refunded`/
  `charge.dispute.*` write `payment_status` (a separate field from
  `status`), matching existing handler behavior; no `_require_ride_in_state`
  transition logic was added or exercised differently than the code already
  does.

For `ai/embeddings.py` — this module is deliberately "fails soft" (per its
own module docstring): every entry point already returns `None` on any
provider/config error so the caller falls back to lexical FAQ matching. The
new tests exercise the real success path plus the timeout path; no change to
the fail-soft contract.

For `core/config.py` — the `Settings` singleton (`backend/core/config.py:346`)
is imported by nearly every backend module. The new tests construct
*additional, independent* `Settings()` instances (never mutating or
reloading the module-level singleton — the same reload-avoidance pattern
`test_p1_auth_hardening.py` already documents and follows, to avoid the
known JWT-signing-breaks-later-tests failure mode from `importlib.reload()`).
Zero risk of cross-test pollution to the real `settings` object used by the
rest of the suite.

## 5. User-experience effect

None — test-only change, no rider/driver/corporate-admin/internal-admin
facing behavior change of any kind.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_webhooks_coverage_gap.py` | New file — 56 tests | Close the coverage gap on `routes/webhooks.py` (75.40% → 95%) |
| `backend/tests/test_ai_embeddings_coverage.py` | New file — 5 tests | Close the coverage gap on `ai/embeddings.py` (76.79% → 100%) |
| `backend/tests/test_core_config_coverage.py` | New file — 33 tests | Close the coverage gap on `core/config.py` (76.86% → 100%) |
| `ACTION_ITEMS.md` | A1c Sub-tier C — added the Batch 11 entry with before/after numbers | Track progress per the existing series format |
| `docs/change-log/2026-08-02-a1c-subtier-c-batch11-coverage.md` | New file (this log) | Required per CLAUDE.md for anything touching a live-tested surface (payments) |

## 7. Before / after

Not applicable — purely additive test files; no existing application-code
behavior-changing diff to show.

## 8. Rollback plan

`git revert` — pure test/doc addition, no live-data footprint, no
application code touched, no migration, no feature flag needed.

## 9. Verification performed

- [x] New test files run alone:
  - `pytest tests/test_webhooks_coverage_gap.py -q --no-cov` → **56 passed**.
  - `pytest tests/test_ai_embeddings_coverage.py -q --no-cov` → **5 passed**.
  - `pytest tests/test_core_config_coverage.py -q --no-cov` → **33 passed**.
- [x] Run together with every pre-existing test file touching each module
  (confirmed no collisions):
  - `pytest tests/test_webhooks_coverage_gap.py tests/test_webhooks_main.py
    tests/test_corporate_webhook.py tests/test_webhook_stripe_v15.py
    tests/test_orphan_refund.py tests/test_ses_webhook.py
    tests/test_twilio_inbound.py -q --cov=routes.webhooks
    --cov-report=term-missing` → **157 passed**, `routes/webhooks.py`
    **75.40% → 95%** (748 stmts, 184 → 40 missing).
  - `pytest tests/test_ai_embeddings.py tests/test_ai_embeddings_coverage.py
    -q --cov=ai.embeddings --cov-report=term-missing` → **18 passed**,
    `ai/embeddings.py` **76.79% → 100%** (56 stmts, 13 → 0 missing).
  - `pytest tests/test_core_config_coverage.py tests/test_p1_auth_hardening.py
    tests/test_admin_routes_auth.py tests/test_csrf_middleware.py -q
    --cov=core.config --cov-report=term-missing` → **81 passed**,
    `core/config.py` **76.86% → 100%** (121 stmts, 28 → 0 missing).
- [x] Full backend suite re-run: `pytest tests/ -q --no-cov` — first full run
  with the new test files (before the `sys.modules` pollution fix below)
  surfaced 4 real failures (see the "Real finding, fixed" note in §3); after
  the fix, re-ran twice for confirmation: **8509 passed, 8 skipped, 1
  xfailed, 0 failed** (397s), then again with `--cov` attached: **8509
  passed** (618s), both 0 failed. `routes/webhooks.py` **75.40% → 95%** (748
  stmts, 40 missing), `ai/embeddings.py` **76.79% → 100%**, `core/config.py`
  **76.86% → 100%** — measured against the full suite, not a keyword-
  filtered subset.
- [x] Blast-radius grep performed — see §4; every real caller of every
  touched function/route enumerated where non-trivial (dispute-closed's
  existing-row-vs-PI-lookup fallback, checkout.session.completed's two
  `_drv_subs` call sites, etc.).
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — Stripe
  idempotency (`claim_stripe_event` patched to `True` on every dispatch-body
  test), "do not silently swallow errors" (asserted existing swallow
  behavior, did not introduce any), `Decimal` money arithmetic (string-typed
  assertions, no float comparisons), dual-import pattern (respected; no
  `importlib.reload()` used, matching `test_p1_auth_hardening.py`'s own
  documented reload-avoidance note).
- [ ] Manual repro against real Supabase/Stripe — not applicable; every DB/
  Stripe/push call is mocked throughout, matching this test tier's existing
  convention across the whole webhook test suite.
- [ ] Feature-flagged — not applicable; test-only, no deployable behavior
  difference.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated, not assumed — test-only, every touched/added
  file enumerated in §6, every real caller of every non-trivially-tested
  function enumerated in §4
- [x] No silent behavior change to an already-shipped flow — zero
  application code modified in this pass

## What was NOT verified

- Not exercised against real Supabase, Stripe, Firebase, or Twilio — every
  test mocks the relevant client/SDK call, consistent with this repo's
  existing convention for this whole test tier (unit, not integration).
- **`routes/webhooks.py`'s remaining 40 uncovered lines were investigated,
  not just left alone:** 12 are the dual-import `ImportError` fallback at
  the top of the file (same structurally-unreachable pattern documented
  across many prior Sub-tier B/C closures — would require breaking
  `sys.modules` import machinery to reach). The other 28 are the bodies of
  the two `@default_limiter.limit(...)`-decorated routes (`ses_sns_webhook`,
  `twilio_inbound_sms`). This was investigated in detail during this
  session: a from-scratch, isolated coverage run of *only*
  `test_ses_webhook.py` (23 passing tests, all making real
  `test_client.post(...)` requests with correct-status-code /
  correct-response-body assertions) still shows these exact lines as
  uncovered — and critically, **this was already true in the original
  75.40% baseline**, before this session touched anything. This is a
  pre-existing coverage-instrumentation blind spot specific to
  rate-limiter-decorated routes in this test harness, not a testing gap this
  session introduced or could close by writing more tests — the business
  logic on those two routes is genuinely exercised and its outputs asserted
  correct by both the pre-existing suite and this session's additions
  (`TestSesRouteMiscBranches`, `TestTwilioInboundSignedPath`); only the
  coverage tool's line-attribution on those specific lines is affected.
  Flagging this as a standing tooling gap rather than re-discovering it in a
  future session — worth a dedicated look if Sub-tier C coverage tooling
  itself ever becomes its own workstream.
- `ai/embeddings.py`'s Gemini path test stubs `google.generativeai` via
  `patch.dict("sys.modules", ...)` rather than requiring the real package to
  be installed — matches how the module itself lazily imports it
  (`import google.generativeai as genai` inside the function body), so this
  is exercising the same import-then-call shape production code uses, not a
  simplification.
- `core/config.py`'s tests do not exercise the actual `.env`-file-loading
  path (`env_file=...` in `SettingsConfigDict`) — every test sets values via
  `os.environ` directly, which pydantic-settings prioritizes over the `.env`
  file, so the `.env`-file-specific code path itself remains implicitly
  covered only by whatever the real startup `Settings()` singleton
  instantiation already exercises (unchanged by this pass).
