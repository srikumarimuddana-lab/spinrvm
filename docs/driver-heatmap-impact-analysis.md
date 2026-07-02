# Driver Demand Heatmap — Impact Analysis & Gap Register

Date: 2026-07-01 (audit revisions 2026-07-02) · Branch: `claude/driver-heatmap-config-ayl7mp`

## 1. What existed before this change

The feature was already present in skeleton form, shipped across three earlier
pieces that were never joined up into a configurable product:

| Piece | Where | State before |
|---|---|---|
| Per-area flag | `service_areas.show_demand_heatmap` (migration 81) | Bare boolean, default `false` |
| Driver endpoint | `GET /drivers/demand-heatmap` (`backend/routes/drivers.py`) | Hardcoded last-7-days window, all ride statuses, **raw pickup coordinates** with weight `1`, no refresh contract |
| Driver app | `driver-app/app/driver/(tabs)/index.tsx` | `<Heatmap>` overlay fed by a **one-shot** inline `api.get` on the idle transition — data went stale until the next idle transition |
| Admin UI | Service-areas General tab | Single on/off toggle |
| Admin Settings "Heat Map Configuration" card | `admin-dashboard .../settings/page.tsx` | **Global** `heat_map_*` keys that style the *admin dashboard's own* heatmap page — they never affected the driver app (a naming trap; see gaps) |

## 2. What this change delivers

1. **Migration 202** — three per-service-area columns with CHECK constraints:
   `heatmap_data_window_hours` (1–720, default 168), `heatmap_refresh_seconds`
   (30–3600, default 300), `heatmap_data_source`
   (`all_requests` | `completed_rides` | `missed_rides`). Defaults reproduce
   pre-change behavior exactly, so the migration is a no-op until an admin
   touches the new controls.
2. **Backend** — the driver endpoint honors the config, and now aggregates
   pickups onto a ~110 m grid (3-decimal rounding) with count weights instead
   of returning each rider's exact pickup point. Cells with fewer than 3
   requests are suppressed (k-anonymity floor) so a lone rural pickup can't
   be re-identified from its cell. Config is re-clamped at read time so a
   pre-constraint row can't produce a runaway polling cadence, and the
   endpoint carries a dedicated 10/min rate limit (the server-directed
   polling floor is 30 s, so legitimate clients sit at ~2/min). Migration
   203 adds the previously missing `rides(service_area_id, created_at DESC)`
   index the query relies on.
3. **Driver app** — the inline one-shot fetch is replaced by a shared
   TanStack Query hook (`useDemandHeatmap`) whose `refetchInterval` reads
   `refresh_seconds` off the last payload: ops tune the cadence per area from
   the dashboard, no app release needed. The query is disabled during rides.
4. **Admin dashboard** — the toggle grew into a "Demand Heatmap" section
   (source / window / refresh) in the service-area General tab, mirroring the
   Surge Pricing block's layout and save semantics.
5. **Tests** — 14 backend unit tests (gating, config, clamping, bucketing,
   k-floor suppression, the no-exact-coordinates guarantee) and 7 driver-side
   normalization tests (malformed payloads degrade to "no overlay", never a
   crash or fast-poll).

## 3. Impact analysis

### Data flow / privacy (PIPEDA)
- **Improvement**: rider pickup coordinates previously left the backend at
  full precision to every driver in the area. They are now aggregated to
  ~110 m cells before leaving the endpoint. This is a data-minimization fix,
  not just a feature: the overlay needs block-level density, never addresses.
- **Residual risk (accepted, mitigated)**: aggregation alone is not
  anonymization — a weight-1 cell in a low-density area still says "one ride
  started in this ~110 m box", which local knowledge could re-identify.
  Cells below 3 requests are therefore suppressed before leaving the
  endpoint (`_HEATMAP_MIN_CELL_COUNT`, pinned by a regression test). The
  trade-off: very-low-volume areas may show an empty overlay; raising the
  data window is the admin's lever there.
