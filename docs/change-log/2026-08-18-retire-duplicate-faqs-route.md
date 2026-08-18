# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | Content/UX review (Claude Code) |
| Surface(s) | backend |
| Domain (Sentry tag) | ai |
| PR / commit link | see PR on branch `claude/spinr-faq-review-uodytp` |
| Related issue or gap ID | Flagged as a separate finding while working PR #4126 (support.py hardcoded FAQ fix); the last of the 3 findings surfaced during this session's FAQ content-quality review |

## 1. Issue / gap identified

Two independent implementations of `GET /api/v1/faqs` existed in the backend: `backend/features.py`'s `support_router.get_faqs` and `backend/routes/faqs.py`'s `api_router.get_public_faqs`. Both were mounted into `v1_api_router` in `backend/server.py`, `support_router` first (line 350) and `faqs_router` second (line 355). FastAPI/Starlette matches the first-registered route for a given path+method, so `routes/faqs.py`'s handler never actually ran in production — dead code that looked live, with its own tests giving 100% "coverage" of a code path zero real traffic ever reaches.

## 2. Root cause

The two implementations were built independently (likely by different sessions/authors at different times) without either noticing the other already existed and was already mounted at the same path. Nothing in the codebase or CI previously checked for this: `backend/scripts/check_route_shadowing.py` (an existing dev script) only detects a literal path shadowed by an earlier *parameterized* route (`/{id}` before `/leaderboard`) **within a single router's own registration order** — it does not check for two separate routers registering the identical literal path against the same parent router, which is what actually happened here. That's a real, separate gap in the existing tooling — flagged below, not fixed in this PR.

## 3. Fix / remediation

- **Deleted `backend/routes/faqs.py` entirely** (32 statements, the dead/shadowed handler) and its mount + import in `backend/server.py`. `backend/features.py`'s `get_faqs` remains the sole `GET /faqs` implementation — nothing about its registration position or behavior changed.
- **Found and fixed a real bug while retiring the dead code**: `features.get_faqs`'s `try/except` only wrapped the lat/lng→service-area resolution branch, not the final `resolve_area_scope(area_id)` call — so a DB blip in that last call raised straight through this public, **unauthenticated** endpoint as an uncaught 500. The dead `routes/faqs.py` module actually had the *more defensive* behavior (its `_resolve_area_scope` wrapped both calls in one try/except, degrading to an empty scope — global-FAQs-only — on any failure). Widened `features.get_faqs`'s try/except to cover both calls, matching the dead module's (correct) behavior, so this endpoint now degrades gracefully instead of 500ing on a transient service-area lookup failure. Caught by a real test failure while porting the dead module's test scenarios onto the live handler (see below), not by inspection alone.
- **Backfilled real test coverage on the live handler** (`backend/tests/test_features.py::TestFAQs`), which previously had only 2 dedicated tests (sort_order ordering, missing-sort_order-as-zero) despite the live handler's own docstring stating an audience filter is safety-critical ("MUST filter by audience — without it, driver-only FAQs surface in the rider app") — that claim was untested. Added 8 new tests: audience-filter-passed-to-query, category-filter-passed-to-query, global-vs-area-tagged-vs-out-of-scope filtering, lat/lng→service-area resolution, no-location-context hides area-tagged rows, DB-returns-None→empty-list, and the area-resolution-failure-degrades-to-empty-scope test that caught the bug above.
- Deleted the two test files that existed solely to exercise the now-removed dead module (`backend/tests/test_routes_faqs_coverage.py`, `backend/tests/test_faqs_coverage.py` — 312 lines combined) and removed the `TestFaqsEndpoint` class (4 tests) from `backend/tests/test_utils_extended.py`, which also only imported the dead module. None of this is a coverage *loss* — every scenario those tests covered on the dead module now has an equivalent (or improved — see the bug fix above) test on the actually-live `features.get_faqs`.
- Updated the two remaining prose references to the now-deleted file: `features.get_faqs`'s own docstring and a comment in `test_features.py`, both of which described the shadowing relationship — now describe the retirement instead. Left the historical mentions in already-merged migrations (`229_faqs_service_area.sql`, `322_consolidate_sos_faq.sql`) and past change-log docs untouched — those are accurate records of what existed at the time they were written (append-only convention).

## 4. Additional finding — not fixed, flagged for a decision

