# Pillar 6 — Operations & Deployment

> How Spinr ships, observes, and survives failure. The operating premise:
> a two-person-scale team can run a regulated, money-moving product safely
> if rollback is one DNS change, every risky action has a runbook, and
> production health is a numbers table rather than a feeling.

## Deployment topology

- **Backend**: Fly.io (`yyz`, Toronto) primary + Railway (Canada) warm
  standby, both deploying from `main` (`deploy-fly.yml`,
  `deploy-backend.yml`); fail-over and fail-back are a single Cloudflare
  DNS change on `api-spinr.spinr.ca`, with Redis behind a `redis.spinr.ca`
  alias so it repoints the same way (ADR-006 → ADR-007,
  `docs/runbooks/railway-fly-failover.md`).
  **Current honest status**: Railway deploys are deliberately paused, so the
  standby is drifting from `main` (ACTION_ITEMS C5), and the fail-over drill
  has never been exercised (C1). Until both close, treat fail-over as a
  documented intention, not a tested capability.
- **Staging**: separate Fly app (`deploy-backend-staging.yml`,
  `docs/runbooks/staging-environment.md`).
- **Admin dashboard**: Vercel. **Mobile**: Expo EAS — OTA updates on push
  (`eas-build.yml`), native builds on demand; store lanes via
  `deploy-driver-play-testing.yml` and the EAS native workflow. `[build]`
  in the commit message is the mobile-build trigger.
- **Migrations**: `run_migrations.py` (ordered, filename-keyed, checksummed)
  against a direct Postgres connection; < 30 s prod window; `--dry-run` and
  `--status` first. High-risk applies get their own dated runbook (the
  house pattern: `deploy-migration-297.md`).

## Release discipline

1. Ship dark behind `app_settings` flags; verify staging/canary; flip on
   without a redeploy.
2. Additive over destructive when anyone could be mid-session.
3. Rollback plan written **before** merge, and it must work on live data —
   flag off, config revert, or rollback SQL; `git revert` is not a rollback
   for applied Stripe charges, wallet deltas, or ride state.
4. Money/state changes deploy with their reconciliation story: the Stripe
   reconcile loop, ledger alerts, and stuck-ride sweeper are the safety nets
   that catch what review missed.

## Observability

- **Logs**: module loggers, structured `extra={}`, level discipline
  (error = actionable, warning = recoverable, info = state transitions);
  never `print()`, never PII (Pillar 5's never-log list).
- **Metrics**: Prometheus snake_case `spinr_<domain>_<metric>_<unit>` —
  dispatch offer/accept counters and latency, fare-calc duration, payment
  settlement outcomes, WS fan-out duration, ride state-transition counters.
  Dashboards and alerts bind to these exact names (the dotted legacy
  spelling is dead). Cross-replica aggregation + alerting: ADR-010, served
  by the dedicated metrics-agent Fly app.
- **Sentry**: user-visible errors only, tagged `domain`/`surface`/`env` +
  bare IDs; degraded-but-recovered stays a warning + metric, never Sentry
  noise.
- **Audit tables**: every security-relevant event (auth failures, RLS
  denials, admin actions) — this is evidence, with `audit_logger.py` as the
  write path.
- **Targets**: the KPI table and P95 SLA table in `CLAUDE.md` are the
  operating contract; `docs/slo.md` and `/kpi` / `/status` read them back.
  A breach opens a stage-1 problem statement (Pillar 1), not a Slack shrug.

## Background jobs are production surface

The 18 startup loops (subscription expiry, surge engine, scheduled dispatch,
payment retry, document expiry, corporate auto-topup, allowance reset,
safety check-in, retention purge, reconciliation, Stripe reconcile, T4A,
earnings statements, stuck-ride sweeper, push retry, watchdog, …) run on
every replica concurrently. Operating rules: every loop is replay-safe
(atomic claim / idempotency flag), watchdog-covered, and observable; a new
loop follows the `spinr-background-loop` skill contract and gets
`spinr-realtime-reliability-reviewer` before merge.

## Incident response

- `/incident` walks P0/P1s; `docs/runbooks/` holds 50+ scenario runbooks —
  per-dependency outages (Supabase, Redis, API-down), domain emergencies
  (SOS incident, stripe-webhook failure, driver-not-receiving-rides),
  and recovery procedures (PITR restore, PII key rotation, OTP-lockout
  false positives).
- Suspected PII exposure is P0 with the 24 h / 72 h protocol (Pillar 5).
- Every incident produces a postmortem (`docs/templates/postmortem.md`) and
  its actions land in `ACTION_ITEMS.md` — the 2026-07-30 key-exposure
  write-up in `docs/incidents/` is the reference for tone and rigor.

## CI/CD estate — and its honesty ledger

30 workflows cover build/test (`ci.yml`, `pr-checks.yml`, `test-env.yml`),
security (`security-gates.yml`, `ci-guardrails.yml`, DAST scaffold),
migration safety (`migration-check.yml` incl. CHECK B collision detection),
mobile (bundle smoke, dep health, lockfile sync, Maestro E2E, EAS), deploys
(Fly ×2, Railway, metrics-agent, Play testing), and scheduled compliance
(sub-processor audit + monitor). The framework rule is not "CI exists" but
**"every inert gate is named"**: DAST no-ops without `STAGING_URL`; Claude
review is key-gated off (C7); Maestro never fires without secrets (B25);
`test-env.yml`'s `|| true` suppressors blind the error-audit chain
(CR-2026-008); admin visual regression self-skips with zero baselines
(B38); Railway deploy is paused (C5). A gate that silently passes is worse
than no gate — `docs/ci/gate-health-2026-08.md` is the standing ledger, and
gate decay files a `[CR]` issue rather than staying folklore.

## Operating cadence

- **Per PR**: gates of Pillar 1 stage 8; subscribed-PR events drive fixes
  to green (merge conflict → CI → review comments, in that order).
- **Daily/weekly**: `/status` health pass; `docs/audit/daily/` when in an
  audit cycle; backlog triage keeps ACTION_ITEMS bands truthful.
- **Quarterly**: sub-processor audit (workflow-scheduled), SGI reporting
  (`docs/compliance/`), dependency-upgrade runbook sweep.
- **The standing P2 debt is operational, not code** — 25 of the 57 open
  backlog items need a human with dashboard access (env sweeps, alert
  rules, MFA comms, drills). The framework treats these as launch work of
  equal rank with code: an unexercised fail-over or an unset alert rule is
  a production risk no test suite can see.
