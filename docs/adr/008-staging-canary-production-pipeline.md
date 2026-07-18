# ADR-008: Staging → canary → production promotion pipeline

**Date:** 2026-07-18
**Status:** Accepted
**Amends:** [ADR-007](007-fly-primary-railway-standby.md) (Fly primary with Railway warm standby, DNS cutover)

---

## Context

Since ADR-007, a push to `main` deploys **directly to production** on Fly and
Railway in parallel. There is no approval gate, no pre-production environment
that mirrors production, and no way to expose a new build to a fraction of real
traffic before it serves everyone. For a platform that moves money (Stripe),
sends SMS (Twilio), and carries safety-critical flows, that is an unacceptable
blast radius for a bad deploy.

Three properties of the current codebase make a naive "add a staging env" unsafe:

1. **`ENV` is binary.** Every guard checks `ENV == "production"`; any other
   value (including a hypothetical `staging`) silently skips the weak-secret
   checks, the PIPEDA `ca-` region enforcement, wildcard-CORS rejection, and
   cookie security flags.
2. **Live credentials live in the database.** Stripe secret/webhook keys,
   Twilio credentials, and the Google Maps server key are rows in the
   `app_settings` Supabase table — a second environment pointed at the
   production database inherits live payment keys.
3. **Background loops are unconditional.** The ~28 loops spawned in
   `core/lifespan.py` (payment retry, pre-auth capture, corporate auto-topup,
   retention purge, Stripe reconcile, …) run on every replica with no
   environment gating. A second environment sharing production data would
   double-charge, double-notify, and purge PII.

## Decision

Introduce a four-tier promotion pipeline:

```
develop ──► dev/test (Railway, existing)
main ─────► STAGING (auto)  ──approval──► CANARY (~5% prod traffic, bake) ──approval──► PRODUCTION (full)
```

### Environment model (code)

- `ENV` becomes a validated enum: `development | test | staging | production`.
  An unknown value refuses to boot.
- New `is_production_like` property (`staging` or `production`) replaces most
  `ENV == "production"` checks: **staging runs every security guard production
  does** (strong secrets, `ca-` Supabase region, HSTS, secure cookies, strict
  CORS, real rate limits, refusal to settle payments without configured Stripe).
- **Deliberate exception:** the dev simulate-ride endpoints
  (`routes/drivers/ride_flow.py`, `routes/rides/lifecycle.py`) stay gated on
  `is_production` only — staging needs them for automated ride-lifecycle smoke
  tests. Staging has test-mode Stripe keys, its own database, and no real
  riders, so the exposure is acceptable; its admin credentials are held to
  production strength by the guards above.
- Canary is **not a separate environment**: it runs `ENV=production` plus a new
  `DEPLOY_STAGE=canary` variable (default `stable`) used for the Sentry tag,
  logs, and the `/health` payload.
- New `BACKGROUND_LOOPS_ENABLED` flag (default `true`). The canary app sets it
  `false`: money-mutating loops never run two code versions concurrently, and
  new loop code takes effect only at full promotion.
- New `RELEASE_SHA`, injected at deploy time and surfaced in `/health`, so the
  pipeline can assert exactly which build is serving.

### Staging tier (full isolation)

- Fly app `spinr-backend-staging` (`yyz`, 1 machine), `api-staging.spinr.ca`.
- **Own Supabase project** (ca-central-1) with its own `app_settings` row
  holding Stripe **test-mode** keys, a staging webhook endpoint/secret, and
  Twilio test credentials. **Own Redis.** Nothing shared with production.
- Auto-deployed on every push to `main`: migrate staging DB → deploy → smoke
  test (`scripts/smoke/full_stack_smoke.py`) against the staging URL.
- Admin dashboard staging deployment at `admin-staging.spinr.ca` (Vercel);
  mobile EAS `preview` channel points `EXPO_PUBLIC_BACKEND_URL` at staging.

### Canary tier (shared production data plane, by design)

