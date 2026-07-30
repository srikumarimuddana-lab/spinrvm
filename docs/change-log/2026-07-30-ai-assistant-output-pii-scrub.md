# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-30 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | ai |
| PR / commit link | (this branch) |
| Related issue or gap ID | AI2, `ACTION_ITEMS.md` P3 AI-assistant backlog (2026-07-28 audit) |

## 1. Issue / gap identified

The AI assistant's chat-turn orchestrator (`backend/ai/orchestrator.py`) scrubs PII (`scrub_pii`) from the rider's own message before persisting it to `ai_messages`, but the assistant's reply was persisted and cached raw. The model can echo tool-result data verbatim in its reply (e.g. a driver's phone number surfaced by a dispatch tool), so a phone number, email, or coordinate pair that never appeared in the rider's own message could still land unscrubbed in stored conversation history and in the cross-user FAQ response cache — asymmetric with the user-side scrubbing and with Sentry's strict `send_default_pii=False` posture elsewhere in the codebase.

## 2. Root cause

`run_chat_turn()` calls `scrub_pii(user_message, keep_trip_pins=True)` once, for the user's message only (`orchestrator.py`, near the top of the function), then later persists `final_text` (the assistant's fully-streamed reply) via `conversations.append_message(..., "assistant", final_text, ...)` and — when cacheable — via `response_cache.store_cached(...)` with no scrubbing step in between. This was a one-sided application of the PII-scrub call, not a deliberate design choice (`conversations.py`'s own docstring already claimed both sides were scrubbed as the intended contract; the code just didn't do it for the assistant side).

## 3. Fix / remediation

- After the assistant's reply is fully streamed to the client (`final_text` fully assembled), scrub it (`scrub_pii(final_text, keep_trip_pins=True)`) into a new `stored_text` variable before it is written to `ai_messages` or the FAQ response cache. The client-facing stream is unaffected — the rider still sees the model's real reply this turn; only what gets *persisted and replayed later* changes.
- `keep_trip_pins=True` mirrors the user-side call, in case the model ever echoes a bracketed `[lat,lng]` trip-endpoint pair back (harmless either way — `scrub_pii` treats free-text coordinate pairs as PII regardless of this flag; only the bracketed app-generated format is exempted).
- Corrected `conversations.py`'s module docstring, which already (inaccurately) claimed both sides were scrubbed — the code now matches what the docstring always said the contract was.
- Updated `test_ai_pii.py::test_orchestrator_is_the_only_trip_pin_optin`, which enumerated every `scrub_pii(..., keep_trip_pins=True)` call site and asserted the result as a literal one-item list — a second legitimate call in the same file broke the list-equality check by coincidence, not because the test's actual intent (only `ai/orchestrator.py` may opt in, no other file) was violated. Changed the check to compare the *set* of files instead of a raw occurrence count, matching what the test's own docstring already said it verifies.
- Added a regression test (`test_assistant_text_is_pii_scrubbed_before_persistence`) that scripts a fake adapter reply containing a raw phone number, asserts the client still receives the raw text via the streamed `token` frames, and asserts the persisted `assistant` row has it redacted to `[PHONE]`.

## 4. Risk & impact on existing functionality

- **Blast radius:** `scrub_pii` is called in exactly one new place (`orchestrator.py`'s post-stream persistence step). Other callers (`routes/support.py`'s legacy driver chat, `utils/sentry_scrub.py`) are untouched. `conversations.append_message` and `response_cache.store_cached` are unchanged — they still receive a plain string, just now a scrubbed one from this one call site.
- **What could regress:** any assistant reply containing a phone number, email, GPS coordinate pair, or Canadian postal code in free text will now show `[PHONE]`/`[EMAIL]`/`[COORDS]`/`[POSTAL]` in *persisted* conversation history and in the FAQ cache — but the rider already saw the real value in the live stream this turn. Grepped `scrub_pii`'s pattern list: it targets only those four identifier classes via regex, not general prose (street addresses like "123 Main St" are untouched), so ordinary assistant replies (fares, ETAs, ride status, addresses) are unaffected.
- **FAQ response cache**: newly-stored cache entries are scrubbed going forward; entries already cached before this change (TTL-bounded, default `ai_faq_cache_ttl_seconds=3600`) age out naturally within an hour — not purged as part of this change, since they self-expire.
- **History replay**: `conversations.load_history()` feeds prior assistant messages back into the model as conversation context on the next turn. Since the *stored* copy is now scrubbed, that's what the model sees on replay too — consistent, and arguably an improvement (the model won't re-see a raw phone number it already emitted once).
- No ride, dispatch, payment, or auth logic touched.

