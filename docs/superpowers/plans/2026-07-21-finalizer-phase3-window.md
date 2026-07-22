# Finalizer Phase 3 Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure completed-route OSRM matching consumes only coordinates captured during the passenger-trip lifecycle window.

**Architecture:** Add a small finalizer-boundary selector that validates ride lifecycle timestamps and retains only Phase 3 rows. Keep `segment_route()` lifecycle-agnostic for its Phase 1/2 analyzer consumers and preserve its invalid-point rejection diagnostics.

**Tech Stack:** Python 3.12, pytest, `unittest.mock.AsyncMock`.

## Global Constraints

- Use capture time, not server receipt time when `captured_at` is present.
- Include both lifecycle endpoints.
- Keep invalid-timestamp rows available to segmentation rejection diagnostics.
- Use the authorized completion point as the route endpoint guardrail.
- Do not modify settled fares or raw location evidence.
- No schema migration.

---

### Task 1: Restrict finalizer evidence to Phase 3

**Files:**
- Modify: `backend/tests/test_route_finalizer.py`
- Modify: `backend/utils/route_finalizer.py`

**Interfaces:**
- Produces: `_phase_3_points(points: list[dict], ride: dict) -> list[dict]`.
- Consumes: `ride_started_at`, `ride_completed_at`, and each row's `captured_at` or legacy `timestamp`.

- [ ] **Step 1: Write the failing regression test**

Add a finalizer test with points at -5, 0, 30, and 601 seconds for a ride whose
window is 0 through 600 seconds. Capture the argument passed to
`compute_segmented_road_route` and assert it contains only points at 0 and 30
seconds.

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest backend/tests/test_route_finalizer.py::test_finalizer_matches_only_phase_3_coordinates -q --no-cov
```

Expected: FAIL because all four points currently reach segmentation and road
matching.

- [ ] **Step 3: Implement the lifecycle selector**

Add `_phase_3_points` near the finalizer helpers. Parse both lifecycle
boundaries with `parse_iso_utc`, raise `ValueError("ride_lifecycle_timestamp_missing")`
if either is absent or completion precedes start, retain invalid-timestamp rows
for downstream rejection, and retain valid rows only inside the inclusive
window.

Pass the selector output to `segment_route()` in `finalize_route()`.

- [ ] **Step 4: Verify GREEN and regressions**

Run:

```powershell
python -m pytest backend/tests/test_route_finalizer.py backend/tests/test_route_reconstruction.py backend/tests/test_route_reconstruction_projection.py -q --no-cov
python -m ruff check backend/utils/route_finalizer.py backend/tests/test_route_finalizer.py
python -m ruff format --check backend/utils/route_finalizer.py backend/tests/test_route_finalizer.py
```

Expected: all tests and Ruff checks pass.

- [ ] **Step 5: Rebuild, commit, and push**

Run:

```powershell
python -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"
git add backend/utils/route_finalizer.py backend/tests/test_route_finalizer.py
git commit -m "fix(routes): restrict finalizer to trip window"
git push origin main
```

