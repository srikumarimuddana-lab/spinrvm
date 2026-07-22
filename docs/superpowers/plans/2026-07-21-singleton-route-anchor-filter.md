# Singleton Route Anchor Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent isolated one-coordinate GPS fallbacks from creating large OSRM detours in completed-ride maps.

**Architecture:** Filter non-drawable singleton fallbacks at the pure observed-section projection boundary. Leave durable evidence untouched and let the existing chronological reconstruction loop connect the surrounding valid sections directly.

**Tech Stack:** Python 3.12, pytest, route reconstruction utilities.

## Global Constraints

- Preserve raw GPS evidence.
- Require at least two valid coordinates for drawable observed fallback geometry.
- Preserve OSRM-inferred geometry provenance and existing endpoint guardrails.
- Do not substitute the planned route.
- No schema migration.

---

### Task 1: Filter singleton fallback anchors

**Files:**
- Modify: `backend/tests/test_route_reconstruction_projection.py`
- Modify: `backend/utils/route_reconstruction_projection.py`

**Interfaces:**
- Consumes: `SegmentedRoute.observed_segments` and matched-route output.
- Produces: `project_observed_sections(...) -> list[dict]` containing only drawable sections.

- [ ] **Step 1: Write the failing regression test**

Construct a `SegmentedRoute` containing a single-point observed segment and no
matched geometry, then assert that projection returns no drawable section:

```python
def test_single_coordinate_fallback_is_not_a_reconstruction_anchor():
    segmented = segment_route(
        [_point(0, 0, 50.45, -104.62)],
        {
            "ride_started_at": BASE_TIME.isoformat(),
            "ride_completed_at": BASE_TIME.isoformat(),
        },
        {"lat": 50.45, "lng": -104.62},
    )

    sections = project_observed_sections(segmented, {"segments": [], "failures": []})

    assert sections == []
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest backend/tests/test_route_reconstruction_projection.py::test_single_coordinate_fallback_is_not_a_reconstruction_anchor -q --no-cov
```

Expected: FAIL because the current projection emits a zero-distance
`observed_fallback` section containing one coordinate.

- [ ] **Step 3: Implement the minimal filter**

In `project_observed_sections`, replace the non-empty fallback guard with a
drawable-coordinate guard:

```python
if len(coordinates) < 2:
    continue
```

- [ ] **Step 4: Verify GREEN and route regressions**

Run:

```powershell
python -m pytest backend/tests/test_route_reconstruction_projection.py backend/tests/test_route_reconstruction.py backend/tests/test_route_finalizer.py -q --no-cov
python -m ruff check backend/utils/route_reconstruction_projection.py backend/tests/test_route_reconstruction_projection.py
python -m ruff format --check backend/utils/route_reconstruction_projection.py backend/tests/test_route_reconstruction_projection.py
```

Expected: all tests pass and both Ruff commands exit zero.

- [ ] **Step 5: Rebuild, commit, and push**

Run:

```powershell
python -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"
git add backend/utils/route_reconstruction_projection.py backend/tests/test_route_reconstruction_projection.py
git commit -m "fix(routes): ignore singleton reconstruction anchors"
git push origin main
```

