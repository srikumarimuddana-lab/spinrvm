# Observed Route Distance Accounting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent OSRM-inferred gap connectors from inflating completed-ride actual-distance statistics.

**Architecture:** Keep the existing reconstructed geometry and provenance for continuous map rendering. At the two accounting boundaries, project only `observed_distance_km` as matched/actual distance while retaining `inferred_distance_km` as quality metadata.

**Tech Stack:** Python 3.12, FastAPI backend utilities, pytest, `unittest.mock.AsyncMock`.

## Global Constraints

- Settled fare fields and `fare_breakdown_snapshot` remain immutable.
- Inferred geometry remains available for rendering and quality metadata.
- No schema migration.
- The change is limited to the route finalizer and its existing regression tests.

---

### Task 1: Use observed distance at accounting boundaries

**Files:**
- Modify: `backend/tests/test_route_finalizer.py`
- Modify: `backend/utils/route_finalizer.py`

**Interfaces:**
- Consumes: reconstructed route dictionaries containing `distance_km`, `observed_distance_km`, and `inferred_distance_km`.
- Produces: `route_quality.matched_distance_km` and the distance argument passed to `_recompute_ride_distance_stats()`.

- [ ] **Step 1: Write the failing regression assertions**

In `test_finalizer_persists_reconstructed_sections_and_distance_quality`, assert that the quality projection and stats recompute use the observed 0.75 km rather than the reconstructed 1.25 km:

```python
assert route_update["route_quality"]["matched_distance_km"] == 0.75
recompute.assert_awaited_once_with("ride_1", ride, 4, 0.75)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
python -m pytest backend/tests/test_route_finalizer.py::test_finalizer_persists_reconstructed_sections_and_distance_quality -q
```

Expected: failure because the current projection and recompute argument equal 1.25 km.

- [ ] **Step 3: Implement the minimal correction**

In `_matched_projection`, prefer observed reconstructed distance:

```python
"matched_distance_km": (reconstructed or {}).get(
    "observed_distance_km", matched_route.get("distance_km")
),
```

At the successful-finalization recompute call, pass observed reconstructed distance:

```python
reconstructed.get("observed_distance_km") if reconstructed is not None else None
```

- [ ] **Step 4: Verify GREEN and regression coverage**

Run:

```powershell
python -m pytest backend/tests/test_route_finalizer.py backend/tests/test_route_finalizer_recompute.py backend/tests/test_route_reconstruction.py -q
python -m ruff check backend/utils/route_finalizer.py backend/tests/test_route_finalizer.py
python -m ruff format --check backend/utils/route_finalizer.py backend/tests/test_route_finalizer.py
```

Expected: all tests pass and both Ruff commands exit zero.

- [ ] **Step 5: Rebuild the graph and commit**

Run:

```powershell
python -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"
git add backend/utils/route_finalizer.py backend/tests/test_route_finalizer.py graphify-out/graph.json graphify-out/GRAPH_REPORT.md graphify-out/manifest.json
git commit -m "fix(routes): exclude inferred gaps from actual distance"
git push origin main
```

