# September 22 director plans: assessment and revised sequence

Baseline: local HEAD `0b6689b61`. Three parallel agents reviewed the remediation plan, the A-grade plan, and independent CI/readiness evidence. This is a planning review, not a production-readiness certification.

## Verdict

Do not execute the two plans verbatim. They combine necessary reliability work, already implemented controls, technically incorrect changes, and optional architecture projects. Preserve the safety outcomes; remove duplicate work and measure bottlenecks before choosing infrastructure.

## Evidence that changes the plan

| Reference | Finding | Better implementation |
|---|---|---|
| P0 | Local HEAD merges PR #5714. Publishing/merging the reliability branch is already done. Native Postgres, device, staging and Stripe proof remain unverified by this review. | Retain missing release gates, not the stale publication task. |
| P1-1 | Both Fly workflows would become automatic. The signed-image workflow is explicitly experimental/manual (`.github/workflows/deploy-fly-signed-image.yml:11`). | One authoritative deploy path. Verify CI job success, exact tested SHA, artifact provenance, served SHA, and stale-run ordering. Keep experimental deployment manual. |
| P1-3 | Proposed `flyctl releases rollback` command is invalid under current Fly documentation. | Record and redeploy the prior immutable image with compatible config. Verify recovery. Database changes need separate recovery decisions. |
| P1-4 | Admin middleware decodes JWTs, but backend authorization remains enforced. | Harden the UI gate without claiming an API authorization bypass. Compare introspection/public-key verification against distributing the backend signing secret; validate role, purpose and expiry too. |
| P1-6/P2-2 | Strict helper calls fallback-capable `redis_set_nx`; no-client branch grants process-local ownership (`backend/utils/redis_client.py:220`). | Require real Redis for strict distributed locks. Test absent client, cold connection failure, failed SET, held lock and successful acquisition. Preserve database/Stripe idempotency. |
| P2-3/P2-4 | API watchdog still uses an unfiltered loop set (`backend/core/lifespan.py:832`). | Role-aware spawning AND monitoring. Separate static inventory from active runtime loops; prove every loop has an owner. Do not activate dormant H3 solely to make sets equal. |
| P3-2 | Direct-pool flag requires restart to open pool (`matching.py:1012`). Active-pool transport errors raise (`matching.py:1103`, `dispatch_pool.py:324`). | Preserve the documented failure contract. Stage restart and rollback proof; do not promise silent fallback after an uncertain claim. |
| P3-3 | Settings already have a 60-second cache and last-known-value handling (`backend/settings_loader.py:19`). | Measure read/invalidation gaps before changing it. Preserve kill switches; do not duplicate settings secrets into a Redis snapshot by default. |
| P3-5 | Fleet-wide WS limiting already exists (`backend/socket_manager.py:184`); ACTION_ITEMS B3/B4 location/limiter work is closed (`ACTION_ITEMS.md:5361`). | Verify existing behavior and outage handling. Further coalescing needs measured benefit and location/insurance review. |
| P4-2 | JSON sink already exists (`backend/server.py:644`). | Audit stdlib/worker coverage, correlation and PII redaction; fix actual gaps. |
| P5-4 | Active-rider unique index already exists (`backend/migrations/53_rides_one_active_per_rider.sql:14`). | Verify applied status/validity. Add only missing constraints. Partial unique indexes do not use CHECK-constraint NOT VALID/VALIDATE staging. |
| A2-3 | Per-client sequence/replay already exists (`backend/utils/ws_pubsub.py:181`). Redis publish order is not DB transition order. | Test existing duplicate/gap/reconnect handling in both apps. Add transactional ride versions only for a demonstrated remaining ordering gap. |
| A2-4/A3-3/A3-6 | Property tests, substantial RLS coverage and refresh-reuse tests already exist. | Extend missing behavior, preserving refresh retry grace and explicit-revocation distinctions. |
| A3-1/A3-5 | Blanket secret relocation conflicts with admin-managed rotation; Firebase App Check already exists in middleware. | Audit concrete exposure/rotation gaps and extend existing attestation enforcement. Device risk scoring is not Firebase verification. |
| A4 | Alert prose mixes p95 thresholds and error-budget burn; expected reconciliation is conflated with invariant repair. | Define actual good/total events, windows, volume and missing-data rules; test alert delivery. Separate unexpected repairs from normal recovery. |
| A5 | Offer-to-accept histogram includes human response time (`backend/routes/drivers/ride_flow.py:432`). | Separate matching/delivery latency from acceptance time. GEO cannot be assumed to remove a second from a driver's decision. |
| A5-2 | Shared GEO key cannot provide the proposed per-member TTL using EXPIRE. | Defer until profiling justifies GEO. Then design last-seen filtering, safe pruning/offline removal and authoritative claim checks. |
| A6 | Postgres/RLS CI and coverage floor jobs already exist (`ci.yml:246`, `ci-guardrails.yml:621`). | Extend existing jobs, require nonzero executed tests, and ratchet actual floors without weakening stronger ones. |
| A6 structural work | arq, mass dual-import removal, tracing and file-size limits are not inherent quality requirements. Docker currently uses flattened `server:app` imports. | Defer scheduler/packaging changes until justified. Test all entrypoints before removing compatibility. Respect ADR-014 unless explicitly superseded. |

