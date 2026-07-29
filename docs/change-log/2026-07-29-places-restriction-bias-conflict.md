# Change Impact & Risk Log — Places API (New) 400: locationRestriction + locationBias sent together

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude |
| Surface(s) | backend |
| Domain (Sentry tag) | ai |
| PR / commit link | branch `claude/fix-places-location-restriction-conflict` |
| Related issue or gap ID | Rider report: `find_place` 400 INVALID_ARGUMENT on "canadian tire" and "walmart" |

## 1. Issue / gap identified

Every AI named-place lookup for a rider whose location is known fails:

```
ai find_place Places API (New) error: {'code': 400,
 'message': 'Location_restriction and location_bias cannot be set at the same time...',
 'status': 'INVALID_ARGUMENT'}
```

The rider gets "place lookup failed — try again or pick the location in the app" and cannot search for a destination by name.

## 2. Root cause

`build_text_search_payload` (`backend/utils/google_places_new.py`) set **both** `locationRestriction` (rectangle) and `locationBias` (circle) on the same searchText request. Google treats those as mutually exclusive and rejects the whole request. Introduced in `88aa0c0` ("migrate named-place lookup to Places API (New) hard restriction (B5)") — the hard rectangle was added correctly, but the pre-existing soft-bias circle was left in place alongside it.

Two things hid it:
- The bias fields are only added when `near_lat`/`near_lng` are present, so a location-less search still worked — the failure looked intermittent rather than total.
- Every test mocks `_maps_post` to return 200 for any body, and no test imported `build_text_search_payload` directly, so no test could observe an invalid payload.

Note the sibling builder `build_autocomplete_payload` is **correct**: Autocomplete (New) does permit `origin` alongside a circular `locationRestriction`. Only searchText was wrong.

## 3. Fix / remediation

Drop `locationBias` from the searchText payload; keep the hard `locationRestriction` rectangle that B5 deliberately introduced. Nothing is lost — the shortlist is re-ordered by real driving distance afterwards in `tools_booking._rank_named_place_candidates_by_route`, which is a stronger signal than the soft bias ever was.

## 4. Risk & impact on existing functionality

- Blast radius: one payload builder. Callers: `tools_booking._lookup_place_candidates` (the AI `find_place` "places" branch) only — verified by grep; the legacy geocode branch and the autocomplete builder are untouched.
- Behavior change is strictly from "request rejected" to "request succeeds"; result *ordering* is unchanged because ranking happens downstream.
- The hard-restriction guarantee (B5, no candidate outside the ~25 km box) is preserved — the field that enforced it is the one kept.
- No DB, state-machine, money, or client change.
- **Related pre-existing behavior, deliberately not changed here:** `_lookup_place_candidates` hard-returns on a non-200 from Places instead of falling through to the geocode attempt (`tools_booking.py:290-292`). That is why this manifested as a total failure rather than a silent degrade. Changing it is a separate resilience decision (and CLAUDE.md prefers loud failure over masking), so it is out of scope for this fix — flagged, not silently altered.

## 5. User-experience effect

Rider-facing, visible immediately on deploy: searching a business by name in the AI chat ("canadian tire", "walmart") works again instead of returning a lookup-failed error. No UI change.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `backend/utils/google_places_new.py` | Removed `locationBias` from searchText payload; documented the mutual exclusion | Fix the 400 |
| `backend/tests/test_google_places_new.py` | **New** — builder unit tests incl. mutual-exclusivity invariant and the legal autocomplete pairing | Close the coverage gap that let this ship |
| `backend/tests/test_ai_tools_booking.py` | Added `test_restriction_and_bias_are_never_both_sent` at the tool level | Pin it on the real call path too |

## 7. Before/after

```python
# before — 400 INVALID_ARGUMENT
payload["locationRestriction"] = {"rectangle": {...}}
payload["locationBias"] = {"circle": {"center": {...}, "radius": ...}}   # ← mutually exclusive

# after
payload["locationRestriction"] = {"rectangle": {...}}
```

## 8. Rollback plan

`git revert` — single pure-function change, stateless, no data or client coupling. Reverting restores the 400, so rollback is only meaningful if the restriction itself proves wrong; in that case the safer revert target is B5 (`88aa0c0`), not this fix.

## 9. Verification performed

- `pytest backend/tests/test_google_places_new.py backend/tests/test_ai_tools_booking.py` — 88 passed.
- `ruff check` on all touched files — clean.
- Verified by inspection that the emitted payload now carries exactly one location field, matching Google's documented searchText contract.

## 10. What was NOT verified

- **Not called against the live Google Places API** in this session (no API key available here) — the fix is validated against Google's documented constraint and the exact error text from the production log, not by a real 200 response. This is the one check worth doing in staging before/while deploying: search "canadian tire" in the AI chat with a rider location set and confirm candidates return.
- Not tested against live Supabase; unit suites use mocks.
- Whether result *quality/ordering* changes in practice without the soft bias is reasoned about (downstream driving-distance ranking) rather than measured against live Google responses.
