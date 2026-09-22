# /ops-triage — Live Operations Health Check

Answer "is anything wrong in production right now" by checking Railway (the deployed `spinrvm` service's health/metrics/recent deployments), Sentry (production error signal), and public vendor status pages (Stripe/Twilio/Google Cloud) in one pass, correlating across them the same way `/sentry-triage` correlates a single issue's timeline. This is the daily-ops counterpart to `/sentry-triage` — that command investigates a specific error; this one asks whether the system as a whole is healthy right now.

Born from the 2026-09-21 fleet-coverage audit's finding that no agent in this repo watches live production state — every other `spinr-*` agent fires only on a code diff. Same posture as every other agent in this repo: **investigate, never act.** No restart, scale, redeploy, or page happens from this command — a human decides what to do with the report.

## Usage

```
/ops-triage                  # full check: Railway + Sentry + vendor status, last 24h
/ops-triage --window 4h      # override the lookback window (useful right after a suspected incident)
```

## 1 · Confirm connector access before doing anything else

- Call `mcp__Sentry__find_organizations()` — if it errors or requires OAuth, say so plainly rather than silently skipping the Sentry section of the report.
- Railway tools don't have an equivalent "confirm auth" call; if `environment-status` errors, treat that as a blocker for the Railway section specifically, not a signal that Railway itself is unhealthy.

## 2 · Investigate — dispatch the investigator agent

Launch `spinr-ops-triage-investigator` (Agent tool) with the window from the invocation. Wait for its structured report (see that agent's own Output format). Do not re-derive its findings yourself.

## 3 · Act on what it finds

- **An active, correlated incident** (e.g., a Railway health issue + a climbing Sentry cluster + a matching vendor-status incident) → surface to the user immediately with the investigator's correlation, don't wait to fold it into a routine-feeling report.
- **A Sentry finding that needs real root-cause work** → don't investigate it yourself; hand it to `/sentry-triage issue <id>` instead (the ops-triage investigator explicitly doesn't duplicate that agent's job).
- **Nothing actionable** → note it plainly. This command does not currently produce a persisted report file (unlike `/sentry-triage`'s weekly report) — its output is a point-in-time check, not an audit-trail artifact. If a persisted ops-health log becomes useful later, that's a deliberate follow-up decision, not something to add silently here.

## Do NOT

- Do not restart, scale, redeploy, or change any Railway config — the investigator has no tool that can, and this command doesn't add one.
- Do not page anyone — no paging integration exists in this repo (see the investigator's own anti-patterns).
- Do not use this command's Sentry section as a substitute for `/sentry-triage`'s real root-cause investigation — it's a coarse signal only.
- Do not treat "Fly.io: NOT INVESTIGATED" in the investigator's report as a clean bill of health for the primary backend — it means exactly what it says: a deliberate policy choice not to use an already-technically-reachable capability, not a permission gap.
- Do not assume this command runs on a schedule — no Routine has been created for it yet; it currently only runs on explicit invocation.

## Known gap — Fly.io primary backend

The investigator agent explicitly does not investigate Fly.io (this repo's primary backend, per CLAUDE.md's Deployment section) — not because it lacks the reach (its `Bash` grant plus this session's ambient, proxy-injected Fly deploy-token make the primary backend technically reachable already), but as a deliberate policy choice, an accepted risk stated plainly rather than framed as a gap. Actually using that reach to investigate Fly's primary health is a separate decision for the user to make explicitly. Until that decision is made, this command's ops-health picture covers the Railway standby, Sentry, and vendor status only — not the primary backend directly (though a Railway/Fly parity issue would likely still surface via `standby-parity-monitor.yml`'s own separate, already-existing check, per CLAUDE.md's Deployment section).

## See also

`.claude/agents/spinr-ops-triage-investigator.md` for the investigation step's own detailed rules and output format. `.claude/commands/sentry-triage.md` for the sibling command this one is modeled after and explicitly defers real Sentry root-cause work to. `.claude/context/connector-scoping.md` for the Railway/Sentry connector-scoping history this command's access is built on.
