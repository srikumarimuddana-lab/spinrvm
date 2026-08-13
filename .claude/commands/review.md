# /review — Pre-Commit Review Router for spinr

Route the current diff to the specialist subagent(s) that actually cover it,
run a lightweight generic code-quality pass yourself, and present one
consolidated report. This command does not reinvent domain rules — the 19
`spinr-*` subagents already encode them in more depth than any inline
checklist here could; this command's job is dispatch, not duplication.

For the "dispatch every agent regardless of path" mode, see `/full-audit`.

## Usage

```
/review                  # audits staged + unstaged changes
/review backend/services/fare_service.py   # audits a specific file/path
/review PR 123            # audits a GitHub PR's diff
```

## 1 · Scope the diff

- No args → `git diff --cached --name-only` + `git diff --name-only` (staged + unstaged), then `git diff --cached` + `git diff` for content
- Path args → those files
- `PR N` → pull the PR diff via the GitHub MCP tools (`mcp__github__pull_request_read` or equivalent)

If the diff is empty, say so and stop — don't run agents against nothing.

## 2 · Route to subagents by path

Match the changed-file list against each trigger set below (same taxonomy as
`.github/labeler.yml`'s `area:*` labels — reuse it, don't re-derive it) and
dispatch every matched subagent **in parallel**. A single diff commonly
triggers several — that's expected, not redundant, since each covers a
different failure mode over the same lines.

