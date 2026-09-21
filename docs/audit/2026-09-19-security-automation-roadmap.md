# Security Automation Program — Roadmap

**Date:** 2026-09-19
**Requested by:** ittalenthire.ca@gmail.com
**Status:** Phase 1 in progress
**Context:** Spinr is nearing live operations. This program builds automated
report generation, log correlation/RCA, threat hunting, DAST execution,
decision-request generation, agent-action logging, adversarial multi-agent
review, and a go/no-go approval gate on top of what already exists
(SAST via `security-gates.yml` + `.semgrep/spinr-rules.yml`, `/full-audit`
parallel reviewer sweep, `CHANGE_IMPACT_LOG.md` template, cron-based
security monitoring workflows).

## Guardrails for this program (per CLAUDE.md + user access policy)

- Every subtask ≤ 3 files, tracked in TodoWrite, committed before the next starts.
- Anything touching `backend/migrations/`, background loops, or a live-tested
  surface gets a Change Impact Log entry + the relevant `spinr-*-reviewer`
  agent run before commit.
- No new infra (Fly apps, Supabase projects, GitHub secrets) is provisioned by
  an agent — those require a human with account access, scoped to this
  project only, never account-wide.
- Agent-action logging (Phase 2 below) must go live before any workstream is
  allowed to take an autonomous *write* action beyond opening a PR — until
  then, everything here is audit/report/draft-generation, never auto-merge,
  auto-deploy, or auto-remediate without a human go/no-go.

## Phase 0 — Blocked, needs a human (no ETA until unblocked)

| Item | Blocker | Exact unblock steps |
|---|---|---|
| DAST activation (ACTION_ITEMS E1/E6) | No staging env | 1) `fly apps create spinr-backend-staging` 2) Create Supabase project in `ca-central-1`, seed synthetic data only 3) Register `FLY_API_TOKEN_STAGING`, `SUPABASE_STAGING_URL`, `SUPABASE_STAGING_SERVICE_ROLE_KEY` as GitHub Actions secrets (repo Settings → Secrets and variables → Actions) 4) Push to `staging` branch or run `deploy-backend-staging.yml` via workflow_dispatch |
| Third-party pentest (E6) | Procurement, not code | Needs a vendor engagement — outside agent scope entirely |

I will keep `docs/runbooks/dast-and-pentest.md` and the ZAP workflow ready so
the scan runs correctly the first time staging exists — no code work needed
on my end until you complete the steps above.

## Phase 1 — Agent Action Log (foundation, starting now)

**Why first:** every other workstream (report generation, threat hunting,
go/no-go gate) needs an append-only record of what an AI agent did, when,
against what data, and with what outcome — otherwise none of it is
auditable or trustworthy as you approach live ops.

- Subtask 1.1 (≤3 files): migration `agent_action_log` table — append-only,
  `{agent_name, task_description, action_type, target_surface, files_touched,
  outcome, risk_domain, started_at, completed_at, session_ref}`. RLS:
  service-role write, admin-role read-only.
- Subtask 1.2 (≤3 files): `backend/utils/agent_action_logger.py` — thin
  wrapper (`log_agent_action()`), used from CI job that runs `/full-audit`
  and future scan jobs. No behavior change to existing runtime paths.
- Subtask 1.3 (≤2 files): admin-dashboard read-only view (or reuse existing
  admin audit-log page pattern) to browse the log.

## Phase 2 — Automated Report Generation

- Wraps existing SAST (`security-gates.yml`) + `/full-audit` output into a
  scheduled job that fills `CHANGE_IMPACT_LOG.md`-shaped Markdown
  automatically and commits it to `docs/change-log/` — replacing hand-writing
  the ~60 `docs/audit/*.md` files that exist today.
- Depends on Phase 1 (report generation is itself a logged agent action).

## Phase 3 — Log Correlation / RCA

- Pulls Sentry issues (blocked today — Sentry MCP connector needs
  authorization, see below) + backend structured logs + Prometheus metrics
  around an incident window, produces a candidate root-cause narrative for
  human review. Highest build effort, most novel — no existing scaffolding.
- **Blocked partially:** Sentry MCP server is unauthenticated in this
  session. You'll need to authorize it via claude.ai connector settings
  before this phase can pull real Sentry data.

## Phase 4 — Threat Hunting / Zero-Day Detection

- Scoped now per your request, but sequenced after Phase 3 — hunting logic
  needs correlated logs to be useful rather than raw noise.
- Two sub-capabilities: (a) CVE/dependency-advisory watcher (scheduled job
  diffing `pip-audit`/`npm audit` against a suppressions file, already
  partially covered by existing `yarn audit`/`npm audit` CI steps per
  CLAUDE.md's "don't chase pre-existing audit failures" note — this
  formalizes triage instead of ignoring), (b) behavioral anomaly flags
  (e.g. GPS-ping implausibility already exists via `spinr-fraud-auditor`;
  extending that pattern to auth/payment anomalies is new work).

## Phase 5 — Go/No-Go Approval Gate

- A required GitHub status check that reads the Change Impact Log's
  "Sign-off" section + the Agent Action Log for a PR and blocks merge until
  a human owner has explicitly approved — modeled on the existing Claude
  Approvals check pattern already documented in this repo's PR rules.
- Decision-request notifications to owners (plain-language + technical
  detail, alternatives considered, recommendation + reasoning) render from
  the same Change Impact Log entry — no new template needed, just an
  automated Slack/email dispatch of it. Needs you to confirm delivery
  channel (Slack webhook? email? GitHub PR comment only?) before wiring.

## Open questions to resolve before Phase 3+

1. Authorize the Sentry MCP connector (claude.ai connector settings) —
   needed for real log correlation.
2. Decision-request delivery channel for Phase 5 (Slack/email/PR-comment).
3. Confirm scope: is threat hunting meant to run against production traffic
   live, or against captured logs/replays only? (Running anything live
   against production is a higher-risk category and needs explicit sign-off
   per the CLAUDE.md safety-first bias.)