## 5. User-experience effect

- **Not visible mid-session** — the rider/driver sees the exact same live-streamed reply this turn (scrubbing only applies to what's written to storage, after streaming completes). The only observable difference is if a user re-opens conversation history later and the assistant had echoed a phone number/email/coordinate/postal code in that turn — it now reads as a redaction token (`[PHONE]` etc.) instead of the raw value.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/ai/orchestrator.py` | Scrub `final_text` into `stored_text` before `append_message`/`store_cached` | Close the one-sided PII-scrub gap (AI2) |
| `backend/ai/conversations.py` | Docstring corrected to state both sides are scrubbed (now true) | Docstring previously claimed this but code didn't do it |
| `backend/tests/test_ai_orchestrator.py` | Added `test_assistant_text_is_pii_scrubbed_before_persistence` | Regression coverage for the fix |
| `backend/tests/test_ai_pii.py` | `test_orchestrator_is_the_only_trip_pin_optin` now checks the set of opt-in files, not a raw occurrence count | The old list-equality check broke on a second legitimate call site in the same file, contradicting its own stated intent |

## 7. Before / after

```python
# Before
final_text = "".join(all_text).strip()
...
assistant_row = await conversations.append_message(
    conversation, "assistant", final_text, ...
)
...
await response_cache.store_cached(audience, scrubbed, final_text, faq_cache_ttl)

# After
final_text = "".join(all_text).strip()
...
stored_text = scrub_pii(final_text, keep_trip_pins=True)
assistant_row = await conversations.append_message(
    conversation, "assistant", stored_text, ...
)
...
await response_cache.store_cached(audience, scrubbed, stored_text, faq_cache_ttl)
```

## 8. Rollback plan

Plain `git revert` — no schema, migration, or external-state change. Pure code-level scrubbing added to one persistence path.

## 9. Verification performed

- [x] `python3 -m py_compile` on both touched backend files: clean.
- [x] `ruff check` on all 4 touched files: all checks passed.
- [x] Ran the full backend test suite in an isolated venv (`pip install -r requirements.txt` succeeded there — the earlier "no venv" limitation was specific to the container's system Python having a conflicting system-installed PyYAML, not this repo's dependencies):
  - `tests/test_ai_orchestrator.py` + `test_ai_pii.py` + `test_ai_conversations.py` + `test_ai_response_cache.py`: **81/81 passed**, including the new regression test and the fixed pre-existing test.
  - Full `-k "ai"` sweep across the whole suite: **1593 passed, 2 skipped, 1 xfailed** (0 failures).
  - Full suite minus one pre-existing, unrelated failure (`test_compliance_reports.py::TestInsurancePeriodRows::test_joins_driver_name`, confirmed to fail identically on `main` before this change — a `format_report_timestamp` vs. raw-ISO-string assertion mismatch from the just-merged report-branding PR, unrelated to this diff): all passed.
- [x] Directly exercised `scrub_pii()` against the exact phone-number string used in the new test to confirm the regex behavior the test relies on.

## 10. What was NOT verified

- Not tested against a real LLM provider or real Supabase — verified via the existing `FakeAdapter`-based orchestrator test harness (mocked `conversations`/`response_cache`/tool execution), the same harness the rest of `test_ai_orchestrator.py` already uses.
- Did not audit whether any *other* AI surface (e.g. `routes/admin/ai_console.py`'s admin-actor path, which reuses `run_chat_turn`) needs a separate check — it goes through the same `run_chat_turn` function and therefore the same fix, but this was not independently re-verified with an admin-actor-specific test.
