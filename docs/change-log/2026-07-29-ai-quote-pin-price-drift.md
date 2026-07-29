# Change Impact & Risk Log — pin the quoted trip so the price does not move between quote and booking card

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude |
| Surface(s) | backend |
| Domain (Sentry tag) | ai |
| PR / commit link | branch `claude/ai-collapse-colocated-store-departments` |
| Related issue or gap ID | Rider report: Costco quote showed 15.1 km / CA$37.53; typing "book it" produced a booking card at CA$40.78 |

## 1. Issue / gap identified

A rider quoted at CA$37.53 was shown CA$40.78 on the booking card for what they believed was the same trip. Two different prices for one journey is a trust failure even though the card's number is the one that would actually be charged.

## 2. Root cause

`propose_ride_booking` computes **no price** — it builds a proposal payload, and `BookingProposalCard` prices whatever coordinates and vehicle it is handed (`rider-app/components/BookingProposalCard.tsx:66-79`).

The drift comes from what reaches that payload. Tool results are never persisted (`backend/ai/conversations.py:1-7`), so on the next turn the model retains no coordinates. Tapping a quote option is safe — that message embeds the priced `[lat,lng]`. **Typing** "book it" does not, and prompt rule 6 then requires the model to re-resolve the destination with `find_place` in that turn. A fresh Places lookup for a generic query ("Costco") is not guaranteed to return the same point: a different branch or a satellite listing (Gas Bar, Business Centre) can rank first, or the device bias point may have shifted.

Arithmetic corroborates a coordinate shift over the alternatives: +CA$3.25 ÷ 1.11 tax ≈ CA$2.93 of subtotal, which at the CA$1.50/km distance rate (`backend/services/fare_service.py:32-36`) is ≈1.95 km of extra trip. A one-tier surge step on this trip would have added roughly CA$7.22 — too large — so surge alone does not explain it.

A secondary contributor exists on the same path: if the model omits `vehicle_type_id`, the card falls back to `available[0]` (`rider-app/components/bookingProposal.ts:36-48`), and `backend/routes/fares.py:330-331` fetches `vehicle_types` with **no `order=`**, so that fallback is arbitrary rather than the quoted/recommended option.

## 3. Fix / remediation

Pin the priced trip per conversation and replay it into the next turn, giving the typed path the same guarantee the tapped path already has:

- `get_fare_quote` stores the trip it just priced (both endpoints' coordinates and addresses, recommended `vehicle_type_id`, total, promo) in Redis under `ai:quote:{conversation_id}`, TTL 15 min. Best-effort: a pin failure logs and degrades to today's behaviour, never breaks the quote.
- The orchestrator appends a `LAST QUOTE IN THIS CONVERSATION` block to the system prompt tail when a fresh pin exists.
- New prompt rule 6c: when the rider confirms in words, pass the pinned coordinates, addresses, vehicle id and total verbatim; do not call `find_place`/`get_rider_location`/`get_fare_quote` to rebuild it. If the rider names a *different* endpoint, ignore the pin and re-quote.

## 4. Risk & impact on existing functionality

- Blast radius: `backend/ai/tools_booking.py` (`get_fare_quote` only — `propose_ride_booking` untouched), `backend/ai/orchestrator.py` (prompt tail), `backend/ai/prompts.py` (rider core). No client, DB-schema, state-machine or fare-engine change. Charging is unchanged: the server still prices the ride at booking.
- **Booking the wrong destination is the risk this must not create.** Mitigations: rule 6c only applies when the rider confirms *the quoted trip* and explicitly defers to any newly named endpoint; the pin is conversation-scoped with a 15 min TTL; and the pre-existing `_dropoff_pair_refusal` guard (`tools_booking.py`) still cross-checks the dropoff label against its coordinates on every quote and proposal, so a misapplied pin is caught by an existing gate rather than reaching a card.
- Prompt-cache impact: the block is appended after the stable instruction text, the same placement rule the support-contact tail already uses, so the cached prefix is unaffected. The tail itself varies per turn once a quote exists.
- New Redis keys `ai:quote:*` (15 min TTL); falls back to the in-process dict when `REDIS_URL` is unset, in which case the pin is per-replica — a miss simply degrades to today's re-resolve behaviour.
- **Residual, deliberately not changed:** a genuine surge tick or promo expiry between quote and booking still moves the price, and the card still shows its "price updated" notice. Honouring a stale price would be a *pricing policy* change — with 0% commission the difference comes out of the driver's fare — so that decision is escalated, not made here. Likewise the unordered `vehicle_types` fetch is left alone: pinning the vehicle id fixes the AI path, and reordering a shared endpoint could shift other surfaces' defaults.

## 5. User-experience effect

Rider-facing, visible mid-session: confirming a quoted trip by typing now books the trip that was priced, at that price, instead of a re-resolved approximation. No UI change.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `backend/ai/tools_booking.py` | `_pin_quote` / `load_pinned_quote` / `_QUOTE_PIN_TTL_SECONDS`; `get_fare_quote` pins the priced trip | Carry the quote across turns |
| `backend/ai/orchestrator.py` | `_pinned_quote_context` appended to the system prompt tail | Hand the model the trip it priced |
| `backend/ai/prompts.py` | Rule 6c | Make the model use it, and only for the same trip |
| `backend/tests/test_ai_tools_booking.py` | Pin round-trip + failure-tolerance tests | Pin the contract |
| `backend/tests/test_ai_orchestrator.py` | Injection, no-pin, and malformed-pin tests | Pin the prompt wiring |

## 7. Before/after

Rider types "book it" after a CA$37.53 quote:

```
before: model has no coordinates → re-runs find_place("Costco") → may resolve a
        different point → card prices that point → CA$40.78
after:  prompt carries "LAST QUOTE …: dropoff Costco, Regina [50.40790,-104.65010],
        vehicle_type_id vt-economy, quoted_total 37.53" → model books that exact
        trip → card prices the same point → CA$37.53
```

## 8. Rollback plan

`git revert` — the pin is an ephemeral Redis key with a 15 min TTL and no schema; reverting stops writes and the keys expire on their own. Behaviour returns to re-resolving. `ai_assistant_enabled=false` remains the hard kill switch.

## 9. Verification performed

- `pytest -k ai_` — 398 passed, 2 skipped. New coverage: pin round-trip and per-conversation isolation; pin survives Redis errors and malformed JSON; the block reaches the system prompt with full 5-decimal coordinates; absent and malformed pins inject nothing.
- `ruff check backend/ai/ …` — clean.
- Full backend suite run for the co-located-departments commit on this branch (5,390 passed); the pin commit's suites re-run as above.

## 10. What was NOT verified

- **The exact upstream mechanism is inferred, not observed.** I could not call Google Places from this environment, so I could not confirm that the rider's second "Costco" lookup returned a different point. The ~1.95 km arithmetic fits a coordinate shift and rules surge out as a sole cause, but the vehicle-fallback path (`available[0]`) could also contribute. The pin removes both by construction, so the fix holds either way.
- **No LLM-in-the-loop test exists in this repo**, so "the model actually obeys rule 6c and stops re-resolving" is verified by prompt-text assertions and reasoning, not by a live model run. This is the main thing to confirm in staging: quote a trip, type "book it", and check the card total matches the quote and that no second `find_place` appears in the tool trace.
- Redis-backed pinning exercised with fakes, not a live Redis; no multi-replica test.
- Not tested against live Supabase.