- GPS coordinates still never appear in logs (unchanged; nothing new is
  logged by the endpoint).
- `missed_rides` source exposes only the same aggregated cells filtered by
  status — no new PII surface.

### Load / performance
- Old client behavior: one fetch per idle transition (unbounded staleness).
  New behavior: one fetch per `refresh_seconds` while idle (default 300 s),
  and focus/reconnect refetches also respect that cadence (staleTime tracks
  the server value).
- Per request: the driver row comes from the auth-warm Redis cache, the
  area row is one PK read, and the aggregated payload is cached per
  (area, source, window) with `ttl = min(refresh_seconds, 300)` — N idle
  drivers in one area cost **one** rides scan per TTL, not one per driver
  per tick. The scan itself is backed by `idx_rides_service_area_created`
  (migration 203; before it this pattern sequential-scanned `rides`) and
  capped at 2 000 rows — in areas busier than 2 000 requests per window the
  effective window is shorter than configured (`total_rides` saturates
  with it).
- A dedicated 10/min rate limit — keyed **per user**, not per IP, so
  drivers behind carrier-grade NAT don't share a bucket — stops a scripted
  client that ignores `refresh_seconds` from using the scan as a DoS lever.
- Response size *shrinks*: bucketing collapses up to 2 000 points into unique
  cells; weights carry the density that duplicate points used to.
- No new background loop, no WebSocket traffic, no Redis dependency.

### Ride state machine, money, dispatch
- Untouched. The endpoint is read-only over `rides`; no state transitions,
  no fare code, no dispatch behavior change. Surge engine unaffected (it
  reads its own demand counts).

### Backward / forward compatibility
- Response keeps `enabled` / `points` / `total_rides`; old app builds ignore
  the new `refresh_seconds` field and keep their one-shot behavior.
- New app builds against an old backend (no `refresh_seconds`) fall back to
  a 300 s default in the client normalizer.
- Pre-migration DB rows (columns absent) get the same defaults server-side.

### Failure modes
- Config row garbage (manual DB edit predating constraints) → server-side
  clamp to sane bounds; client clamps again (30 s floor).