- Fly app `spinr-backend-canary` (`yyz`, 1 machine) with secrets byte-identical
  to production, `DEPLOY_STAGE=canary`, `BACKGROUND_LOOPS_ENABLED=false`.
- It **must** share production Supabase and Redis: the WebSocket pub/sub
  channel (`spinr:ws:dispatch`) is how a rider connected to a canary replica
  and a driver connected to a stable replica still see each other's events.
- Traffic split via **Cloudflare Load Balancer** on `api-spinr.spinr.ca`
  (this supersedes ADR-007's manual CNAME cutover):
  - Pool `stable` (Fly `spinr-backend-yyz`): weight ≈ 0.95
  - Pool `canary` (Fly `spinr-backend-canary`): weight ≈ 0.05
  - Pool `railway` (warm standby): **designated fallback pool**, receiving
    traffic only when the stable pool's `/health` monitor fails. Railway
    fail-over therefore becomes automatic; the manual runbook steps in
    `docs/runbooks/railway-fly-failover.md` become verification steps.
- Between releases the canary app runs the same SHA as stable, so the standing
  5% weight is safe.

### Promotion flow (CI)

- `deploy-staging.yml` — push to `main` (backend paths): migrate staging →
  deploy staging → smoke. No human gate.
- `promote-production.yml` — manual dispatch, two GitHub Environment approvals:
  1. **`production-canary`** (required reviewers): run production migrations
     (expand/contract — every migration must be compatible with the
     still-running stable code; contract steps ship one release later), then
     deploy the canary app and verify `/health` reports the promoted SHA.
  2. **Bake** for a configurable window, failing on canary error-rate
     regression (`/metrics` deltas) and summarizing the Sentry
     `deploy_stage:canary` view for the approver.
  3. **`production`** (required reviewers): full rollout — Fly stable deploy +
     Railway standby deploy, reusing the existing deploy steps.
- The existing push-triggered `deploy-fly.yml` / `deploy-backend.yml` prod
  deploys are retired (converted to `workflow_dispatch` break-glass) **only
  after** one successful end-to-end promotion rehearsal.

## Options considered

| Option | Rejected / chosen because |
|--------|---------------------------|
| **Cloudflare weighted LB canary** (chosen) | True percentage of real traffic with instant rollback (weight → 0); also automates the Railway fail-over that ADR-007 left manual. Small fixed monthly cost. |
| Fly blue-green only | Health-gated cutover but all-or-nothing — no bake period on partial real traffic. |
| Cohort/header-based canary in app code | Real code complexity in the request path; couples routing to app deploys. |
| Staging sharing prod DB with test flags | Rejected outright: `app_settings` live keys + ungated background loops make this a double-charge/PII-purge hazard. |

## Consequences

**Positive**
- Bad deploys hit a 1-machine canary at ~5% of traffic with loops off, not the
  whole fleet; rollback is a weight change or a redeploy of the previous SHA.
- Staging exercises the full guard surface (secrets, region, CORS, cookies)
  before production does, ending the "works in dev, fails prod fail-fast" class
  of deploy failure.
- Railway fail-over becomes automatic (LB fallback pool) instead of a paged
  human doing DNS surgery.

**Negative / trade-offs**
- New standing costs: staging Supabase project + Redis + 1 Fly machine, canary
  Fly machine, Cloudflare LB subscription.
- Canary secret parity with production is procedural (Fly secrets are not
  readable, so there is no copy command); drift causes token rejections on 5%
  of requests. Rotations must cover stable + canary + Railway in one window.
- 5% of current traffic is a weak statistical signal; bake verdicts lean on the
  staging smoke suite, Sentry, and error counters rather than volume. The
  promote workflow takes a weight/bake-minutes input so this can be tuned.
- Two human approvals add latency to every release; break-glass direct deploy
  remains available via `workflow_dispatch`.

Operational procedures: `docs/runbooks/canary-deploy.md` (promote/bake/rollback)
and `docs/runbooks/staging-canary-provisioning.md` (one-time infra setup).
