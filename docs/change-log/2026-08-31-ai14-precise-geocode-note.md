# Change Impact & Risk Log — AI14(b): precise-geocode note on tapped location suggestions

**Date:** 2026-08-31
**Surface:** Booking-adjacent AI assistant (`backend/ai/`, `shared/`, `rider-app/`)
**Status:** Documenting an already-shipped change. The code, prompt, and unit
tests described below were merged in commit `139e607`
(PR #4777, "fix: AI daily-cap bounded fallback, approximate-geocode warning,
corporate policy-edit confirmation, scheduled-rides test flake (#3742, #2683)")
on 2026-08-31, under decision label **AI14(b)**. No new implementation code
was written by this session — this entry closes the documentation gap:
`ACTION_ITEMS.md`'s AI14 item was never flipped to `[x]` or linked to a
change-log entry when the fix landed, which `spinr-ai-guardrail-reviewer`'s
own audit checklist explicitly calls out as something to flag ("confirm the
diff doesn't make it worse; if it claims to close one, verify the fix
matches what's described there and flag if `ACTION_ITEMS.md` itself wasn't
updated to `[x]` with a change-log link"). This entry, plus the
`ACTION_ITEMS.md` update alongside it, closes that gap.

## Issue/gap identified
`ACTION_ITEMS.md`'s AI14 entry described a known accepted risk — a
rider-tapped `location_suggestions` candidate is trusted and booked even when
Google could only geocode it approximately (street/neighbourhood centroid,
not the building) — and proposed a concrete, low-risk middle ground: "have
`find_place` surface `precise=False` on the card and let the assistant quote
immediately while offering the map pin as an optional refinement ... rather
than gating the quote." That middle ground was implemented and merged, but
the ACTION_ITEMS.md entry itself was left unresolved-looking (`[ ]`) with no
pointer to the fix, so a reader of ACTION_ITEMS.md today would believe this
gap is still fully open.

## Root cause
The commit that shipped AI14(b) (139e607 / PR #4777) bundled multiple
unrelated fixes in one PR (AI1b daily-cap fail-closed, #2683 corporate
policy-edit confirmation, a scheduled-rides test flake) and updated
`ACTION_ITEMS.md` for AI1b only — the AI14 entry was not touched in that
diff even though its own proposed text was the change's stated motivation.

## Fix/remediation
No implementation code change in this session. This file plus a follow-up
edit to `ACTION_ITEMS.md`'s AI14 item (marking it `[x]` and describing what
shipped) brings the paper trail in line with the code that has been live
since 139e607.

For completeness, what actually shipped (139e607):
- `backend/ai/tools_booking.py`: `_candidates_from_results` computes
  `match_quality` from Google's real precision signal — `partial_match` (a
  house number Google had to ignore) outranks `geometry.location_type`; among
  `location_type` values, `ROOFTOP`/`RANGE_INTERPOLATED` are precise,
  `GEOMETRIC_CENTER`/`APPROXIMATE`/absent are not (`_PRECISE_LOCATION_TYPES`).
  Every `location_suggestions` candidate the client receives already carries
  `precise: bool` and `match_quality: str`. This computation predates 139e607
  — 139e607 is where that existing field was first *consumed* to produce a
  rider-facing note, not where the field itself was added.
- `shared/types/ai.ts`: declared `precise`/`match_quality` on
  `LocationSuggestionCandidate` to match the wire shape the backend already
  sent (type-only change, no runtime behavior).
- `shared/utils/aiLocationMessages.ts`: `buildLocationChoiceMessage` appends
  `" (approximate location — Google could not match an exact address)"` to
  the rider-tap message when `candidate.precise === false`. `precise: true`
  or absent is byte-identical to the old behavior.
- `backend/ai/prompts.py` (rule 6b): when the rider's tapped-suggestion
  message carries that marker, the model still passes the coordinates
  verbatim to `get_fare_quote`/`propose_ride_booking` (never re-geocodes —
  unchanged from the pre-existing rule) but adds one short line before the
  quote/booking card telling the rider the location is approximate and they
  may want to double-check it or tell the driver their exact spot.
- This never gates or blocks a quote — the quote/booking call happens exactly
  as before; the note is additive text before it. It also never depends on
  the `map_pin` client capability (`request_map_pin`'s `shown` gate in
  `tools_booking.py`): rule 6b's note is generic, capability-agnostic prose
  ("double-check it or let the driver know their exact spot") rather than an
  instruction to drop a pin, so it cannot dead-end on a client that doesn't
  advertise `map_pin` — unlike rule 8b's `imprecise_address` gate, which does
  call `request_map_pin` and is capability-checked there. AI14(b) deliberately
  never invokes `request_map_pin`, sidestepping the capability question
  entirely rather than needing to reuse `shown`.

## Risk & impact on existing functionality
- `_candidates_from_results` / `precise` field: consumed by `find_place`
  (the only caller building `location_suggestions` cards) and by the
  in-module candidate-ranking filter (`tools_booking.py` around line
  1089-1090, which prefers precise candidates when any exist). No other
  module reads `candidate["precise"]`.
- `buildLocationChoiceMessage`: shared between `rider-app/app/ai-assistant.tsx`
  and `admin-dashboard/src/app/dashboard/ai-console/page.tsx` (mirrors the
  rider card). Both already passed the raw API candidate through untouched,
  so both picked up the new marker with no call-site changes — per the
  139e607 commit message and confirmed unchanged in this session.
- Prompt rule 6b: touches only the rider-facing text of the tapped-suggestion
  branch; does not touch rule 8b/8c's separate `imprecise_address`/
  `dropoff_label_mismatch` gates, which remain the harder refusal path for
  addresses the rider typed rather than tapped from a card.
- Blast radius: isolated to the tapped-location-suggestion flow. No ride
  state, wallet, or Stripe path touched.

## User experience effect
Rider-facing: when a rider taps a location-suggestion card whose geocode
Google could only resolve approximately, they now see one extra short line
before the quote/booking card ("this location is approximate, you may want
to double-check it or let the driver know your exact spot") instead of no
signal at all. The quote/booking flow itself is unchanged — same number of
turns, no new required step. Not visible mid-session to a rider already in
an active ride; only affects a not-yet-booked trip being planned in chat.

## Files modified
This session modified no implementation files (already merged in 139e607).
This session added/modified:

| File | What changed | Why |
|---|---|---|
| `docs/change-log/2026-08-31-ai14-precise-geocode-note.md` | New — this file | Paper trail for the already-shipped AI14(b) fix |
| `ACTION_ITEMS.md` | AI14 entry marked `[x]`, describes what shipped and links here | Close the doc gap `spinr-ai-guardrail-reviewer`'s checklist flags |

## Before/after snippet
Not applicable — no behavior-changing diff in this session. See commit
`139e607` for the original before/after (rule 6b addition in
`backend/ai/prompts.py`, marker append in `shared/utils/aiLocationMessages.ts`).

## Rollback plan
No implementation code shipped by this session. If AI14(b) itself needs
rolling back, it is a plain revert of 139e607's `backend/ai/prompts.py`,
`shared/utils/aiLocationMessages.ts`, and `shared/types/ai.ts` hunks — no
migration, no feature flag, no live data touched (the note is pure prompt/
copy text, computed fresh each turn from `candidate.precise`).

## Verification performed
- Re-ran the existing unit coverage for the `precise`/`match_quality`
  computation: `pytest backend/tests/test_ai_tools_booking.py -k "precise or
  rooftop or interpolated or geometric or partial_match"` — 5 passed.
- Re-ran the existing `buildLocationChoiceMessage` marker tests:
  `yarn jest components/__tests__/aiLocationMessages.test.ts` (rider-app) —
  10 passed.
- Read `backend/ai/prompts.py` rule 6b, `backend/ai/tools_booking.py`
  (`_match_quality`, `_candidates_from_results`, `request_map_pin`'s `shown`
  gate), `backend/ai/orchestrator.py`'s tool-result-to-card flow, and
  `shared/utils/aiLocationMessages.ts` to confirm the shipped behavior
  matches ACTION_ITEMS.md's proposed middle ground: quote is never gated,
  the note is capability-agnostic (never references `map_pin`/dropping a
  pin), and no PII/precise-GPS is present in the note's copy (it names no
  coordinates, addresses, or provider names — plain "approximate location").
- Ran a manual `spinr-ai-guardrail-reviewer`-checklist pass over the diff in
  139e607 that touches this feature (rule 6b, `aiLocationMessages.ts`,
  `shared/types/ai.ts`): no PII/GPS leak in provider-egress text, no new
  state-mutating tool call, no fare recomputed outside `fare_service`
  (nothing here touches fare calculation), no cross-provider fallback
  change. See "Manual guardrail audit" below.
- No production build (`npm run build`) was run for `rider-app` or
  `admin-dashboard` in this session, since no frontend file was modified by
  this session's own diff.

## What was NOT verified
- Not re-run against a live LLM provider — no eval harness exists in this
  repo for prompt-driven tool-selection behavior (a standing, previously
  documented gap; not something this doc-only session could close).
- Not verified against a live Google Geocoding/Places response — the unit
  tests mock the API response shape (`location_type` values); real-world
  distribution of `APPROXIMATE`/`GEOMETRIC_CENTER` results was not sampled
  in this session.
- Not screenshotted or visually regression-tested in the rider app or admin
  AI console — no active visual-regression tooling exists for either
  surface (per `CLAUDE.md`'s standing note); this is copy-only text, reasoned
  about rather than screenshotted.

## Manual guardrail audit (spinr-ai-guardrail-reviewer checklist, applied by hand)
```
SPINR AI GUARDRAIL AUDIT — AI14(b) precise-geocode note (already-merged, 139e607)
===================================================================
BLOCKERS
  - none found

WARNINGS
  - ACTION_ITEMS.md AI14 entry was not updated to [x] with a change-log
    link when 139e607 merged — fixed by this session (this file + the
    ACTION_ITEMS.md edit alongside it).

OPEN BACKLOG TOUCHED
  - AI14 — closed by 139e607 (AI14(b) middle ground); documentation now
    updated to match by this session.

VERIFIED
  - prompts.py rule 6b: quote/booking call happens exactly as before the
    note is appended — never gated, never blocked.
  - aiLocationMessages.ts: the approximate-location marker text is
    capability-agnostic prose ("double-check it or let the driver know
    their exact spot") — it never mentions map_pin, request_map_pin, or
    dropping a pin, so it cannot dead-end on a client that doesn't
    advertise the map_pin capability. request_map_pin's own `shown` gate
    (tools_booking.py) is untouched by this feature and still correctly
    capability-checked for the separate rule-8b imprecise_address path.
  - Note text contains no coordinates, addresses, or provider names — only
    the words "approximate location."
  - No new ToolSpec/register() call — this is a card-field/prompt-copy
    change on an existing tool (find_place), not a new tool, so the
    "no new tool ships without an eval case" rule does not apply as a
    blocker here (it remains a standing, documented gap for future new
    tools).

VERDICT: SAFE TO MERGE (already merged; no outstanding blockers found)
```
