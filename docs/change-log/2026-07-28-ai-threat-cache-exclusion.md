# Change Impact & Risk Log — threat-flagged AI turns excluded from the cross-user FAQ cache

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude (AI guardrail hardening, commit 9 of series) |
| Surface(s) | backend |
| Domain (Sentry tag) | ai |
| PR / commit link | branch `claude/rider-ai-location-selection-yn0mem` |
| Related issue or gap ID | Guardrail audit §6/§8.7: threat-flagged turns were still cache-eligible |

## 1. Issue / gap identified

A first-turn message that trips the prompt-injection tripwire (`scan_message`) could still be served from — and stored into — the cross-user FAQ response cache, replaying a potentially crafted answer verbatim to other users who ask the same normalized question.

## 2. Root cause

`cache_eligible` considered only `faq_cache_enabled`, `admin_actor_id`, and `prior_turns`; the `threat_hit` computed a few lines earlier was used only for security-event logging.

## 3. Fix / remediation

`cache_eligible` now also requires `not threat_hit`. Detection remains log-only for the turn itself (the tool layer blocks actual harm); the turn completes normally, it just never touches the shared cache.

## 4. Risk & impact on existing functionality

- Blast radius: one boolean in `run_chat_turn`; cache hit/store behavior for non-flagged turns is unchanged (pinned by the existing cache suite).
- False-positive tripwire hits (legitimate messages that look like injection) now skip the cache — they still get a normal LLM answer, just uncached. Cost: one extra LLM call per false positive; negligible at the FAQ cache's traffic share.

## 5. User-experience effect

None visible.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `backend/ai/orchestrator.py` | `cache_eligible &= not threat_hit` | Keep flagged turns out of the shared cache |
| `backend/tests/test_ai_response_cache.py` | `test_threat_flagged_turn_bypasses_cache` | Regression pin (no get, no store, turn completes) |

## 7. Before/after

```python
# before
cache_eligible = faq_cache_enabled and admin_actor_id is None and prior_turns == 0
# after
cache_eligible = faq_cache_enabled and admin_actor_id is None and prior_turns == 0 and not threat_hit
```

## 8. Rollback plan

`git revert` — stateless; also `ai_faq_cache_enabled=false` in app_settings disables the whole cache without redeploy.

## 9. Verification performed

- `pytest backend/tests/test_ai_response_cache.py backend/tests/test_ai_orchestrator.py` — 36 passed.

## 10. What was NOT verified

- Tripwire pattern coverage itself is unchanged and untested here (existing `test_ai_threat.py` scope).
