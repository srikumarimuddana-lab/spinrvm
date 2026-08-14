# Change Impact & Risk Log — Heatmap Admin Follow-ups (surge N+1, tuning guidance, demand cross-link)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-14 |
| Author | Claude Code |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin, dispatch (surge read path only) |
| Severity | Performance + operator-safety; no money path, no rider/driver-facing change |
| Found by | Follow-up items from the 15-agent pre-deploy review (`docs/reviews/2026-08-13-heatmap-predeploy-review.md`) plus the "not verified" list in `docs/change-log/2026-08-13-per-area-heatmap-config.md` |

Three independent items, batched because they are all admin-surface follow-ups
from the same review and share one verification pass. They do not depend on
each other and can be reverted individually.

---

## Item 1 — `GET /admin/surge/status` re-read the drivers table once per area

### 1. Issue / gap identified

The admin surge-status endpoint issued one unscoped read of `drivers` **per
service area**, plus one `rides` read per area, on every call — and both the
heatmap page and the monitoring page poll it every 2 minutes, from every open
tab.

### 2. Root cause

`_count_supply_in_area(area)` fetched the platform's online+available drivers
itself and then applied the area polygon test. That is correct for a
single-area caller, but the driver set it fetches is **platform-wide** — only
the polygon test that follows is per-area. So a caller sweeping N areas issued
the identical query N times and threw N−1 of the results away.

Reads went `1 + 2N` (1 area list, N driver fetches, N ride fetches).

### 3. Fix / remediation

Extracted `_fetch_dispatchable_drivers(context)` — the `get_rows` + fetch-cap
warning + Redis presence filter — and gave `_count_supply_in_area` an optional
`prefetched_drivers` argument. `get_surge_status` fetches once and passes the
batch; every other caller omits the argument and is unchanged.

Reads are now `2 + N`.

Separately, the assembled status is cached in Redis for 30 s
(`spinr:surge:status:all`), well inside the surge engine's own 120 s
recalculation cadence, so a cached read is never staler than the numbers
already are. The cache is dropped by `invalidate_surge_status_cache()` on:

| Trigger | Why |
|---|---|
| `PUT /service-areas/{id}/surge` | Operator just changed a regulated price |
| `PUT /service-areas/{id}` touching `surge_*` | Same, via the general update path |
| The engine moving a multiplier | Ops dashboard should show it on next refresh |

An unrelated edit (renaming an area) does **not** invalidate.

### 4. Risk & impact on existing functionality

**Blast radius — every caller of the two changed functions, grep-verified:**

| Caller | Uses batch? | Effect |
|---|---|---|
| `calculate_surge_for_area` → `_count_supply_in_area(area)` | No | Unchanged — argument defaults to `None`, original per-area fetch |
| `recalculate_all_surges` (the live pricing loop) | No | Unchanged. **Deliberate**: the loop writes the multiplier riders are charged, so it keeps its own fetch per area rather than sharing a snapshot |
| `_record_manual_surge_history` (admin/service_areas.py) | No | Unchanged |
| `get_surge_status` | Yes | The only behaviour change |

`_count_supply_in_area` still checks the flag-gated PostGIS spatial path
**before** consulting the prefetched list — the spatial count has no row cap
and is strictly more accurate, so a caller that pre-fetched simply wasted one
query rather than silently downgrading accuracy to reuse a batch.

**What could regress:**

- **Stale supply within one sweep.** All areas in one status call now score
  against the same driver snapshot rather than N snapshots taken seconds apart.
  This is *more* internally consistent, not less, but it is a real difference:
  a driver who goes offline mid-sweep is now counted in every area or none,
  instead of some. For a read-only ops view that is the better property.
- **A Redis outage** now costs a cache miss on this endpoint. Both read and
  write are wrapped: a read failure logs a warning and recomputes live, a write
  failure logs and returns the fresh data. The endpoint cannot fail *because
  of* the cache.
- **A missed invalidation would be visible for ≤30 s.** The three write paths
  are covered by tests; a future fourth write path that forgets would show a
  stale multiplier briefly. Mitigated by the TTL being short, but noted.

**Explicitly unaffected:** the surge multiplier applied to any fare, the tier
table, `SURGE_CAP`, the manual-override justification gate, and the
`surge_pricing` history rows. This changes how the admin *reads* surge, never
how it is computed or charged.

### 5. User-experience effect

- **Internal admin only.** No rider, driver, or corporate-admin surface.
- **Mid-session visible:** an admin with the page open sees the same numbers;
  the page just stops issuing N redundant driver reads per poll.
