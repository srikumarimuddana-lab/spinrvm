---
name: spinr-ai-tool
description: Recipe and safety contract for adding a new tool the AI assistant can call (a new ToolSpec/register() call in backend/ai/tools_*.py). Use whenever the task is to add a new tool the model invokes, not for editing an existing tool's business logic alone.
---

# AI Tool Recipe

Every tool the assistant can call is a new way for a model — which can be
prompted, jailbroken, or simply wrong — to read or write real rider/driver
data and reach a paid third-party LLM provider. A new tool must satisfy the
same six-point contract every existing tool in `backend/ai/tools_*.py`
already does, or it is a regression even if its own handler logic is
correct.

Template for a new tool:

```python
# backend/ai/tools_<domain>.py
async def my_new_tool(user: Dict[str, Any], some_id: str, note: str) -> Dict[str, Any]:
    # `user` is server-injected by execute_tool() -- never a model-supplied
    # argument. `some_id` is owned-resource -- ownership is verified centrally
    # BEFORE this handler runs (see registration below), so by the time you're
    # here it's already confirmed to belong to `user`.
    row = await db_supabase.get_some_row(some_id)
    if not row:
        return {"error": "not found"}
    return _pick(row, _WHITELISTED_FIELDS)  # never return the raw row

register(
    ToolSpec(
        name="my_new_tool",
        description="Call this when...",  # prescriptive -- providers pass this through verbatim
        input_schema={
            "type": "object",
            "properties": {"some_id": {"type": "string", "maxLength": 64}, "note": {"type": "string", "maxLength": 300}},
            "required": ["some_id"],
        },
        handler=my_new_tool,
        owned_id_args={"some_id": "some_resource_kind"},  # or public_id_args={"some_id"} if it's not user-owned
        audiences=frozenset({"rider"}),  # or {"driver"} / {"rider", "driver"}
        mcp_exposed=True,  # False if the tool writes anything, or is booking-flow-only
    )
)
```

## 1. Registration contract (enforced at import time, don't fight it)

- **Identity args are banned outright.** `user_id`/`rider_id`/`driver_id`/etc.
  (`FORBIDDEN_ID_ARGS` in `tools.py`) can never appear in `input_schema`. The
  caller's identity is always the server-injected `user` dict — the model
  must never be able to choose whose data a tool reads.
- **Every other id-shaped argument must be classified.** `_is_id_arg()`
  matches `"id"` or anything ending `_id`. Put it in `owned_id_args` (with a
  registered `register_ownership_verifier(kind, fn)` — see `tools_rides.py`'s
  `_verify_ride_ownership` for the pattern) if it names a resource the caller
  must own, or `public_id_args` if it's non-personal (a vehicle-type id, a
  service-area id). An unclassified id arg fails `register()` at import time
  — that's a feature, don't work around it by renaming the argument to dodge
  the `_id` suffix check.
