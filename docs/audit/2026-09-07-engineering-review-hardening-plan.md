# Engineering review and prioritized hardening plan

Review date: 2026-09-07. Repository snapshot: `0b66ae53e936ae179d14f1504f5e188e71cc6dd2`.

This document records a risk-focused, read-only review of backend, rider, driver, admin, shared code, and delivery infrastructure. It proposes follow-up work; it implements no fixes. Findings distinguish source evidence, isolated reproductions, observed GitHub CI results, and production questions. Production databases, credentials, traffic, and deployed behavior were not inspected. This is not a certification of every file or a penetration test.

Priorities: **P0 verification** means resolve potentially serious exposure immediately; **P1** means address before broader production rollout; **P2** means planned hardening. Suggested owners below are roles, not assignments accepted by individuals.

## 🚨 Critical Issues & Security Flaws

### F1 — P1: Refund accounting is not atomic across concurrent events

**Evidence:** [refund webhook](https://github.com/srikumarimuddana-lab/spinrvm/blob/0b66ae53e936ae179d14f1504f5e188e71cc6dd2/backend/routes/webhooks.py#L1156), [refund ledger call](https://github.com/srikumarimuddana-lab/spinrvm/blob/0b66ae53e936ae179d14f1504f5e188e71cc6dd2/backend/services/payment_service.py#L298), and [ledger persistence](https://github.com/srikumarimuddana-lab/spinrvm/blob/0b66ae53e936ae179d14f1504f5e188e71cc6dd2/backend/services/ledger_service.py#L387).

The handler reads the previous cumulative refund, computes a delta, updates the ride by ID, and records accounting separately. Event-level deduplication does not serialize different events for the same payment. An isolated execution of the actual extracted handler branch with a mocked database reproduced concurrent cumulative refunds of $10 and $20 producing $30 of ledger entries and a final ride refund of $10. This demonstrates the interleaving, not a historical production incident.

The ledger service can return no record after retries; the caller does not make the ride update contingent on ledger success. A replay can then skip the missing accounting delta because the ride already reflects the cumulative refund.

**Plan:** Put cumulative refund advancement and ledger insertion in one database transaction with payment/ride row locking and a stable accounting identity. Enforce monotonic cumulative amounts, replay safety, and durable retry/reconciliation. Queue notifications after committed accounting. Inspect historical discrepancies before asserting customer impact.

**Acceptance:** Concurrent, reordered, duplicate, and partially failed deliveries always converge to provider totals; ledger failure cannot leave a successful ride-only accounting update. Stripe explicitly does not guarantee [webhook ordering](https://docs.stripe.com/webhooks).

### F2 — P1: Custom exceptions expose internal diagnostic details

**Evidence:** [exception serialization and handler](https://github.com/srikumarimuddana-lab/spinrvm/blob/0b66ae53e936ae179d14f1504f5e188e71cc6dd2/backend/utils/error_handling.py#L163), [repository error construction](https://github.com/srikumarimuddana-lab/spinrvm/blob/0b66ae53e936ae179d14f1504f5e188e71cc6dd2/backend/repositories/_base.py#L551), and [shared client](https://github.com/srikumarimuddana-lab/spinrvm/blob/0b66ae53e936ae179d14f1504f5e188e71cc6dd2/shared/api/client.ts#L519).

`SpinrException.to_dict()` includes `details`; its handler returns that payload. Repository errors populate diagnostic strings and exception types. Isolated execution of the actual serializer confirmed synthetic internal diagnostics survive into the response. Some database text is redacted, but technical details remain. The separate HTTPException sanitizer does not cover this custom path.

**Plan:** Separate private diagnostic context from a small allowlisted public error schema. Apply it consistently to every exception family and client fallback. Preserve explicitly approved payment-action fields needed for flows such as authentication challenges. Log private context with a correlation ID and redaction.

**Acceptance:** Contract tests across every handler prove raw errors, stack traces, SQL diagnostics, credentials, and exception class names never reach user responses.

### F3 — P0 verification: Documented deferred RLS requires live access verification

**Evidence:** [ACTION_ITEMS C43](https://github.com/srikumarimuddana-lab/spinrvm/blob/0b66ae53e936ae179d14f1504f5e188e71cc6dd2/ACTION_ITEMS.md#L19052) and [migration 379](https://github.com/srikumarimuddana-lab/spinrvm/blob/0b66ae53e936ae179d14f1504f5e188e71cc6dd2/backend/migrations/379_enable_rls_settings_document_files_driver_imports.sql).

The repository documents RLS disabled on four public tables: `settings`, `document_files`, `driver_csv_import`, and `driver_bank_import`. Its prepared migration was deferred pending legacy migration work. Current production RLS, grants, and API exposure were not verified; disabled RLS alone does not establish accessible data or exploitation.

**Plan:** Security/backend owners must verify the actual production project identity, migration ledger, table grants, exposed schemas, and anonymous/authenticated cross-user access. Stage and apply the appropriate policy/grant correction with compatibility checks. Assess incident scope and rotation only if exposure is established. Follow [Supabase RLS guidance](https://supabase.com/docs/guides/database/postgres/row-level-security).

**Acceptance:** Recorded role-by-role access tests prove sensitive data is inaccessible outside intended permissions, including relevant RPC paths.

### F4 — P1: Release enforcement permits merging before checks establish safety

**Evidence:** [PR #5048](https://github.com/srikumarimuddana-lab/spinrvm/pull/5048) merged at 02:58:46 UTC on September 6; its [backend job](https://github.com/srikumarimuddana-lab/spinrvm/actions/runs/34007800383/job/101418121353) ran until 03:14:59 and failed. Required PR fields had already failed before merge; subsequent reruns do not retroactively establish safe gating.

The [security summary](https://github.com/srikumarimuddana-lab/spinrvm/blob/0b66ae53e936ae179d14f1504f5e188e71cc6dd2/.github/workflows/security-gates.yml#L775) runs with `always()` and prints a summary without failing on dependency failures. [Fly deployment](https://github.com/srikumarimuddana-lab/spinrvm/blob/0b66ae53e936ae179d14f1504f5e188e71cc6dd2/.github/workflows/deploy-fly.yml) is independently triggered by main pushes. The branch endpoint observed during PR preparation reports main unprotected with required status checks off. Administration-only protection reads were unavailable; validate organization enforcement and bypass permissions with an administrator.

**Plan:** Make aggregate checks evaluate all required results, explicitly handling intentional skips. Require checks before merge and deployment, constrain bypasses, and promote the exact tested commit/artifact.

**Acceptance:** A controlled failing required check blocks both merge and release; summary status accurately reflects constituent results.

### F5 — P1: Redis connection logging can include credential material

**Evidence:** [Redis initialization](https://github.com/srikumarimuddana-lab/spinrvm/blob/0b66ae53e936ae179d14f1504f5e188e71cc6dd2/backend/utils/redis_client.py#L152) logs a URL prefix. Redis URLs can contain credentials before the hostname; truncation is not redaction. This path uses standard logging, so a Loguru filter is not sufficient proof of protection. No actual credentials or exposed logs were inspected.

**Plan:** Log only parsed, credential-free endpoint metadata. Test password-bearing URLs with synthetic values and assess retained log exposure securely.

**Acceptance:** Synthetic credential fragments never appear in any configured log sink.

## 🛡️ Error Handling & Telemetry (User experience vs. Admin logging)

Existing request IDs, Sentry integration, structured logging, PII scrubbing, and mobile error presentation provide useful foundations. F2 prevents treating the system as consistently safe today.

| Situation | User experience | Administrative evidence and recovery |
|---|---|---|
| Payment outcome unknown | Show pending confirmation; avoid prompting a second charge | Correlate provider identity, attempt, and reconciliation result |
| Receipt/notification unavailable | Preserve the successful trip and deliver later | Durable retry, bounded backoff, exhausted-job alert |
| Tracking disconnect | Show reconnecting and resynchronize current trip state | Connection lifecycle, last event sequence, resync outcome |
| Authorization failure | Clear approved message; protect the operation | Redacted denial reason and request ID |

**F6 — P1: Metrics do not aggregate across workers.** [Metrics](https://github.com/srikumarimuddana-lab/spinrvm/blob/0b66ae53e936ae179d14f1504f5e188e71cc6dd2/backend/utils/metrics.py) are process-local dictionaries; [Fly configuration](https://github.com/srikumarimuddana-lab/spinrvm/blob/0b66ae53e936ae179d14f1504f5e188e71cc6dd2/backend/fly.toml) runs two Uvicorn workers, while [agent discovery](https://github.com/srikumarimuddana-lab/spinrvm/blob/0b66ae53e936ae179d14f1504f5e188e71cc6dd2/metrics-agent/discover-targets.sh) scrapes machine endpoints. A scrape sees one worker, or apparent resets as workers change, undermining totals and alerting. Adopt correct [Prometheus multiprocess aggregation](https://prometheus.github.io/client_python/multiprocess/) or independently identified process exports with aggregation. Verify totals under two workers, restarts, and reconnects before trusting alerts.

**F7 — P2: Detached task cancellation creates noisy callback errors.** [AI tools](https://github.com/srikumarimuddana-lab/spinrvm/blob/0b66ae53e936ae179d14f1504f5e188e71cc6dd2/backend/ai/tools.py#L308) and [threat handling](https://github.com/srikumarimuddana-lab/spinrvm/blob/0b66ae53e936ae179d14f1504f5e188e71cc6dd2/backend/ai/threat.py#L127) call `t.exception()` in completion callbacks without first handling cancellation. CI logs showed cancellation exceptions. Supervise tasks, explicitly handle cancellation, and persist essential audit work rather than relying on process lifetime.

Use operation, request/trace ID, redacted entity identifiers, dependency, attempt, and outcome as structured fields. Keep high-cardinality IDs out of metric labels. Sample repetitive failures while retaining actionable exceptions and alerting on exhaustion.

## 🐢 Performance Bottlenecks & Optimizations

These are source-level bottlenecks and capacity risks, not measured production latency rankings.

| Finding and evidence | Root cause / impact | Improvement and validation |
|---|---|---|
| F8: [Invoice helper](https://github.com/srikumarimuddana-lab/spinrvm/blob/0b66ae53e936ae179d14f1504f5e188e71cc6dd2/backend/routes/webhooks.py#L190) calls synchronous Stripe retrieval from an async handler | Provider latency blocks the event loop | Supported async SDK path or bounded executor; measure event-loop lag and webhook latency under injected delay |
| F9: [Matching](https://github.com/srikumarimuddana-lab/spinrvm/blob/0b66ae53e936ae179d14f1504f5e188e71cc6dd2/backend/routes/rides/matching.py#L973) defaults direct-pool dispatch off and performs sequential claims | Candidate count multiplies database round trips | Benchmark existing gated direct-pool/batched paths; shadow and canary after race/fairness checks |
| F10: [Repository executor](https://github.com/srikumarimuddana-lab/spinrvm/blob/0b66ae53e936ae179d14f1504f5e188e71cc6dd2/backend/repositories/_base.py#L157) has 64 threads with an unbounded submission queue | Cancelling an awaiting timeout does not stop an already running synchronous call | Bounded admission, coordinated database/socket budgets, queue-wait metrics; test sustained overload and recovery |
| F11: [Estimates](https://github.com/srikumarimuddana-lab/spinrvm/blob/0b66ae53e936ae179d14f1504f5e188e71cc6dd2/backend/routes/rides/estimates.py#L43) allows roughly 3.5 seconds for routing | Tail latency can exceed a fast quote target | Cache/prefetch eligible routes and measure provider tails; preserve quote/confirmation fare basis rather than shortening timeouts blindly |
| F12: [Admin verification](https://github.com/srikumarimuddana-lab/spinrvm/blob/0b66ae53e936ae179d14f1504f5e188e71cc6dd2/backend/dependencies/__init__.py#L307) reads staff then writes last activity on each request | Authentication creates avoidable write load | Coalesce activity writes within a bounded idle-session policy; preserve prompt revocation |
| F13: [Lifespan](https://github.com/srikumarimuddana-lab/spinrvm/blob/0b66ae53e936ae179d14f1504f5e188e71cc6dd2/backend/core/lifespan.py#L278) starts many loops in each worker | API scaling multiplies polling even where locks/claims prevent duplicate effects | Separate worker capacity from API capacity; retain existing claims and measure database polling |

Make Redis connection and operation budgets explicit for each path; do not assume all current calls are unbounded. Follow [redis-py production guidance](https://redis.io/docs/latest/develop/clients/redis-py/produsage/). Distinguish throttled map-marker writes from durable location/insurance evidence: performance work must preserve required records.

## 💡 Tech Stack & Architecture Recommendations

Retain FastAPI/Python, PostgreSQL/Supabase, Redis, Expo/React Native, Next.js, and Stripe. The immediate constraint is correctness and operational enforcement, not the absence of a fashionable framework.

The comparisons below use public engineering publications as examples, including historical material. They do not claim access to Uber or Lyft's current private architecture or equivalent scale.

| Capability | Public market-leader reference | Spinr direction |
|---|---|---|
| Spatial dispatch | [Uber H3](https://www.uber.com/us/en/blog/h3/) | H3/PostGIS and shadow candidate paths already exist; validate freshness, coverage, assignment correctness, and rollout flags before expanding |
| Release confidence | [Lyft automated acceptance gates](https://eng.lyft.com/scaling-productivity-on-microservices-at-lyft-part-4-gating-deploys-with-automated-acceptance-4417e0ebc274) | Gate login, booking, acceptance, completion, and payment on the artifact being promoted |
| Operational visibility | [Uber observability](https://www.uber.com/us/en/blog/optimizing-observability/) | Fix worker metrics, then connect request-to-payment/dispatch traces with [OpenTelemetry](https://opentelemetry.io/docs/languages/python/instrumentation/) |
| Service boundaries | [Lyft service evolution](https://eng.lyft.com/scaling-productivity-on-microservices-at-lyft-part-1-a2f5d9a77813) | Keep a modular application; extract independently scaled workers only where ownership and measurements justify it |

**Database access:** Expand the existing gated Psycopg async-pool approach selectively rather than adding a competing driver. Size per-process pools against total fleet connection limits and use [pool lifecycle guidance](https://www.psycopg.org/psycopg3/docs/advanced/pool.html).

**Durable work:** A transactional outbox and receipt worker already exist. Verify deployment, flags, ownership, retries, and reconciliation before recommending another queue platform. Move money-adjacent side effects onto proven durable paths.

**Contracts:** Generate TypeScript contracts from OpenAPI and validate monetary/state-transition boundaries at runtime. Shared static types alone cannot validate network payloads.

**Deploy readiness:** Current Fly deployment performs optional post-deploy checks; a failed later workflow step does not itself roll back deployed machines. Add mandatory deploy-time readiness and exact-artifact verification using appropriate [Fly machine checks](https://fly.io/docs/reference/health-checks/). Preserve the deliberate distinction between runtime liveness and dependency readiness so a shared database outage does not unnecessarily unroute all machines.

## 🛠️ Maintainability & Code Smells

Large modules concentrate unrelated responsibilities: admin drivers has 4,334 lines, admin rides 4,171, webhooks 2,357, payment service 2,340, and rider ride-options 2,324 at the reviewed snapshot. Size alone is not a defect, but these boundaries increase review burden and make transactional responsibilities harder to follow.

**Plan:** Extract cohesive operations incrementally behind existing interfaces. Prioritize refund accounting and webhook orchestration, then dispatch and screen state. Require characterization tests around behavior before moving sensitive logic.

**Documentation drift:** AGENTS.md contains older SDK, migration, and deployment guidance than newer repository instructions. ACTION_ITEMS exceeds 21,000 lines and mixes historical and active work. Establish a canonical instruction source and an owned active backlog with evidence and exit criteria; preserve history separately.

**Migration coordination:** Both `376_corporate_wallet_adjust_idempotency.sql` and `376_service_area_tax_history.sql` exist. A [nightly run](https://github.com/srikumarimuddana-lab/spinrvm/actions/runs/34136403302) failed the uniqueness check. Filename-based bookkeeping means this does not automatically establish lost migrations or data loss. Check the combined merge set before merge; never casually rename already-applied migrations.

Treat comments, completed checklist entries, and prepared migrations as claims to verify against executable behavior and deployment state.

## 🧪 Testing & QA (Missing Edge Cases)

**Observed CI, not a new local full-suite run:** The reviewed commit's [CI run](https://github.com/srikumarimuddana-lab/spinrvm/actions/runs/34133427922) reported 14,001 backend passes, seven failures, six skips, one expected failure, and 46 warnings. The seven failures were in the AI public-web FAQ tests; they do not establish seven broken production features. Separate direct-pool tests reported 30 passes and RLS tests 61 passes. Rider, driver, admin, and Playwright jobs passed.

The [security run](https://github.com/srikumarimuddana-lab/spinrvm/actions/runs/34133427942) had failing mobile dependency audits, including high-severity browserslist/fast-uri findings. Resolve exact dependency paths and validate compatible updates. The admin-bundle secret scan failed while downloading its scanner after an HTTP 504; that is missing scan evidence, not proof of a leaked secret.

The [Maestro workflow](https://github.com/srikumarimuddana-lab/spinrvm/blob/0b66ae53e936ae179d14f1504f5e188e71cc6dd2/.github/workflows/maestro-e2e.yml#L65) references `matrix` in a job-level condition, where that context is unavailable under [GitHub's context rules](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts). Its observed run failed without jobs. Repair workflow evaluation, verify credentials, and demonstrate Android and iOS journeys; web export is not native verification.

Prioritize regression scenarios in this order:

1. Refund concurrency, reordering, duplicates, and failure between accounting writes.
2. Public error schemas for all exception families and synthetic credential redaction.
3. Two-driver acceptance racing cancellation; only valid final assignments survive.
4. Stripe success followed by a network timeout; retry cannot double-charge.
5. Process death, offline reconnect, token refresh, and background-location recovery.
6. Multiprocess metrics totals and alert delivery under restarts.
7. Failed required check and unhealthy candidate release block promotion.
8. Anonymous and cross-user denial tests for sensitive tables and RPCs.

Review-local validation used isolated source extraction with synthetic mocks for refund interleaving and exception serialization, plus inspection of process-local metrics. Full application dependencies were unavailable locally; no full backend, native, or production load suite was rerun. Follow-up implementation PRs must include durable regression tests.

## 📈 Manager's Verdict (Overall summary of code health)

Spinr has substantial product coverage, credible technology choices, and meaningful testing investment. Its highest risks are payment consistency, public diagnostic exposure, unresolved access-control verification, and delivery controls that do not reliably enforce the intended quality bar.

I would block broader production rollout until those risks have evidence-backed closure. Fund focused hardening before adding major feature scope; a wholesale rewrite would add delivery risk without resolving the immediate issues.

| Order | Suggested accountable roles | Work package | Exit criteria |
|---|---|---|---|
| 1 | Backend, payments, security | F1–F3 and F5: atomic refunds, safe exceptions/logging, production RLS verification | Reproductions pass as regression tests; access matrix verified; historical exposure/accounting scope assessed |
| 2 | Platform, QA | F4: enforce merge/release checks; repair backend failures, security scans, and native workflow | Failed required checks block release; actual promoted artifact has successful mandatory checks |
| 3 | Payments, mobile, QA | Failure/retry, assignment concurrency, reconciliation, Android/iOS recovery | Correct final money/trip state after retries, disconnects, and process termination |
| 4 | Platform | F6–F7: aggregate metrics, supervised tasks, tracing and tested alerts | Synthetic traffic totals are accurate across workers; actionable alerts demonstrated |
| 5 | Backend, platform | F8–F13: bounded capacity, measured dispatch/routing improvements, worker separation | Recorded baseline and improved tail latency/capacity without correctness regressions |
| 6 | Engineering lead, domain owners | Small module boundaries, canonical guidance, migration coordination, active backlog ownership | Each active risk has an owner, dependency, evidence link, and objective closure criteria |

Run access verification and release-control work concurrently where staffing permits. Estimate implementation only after owners confirm production constraints and dependencies. Each follow-up PR should address a bounded concern with rollback and validation evidence; this document does not close any finding.
