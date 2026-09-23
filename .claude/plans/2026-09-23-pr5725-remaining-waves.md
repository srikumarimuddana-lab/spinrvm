# PR 5725 — remaining waves execution plan

User instruction: complete all waves, committing each logical task to PR 5725. Baseline: `c367e31c6`. Use GPT-6 Luna for delegated implementation/review.

## Completion contract

The revised sequence in `docs/superpowers/plans/2026-09-22-director-plans-assessment.md` is authoritative. Code, passing local tests, enabled infrastructure and real operational proof are different states. Do every available task; do not mark elapsed observation periods, physical device journeys, or unavailable vendor drills complete from simulated tests.

Each code task uses a reproducing test, a minimal fix, focused verification, independent actual-diff review and its own commit. Target at most three files and roughly 200 changed lines per commit; split helpers, integration and runbooks where necessary. Preserve Decimal math, database claims, Stripe idempotency, dual imports, existing insurance logic and production process defaults.

## Preflight decisions and shared interfaces

| Producer → consumer | Contract / ruling |
|---|---|
| Staging snapshot → recovery workflow | Only verified staging app/image digest/prior served SHA plus matching checked-in config; no raw machine env or secrets. Missing baseline denies promotion. Production auto-recovery waits for actual staging drill. |
| Worker startup → API production validator | Reuse existing validation without adding worker HTTP middleware or changing API startup. Worker fleet selection remains opt-in. |
| Worker registry → fleet preflight | Explicit API/worker process ownership and independent health/metrics evidence; existing all-role fleet remains valid. |
| Impact validator → CI guardrails | Validate substantive evidence for changed surfaces; a heading or unrelated log alone is insufficient. |
| Staging origin guard → DAST/load entrypoints | Positive exact target validation, known production project denied, redirects must not widen the target. No execution against production. |
| Migration runner → future release automation | Read-only audit must not bootstrap schema; concurrent applies require database-wide serialization on a stable session. Automation remains disabled until provenance/compatibility/container proof. |
| Durable WS publish → reconnect replay | Reproduce ordering/race gaps against the existing sequence protocol; fix only demonstrated failures. |

Alternative rejected: enable workers, migration auto-apply and rollback directly in production to call the roadmap complete. Chosen: implement and verify prerequisites, then retain the actual activation/drill gate; the repository documents live rider/driver testing and missing staging infrastructure.

## Wave 0 — release evidence

- [x] Read-only Supabase project discovery: only the confirmed production project is exposed.
- [x] Verify marker signature and admin RPC ACLs against live catalogs; query only nonsecret feature flags.
- [ ] Commit reproducible evidence including migration provenance discrepancies and current provider selection.
- [ ] Retrieve available GitHub test artifacts/check results; distinguish missing proof from failed proof.
- [ ] Establish one conservative release rubric: 14-day rollout gate, 30-day correctness/alert observation, and the original representative-quarter A-grade requirement are cumulative, not interchangeable. No grade awarded by code completion.

## Wave 1 — recoverable promotion

- [ ] R1: staging image/config/served-SHA snapshot helper + rejection/redaction tests + impact record.
- [ ] R2: staging workflow captures before mutation, stamps candidate SHA, requires readiness/SHA, restores verified baseline on failed candidate, preserves original failure and uploads evidence. Test wiring and failure states.
- [ ] R3: operator baseline/forced-failure drill runbook. Production automatic rollback remains gated on an actual successful staging restore.

## Wave 2 — ownership and state

- [ ] W1: worker production configuration validation with actual lifespan tests.
- [ ] W2: coherent worker-aware fleet preflight and explicit opt-in configuration; preserve default API/all fleet.
- [ ] W3: worker selection, cancellation/death, recovery and overlap coverage; verify existing claims rather than assume a TTL lease gives whole-tick exclusivity.
- [ ] S1: reproduce and fix any durable WebSocket ordering/reconnect gap using the existing protocol.
- [ ] S2: add missing real-Postgres financial/state concurrency proofs using shipped SQL. Do not count skips as passes or execute fixture writes in production.
- [ ] M1: migration status/dry-run performs no schema writes; tests cover missing tracking table.
- [ ] M2: serialize migration apply runs before classification/bootstrap; test contention, failure and independent commit behavior. Keep cross-provider automation off pending complete provenance and stable connection proof.
- [ ] M3: verify container migration entrypoint/import and document old-code/schema compatibility requirements; wire only isolated CI proof where infrastructure permits.

## Wave 3 — assurance and measured optimization

- [ ] A1: substantive impact-record validator and CI wiring, rejecting placeholders/unrelated records.
- [ ] A2: DAST target/report/severity checks fail closed; preserve evidence and existing staging-only triggers.
- [ ] A3: Maestro Android/iOS retain execution artifacts and validate actual completed-flow evidence where vendor semantics permit; simulator evidence is not physical-device proof.
- [ ] A4: enforce exact staging origin and nonproduction database identity at load-test preparation and execution boundaries; test URL confusion, known production and missing configuration.
- [ ] A5: inventory actual alert metrics, unexpected-repair signals and delivery tests; record unconfigured external delivery honestly. No unsolicited external messages.
- [ ] A6: record current PostGIS deployment and measured-load prerequisites. Direct-claim/OSRM/GEO/arq/type-generation/route extraction proceed only for verified missing contracts or measured benefit; no speculative broad rewrites.
- [ ] A7: independent integrated review, regression and full wave evidence matrix, with external blockers named.

## Operational gates requiring unavailable evidence

The current environment has no Fly token, Stripe test credentials, local PostgreSQL/Redis binaries or test DSN. OS package setup is blocked by runtime setgroups permissions. The Supabase connector exposes production only; no synthetic staging project is available. Existing staging workflow/runbook describe provisioning as incomplete.

Real staging promotion/rollback, worker topology activation, native device journeys, two staged rides, representative load, alert delivery receipts, DNS failover and 14/30-day/quarter observations require those resources and elapsed time. Record precise remaining steps after all feasible code and verification work. Do not use live financial or customer data as test fixtures.