| Trigger paths (`area:*` in labeler.yml, or the subagent's own scope) | Subagent |
|---|---|
| `backend/routes/auth.py`, `backend/routes/admin/auth*.py`, `backend/utils/crypto.py`, `backend/utils/rate_limiter.py`, any `*rls*.sql`/`*policy*.sql` migration, `backend/routes/admin/**`, anything touching JWT/OTP/PII handling | `spinr-security-auditor` |
| `backend/services/fare_*.py`, `backend/services/corporate_*.py`, `backend/routes/payments.py`, `routes/wallet.py`, `routes/corporate*.py`, `routes/fares.py`, `routes/tips.py`, `routes/payouts.py`, `utils/surge_engine.py`, `utils/payment_retry.py`, `routes/drivers.py`, `routes/rides.py` (money paths — `area:money`) | `spinr-money-auditor` |
| `backend/utils/surge_engine.py`, `backend/routes/admin/*surge*`, surge application inside `fare_service.py` | `spinr-surge-auditor` |
| `backend/routes/corporate*.py`, `backend/services/corporate_*.py`, `backend/routes/wallet.py`, any caller of `corporate_wallet_apply_delta` | `spinr-corporate-billing-reviewer` |
| `backend/services/dispatch_service.py`, `backend/routes/rides.py`, `backend/socket_manager.py`, `backend/utils/ws_pubsub.py`, `backend/utils/scheduled_rides.py` (`area:dispatch`) | `spinr-dispatch-reviewer` |
| Any diff touching `driver_insurance_periods` writes, `go_online`, or ride-state transitions that cross a period boundary | `spinr-insurance-period-auditor` |
| New/modified `backend/migrations/*.sql` | `spinr-migration-reviewer` |
| `backend/routes/safety.py`, `routes/sos.py`, `services/insurance_*.py`, `utils/emergency_*.py` (`area:safety`) | `spinr-security-auditor` **and** `spinr-regulatory-compliance-checker` |
| Anything touching driver eligibility, trip/GPS retention, receipt tax line items, accessibility (WAV/service animal), logging/analytics/Sentry payloads, or data-deletion flows | `spinr-regulatory-compliance-checker` |
| `backend/ai/**`, `backend/routes/ai.py`, `backend/routes/admin/ai_console.py`, `rider-app/app/ai-assistant.tsx` (`area:ai`) | `spinr-ai-guardrail-reviewer` |
| Referrals (rider/driver), promo codes, quests/incentives/loyalty, signup flow, or driver location-ping ingestion | `spinr-fraud-auditor` |
| `backend/socket_manager.py`, `backend/utils/ws_pubsub.py`, WebSocket route handlers, or any of the 18 loops in `backend/core/lifespan.py` | `spinr-realtime-reliability-reviewer` |
| `routes/safety.py`, `routes/sos.py`, emergency-contact handling (product/UX surface, not insurance classification) | `spinr-safety-sos-reviewer` **and** `spinr-security-auditor` |
| A path with a stated P95 SLA (dispatch offer→accept, fare estimate/settlement, WS fan-out, driver location write, auth refresh, Stripe webhook) or any new admin/dashboard list endpoint | `spinr-performance-sla-reviewer` |
| New/changed Sentry captures, metric emission, log statements, or background loops | `spinr-observability-reviewer` |
| `rider-app/`, `driver-app/`, `admin-dashboard/` UI-surface files | `spinr-accessibility-reviewer` |
| A diff adding a ride-state transition, fare-calc branch, auth/RLS policy, or Stripe webhook type; or touching a module with a stated coverage minimum | `spinr-test-coverage-reviewer` |
| `rider-app/`, `driver-app/`, `admin-dashboard/` UI-surface files (brand/theme/loading-empty-error-state completeness — pairs with `spinr-accessibility-reviewer`, different lens) | `spinr-design-consistency-reviewer` |
| Corporate report/export code (`routes/corporate_company_bookings.py`, admin-dashboard corporate report views, any `*export*`/`*report*` file touching `corporate_*`) | `spinr-corporate-reporting-reviewer` |
| A multi-step user-facing flow (booking, payment, document upload) spanning network round-trips, a `shared/` contract change, or code reading client-supplied timestamps | `spinr-edge-case-reviewer` |

`spinr-regulatory-compliance-checker` explicitly isn't path-scoped in its own
definition ("compliance issues can appear anywhere") — if the diff touches
anything logging-, retention-, or receipt-related outside the obvious paths
above, include it anyway rather than relying on the table being exhaustive.

If **no** trigger matches (e.g. the diff is docs-only, or touches only
`rider-app`/`driver-app`/`admin-dashboard` UI with no money/auth/safety
surface), skip agent dispatch entirely and go straight to step 3 — don't
force an irrelevant subagent to run just to have output.

## 3 · Generic code-quality pass (inline, not delegated)

None of the 19 subagents cover this — it's intentionally generic, not
Spinr-specific, so keep it here rather than inventing another subagent for it:

- Python: type hints present, no bare `except:` clauses
- TypeScript: no `any` types, errors handled on async functions
- No dead code or unused imports
- New functions have docstrings (Python) or JSDoc (TypeScript)
- Tests: does the diff touch a path with a coverage minimum in CLAUDE.md
  (`routes/payments.py`, `services/fare_service.py`, `utils/crypto.py` ≥90%;
  `routes/rides.py`, `services/dispatch_service.py` ≥80%) without a
  corresponding test file change? Flag if so.

## 4 · Consolidate and report

Present each dispatched subagent's report verbatim under its own heading —
don't paraphrase or compress their findings, they're already terse. Then add
one rollup verdict.

```
SPINR REVIEW — <scope>
=======================
Files: X changed | +Y -Z lines
Routed to: <list of dispatched subagents, or "none — no domain paths touched">

── spinr-security-auditor ──────────────────────
<verbatim report, or omitted if not dispatched>

── spinr-money-auditor ─────────────────────────
<verbatim report, or omitted if not dispatched>

... (one section per dispatched subagent) ...

── CODE QUALITY (inline) ───────────────────────
<findings from step 3>

VERDICT: SAFE TO COMMIT / FIX BLOCKERS / NEEDS HUMAN REVIEW
```

The rollup verdict is the worst of: any dispatched subagent's own verdict, or
`FIX BLOCKERS` if step 3 found something. Never soften a subagent's verdict
in the rollup — if `spinr-security-auditor` says `FIX BLOCKERS`, the rollup
says `FIX BLOCKERS` regardless of what anything else found.

## When to run

- Before every commit that isn't purely docs/formatting (the pre-commit git
  hook catches secrets/PII/float regressions mechanically; `/review` is the
  deeper pass before that, not a replacement for it)
- Before opening a PR with `/pr` — `/pr` doesn't run these subagents itself

## Do NOT

- Do not re-derive the domain rules inline — if a check feels missing, the
  fix is updating the relevant `spinr-*` subagent definition, not adding a
  bullet here
- Do not skip dispatching a matched subagent because the diff "looks small"
  — one rogue line is exactly what these exist to catch
- Do not auto-fix findings — every subagent is audit-only by design; report,
  let the user decide

## See also

For a single-domain deep dive instead of the full router: `/fare-audit`
(money), `/migration-check` (migrations), `/security-check`,
`/dispatch-check`, `/surge-check`, `/corporate-check`, `/insurance-check`,
`/compliance-check`, `/ai-check` (AI/LLM surface).
