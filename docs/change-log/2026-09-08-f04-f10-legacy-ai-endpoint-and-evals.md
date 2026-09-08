# Change Impact & Risk Log — F04 (legacy AI endpoint) + F10 (eval route contract)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | Claude Code (agent), for the PR #5138 remediation plan |
| Surface(s) | backend |
| Domain (Sentry tag) | ai |
| PR / commit link | branch `claude/pr-5138-implementation-27zn2l` |
| Related issue or gap ID | F04 (order 2) + F10 (order 5), `docs/security/2026-09-08-ai-security-assessment.md` (PR #5138) |

## 1. Issue / gap identified

**F04** — `POST /api/v1/support/chat` was still mounted and called Gemini
directly via the deprecated `google.generativeai` SDK. It sat outside every AI
control added since it was written: it never consulted `ai_assistant_enabled`
(the "stop sending user data to the third-party LLM provider" incident lever),
never went through the provider factory, never counted against the per-user AI
daily quota, had no message length bound at all, and called the **synchronous**
`generate_content` inside an async route — so a slow provider blocked that
worker's event loop.

**F10** — `promptfooconfig.yaml` posted to `{base}/ai/chat` while the app mounts
the router at `/api/v1`. With `SPINR_EVAL_BACKEND_URL` set to the server root as
the README instructs, every request 404'd. That is worse than "the suite doesn't
run": `transformResponse: "json.reply"` yielded `undefined`, so every
`not-icontains` assertion passed trivially. **The adversarial suite reported
green while testing nothing.**

## 2. Root cause

**F04** — the endpoint predates the central AI engine and was never migrated
when the controls were built around the new path. Nothing pointed at it, so
nothing forced the question. Its second copy of the system prompt is the same
story: it had already drifted to state a fabricated "2-3 business days" payout
timeline and a "platform service fee" that contradicts Spinr's 0%-commission
model.

**F10** — the config was written against a base URL that included the API
prefix; the README documented the server root. Neither is wrong alone, and no
assertion existed that could tell the difference, because the suite's assertions
are all *negative* ("the reply must not contain X"). A negative-only suite
cannot distinguish a hardened assistant from an unreachable one.

## 3. Fix / remediation

**F04** — `/support/chat` is now a thin compatibility shim delegating to
`ai.orchestrator.run_chat_turn`, the same engine `/api/v1/ai/chat` uses. It
inherits the global switch, provider factory, daily quota, rate limit, PII
scrubbing and async provider handling. `message` is bounded to 1000 chars,
matching `AiChatRequest`. `driver_id` is still accepted and still ignored. The
legacy `{"reply": str}` response shape is unchanged.

**Kept rather than deleted, deliberately.** No client in this repo calls it —
the shared help-centre UI uses `/ai/chat`
(`shared/components/SupportScreen.tsx`) — so deletion was the tempting option
and is what "remove the unused route" in the assessment suggests first. But an
older installed build in the field could still call it, and a 404 there strands
a driver mid-conversation during live app testing. Delegating closes the entire
bypass with no breaking change, so there is no reason to take the risk.

The orphaned `SYSTEM_PROMPT` is removed with the code that used it. Its content
assertions are **moved**, not dropped, to `tests/test_ai_prompts_policy.py`
pointed at `ai/prompts.py` — the prompt the endpoint now actually uses.

**F10** — URL corrected to `{base}/api/v1/ai/chat`; README states the variable
is the server root. Two suite-integrity guards added to `defaultTest`:
`transformResponse` now emits `SPINR_EVAL_NO_REPLY: <body>` instead of
`undefined` for a non-`{reply}` response, and `defaultTest` asserts both that
this marker is absent and that output is a non-empty string. A transport-level
failure now fails loudly instead of passing silently.

## 4. Risk & impact on existing functionality

**Blast radius: one endpoint, plus the eval harness (not runtime).**

Greps performed:

- `grep -rn "support/chat"` across the repo (excluding `node_modules`) → **only
  `backend/routes/support.py` itself**. No rider-app, driver-app, shared or
  admin-dashboard caller.
- `grep -rn "ai/chat\|/api/v1/ai"` in the apps → `shared/components/SupportScreen.tsx`
  and `rider-app/utils/aiChat.ts` both use the central route. Confirms the
  assessment's "the current help-centre UI already uses the newer endpoint".
- `grep -rn "SYSTEM_PROMPT\|scrub_pii\|FALLBACK_REPLY"` → `SYSTEM_PROMPT` had
  one consumer (the deleted Gemini call) and 4 test assertions (moved);
  `scrub_pii` had one (same); `FALLBACK_REPLY` is still used by the new shim.
- `/support/escalate` in the same module is **untouched** — it does not use the
  AI path.

Regressions considered:

- **Behaviour change for any live caller.** Real, and the main risk here. A
  caller that previously got a Gemini answer will now get a central-engine
  answer, or `FALLBACK_REPLY` when `ai_assistant_enabled` is off — where before
  it answered regardless of that switch. **That is the finding, not a
  side-effect**: the endpoint answering while the kill switch is off is exactly
  the bypass being closed. It does mean flipping the global switch off now takes
  this endpoint down too, which is the intended behaviour.
- **A caller sending a >1000-char message now gets a 422** where it previously
  got an answer. Judged correct (the central route has always had this bound)
  and low-risk (no known caller), but it is a hard behaviour change and is
  called out here rather than left implicit.
- **The shim now runs tools.** `run_chat_turn` at rider/driver audience has
  access to the full authenticated tool set, where the old endpoint was a
  prompt-only chat. Every one of those tools is owner-scoped by `execute_tool`
  and injects the authenticated user, so this does not widen data access — but
  it does mean this endpoint now has a larger functional surface than before.
- **Rate limiting:** the shim now shares `ai_chat_limit` (10/min per user) with
  `/ai/chat`. A caller using both would consume one budget. No known such caller.
- **F10 changes no runtime code** — config and docs only. The harness is
  deliberately outside CI (`CLAUDE.md` PR-review section), so no gate changes.

## 5. User-experience effect

- **Rider / driver on a current build:** none. They use `/ai/chat`.
- **A driver on an older build that still calls `/support/chat`:** answers now
  come from the central engine — better answers (real tools, current prompts,
  no fabricated payout timeline), and a `FALLBACK_REPLY` when AI is globally
  disabled. Not visible mid-ride; this is a help-centre surface.
- **Internal:** the endpoint now appears in AI usage/quota accounting where it
  previously did not, so per-user AI counts may tick up slightly if anything
  still calls it.
- No copy change. `FALLBACK_REPLY` is unchanged.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/support.py` | `support_chat` delegates to `run_chat_turn`; bounded `message`; `ai_chat_limit` applied; Gemini import, `scrub_pii` import and `SYSTEM_PROMPT` removed; module docstring rewritten. | F04. |
| `backend/tests/test_routes_support_coverage.py` | Gemini-SDK tests deleted (the code path is gone — they would test nothing); 8 new tests for the shim's contract; docstring and patch helper updated. | Cover what exists now. |
| `backend/tests/test_ai_prompts_policy.py` | **New.** The moved prompt-policy assertions, parametrised across all three personas. | Keep the guard when the duplicated prompt goes. |
| `backend/evals/promptfoo/promptfooconfig.yaml` | URL → `/api/v1/ai/chat`; `transformResponse` surfaces non-`{reply}` bodies; 2 integrity guards in `defaultTest`. | F10. |
| `backend/evals/promptfoo/README.md` | Base-URL semantics stated; the false-green failure mode documented. | F10. |

## 7. Before / after

```python
# Before — routes/support.py
import google.generativeai as genai            # deprecated SDK
api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
if not api_key:
    return {"reply": FALLBACK_REPLY}
genai.configure(api_key=api_key)
model = genai.GenerativeModel("gemini-1.5-flash", system_instruction=SYSTEM_PROMPT)
response = model.generate_content(scrubbed_message)   # SYNC call in an async route
return {"reply": response.text.strip() or FALLBACK_REPLY}
# no ai_assistant_enabled check, no quota, no length bound
```

```python
# After
async for name, payload in run_chat_turn(
    user=current_user, conversation_id=None,
    user_message=req.message,
    audience="driver" if current_user.get("is_driver") else "rider",
):
    if name == "token":
        reply_parts.append(payload.get("text", ""))
    elif name == "error":
        return {"reply": FALLBACK_REPLY}
return {"reply": "".join(reply_parts).strip() or FALLBACK_REPLY}
```

```yaml
# Before — promptfooconfig.yaml  (app mounts /api/v1; this 404s, suite passes anyway)
url: "{{env.SPINR_EVAL_BACKEND_URL}}/ai/chat"
transformResponse: "json.reply"
```

```yaml
# After
url: "{{env.SPINR_EVAL_BACKEND_URL}}/api/v1/ai/chat"
transformResponse: "json && json.reply !== undefined ? json.reply : 'SPINR_EVAL_NO_REPLY: ' + JSON.stringify(json)"
# + defaultTest guards: not-icontains SPINR_EVAL_NO_REPLY, and non-empty output
```

## 8. Rollback plan

**F04** — `ai_assistant_enabled` false in `app_settings` (admin dashboard, no
redeploy) makes the endpoint return `FALLBACK_REPLY`, which is the pre-existing
"provider unavailable" behaviour an old client already handles. That is the
operational lever.

Reverting the *code* restores the direct Gemini call and with it the bypass, so
it should be a last resort. If the shim misbehaves, the correct move is the
switch above, then diagnose — not a revert.

No migration, no schema change, no data written. Nothing to unwind at the data
level.

**F10** — config and docs only; `git revert` is complete and consequence-free.
The harness is not in CI, so a bad config cannot block a merge.

## 9. Verification performed

- [x] **Blast-radius grep performed** — `support/chat` across the whole repo
      (zero callers outside the route itself), plus the apps' actual AI
      endpoints, plus every consumer of the symbols removed. Listed in §4.
- [x] **Moved prompt assertions verified against the real prompts** — extracted
      `_RIDER_CORE`/`_DRIVER_CORE`/`_WEB_CORE` from `ai/prompts.py` with `ast`
      and ran all of `test_ai_prompts_policy.py`'s conditions against them:
      **all pass**, so the moved guard is green on current content, not
      aspirational.
- [x] **promptfoo config parses** and resolves to
      `{{env.SPINR_EVAL_BACKEND_URL}}/api/v1/ai/chat` with 6 `defaultTest`
      assertions over 9 test cases (checked with `yaml.safe_load`).
- [x] `ruff check` + `ruff format --check` clean on all changed Python files.
- [x] Reviewed against CLAUDE.md: dual-import pattern preserved in
      `routes/support.py`; the error path logs at `error` with `domain`/`surface`
      tags and does not swallow silently; the blanket-fallback design is
      retained deliberately and justified in the docstring.

## 10. What was NOT verified

- **The pytest suite was not run.** PyPI is unreachable from this environment
  (gateway 403 on `pypi.org`), so backend dependencies cannot be installed. The
  8 new shim tests and 6 moved prompt-policy tests are **unrun** and must go
  green in CI before merge.
- **The shim was never exercised against a real `run_chat_turn`.** Its tests
  patch `run_chat_turn` with a fake frame generator. The frame contract
  (`token` / `error` names and payload shapes) was read from
  `routes/ai.py::ai_chat`'s own non-streaming drain, not observed at runtime.
  If that contract is wrong, the shim returns `FALLBACK_REPLY` for everything —
  degraded, not dangerous, but it would be a silent functional regression.
  **A single manual call to `/api/v1/support/chat` against a dev backend is the
  cheap check owed here.**
- **The promptfoo suite was not run.** It needs a live backend, a real test JWT
  and paid model calls — none available here, and it is deliberately outside CI.
  So F10 is fixed *as configuration*; the assessment's actual acceptance
  evidence ("a real-provider attack suite reaches the correct endpoint and
  records both positive and negative outcomes") is **not** met by this change
  and still requires a human to run it.
- **`promptfoo`'s exact `transformResponse` signature was not verified against
  the installed version** (npm registry is also unreachable here). The
  expression only uses `json`, which is the first argument in every version of
  the HTTP provider, so it should be version-safe — but the first real run
  should confirm the guards behave as intended rather than erroring themselves.
- **The deprecated `google.generativeai` SDK is still used elsewhere** —
  `ai/providers/gemini_adapter.py` and the Gemini embeddings path. F04's
  migration recommendation is therefore only partly done: the legacy *route* no
  longer touches it, but the adapter does. That is separate, larger work and is
  **not** in this change.
- No load or latency measurement of the shim.