- Endpoint error / malformed payload → normalizer returns empty overlay;
  TanStack retries on its own schedule. Map renders normally without the
  overlay — degraded, not broken (informational surface, silent-fail is
  appropriate here, matching the surge badge's contract).

## 4. Gap register

Closed by this change:

- [x] Config was enable-only → now source/window/refresh per area
- [x] No client refresh → server-driven polling while idle
- [x] Exact rider pickup coords shipped to drivers → ~110 m aggregation
      with a k-anonymity floor (cells under 3 requests suppressed)
- [x] All points weight=1 (density invisible) → count weights per cell
- [x] No covering index on the heatmap query → `idx_rides_service_area_created`
- [x] Only the global rate limit guarded the endpoint → dedicated 10/min cap,
      keyed per user (per-IP would 429 legitimate drivers behind carrier NAT)
- [x] Zero test coverage on the endpoint or client mapping → 33 tests
      (18 backend endpoint/config/cache, 7 payload normalization, 8 render
      spec/legend)

Closed by the post-merge audit (8-angle review, 2026-07-02):

- [x] `missed_rides` counted every cancellation as unmet demand → now filters
      `cancellation_type='no_drivers_found'` (migration 38), so rider
      changed-my-mind cancels can't steer drivers to the wrong blocks
- [x] Migration 203's block comment broke the CONCURRENTLY runner path
      (splitter chokes on `/* ...; */`) → line comments; runner-verified
- [x] Heatmap cells persisted 24 h in AsyncStorage and could hydrate stale
      (or another account's) demand at cold start → `meta.noPersist` +
      persister filter
- [x] Unrelated admin saves would 500 against a pre-migration backend →
      new columns sent only when touched (surgeTouched pattern)
- [x] Admin entering `0` silently got the default → NaN-aware clamps
- [x] Every driver re-scanned rides per poll → per-area Redis payload cache
- [x] Config bounds/enums duplicated across four files → `utils/heatmap.py`
      single source (pydantic imports the same constants the endpoint clamps
      with); k-floor and grid size live there as named PIPEDA primitives
- [x] Fixed teal→gold→red gradient (red-green CVD failure; one hot cell
      flattened the scale; no labeling) → validated lightness-monotonic
      ramp, theme-aware opacity, log-damped weights, on-map legend

Known gaps remaining (candidates for follow-up tickets):

1. **Naming collision**: the admin Settings page has a global "Heat Map
   Configuration" card (`heat_map_*` in `app_settings`) that styles the
   *admin dashboard's* heatmap page only. Operators may reasonably believe it
   configures the driver overlay. Recommend renaming that card ("Admin
   Analytics Heatmap") or folding both into one surface.
2. ~~Render styling is app-side only~~ **Closed (migration 204)**: admins now
   pick a per-area color theme (Inferno / Ocean / Viridis) in the Demand
   Heatmap section, delivered via the payload's `color_theme`. Deliberately a
   curated enum, not free-form colors: every ramp is lightness-monotonic and
   CVD-validated, so an operator can't configure an inaccessible ramp. The
   authoritative ramps live in
   `driver-app/components/dashboard/demandHeatmap.ts`; unknown names fall
   back to Inferno, so themes can be retired without breaking old rows or
   old app builds. Radius/damping remain app-side.
3. **No time-of-day weighting**: a 168 h window mixes Friday-night and
   Tuesday-morning demand. A "same hour-of-week" mode would make the overlay
   predictive rather than historical. (Kept out of scope: needs product
   decision on UX.)
4. **iOS renders via Apple Maps** (`PROVIDER_GOOGLE` is Android-only in this
   screen). react-native-maps `<Heatmap>` support on Apple Maps is limited;
   the overlay silently no-ops where unsupported. Needs a device-matrix pass
   before advertising the feature to iOS drivers.
5. **Sub-areas (airports)**: a driver attached to a parent area sees only
   rides tagged with the parent `service_area_id`. Rides tagged to child
   airport zones are excluded from the parent's overlay. Decide whether to
   aggregate children into the parent map.
6. **Driver-app UI affordance**: there is no legend/toggle in the driver app
   to explain or hide the overlay; drivers just see colors appear. UX copy
   ("busier blocks") + an on/off preference would help adoption.
7. **Admin page component tests**: `service-areas/page.tsx` is only covered
   by the render-smoke test; the new section's clamp logic is enforced by
   backend validation but has no dedicated frontend test. (Five pre-existing
   smoke failures on other dashboard pages — users, earnings, analytics,
   corporate-accounts, disputes — predate this work and are unrelated.)
8. **Metrics**: no `spinr_dispatch_heatmap_*` counters exist. If the KPI
   hypothesis is "heatmap improves driver utilization ≥ 55%", add a fetch
   counter + a driver-positioning A/B signal before rollout judgment.

## 5. Rollout notes

- **Apply migrations 202, 203, and 204 BEFORE deploying the backend and admin
  dashboard.** Migrations run manually (`migrate.py`) while Fly/Railway and
  Vercel auto-deploy from main — under the inverse order, creating a service
  area 500s until 202 lands (updates are protected by touched-gating, but
  creates always insert the new columns). 202 is additive and safe with
  traffic in flight (migration checklist verdict: safe to apply); 203 is
  `CREATE INDEX CONCURRENTLY`, no write lock on `rides` — and must keep its
  double-dash comment style (the CONCURRENTLY runner path splits on
  semicolons and only strips `--` lines).
- Feature stays dark until an admin enables it per area; defaults reproduce
  the old behavior for areas where the old boolean was already on.
- Client change requires an app release (Expo EAS, `[build]` commit tag) for
  the polling behavior; old builds keep working against the new backend.