- **One behaviour change worth naming:** after changing surge, the status now
  reflects it immediately rather than up to 30 s later — which is only true
  *because* the invalidation shipped with the cache. Shipping the cache alone
  would have been a UX regression on a regulated-price confirmation screen.

---

## Item 2 — Per-area heatmap tuning had ranges but no guidance

### 1. Issue / gap identified

Recorded as an open gap in the per-area config change log: the form exposed
eleven numeric knobs with min/max and a one-line description, but recommended
nothing. Choosing a baseline window for a low-volume area was an operator
judgement call with no in-product help.

Worse, the two knobs an operator reaches for first are the two with real costs:

- **`k_floor`** — the obvious fix for an empty map is to lower the privacy
  floor, which trades a k-anonymity guarantee for coverage.
- **`refresh_seconds`** — the obvious fix for a map that feels stale is to poll
  more often, which multiplies battery/data use for **every online driver in
  the area at once**.

Nothing in the form pushed back on either.

### 2. Root cause

The form was built to expose the config surface correctly (bounds, inherit vs
override, clamping) and that work stopped at correctness. Guidance was listed
as a known gap rather than an oversight.

### 3. Fix / remediation

New `admin-dashboard/src/lib/heatmap-tuning-guidance.ts`:

- `TUNING_PLAYBOOK` — five symptom → action → **trade-off** entries, rendered
  in a collapsed `<details>` above the knobs. Symptom-first, because that is
  the direction an operator reads it in. Every entry states a cost; an entry
  with an action and no cost would read as a recommendation.
- `tuningWarnings(draft, inherited)` — a pure function evaluated against the
  **draft**, so a risky value is flagged while it is being typed:
  - `k_floor` below the inherited value → `warning`, and the message names the
    alternative (widen the baseline window or cell size). A warning that only
    obstructs leaves the operator with the same empty map.
  - `refresh_seconds` below inherited → `warning`, quantified (`90s → 30s` reads
    as "3.0×"), because "uses more battery" is not something anyone can judge.
  - `cell_*_deg` below inherited → `info`. This is the counterintuitive one:
    smaller cells hold fewer rides each, so **more** of them fall under the
    privacy floor and the map gets sparser, not more detailed.

Changes in the safe direction produce no warning at all — a form that warns
about everything trains operators to dismiss it.

### 4. Risk & impact on existing functionality

**Blast radius:** the new module has exactly one importer
(`service-areas/page.tsx`), grep-verified. It is pure, has no network or store
access, and cannot change what gets saved — `handleSave` is untouched, and no
warning blocks the save button.

**What could regress:**

- **Warning fatigue** if the thresholds are wrong. They fire only on a *worse
  than inherited* value in a direction with a stated cost, which is the
  narrowest defensible rule; unknown keys and missing inherited values produce
  nothing rather than guessing.
- **The playbook is prose and can go stale** if a knob's semantics change. The
  labels and bounds are already served from the backend `HEATMAP_SPEC`, so a
  new knob appears in the form automatically — but its playbook entry does not
  write itself. A test asserts the playbook never recommends lowering the
  privacy floor, which is the one direction that must not drift.

**Explicitly unaffected:** the resolution chain, the clamps, the saved payload
shape, and the backend validator. This is advisory UI only.

### 5. User-experience effect

- **Internal admin only** (Service Areas → Heatmap tab).
- **Mid-session visible:** yes, but additive — a new collapsed section and
  inline notes that appear only on a risky draft value. No existing control
  moved or changed behaviour.
- **New copy** (playbook + three warning messages). No rider/driver copy.

---

## Item 3 — Demand cards were a dead end

### 1. Issue / gap identified

