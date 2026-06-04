# ADR-007: Fly.io primary with Railway as warm standby (DNS cutover)

**Date:** 2026-06-04
**Status:** Accepted
**Amends:** [ADR-006](006-railway-deployment.md) (Railway as the primary hosting platform)

---

## Context

ADR-006 put the FastAPI backend on Railway with **Render** named as a fallback.
That fallback was never wired up — no Render config is live, no deploy runs there,
and a Railway outage would take the entire API down with no tested recovery path.

We need a backup backend that is actually hot, in sync, and one step away from
serving production traffic. We also want a clean path to making **Fly.io the
primary**, since Fly now has a Canadian region (`yyz`, Toronto) — the gap that
disqualified it at ADR-006 time.

Requirements:
- A second backend that auto-deploys from `main`, so the backup never drifts.
- Cutover and fail-back that require no code change and no provider-dashboard edits.
- Canadian data residency preserved (`SUPABASE_REGION=ca-central-1`, PIPEDA).
- Shared ephemeral state (OTP lockouts, rate limits, WebSocket pub/sub, loop
  leader locks) across both providers, or those features degrade to per-machine.

Options considered for routing/failover:

| Option | Rejected / chosen because |
|--------|---------------------------|
| Cloudflare **Load Balancer** (active-active, health-based pools) | More moving parts (monitors, pools, session affinity) to validate before launch; not needed for a "Fly primary, Railway just-in-case" posture. |
| **Cloudflare CNAME cutover** (chosen) | One public hostname `api-spinr.spinr.ca` as a CNAME to the active backend; fail-over / fail-back is a single DNS change. Lowest complexity, no LB to operate. |
| Keep Render fallback | Never implemented; no Canadian region; cold-start on free tier. |

---

## Decision

Run the backend on **Fly.io (`spinr-backend-yyz`, region `yyz`) as the intended
primary** and keep **Railway as a warm standby**, both auto-deploying from `main`.

Key implementation details:
- **Parallel CI deploys.** `.github/workflows/deploy-fly.yml` deploys to Fly on
  every push to `main` (in parallel with the existing `deploy-backend.yml` for
  Railway). Both run the same `backend/Dockerfile`, so the two providers stay in
  lockstep. Fly uses a rolling strategy (`max_unavailable=1`); new machines must
  pass `/health` before old ones are replaced.
- **CNAME routing, no load balancer.** `api-spinr.spinr.ca` is a Cloudflare CNAME
  to the active backend. Cut over to Fly-primary by pointing the CNAME at the Fly
  app; fail back to Railway by pointing it back. Mobile and admin builds only
  ever use `https://api-spinr.spinr.ca`, never a provider domain.
- **Shared Redis behind a DNS alias.** Redis is provisioned on Fly and exposed via
  a stable alias (e.g. `redis.spinr.ca`). `REDIS_URL`, `RATE_LIMIT_REDIS_URL`, and
  `WS_REDIS_URL` point at the alias on **both** providers, so the Redis backend
  can be swapped by repointing the alias — no redeploy needed.
- **Secret parity.** All secrets (`JWT_SECRET`, `SUPABASE_*`, `ADMIN_PASSWORD`,
  `FIREBASE_*`, Redis URLs, `ALLOWED_ORIGINS`) are byte-identical across Railway
  and Fly, set via `fly secrets` and Railway env vars respectively.
- **Capacity.** Fly runs `UVICORN_WORKERS=2` on `shared-cpu-1x`/`1gb`, 2 warm
  machines (`auto_stop_machines=false`, `min_machines_running=2`). Worker count is
  a capacity knob only — the 16 background loops are replay-safe per process.

The operational procedure (Fly app + secrets, Redis alias, CNAME cutover,
fail-back drill) lives in `docs/runbooks/railway-fly-failover.md`.

---

## Consequences

**Positive:**
- A real, hot backup: Fly is deployed and `/health`-green at all times, not a
  paper fallback. Cutover is a single DNS change with seconds-to-minutes TTL.
- Path to Fly-primary is the same mechanism as fail-back — symmetric and tested by
  the fail-back drill.
- No load balancer to operate; one public hostname for all clients.

**Negative / trade-offs:**
- **Redis failure-domain coupling.** If Redis lives *only* in Fly and the Fly
  region is the thing that failed, Redis is down too — making a fail-back to
  Railway hollow (OTP lockouts, rate limits, WS pub/sub, and loop leader locks all
  degrade). Mitigation: keep a second Redis reachable from Railway and repoint the
  `redis.spinr.ca` alias on fail-back. This must be provisioned, not assumed.
- **Dual hot deploys run the loops twice over.** Railway and Fly both run the 16
  background loops concurrently while both are live. This is safe *only* because
  both share the same Supabase and the same aliased Redis (leader locks). If the
  shared Redis alias is misconfigured per provider, leader-locked loops
  (reconciliation, retention purge) could double-fire. Re-confirm the alias is
  live on both before both take traffic.
- **Secret drift = random logouts.** A `JWT_SECRET` mismatch means tokens minted
  by one provider are rejected by the other. Rotations must be applied to both in
  the same change window.
- **Manual cutover latency.** Without a load balancer, fail-over is a human DNS
  action bounded by the CNAME TTL — not automatic. Accepted for now given the
  "Fly primary, Railway just-in-case" posture; a Cloudflare LB remains a future
  option if automatic health-based failover is needed.
