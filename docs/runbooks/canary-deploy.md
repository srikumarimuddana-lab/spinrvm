# Runbook: Canary deploy & production promotion

Companion to [ADR-008](../adr/008-staging-canary-production-pipeline.md).
One-time infra setup lives in [staging-canary-provisioning.md](staging-canary-provisioning.md).

## Topology recap

```
push to main ──► deploy-staging.yml (auto): migrate staging DB → deploy
                 spinr-backend-staging → smoke api-staging.spinr.ca

promote-production.yml (manual dispatch, input: sha + bake_minutes)
  ├─ approval #1 (GitHub Environment `production-canary`)
  │    ├─ migrate PRODUCTION DB (expand/contract-safe)
  │    ├─ deploy spinr-backend-canary  (~5% of api-spinr.spinr.ca via
  │    │   Cloudflare LB weighted pool; loops disabled)
  │    └─ bake: health watch + /metrics snapshots → job summary
  └─ approval #2 (GitHub Environment `production`)
       └─ deploy spinr-backend-yyz (stable, ~95%) + Railway standby
```

## Promoting a release

1. Confirm `deploy-staging` is green for the commit (Actions → Deploy Backend
   to Staging). Staging smoke skips OTP; if the release touches auth/OTP,
   verify manually against staging with a `REVIEW_LOGIN_ACCOUNTS` login.
2. Actions → **Promote to Production** → Run workflow. `sha` = the full commit
   SHA that passed staging; `bake_minutes` = 20 unless the release touches
   payments/dispatch (use 60).
3. **Approval #1** (`production-canary` reviewers): approving runs prod
   migrations and puts the new build on ~5% of real traffic.
4. During the bake, watch:
   - Sentry: `environment:production deploy_stage:canary` — any NEW issue
     type is a stop signal.
   - The bake job log (health ticks) and, after it finishes, the job summary
     (metrics at bake start/end — compare `spinr_payment_settlement_total`
     outcomes and error counters between snapshots).
   - `flyctl logs -a spinr-backend-canary` for anything loud.
   - Verify the loops-disabled line is present in canary boot logs:
     `Background loops disabled (ENV=production, DEPLOY_STAGE=canary)`.
5. **Approval #2** (`production` reviewers): only after reading the bake
   summary. This rolls the stable Fly app (rolling, health-gated) and the
   Railway standby.
6. After full rollout the canary keeps serving ~5% — it now runs the same SHA
   as stable, which is the steady state between releases.
7. If the release includes mobile JS changes that need production phones:
   Actions → EAS Mobile Update → dispatch with profile=production (never
   before approval #2).

## Rolling back

| Situation | Action |
|---|---|
| Bake looks bad, approval #2 not given | Don't approve. Re-run promote with the previous SHA to reset the canary (skips migrations already applied), or set the canary pool weight to 0 in Cloudflare for instant removal. |
| Bad release fully promoted | Re-run promote-production with the last good SHA (both approvals). For a fire: `flyctl releases -a spinr-backend-yyz` + `flyctl releases rollback` on stable, then reconcile canary/Railway to the same SHA. |
| Canary app itself is broken/looping | Cloudflare → Load Balancer → set canary pool weight to 0. Traffic is 100% stable within the LB update interval. Restore the weight after redeploying the canary. |
| Migration applied but code rolled back | Expand/contract means the old code must keep working against the new schema — that is the review bar for every migration (see migration rules in CLAUDE.md). If a migration violated it, treat as an incident (/incident); fix forward. |

## Failure modes & notes

- **Migrations run before the canary deploys** and while stable still serves
  100%: every migration must be forward-compatible with the running release.
  Contract/destructive steps ship one release later.
- **Canary shares prod Supabase + Redis by design** (WS fan-out must cross
  fleets). Its one behavioral difference is `BACKGROUND_LOOPS_ENABLED=false`.
  Never "fix" a canary by enabling loops on it.
- **Secret parity is procedural**: Fly secrets are write-only, so canary
  secrets are set from the canonical secret store in the same change window
  as stable + Railway. A JWT_SECRET mismatch shows up as ~5% of requests
  randomly 401ing — check secret parity before debugging auth code.
- **Long-lived WebSockets** on canary machines drop when the canary redeploys;
  clients auto-reconnect and may land on either fleet. Shared Redis pub/sub
  makes this seamless.
- **Railway failover**: the Railway service is the LB's designated fallback
  pool (zero steady-state traffic). The manual CNAME procedure in
  [railway-fly-failover.md](railway-fly-failover.md) remains the break-glass
  path if the LB itself must be bypassed.
- **Break-glass direct deploy**: deploy-fly.yml / deploy-backend.yml remain
  dispatchable manually to push stable/Railway without the pipeline. Use only
  during an incident, and re-run promote-production afterwards so the canary
  isn't left behind.
