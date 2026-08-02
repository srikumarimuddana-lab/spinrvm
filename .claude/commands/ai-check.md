# /ai-check — AI/LLM Guardrail Audit

Delegate to the `spinr-ai-guardrail-reviewer` agent to review PII scrubbing on
provider-egress paths, PIPEDA ban-list enforcement, prompt-injection
resistance on state-mutating tools, rate/cost controls, eval coverage for new
tools, money-rule reuse, and provider-fallback behavior in the current diff
(or a named scope).

## Usage

```
/ai-check                        # audits staged + unstaged changes
/ai-check backend/ai/tools_booking.py   # audits a specific file
/ai-check PR 123                 # audits a GitHub PR's diff
```

## What it does

1. Scopes the audit:
   - No args → `git diff --cached` + `git diff` filtered to AI-relevant paths
   - Path args → those files
   - `PR N` → pulls the PR diff via the GitHub MCP tools
2. Dispatches the `spinr-ai-guardrail-reviewer` subagent with the scope
3. Presents the agent's report to the user — no edits without explicit approval

## AI-relevant paths (auto-included when no args)

- `backend/ai/**` (orchestrator, MCP server, provider adapters, `pii.py`, all `tools_*.py`, `response_cache.py`, `conversations.py`, `threat.py`)
- `backend/routes/ai.py`
- `backend/routes/admin/ai_console.py`
- `rider-app/app/ai-assistant.tsx`

## What gets checked

From the agent — see `.claude/agents/spinr-ai-guardrail-reviewer.md` for the
full rule set, summarized here:

- **PII scrubbing verified per-path, not blanket** — orchestrator (both the
  live-stream and persisted-message sinks), each of the three provider
  adapters, every `tools_*.py` module, `mcp_server.py`, `response_cache.py`,
  `conversations.py` — checked independently, per this codebase's repeat
  "scrubbed on one path, not its sibling" failure mode
- **PIPEDA ban-list** applied to provider payloads and logs alike (raw GPS,
  full phone/name/email, gov IDs, exact addresses)
- **Prompt-injection resistance** on booking/cancel/wallet tools — must stay
  proposal-only through the unmodified `POST /rides` path, never write
  dispatch/wallet state directly from inside a tool
- **Rate limiting and cost controls** on both the rider (`routes/ai.py`) and
  admin (`ai_console.py`) entry points, plus the parallel-tool-call cap
- **Eval coverage** — flags any new tool shipped with only mocked unit tests
  and no eval harness as a blocking gap, not a silent pass (no eval harness
  exists in this repo yet)
- **Money rules inside AI paths** — fare quotes must run through the real
  fare engine, Decimal-only arithmetic, corporate-billing-priority parity
- **Provider fallback** — `AIConfigError` must surface loudly, never a silent
  swap to a different provider/model with different guardrails

## Output

The agent's report:

```
SPINR AI GUARDRAIL AUDIT — <scope>
===================================
BLOCKERS ...
WARNINGS ...
OPEN BACKLOG TOUCHED  (ACTION_ITEMS.md AI1-AI14 items this diff relates to)
VERIFIED ...
VERDICT: SAFE TO MERGE / FIX BLOCKERS / NEEDS PRODUCT+LEGAL REVIEW
```

## When to run

- Before any PR touching `backend/ai/**`, the AI chat routes, the admin AI
  console, or the rider AI assistant screen
- Before a release that adds a new AI tool, changes a provider adapter, or
  touches `pii.py`
- Whenever `ACTION_ITEMS.md`'s open `AI1`–`AI14` backlog items are relevant to
  the change — the agent cross-checks the diff against them

## See also

`spinr-ai-guardrail-reviewer` is also dual-dispatched automatically inside
`/review`'s router, `/security-check`, `/compliance-check`, `/dispatch-check`,
`/surge-check`, `/fare-audit`, and `/corporate-check` whenever their scope
touches an AI-surface path — use `/ai-check` when you want *only* the AI
guardrail audit, without pulling in whichever other domain reviewer those
commands would normally dispatch alongside it.

## Do NOT

- Skip because the diff "just adds a small tool" — a new tool with no eval
  coverage is exactly the class of gap this command exists to catch
- Accept "PII scrubbing looks fine" as a blanket verdict — the agent is
  expected to list each path checked individually
- Auto-fix findings — the agent reports, humans decide the fix
