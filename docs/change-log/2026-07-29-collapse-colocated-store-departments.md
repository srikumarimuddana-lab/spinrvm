# Change Impact & Risk Log — collapse co-located store departments into one destination choice

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude |
| Surface(s) | backend |
| Domain (Sentry tag) | ai |
| PR / commit link | branch `claude/ai-collapse-colocated-store-departments` |
| Related issue or gap ID | Rider report: searching "walmart" returned 3 options, two of them the same Walmart ("Walmart Wireless", "Walmart Vision & Glasses") |

## 1. Issue / gap identified

A named-place search offers the rider duplicate destinations: two of the three "walmart" suggestions were departments inside the same store, at the same street address. The rider must choose between options that go to the same door, and the duplicates consume the 3-candidate limit that should be showing genuinely different stores.

## 2. Root cause

Google Places lists a store's departments as separate POIs — each with its own display name and its own pin a few metres from the parent store. `_candidates_from_results` already tried to collapse these, but keyed strictly on the normalized `formatted_address` string. That only fires when Google returns a byte-identical address; a department listing whose address carries a unit/suite token ("… Blvd Unit 2") produces a different key, so both entries survive and are shown as separate choices.

## 3. Fix / remediation

A second dedupe pass (`_collapse_colocated`) for **named-place results only**. Two candidates merge when they are both:
- within 75 m of each other, and
- sharing a leading brand token ("walmart").

Both conditions are required, so two different businesses in one plaza are never merged. The nearest member is kept as the representative; if one candidate's name is a prefix of the others, that parent name wins ("Walmart" over "Walmart Wireless"). No name is ever invented — if Google returned only department names, the nearest department name is kept.

## 4. Risk & impact on existing functionality

- Blast radius: `_candidates_from_results`, one caller (`_lookup_place_candidates`), which serves the AI `find_place` tool only. The collapse is opt-in via a new `collapse_colocated` parameter, passed `True` **only** on the Places branch.
- **Street-address geocodes deliberately excluded.** Neighbouring houses sit ~15–20 m apart, well inside the 75 m radius; collapsing them would silently drop the house the rider asked for. Pinned by `test_street_address_geocodes_never_collapse`.
- Over-collapse risk: bounded by the shared-brand-token requirement. The realistic false positive is two same-brand outlets within 75 m of each other (e.g. two Tim Hortons at opposite ends of one building) — for a drop-off these are the same destination anyway.
- Under-collapse (unchanged behavior) when a department is listed under a different brand name entirely (e.g. "Subway" inside a Walmart) — correct, that is genuinely a different storefront and may be the rider's intent.
- No DB, state-machine, money, or client change. Fewer candidates can reach the downstream driving-distance ranking, which is strictly less work.

## 5. User-experience effect

Rider-facing, visible mid-session: the AI's location suggestions no longer show the same store twice under department names, and the parent store name is preferred. This is a behavior change to a live-tested flow — a rider mid-booking sees a shorter, cleaner suggestion list. No UI/layout change.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `backend/ai/tools_booking.py` | `_leading_token`, `_collapse_colocated`, `_COLOCATED_MAX_KM`; `collapse_colocated` param wired to the Places branch | Collapse same-building departments |
| `backend/tests/test_ai_tools_booking.py` | 3 tests: departments collapse + parent name wins; distinct nearby brands survive; street-address geocodes never collapse | Pin the fix and both over-collapse guards |

## 7. Before/after

Before — three choices, two of them the same building:
```
Walmart Wireless          — 3939 Rochdale Blvd Unit 2
Walmart                   — 3939 Rochdale Blvd
Walmart Vision & Glasses  — 3939 Rochdale Blvd Suite 1
```
After — one choice, parent name:
```
Walmart                   — 3939 Rochdale Blvd
```

## 8. Rollback plan

`git revert` — stateless, no data or client coupling; reverting restores the duplicate suggestions. No flag: the change is a pure narrowing of an existing dedupe already present in this function, and the two guard tests bound its behavior.

## 9. Verification performed

- `pytest backend/tests/test_ai_tools_booking.py` — 86 passed.
- **Verified the new test genuinely fails without the fix**: temporarily forced `collapse_colocated=False`, confirmed `test_store_departments_collapse_to_one_choice` fails and the two guard tests still pass, then restored.
- Full backend suite (`pytest -m "not slow"`) — see below.

## 10. What was NOT verified

- **Not run against the live Google Places API** (no API key in this environment). The department addresses in the fixture ("Unit 2"/"Suite 1" variants) reproduce the reported symptom and are the most likely shape of the real response, but I could not confirm the exact `formattedAddress` strings Google returned for the rider's actual "walmart" search. If the real duplicates share a byte-identical address, the pre-existing address dedupe would already have caught them — meaning the real variance is elsewhere (unit token, or the pins differing). The proximity+brand rule covers both, so the fix holds either way, but the precise upstream shape is inferred, not observed.
- The 75 m radius is a judgment call, not tuned against live data on Regina store layouts.
- Not tested against live Supabase; unit suites use mocks.
