# Change Impact & Risk Log — FAQ search: keyword queries + one re-search

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code session (requested by mkkreddy52) |
| Surface(s) | backend (affects answers in rider-app, driver-app and spinr.ca AI chat) |
| Domain (Sentry tag) | ai |
| PR / commit link | branch `claude/intelligent-goodall-42w02z` |
| Related issue or gap ID | FAQ AI accuracy test, 2026-09-25 |

## 1. Issue / gap identified

For about a third of realistic questions the FAQ assistant is not handed the right help-centre article, so it either says it can't find an answer the help centre has, or leans on an unrelated article.

## 2. Root cause

Production runs lexical FAQ ranking only (`ai_faq_semantic_enabled=false`, 0 of 60 FAQs embedded). `_lexical_results` scores shared words with no stop-word filtering, so when the model passes the user's full sentence, filler ("how do I", "can you") dominates and the topic word loses. The tool schema only said "Keywords from the rider's question." Measured with `backend/tests/test_ai_faq_retrieval_eval.py` against a snapshot of the 60 live FAQs:

| Query style | Right FAQ #1 | Right FAQ in top 5 |
|---|---|---|
| User's raw wording | 23/48 (48%) | 32/48 (67%) |
| 2–6 topic keywords | 43/48 (90%) | 48/48 (100%) |

Stop-word filtering and a minimum score were tried and made ranking WORSE (top-5 fell to 65% / 50%), so the ranker itself is left unchanged.

## 3. Fix / remediation

Prompt-level only, in the `search_faqs` ToolSpec (`backend/ai/tools_support.py`):
- `query` description now asks for 2–6 topic keywords in help-centre terms, dropping filler words (and no longer says "rider's" — the tool also serves drivers and the website).
- Tool description tells the model results may be unrelated, to check relevance, and to re-search ONCE with different help-centre terms before saying nothing matches.

## 4. Risk & impact on existing functionality

- Consumers of this ToolSpec: the in-app orchestrator (rider + driver), the public website assistant (`ai/public_assistant.py`), and the MCP server (`ai/mcp_server.py` exposes the same description). No code or test depends on the description text.
- Iteration budget: a re-search adds one tool round. In-app cap is `ai_max_tool_iterations` = 6; website cap is `MAX_TOOL_ITERATIONS = 3` (search → re-search → answer fits exactly). If the model re-searches AND calls another tool on the website, it could hit the cap and log a warning.
- Latency: a miss now costs one extra LLM round trip (~1–2 s) instead of an immediate "not found". Hits are unchanged.
- Cost: at most one extra LLM call per missed search.
- Response cache (`ai_faq_cache_enabled`) is off in production; unaffected either way (keyed on the user's question, not the tool query).

## 5. User-experience effect

Riders, drivers and website visitors should get the correct FAQ answer more often, and fewer "I couldn't find that" replies for questions the help centre covers. Some previously-unanswered questions take ~1–2 s longer. No copy change. Takes effect on the next message after deploy, including mid-conversation.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| backend/ai/tools_support.py | `search_faqs` description + `query` schema description | Steer the model to keyword queries and one re-search |

## 7. Before / after

```
# Before
"query": {"description": "Keywords from the rider's question."}
```

```
# After
"query": {"description": "2-6 topic keywords, not the user's full sentence. Drop filler words
          (how, can, do, I, my, what). Prefer the terms a help centre would use, ..."}
+ tool description: check relevance; if none fits, search ONCE more with different terms.
```

## 8. Rollback plan

No flag gates tool descriptions, so rollback is a revert + redeploy (text-only, no data written). To stop all AI answers immediately without a deploy: `ai_assistant_enabled = false` (Admin → Settings → AI Assistant).

## 9. Verification performed

- [x] Offline retrieval eval (numbers above), run through a stand-in runner that loads the real `_lexical_results` from source.
- [x] ruff check + format clean; module parses.
- [x] Blast-radius grep: every `.description` consumer (tools.py, mcp_server.py) and every test asserting on descriptions — none pin this text.
- [ ] **pytest not executed** — the session sandbox could not install backend dependencies (PyPI blocked). CI must run the suite.

## What was NOT verified

- **That gpt-5.4 actually follows the new instruction.** The 48/48 figure measures ranking GIVEN keyword queries; whether the live model produces such queries was not observed (production API and OpenAI are unreachable from this sandbox). The keyword queries in the eval were written by the same session that knows the corpus, so they are likely better than a model's average — treat 100% as an upper bound.
- No live before/after on real conversations. Next step: re-run the question set against staging/production chat and compare answers.
