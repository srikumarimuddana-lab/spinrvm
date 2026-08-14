# Change Impact & Risk Log — AI Console Gate + Medium/Low Remediation

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-14 |
| Author | Claude Code |
| Surface(s) | backend, driver-app, admin-dashboard |
| Domain (Sentry tag) | admin, drivers, ai |
| Severity | One crash-class fix (driver-app), one correctness bug in a shipped feature, one boundary hardening; the rest are reliability and a11y |
| Found by | Clearing the MEDIUM + LOW tiers of `docs/reviews/2026-08-13-heatmap-predeploy-review.md`, plus two defects found while doing so |

Committed as four scoped commits. Two items in this batch were **not** on the
review's list — they were found while working through it and are called out
below, because both are more serious than most of what was.

---

## Item 1 — AI Console boundary was correct but implicit

### 1. Issue / gap identified

`ai_console_router` was mounted with no dependency. **Correcting an earlier
report in this branch: I previously stated any admin could call these
endpoints. That was wrong** — I had read the mount line and not the function
bodies. All four routes call `_require_super_admin()` themselves, so
enforcement has always held.

What was missing is structure. With no gate at the mount, the boundary lived
entirely in whether each future route author remembered one line, and nothing
at the mount hinted that they had to.

### 2. Why it matters anyway

These endpoints impersonate a rider or driver in an AI chat turn and read their
conversation history — PII plus an action-capable LLM session, stricter than
any grantable module. A route added to this router without the in-body call
would have been reachable by any admin.

### 3. Fix

Mounted under `require_super_admin`; the per-route calls stay as defence in
depth for anywhere else the router might be included. Same argument the
`bulk_operations` comment already makes in that file.

The sidebar entry moves to the explicit `superAdminOnly` flag (matching Sentry)
instead of `module: "ai_console"`. That spelling produced the right outcome for
the wrong reason: it depended on `ai_console` **never** being added to
`AVAILABLE_MODULES`, so someone adding it for an unrelated feature would have
silently surfaced impersonation in the nav.

### 4. Risk

No behaviour change for any current caller — a super_admin was already the only
role that got through. A non-super_admin now receives 403 from the dependency
rather than from the function body; same status, same outcome.

`test_ai_console_super_admin_mount.py` (6 tests) pins both layers, including
that a plain `admin` role holding modules is refused — `admin` is module-scoped
in this codebase, not a super-admin bypass.

---

## Item 2 — heatmap endpoint had no rate limit, and sent the wrong grid size

### 2a. Rate limit (was on the review's list)

Every sibling driver endpoint has one; this had none. Now 20/minute per user.
The client polls on the interval this endpoint itself serves, clamped to a 30 s
floor — ~2/minute normally — so this is ~10x headroom and only bites a runaway
client. "The response is usually cached" is not a ceiling: a stuck retry loop
or a stolen token can still drive uncached rebuilds against `rides`. Keyed per
user so one bad device cannot exhaust the budget for drivers sharing a carrier
NAT.

### 2b. Cell size — **not on the list; found while fixing 2a**

The v2 payload did not carry the grid size it bucketed with. The driver app
draws each cell as a rectangle and re-derives its corners from the centroid,
hardcoding `0.004`/`0.006` — correct only while those were global constants.

**The per-area config work made that wrong.** For an area with a tuned cell
size, polygons were drawn at the wrong size and — because the client re-floors
the centroid with its own constant — snapped into the **wrong grid square
entirely**, pointing drivers at the wrong block.

Fixed by sending `cell_lat_deg` / `cell_lng_deg` and having both renderers use
them, falling back to the constants when absent. Additive: an older app build
ignores the fields and keeps its current behaviour exactly.

This is a correctness bug in a feature shipped earlier in this same branch. It
would only have been visible in an area whose cell size had actually been
tuned — i.e. nowhere yet, which is why nothing caught it.

---

## Item 3 — Android Auto: one poller, one online signal, no frozen map

### 1. Issue

Android Auto projects from the running phone app, so both surfaces called
`useDemandHeatmap()` and each instance owns a timer. Three problems:

- **Double polling.** Plugging in a head unit doubled the request rate, battery
  and data for identical payloads.
- **Divergent online signal.** The car read `authStore.driver.is_online`; the
  phone dashboard used `useDriverDashboard`'s own state. A driver taken offline
  by dispatch — expired insurance, say — stopped seeing demand on the phone and
  **kept seeing it on the head unit**, told to reposition for work they could
  not be offered.
- **A frozen map that looks live.** With the phone locked while projecting the
  poll pauses, but the car screen stays lit on the last payload with no way for
  the driver to tell it stopped updating.

### 2. Fix

`hooks/demandHeatmapShared.ts` makes the phone the single publisher and the car
a read-only subscriber: one timer, one online signal, one payload.

