# Change Impact & Risk Log — AI tool-result truncation preserves guardrail keys

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude (AI guardrail hardening, commit 8 of series) |
| Surface(s) | backend |
| Domain (Sentry tag) | ai |
| PR / commit link | branch `claude/rider-ai-location-selection-yn0mem` |
| Related issue or gap ID | Guardrail audit §8.3: oversized results lose their "Do NOT quote" note while the client card survives |

## 1. Issue / gap identified

Tool results over 4000 chars were replaced wholesale with `{"_truncated": True, "preview": ...}`. `get_fare_quote` appends its guardrail `note` last, so a large multi-vehicle quote could reach the model without the instruction that made it safe — while the `_client_action` card still rendered to the rider.

## 2. Root cause

`_cap_result` preserved only `_client_action` through truncation; `note`, `needs_confirmation`, `needs_correction`, `imprecise_address`, `error` were dropped with the rest of the payload.

## 3. Fix / remediation

Truncation now re-attaches those five guardrail keys alongside `_truncated`/`preview`.

## 4. Risk & impact on existing functionality

- Blast radius: `_cap_result` only; callers are `execute_tool` (chat + /mcp). Sub-4000-char results are byte-identical to before.
- Truncated results grow by the size of the preserved keys (a `note` is a few hundred chars) — bounded, and far smaller than the safety cost of losing it.
- No tool handler or client contract changes.

## 5. User-experience effect

None directly; prevents a rare model-behavior hazard (acting on a quote it was told to refuse).

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `backend/ai/tools.py` | `_GUARDRAIL_KEYS` preserved through truncation | Never truncate away refusal instructions |
| `backend/tests/test_ai_tools_core.py` | `test_guardrail_keys_survive_truncation` | Regression pin |

## 7. Before/after

```python
# before
result = {"_truncated": True, "preview": serialized[:TOOL_RESULT_MAX_CHARS]}
# after
preserved = {k: result[k] for k in _GUARDRAIL_KEYS if isinstance(result, dict) and k in result}
result = {"_truncated": True, "preview": serialized[:TOOL_RESULT_MAX_CHARS], **preserved}
```

## 8. Rollback plan

`git revert` — stateless, server-internal.

## 9. Verification performed

- `pytest backend/tests/test_ai_tools_core.py` — 22 passed (existing truncation tests unchanged).

## 10. What was NOT verified

- No live-provider run demonstrating the model-behavior difference; the fix is structural.
