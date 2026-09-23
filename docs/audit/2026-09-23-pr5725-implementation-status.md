# PR 5725 implementation status — 2026-09-23

This is the historical first-wave record. The subsequent all-wave coding and current verification are recorded in [remaining-wave status](2026-09-23-pr5725-remaining-waves-status.md).

The verified first reliability wave is implemented. This is source/test evidence, not approval for production go-live or an A-grade claim. No deployment, merge, live migration, worker activation, or financial replay was performed.

## Planning decision

Read and reconciled `.claude/plans/2026-09-22-path-to-a.md`, `docs/audit/2026-09-22-consolidated-go-live-remediation.md`, and `docs/superpowers/plans/2026-09-22-director-plans-assessment.md` against the actual source. The director assessment takes precedence where the older roadmap proposes duplicate or premature work. The executable sequence is [the PR 5725 implementation plan](../superpowers/plans/2026-09-23-pr5725-implementation.md).

Starting PR head: `101d3fdf2a5b498433124c0b496a028b92b18b60` (three planning documents only). The old PR description referred to unpublished implementation and was not treated as proof.

## Published implementation

| Task | Result | Commit(s) |
|---|---|---|
| Plan | Verified first wave and external release gates | `aac659e65` |
| 1 | Strict Redis SET NX EX; no process-local fallback | `6f47b0d22` |
| 2 | Two-second Redis connect/command timeouts, no retries; sanitized initialization errors | `70c9341b7` |
| 3 | Payment retry skips all tick work without strict acquisition | `b47849b26` |
| 4 | Preauthorization capture fails closed; existing lease retained | `9b988136b` |
| 5 | Orphaned-hold reconciliation fails closed | `5c012497f` |
| 6 | Automatic payout and hourly stale transfer retries require acquisition | `c278a50d0`, `33af6f80e` |
| 7 | Corporate top-up gate; 510-second TTL below minimum 540-second sleep | `8ab886b0c` |
| 8 | Production validates actual runtime REDIS_URL without credential disclosure | `9f23bd078` |
| 9 | Exact-SHA/repository/main-push CI and executed security jobs; mandatory production probes | `68dd18c3c`, `d08e11002`, `0cb2b37ca`, `e030c0dd1` |
| 10 | Existing loop catalog completed; spawning and watchdog share actual role selection | `93267d209`, `3259ce4cf` |
| 11 | Fixed actionable statement-period 422 response; redacted diagnostics | `d8be275b6` |

Database claims, Decimal calculations, Stripe idempotency, existing kill switches, and default process role `all` remain. A TTL lease gates tick starts; a tick can outlast it, so it is not proof of full-tick exclusivity. Other Redis consumers retain their prior contracts. Other money loops require separate assessment; this is not a claim that every financial background loop now fails closed.

The production Fly workflow runs on every main push, with no workflow_dispatch bypass. A docs-only main advance therefore starts a replacement gated deployment instead of merely invalidating the previous SHA. The signed-image workflow remains manual. Exact-SHA checks do not prove the remote-built Fly image is byte-identical to a scanned artifact.

## Review outcomes and implementation adjustments

- GPT-6 Luna agents performed scoped implementation and independent money/CI/worker review; the orchestrator inspected diffs and integrated each logical commit.
- Final review caught stale payout transfer retries outside the Sunday Redis gate. Follow-up `33af6f80e` closes that gap, with outage/recovery plus weekday/Sunday contention tests. The database-only stale-batch finalizer remains outside the gate.
- Existing coverage files held the old fail-open assertions for payment retry and orphaned holds; those files were updated instead of the initially proposed test filenames.
- Gate code, workflow integration, and loop catalog changes were split into logical commits. The coherent lifespan spawning/watchdog change exceeded the approximate 200-line target to keep its runtime tests and impact record together; each runtime commit retained at most three files.
- The worker-role registry helper can still describe candidate API loops for role `worker`; actual lifespan spawning intersects with the empty active set, and no API watchdog starts. A helper-contract cleanup and a separate unset-environment default-role test are deferred, not production activation evidence.

## Existing incident fixes verified in source