- **Publisher presence is refcounted**, not a boolean: React can mount the
  replacement before unmounting the outgoing instance, and a boolean would have
  the stale unmount blank the car while a live publisher ran.
- **With no publisher the snapshot resets to idle**, so the car shows nothing
  rather than freezing. An empty map is honest; a stale one is not.
- The car also gates on the shared `status`, so it renders only fresh data.

### 3. Risk

**Blast radius:** two call sites, both changed —
`app/driver/(tabs)/index.tsx` (now the publisher, API unchanged) and
`lib/androidAuto/carSurface.tsx` (now a subscriber). No other file imports the
hook. Grep-verified.

**What could regress:** the car is now blank in a state where it previously
showed something — specifically when the phone's dashboard screen is unmounted.
That is the intended trade (the something it showed was stale and unlabelled),
but it is a visible behaviour change for a projecting driver who navigates away
from the dashboard tab.

`layer` stays local to each surface, so the phone's layer selector does not
reach through to the car.

---

## Item 4 — driver-app rendering guards

Three fixes in the same few lines of `HeatmapCells.tsx`:

- **Non-finite coordinates are filtered before `<Polygon>`.** This is a
  **crash** guard, not a rendering one — react-native-maps hands coordinates to
  the native map and a `NaN` takes the app down on Android. One corrupted cache
  row or a partial payload was enough. Tagged LOW by likelihood; it is the
  highest-consequence item in this batch.
- **Sorts a copy.** With `region` null — which the phone always passes —
  `filtered` **was** the hook's own `cells` state array, so this sorted it in
  place and reordered what every other consumer sees.
- **Polygons key on coordinates, not array index.** The list re-sorts by weight
  every poll, so index-prefixed keys changed for most cells whenever demand
  shifted, and React tore down and recreated native views that had only moved.

The car surface applies the same finite filter.

---

## Item 5 — admin reliability and a11y (LOW tier)

| Item | Fix |
|---|---|
| `spinr:heatmap:` / `spinr:surge:status:` missing from `KNOWN_KEY_PREFIXES` | Registered, so the ops Redis dashboard attributes them instead of bucketing into `__other__` — exactly when you need the attribution is when someone is hunting memory growth |
| Two parsers for one allowlist field | `lib/allowlist-ids.ts` — save path and the count below it now share one parser. They split differently, so a space-separated paste displayed as 1 ID and saved as several |
| Allowlist accepted junk silently | Non-UUID entries are flagged inline (still saved — the backend is the authority; silently discarding input is how you get "I added them and nothing happened") |
| Fixed 120 s demand polls | Jittered ±10% on both admin pages. A shift handover or an incident everyone opens at once put every tab's poll on the same instant |
| 24 h surge chart repeated clock labels | Date shown at ≥24 h, not >24 h — a full day spans two dates, so "14:30" appeared twice and the chart read as doubling back |
| No unsaved-changes guard | `useUnsavedChangesWarning` on both config panels. Covers browser-level exits only; Next's client-side routing does not fire `beforeunload`, and that limitation is stated in the hook rather than implied |

Already fixed in the earlier H-tier batch and verified rather than re-done:
emoji `aria-hidden`, AD-02 select and chart `aria-label`s, the truncation
notice, the driver-app `Array.isArray` / `isFinite(refresh_seconds)` guards,
the layer-switch refetch, the bare `except` on settings load, and the forecast
DB failure log level.

---

## Item 6 — AD-01 had 0% real test coverage

`monitoring-map.tsx` and `toolbar.tsx` are stubbed in the page smoke suite, so
every test that appeared to cover the live demand overlay executed neither file.

- `monitoring-toolbar.test.tsx` (15 tests) — the demand toggle (AD-01's entire
  entry point), every filter toggle, `aria-pressed` state in both directions,
  emoji hidden from assistive tech, the area/vehicle filters and the search.
- `monitoring-map-demand-fill.test.ts` (10 tests) — `areaFillColor` /
  `areaFillOpacity` were module-private and are now exported for this. Covers
  the overlay gate, per-area lookup, and **the missing-area case that had the
  bug**: an empty response now reads as no-data everywhere rather than as a
  uniformly healthy city.

Rendering `MonitoringMap` itself still needs maplibre-gl and is still not
attempted — the exported functions are this file's own logic, and that boundary
is stated in the test file rather than left implied.

---

## 7. Files modified

