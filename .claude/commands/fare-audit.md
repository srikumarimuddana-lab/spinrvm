# /fare-audit — Deep Fare & Payment Audit

Delegate to the `spinr-money-auditor` agent to review all money-touching code in the current diff (or a named scope). When the diff touches the
AI/LLM surface, also dispatch `spinr-ai-guardrail-reviewer` in parallel (same
dual-dispatch pattern as `/review`'s safety row, `/security-check`,
`/compliance-check`, `/dispatch-check`, and `/surge-check`) — its rule #6
requires AI-path fare quotes go through the same real fare engine as every
other path (Decimal-only, no float, no model-guessed prices), which is
exactly the discipline `spinr-money-auditor` enforces everywhere else. A
model that recomputes or approximates a price outside `fare_service.py` is
the same class of bug this command exists to catch, just entered through
`backend/ai/tools_booking.py` instead of a route handler.

## Usage

```
/fare-audit                  # audits staged + unstaged changes
/fare-audit backend/services/fare_service.py   # audits a specific file
/fare-audit PR 123           # audits a GitHub PR's diff
```

## What it does

1. Scopes the audit:
   - No args → `git diff --cached` + `git diff` filtered to money-relevant paths
   - Path args → those files
   - `PR N` → pulls the PR diff via the GitHub MCP tools

2. Loads context: `@.claude/context/domain-payments.md`

3. Dispatches the `spinr-money-auditor` subagent with the scope

4. If the scope includes `backend/ai/**`, `backend/routes/ai.py`,
   `backend/routes/admin/ai_console.py`, or `rider-app/app/ai-assistant.tsx`,
   also dispatches `spinr-ai-guardrail-reviewer` **in parallel** with the same
   scope — independent audits over the same diff, not a sequential pass

5. Presents both agents' reports to the user, each under its own heading —
   no edits without explicit approval

## Money-relevant paths (auto-included when no args)

- `backend/services/fare_service.py`
- `backend/services/corporate_wallet_service.py`, `corporate_allowance_service.py`
- `backend/routes/payments.py`, `routes/wallet.py`, `routes/webhooks.py`, `routes/fares.py`
- `backend/utils/surge_engine.py`, `payment_retry.py`, `stripe_charge.py`
- `backend/migrations/*.sql` touching `ride_fare_breakdown`, `wallet_*`, `corporate_*`
- `shared/src/**/fare*`, `shared/src/**/payment*`
- Any file matching `*fare*`, `*payment*`, `*wallet*`, `*stripe*`
- `backend/ai/**`, `backend/routes/ai.py`, `backend/routes/admin/ai_console.py`,
  `rider-app/app/ai-assistant.tsx` — triggers `spinr-ai-guardrail-reviewer`
  alongside `spinr-money-auditor` (see step 4 above)

## Output

`spinr-money-auditor`'s report:

```
SPINR MONEY AUDIT — <scope>
===========================
BLOCKERS ...
WARNINGS ...
VERIFIED ...
VERDICT: SAFE TO MERGE / FIX BLOCKERS / NEEDS FINANCE/LEGAL REVIEW
```

If `spinr-ai-guardrail-reviewer` was also dispatched (AI-surface paths in
scope), its report follows under its own heading, verbatim — don't merge or
paraphrase the two reports into one:

```
SPINR AI GUARDRAIL AUDIT — <scope>
===================================
BLOCKERS ...
WARNINGS ...
OPEN BACKLOG TOUCHED ...
VERIFIED ...
VERDICT: SAFE TO MERGE / FIX BLOCKERS / NEEDS PRODUCT+LEGAL REVIEW
```

When both ran, the overall verdict is the worst of the two — never soften
one agent's verdict because the other came back clean (same rule the other
`-check` commands use for their rollups).

## When to run

- Before any PR that touches a money-relevant path
- Before a release that includes payment changes
- After a Stripe library upgrade
- On a cadence — e.g. monthly regression sweep of `fare_service.py`

## Do NOT

- Skip when the diff is "small" — one rogue `float()` is enough
- Auto-fix findings — the agent reports, humans decide the fix
