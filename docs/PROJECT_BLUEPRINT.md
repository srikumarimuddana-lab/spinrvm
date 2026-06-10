# Spinr — AI-Assisted Project Blueprint

_A reference for standing up any new project the way this repo is built. Every
section lists what exists here, why it earns its place, and what to adapt.
Treat this repo as the sample implementation; copy the structure, not the
domain specifics._

The stack has six layers. Each one catches a different class of mistake:

```
CLAUDE.md (constitution)        → wrong approach never starts
Skills (/commands)              → repeatable workflows stay consistent
Subagents (auditors)            → domain expertise reviews every risky diff
Hooks (session/tool/commit)     → mistakes caught at write/commit time
CI/CD (GitHub Actions)          → nothing unsafe reaches main or production
Living docs (backlog/sprint)    → context survives across sessions and people
```

---

## 1. CLAUDE.md — the constitution

One file at repo root that every AI session loads. What belongs in it (and what
this repo's version contains):

| Section | Purpose |
|---|---|
| Working style | Task decomposition rules (≤3 files/subtask, commit-per-subtask, ~200-line diffs), context discipline, PR-review handling |
| Context imports | `@`-references to backlog (`ACTION_ITEMS.md`), sprint state, and domain deep-dives — loaded on demand, not baked in |
| Project overview + commands | Surfaces table, how to run/test/lint each one |
| **Critical conventions** | The invariants that must never break (see §6 Guardrails) |
| DB/migration conventions | Numbering, append-only, RLS-first, rollback comments |
| Background-job recipe | Replay-safety contract with a code template |
| Observability conventions | Log levels, metric naming, what goes to Sentry vs logs |
| Testing conventions | Tiers, fixtures, patch targets, per-domain coverage floors |
| Performance SLAs | P95 targets per critical path + known anti-patterns |
| Regulatory/compliance | The legal context the product lives in (here: PIPEDA + SK Transportation Act) |
| **"What X Is NOT"** | Product guardrails — features that look like improvements but violate the product's identity |
| KPI targets | What production health means, numerically |

**Rule of thumb:** if an AI session got something wrong twice, the correction
belongs in CLAUDE.md. If it's sprint-scoped, it belongs in
`.claude/context/sprint-current.md` instead — keep the constitution stable.

## 2. Skills — `.claude/commands/*.md`

Slash-commands encoding repeatable workflows. Ten here; the first six are
generic to any project, the rest are domain-specific:

| Skill | What it encodes | Generic? |
|---|---|---|
| `/plan` | Decompose before coding (mandatory >5 files) | ✅ |
| `/start` | New feature branch with conventions applied | ✅ |
| `/commit` | Conventional commit with scope detection | ✅ |
| `/review` | Pre-commit review checklist against CLAUDE.md rules | ✅ |
| `/pr` | PR creation with template + checklist | ✅ |
| `/status` | Project health dashboard (git, tests, CI, **reads ACTION_ITEMS.md**) | ✅ |
| `/adr` | Architecture Decision Record scaffold into `docs/adr/` | ✅ |
| `/incident` | P0/P1 incident runbook walker | ✅ adapt |
| `/migration-check` | DB migration safety review (numbering, RLS, locks) | adapt to your DB |
| `/fare-audit` | Deep money-path audit | domain — replace with *your* money path |

**Pattern:** every skill ends with a defined output format so results are
comparable across sessions.

## 3. Subagents — `.claude/agents/*.md`

Specialist reviewers fired proactively on risky diffs. Three here:

| Agent | Trigger | Replace-with in a new project |
|---|---|---|
| `spinr-security-auditor` | Any auth/payments/RLS/admin/PII change | Keep nearly as-is — OWASP + privacy-law focus |
| `spinr-money-auditor` | Any fare/Stripe/wallet/refund change | Point at your money invariants (Decimal-only, idempotency, line-item transparency) |
| `spinr-migration-reviewer` | Any new `NN_*.sql` in a diff | Point at your migration conventions |

**Pattern:** one auditor per *irreversible-mistake domain* (money, security,
schema). Each agent file states triggers, the checklist it enforces, and the
severity ladder for findings. (Separate from these: `agents/` is a Python
multi-agent SDK for dev automation — not part of the Claude Code layer.)

## 4. Hooks — `.claude/settings.json` + `.claude/hooks/`

| Hook | When | What it does |
|---|---|---|
| `SessionStart` ×6 | New session | Installs the git pre-commit hook if missing; prints sprint banner from `sprint-current.md`; installs backend pip + 3× node_modules so tests run immediately |
| `PreToolUse` (Write\|Edit) | Before file write | `pre-migration-write.sh` — surfaces the migration checklist when touching `backend/migrations/` (warns, never blocks) |
| `PostToolUse` (Write\|Edit) | After file write | `post-python-write.sh` — auto-runs `ruff format` + `ruff check --fix` on backend Python (silent on success, never blocks) |
| `Stop` (user-level) | Session end | `stop-hook-git-check.sh` — nags if uncommitted changes remain |
| **git `pre-commit`** | Every commit | 5-step gate: secret scan → forbidden files → PII-in-logs scan → branch check (no direct main) → float-arithmetic-in-money-code block |

**Pattern:** session hooks bootstrap the environment; tool hooks enforce style
and surface checklists; the git hook is the hard gate. Advisory hooks exit 0;
only the commit gate blocks.

## 5. Permission guardrails — `settings.json`

- **Allow:** read everything; write to source trees, docs, tests, `.claude/`;
  git on `claude/*`/`feature/*`/`fix/*` branches; safe npm/pytest commands.
- **Deny:** `rm -rf`, `git push --force` (only `--force-with-lease` on feature
  branches), writes to `.env*`/`*.pem`/`*.key`, and **writes to the deploy
  workflows themselves** (`ci.yml`, `deploy-*.yml`, `eas-build.yml`) — the AI
  can propose pipeline changes only via files a human reviews.

## 6. Guardrails — the conventions that prevent entire bug classes

These live in CLAUDE.md and are enforced by hooks/CI/auditors. The categories
generalize to any project:

| Class | Spinr instance | Generalize to |
|---|---|---|
| Money | `Decimal` only, `_d()/_round()/_f()` helpers, pre-commit float block | Whatever moves money: integer cents or Decimal, never float; idempotency keys on every external charge |
| State machine | `_require_ride_in_state()` guards; illegal transitions surface loudly | Any entity lifecycle: central guard fn + a test per transition |
| Trust model | Admin JWT trusted; user role always re-read from DB | Decide explicitly which claims are trusted and write it down |
| Concurrency | Atomic claim (`UPDATE … WHERE status='searching'`), replay-safe loops | Every multi-replica job needs a claim flag / idempotency key / atomic claim |
| Privacy | No GPS/phone/name/email in logs, Sentry, analytics; geohash at most | Enumerate your never-log list; scan for it in pre-commit + CI |
| Error handling | Never warn-and-continue on DB/auth/payment errors; 503 so clients retry | Define which errors must surface vs degrade |
| Schema | Append-only migrations, RLS in same migration, rollback comment | Same for any migration system |
| Product identity | "What Spinr Is NOT" (no commission, surge cap, no hidden fees) | Write the NOT-list — it stops well-meaning scope drift |

## 7. CI/CD pipelines — `.github/workflows/` (20 here)

Grouped by what each prevents:

**Core quality (every PR)**
- `ci.yml` — backend pytest w/ coverage ratchet, rider/driver Jest, admin Vitest
- `pr-checks.yml` — lint, type-check, conventions
- `ci-guardrails.yml` — repo-specific guard rails summary (required check)
- `test-env.yml` — environment sanity

**Security (every PR + scheduled)**
- `security-gates.yml` — Bandit SAST, ESLint-security, Semgrep, dependency audit (required check)
- `ci-error-audit.yml` — audits CI failures into `reports/` so flakes get fixed, not retried

**Schema safety (PRs touching migrations)**
- `migration-check.yml` — naming/sequence, RLS-on-new-tables, rollback comment, prefix-uniqueness, cross-PR `CREATE OR REPLACE` collision scan (born from a real incident — see `docs/runbooks/migration-conflict-detection.md`)
- `apply-supabase-schema.yml` — ordered runner against the live DB (manual trigger)

**Deploy (push to main)**
- `deploy-fly.yml` + `deploy-backend.yml` — parallel primary + warm standby
- `bootstrap-fly.yml` — one-time infra setup
- *(pending: post-deploy smoke job — ACTION_ITEMS A2)*

**Mobile**
- `eas-build.yml` — Expo EAS, gated on `[build]` commit flag
- `mobile-dep-check.yml`, `fix-rider-lockfile.yml` — RN dependency hygiene

**Dependency & compliance cadence (scheduled)**
- `dependabot-auto-merge.yml`, `pip-compile-check.yml` — drift control
- `subprocessor-monitor.yml` — opens an issue when the vendor inventory goes 90+ days unreviewed (PIPEDA/SOC2 cadence)
- `subprocessor-audit.yml`

**AI-assisted review**
- `claude-review.yml`, `claude-audit.yml` — Claude reviews PRs / runs audits in CI

**Branch protection (GitHub settings, not a file):** PR + 1 review required;
guard-rail and security-gate summaries are required checks; force-push and
deletion blocked on `main`.

## 8. Living documents — context that survives sessions

| Document | Role |
|---|---|
| `ACTION_ITEMS.md` | Agent-consumable backlog: priority, files, approach, acceptance criteria, do-not-redo ledger |
| `docs/PRODUCTION_READINESS.md` | Consolidated audit context behind the backlog |
| `.claude/context/sprint-current.md` | Sprint goal, in-flight, recently-shipped (the SessionStart banner reads it) |
| `docs/runbooks/` (30) | One per failure mode: API down, Redis down, webhook failure, data breach, failover… |
| `docs/adr/` (9) | Why the architecture is the way it is |
| `reports/` | Append-only audit/remediation history (traceability) |

**Pattern:** when an incident or audit finding is fixed, the *lesson* lands in
a runbook or CLAUDE.md; the *task ledger* stays in ACTION_ITEMS/sprint files.

## 9. Bootstrap checklist for a new project

Ordered; each step is cheap when done at the start and expensive retrofitted:

1. **Write CLAUDE.md first** — even 50 lines: commands, conventions, the NOT-list.
2. **Git pre-commit gate** — secret scan + forbidden files + branch check; add
   domain scans (PII, float-money) as conventions appear.
3. **`.claude/settings.json`** — permission allow/deny (deny force-push, .env
   writes, deploy-workflow writes), SessionStart dep-install hooks.
4. **Core CI** — test + lint + type-check on every PR; coverage floor that
   ratchets up, never down.
5. **Security gates** — SAST + dependency audit as a required check from day one.
6. **Skills** — start with `/plan`, `/commit`, `/review`, `/status`; add domain
   skills when a workflow repeats 3×.
7. **Subagents** — one per irreversible-mistake domain (security always; money
   and schema if applicable).
8. **Living docs** — ACTION_ITEMS.md + sprint-current.md + first ADR; SessionStart
   banner so every session opens with sprint context.
9. **Migration safety workflow** — before the second migration exists, not after
   the first collision.
10. **Deploy + smoke** — deploy workflow ships with its post-deploy smoke check;
    runbook for rollback written the same day.
11. **Branch protection** — required checks the moment CI is green twice.
12. **Compliance cadence** — scheduled workflows for anything a regulator expects
    "periodically" (vendor review, retention checks) — calendar discipline as code.