`backend/scripts/check_route_shadowing.py` does not catch this bug class (two separate routers registering the identical literal path against the same parent router — no `{param}` involved at all, so its param-vs-literal regex check never triggers). Extending it to also flag literal-vs-literal duplicate registrations across all routers mounted into `v1_api_router` would have caught this months earlier and would catch a repeat elsewhere in the ~25 routers `server.py` mounts. Not attempted here — a meaningfully separate task (needs to walk `server.py`'s full mount list, not just one routes package) from retiring this specific duplicate, and risks scope creep beyond what was asked. Flagging directly for a follow-up decision.

## 5. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped the full backend for `routes.faqs`, `routes/faqs.py`, and `faqs_router` — the only remaining source hits are `backend/routes/admin/faqs.py`'s unrelated `faqs_router` (admin CRUD, a completely different module, untouched) and historical migration/change-log prose (left as-is per the append-only convention).
- **No other code imported anything from the deleted module.** Full test collection (`pytest --collect-only`, 12,058 tests) succeeds with zero import errors after the deletion — if anything else had imported `backend.routes.faqs`, collection would have failed immediately.
- **The bug fix (widened try/except) only affects `features.get_faqs`**, which has exactly one caller: the `GET /api/v1/faqs` route itself. No other function calls `features.get_faqs` internally (confirmed by re-reading the full file structure — it's a route handler, not a shared helper).
- No interaction with the ride state machine, wallet/payment deltas, RLS policies, or any of the 16 background loops. No schema change.

## 6. User-experience effect

- **Rider- and driver-facing**, but the *visible* behavior is unchanged — `features.get_faqs` was already the handler actually serving every real request; deleting its shadowed twin changes zero currently-observed behavior.
- **The one behavior that does change**: if the service-area-scope lookup ever fails (a DB blip on `resolve_area_scope`), the endpoint now returns global-FAQs-only instead of a raw 500. This is a reliability improvement, not a new user-visible feature — nobody was relying on the 500.
- Not visible mid-session to anyone already viewing the Help Center (refetches on screen load).

## 7. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/faqs.py` | Deleted | Dead code — shadowed by `features.py`'s identical, earlier-registered `GET /faqs` handler; never ran in production |
| `backend/server.py` | Removed the `routes.faqs` import and its `v1_api_router.include_router(faqs_router)` mount | Same |
| `backend/features.py` | Widened `get_faqs`'s `try/except` to also cover the final `resolve_area_scope(area_id)` call (previously outside the guard); updated its docstring | Fix a real uncaught-exception bug on a public endpoint found while retiring the dead code; remove a stale cross-reference to the deleted file |
| `backend/tests/test_routes_faqs_coverage.py` | Deleted | Exclusively tested the now-removed dead module |
| `backend/tests/test_faqs_coverage.py` | Deleted | Same |
| `backend/tests/test_utils_extended.py` | Removed `TestFaqsEndpoint` class (4 tests) | Same — only imported the dead module |
| `backend/tests/test_features.py` | Added 8 new tests to `TestFAQs` covering audience/category filtering, area-scope filtering, lat/lng resolution, None-from-DB, and the area-resolution-failure path; updated one docstring | Backfill real coverage on the actually-live handler, which previously had a documented-but-untested "MUST filter by audience" safety claim |

## 8. Before / after

```
# Before (backend/features.py::get_faqs)
    area_id = service_area_id
    if not area_id and lat is not None and lng is not None:
        try:
            area = await resolve_service_area_for_point(float(lat), float(lng))
            area_id = area.get("id") if area else None
        except Exception:
            logger.opt(exception=True).error("public faq service-area resolve failed")
    scope = await resolve_area_scope(area_id)   # <-- NOT inside the try/except
    return [f for f in faqs if not f.get("service_area_ids") or (set(f["service_area_ids"]) & scope)]
```

```
# After
    area_id = service_area_id
    try:
        if not area_id and lat is not None and lng is not None:
            area = await resolve_service_area_for_point(float(lat), float(lng))
            area_id = area.get("id") if area else None
        scope = await resolve_area_scope(area_id)
    except Exception:
        logger.opt(exception=True).error("public faq service-area resolve failed")
        scope = set()
    return [f for f in faqs if not f.get("service_area_ids") or (set(f["service_area_ids"]) & scope)]
```

```
# Before (backend/server.py)
from routes.faqs import api_router as faqs_router
from routes.fares import api_router as fares_router
...
v1_api_router.include_router(pricing_router)
v1_api_router.include_router(faqs_router)
v1_api_router.include_router(legal_documents_router)

# After
from routes.fares import api_router as fares_router
...
v1_api_router.include_router(pricing_router)
v1_api_router.include_router(legal_documents_router)
```

## 9. Rollback plan

`git-revert-safe` — pure Python source deletion/edit, no schema, no data migration, no config/flag. A plain `git revert` restores `routes/faqs.py`, its server.py mount, and the narrower try/except exactly (including the exception-handling bug, if that were ever desired, which it isn't).

## 10. Verification performed

- [x] **`backend/tests/test_features.py -k TestFAQs`**: 12/12 passed (4 pre-existing + 8 new), including the new test that caught and validated the fix for the uncaught-exception bug.
- [x] **`backend/tests/test_utils_extended.py`** (full file, post-deletion of `TestFaqsEndpoint`): 161 passed, 1 pre-existing skip, 0 failures.
- [x] **`pytest --collect-only`** across the full backend test tree: 12,058 tests collected with zero import errors — confirms nothing else imports the deleted module.
- [x] **`ruff check`** on all 4 touched Python files: all checks passed.
- [x] **Blast-radius grep**: confirmed the only remaining `routes.faqs`/`faqs_router` hits are the unrelated admin-CRUD module and historical (correctly-unedited) migration/change-log prose.
- [ ] Full backend suite (`pytest -q`) run in background alongside writing this log — see the accompanying PR comment/description for the result once it completes.
- [ ] Not run against a real/throwaway Supabase schema — pure code deletion/refactor with mocked-DB unit tests, no schema touched.

**What was NOT verified**: whether any external caller outside the two shipped apps (a web widget, a partner integration, manual QA) depends on any response-shape difference between the two implementations (there wasn't one that mattered — both returned the same list shape — but this wasn't independently re-verified against a live client beyond the source-level comparison already done when the two files were first compared during this session's earlier FAQ audit).

## 11. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow: the live-serving handler and its response shape are unchanged; the one behavior change (graceful degradation instead of a 500 on a DB blip) is a reliability fix, not a UX change anyone was relying on
- [x] Found-but-out-of-scope issue (`check_route_shadowing.py`'s coverage gap for cross-router literal-path collisions, section 4) surfaced explicitly rather than silently fixed or silently ignored