| File | What changed |
|---|---|
| `backend/routes/admin/__init__.py` | `ai_console_router` mounted under `require_super_admin` |
| `backend/utils/rate_limiter.py` | New `heatmap_read_limit` (20/min, per user) |
| `backend/routes/drivers/_deps.py`, `profile.py` | Limiter applied; `cell_lat_deg`/`cell_lng_deg` added to the v2 payload |
| `backend/utils/redis_client.py` | Two prefixes registered |
| `backend/tests/test_ai_console_super_admin_mount.py` | New: 6 tests |
| `backend/tests/test_admin_module_list_parity.py` | `ai_console` removed from the known-ungrantable allowlist |
| `driver-app/hooks/demandHeatmapShared.ts` | New: single-publisher snapshot |
| `driver-app/hooks/useDemandHeatmap.ts` | Publishes; exposes server cell size |
| `driver-app/components/dashboard/HeatmapCells.tsx` | Finite filter, copy-sort, coordinate keys, server cell size |
| `driver-app/lib/androidAuto/carSurface.tsx` | Subscriber, not poller; status-gated |
| `driver-app/app/driver/(tabs)/index.tsx` | Passes cell size through |
| `driver-app/__tests__/hooks/demandHeatmapShared.test.ts` | New: 9 tests |
| `driver-app/__tests__/components/heatmapCellGeometry.test.ts` | New: 17 tests |
| `admin-dashboard/src/components/sidebar.tsx` | AI Console uses `superAdminOnly` |
| `admin-dashboard/src/lib/allowlist-ids.ts` | New: shared parser |
| `admin-dashboard/src/hooks/useUnsavedChangesWarning.ts` | New |
| `admin-dashboard/src/app/dashboard/service-areas/page.tsx` | Shared parser, invalid-entry warning, axis fix, unsaved guard ×2 |
| `admin-dashboard/src/app/dashboard/heatmap/page.tsx` | Jittered poll |
| `admin-dashboard/src/app/dashboard/monitoring/page.tsx` | Jittered poll |
| `admin-dashboard/src/app/dashboard/monitoring/monitoring-map.tsx` | Fill functions exported for test |
| 3 new admin test files | 40 tests |

## 8. Rollback plan

Four independent commits, each reverting alone.

- **AI Console gate** — revert restores the previous mount. Enforcement is
  unchanged either way (the in-body calls do the work), so there is no window
  where the endpoints are less protected.
- **Backend heatmap** — the rate limit reverts to unlimited; the payload fields
  are additive and any client ignoring them is at its previous behaviour.
- **Driver-app** — pure client logic, no persistence. Reverting restores the
  two-poller arrangement. Note the asymmetry: rolling forward is instant per
  build, rolling back leaves already-updated clients on the shared poller until
  they update again — which is the safe direction to be stuck in.
- **Admin** — UI only. The `KNOWN_KEY_PREFIXES` addition is display-only and
  affects no key that is read or written.

Nothing here touches a schema, a migration, live money, or ride state.

## 9. Verification performed

- [x] **Backend: full suite** (`-m "not slow"`), plus targeted runs on the redis,
      session-revocation, RBAC, parity and ai-console slices.
- [x] **Driver-app: 423 tests, 56 files, 0 failures** (was 397 — 26 new).
      `tsc --noEmit` clean.
- [x] **Admin: 277 tests, 28 files, 0 failures** (was 232 — 45 new).
      `tsc --noEmit` clean and **`npm run build` — a real production build**.
- [x] `ruff check` + `ruff format --check` clean on every touched Python file.
- [x] The crash guard is tested against `NaN`, `Infinity` and a non-finite
      weight, and asserts that every surviving cell produces finite corners.
- [x] The cell-size defect is stated as a test: the same centroid at two grid
      sizes lands in different squares, so a regression to hardcoded constants
      fails rather than merely looking different.

## 10. What was NOT verified

- **No device run, and this batch is mostly driver-app.** The Android Auto
  changes in particular were not exercised on a head unit — not the shared
  poller under a real projection session, not the locked-phone case, not the
  blank-when-no-publisher behaviour. All of it is unit-tested and typechecked;
  none of it has been seen on hardware. This is the single biggest gap here.
- **The crash guard has not been reproduced.** No corrupted cache row was
  synthesised end-to-end; the filter is tested at the function level, and the
  claim that a `NaN` coordinate crashes native Android is from the library's
  behaviour, not an observed crash in this app.
- **The rate limit was not load-tested.** 20/minute is reasoned from the 30 s
  poll floor, not measured against a real fleet.
- **Jitter was not measured.** The thundering-herd it prevents is structural,
  but no before/after request distribution was captured.
- **`beforeunload` does not cover in-app navigation.** Moving between dashboard
  pages still discards unsaved config silently. Stated in the hook; not fixed,
  because it needs a router interception this codebase does not have anywhere
  and adding it here alone would be inconsistent.
- **No staging or browser run** for any admin change; no visual-regression
  tooling exists for this surface, so layout and contrast of the new inline
  warning were reasoned about, not screenshotted.
- **`pricing`, `surge` and `compliance` remain known debt**, pinned in the
  parity test's allowlists. Unchanged by this batch.