## Missing completion gates

- Verify actual required repository checks (ACTION_ITEMS C21), not just YAML jobs. A deliberately failing PR must be unmergeable; include merge queues where used.
- Validate meaningful change-impact fields and all live-sensitive paths. Honor canonical policy allowing the record in the PR body or a file; a one-line markdown file is insufficient.
- Give ZAP a real task: configured staging target, nonempty report, defined severity gate and exceptions. Current workflow skips unset targets and uses `fail_action: false`.
- Fix Maestro job conditions as well as schedule triggers; require executed tests/artifacts. An iOS workflow already exists.
- Use exact staging-origin allowlists and staging credentials for load tests, not hostname substring rejection.
- Version the grade rubric explicitly: September 1 asked for a quarter of evidence and restored automated review; September 22 substitutes 30-day clocks and omits review restoration.
- Quiet repair counters need representative traffic, running reconcilers and healthy telemetry. Zero events alone proves nothing.
- Replace broken grep gates: the wc command includes its total line, and zero ImportError counts also catch legitimate optional imports. Measure behavior and effective configuration.

## Revised implementation sequence

Each implementation commit must be one logical change, at most three files and roughly 200 changed lines. Include a change-impact record for live behavior changes. Commit before starting the next task in that stream. Parallelize investigation/review; deploy only one money/state canary at a time.

### Wave 0: establish release truth

- [ ] Map each retained task to implemented, tested, enabled and operationally verified; record SHA, environment, date and artifact. Unknown remains unknown.
- [ ] Mark reliability merge complete. Retrieve or run missing native-Postgres, signed Android/device, two staged-ride and Stripe test-mode checks.
- [ ] Inspect applied migrations, live flags, worker topology and required checks read-only. Do not export credentials or PII into the report.
- [ ] Agree on metric definitions and one readiness rubric before assessing a grade.

### Wave 1: contain financial and release failures

