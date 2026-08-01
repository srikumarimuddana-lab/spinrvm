# /dispatch-check — Ride Dispatch & State Machine Audit

Delegate to the `spinr-dispatch-reviewer` agent to review ride state machine
transitions, driver matching, offer-timeout logic, and WebSocket event
coverage in the current diff (or a named scope). When the diff touches the
AI/LLM surface, also dispatch `spinr-ai-guardrail-reviewer` in parallel (same
dual-dispatch pattern as `/review`'s safety row, `/security-check`, and
`/compliance-check`) — `spinr-ai-guardrail-reviewer`'s rules don't cover ride
state machine, WS events, or offer-timeout logic themselves, but its rule #3
(booking/cancel tools must stay proposal-only through the unmodified
`POST /rides` path, never writing dispatch state directly) is exactly the
guardrail that keeps AI-originated bookings from bypassing everything
`spinr-dispatch-reviewer` enforces on that path.

## Usage

```
/dispatch-check                  # audits staged + unstaged changes
/dispatch-check backend/services/dispatch_service.py   # audits a specific file
/dispatch-check PR 123           # audits a GitHub PR's diff
```

## What it does

1. Scopes the audit:
   - No args → `git diff --cached` + `git diff` filtered to dispatch-relevant paths
   - Path args → those files
   - `PR N` → pulls the PR diff via the GitHub MCP tools
2. Loads context: `@.claude/context/domain-dispatch.md`
3. Dispatches the `spinr-dispatch-reviewer` subagent with the scope
4. If the scope includes `backend/ai/**`, `backend/routes/ai.py`,
   `backend/routes/admin/ai_console.py`, or `rider-app/app/ai-assistant.tsx`,
   also dispatches `spinr-ai-guardrail-reviewer` **in parallel** with the same
   scope — independent audits over the same diff, not a sequential pass
5. Presents both agents' reports to the user, each under its own heading —
   no edits without explicit approval

## Dispatch-relevant paths (auto-included when no args)

- `backend/services/dispatch_service.py`
- `backend/routes/rides.py`
- `backend/socket_manager.py`, `backend/utils/ws_pubsub.py`
- `backend/utils/scheduled_rides.py`
- `backend/core/lifespan.py` if the diff touches the scheduled-dispatch background loop
- `backend/ai/**`, `backend/routes/ai.py`, `backend/routes/admin/ai_console.py`,
  `rider-app/app/ai-assistant.tsx` — triggers `spinr-ai-guardrail-reviewer`
  alongside `spinr-dispatch-reviewer` (see step 4 above)

## Output

`spinr-dispatch-reviewer`'s report:

```
SPINR DISPATCH AUDIT — <scope>
===============================
BLOCKERS ...
WARNINGS ...
VERIFIED ...
VERDICT: SAFE TO MERGE / FIX BLOCKERS / NEEDS DISPATCH-TEAM REVIEW
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
`/security-check`, and `/compliance-check` use for their rollups).

## When to run

- Before any PR touching the ride state machine, driver matching, or offer-timeout logic
- Before a release that changes WS event emission on any ride transition
- After any change to `is_online`/`is_available` handling

## Do NOT

- Skip when the diff "only touches one branch" — a missing WS event on one transition branch is enough to strand a UI
- Auto-fix findings — the agent reports, humans decide the fix
