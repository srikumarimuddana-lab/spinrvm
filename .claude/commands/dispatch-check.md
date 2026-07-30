# /dispatch-check — Ride Dispatch & State Machine Audit

Delegate to the `spinr-dispatch-reviewer` agent to review ride state machine
transitions, driver matching, offer-timeout logic, and WebSocket event
coverage in the current diff (or a named scope).

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
4. Presents the agent's report to the user — no edits without explicit approval

## Dispatch-relevant paths (auto-included when no args)

- `backend/services/dispatch_service.py`
- `backend/routes/rides.py`
- `backend/socket_manager.py`, `backend/utils/ws_pubsub.py`
- `backend/utils/scheduled_rides.py`
- `backend/core/lifespan.py` if the diff touches the scheduled-dispatch background loop

## Output

The agent's report:

```
SPINR DISPATCH AUDIT — <scope>
===============================
BLOCKERS ...
WARNINGS ...
VERIFIED ...
VERDICT: SAFE TO MERGE / FIX BLOCKERS / NEEDS DISPATCH-TEAM REVIEW
```

## When to run

- Before any PR touching the ride state machine, driver matching, or offer-timeout logic
- Before a release that changes WS event emission on any ride transition
- After any change to `is_online`/`is_available` handling

## Do NOT

- Skip when the diff "only touches one branch" — a missing WS event on one transition branch is enough to strand a UI
- Auto-fix findings — the agent reports, humans decide the fix
