<!-- Validation record for PR #5079 and the implementation plan derived from it.
     Produced 2026-09-07 by a Claude Code session on branch claude/pr-5079-analysis-plan-55dus9;
     verdicts come from code reading, ACTION_ITEMS/audit cross-referencing, and GitHub Actions run
     history — no backend tests were executed in the validating environment (deps not installed). -->

# PR #5079 — validated findings and prioritized hardening plan

## Context

[PR #5079](https://github.com/srikumarimuddana-lab/spinrvm/pull/5079) is a docs-only PR adding
`docs/audit/2026-09-07-engineering-review-hardening-plan.md`: a read-only engineering review with
13 numbered findings (F1–F13) plus testing, maintainability and process observations, and six
proposed work packages. The repo owner asked for each finding to be validated by an architect, a senior
developer and a product-manager lens: is it real, is it useful, is it already implemented or
tracked, and what can be done immediately.

Method used: three parallel validation passes against `main`@`1d69834` (the branch this validation
ran on), then direct re-verification of every finding proposed for immediate work (file:line
reads, GitHub Actions run history, saved CI logs), then cross-referencing against
`ACTION_ITEMS.md`, `docs/audit/2026-09-01-engineering-director-teardown.md`,
`docs/audit/2026-09-03-engineering-director-teardown-round2.md`,
`docs/audit/2026-08-27-cicd-gates-guardrails-audit.md`, `docs/audit/2026-09-03-c59-teardown-round2-remediation-plan.md`
and the standing implementation plan `plans/2026-09-03-path-to-a-implementation-plan.md` (WS-1..WS-9).

Headline: the PR's mechanisms are mostly real, but its evidence is stale on about half the items,
it re-states work that is already tracked or scheduled, it mis-describes the one money finding
(F1), and it misses the most urgent thing its own evidence shows: **`main` is red right now
because of a real, already-tracked production defect (AI18)**. Backend Python deps were not
installed in the validating environment, so nothing here was executed locally; every verdict is from code
reading plus CI run history.

## Verdict matrix

Legend: REAL = defect confirmed in code; PARTIAL = mechanism real but the PR's description or
evidence is wrong; TRACKED = already has an ACTION_ITEMS ID or workstream; DECIDED = a recorded
decision exists; FIXED = already landed on `main`.

| # | PR finding | Verdict | Verified evidence | Existing tracking | Do now? |
|---|---|---|---|---|---|
| T-a | 7 backend test failures (`TestSearchFaqsPublicWeb`) in CI | **REAL, CURRENT — main is red** on the last 4 `ci.yml` runs incl. HEAD `1d69834` (run 34155272994). Root cause: `backend/ai/tools.py:381-385` fails closed on a missing `user["id"]`; `backend/ai/public_assistant.py:231-238` deliberately sends the web user with no `id` → every spinr.ca tool call returns "not authorized". The 7 tests (PR #5056, `adf4549`) are the first to exercise the real `execute_tool` and fail at test line 171 (`get_rows("faqs")` never reached). PR #5079's "do not establish broken production features" is wrong: they establish one. | **AI18** (`ACTION_ITEMS.md:17718`, open) with a design already written | **Yes — first** |
| F1 | Refund accounting not atomic | **PARTIAL (inverse)**. The PR's $30 double-ledger scenario is already prevented by the monotonic guard `delta_cents <= 0` at `backend/routes/webhooks.py:1181`. Real defects: (a) ride update at `:1228-1230` filters on `id` only — no compare-and-swap on `refund_amount`; (b) ordering is ride update (`:1228`) then ledger (`:1255`), so a failure between them advances `refund_amount` and the admin replay (`routes/admin/stripe_events.py:188-238`) then skips the ledger row forever; (c) `record_refund_event` (`services/payment_service.py:298-352`) passes no `dedupe_key` (`:340`), unlike the dispute path at `:429`. `ledger_service.record_event` returns `None` on failure (`services/ledger_service.py:400-431`). No concurrency test exists. | Not tracked (2026-08-19 change-log proved single-writer only) | **Yes** (S) |
| F2 | Custom exceptions expose diagnostics | **PARTIAL**. `spinr_exception_handler` returns `exc.to_dict()` verbatim (`backend/utils/error_handling.py:644-667`); the 4xx redactor and 5xx sanitiser only run for `HTTPException` (`:714-812`). Unredacted sources: `backend/dependencies/__init__.py:479` and `:533` (`details={"original": str(e)}`). But `details` is a live client contract (3DS `client_secret`, `unpaid_ride_id`; `shared/api/client.ts:452-457`), so a blanket strip is wrong. | Not tracked; 2026-09-05 fix covered `HTTPException` 4xx only | **Yes** (S, key-targeted) |
| F3 | RLS disabled on 4 tables | **TRACKED + DECIDED (deferred)**. C43 (`ACTION_ITEMS.md:19085`), deferred by the owner 2026-08-25 until A41-family legacy migration; migration 379 + runbook ready; live advisor scan and anon-key-client grep already done. Deferral has no expiry date. | C43 | No code. Add a re-review date |
| F4a | `main` unprotected, PR #5048 merged before checks | **TRACKED ×3**. A43, C73 (`:21921`, root-caused via check-run timestamps), C21, C13, E8; CI/CD audit §1. Repo-admin-only. | A43/C73/C21 | Human only |
| F4b | `security-gates.yml` summary uses `always()` and never fails | **REAL, NEW**. `.github/workflows/security-gates.yml:775-782` has no `needs.*.result` check and no `exit 1`, yet its comment names it the intended required check. `ci-guardrails.yml:1502-1542` has the same never-failing roll-up. | Not tracked | **Yes** (S) |
| F4c | `deploy-fly.yml` deploys on push regardless of CI | **DUPLICATE**. Round-2 Blocker #3; C59 T3; WS-4. | WS-4, C59 T3 | No |
| F5 | Redis URL logged with credentials | **REAL, NEW**. `backend/utils/redis_client.py:159` logs `url[:30]` — leaks a full `redis://:pw@host` password and 13 chars of an Upstash token. Masking helper exists at `utils/redis_diag.py:85-97`. | Not tracked | **Yes** (S) |
| F6 | Metrics don't aggregate across workers | **PARTIAL, new nuance**. Per-process design is documented (`utils/metrics.py:8-13`, ADR-010) and cross-replica scraping is solved; the untracked gap is `fly.toml:43` `UVICORN_WORKERS="2"` sharing one port so each scrape hits a random worker. `Dockerfile:110` / `railway.json:8` default to 4. | Not tracked | Later (PromQL-sensitive) |
| F7 | Done-callbacks call `t.exception()` on cancelled tasks | **REAL, NEW**, 3 sites: `backend/ai/tools.py:309`, `backend/ai/threat.py:128`, `backend/ai/tools_support.py:182`; also no strong task reference (GC hazard) unlike `utils/background.py:37-61`. | Not tracked | Yes (S, low value) |
| F8 | Sync Stripe call in async webhook handler | **REAL, NEW**. `backend/routes/webhooks.py:233` (`Invoice.retrieve` inside sync helper defined `:190`, called from async handler `:305`) and `:1652` (`Subscription.retrieve`). 9 further bare sites in background loops/admin. Round-2 graded this path "Fine" — it missed this. | Not tracked | **Yes** (S) |
| F9 | Direct-pool dispatch off, sequential claims | **NOT A DEFECT**. Flag default `False` is a recorded rollback switch (migration 401), gated on C50's G6 Go/No-Go (ADR-011); claim loop is one round trip per candidate and bounded by `max_offers`; batched RPC exists and is tested. C54 closed 2026-09-04. | C50, WS-5 subtask 1, H7 | No |
| F10 | 64-thread executor, unbounded queue | **DUPLICATE + partly wrong**. 09-01 teardown C3, round-2 bottleneck #2. Queue-wait histograms already exist (`repositories/_base.py:428-440`); circuit breaker and deadline shedding exist. Correct part: cancel-after-timeout doesn't stop a running sync call (`:449-462`); bounded admission is the only new idea (M, risky). | C3 / WS-5, WS-9 | Later |
| F11 | Estimates wait 3.5 s | **DECIDED 2026-08-21** (B6 closed; `docs/audit/2026-08-19-decision-writeups.md` §8 Option A; CLAUDE.md SLA exception). | B6 | Strike from PR |
| F12 | Admin auth writes last-activity per request | **REAL, NEW**. `backend/dependencies/__init__.py:310` uncached staff read + `:328` unconditional `update_one` on every admin request; single reader is the 30-min idle check at `:316-322`; dashboard polls every 5–60 s. | Not tracked | **Yes** (S) |
| F13 | Loops start in every worker | **DUPLICATE** (09-01 C2, round-2 Blocker #2, WS-3); every loop already holds its own Redis leader lock. New: `backend/fly.toml:11-13` says 16 loops and `:20-21` says 18; actual is 42 `_spawn` calls / 41 watchdog names. | WS-3 | Comment fix only |
| M-1 | God files (4,334 / 4,171 / 2,357 / 2,340 / 2,324 lines) | **DUPLICATE**, counts verified. | Round-2 §Maint #1, WS-8 | No |
| M-2 | `AGENTS.md` drift | **REAL** (5 hard contradictions: next migration "102" vs 407; Railway/Render vs Fly primary; 16 loops vs 41; stdlib `extra=` vs enforced loguru rule; dead paths `.Codex/`, `graphify-out/`, `routes/rides.py`). The PR's `migrate.py` sub-claim is wrong (`AGENTS.md:233` is correct). #5078 reverted the Codex-section removal, so keep the Codex framing. | Not tracked | Yes (S–M) |
| M-3 | `ACTION_ITEMS.md` size (21,999 lines) | **DUPLICATE** (round-2 #12). | — | No |
| M-4 | Two migrations numbered 376 | Duplicate itself is **TRACKED/accepted** (`ACTION_ITEMS.md:15690-15697`, both applied in prod, renaming forbidden). **NEW: the nightly sweep is red every night since 2026-09-02** (`migration-duplicate-nightly.yml` runs #3–#8) because `backend/migrations/.known_duplicate_prefixes.json` (max key "370") lacks "376". | Not tracked | **Yes** (S) |
| T-b | yarn-audit HIGH findings (browserslist, fast-uri) | **FIXED at HEAD** by #5078. | — | Strike from PR |
| T-c | Admin-bundle secret scan 504 | **REAL flake class, low**. `ci.yml` `security-scan` failed 2026-09-07 at "Install trufflehog v3" (non-gzip download), G5b failed on a 504 the same day; both passed on the next run. | Not tracked | Optional (S) |
| T-d | Maestro workflow references `matrix` in a job-level `if` | **REAL and worse than stated**. `.github/workflows/maestro-e2e.yml:66-74` (`matrix.app.dir` at `:73`); `matrix` is not available in `jobs.<id>.if`, so the file fails validation: 3,206 runs, every one a zero-duration `failure` with no jobs, recorded on every push although `on:` (`:33-46`) has only `workflow_dispatch` + `pull_request[labeled]`. B25 only tracks the missing secrets. | B25 (partial) | **Yes** (S) |
| D-1 | Missing review doc | **NEW doc gap**: 8 files in `docs/change-log/2026-09-05-*.md` cite `docs/audit/2026-09-05-engineering-director-review-round3.md`, which was never committed. | Not tracked | Yes (doc) |
| Arch | Outbox/worker, OpenAPI contracts, psycopg pool, Fly readiness | **DUPLICATE** of round-2 tech-stack table / WS-3, WS-4, WS-5 / round-2 plan item 22; liveness/readiness split already landed (`docs/change-log/2026-09-05-health-liveness-readiness-split.md`). | WS-3/4/5 | No |

Lens disagreements resolved with primary evidence: the product lens could not find the Redis log
line (it exists at `redis_client.py:159`); the product lens called the Maestro finding a false
positive (the Actions history shows 3,206 startup failures, so it is real).

## What can be done immediately (summary)

Seven small PRs, each independently revertible, ordered by urgency and risk. Nothing here needs a
migration, a flag, or a new dependency. Backend deps are not installed in this environment, so the
implementing session must run `cd backend && pip install -r requirements.txt` first.

| Order | PR | Contents | Why here |
|---|---|---|---|
| 0 | **Review on PR #5079** (request changes) | One GitHub review listing the corrections in the disposition section below; no code | Authorized by the repo owner; gets the author revising while code work proceeds. |
| 1 | **PR-1 `fix(ai)`: AI18 anonymous web tool allow-list** | 2 backend files + tests + Change Impact Log; closes AI18 | Unbreaks `main` (red on every run since 2026-09-07) and makes the spinr.ca assistant actually use FAQ tools. Alone, so it is bisectable. |
| 2 | **PR-2 `ci`: gate roll-ups, nightly baseline, Maestro workflow** | F4b, migration-376 baseline, Maestro `matrix` bug, optional scanner-download retries | Zero runtime risk, ≤1 hour, greens a nightly job that has been red since 2026-09-02 and stops one failed run per push. File-disjoint from everything else. |
| 3 | **PR-3 `fix(payments)`: charge.refunded CAS + idempotent ledger + replay recovery (F1)** | `payment_service.py`, `webhooks.py`, 2 test files, Change Impact Log | Silent, permanent loss of a 7-year ledger row is the most serious defect the PR surfaced. Money path: own reviewer pass. |
| 4 | **PR-4 `fix(webhooks)`: Stripe retrieve off the event loop (F8)** | `webhooks.py` two call sites + tests | Same file as PR-3, so after it to avoid a conflicting rebase on a money handler. |
| 5 | **PR-5 `fix(errors)`: redact `SpinrException.details` (F2)** | `error_handling.py`, `dependencies/__init__.py`, tests, Change Impact Log | Contract-sensitive (mobile reads `details`): own reviewer pass. |
| 6 | **PR-6 `chore(hygiene)`: F5, F7, F12, F13 comment** | one commit each | Small and independent; batched to cut review overhead. |
| 7 | **PR-7 `docs`: AGENTS.md drift, round-3 citations, ACTION_ITEMS C74–C87** | docs only | Last; no runtime effect. |

Ground rules from `CLAUDE.md` that apply to every PR: ≤3 files per subtask with a verify step;
one logical change per commit; Change Impact & Risk Log for anything touching payments, auth, CI
gates or a public surface; Decimal-only money; dual-import pattern; loguru conventions (`logger.bind`,
`logger.opt(exception=True)`) in loguru modules, `%`-style + `exc_info=` in stdlib modules; never
skip or delete a test to go green; run the named reviewer subagent before opening each PR;
`pytest -m unit -q` and `ruff check .` before every push.

## PR-1 — AI18: anonymous web tool allow-list (unbreaks `main`)

| Field | Detail |
|---|---|
| Files | `backend/ai/tools.py`, `backend/ai/tools_support.py`, `backend/tests/test_ai_tool_scoping.py` |
| Change | `ToolSpec` (`tools.py:88-110`): add `allow_anonymous: bool = False` with a comment "honoured only for audience `web`; never for tools with `owned_id_args`". Identity gate (`tools.py:381-385`): `if not user_id and not (audience == "web" and spec.allow_anonymous): <existing fail-closed block>`. Keep the audience check at `:376` first. Audit payload (`tools.py:339`): `"user_id": (user or {}).get("id") or ("web:anonymous" if audience == "web" else None)` because `ai_tool_audit.user_id` is `TEXT NOT NULL` (`backend/migrations/217_ai_tool_audit.sql:33`) — today every web call also fails the audit insert. `search_faqs` (`tools_support.py:461-482`) and `get_company_info` (`:486-494`): `allow_anonymous=True`. Do not touch `escalate_to_support`. Do **not** add an `id` to `public_assistant.py`'s `tool_user` — `tests/test_ai_public_assistant.py:173` asserts its absence and the no-identity design is deliberate. Handler safety already holds: `_current_area_scope` reads `user["id"]` only when `audience == "driver"` (`tools_support.py:256-258`); the executor injects `ai_audience` (`tools.py:426`) so `_faq_audiences` returns all three audiences on the web path. |
| Tests (add to `TestRuntimeEnforcement` after `:77`) | (1) web + `search_faqs` + no id → handler runs (`patch.object(tools_support.db_supabase, "get_rows", …)`), `ok is True`; (2) registry invariant: every spec with `allow_anonymous` has `"web"` in `audiences` and empty `owned_id_args`; (3) `rider`/`driver` audience + no id + `search_faqs` → still `"not authorized"`; (4) web + no id → audit payload `user_id == "web:anonymous"`. Keep `test_missing_user_id_fails_closed` (`:77`) and `test_execute_tool_refuses_an_account_tool_at_web_audience` (`test_ai_public_assistant.py:110`) green and unchanged. |
| Verify | `pytest tests/test_ai_tools_support.py tests/test_ai_tool_scoping.py tests/test_ai_public_assistant.py tests/test_ai_mcp.py -q` → the 7 red tests pass; then `pytest -m unit -q`. After merge: `backend-test` green on `main` for the first time since `adf4549`. After deploy: the spinr.ca widget answers "what do I need to drive?" from FAQs; logs stop emitting `ai tool blocked: no authenticated user id`; `ai_security_events` stops receiving `tool_blocked` rows from web. |
| Blast radius | `execute_tool` callers: `ai/orchestrator.py` (rider/driver chat, carry ids — unaffected), `ai/public_assistant.py:283`, MCP surface (`tests/test_ai_mcp.py`). Behaviour deltas: web FAQ calls stop filing `critical` `tool_blocked` security events (`tools.py:351-364`); `ai_tool_audit` rows now insert for web calls with the sentinel — grep `routes/admin/` for readers that join `ai_tool_audit.user_id` to `users.id` and confirm the sentinel does not break a join. |
| Rollback | Revert PR-1 (returns to today's blocked state) or flip `ai_public_chat_enabled` off in `app_settings` (`public_assistant.py:185-193`, no redeploy). |
| Reviewers / log | `spinr-ai-guardrail-reviewer` + `spinr-security-auditor`. Change Impact Log required (public surface): `docs/change-log/2026-09-08-ai18-anonymous-web-tool-allowlist.md`. Second commit: log + `ACTION_ITEMS.md` AI18 → `[x]`, noting the seven `TestSearchFaqsPublicWeb` failures were this defect. |

## PR-2 — CI gates: failing roll-ups, nightly baseline, valid Maestro workflow

| Subtask | Files | Change | Verify |
|---|---|---|---|
| 2a F4b roll-ups | `.github/workflows/security-gates.yml` (`summary`, `:775-800`), `.github/workflows/ci-guardrails.yml` (`guardrail-summary`, `:1488-1580`) | Append a final step to each: `if: ${{ contains(needs.*.result, 'failure') \|\| contains(needs.*.result, 'cancelled') }}` → `echo "::error::one or more gates failed"; exit 1`. `skipped` (path filters) still passes. Leave C24's `coverage-regression-gate` semantics alone (`ci-guardrails.yml:1518-1526`). Update the comment text that says the summary is the intended required check to say it now actually fails. | Local `actionlint` (`docker run --rm -v "$PWD":/repo -w /repo rhysd/actionlint:latest`); both summary jobs green on the PR; on a docs-only PR `skipped` gates still pass; the next real gate failure shows the summary job red. |
| 2b nightly baseline | `backend/migrations/.known_duplicate_prefixes.json` | Add `"376": ["376_corporate_wallet_adjust_idempotency.sql", "376_service_area_tax_history.sql"]` after `"370"`. Both are applied in production (`ACTION_ITEMS.md:15690-15697`); renumbering is forbidden (filename is the idempotency key). | Re-run the workflow's own Python (`migration-duplicate-nightly.yml:40-70`) locally → `new_or_worsened == {}`; next scheduled run (09:17 UTC) green, or a human dispatches it. |
| 2c Maestro | `.github/workflows/maestro-e2e.yml` (`:53-82`) | Split into a `plan` job whose job-level `if:` keeps only the trigger gate (`workflow_dispatch` or `pull_request` labeled `run-maestro`) and whose one step emits the matrix as JSON from `inputs.apps` (`both` → both entries, else one); `maestro-android` gets `needs: plan`, `strategy.matrix.app: ${{ fromJSON(needs.plan.outputs.matrix) }}`, and its own `if:` removed. Replace the `:59-65` comment (its "folded into the same `if`" rationale is the bug: `matrix` is unavailable in `jobs.<id>.if`). Steps `:84+` unchanged. | `actionlint` reports no context error; after merge **the next push to any branch produces no zero-duration `maestro-e2e.yml` failure run** (today: one per push, 3,206 total); a manual dispatch reaches "Setup EAS" and fails there on the missing `EXPO_TOKEN` — that is B25's blocker and the expected new behaviour. |
| 2d scanner retries (optional) | `.github/workflows/ci.yml` (`security-scan`, `:909-939`), `security-gates.yml` `bundle-secrets` (`:561-612`) | `curl -fsSL --retry 5 --retry-delay 3 --retry-all-errors`, `tar -tzf` before extraction, Trivy upload guarded by `if: ${{ always() && hashFiles('trivy-results.sarif') != '' }}`. | `actionlint`; green `security-scan`. |

Rollback: revert any step. Blast radius: `main-branch-guard.yml` and the pending A43/C73
required-checks list key on the summary jobs; `label-run-maestro.yml` will now genuinely start
the Maestro job (and bill EAS minutes) once B25's secrets exist — say so in the log. Reviewer:
`spinr-cicd-infra-reviewer` (+ `spinr-migration-reviewer` sign-off that 2b implies no renumber).
One Change Impact Log for the PR: `docs/change-log/2026-09-08-ci-gate-rollups-nightly-baseline-maestro.md`.

## PR-3 — F1: charge.refunded compare-and-swap, idempotent ledger, replay recovery

Design note: writing the ledger before the ride row does **not** fix the concurrent case (two
events reading `NULL` would book −1000 and −2000 under different cumulative keys). The order is
therefore CAS first → idempotent ledger → recovery on replay.

| Subtask | Files | Change | Tests |
|---|---|---|---|
| 3a ledger side | `backend/services/payment_service.py`, `backend/tests/test_refund_ledger.py` | `record_refund_event` (`:298-352`): add kw-only `dedupe_key: str \| None = None`, forward it to `ledger_service.record_event` (`:340`), return the ledger id (`-> Optional[str]`); keep "never raises". Add `async def refund_booked_cents(payment_intent_id) -> int`: sum of `-delta_cents` over `financial_events` rows with `event_type="stripe_refund"` and `ref=payment_intent_id` (confirm `record_refund_event` sets `ref` to the PI; adjust the filter to whatever it sets); on DB error `logger.error` and **raise** (money path). | (1) key forwarded verbatim; (2) returns the id, or `None` when the ledger returns `None`; (3) `refund_booked_cents` sums negatives to a positive total, 0 on no rows. Existing callers pass no key → unchanged. |
| 3b handler | `backend/routes/webhooks.py` (`:1181-1261`), `backend/tests/test_routes_webhooks_coverage.py`, `backend/tests/test_refund_replay_safety.py` (new) | In the `delta_cents > 0` branch: (1) `prev_raw = ride.get("refund_amount")` (use the raw read-back value; `None` compiles to `is.null`), `dedupe_key = f"stripe_refund\|{payment_intent_id}\|{refunded_cents}"` (cumulative cents). (2) **CAS**: `updated = await update_one("rides", {"id": ride_id, "refund_amount": prev_raw}, payload)`; if `updated is None` → `logger.error(...)`, `await unclaim_stripe_event(event_id)`, `raise HTTPException(500, "Refund state changed concurrently — Stripe will retry")` (same pattern as `:880-891`; the tail's `mark_stripe_event_processed` must not run). (3) `ledger_id = await record_refund_event(..., dedupe_key=dedupe_key)`; if `None` → error log, unclaim, 500. **No revert of the ride row** (a reverted `payment_status` would let a delayed `invoice.paid` re-settle a refunded ride via `:271`). (4) Notifications unchanged. In the `delta_cents <= 0` branch (`:1181-1202`) add a recovery case before the stale log: `if refunded_cents == previous_refunded_cents and refunded_cents > 0:` → `missing = refunded_cents - await refund_booked_cents(pi)`; if `missing > 0` book it via `record_refund_event(refund_cents=missing, dedupe_key=dedupe_key)` (info log "recovered ledger row after partial failure"; `None` → unclaim + 500); else fall through. Fix the C3 comment at `:1241-1250` (it still says dedup is "upstream by claim_stripe_event"). | `test_refund_replay_safety.py` (mocked db, fixture style of `test_routes_webhooks_coverage.py:95-330`): (1) two events both read `refund_amount=None`; `update_one` returns a row then `None`; the loser raises 500 and `unclaim_stripe_event` is awaited; the ledger is written once; redelivery of the loser with `refund_amount="10.00"` filters on `{"id":…, "refund_amount": "10.00"}` and books its 1000 under key `\|2000`. (2) ledger returns `None` after the CAS → 500 + unclaim, ride not reverted; replay with equal cumulative and no ledger rows → books the full missing amount under the same key. (3) same cumulative twice with the row already booked → no write. (4) keys use cumulative, not delta. In `test_routes_webhooks_coverage.py` add `args[1]` assertions on the CAS filter to the existing refund tests (`refund_amount: None` first event, `"10.00"` second). |

Trace that the design converges: A(1000) CAS `NULL→"10.00"` then crash; retry A: delta 0, equality,
booked 0 → books −1000 `|1000`; B(2000): delta 1000, CAS `"10.00"→"20.00"`, books −1000 `|2000`;
total −2000 ✓. Loser-first ordering (B wins CAS first, books −2000 `|2000`; A redelivered reads
"20.00", delta < 0, not equal → skipped) ✓.

Verify: `pytest tests/test_refund_replay_safety.py tests/test_routes_webhooks_coverage.py tests/test_webhooks_coverage_gap.py tests/test_webhooks_main.py tests/test_orphan_refund.py tests/test_stripe_reconcile.py tests/test_refund_ledger.py tests/test_dispute_ledger_replay_safety.py -q`, then `pytest -m unit -q`.
**Mandatory before merge:** one read-only live round-trip — `get_rows("rides", {"id": <a refunded ride>, "refund_amount": <its read-back value>})` returns exactly that row — to prove the CAS filter matches the stored representation (no migration asserts the column type; `163_*:159` casts `::text::numeric`). If it does not round-trip, use `{"refund_amount": {"$lte": prev}}` plus the `None` form instead.
Rollback: revert PR-3; live-data effect is only additional, correct ledger rows with deterministic ids (no mutation of existing rows, no schema change); the old handler tolerates either id shape.
Blast radius (readers): `webhooks.py:52` `_SETTLED_PAYMENT_STATUSES`, `:271`, `routes/admin/rides.py:1905`, `:2800-2801`; ledger projection `services/ledger_service.py:199` derives legs from `metadata.tax_reversed` — recovery rows carry the same metadata because they go through `record_refund_event`. Behaviour change to disclose: a replayed pre-C3 legacy refund with no ledger rows would now be booked (more correct, but new).
Reviewers: `spinr-money-auditor` (mandatory) + `spinr-edge-case-reviewer`. Change Impact Log required with the concrete before/after scenario above: `docs/change-log/2026-09-08-charge-refunded-cas-ledger-dedupe.md`; residual (single-statement RPC like `288_settle_ride_card_payment.sql`) → WS-6/WS-9, tracked as C77.

## PR-4 — F8: Stripe retrieve calls off the event loop

| Field | Detail |
|---|---|
| Files | `backend/routes/webhooks.py`, `backend/tests/test_webhooks_coverage_gap.py` |
| Change | `:305` → `payment_intent_id = await asyncio.to_thread(_extract_invoice_payment_intent, invoice, stripe_secret)` (helper stays sync; `asyncio` already imported — see `:2207`). `:1652` → `_sub_obj = await asyncio.to_thread(_stripe.Subscription.retrieve, stripe_sub_id, api_key=stripe_secret)`. Comment both with the reason (event-loop stall; "Stripe webhook < 500 ms" SLA). Do **not** convert the helper to async: 6 sync tests call it directly (`test_webhooks_helpers_coverage.py:67-116`, `test_webhooks_coverage_gap.py:483-511`). |
| Tests | (1) anyio test of `_handle_ride_invoice_paid` with a Basil-shaped invoice lacking a PI and `stripe.Invoice.retrieve` patched (a module-attribute patch still intercepts under `to_thread`) → PI resolves and the ride update proceeds; (2) the `:1644-1660` subscription-recovery branch with `stripe.Subscription.retrieve` patched (first coverage of that branch). |
| Verify | `pytest tests/test_webhooks_helpers_coverage.py tests/test_webhooks_coverage_gap.py tests/test_webhooks_main.py tests/test_routes_webhooks_coverage.py -q`; `grep -n "_stripe\.\w*\.retrieve(" backend/routes/webhooks.py` shows both sites inside `asyncio.to_thread(`. |
| Rollback / blast radius | Revert. `to_thread` uses the default executor (the same pool F10 tracks) — at most one short-lived thread per invoice.paid fallback. The 9 secondary bare sites (background loops/admin) go to C86, not here. |
| Reviewers / log | `spinr-money-auditor` (light) + `spinr-performance-sla-reviewer`. Change Impact Log required: `docs/change-log/2026-09-08-f8-stripe-retrieve-off-event-loop.md`. |

## PR-5 — F2: redact `SpinrException.details` without breaking the client contract

| Subtask | Files | Change | Tests |
|---|---|---|---|
| 5a handler | `backend/utils/error_handling.py`, `backend/tests/test_error_response_sanitisation.py` | In `spinr_exception_handler` after `content = exc.to_dict()` (`:644`): if `details` is a dict, replace it with a copy that drops `exception_type` and passes `original` (when a `str`) through `_redact_error_detail` (already imported `:19-21`). Everything else (`unpaid_ride_id`, `resource_type/resource_id`, `client_secret`, `next_action`, `code`) passes through verbatim — running the redactor over arbitrary keys would mangle the 3DS secret the client needs. All environments. Do not alter `message` here. Server-side logging at `:625-633` keeps the raw text. `error_response()` (`:925-940`) has no non-test callers — leave it. | (1) `DatabaseError(details={"original": "Key (phone)=(+13065551234) … x@y.com", "exception_type": "APIError"})` → no `exception_type`, `original` contains `[redacted]` and neither the phone nor the email, `request_id` present; (2) structured details preserved byte-for-byte; (3) 4xx and 5xx treated the same; (4) `details=None` unchanged. Precondition: `grep -rn exception_type shared rider-app driver-app admin-dashboard/src backend/tests` shows zero client readers before dropping it. |
| 5b raise sites | `backend/dependencies/__init__.py` (`:479`, `:533`), `backend/tests/test_dependencies_auth_gaps.py` | `raise DatabaseError(details={"original": redact_error_detail(str(e))}) from e` (dual-import from `utils.pii`). Defence in depth: the exception object also feeds the loguru→Sentry bridge, and CLAUDE.md forbids phone/email in Sentry events. | `get_user_by_id` raising `RuntimeError("boom user@example.com +13065551234")` → `details["original"]` contains `[redacted]`, not the email/phone; same for the driver lookup at `:525-533`. |

Verify: `pytest tests/test_error_response_sanitisation.py tests/test_create_ride_guard_clauses.py tests/test_dependencies_auth_gaps.py -q`. Rollback: revert (response-shape only). Blast radius: `error.details` readers `shared/api/client.ts:452-457, 649-651`, `tests/test_create_ride_guard_clauses.py:201`, `docs/runbooks/error-responses.md:80-85,199-203`; all ~18 `DatabaseError(details=…)` sites now emit a redacted `original` without per-site edits. Reviewer: `spinr-security-auditor`. Change Impact Log required (auth + client contract): `docs/change-log/2026-09-08-f2-spinr-exception-details-redaction.md`, including the `exception_type` grep result verbatim.

## PR-6 — hygiene: F5, F7, F12, F13 comment (one commit each)

| Subtask | Files | Change | Tests / verify |
|---|---|---|---|
| 6a F5 | `backend/utils/redis_client.py` (`:159`), `backend/tests/test_redis_client_coverage.py` | `parsed = urlparse(url); logger.info("Redis connected: %s://%s:%s", parsed.scheme, parsed.hostname or "?", parsed.port or "?")` (stdlib logger, so `%`-style is correct), mirroring `utils/redis_diag.py:85-97`. | Next to `test_get_redis_connects_and_caches_client` (`:104-119`): URL `rediss://default:SECRETTOKEN123@example.upstash.io:6379`, `caplog` at INFO → token absent, `example.upstash.io:6379` present. |
| 6b-i helper | `backend/utils/background.py`, `backend/tests/test_background_deadline_detach.py` | Add `log_task_exception(task)`: return on `task.cancelled()`; else log `task.exception()` at error with `exc_info` (stdlib module; add `logging.getLogger(__name__)`). | cancelled task → no log, no raise; failing task → one error record. |
| 6b-ii F7 sites | `backend/ai/tools.py` (`:308-312`), `backend/ai/threat.py` (`:127-130`), `backend/ai/tools_support.py` (`:182-183`) | Replace `asyncio.create_task(...)` + `add_done_callback(lambda t: t.exception())` with `task = spawn(...)`; `if task is not None: task.add_done_callback(log_task_exception)`. Keep the `except RuntimeError` guards (`spawn()` does not catch a no-loop error). Note `spawn()` clears the request deadline (`background.py:37-61`) — desirable for audit/security/embedding writes. Read `tests/test_ai_tools_support.py:300-335` first: it patches `tools_support.asyncio.create_task` with a double; confirm the double's return type. | Extend `tests/test_ai_tool_scoping.py:147-176` / `test_ai_threat.py`: a cancelled audit task does not raise inside the callback; a failing one logs. `pytest tests/test_ai_tool_scoping.py tests/test_ai_threat.py tests/test_ai_tools_support.py tests/test_background_deadline_detach.py -q`. |
| 6c F12 | `backend/dependencies/__init__.py` (`:316-332`), `backend/tests/test_dependencies_auth_gaps.py` | `_ACTIVITY_TOUCH_INTERVAL_S = 60` beside `_IDLE_SECONDS`; skip the `update_one` at `:328` when `last_active` parsed and is younger than the interval. NULL/malformed keeps writing (`:325-327` fallthrough). `routes/admin/auth.py:400` login reset untouched. Idle detection degrades by ≤60 s — state it. | `…skips_activity_write_when_fresh` (now−10 s → not awaited) and `…writes_when_stale_but_not_idle` (now−120 s → awaited once); keep `…malformed_last_activity_lets_through` asserting the write; `tests/test_admin_login_resets_idle_clock.py` unchanged. Change Impact Log required (auth): `docs/change-log/2026-09-08-f12-admin-last-activity-write-throttle.md`. |
| 6d F13 comment | `backend/fly.toml` (`:11-13`, `:20-21`) | "16"/"18 loops" → "41 loops — `_WATCHDOG_LOOP_NAMES` in `backend/core/lifespan.py:736` is the live registry; do not hard-code the count elsewhere". No per-worker gating (each loop holds its own Redis leader lock; WS-3 owns topology). | Comment-only; a push to `main` triggers `deploy-fly.yml`, so include in the `spinr-cicd-infra-reviewer` pass. |

Reviewers: `spinr-security-auditor` (6a, 6c), `spinr-admin-rbac-reviewer` (6c), `spinr-ai-guardrail-reviewer` (6b). Verify also `pytest tests/test_loguru_call_conventions.py -q` (background.py is stdlib; ai/*.py logger usage unchanged).

## PR-7 — docs

| Commit | Files | Change |
|---|---|---|
| 7a | `AGENTS.md` | Patch, don't gut (#5078 deliberately reverted the Codex-section removal): `:111`/`:474` Railway/Render → Fly.io primary, Railway warm standby (see CLAUDE.md Deployment); `:123`/`:258` "16" → "41 (registry `_WATCHDOG_LOOP_NAMES`)"; `:147`/`:335` `backend/routes/rides.py` → the `backend/routes/rides/` package; `:235` replace the hard-coded "101/102" with "run `ls backend/migrations \| sort -V \| tail -1`" (407 today); `:293` add the loguru rule + pointer to `tests/test_loguru_call_conventions.py`; `:31-45`/`:499` `.Codex/` → `.codex/`, drop `graphify-out/`. Add a two-line header: "Codex-facing view; `CLAUDE.md` is canonical — where they disagree, CLAUDE.md wins." `:233` (migrate.py deleted) is already correct. |
| 7b | the eight `docs/change-log/2026-09-05-*.md` files (`4xx-exception-text-redaction`, `dispute-ledger-replay-safety`, `health-liveness-readiness-split`, `noshow-card-fee-collection`, `offer-expiry-accept-race`, `pickup-otp-bruteforce-hardening`, `stop-edit-promo-discount`, `webhook-payment-failed-guard`) | One-line footnote under each "§N" citation: the round-3 review doc was never committed; the finding text is reproduced in §1 of the log. Three commits of ≤3 files. Recovering the original doc is a human action (below). |
| 7c | `ACTION_ITEMS.md` | New entries **C74** F4b roll-ups · **C75** nightly baseline · **C76** Maestro invalid workflow (cross-ref B25: its "wired but never fires" premise hid a second, in-repo cause) · **C77** F1 CAS/dedupe/recovery (+ residual RPC → WS-6/WS-9) · **C78** F2 · **C79** F5 · **C80** F7 · **C81** F12 · **C82** F6 worker label (scheduled with WS-3) · **C83** F13 comment drift · **C84** AGENTS.md drift · **C85** missing round-3 doc · **C86** F8 secondary sites · **C87** scanner-download retries. Under C43: "re-review by 2026-10-15 or at A41 close, whichever first". Under C73: "add `Security gates summary` and `guardrail-summary` to the required-checks list once C74 lands". Red main itself is AI18 (closed in PR-1), not a new ID. |

## Scheduled into existing workstreams (not in this tranche)

| Item | Where it lives | Note |
|---|---|---|
| F6 per-worker counter flapping | **WS-3** (worker tier), C82 | Add a `worker_pid` label in `utils/metrics.render_prometheus` (`:153-176`). Counters/histograms already use `sum()` in ADR-010 §3 and `metrics-agent/grafana/alert-rules.yaml`; **gauges** (`set_gauge`, e.g. `spinr_redis_used_memory_bytes`) must move to `max by()` first. Pin `UVICORN_WORKERS` in `railway.json:8` / `Dockerfile:110` (default 4 ≠ Fly's 2). |
| F8 secondary bare Stripe sites | WS-5/WS-9, C86 | `services/stripe_kyc_sync.py:471`, `utils/payment_retry.py:465/:491/:558`, `utils/reconciliation.py:303`, `utils/stripe_reconcile.py:157`, `services/stripe_payout_sync_service.py:234`, `services/stripe_mapping_import_service.py:967`, `routes/admin/dispute_evidence_submission.py:143`, `services/legacy_payout_correction_service.py:569`. |
| F10 bounded admission | WS-5/WS-9 (after staging A/B exists) | ~12-line `asyncio.Semaphore` gate before `run_in_executor` in `repositories/_base.py:442`, `app_settings`-flagged; do not ship blind. |
| F13 topology (loops in every worker) | WS-3 subtask 1 | `should_spawn_on_api()` exists (`core/background_loop_registry.py:91`) with zero call sites in `lifespan.py`. |
| F3 RLS on 4 tables | C43 (event-gated) | Add the re-review date only. |
| F4a/F4c release enforcement | A43/C73/C21, WS-4 | Human-only branch protection; deploy gating is WS-4. |
| F9, F11, maintainability, ACTION_ITEMS size, TEST-b | C50/H7, B6 (decided), WS-8, — | No action; strike F11 and TEST-b from the PR text. |

## PR #5079 disposition (recommendation)

**Request changes, then merge as docs** rather than close as duplicate: the document's value is the
record of what was checked, and three validation passes produced verdicts that otherwise live only
in one session's transcript. Required edits before merge: strike F11 and TEST-b; correct F1 (the monotonic
guard already prevents the "$30" case; the real defects are the missing CAS, ride-then-ledger
ordering and the absent `dedupe_key`); correct F13 (no per-worker gating needed; only the
`fly.toml` comment is stale); replace the F3/F4a/F4c/F9/F10/F13/maintainability restatements with
one-line cross-refs to C43 / A43+C73+C21 / C50 / WS-3 / WS-8; add the verified new items with
file:line (red main = AI18, F4b, nightly baseline, Maestro job-level `matrix`, F2 scoped to the
`SpinrException` handler, F5 at `redis_client.py:159`); fix the sentence claiming the seven test
failures "do not establish broken production features" (they establish one). Keep it in
`docs/audit/` with a pointer from `ACTION_ITEMS.md`. If the author declines, close it and land the
verdicts as `docs/audit/2026-09-07-pr-5079-validation-verdicts.md` in PR-7 instead.

**Decision (repo owner, 2026-09-07): post the change request.** Step 0 of execution is one review on
PR #5079 (`pull_request_review_write`, event "request changes") containing: the strike list
(F11, TEST-b), the two corrections (F1 mechanism, F13 mitigation), the cross-reference list
(C43, A43/C73/C21, C50, WS-3, WS-8), the verified new items with file:line (AI18 red main, F4b,
nightly baseline 376, Maestro job-level `matrix`, F2 scoped, F5 `redis_client.py:159`), and a
pointer to the ACTION_ITEMS IDs PR-7 will add. It ends with the mandatory Claude Code attribution
footer. No commits are pushed to the PR's branch (`docs/engineering-review-hardening-2026-09-07`);
all code goes to `claude/pr-5079-analysis-plan-55dus9`. Subscribe to PR #5079 activity after
posting so the author's revision is seen.

## Human / admin-only actions

| Action | Owner | Tracker | Unblocks |
|---|---|---|---|
| Configure `main` branch protection: require `backend-test`, `Security gates summary`, `guardrail-summary` (once PR-2 lands), `migration-check`; block merge on failed or incomplete checks; disable admin bypass | Repo admin | A43, C73, C21 | Ends the merged-while-red pattern that produced this red main |
| Provide `EXPO_TOKEN` / Maestro Cloud secrets | Org admin | B25 | Lets the now-valid Maestro workflow run on labeled PRs |
| Set the C43 re-review date (proposed 2026-10-15) or confirm A41-close as the trigger | Product + eng owner | C43 | Dated deferral |
| Confirm the F2 `details` contract: drop `exception_type`, redact `original`, pass the rest through; decide separately whether `message` should ever be redacted | Mobile + backend owner | C78 | PR-5 scope |
| Recover `docs/audit/2026-09-05-engineering-director-review-round3.md` from the author's machine or accept the footnote approach | Author of #5048 | C85 | PR-7 7b |
| `workflow_dispatch` the nightly sweep after PR-2 (agents have no Actions-dispatch access) | Anyone with Actions write | C75 | Immediate green instead of waiting for 09:17 UTC |

## Verification (end-to-end)

| Item | Command / observation | Expected |
|---|---|---|
| Deps | `cd backend && pip install -r requirements.txt` | Suite importable (not possible in this planning environment) |
| PR-1 | `pytest tests/test_ai_tools_support.py tests/test_ai_tool_scoping.py tests/test_ai_public_assistant.py tests/test_ai_mcp.py -q`; `ci.yml` on the merge commit | 0 failed; `backend-test` green on `main` for the first time since `adf4549` |
| PR-1 in production | Send "what do I need to drive?" to the spinr.ca widget; check logs and `ai_security_events` | FAQ-grounded answer; no `ai tool blocked: no authenticated user id`; no web `tool_blocked` rows |
| PR-2 | `actionlint` clean; Actions list after merge; nightly run | No zero-duration `maestro-e2e.yml` run on the next push; nightly sweep prints "PASS … (66 known …)"; summary jobs fail on the next real gate failure and pass on a docs-only PR |
| PR-3 | Test list above; `spinr-money-auditor` verdict; the read-only live CAS round-trip | All green; live filter returns exactly one row |
| PR-4 | Test list above; grep for `retrieve(` sites | Both inside `asyncio.to_thread(` |
| PR-5 | Test list above; repo-wide `exception_type` grep | Green; zero client readers |
| PR-6 | Test list above; `pytest tests/test_loguru_call_conventions.py -q` | Green |
| Every PR | `pytest -m unit -q`; `ruff check .`; CI `backend-test` green on each merge commit | Green throughout |

## Risks

| Fix | Could break | Mitigation |
|---|---|---|
| AI18 allow-list | A future web-exposed tool reading `user["id"]` reaches its handler with none; admin AI console joins `ai_tool_audit.user_id` to `users` | Registry-invariant test; grep admin readers before merge |
| F1 CAS filter | If `rides.refund_amount`'s stored representation does not round-trip through `eq.`, every refund 500s and unclaims → Stripe retries forever | Mandatory live round-trip; `$lte` fallback |
| F1 recovery branch | Replayed pre-C3 legacy refund gets booked | Disclose; `spinr-money-auditor` confirms |
| F1 unclaim + 500 | More 5xx to Stripe under genuine concurrency | Same pattern as `:880-891`; error log with `event_id` |
| F8 `to_thread` | Executor saturation under an `invoice.paid` burst | Watch `spinr_db_*` queue-wait metrics after deploy |
| F4b | A path-filtered `skipped` job treated as failure would block every PR | Expression tests only `failure`/`cancelled`; verify on a docs-only PR |
| Maestro `fromJSON` matrix | Bad JSON quoting fails the `plan` job | `actionlint`; a dispatch stopping at "Setup EAS" is the pass signal |
| F2 | Dropping a key a client reads | Only `exception_type` dropped after the grep; `original` redacted, never removed |
| F7 via `spawn()` | Request deadline cleared; `create_task` test doubles | Read `test_ai_tools_support.py:300-335` first; clearing is the intended semantics |
| F12 | Idle timeout fires up to 60 s late; NULL still writes every request | Tests pin both edges; disclosed |

## Decisions (repo owner, 2026-09-07)

- **Scope:** implement all seven PRs in the order above on `claude/pr-5079-analysis-plan-55dus9`. Money and auth PRs (PR-3, PR-4, PR-5, PR-6c) stop for the owner's sign-off before merge, per CLAUDE.md's "escalate, don't silently ship" gate.
- **PR #5079:** the validating session posts one change-request review (step 0 above); the author revises; it then merges as docs.

## Assumptions

- One PR per row above, each with its own Change Impact Log where required; nothing is pushed to PR #5079's branch.
- The F1 fix stays in the application layer (CAS + idempotent ledger); the single-statement RPC is deferred to WS-6/WS-9.
- The F2 change keeps `details` as a contract and only drops `exception_type` and redacts `original`.
- Items marked optional (2d scanner retries, F7) can be dropped without affecting the rest.