| Commit-sized task | Files (maximum three) | Acceptance proof |
|---|---|---|
| Strict Redis primitive | `backend/utils/redis_client.py`, `backend/tests/test_redis_client_coverage.py`, impact record | No-client/connection/command failure cannot yield distributed ownership; success and contention remain distinct. |
| First consumer | `backend/utils/payment_retry.py`, `backend/tests/test_payment_retry.py`, impact record | No tick under lock unavailability; next healthy interval works; replay has one financial effect. |
| Other money consumers | One loop module, its existing test module, its impact record | Repeat for preauth capture, payout, orphaned holds and autotopup; test lease expiry/overlap as well as exceptions. |
| Redis boot validation | `backend/core/middleware.py`, `backend/tests/test_middleware_production_config_guard.py`, impact record | Invalid production Redis configuration cannot serve traffic. |
| Redis timeouts | Redis client, client tests, impact record | Bounded connection/operation wait with explicitly tested caller failure behavior. |
| Single Fly gate | `.github/workflows/deploy-fly.yml`, new `scripts/test_deploy_gate.py`, runbook | Failed/skipped/missing/foreign/stale CI cannot deploy; successful exact SHA can; manual bypass is explicit/audited. |
| Recoverable promotion | Production workflow, gate tests, runbook | Forced staging failure redeploys recorded image/config and verifies readiness/SHA without undoing live data. |
| Exception-detail cleanup | One route module, existing detail guard test, impact record | Safe fixed client text, correct status and useful redacted server diagnostics. |

Run applicable backend tests from `backend`, e.g. `python -m pytest tests/test_redis_client_coverage.py tests/test_payment_retry.py -q --no-cov`. Existing loop test files include `test_preauth_capture.py`, `test_auto_payout.py`, and `test_orphaned_hold_reconciler_loop_coverage.py`. New deploy tests are proposed, not already present. Review actual diff with the relevant security/money reviewer before committing.

### Wave 2: worker ownership and state correctness

- [ ] Align role-aware spawning/watchdog behavior before topology changes. Initial slice: lifespan, watchdog tests, impact record; correct registry separately if needed.
- [ ] Test worker behavior; stage fleet-preflight support before coherent worker deployment activation. Validate health/metrics/scale settings and replay safety before overlap.
- [ ] Move one loop at a time. Inject worker death, Redis outage, restart and overlapping execution; zero owners and unsafe duplicate owners both fail.
- [ ] Add migration release automation only after pending/never-apply audit, container import proof, cross-provider apply serialization and old-code compatibility. Plan long index operations separately.
- [ ] Extend existing transactional RPCs and real Postgres tests for missing accept/cancel, wallet, insurance and webhook races before introducing another DB layer. Keep Stripe HTTP outside transactions.
- [ ] Extend current WebSocket replay/resume tests before inventing a new sequence protocol.
- [ ] Instrument unexpected repairs and test alert delivery before starting a representative soak.

### Wave 3: assurance, then measured optimization

- [ ] Enforce required checks and meaningful risk records; repair no-op DAST/Maestro paths. Extend existing coverage and RLS tests only where missing.
- [ ] Establish representative staging load with explicit isolation, sample counts and separate backend/human metrics.
- [ ] Canary existing PostGIS/direct-claim capability after verifying actual area/global settings. Respect restart and failure semantics.
- [ ] Consider OSRM only if routing dominates measured quote latency; validate representative route/price parity and the quote-confirm contract. Ten trips are a smoke check, not production accuracy evidence.
- [ ] Adopt generated API types incrementally and extract high-change route responsibilities; ratchet existing lint/coverage gates.
- [ ] Defer GEO, arq, blanket secret relocation, mass packaging changes and mandatory tracing until each has a demonstrated need.
- [ ] Perform failover/rollback drills and regrade only against the explicitly agreed evidence window and representative traffic.

## Platform references and verification boundary

Current primary references: [Fly rollback](https://fly.io/docs/blueprints/rollback-guide/), [process-targeted deployment](https://fly.io/docs/blueprints/custom-deploy-workflows/), [Redis GEOADD](https://redis.io/docs/latest/commands/geoadd/), [Redis EXPIRE](https://redis.io/docs/latest/commands/expire/). Different app/worker deployment strategies require explicit targeting and overlap validation, not only a global strategy stanza.

Read-only source/workflow/migration/history review plus primary platform documentation. No product code or original plan changed. No tests, build, device run, live configuration query, migration or deployment was performed. Existing test reports were not treated as newly reproduced proof. Backend dependency availability was not assumed.
