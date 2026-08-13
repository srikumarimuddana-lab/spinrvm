# /full-audit — Full-Fleet Parallel Reviewer Audit

Dispatch **every** `spinr-*` reviewer agent against the same scope, **independently and in parallel** — no path-routing, no skipping. `/review` is a *router* (dispatches only the subagents whose trigger paths match the diff); `/full-audit` is the **comprehensive** pass: every domain gets its own independent look at the same lines, because a single diff can carry a failure mode only one specialist would catch even on a path that "looks like" someone else's territory. Use this before a release cut, a large/cross-cutting PR, or whenever you want the full panel rather than a routed subset.

Note: not every quality dimension of the app is agent-shaped. Load/chaos testing (`ACTION_ITEMS.md` E2), DAST/pentest (E6), backup-restore drills (E7), and real screen-reader/visual-regression passes (N12) need real tooling or a human, not a grep-based agent — `/full-audit` doesn't claim to cover those; track them where they already are.

## Usage

```
/full-audit                  # audits staged + unstaged changes
/full-audit backend/services/dispatch_service.py   # audits a specific file/path
/full-audit PR 123           # audits a GitHub PR's diff
```

## 1 · Scope the diff

- No args → `git diff --cached --name-only` + `git diff --name-only` (staged + unstaged), then `git diff --cached` + `git diff` for content
- Path args → those files
- `PR N` → pull the PR diff via the GitHub MCP tools

If the diff is empty, say so and stop.

## 2 · Dispatch all 21 reviewer agents, in parallel, unconditionally

Launch every agent below in a **single batch of parallel `Agent` tool calls** (all in one response — not sequential). Each agent gets the same scope and works independently; none of them talk to each other, and a quiet one is a real finding ("nothing wrong here"), not a skipped step.

**Money & business logic**
| Agent | Independent angle |
|---|---|
| `spinr-money-auditor` | Decimal-only arithmetic, Stripe idempotency, receipt line-item transparency, surge cap respected in fare math |
| `spinr-surge-auditor` | 2.5× hard cap, tier table correctness, never-retroactive, never-on-corporate |
| `spinr-corporate-billing-reviewer` | `corporate_wallet_apply_delta` idempotency/row-locking, allowance-cap discipline |
| `spinr-fraud-auditor` | Referral velocity/self-referral, promo-stacking, device+phone reuse, GPS-ping plausibility |

**Core platform**
| Agent | Independent angle |
|---|---|
| `spinr-security-auditor` | OWASP Top 10, JWT trust model, RLS bypass, GPS-in-logs, secrets |
| `spinr-dispatch-reviewer` | Ride state machine legality, WS event emission, optimistic-lock acceptance guard |
| `spinr-insurance-period-auditor` | Period 0–3 classification correctness against ride state |
| `spinr-safety-sos-reviewer` | SOS never-auto-dial-911, degraded-auth availability, emergency-contact PII |
| `spinr-realtime-reliability-reviewer` | WS auth/heartbeat/rate-limit contract, cross-replica fan-out, background-loop replay-safety |
| `spinr-migration-reviewer` | Filename ordering, append-only, RLS coverage, reversibility, indexing (only if `.sql` files in scope) |
| `spinr-admin-rbac-reviewer` | Admin module-grant workflow — every sub-router gated, every `require_module()` string reachable via an actual grant path, sensitive surfaces held at `require_super_admin` not a module grant |
| `spinr-cicd-infra-reviewer` | CI/CD workflow + Docker/Fly/Railway config correctness — health checks, secrets handling, required-check wiring, dual-deploy parity (only if `.github/workflows/*.yml`/Docker/Fly/Railway config in scope) |

**Compliance & quality**
| Agent | Independent angle |
|---|---|
| `spinr-regulatory-compliance-checker` | SK Transportation Act + PIPEDA — retention, receipts/tax, accessibility floor, data deletion |
| `spinr-accessibility-reviewer` | WCAG 2.1 AA on any UI-surface files in scope |
| `spinr-ai-guardrail-reviewer` | PII scrubbing on provider egress, prompt-injection resistance (only if AI-surface files in scope) |
| `spinr-performance-sla-reviewer` | N+1 queries, blocking third-party calls, pagination, against the stated P95 SLA table |
| `spinr-observability-reviewer` | Sentry tag completeness, metric naming, log-level discipline, audit-table coverage |
| `spinr-test-coverage-reviewer` | Missing/theater test coverage against CLAUDE.md's required-test list and coverage minimums |
| `spinr-design-consistency-reviewer` | Brand/color/theme-parity consistency and UX-completeness (loading/empty/error states) — not a WCAG check, a product-polish check |
| `spinr-corporate-reporting-reviewer` | Cross-tenant data scoping in corporate reports/exports, tax-line-item export correctness |
| `spinr-edge-case-reviewer` | Network-retry safety, app-lifecycle state reconciliation, client/server version skew, multi-device races, clock-trust — the failure modes that live *between* domains |

