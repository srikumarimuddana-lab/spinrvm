# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-07 |
| Author | Claude (session 01JwSyq7NYGkg4ReSHBZDqva) |
| Surface(s) | backend |
| Domain (Sentry tag) | ai |
| PR / commit link | PR #5082 |
| Related issue or gap ID | Found while investigating a CI failure in `tests/test_ai_tools_support.py::TestSearchFaqsPublicWeb` |

## 1. Issue / gap identified

Every tool call made by the public, anonymous spinr.ca website assistant ("web" audience) has been silently blocked as `{"error": "not authorized"}` since that feature shipped — including `search_faqs`, the tool the 2026-09-06 audience-scoping fix (`docs/change-log/2026-09-06-public-faq-audience-scope.md`) was specifically about. The website assistant has never actually been able to answer a question from its FAQ corpus.

## 2. Root cause

`backend/ai/tools.py`'s `_execute_tool_inner` has an unconditional identity guard: `if not user.get("id"): return {"error": "not authorized"}, False`. `backend/ai/public_assistant.py` deliberately builds its `tool_user` dict with **no `id` key at all** for anonymous website visitors (its own comment: *"It carries NO identity — deliberately no 'id' key, so any handler that reached for one would raise loudly rather than read someone else's row by accident"*). The guard never special-cased the `"web"` audience, so it fired on every single web-audience tool call, not just ones that actually needed an id. Confirmed via a real pytest run (not just the CI summary line): the blocked call also fires `record_security_event(..., event_type="tool_blocked", severity="critical")` on every occurrence, meaning every ordinary anonymous website question has also been generating a false-positive **critical** security event.

## 3. Fix / remediation

`_execute_tool_inner`'s identity guard now skips the `user_id` requirement specifically when `audience == "web"`. This is safe because a tool is only reachable under `audience="web"` at all if its own registration already opted into that (`audience not in spec.audiences` is checked earlier in the same function) — currently only `search_faqs` and `get_company_info`. Rider/driver calls are completely unaffected; the guard still fails closed for them exactly as before.

Added a companion registration-time invariant in `register()`: a tool cannot be both web-audience and declare `owned_id_args`, since ownership verification needs a real `user_id` the web audience never has. This doesn't fire today (neither web tool declares owned id args) — it's a guard against a future silent regression, not a fix for an existing bug.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to the "web" audience path.** Grepped every real (non-test) caller of `execute_tool`: `ai/orchestrator.py` (in-app rider/driver chat) and `ai/mcp_server.py` (the authenticated `/mcp` surface, audience resolved from a signed-in user) never pass `audience="web"` — only `ai/public_assistant.py` does. Rider/driver tool calls go through the exact same `if not user_id and audience != "web"` line and fail closed identically to before.
- Only 2 of the ~20+ registered tools have `"web"` in their `audiences` (`search_faqs`, `get_company_info`) — confirmed by `tests/test_ai_public_assistant.py::test_no_account_or_booking_tool_opts_into_the_web_audience`, which already asserts no other tool leaks into the web allowlist and is unaffected by this change.
- No ride state, money, or wallet path touched.
- Security-console side effect: this fix also **stops** the flood of false "critical" `tool_blocked` events for ordinary anonymous website chat traffic going forward. It does not retroactively clean up already-recorded events — `ai_security_events` is an audit table, out of scope for a code fix.

## 5. User-experience effect

- **Rider / driver in-app assistant: no change.**
- **Anonymous spinr.ca website visitor: this is the actual fix.** Before, every question that needed a tool (FAQ lookup, company contact info) silently failed and the assistant could only answer from its own training/prompt, never the real help-centre content. After, `search_faqs`/`get_company_info` actually run. Not visible mid-session to an existing app user — this is the public marketing site only.
- **Internal admin (security console):** will stop seeing a "critical" `tool_blocked` event for every anonymous website chat message.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/ai/tools.py` | `_execute_tool_inner`'s identity guard now exempts `audience == "web"`; `register()` gained a registration-time check rejecting a web-audience tool with `owned_id_args` | Fix the guard to match the "web" audience's documented anonymous-by-design contract, and prevent a future silent regression of the same shape |

## 7. Before / after

```python
# Before
call_args = args if isinstance(args, dict) else {}

# Fail closed: no tool runs without an authenticated identity to scope to.
user_id = (user or {}).get("id")
if not user_id:
    logger.error("ai tool blocked: no authenticated user id", extra={"tool": name})
    return {"error": "not authorized"}, False
```

```python
# After
call_args = args if isinstance(args, dict) else {}

user_id = (user or {}).get("id")
if not user_id and audience != "web":
    logger.error("ai tool blocked: no authenticated user id", extra={"tool": name})
    return {"error": "not authorized"}, False
```

## 8. Rollback plan

Pure code change, no data/schema/flag coupling — `git revert` is a complete rollback. No migration, no `app_settings` value, no wallet/ride-state involved. Reverting restores the previous (broken) behavior exactly, with no data cleanup needed either way.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_ai_tools_support.py` (42/42 passed, was 7 failed/35 passed before the fix), the full AI test surface (`-k "ai_tool or ai_public_assistant or ai_orchestrator or ai_pii or ai_security or test_ai_"`, 561 passed/2 skipped, 0 failed), `ruff check`/`ruff format --check` on the changed file (clean).
- [ ] Manual repro steps followed in staging — **not done**, no staging access from this session; verified via unit tests only (the same boundary the rest of this session's work has been under).
- [x] Blast-radius grep performed: every real (non-test) caller of `execute_tool` (`ai/orchestrator.py`, `ai/mcp_server.py`, `ai/public_assistant.py`) — see §4.
- [x] Reviewed against relevant CLAUDE.md convention(s): PIPEDA (no PII in the audit/security-event path, unchanged), observability (Sentry/security-event tagging behavior improves, doesn't regress).
- [ ] Feature-flagged — not applicable; this restores documented intended behavior for a surface that has never worked, not a new user-visible feature being rolled out.

## What was NOT verified

Not tested against a live spinr.ca chat session or real Anthropic/OpenAI provider call — verified at the `execute_tool` dispatch layer via unit tests with mocked handlers, not an end-to-end browser/API smoke test. The `ai_security_events` audit table was not queried live to confirm the volume of pre-existing false-positive `tool_blocked` events this bug produced — no production Supabase access from this session (see PR #5082's earlier migration-audit context for the same access-scoping gap).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed (grepped every real caller)
- [x] No silent behavior change to an already-shipped *working* flow — the web assistant's tool calls have never worked, so this is a fix to dead-on-arrival functionality, not a change to something rider/driver-facing that was already relied upon
