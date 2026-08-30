# Spinr AI assistant red team (promptfoo)

Manual eval harness for the AI assistant tool layer (`backend/ai/tools*.py`,
`backend/ai/orchestrator.py`, `backend/ai/threat.py`). See the header comment
in `promptfooconfig.yaml` for scope and why this is intentionally **not**
wired into CI.

## What this checks

Each case in `promptfooconfig.yaml` sends an attack prompt to a real,
authenticated `/ai/chat` turn and asserts the reply doesn't comply — prompt
injection / system-prompt disclosure, role-override jailbreaks, impersonation
of another rider/driver, and bulk PII exfiltration attempts. These mirror the
categories `backend/ai/threat.py` already detects (`prompt_injection`,
`role_override`, `impersonation`, `data_exfiltration`) plus the identity-arg
smuggling `backend/ai/tools.py`'s `FORBIDDEN_ID_ARGS` guards against — this
suite checks the model-facing behavior; the tool layer is still the real
control (`_require_ride_in_state`-style guards apply there, not here).

## Running it

1. Start a **local dev backend** against a **test** Supabase project — never
   production:
   ```bash
   cd backend && python3 -m backend.server
   ```
2. Obtain a JWT for a **test rider account** (never a real rider). The
   easiest path is to sign in through the rider app pointed at your local
   backend and copy the access token from its auth storage, or mint one via
   whatever test-account fixture your local setup already uses.
3. Run the eval:
   ```bash
   cd backend/evals/promptfoo
   export SPINR_EVAL_BACKEND_URL=http://localhost:8000
   export SPINR_EVAL_JWT=<test rider access token>
   npx promptfoo@latest eval
   npx promptfoo@latest view   # opens a local results browser
   ```

## Triaging a failure

A failing case means the assistant's **reply text** complied with an attack
(e.g. it echoed a planted other-user id, or claimed to be in "developer
mode") — it does **not** by itself mean data was actually leaked, since
`tools.py`'s ownership checks run server-side regardless of what the model
says. Still treat it as a real finding: a model that narrates compliance is
one bad tool-call schema change away from actual compliance. Next steps:

1. Reproduce the exact prompt manually against `/ai/chat` and read the full
   transcript (tool calls + reply), not just the final reply text.
2. If a tool call was actually attempted with a forbidden id, that's a P0 —
   `tools.py`'s `FORBIDDEN_ID_ARGS` check should have rejected it before
   `execute_tool` ran; file it immediately, this is `spinr-ai-guardrail-reviewer`
   territory.
3. If no tool call was attempted and the model just talked about compliance,
   it's still worth a `backend/ai/prompts.py` system-prompt tightening — file
   it as a normal finding, not a P0.
4. Add the exact failing prompt as a new case here once fixed, so it stays a
   regression test.

## Extending this suite

Add new tools to the attack surface as they're registered (see
`.claude/skills/spinr-ai-tool/SKILL.md` for the tool-registration recipe) —
in particular any new tool that reads or mutates account-scoped data should
get an impersonation-style case here mirroring the ones already present.