| Incident | Source/test evidence already present | Still needs operational proof |
|---|---|---|
| TEXT marker RPC mismatch | Migration 447 installs the TEXT-ID signature; native Postgres regression file exists | Applied migration/signature/ACL checks and execution against real PostgreSQL |
| WebSocket marker write disconnects | Handler isolates database marker errors; resilience tests pass | Device location stream and zero handler disconnects/42883 errors |
| Stripe reconciliation shape | `stripe_get` handling and genuine PaymentIntent-shaped tests present | Controlled provider/account reconciliation outcomes |
| Admin SECURITY DEFINER exposure | Migration 450 restricts 15 `admin_*` functions plus the lost-and-found helper (16 total); authenticated retains the helper for RLS | Live function ACL/advisor check; migration 448 is not the corrective migration |
| Dispatch no-offer/re-offer incidents | Decline/expiry return to searching and reoffer; XL/Economy behavior is intentional; dispatch tests pass | Request-time evidence for four reported no-offer incidents and separate-account/device retest |
| Rider cancellation reason | Rider flow already sends the cancellation reason | Verify deployed mobile build and server payload |

These existing fixes were not duplicated. Source presence does not establish deployed versions, applied migrations, active flags, or successful user journeys.

## Verification

Final implementation code head: `33af6f80e6ec70bce96c3ade48ce63e97e1c54b8`.

- Combined backend regression: **539 passed**, six pre-existing warnings (Starlette deprecation and synchronous tests carrying asyncio marks).
- Deployment-gate suite: **14 passed**, covering missing/failed/skipped/foreign/stale evidence, polling, required probes, and workflow integration.
- `git diff --check` passed. Runtime import testing used the pinned backend dependencies plus the environment-specific `socksio` proxy dependency. No dependency manifest was changed.
- Remote GitHub checks are a separate gate; this record does not assert that pending CI checks have passed.

The backend regression includes Redis helper behavior, five financial loops and replay tests, configuration guard, statement errors, lifespan/watchdog/worker behavior, and the existing marker/Stripe/ACL/dispatch/webhook incident suites. It is a focused combined regression, not the full repository suite.

Reproduce from `backend` with the installed backend requirements:

```bash
python -m pytest -q --no-cov \
  tests/test_redis_client_coverage.py \
  tests/test_payment_retry.py tests/test_payment_retry_coverage.py \
  tests/test_replay_safety_payment_loops.py \
  tests/test_preauth_capture.py tests/test_preauth_capture_coverage.py \
  tests/test_orphaned_hold_reconciler.py tests/test_orphaned_hold_reconciler_coverage.py \
  tests/test_orphaned_hold_reconciler_loop_coverage.py \
  tests/test_auto_payout.py tests/test_corporate_autotopup.py \
  tests/test_middleware_production_config_guard.py tests/test_driver_statement_email.py \
  tests/test_lifespan_watchdog_coverage.py tests/test_core_lifespan_coverage.py tests/test_worker_app.py \
  tests/test_live_marker_write_resilience.py tests/test_stripe_reconcile.py \
  tests/test_admin_secdef_fn_revokes.py tests/test_dispatch_cascade.py tests/test_webhooks_main.py
```

From repository root: `python -m unittest discover -s scripts -p 'test_deploy_gate.py'`.

## Open release gates and subsequent work

1. Run native PostgreSQL TEXT-ID marker and transactional acceptance/cancellation/wallet/webhook tests; verify live migrations and ACLs. No PostgreSQL service was available locally.
2. Exercise real Redis loss/recovery/latency and overlapping or long-running financial ticks in staging. Preserve claims/idempotency; monitor lock-unavailable counters and financial backlogs. No real Redis or Stripe proof was run.
3. Verify required GitHub checks, existing Fly probe secrets, served SHA, immutable previous image/config capture, and a forced staging rollback. Local workflow tests are not live deployment proof.
4. Capture controlled separate-account rider/driver/admin/device evidence: timely first and second offers, decline/expiry/cancel behavior, marker stability, payment outcomes and reconciliation health.
5. Before worker activation, add shared production-config validation, fleet/process/metrics preflight, a single-loop canary, worker-death and overlapping-run evidence. No topology change was activated here.
6. Finish meaningful impact/DAST/Maestro artifacts, staging load-origin allowlists, alert delivery and traffic evidence. Benchmark before PostGIS/direct-claim/OSRM changes; defer GEO/arq/mass packaging/cache/WS protocol projects without measured need.
7. Retain the 14/30-day observation requirements and settle the quarterly-versus-30-day rubric discrepancy before any A-grade claim; include failover and onboarding proof.

Rollback must be coordinated: restoring a compatible previous image/config can restore old fail-open behavior. Prefer restoring Redis health or pausing the affected feature while investigating. Never reverse financial data as part of a deployment rollback.
