# PR 5725 — remaining-wave implementation evidence

This record covers the continuation from `c367e31c6` after the user requested all waves. It supplements the earlier first-wave status; it does not turn local tests into deployment, device or elapsed-observation proof. Every implementation slice was reviewed and published to the existing PR.

## Wave matrix

| Wave | Repository result | Operational evidence still required |
|---|---|---|
| 0 — release evidence | Read-only catalog/ACL/provider checks, migration provenance discrepancies, historical GitHub run statuses and cumulative release rubric recorded | Resolve migration history, final-head CI, staging resources and accountable release owner |
| 1 — recoverable promotion | Verified digest/config/SHA baseline, mandatory staging probes, guarded restore, forced-failure drill input and operator runbook | Execute the distinct-SHA staging failure/restore drill; production auto-rollback remains gated |
| 2 — ownership and state | Shared worker startup validation and loop selection, eight-machine preflight, inactive private-worker canary, atomic WS publication, real-PG race test sources, read-only migration audit and serialized apply, image entrypoint smoke gate | PostgreSQL execution, image build smoke, provider config validation, actual worker parity/death/overlap and provider outcomes |
| 3 — assurance | Substantive impact gate, DAST target/context/report checks and required isolated-runner gate, Maestro completed-flow artifacts, load origin/database guards, payment alert sample floor and alert evidence inventory | Provision and prove scan-container egress isolation; actual staging scan/load, Maestro/device journeys, deployed alerts and receipt, measured performance and observation windows |

## Verification boundaries

- Focused integrated backend run: 730 passed, six existing warnings. It covers the first-wave money loops, production config, lifespan/worker ownership, incident regressions, WS publication/authentication, migration runner safeguards, seed isolation, and alert/capacity behavior.
- Real Redis 6.2.14 loopback DB15: durable WS integration 1 passed. An initial rerun failed because the disposable server had stopped; a run with an explicitly managed server passed and cleaned up. This proves ordered publication/payload/retention, not Stripe or financial provider outcomes.
- Six real-Postgres tests collect successfully; they have not executed here. A compatible bundled PostgreSQL wheel was found, but its supported root bootstrap required account creation rejected by the environment. No privilege workaround or production fixture write was attempted.
- Source migration import/discovery and CLI help pass. Docker is unavailable locally; the actual image smoke check is wired to CI.
- Alert delivery/threshold unit suites: 50 passed, one existing warning. Delivery uses mocks and does not prove receiver arrival.
- Final combined offline suite: **63 passed and 32 subtests passed**, covering deployment gates, recovery snapshots, fleet limits, impact validation, DAST configuration/report gates, payment alert structure, load origins, pre-auth and Locust/cache boundaries. The rule test checks expression structure and intended truth table, not promtool execution.

## Release restrictions

No merge, production deployment, migration application, worker activation, financial replay, external alert message or device test occurred. Active production process configuration retains its prior topology. The canary file is inactive and begins with API ownership retained. Existing claims, Decimal arithmetic, Stripe keys and insurance business logic are preserved.

The live preflight found PostGIS already selected globally and in six service areas, so no redundant provider switch was made. OSRM, GEO, arq, generated types and broad route extraction remain conditional on measured benefit or a demonstrated missing contract.

The plans require actual staging journeys and restore proof plus 14-day rollout, 30-day correctness/alert and representative-quarter evidence. Those windows are cumulative. No A-grade or go-live approval is claimed.

## DAST activation boundary

Source review proved ZAP context does not prevent the initial HTTP redirect from leaving that context. Scans therefore require hosted validation of `DAST_EGRESS_ORIGIN_ATTESTATION` matching the exact canonical staging origin, followed by an operator-provisioned `dast-egress-locked` runner. Its scan-container proxy/firewall must enforce hostname/port restrictions and prevent direct bypass, including shared-IP hosts. A runner label and attestation do not themselves enforce network isolation. That infrastructure was not provisioned here; actual scans remain blocked until it exists.

## Publication and review

GPT-6 Luna agents implemented and independently reviewed bounded slices; root reviewed actual diffs and integrated them. Final Ruff cleanup affects only tracked Python files changed by this PR: 36 files pass Ruff 0.15.12 lint and format checks. Unrelated untracked work remains untouched. Remaining reviewed commits are published in batches preserving individual logical commits, avoiding repeated branch updates and CI cancellation.

Final-head GitHub execution is a separate gate. The PR description records the latest queried status; pending or cancelled runs are never counted as passes. Live catalog evidence and historical baseline run IDs are in [live preflight](2026-09-23-pr5725-live-preflight.md).
