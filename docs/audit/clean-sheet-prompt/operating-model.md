# Clean-Sheet Audit — Operating Model (Phase 6)

Companion to `docs/audit/SPINR_CLEAN_SHEET_REBUILD_AUDIT_PROMPT.md`. Covers how Spinr
**stays** good after the audit: audit cadence, issue/PR/CR lifecycle, incident loop,
agent loops for new features, and project-scoped access guardrails. The Operating-Model
Designer (R20) checks each item against what exists today and writes the gaps into
`docs/audit/clean-sheet/06-operating-model.md`.

Reuse existing commands first — `.claude/commands/`: `/spinr-feature`, `/plan`,
`/review`, `/full-audit`, `/fleet-check`, `/ops-triage`, `/sentry-triage`,
`/incident`, `/impact-log`, `/pr`, `/spinr-swarm`, `/tooling-check`.

---

## 1. Audit cadence

| Cadence | What runs | Owner | Output |
|---|---|---|---|
| Daily | `/ops-triage` (Railway, Sentry, vendor status) · `/sentry-triage` daily scope (payments/auth/dispatch/safety) | On-call | `docs/audit/daily/` note; Sentry-found fixes go through the normal Change Impact Log gate |
| Weekly | `/sentry-triage` full window · money reconciliation (ledger vs Stripe) · open CRITICAL/HIGH review in `ACTION_ITEMS.md` · CI gate health (anything red or `continue-on-error`) | Eng lead | Weekly health note: what changed since last week |
| Bi-weekly | One domain lane from the role council re-run on changed files only | Rotating | Updated `02-findings/<domain>.md` |
| Monthly | `/fleet-check` (unowned surfaces) · `/tooling-check` · connector-scope verification · access review of admin modules | Eng lead | Drift report |
| Quarterly | Full clean-sheet Phase 0 + Phase 2 · failover drill (C1) · PITR restore test · regulatory source re-check (§4 of sweep catalog) | Founder + eng | Refreshed report and roadmap |

**Rule:** a finding that recurs in two consecutive audits is escalated from "fix" to
"systemic root cause" and gets an architecture item in `04-blueprint.md`.

---

## 2. Issue / PR / CR lifecycle (to resolution, not to merge)

```
Detect (user report, Sentry, audit, agent, support ticket)
  → Triage (severity × blast × likelihood; owner; duplicate check vs ACTION_ITEMS.md)
  → Plan (/plan: ≤ 3 files per subtask, verify step per item)
  → Implement on a feature branch (flag if user-visible)
  → Review (mapped spinr-* agent(s) + /code-review; forks checked)
  → Change Impact Log (CLAUDE.md template — rollback plan before merge)
  → CI green (no new continue-on-error) → merge
  → Staged rollout (flag dark → staging → canary → on)
  → Verify in production (metric/Sentry/query named in the log)
  → Close with evidence; update KB/FAQ/runbook if behaviour changed
```

Target SLAs (to be confirmed by the founder): CRITICAL triaged < 1 h, fixed or mitigated
< 24 h; HIGH < 1 week; MEDIUM next sprint; LOW backlog with quarterly review.
R20 measures today's actual numbers from `ACTION_ITEMS.md` dates and reports the gap.

---

## 3. Incident loop

1. **Detect** — alert, Sentry spike, support surge, or SOS.
2. **Declare** — `/incident`, severity, incident commander, comms channel.
3. **Mitigate** — flag off / rollback / failover (runbook in `docs/runbooks/`).
4. **Communicate** — rider/driver in-app notice; regulator/OPC if a breach (72 h clock per CLAUDE.md breach protocol).
5. **Resolve & learn** — post-mortem in `docs/incidents/`, actions into `ACTION_ITEMS.md`, new check appended to `sweep-catalog.md`.
6. **Proactive triage** — weekly review of warnings-trending-up before they become incidents.

---

## 4. Agent loop for new features

```
Intake (/spinr-feature) → PRD check (docs/PRD.md scope) → /plan (subtasks + verify steps)
 → Adversarial pre-review (name one alternative; mapped spinr-* agent reads the plan)
 → Implement subtask → /review routes to spinr-* agents → fix → commit (≤ 200-line diff)
 → Change Impact Log → PR (draft) → CI → human approval → flag-dark deploy
 → Canary metrics check → flag on → KB/FAQ/training updated → close
```

Guardrails: agents never merge, never flip production flags, never mutate live data or
vendor state; a human approves every merge. Automated fix suggestions (Codex, Seer,
agents) go through the same gate as human code.

---

## 5. Project-scoped access guardrails

Policy source: `.claude/context/connector-scoping.md` (narrowest scope the vendor
supports, verified by calling the connector's own list tool — not assumed).

| Tool / CLI | Audit phase access | Scope unit | Guardrail |
|---|---|---|---|
| GitHub | Read (repo, PRs, issues, Actions) | `srikumarimuddana-lab/spinrvm` only | Branch protection on `main`; agents push only to their own feature branch |
| Supabase | Read-only SQL / advisors | Spinr project only | Never `apply_migration`/writes during audit; prefer a staging branch for any query that could be heavy |
| Stripe | Read-only (restricted key) | Spinr account only | No refunds, payouts, or config changes by agents |
| Sentry | Read (search issues/events) | Spinr org/project only | No resolve/assign/comment by agents |
| Vercel | Read (deployments, logs) | Spinr admin project only | Verify the team contains no unrelated projects before use |
| Railway | Read (status, logs, metrics) | `cooperative-harmony` / `spinrvm` | No redeploy/restart/variable changes |
| Fly.io | **Not used in audit** | — | Session token is deploy-capable; treat any reach as a finding (see `spinr-ops-triage-investigator` known gap) |
| Expo / EAS | Read (builds, crashes, reviews) | Spinr rider + driver apps only | Builds only via `[build]` commits by humans |
| Twilio, Firebase | Read (logs/usage) if connected | Spinr project only | No sends from agents |
| Semgrep / scanners | Run locally / CI | This repo only | Findings filed, not auto-fixed |

Every new connector: record its scope in `connector-scoping.md` **before** first use,
with the list-tool output that proves it.

---

## 6. What "complete" looks like (exit checklist for the whole programme)

- [ ] Every L6 code unit mapped to an L4 story; orphans resolved or documented.
- [ ] Every §3 edge case in the sweep catalog has a status and, if Unhandled, a finding.
- [ ] Every regulatory/tax item has a primary-source citation or is on the escalation list.
- [ ] Every KPI and SLA has a live metric and an alert.
- [ ] Every CRITICAL/HIGH finding has an owner, a flag/rollback plan, and a date.
- [ ] Every recurrence family has a systemic fix planned, not another point fix.
- [ ] KB/FAQ/training updated for every behaviour change shipped from the roadmap.
- [ ] Cadence in §1 is scheduled (not just documented) and the first cycle has run.