- Handlers still re-verify ownership defensively even though
  `execute_tool()` already checked it centrally (defense in depth — see
  `tools_rides.py`'s `_owned_ride`).

## 2. Mandatory PII scrubbing — know what it does NOT cover

`ai/pii.py`'s `scrub_pii()` is applied to the user's typed message and to the
assistant's final reply **before persistence** — it is not applied to tool
*results*. Whatever your handler returns goes to the configured LLM provider
essentially verbatim. This is not a bug to route around; it's why every
existing tool hand-whitelists its output fields instead of returning a raw
DB row:

- Return only named, whitelisted fields (`_pick(row, _SOME_FIELDS)` pattern
  throughout `tools_rides.py`/`tools_account.py`) — never `return dict(row)`
  or an ORM object.
- Never include: raw GPS coordinates outside the booking-flow tools (which
  need them and treat them as ephemeral — never logged, never persisted,
  see `tools_booking.py`'s module docstring), OTPs, driver earnings *splits*,
  payment intent ids, another user's data of any kind.
- If the tool is genuinely coordinate-carrying (booking/quote tools), that's
  an accepted, documented exception (`docs/compliance/pia-ai-surfaces-2026-08.md`
  Section 3) — not a precedent for a new tool to also skip whitelisting.

## 3. Rate limiting

- Handler timeout: omit `timeout_seconds` to inherit `TOOL_TIMEOUT_SECONDS`
  (5s). Only override it for a tool with a real multi-network-call worst
  case (see `tools_booking.py`'s Maps fan-out tools at 15s) — and comment
  *why*, with the arithmetic, same as they do.
- A new tool does NOT get its own daily cap. It rides the existing per-user
  caps: `ai_daily_message_cap` (chat path, `orchestrator.py`) and
  `ai_mcp_daily_tool_cap` (the `/mcp` path, `mcp_server.py`) if
  `mcp_exposed=True`. Don't add a parallel rate-limit mechanism.
- Result size: `_cap_result()` in `tools.py` truncates any result over
  `TOOL_RESULT_MAX_CHARS` automatically — don't hand-truncate in the
  handler, but DO make sure any guardrail note/refusal field you add is
  named in `_GUARDRAIL_KEYS` if it must survive truncation (see
  `tools_booking.py`'s `needs_confirmation`/`imprecise_address` pattern).

## 4. The parallel-call cap

The orchestrator caps tool calls per model turn at
`MAX_TOOL_CALLS_PER_ITERATION` (5, in `orchestrator.py`) — a single
adversarial or hallucinating turn cannot fan your new tool out unbounded.
This is enforced centrally; a new tool does not need (and must not add) its
own per-turn concurrency guard. If your tool makes its own paid external
call (Maps, an LLM, anything metered), that's exactly the kind of tool this
cap exists to protect — do not raise `MAX_TOOL_CALLS_PER_ITERATION` to work
around a slow tool; fix the tool's latency instead.

## 5. The eval requirement

**No dedicated eval harness exists in this repo as of this skill's
introduction.** `backend/tests/test_ai_tools_*.py` covers unit-level
handler correctness (mocked Supabase, deterministic inputs) — it does not
exercise whether a real model, given a realistic prompt, actually selects
and calls your new tool correctly, resists being talked out of a guardrail,
or behaves across a multi-turn conversation. Do not treat unit-test
coverage as eval coverage; they test different things.

- A new tool ships with unit tests for its handler (required, same as any
  other backend code) **and** a plain-language note in the PR description
  stating that no eval harness verifies the model's tool-selection
  behavior — this is a known, standing gap, not something to silently
  paper over.
- `spinr-ai-guardrail-reviewer` enforces this at review time (its §5). This
  skill is the authoring-time reminder; that agent is the check.
- If you're the one building the eval harness itself: it must actually
  invoke a provider adapter (or replay a recorded transcript) — a harness
  that only calls the tool handler directly is unit tests wearing an eval's
  name.

## 6. Safe MCP exposure

`mcp_exposed` (default `True`) decides whether `/mcp` — a READ-ONLY surface
for external agent clients, gated by `ai_mcp_enabled` — can reach this tool
at all.

- Set `mcp_exposed=False` for anything that writes, anything booking-flow
  (`find_place`, `get_fare_quote`, `propose_ride_booking`,
  `request_map_pin` are all chat-only), and anything whose result should
  never leave Spinr's own chat surface into a rider/driver's own external
  agent client (`escalate_to_support` — it can open a real ticket).
- Leave the default `True` only for genuinely read-only, no-side-effect
  tools (ride history, wallet balance, FAQ search).
- `mcp_exposed=True` is not a stronger safety boundary than the chat
  path — it's a *different, less visible* one (Spinr does not control what
  the external client or its own LLM does with the result afterward, see
  `docs/compliance/pia-ai-surfaces-2026-08.md` R-10). Default to `False`
  when in doubt; making a chat-only tool MCP-visible later is a one-line
  change, the reverse requires auditing every external client that might
  already depend on it.

Forbidden: a tool argument the model supplies that is later used, directly
or indirectly, to select *whose* data is read (that's what
`FORBIDDEN_ID_ARGS`/ownership verification exists to prevent); a tool that
returns an un-whitelisted raw row; a tool with its own bespoke rate limiter
instead of the shared per-user caps; a new tool with zero tests.
