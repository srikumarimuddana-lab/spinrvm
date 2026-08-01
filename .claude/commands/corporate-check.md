# /corporate-check — Corporate Billing & Wallet Audit

Delegate to the `spinr-corporate-billing-reviewer` agent to review
`corporate_wallet_apply_delta` callers, allowance-cap enforcement, payment-source
priority, and idempotency in the current diff (or a named scope). When the
diff touches the AI/LLM surface, also dispatch `spinr-ai-guardrail-reviewer`
in parallel (same dual-dispatch pattern as `/review`'s safety row,
`/security-check`, `/compliance-check`, `/dispatch-check`, and
`/fare-audit`) — `ACTION_ITEMS.md`'s `B-AI1` documents an **open, named** gap
where corporate rider booking via AI chat may bypass corporate billing rules
entirely, so this isn't a speculative connection like `/surge-check`'s —
it's a confirmed intersection between the two domains.

## Usage

```
/corporate-check                 # audits staged + unstaged changes
/corporate-check backend/services/corporate_wallet_service.py   # audits a specific file
/corporate-check PR 123          # audits a GitHub PR's diff
```

## What it does

1. Scopes the audit:
   - No args → `git diff --cached` + `git diff` filtered to corporate-relevant paths
   - Path args → those files
   - `PR N` → pulls the PR diff via the GitHub MCP tools
2. Loads context: `@.claude/context/domain-corporate.md`, `@.claude/context/domain-payments.md`
3. Dispatches the `spinr-corporate-billing-reviewer` subagent with the scope
4. **Cascade-effect reminder** (from `domain-corporate.md`'s documented lessons
   learned — three separate integration gaps shipped this exact way): if the
   diff touches `routes/rides/booking.py`'s two independent corporate booking
   paths (`company_allowance` and `work_profile`), explicitly confirm the fix
   was applied to **both** blocks, not just the one that prompted the change
5. If the scope includes `backend/ai/**`, `backend/routes/ai.py`,
   `backend/routes/admin/ai_console.py`, or `rider-app/app/ai-assistant.tsx`,
   also dispatches `spinr-ai-guardrail-reviewer` **in parallel** with the same
   scope — independent audits over the same diff, not a sequential pass. Ask
   it explicitly whether the diff touches AI-originated corporate booking and,
   if so, whether it goes through the same payment-source-priority path
   (rider wallet → corporate allowance → master wallet → rider card) as
   `B-AI1` requires
6. Presents both agents' reports to the user, each under its own heading —
   no edits without explicit approval

## Corporate-relevant paths (auto-included when no args)

- `backend/routes/corporate*.py`
- `backend/services/corporate_*.py`
- `backend/routes/wallet.py`
- `backend/routes/rides/booking.py` (the two corporate booking paths inside `create_ride`)
- `backend/utils/allowance_reset.py`, `backend/utils/corporate_low_balance.py`
- Any caller of `corporate_wallet_apply_delta` anywhere in the codebase, not just the diff
- `backend/ai/**`, `backend/routes/ai.py`, `backend/routes/admin/ai_console.py`,
  `rider-app/app/ai-assistant.tsx` — triggers `spinr-ai-guardrail-reviewer`
  alongside `spinr-corporate-billing-reviewer` (see step 5 above), especially
  relevant given the open `B-AI1` gap

## Output

`spinr-corporate-billing-reviewer`'s report:

```
SPINR CORPORATE BILLING AUDIT — <scope>
========================================
BLOCKERS ...
WARNINGS ...
BLAST RADIUS  (other callers of any shared function touched)
VERIFIED ...
VERDICT: SAFE TO MERGE / FIX BLOCKERS / NEEDS FINANCE REVIEW
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

- Before any PR touching corporate accounts, membership, wallet, or allowance code
- Before any lifecycle change (company suspend/close, member offboarding, policy edit) — cross-check against the cascade-effect table in `domain-corporate.md`
- Before a change to `corporate_wallet_apply_delta`'s signature or locking behavior — this is a shared money primitive, list every consumer

## Do NOT

- Accept "just this once" for a direct wallet UPDATE, even in a migration/backfill script
- Approve a payment-source-order change without an explicit product/finance sign-off note
- Auto-fix findings — the agent reports, humans decide the fix
