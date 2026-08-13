# Pre-Deploy Multi-Agent Review — Driver Heatmap PR (P0–P3 + Admin Track)

**Date:** 2026-08-13
**Branch:** `claude/driver-app-heatmap-planning-o7v5ic` (HEAD `af8e3aa`)
**Method:** 15 parallel specialized reviewers — 2 senior-dev passes (correctness, performance), tester (test coverage), product manager (UX completeness), business analyst (surge rules), accessibility, admin RBAC, backend heatmap integration, migrations, driver-app integration, dispatch, money/payments, rider-app, realtime/background-loops, security/auth. Every finding below was verified against actual code (frontend and the backend handlers it calls), not assumed. Test suites were executed where cited.

**Scoping note (important):** local `main` was 130 commits behind `origin/main`. The PR's true new surface vs current `origin/main` is: **backend heatmap endpoints, driver-app heatmap UI, admin AD-01/02/03/05, migration 307, and additive `shared/theme` keys.** Dispatch, payments, rider-app, auth, middleware, lifespan, and migrations 301–306 visible in the stale-range diff are **already merged to main** via earlier PRs. Findings on those are filed under "Already on main" below — real, but not introduced by this PR.

---

## VERDICT: NO-GO until the Deploy Gate below is cleared

No data-corruption, money, dispatch, or authorization-bypass defect was found anywhere. The live-app core (dispatch, payments, WS, rider-app) is untouched and verified safe. The blockers are concentrated in the **new** heatmap/admin surfaces: one broken cross-phase integration point (the admin config write path is a silent no-op), one privacy hole armed behind it, one migration numbering collision, one compliance-relevant chart gap, and self-inflicted test/attestation regressions. All fixes are small and precisely specified.

---

## DEPLOY GATE (blockers — fix before merge)

**B1. Admin heatmap config is an end-to-end silent no-op** *(backend-integration, correctness, security, RBAC — 4 independent confirmations)*
`backend/routes/admin/settings.py:158-389` — `SettingsUpdateRequest` (`extra="ignore"`) declares **none** of the 7 heatmap keys the new AD-05 tab PUTs; all are dropped at validation, the handler returns 200, the toast says success, and the audit log records `changed_keys: []` (a fabricated audit trail). `GET` never returns the 5 numeric keys (absent from `AppSettings`), and **no migration adds the columns to the `settings` table**. Consequence: heatmap v2 can never be enabled, tuned, or rolled back through any supported path; an operator who "raises" the k-floor privacy control believes they did, with audit evidence, and nothing changed.
*Fix:* migration adding the 7 columns; add the 7 fields to `SettingsUpdateRequest` **with bounds** (`heatmap_k_floor ge=1 le=50`, cell degs `gt=0 le=0.05`, `heatmap_decay_half_life_days gt=0 le=30`, `heatmap_refresh_seconds ge=30 le=600`, allowlist `max_length=500`); add the 5 numerics to `AppSettings`; add a persistence round-trip test (PUT → GET reflects it). **Must land in the same change as B2.**