The Live Demand Pressure cards report a ratio per area ("Regina, 3.0, +8 over
idle drivers") and stop there. The next question is always *where in Regina*
and *which drivers are idle nearby* — neither of which the card can answer.
Getting to the answer meant navigating to Live Monitoring, re-finding the area
in a dropdown, and re-enabling the demand overlay, so in practice the two
screens read as unrelated.

### 2. Root cause

AD-01 (the monitoring overlay) and AD-03 (the demand cards) were built as
separate tickets and nothing linked them. The monitoring page also had no
deep-link support, so there was nothing to link *to*.

### 3. Fix / remediation

- Monitoring page accepts `?area=<id>&demand=1` on mount and seeds
  `filters.serviceAreaId` / `filters.showDemand` from it. The pre-existing
  fit-to-area effect then flies the map to that area with no further change.
- Each demand card renders "View <area> on the live map" linking to exactly
  that URL.

Read once on mount from `window.location.search`, **not** `useSearchParams()`:
that hook requires a `<Suspense>` boundary this route lacks and fails
`next build`. This follows the identical, commented precedent in
`dashboard/rides/page.tsx`.

### 4. Risk & impact on existing functionality

**RBAC — the part that would have shipped as a broken link.** Live Monitoring
is gated by the `rides` module; the heatmap page by `heatmap`. An
unconditional link would send a heatmap-only admin to `/403`, which reads as a
bug rather than a permission boundary. The link is therefore rendered only when
the viewer is `super_admin` or holds `rides`, mirroring `sidebar.tsx` exactly
(and note that role `admin` does **not** bypass — it is module-scoped like any
other non-super role).

**Blast radius on the monitoring page:** the new effect is mount-only and
returns immediately when neither param is present, so arriving at
`/dashboard/monitoring` with no query string is byte-for-byte the previous
behaviour. It writes through the same `setFilters` the toolbar uses, so a later
manual dropdown change wins — it is a seed, not a lock.

**What could regress:**

- A **stale or deleted area id** in a bookmarked URL selects an area that no
  longer exists. The existing fit-to-area effect already guards this
  (`if (!area) return`) and the toolbar shows no selection, so it degrades to
  an unfiltered map rather than an error.
- **The link is hidden, not disabled**, for admins without `rides`. They lose a
  shortcut they never had; they do not lose information.

**Explicitly unaffected:** the monitoring WebSocket, the driver/ride marker
pipeline, the poll intervals, and every existing filter interaction.

### 5. User-experience effect

- **Internal admin only.**
- **Mid-session visible:** yes — a new link on each demand card, and a
  monitoring page that now honours query params it previously ignored.
- **New copy:** "View <area> on the live map".

---

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `backend/utils/surge_engine.py` | `_fetch_dispatchable_drivers()` extracted; `prefetched_drivers` param; 30s status cache + `invalidate_surge_status_cache()`; `changed_any` invalidation in the recalc loop | Item 1 |
| `backend/routes/admin/service_areas.py` | `_invalidate_surge_status_cache()` helper called from both surge write paths; two pre-existing ruff findings fixed (`B904`, import order) | Item 1 |
| `backend/tests/test_surge_engine.py` | 5 new tests (batching, fallback, spatial skip, cache hit, invalidation); existing status test now passes `use_cache=False` | Item 1 |
| `backend/tests/test_utils_extended.py` | Existing status test passes `use_cache=False` | Item 1 |
| `backend/tests/test_admin_service_areas_coverage.py` | 3 new tests pinning cache invalidation on surge writes and non-invalidation otherwise | Item 1 |
| `admin-dashboard/src/lib/heatmap-tuning-guidance.ts` | New: `TUNING_PLAYBOOK`, `tuningWarnings`, `warningsFor` | Item 2 |
| `admin-dashboard/src/app/dashboard/service-areas/page.tsx` | Playbook `<details>`, inline per-key warnings, save-button reminder | Item 2 |
| `admin-dashboard/src/__tests__/lib/heatmap-tuning-guidance.test.ts` | New: 12 tests | Item 2 |
| `admin-dashboard/src/__tests__/dashboard/area-heatmap-overrides.test.tsx` | 4 new tests for the rendered guidance | Item 2 |
| `admin-dashboard/src/app/dashboard/monitoring/page.tsx` | Mount-time `?area=`/`?demand=` deep-link seed | Item 3 |
| `admin-dashboard/src/app/dashboard/heatmap/page.tsx` | `canOpenMonitoring` RBAC check + per-card link | Item 3 |
| `admin-dashboard/src/__tests__/dashboard/demand-monitoring-crosslink.test.tsx` | New: 3 tests (both params present, hidden without `rides`, shown to super_admin) | Item 3 |
| `.semgrep/spinr-rules.yml` | SR-03 allowlist corrected from 6 paths (2 non-existent) to 10 verified-clean, with documented exclusions | Committed separately |
| `.github/workflows/security-gates.yml` | Stale comment describing SR-03 as untuned | Committed separately |

## 7. Before / after

**Item 1 — the N+1:**

```python
# Before — get_surge_status
for area in areas:
    demand = await _count_demand_in_area(area["id"])
    supply = await _count_supply_in_area(area)   # re-reads ALL drivers, per area
```

```python
# After — one fetch for the sweep; the live pricing loop is untouched because
# it simply doesn't pass the argument.
prefetched = None
if not _SURGE_SPATIAL_COUNT:
    try:
        prefetched = await _fetch_dispatchable_drivers(context="status")
    except Exception as exc:
        logger.warning(f"...falling back per-area: {exc}")   # never reports 0 supply

for area in areas:
    demand = await _count_demand_in_area(area["id"])
    supply = await _count_supply_in_area(area, prefetched_drivers=prefetched)
```

**Item 2 — the form:**

```tsx
// Before — bounds and a description; nothing about consequences.
<span className="ml-1 opacity-70">({spec.min}–{spec.max})</span>
```

```tsx
// After — the same bounds, plus a warning on a value that costs something.
{warningsFor(key, warnings).map((w) => (
  <p role={w.severity === "warning" ? "alert" : undefined} ...>{w.message}</p>
))}
```

**Item 3 — the card footer** is purely additive (no prior element replaced), so
there is no before-state to show beyond its absence.

## 8. Rollback plan

Three independent commits; each reverts alone.

- **Item 1** — `git revert` restores the per-area fetch. Nothing is persisted;
  the Redis key expires on its own 30 s TTL, so no cleanup is needed and no
  stale data can survive the revert. Safe to revert without a second deploy.
- **Item 2** — advisory UI only. Reverting removes copy; nothing saved by the
  form depends on it. If only the *warnings* are unwanted, deleting the
  `tuningWarnings` call site is a one-line change that keeps the playbook.
- **Item 3** — reverting removes the link and the query-param seed. Any
  bookmarked `?area=…&demand=1` URL degrades to the previous behaviour
  (params ignored), not an error.

None of the three touches a schema, a migration, live money, or ride state, so
there is nothing applied to production data that a revert would have to undo.

## 9. Verification performed

- [x] **Backend:** `test_surge_engine.py` (40), `test_utils_extended.py` (163),
      `test_admin_service_areas_coverage.py` (62), `test_admin_surge_history.py`
      (2) — all pass.
- [x] **The three cache-invalidation tests verified to FAIL with the
      invalidation calls removed** (2 of 3 fail; the third asserts
      non-invalidation and correctly still passes). They pin the defect, not
      the shape.
- [x] `ruff check` + `ruff format --check` clean on every touched Python file,
      including two pre-existing findings in `service_areas.py` fixed in passing.
- [x] **Admin dashboard: full vitest suite, 232 passed / 25 files, 0 failed**
      (was 213 — 19 new).
- [x] `tsc --noEmit` clean.
- [x] **`npm run build` — a real production build — exit code 0.** Per CLAUDE.md
      this is required for any admin-dashboard change; a dev server or
      `tsc --noEmit` alone would not be equivalent. Both were also run.
- [x] `eslint` on all changed admin files: **0 errors**. Remaining warnings are
      pre-existing (`react-hooks/set-state-in-effect` etc.) and untouched by
      this diff; one *new* warning this change introduced (an unnecessary
      `eslint-disable` directive) was found and removed.
- [x] Blast-radius enumeration of every `_count_supply_in_area` caller (Item 1)
      and every importer of the new guidance module (Item 2), both stated above.

## 10. What was NOT verified

- **No staging or browser run.** The admin changes were not exercised in a real
  browser against a real backend. The build compiles and the components are
  mounted and interacted with in jsdom, but the deep-link was not clicked
  end-to-end from the heatmap page to a live monitoring map.
- **No visual-regression tooling exists for admin-dashboard**, so the new
  `<details>` block, the warning callouts and the card link were reasoned about
  for layout and contrast, not screenshotted. Standing gap — see
  `ACTION_ITEMS.md`.
- **The N+1 improvement was not measured.** The read-count reduction is
  structural (1+2N → 2+N, verifiable in the test that asserts one `drivers`
  read for four areas), but no latency benchmark was run before or after, and
  the endpoint has no perf baseline in `perf_*_before.json` to compare against.
- **The 30 s cache TTL was not load-tested.** The claim that it collapses
  concurrent viewers onto one computation follows from how Redis TTLs work, not
  from an observed reduction under real tab counts.
- **Not tested against live Supabase or a real Redis.** All backend tests use
  `mock_supabase_client` and the in-process Redis fallback. In particular, the
  cache's behaviour under a *real* Redis eviction or failover was not observed
  — only the "raises an exception" path, which is covered.
- **RBAC was verified against `sidebar.tsx`'s logic, not against a real
  module-limited admin session.** A user with `heatmap` but not `rides` was
  simulated in the test's auth store; no such account was logged in.
- **Item 1's cache invalidation covers the three write paths that exist today.**
  If another route learns to write `surge_*` on `service_areas` without going
  through `admin_update_service_area` or `admin_update_surge_pricing`, this
  sweep would not have found it and that path would leave a ≤30 s stale read.
