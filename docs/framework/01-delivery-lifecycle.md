# Pillar 1 — Delivery Lifecycle

> How a change moves from idea to production at Spinr. Every stage below is
> mapped to machinery that already exists in this repo; nothing here is
> aspirational tooling. Where a stage names a gate, the gate is mandatory
> while live app testing is active (see `CLAUDE.md` → Pre-merge release gates).

## The lifecycle at a glance

```
DISCUSS → BRAINSTORM → REQUIREMENTS → DESIGN → DEVELOP → VALIDATE → TEST → ALIGN → DEPLOY → OPERATE
   │           │             │           │         │          │        │       │        │        │
 issue     options doc    checkable   ADR/plan  subtasks   local     suite   review   staged   watch
 or ask    + tradeoffs    acceptance  + blast   ≤3 files   gates     + CI    agents   rollout  KPIs
                          criteria    radius    each                         + human
```

## Stage contracts

### 1. Discuss — every change starts as a stated problem
- Input: a user report, a KPI breach (`CLAUDE.md` → KPI Targets), an
  `ACTION_ITEMS.md` open item, or an audit finding (`docs/audit/`).
- Output: one sentence of *what is wrong today* — the same sentence that will
  later become the Change Impact Log's "Issue/gap identified" field.
- Rule: no code before the problem statement exists. A fix without a stated
  problem cannot be verified.

### 2. Brainstorm — name the readings, don't pick one silently
- If the request has more than one plausible reading, enumerate them and ask
  (`AskUserQuestion`), per the "surface assumptions" principle in `CLAUDE.md`.
- For anything architecturally significant, write the options down with
  tradeoffs — `docs/audit/2026-08-19-decision-writeups.md` is the house style:
  each option gets a cost, a risk, and a recommendation.

### 3. Requirements — turn vague asks into checkable ones
- Every requirement gets a verify step *before* implementation starts:
  `1. [change] → verify: [test/command/manual check]`.
- "Fix the bug" becomes "write a test that reproduces it, then make it pass."
- Requirements on regulated surfaces (rides, payments, auth, corporate,
  safety) inherit the domain contract docs: `.claude/context/domain-*.md`,
  `.claude/context/regulatory-sk.md`.

### 4. Design — decide, record, bound the blast radius
- Significant decisions become ADRs (`docs/adr/`, `/adr` skill). A decision
  that changed behavior but has no ADR is a future re-litigated argument.
- **Blast-radius check first** (release gate 1): before writing the fix, grep
  for every other caller/reader of what you're changing and state the radius,
  even when it's "isolated, no other callers."
- Tasks touching > 5 files go through `/plan` decomposition first.

### 5. Develop — small, surgical, sequenced
- Break work into subtasks of ≤ 3 files; track with the task list; never
  start subtask N+1 before N is committed.
- One logical change per commit; split diffs over ~200 lines.
- Match surrounding style; touch nothing the task doesn't require.
- Required scaffolding is not optional complexity: Decimal-only money math,
  the dual-import pattern, `_require_ride_in_state()` guards, insurance-period
  audit rows, query-filter escaping (`repositories/_base.py`).

### 6. Validate — prove it locally before CI sees it
- Run the repo's own fast checks: `ruff check` / `ruff format` (backend),
  `expo lint` + `jest` (rider/driver), `eslint` + `vitest` (admin),
  `next build` / `expo export` for a real production build when the change
  touches a frontend surface — a passing dev server or `tsc --noEmit` alone
  is not equivalent.
- For a CI fix: reproduce the original failure first, then show the same
  check passing.
- State-machine and money changes get a dry run against
  `mock_supabase_client` fixtures plus a concrete before/after scenario.

### 7. Test — the suite is the contract
- Coverage floors are per-domain, not global (see Pillar 4 —
  Quality Engineering): payments/fare/crypto ≥ 90%, rides/dispatch ≥ 80%,
  corporate ≥ 80%, admin/utilities ≥ 70%.
- Mandatory test additions: every new state transition, every fare-calc
  branch, every auth/RLS policy (allowed *and* denied), every Stripe webhook
  type.
- Frontend surfaces have **no active visual-regression coverage** (rider-app
  and driver-app none at all; admin-dashboard's Playwright job self-skips
  with zero baselines — `ACTION_ITEMS.md` B38). Say so explicitly in every
  UI change's verification notes rather than implying coverage.

### 8. Align — review is a fleet, then a human
- This repo ships 20+ specialized reviewer agents (`spinr-security-auditor`,
  `spinr-money-auditor`, `spinr-dispatch-reviewer`, `spinr-migration-reviewer`,
  `spinr-insurance-period-auditor`, …). Routing: `/review` picks reviewers by
  diff; `/full-audit` runs the whole fleet.
- **No automated PR review is currently running** (Codex silent since
  2026-07-30, Claude audit workflow off by design — `ACTION_ITEMS.md` C9/C7).
  Until restored, any PR touching money, auth, migrations, dispatch, or
  safety gets a *manual* pass with the relevant auditor agents before merge.
- Every behavior-changing PR carries a Change Impact & Risk entry
  (`docs/templates/CHANGE_IMPACT_LOG.md`) — including the two honesty fields
  competitors' processes usually lack: **"What was NOT verified"** and a
  **rollback plan that works on live data** (a `git revert` is not a rollback
  plan for applied Stripe charges or wallet deltas).
- Escalate, don't silently ship: unclear blast radius or unverifiable
  consumers on a regulated surface → `AskUserQuestion` before merge.

### 9. Deploy — dark first, DNS-fast rollback
- Backend deploys to Fly.io (primary, `yyz`) and Railway (warm standby) from
  `main`; fail-over is a single Cloudflare DNS change
  (`docs/runbooks/railway-fly-failover.md`, ADR-007). Check `ACTION_ITEMS.md`
  C5 before assuming standby is live — it is currently drifting.
- Feature-flag anything user-visible and non-trivial via the `app_settings`
  table (flag-without-redeploy). Ship dark → verify staging/canary → flip on.
- Additive over destructive: new column/flag over mutating one that a rider
  mid-ride or driver online might be observing.
- Migrations: `run_migrations.py` only, next free `NN_` prefix, < 30 s apply
  window, never rename an applied file (filename is the idempotency key).
- Mobile builds only on commit messages containing `[build]` (Expo EAS).

### 10. Operate — the loop closes back into stage 1
- Watch the KPI table (`/kpi`, `/status`) and P95 SLA table; a breach is a
  new stage-1 problem statement.
- Sentry events carry `domain`/`surface`/id tags only — never PII.
- Incidents: `/incident` runbook; suspected PII exposure is P0 with the
  24 h / 72 h breach protocol (`docs/runbooks/data-breach.md`).
- Findings that can't be fixed now become `ACTION_ITEMS.md` entries — the
  backlog is the memory of the operate stage, and its open items are where
  the next discuss stage picks up.

## Why this beats a big-tech release train

Uber-scale processes optimize for thousand-engineer coordination; Spinr's
lifecycle optimizes for **verified truth in a small, high-leverage team**:
every stage produces an artifact a later stage checks (problem statement →
acceptance criteria → blast radius → Change Impact Log → test evidence →
rollback plan), and the honesty fields ("What was NOT verified", "no visual
regression tooling exists") prevent the silent-confidence failure mode that
process-heavy orgs suffer. The gates are cheap enough to run on every PR and
strict exactly where the product is regulated: money, rides, insurance
periods, PII.
