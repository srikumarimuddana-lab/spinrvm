# /surge-check — Surge Pricing Audit

Delegate to the `spinr-surge-auditor` agent to review the auto-mode tier
table, the 2.5× hard cap, admin manual-override justification, and the
never-retroactive / never-on-corporate rules in the current diff (or a named
scope). When the diff touches the AI/LLM surface, also dispatch
`spinr-ai-guardrail-reviewer` in parallel (same dual-dispatch pattern as
`/review`'s safety row, `/security-check`, `/compliance-check`, and
`/dispatch-check`) — `spinr-ai-guardrail-reviewer` doesn't check the surge
cap, tier table, or corporate-exemption rule itself (those stay
`spinr-surge-auditor`'s job), but its rule #6 (AI-path fare quotes must run
through the real fare service, never be recomputed or approximated by the
model) is what stops a surged price from being reconstructed outside the one
engine that actually enforces the cap. Weaker overlap than the other three
`-check` commands; wired in for consistency at the user's request rather than
a domain-rule match as tight as security/compliance/dispatch.

## Usage

```
/surge-check                     # audits staged + unstaged changes
/surge-check backend/utils/surge_engine.py   # audits a specific file
/surge-check PR 123              # audits a GitHub PR's diff
```

## What it does

1. Scopes the audit:
   - No args → `git diff --cached` + `git diff` filtered to surge-relevant paths
   - Path args → those files
   - `PR N` → pulls the PR diff via the GitHub MCP tools
2. Loads context: `@.claude/context/domain-payments.md` (surge section)
3. Dispatches the `spinr-surge-auditor` subagent with the scope
4. If the scope includes `backend/ai/**`, `backend/routes/ai.py`,
   `backend/routes/admin/ai_console.py`, or `rider-app/app/ai-assistant.tsx`,
   also dispatches `spinr-ai-guardrail-reviewer` **in parallel** with the same
   scope — independent audits over the same diff, not a sequential pass
5. Presents both agents' reports to the user, each under its own heading —
   no edits without explicit approval

## Surge-relevant paths (auto-included when no args)

- `backend/utils/surge_engine.py`
- `backend/routes/admin/*surge*`
- Surge application inside `backend/services/fare_service.py`
- `backend/ai/**`, `backend/routes/ai.py`, `backend/routes/admin/ai_console.py`,
  `rider-app/app/ai-assistant.tsx` — triggers `spinr-ai-guardrail-reviewer`
  alongside `spinr-surge-auditor` (see step 4 above)

## Output

`spinr-surge-auditor`'s report:

```
SPINR SURGE AUDIT — <scope>
============================
BLOCKERS ...
WARNINGS ...
VERIFIED ...
VERDICT: SAFE TO MERGE / FIX BLOCKERS / NEEDS BUSINESS+LEGAL REVIEW
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
one agent's verdict because the other came back clean (same rule `/review`,
`/security-check`, `/compliance-check`, and `/dispatch-check` use for their
rollups).

## When to run

- Before any PR touching `surge_engine.py`, the admin surge-override endpoint, or surge application in fare calc
- Before raising the auto-mode tier table or cap — this needs an ADR link and documented business+legal review regardless of what the agent finds
- On a cadence — surge is the single most reputationally/regulatory-sensitive number in the fare calc

## Do NOT

- Skip because "it's just the admin override" — override paths are exactly where the >2.5× justification requirement gets forgotten
- Approve any change that raises `SURGE_CAP` without a business+legal sign-off note in the diff, regardless of what this command reports
- Auto-fix findings — the agent reports, humans decide the fix