**B2. k-anonymity hole in the v2 baseline layer (PIPEDA)** *(backend-integration + security)*
`backend/routes/drivers/profile.py:527-547` — `bl_ok = bl_val > 0` checks the **normalized** baseline score, so a cell with exactly one historical ride (someone's recurring home pickup) passes the floor and is emitted with its ~450 m-cell centroid; it can surface as a "High demand" hotspot chip via the client's blend layer. Live and scheduled layers floor correctly. Dormant today only because B1 makes v2 unenableable — fixing B1 without B2 arms a live privacy leak.
*Fix:* track raw baseline counts and require `baseline_raw >= k_floor` before emitting/normalizing, zeroing otherwise (same semantics as live/scheduled).

**B3. Migration numbering collision: two different `307_*.sql`** *(migrations)*
`origin/main` merged `307_email_send_log_error_detail.sql` today; this branch carries `307_rides_area_created_idx.sql`. Filenames differ so git merges both silently, and **both CI numbering gates were traced and confirmed blind to it** (each builds its "existing numbers" set from the PR's own checked-out tree).
*Fix:* `git mv` to `308_rides_area_created_idx.sql`; merge/rebase current `origin/main` into the branch so CI evaluates real state. (The index itself is correct: `CREATE INDEX CONCURRENTLY IF NOT EXISTS`, properly routed through `migrate.py`'s autocommit path — it will **not** lock the live `rides` table. Pre-apply check: verify no `INVALID` leftover index of this name exists, since `IF NOT EXISTS` silently skips one.)

**B4. Manual surge overrides write no history — AD-02 chart is blind to the exact periods compliance cares about** *(surge/BA, corroborated by correctness)*
The only real UI path for manual overrides (`PUT /api/admin/service-areas/{id}` via `admin_update_service_area`, `backend/routes/admin/service_areas.py:493-691`) never inserts a `surge_pricing` row, and the auto engine skips manual areas — so during any manual override (including >2.5×) the Surge History chart flatlines at the last auto value and its "Contains manual overrides" indicator can never fire. The one endpoint that does write manual history rows has zero frontend callers, and it separately contains a latent PK-collision bug (`update_one` on all rows with a fresh `id` → unique violation → 500; `service_areas.py:751-757`).
*Fix:* insert a `surge_pricing` row (with live-computed demand/supply/ratio, `source: "manual"`) whenever `surge_source`/`surge_multiplier` changes in `admin_update_service_area`; delete or fix the two dead duplicate endpoints so all surge writes flow through one history-appending path.

**B5. AD-03 forecast chart is dead — all bars render zero** *(3 independent confirmations)*
`admin-dashboard/src/app/dashboard/heatmap/page.tsx:177-181` maps `predicted_demand`/`demand`/`time`/`label`/`slots` — none exist. Backend returns `{forecast: [{hour, day_name, predicted_rides, ...}]}`. Every bar renders at the 4% floor with tooltips reading "0 predicted" — actively wrong ops signal.
*Fix:* map `s.predicted_rides` and label from `s.hour`/`s.day_name`; drop the speculative fallbacks; add a regression test asserting a non-trivial value produces a bar above the floor.

**B6. Commit `af8e3aa` broke the test suite and typecheck at HEAD** *(tester, correctness — empirically proven)*
(a) The `@/lib/api/settings-ai` mock exports 2 of 7 real functions → via the barrel's `importOriginal`, `getEmailDeliverability` becomes undefined → settings smoke test fails (`1 failed / 22 passed`); while any test is red, vitest suppresses the entire coverage report. (b) The `@/lib/api/analytics-payouts` mock omits `getPayoutsOverview`/`closePayoutPeriod`/`resolveDispute` — dormant landmine for the first interaction test. (c) 5 duplicate keys in the lucide mock → `tsc --noEmit` fails with TS1117 at HEAD.
*Fix:* rebuild both submodule mocks with the `importOriginal`-spread pattern; delete the 5 duplicate keys (keep only `Flame`, `ArrowRightLeft`).

**B7. AD-05 config tab is unusable by assistive technology** *(accessibility)*
`service-areas/page.tsx:2315-2319` v2 toggle: icon-only button, no accessible name, no state. `:2374-2390` + call sites: all 5 numeric fields and the driver-ID textarea have labels not programmatically associated (`htmlFor`/`id` missing). Mechanical fixes; the repo's own `Switch`/`Label` components are already imported one file over.

**B8. Change-impact log contains false attestations** *(correctness, security, tester)*
`docs/change-log/2026-08-13-admin-heatmap-ops.md`: "tsc clean" and "tests pass" are false at HEAD (B6); "No backend changes needed" is false for AD-05 (B1); the AD-01 before-snippet cites the wrong prior fill color (`#3b82f6` vs actual `#8b5cf6`); the importer list omits `monitoring-map.tsx`; the rollback plan ("re-edit settings / edit via Supabase") is impossible while B1 stands and the columns don't exist. In a repo whose merge gates are these attestations, correct the log alongside the code.

---

## HIGH (fix before ops relies on these screens / before v2 rollout)

**H1. Polling-interval clamp missing on both ends.** Backend serves `heatmap_refresh_seconds` raw (`profile.py:397`); client applies it raw (`useDemandHeatmap.ts:125,157-167`). A value of 1 → fleet-wide ~1 s polling; the disabled-area path returns **before** the cache, so those drivers hit the DB directly. *Fix:* clamp in `profile.py` (`max(v,30)`, mirror the existing `_clamp` used by `get_driver_config`), clamp client-side (`min(max(v,30),600)`), plus B1's Pydantic bounds. Also `k_floor = max(v,1)` at point of use — `k_floor=0` via any path disables the privacy floor for v1 too.

**H2. Unguarded Redis cache read can 500 the existing live heatmap.** `profile.py:382` — read unguarded while the write is guarded; with `REDIS_URL` set, a Redis blip → unhandled ConnectionError → 500 for every driver's poll (old builds included) while the DB is healthy. *Fix:* try/except → treat as cache miss, log + metric.

**H3. "Unmet Gap … unfulfilled requests" is factually wrong.** `demand_count` counts all active-status rides in a 10-min window (including matched/in-progress) while `supply_count` excludes their drivers — a fully-served rush hour shows a large red "unmet" figure (worked example: 20 demand/18 matched/2 searching/5 idle → "15 unfulfilled" vs 2 real). *Fix:* relabel ("Demand pressure — active demand vs idle drivers; not stranded riders"), and/or add a backend `searching`-only count for a true unmet metric.

**H4. Ratio bands mislabeled as multipliers; green band hides two live surge tiers.** Legend "Critical (≥3.0×)" names a fare multiplier that can never legally exist (cap 2.5×); "Balanced (0.5–1.2×)" merges the 1.25× and 1.5× tiers so already-surging areas look quiet. *Fix:* drop "×" (use "ratio ≥ 3.0"), retitle "Demand : Supply Ratio", split the green band at 0.8 so every color maps to exactly one engine tier.

**H5. New continuous polling lands on two pathological backend queries, and disturbs the admin's map.** (a) Heatmap-page demand poll is **ungated** — 2 requests/120 s from page mount forever (monitoring page gates correctly). (b) `/api/admin/surge/status` is an N+1: re-fetches the entire platform's available-driver set once per area (1+2N sequential queries), recomputing what the engine computed 120 s ago — this PR gives it its first continuous poller. (c) `/api/admin/analytics/demand-forecast` fetches the **oldest** 10 k completed rides (`desc` missing), no date/area filter in SQL, `SELECT *`, no cache — once the table passes ~10 k completed rides the forecast silently degrades to the default curve forever; HM-23 also put this on the driver path per cache miss. (d) Each poll re-renders `HeatMapPage`; non-memoized props re-fire the map effect → **animated `fitBounds` snaps the admin's viewport back every ~2 min** (confirmed chain). *Fix:* gate the poll behind a toggle; `useMemo` the heat-map props; fix the forecast query (`status+area+created_at >= cutoff` in SQL, `desc=True`, `columns="created_at"` — it then uses index 307/308); hoist the driver fetch out of the per-area loop and/or add a 30–90 s Redis cache to `get_surge_status` (precedent exists in `analytics.py`).

**H6. Offline drivers see a permanent loading-shimmer legend pill.** `useDemandHeatmap.ts:76,144-152,206` + `(tabs)/index.tsx:815-825` — status stays `loading` while offline (no fetch ever resolves it), and the render gate lacks an `isOnline` check; flag-off is therefore not visually clean pre-branch. *Fix:* gate the legend on `isOnline` / add an `idle` status excluded from `visible`.

**H7. i18n (HM-04) is not actually wired.** Components hardcode English (`DemandLegend`/`ForecastStrip`/`HotspotChips`); 17 of 18 new `en.json` keys are dead; `fr.json`/`es.json` have zero heatmap keys and `translate()` returns the raw key on miss — the one wired call renders `heatmap.airport.zone` literally to FR/ES drivers. *Fix:* route all strings through `t()`, add full key sets to fr/es (or add an en fallback).

**H8. Module-RBAC mismatches make each new admin section silently dead for module-scoped admins.** The shipped `operations` preset (no `settings` module) sees the config tab render **hardcoded defaults indistinguishable from live values** after a swallowed 403; the heatmap page needs `service_areas`+`dashboard` and its `Promise.all` failure renders *"Surge engine may not be running"* (misdiagnosing permissions as infra); monitoring's Demand toggle is a silent no-op for `rides`-only admins; the `heatmap` module string gates **nothing** in the backend. No authorization bypass anywhere — backend gates hold and secrets are masked. *Fix:* gate each new widget on its real module(s) with a distinguishable "insufficient permissions" state; split the `Promise.all`; decide what the `heatmap` grant means (follow-up).

**H9. Failed config load + save = silent reset of the 7 heatmap keys.** On GET failure the tab renders `HEATMAP_DEFAULTS` with no error state; a subsequent save PUTs all 7 fields, stomping k-floor/allowlist back to defaults. (Refutation narrowed scope: only the 7 keys — the backend merges per-field, credentials are not at risk.) *Fix:* on load failure disable the form + show explicit error/retry; send only changed fields.

**H10. Demand overlay: silent errors, no staleness, and "no data" painted as "Oversupply".** `monitoring/page.tsx:427-446` `.catch(() => {})`; no last-updated anywhere (the type drops the field); areas absent from the response (airport sub-zones, new areas) get the same purple as ratio < 0.5. *Fix:* error + `fetchedAt` state, staleness chip (the page already has this pattern for WS), neutral fill + "No data" legend row for missing areas.

**H11. AD-02 truncates silently: 24 h/48 h/7 d selections show ~16.7 h.** Backend `limit=500` vs engine's 30 rows/hour/area; Peak/Avg computed over the truncated window while the selector claims the full range. *Fix:* downsample server-side for wide windows, or return `truncated: true` + honest frontend label.

**H12. Driver-app's new base UI is not feature-flagged.** The v2 flag gates only data fields; the Polygon renderer + legend pill + 90 s polling replaced the old `<Heatmap>` for the whole v1 fleet unconditionally — against pre-merge gates #3/#5. *Fix:* gate renderer/legend/polling behind the flag (pairs with the kill-switch work below).

**H13. Accessibility HIGHs.** Toolbar toggles (incl. new 🔥 Demand) expose state by color only — add `aria-pressed`; forecast bar values exist only in `title` tooltips (invisible to AT/keyboard); the monitoring map's demand data has no text alternative on that page (no polygon click handler); computed contrast failures: `text-destructive` on dark cards (~4.09:1 — the exact failure the repo's own globals.css routes around), `orange/amber/green-600` on light (~3.2–3.6:1), `purple-600` on dark. *Fix:* aria-pressed; sr-only values per bar; polygon click → text panel; theme-aware semantic tokens.

**H14. Allowlist contract is doubly wrong in the UI.** Backend semantics: allowlist grants v2 **while the global flag is OFF** and is ignored when ON; the tab copy says the opposite ("only these drivers see v2 when enabled"). And it compares `users.id` while the label says "Driver IDs" (admin screens display `drivers.id`) — pasted values silently match nothing. *Fix:* correct copy to dark-launch semantics; label "driver **user** IDs" or resolve server-side.

---

## MEDIUM (schedule; several fold into the kill-switch/per-area work)

- **Config readers have no type/range defense** (`profile.py:393-397`): `null`/`"abc"` → 500 per driver poll; `cell_lat=0` → ZeroDivisionError; negative decay → nonsense weights. Defensive cast+clamp at read site (independent of B1's bounds — the DB row is writable out-of-band).
- **v2 surge mirror skips `surge_enabled`** (`profile.py:550-553`): drivers can be shown active surge riders aren't paying (the public endpoint zeroes it; comment says stale flags "must never surface"). Also `float(...)` crashes on explicit NULL. Reuse the public-projection guard.
- **Demand coloring ignores `surge_enabled`**: areas with surge deliberately off still render red "Critical" (backend sends the flag; frontend types drop it). Gray-out/annotate.
- **Scheduled layer has no lower bound** (`profile.py:503-512`): counts past-due stuck scheduled rides (e.g. with the dispatch kill switch on) — phantom demand cells. Add `$gte: now`.
- **Error pill isn't silent + polling never stops in disabled areas** (driver-app): persistent "Demand info unavailable" pill after 3 failures; poller runs forever in `disabled` areas. Hide on error, stop when disabled.
- **Android Auto**: second independent `useDemandHeatmap` instance (double polling); its `isOnline` source diverges from the dashboard's (server-forced-offline driver keeps seeing demand cells on the head unit); phone locked while projecting → AppState gate freezes the car heatmap stale with no treatment. Share one hook instance / pass car context.
- **Theater test coverage** (tester, confirmed by coverage run): `monitoring-map.tsx` and `toolbar.tsx` (all of AD-01's logic) are stubbed in tests — 0%; `SurgeHistoryChart`/`HeatmapConfigTab` never mounted by any test (the "covers heatmap config tab" commit-message claim is false); the AD-03 heading test asserts a static `<h2>`. Zero driver-app tests for any new hook/component. Priority plan: extract pure functions (color tiers, gap/bar math, driver-ID parser, forecast transform) + unit-test boundaries; then component tests that click the toggle/expand the card; then output-assertion tests (peak/avg values, exact parsed allowlist payload).
- **AD-02 wrong-area data on error + uncancelled fetch races**: expand B after A and B's fetch fails → A's chart under B's name; slow 48 h response can overwrite a newer 6 h view. Standard `cancelled` effect guard; clear data + distinct error state.
- **Tier thresholds/colors duplicated in 4+ frontend places** with no shared source of truth, including **three different purples** for "oversupply" within this one diff (`#8b5cf6` fill vs `#a855f7` legend vs `#9333ea` text) — and none match the repo's `--chart-*` brand tokens. One shared `RATIO_BAND_COLORS` constant.
- **0/0 area renders a 100 %-green "healthy" bar** while the same card shows purple "Oversupply" — contradictory; dead areas look fully matched. Explicit "No activity" state.
- **No rate limit on the new driver heatmap endpoint** (every sibling endpoint has one; DB cost is cache-bounded, so defense-in-depth) — add per-user limit before widening the v2 rollout.
- **UX mediums**: no unsaved-changes guard on the one tab with platform-wide blast radius; "applies to all areas" disclosure buried (put "(All Areas)" on the tab label; better placement comes with per-area config); heatmap page's historical map vs live section time-bases only half-labeled; no loading state on the monitoring demand toggle; AD-03 cards have no legend; forecast honors the area filter while the cards above are global, unlabeled; first paint shows the misleading empty state before the first fetch (`demandLoading` init `false`).
- **Docs**: P0 change-log's rollback cites a nonexistent `heatmap_enabled` key (the global kill switch below makes the docs true); admin-ops log corrections per B8.

## LOW

`layer` switch triggers a needless refetch (read from ref); v2 UI vanishes when `cells: []` (branch on `Array.isArray`, not length — quiet nights lose the layer selector/forecast); in-place `sort` mutates hook state (`[...cells].sort`); no `Number.isFinite` filter before native Polygon (defense vs a corrupted cache row = native Android crash); index-based polygon keys churn native views; surge tint vs chip can disagree ~2 min; `spinr:heatmap:` missing from `KNOWN_KEY_PREFIXES` (ops dashboard buckets it as `__other__`); no jitter on the admin demand poll; 🔥/🚗 emoji announced by screen readers (wrap `aria-hidden`); AD-02 select needs an `aria-label`; chart needs a summary `aria-label`; 24 h x-axis labels don't disambiguate the date; allowlist accepts space-joined junk tokens (split on `/[\s,]+/`, flag non-UUIDs); settings-load failure on the driver endpoint is a bare `except: pass` (log it); forecast DB failure is warn-and-continue (record as deliberate or raise to error).

---

## ALREADY ON MAIN (not this PR — file separate tickets)

1. **Corporate payment toggle can force itself back ON over a rider's explicit "pay personally" choice** (`payment-confirm.tsx:121-141`, `ride-options.tsx:423-440`) — a lint-driven dep widening lets a late work-profile fetch flip the toggle after a personal card was chosen → company billed for a personal trip. Money-adjacent; no test pins it. Guard with an explicit-choice ref.
2. **Migration 301 builds a unique index on `drivers` without CONCURRENTLY** — would block go-online/dispatch writes during the build. Already merged; needs a corrective follow-up migration, and per ACTION_ITEMS C22 verify whether 301 actually reached production before assuming it's moot.
3. **Float-arithmetic gate is not actually enforcing**: the Semgrep money rule is advisory-only in CI, its allowlist misses `payment_service.py` and driver-earnings files, and two listed paths don't exist; the pre-commit float check never blocks. No float regression exists today — the advertised net just isn't deployed. Fix paths + make the money rule blocking.
4. **Dead `DispatchService.assign_driver_to_ride` writes `driver_assigned` without an Insurance Period 2 transition** — zero call sites today; delete or fix before any revival (regulatory misclassification if revived as-is).
5. **`$eq` missing from the `$or` builder branch** (`repositories/_base.py`) — safe (raises, never drops), add for completeness.
6. **Housekeeping**: `.easignore` Play-key fix (`c5d32b3`) has no change-log entry (release-security fix — deserves one); `verifyEmailScreen` cold-run timeout flake; `privacySettingsToggles` worker-teardown leak; typed WS lifecycle events are un-versioned (1–2 s transient status regression window, self-corrected — backend should stamp `version`).
7. **Process**: pre-deploy audits must compute merge-base against freshly-fetched `origin/main` (this review initially ran against a 130-commit-stale base).

---

## VERIFIED SAFE — the "will this break the live app?" answer

- **Dispatch:** zero files touched by this PR; acceptance race guards, offer-timeout release, `is_available ⇒ is_online`, WS emissions all verified intact; no heatmap coupling (grep-proven); **361 tests pass**.
- **Money:** Stripe webhooks/idempotency zero diff; corporate priority untouched; 0 %-commission intact; legacy-rides exclusion read-path-only with the A26 fix present; **1,160 tests pass**.
- **Realtime:** WS core zero diff (contract spot-verified as implemented); the one new background loop is replay-safe, watchdog-registered, read-only; middleware change additive and WS-inert; **683 tests pass**; monitoring-socket refactor *fixes* a stale-closure bug.
- **Rider-app:** zero changes in this PR; `tsc` clean; **468/468 tests pass** (one cold-run flake, green in isolation); version skew fenced by the `runtimeVersion` bump.
- **Driver-app:** home screen crash-safe (ErrorBoundary; go-online/offers/rides provably independent of heatmap state; nothing heatmap-related renders or fetches during an offer or trip); version skew safe both directions (v1 shape unchanged; v2 additive; old-backend → clean degrade); render caps (200 phone / 80 car); `tsc` clean; **164 existing tests pass**; `shared/theme` change additive with all consumers migrated.
- **Backend heatmap:** read-only (no ride/driver writes), no dispatch imports; DB failures → 503 (no empty-200); v1 k-floor enforced; cache keyed per area+version so allowlisted v2 payloads can't leak to v1 drivers; no raw GPS in logs/metrics; metric names follow convention; **76 heatmap/forecast/airport tests pass**; migration 307's index shape exactly matches its query.
- **Security:** no authorization bypass (all four admin endpoints correctly module-gated regardless of calling page); credentials masked server-side on every settings read; settings PUTs audit-logged; JWT trust model and OTP rules untouched; airport endpoint's public projection leaks nothing beyond its 5-field allowlist.

**Test-run totals across reviewers:** backend 361+1,160+683+76 passing in the cited slices; rider-app 468/468; driver-app 164/164; admin-dashboard 162/163 (the 1 failure is B6, this PR's own regression) + `tsc` red at HEAD (B6).

---

## REMEDIATION PLAN (order matters)

1. **B6** first (restores green suite + coverage reporting), then **B8** (correct the log).
2. **B3** migration rename + merge `origin/main` into the branch.
3. **B1+B2+H1 together** (settings write path + baseline k-floor + clamps both ends + persistence test) — the security reviewer's explicit coupling requirement.
4. **B4** surge-history manual writes; **B5** forecast field fix; **B7** a11y mechanical fixes.
5. **H-tier**: gate/memoize admin polling (H5), error/staleness states (H9/H10), labeling fixes (H3/H4/H14), RBAC gating (H8), driver-app H6/H7/H12, Redis guard (H2), truncation honesty (H11), contrast/aria (H13).
6. **Kill switch + per-service-area config (task #32)** — builds directly on B1's fixed write path: global `driver_heatmap_enabled` master switch (checked before per-area `show_demand_heatmap`, before cache; follows the shipped e5 kill-switch pattern; makes the P0 runbook's rollback instruction true), plus `heatmap_config` JSONB on `service_areas` with per-area overrides for the hardcoded windows (live 7 d, live-now 10 min, baseline 28 d, scheduled 2 h, forecast 6 h/28 d) and the global knobs, resolved area → global → default with server-side clamps; admin tab becomes genuinely per-area (resolving H8/H12/M-placement findings properly).
7. Mediums/lows batched by file; already-on-main items filed as separate tickets.