That's 21 agents. Dispatch **all of them**, every run — do not pre-filter by path the way `/review` does. An agent finding nothing is itself useful signal ("audited, clean"); an agent that was never dispatched tells you nothing. The only legitimate skip: `spinr-migration-reviewer` and `spinr-ai-guardrail-reviewer` may report "not applicable — no matching files in scope" themselves rather than being excluded from dispatch, so the consolidated report shows they were checked.

## 3 · Generic code-quality pass (inline, not delegated)

Same as `/review` step 3 — Python type hints/no bare `except`, TypeScript no `any`/async error handling, no dead code, docstrings/JSDoc present. Keep this here, not as another agent — it's intentionally generic.

## 4 · Consolidate and report

Present all 21 verbatim, grouped by the four tables above, then the code-quality pass, then one rollup:

```
SPINR FULL-FLEET AUDIT — <scope>
==================================
Files: X changed | +Y -Z lines
Agents dispatched: 21/21

── MONEY & BUSINESS LOGIC ──────────────────────
  spinr-money-auditor                <verdict>
  spinr-surge-auditor                <verdict>
  spinr-corporate-billing-reviewer   <verdict>
  spinr-fraud-auditor                <verdict>
  spinr-corporate-reporting-reviewer <verdict, or "n/a — no report/export files in scope">

── CORE PLATFORM ───────────────────────────────
  spinr-security-auditor              <verdict>
  spinr-dispatch-reviewer             <verdict>
  spinr-insurance-period-auditor      <verdict>
  spinr-safety-sos-reviewer           <verdict>
  spinr-realtime-reliability-reviewer <verdict>
  spinr-migration-reviewer            <verdict, or "n/a — no migrations in scope">
  spinr-admin-rbac-reviewer           <verdict, or "n/a — no admin routes/staff.py in scope">
  spinr-cicd-infra-reviewer           <verdict, or "n/a — no CI/Docker/Fly/Railway config in scope">
  spinr-edge-case-reviewer            <verdict>

── COMPLIANCE & QUALITY ────────────────────────
  spinr-regulatory-compliance-checker <verdict>
  spinr-accessibility-reviewer      <verdict, or "n/a — no UI files in scope">
  spinr-design-consistency-reviewer <verdict, or "n/a — no UI files in scope">
  spinr-ai-guardrail-reviewer       <verdict, or "n/a — no AI-surface files in scope">
  spinr-performance-sla-reviewer    <verdict>
  spinr-observability-reviewer      <verdict>
  spinr-test-coverage-reviewer      <verdict>

<then each agent's full verbatim report under its own heading, same as /review>

── CODE QUALITY (inline) ───────────────────────
<findings>

VERDICT: SAFE TO MERGE / FIX BLOCKERS / NEEDS HUMAN REVIEW
```

The rollup is the worst verdict across all 21 plus the inline pass. Never soften any single agent's verdict.

## When to run

- Before cutting a release
- Before merging a PR that crosses two or more surfaces (backend + a frontend app, or two backend domains)
- On a scheduled cadence (e.g. weekly) against `main` as a standing-drift check, independent of any one PR
- When `/review`'s path-routing would legitimately skip something you want checked anyway (e.g. you want the accessibility and test-coverage passes on a PR that `/review`'s table wouldn't route to them)

## Do NOT

- Do not pre-filter which agents run based on the diff's apparent domain — that's what `/review` is for; this command's entire value is running the full panel regardless
- Do not run the 16 agents sequentially — batch them in one parallel dispatch; sequential defeats the purpose (wall-clock cost) and this task explicitly calls for independent, concurrent review
- Do not auto-fix findings — every agent is audit-only; report, let the user decide
- Do not skip `Codex`-review context if present on a PR — cross-reference per CLAUDE.md's "PR review handling" section in addition to, not instead of, this fleet

## See also

`/review` for routed (cheaper) single-PR review. Single-domain deep dives: `/fare-audit`, `/migration-check`, `/security-check`, `/dispatch-check`, `/surge-check`, `/corporate-check`, `/insurance-check`, `/compliance-check`, `/ai-check`.
