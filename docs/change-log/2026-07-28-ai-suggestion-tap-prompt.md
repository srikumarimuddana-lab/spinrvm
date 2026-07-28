# Change Impact & Risk Log — AI prompt: trust tapped-suggestion coordinates; never re-ask an address twice

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude (AI location-selection loop fix, commit 1 of series) |
| Surface(s) | backend |
| Domain (Sentry tag) | ai |
| PR / commit link | branch `claude/rider-ai-location-selection-yn0mem` |
| Related issue or gap ID | Rider report: AI keeps asking for "exact street address" after tapping a Canadian Tire suggestion (655 Albert St, Regina) |

## 1. Issue / gap identified

After tapping a location-suggestion card in the AI chat, the assistant loops forever on "check the exact street address and postal code" and never quotes — the reported rider-facing dead end.

## 2. Root cause

The suggestion-card tap sends only address text (fixed separately in commits 3–4). Because conversation history is text-only and prompt rule 6 orders the model to re-resolve every endpoint with `find_place`, the model re-geocodes the tapped address each turn. A street address Google can't pin (non-ROOFTOP) re-trips the `imprecise_address` gate (`backend/ai/tools_booking.py:584-594`) on every retry — same input, same refusal, deterministic loop. Rule 6 additionally scoped bracket-trust to "a tapped quote card" only, so even a coordinate-carrying tap message would be distrusted without this prompt change — which is why this commit must deploy **before** the client commits.

## 3. Fix / remediation

Two additions to the rider system prompt (`backend/ai/prompts.py`):
- Rule 6b: a rider message shaped `Use <address> [lat,lng] as my pickup/dropoff` is the find_place candidate the rider chose — pass coordinates and address verbatim to `get_fare_quote`/`propose_ride_booking`, never re-run `find_place` on it; `imprecise_address` does not apply to a rider-chosen candidate. Inserted before the "most recent message only" sentence so the recency constraint still governs it.
- Rule 8b: never ask the rider to fix the same address twice — on a repeat, use tapped/pinned coordinates verbatim or call `request_map_pin`; do not repeat the question. This alone breaks the loop for **old app builds** that still send text-only taps.

## 4. Risk & impact on existing functionality

- Blast radius: single file, `_RIDER_CORE` only. Consumers of `build_system_prompt`: `backend/ai/orchestrator.py` (rider + driver audiences — driver core untouched) and prompt tests. No routes, DB, or state machine touched.
- The stale-coordinate protections are unchanged: bracket-trust still applies only to the MOST RECENT message, and `_dropoff_pair_refusal` / `_reconcile_pickup` server-side gates still validate every quote/proposal pair. A tapped candidate's coordinates pass those gates because the label geocode is biased at the tapped pin (~0 km).
- Residual risk: a rider-chosen approximate candidate can be a few dozen metres off the true building for rare non-ROOFTOP geocodes. Accepted per product decision (tap = confirmation, same trust model as the quote card); the booking card still shows the address for review before Confirm.
- Prompt text is provider-cache-stable; this edit invalidates the provider prompt cache once (expected, harmless).

## 5. User-experience effect

Rider-facing, visible mid-session: the assistant stops re-asking for an address the rider already picked, and quotes instead. No visual/UI change; behavior change is in assistant replies only.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `backend/ai/prompts.py` | Rule 6b: trust tapped-suggestion `[lat,lng]`; rule 8b: never re-ask the same address twice | Break the clarification loop |
| `backend/tests/test_ai_tools_booking.py` | New `test_rider_prompt_trusts_tapped_suggestion_coordinates` | Regression-pin the new rules and their ordering |
| `backend/tests/test_ai_pii.py` | New `test_tapped_suggestion_message_keeps_coordinates` | Prove the new tap-message format survives `scrub_pii(keep_trip_pins=True)` |

## 7. Before/after

Before (rule 8b tail):
```
... Do not set confirm_same_location unless they explicitly insist the distance really is correct.
```
After:
```
... Do not set confirm_same_location unless they explicitly insist the distance really is
correct. Never ask the rider to fix the same address twice: if you already asked once, or their
most recent message carries bracketed [lat,lng] coordinates (a tapped suggestion or a dropped
pin — see 6b), use those coordinates verbatim or call request_map_pin — do not repeat the question.
```
Rule 6b gains (before the "Bracketed coordinates count ONLY in the rider's most recent message" sentence):
```
The same applies when the rider taps one of your location suggestions — their message reads
"Use <address> [lat,lng] as my pickup/dropoff": those coordinates are the find_place candidate
THEY chose, so pass them and the accompanying address verbatim to get_fare_quote and
propose_ride_booking, and never re-run find_place on that address — imprecise_address does not
apply to a rider-chosen candidate.
```

## 8. Rollback plan

`git revert` of this commit and redeploy backend — prompt-only, no data or client coupling; both prompt versions handle both message formats. No flag needed (prompt text is not in `app_settings`).

## 9. Verification performed

- `pytest backend/tests/test_ai_pii.py backend/tests/test_ai_tools_booking.py` (prompt + PII suites) — run locally in this session.
- No production build applies (backend-only).

## 10. What was NOT verified

- Actual LLM obedience to the new rules — this repo has no LLM-in-the-loop test harness; prompt coverage is text-assertion only. Manual staging verification via the admin AI console is the planned end-to-end check after the client commits land.
- Not tested against live Supabase or a live model provider; unit suites use mocks.
