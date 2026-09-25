# Change Impact & Risk Log — destination mode behind a settings switch (default off)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (owner-approved change) |
| Surface(s) | backend / driver-app / shared (hook consumed by rider-app) |
| Domain (Sentry tag) | dispatch / drivers |
| PR / commit link | branch `claude/destination-mode-flag` (local commits, not pushed) |
| Related issue or gap ID | ACTION_ITEMS.md C136 (PRs #5769 / #5774, migration 465) |

## 1. Issue / gap identified

Destination ("heading home") mode is a **hard** dispatch filter. On 2026-09-24 it caused a zero-offer incident: the C136 incident driver received no offers all day. Only one driver has ever used the feature in production. Owner decision (2026-09-25): hide the feature behind a settings switch that defaults to off. Fix its known defects so it can be turned back on later.

## 2. Root cause

Incident RCA (C136):
- `drivers.destination_mode` was stuck `true` with a NULL expiry. This was a pre-C136 row: before migration 465 the mode never expired.
- `dispatch_service._ride_brings_driver_closer_to_destination` drops every ride whose dropoff is not at least 5% closer to the driver's destination. On 2026-09-24 it excluded every nearby ride for that driver. **0 of the 8 test rides passed the 5% rule.**
- The app showed nothing on screen, and the exclusion was not logged.
- C136 (PRs #5769/#5774) added the 2h TTL, the banner and the exclusion log/metric. The feature still had three problems:
  - The copy told drivers we would "prioritize" rides heading their way. It is really a filter.
  - The address geocoder silently took the first unbiased autocomplete result. Its `catch {}` turned every failure into "Address not found".
  - The destination screen decided on/off from the raw `destination_mode` flag instead of the server's computed `active`.
- Separately, `maps_proxy.places_autocomplete` logged the raw typed address, the bias lat/lng and the top result's description at INFO. That is PII under CLAUDE.md's PIPEDA rules.

Data reset (approved, done 2026-09-25 before this change, outside this diff): the incident driver's stale destination was cleared. **0 drivers have a destination set now.**

## 3. Fix / remediation

- **Migration 482** adds `settings.destination_mode_enabled BOOLEAN NOT NULL DEFAULT FALSE`. It is additive and has a `-- Rollback:` header.
- `AppSettings` defaults the field to False, and `SettingsUpdateRequest` (the admin write allow-list) now includes it.
- **Dispatch:** `filter_and_rank_drivers` gains the keyword-only `destination_mode_enabled` (default False).
  - While it is off, the destination filter is skipped completely. It does not count as a `destination` exclusion either.
  - `matching.py` passes `app_settings.get("destination_mode_enabled") is True` to both call sites (primary and cascade).
  - The C136 fail-open logic is unchanged when the switch is on.
- **Driver endpoints** (`routes/drivers/profile.py`):
  - While the switch is off, POST returns **409** "Destination mode is not available.".
  - GET adds `enabled` and reports `active=false` while off.
  - DELETE is never gated.
  - A settings read failure returns 503; it is not silently treated as off.
- **Audit:** set and clear each write an `audit_logs` row through `log_user_action`:
  - `driver_destination_mode_set` records the driver id and expiry.
  - `driver_destination_mode_cleared` records the driver id and prior-state booleans.
  - Neither row contains the address or coordinates.
- **maps_proxy:** the autocomplete logs now record only input length, whether a bias was sent, the result count and whether the top result has a place_id.
- **driver-app:**
  - `isDestinationActive` returns false when `enabled === false`, so the banner hides.
  - The Settings row, the only navigation entry point, renders only when the server says the feature is available.
  - The destination screen:
    - takes on/off from `active` through `isDestinationActive`;
    - shows an "unavailable" notice instead of the form when the feature is off;
    - replaces `geocodeAddress` with the shared `usePlacesAutocomplete` typeahead, biased to the driver's last-known GPS (50 km, the same as the rider app), with a pick list;
    - distinguishes "Address search is temporarily unavailable, try again" (the hook's new `error`) from "No matching address".
  - The copy now reads: "You'll only receive ride requests heading toward your destination. Turns off automatically after 2 hours or when you go offline." This is updated in en/es/fr, along with the clear-confirm text.
- **shared:** `usePlacesAutocomplete` gains the additive fields `error: 'unavailable' | null` and `searched: boolean`.
- **Docs:** `docs/prds/driver-app-edge-cases.md` now says the TTL is a fixed 2h and the feature is behind the switch. ACTION_ITEMS.md C136 has a status line.

## 4. Risk & impact on existing functionality

Blast-radius grep: `destination_mode|destination_expires_at|is_destination_mode_active|filter_and_rank_drivers|usePlacesAutocomplete|/drivers/destination`.

- **Dispatch**
  - Callers of `filter_and_rank_drivers`: only `routes/rides/matching.py`, which has two call sites and both now pass the flag. Its re-exports in `routes/rides/__init__.py` and `_deps.py` are unchanged.
  - Tests that patch `filter_and_rank_drivers` accept `**kw`, so the new keyword is harmless to them.
  - Behaviour change with the switch off (the default): no driver is ever excluded for destination reasons. That is the intended effect. With 0 drivers holding a destination today, the observable dispatch change on deploy is nil.
  - Nothing else changes: ride state machine, insurance periods and money paths are untouched.
- **Readers of the destination columns**
  - `dispatch_candidates.py` (select list) is unchanged.
  - `routes/rides/estimates.py` selects the columns but does not filter on them. It is unchanged.
  - `routes/drivers/status.py` clears destination on go-offline. It is unchanged and still runs.
- **Settings**
  - `get_app_settings()` merges `AppSettings` defaults, so the key is False everywhere, even before migration 482 is applied.
  - `test_settings_column_parity.py` discovers the column from the migration automatically.
  - `test_admin_settings_write_allowlist_drift.py` snapshot is updated.
  - Settings are cached for up to 60 s per replica, so a flip takes up to one minute to propagate.
- **Endpoints**
  - `POST /drivers/destination` newly returns 409 while off. That is a new rejection of previously-valid input, and it is intended and flagged.
  - `GET /drivers/destination` gains `enabled` (additive), and `active` is forced false while off.
  - GET and POST each add one `get_app_settings()` call, which is normally a cache hit.
  - Set and clear each add one `audit_logs` insert. `log_admin_action` catches its own failures, so an audit failure never fails the request.
- **shared hook** (`shared/hooks/usePlacesAutocomplete.ts`)
  - Consumers: `rider-app/app/saved-places.tsx`, `rider-app/app/search-destination.tsx`, and now `driver-app/app/driver/destination-mode.tsx`.
  - The change is additive: two new returned fields plus two extra state setters. The rider-app consumers destructure named fields, so their behaviour is unchanged.
  - The rider-app tests mock the hook, so they give **zero real coverage** of this change for rider-app.
  - The admin-dashboard has its own parallel hook, which is untouched.
- **driver-app**
  - The Settings row is now hidden when availability is unknown (fetch failed). Before, it showed the generic description.
  - `DestinationModeBanner` is unchanged in code. It hides through `isDestinationActive`.
  - `driver-app/app/driver/addresses.tsx` still has its own `geocodeAddress` copy (first result, `catch {}`). That is left as a follow-up; see "What was NOT verified".
- **maps_proxy:** the change is log-only. Any log-based dashboard that parsed `input=` or `top=` from these lines loses them. None were found in the repo.
- **Blast radius:** cross-surface (backend + driver-app + shared).

## 5. User-experience effect

- **Drivers on the new app build, switch off:**
  - No Destination Mode row in Settings and no banner.
  - If the screen is reached anyway, it shows "Destination mode is not available." Clear is still shown if a row is stored.
- **Drivers on old app builds (until an EAS build ships), after the backend deploys:**
  - The Settings row and screen still appear. The status reads "off", because 0 drivers have a destination and old apps show the raw flag.
  - The banner stays hidden, because the old app already uses `isDestinationActive` and the server now returns `active=false`.
  - Trying to set a destination still runs the old geocoder first. The POST then returns 409, and the old `handleSave` `catch` shows the toast "Save Failed — Destination mode is not available."
  - Checked by reading the code: `getApiErrorMessage` extracts `detail` from the 409 body, and state updates only on success. The old app shows the error; it does not crash.
- **Switch on (future):**
  - The typeahead pick list replaces free-text geocoding.
  - Search errors and empty results have distinct messages.
  - The copy is accurate. The screen status follows the server's `active`.
- **Mid-session:** a driver online with the old app sees no change unless they open the screen. No driver has an active destination, so nobody's offers change.
- **Riders:** no visible change. The shared hook's behaviour is unchanged for rider-app.
- **Admin:** no UI change. The switch is writable via `PUT /api/admin/settings` (`destination_mode_enabled`). No toggle was added to the admin-dashboard settings page; that page has a seeded visual baseline.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| backend/migrations/482_settings_destination_mode_enabled.sql | New `settings.destination_mode_enabled` boolean, default false, with a Rollback header | Owner decision: hide destination mode by default |
| backend/schemas.py | `AppSettings.destination_mode_enabled: bool = False` | Default is present even before the migration runs |
| backend/routes/admin/settings.py | `destination_mode_enabled` added to `SettingsUpdateRequest` | Admin can flip it without a deploy |
| backend/services/dispatch_service.py | `filter_and_rank_drivers(..., destination_mode_enabled=False)`; filter skipped when off | Stop the hard filter unless explicitly enabled |
| backend/routes/rides/matching.py | Reads the switch once; passes it to both rank calls | Wire the setting into dispatch |
| backend/routes/drivers/profile.py | POST 409 when off; GET `enabled` and `active` gated; DELETE ungated; audit rows on set/clear; accurate docstring | Hide the feature server-side and make usage measurable |
| backend/routes/maps_proxy.py | Autocomplete logs drop the raw input, bias coords and top description | PIPEDA: no addresses or raw lat/lng in logs |
| backend/tests/services/test_dispatch_service.py | Existing C136 tests pass `destination_mode_enabled=True`; new switch-off tests | Pin both paths |
| backend/tests/test_dispatch_destination_mode_flag.py | New: matching passes the switch to both call sites; missing or non-bool value means off | Wiring test |
| backend/tests/test_drivers_extended.py | Destination tests pin switch on; GET key set includes `enabled` | Flag-on contract unchanged |
| backend/tests/test_destination_mode_flag_endpoints.py | New: switch off gives 409, GET disabled, clear works, 503 on settings failure; audit rows with no address or coords | Endpoint and audit coverage |
| backend/tests/test_admin_settings_write_allowlist_drift.py | Column snapshot includes `destination_mode_enabled` | Drift guard |
| backend/tests/test_maps_proxy.py | caplog test: no typed text, result text or coords in logs | Privacy regression guard |
| shared/hooks/usePlacesAutocomplete.ts | Additive `error` / `searched` return fields | Distinguish "unavailable" from "no results" |
| driver-app/utils/destinationModeState.ts | `enabled` field; `isDestinationActive` false when disabled; `isDestinationModeAvailable` | Single source of truth for banner, row and screen |
| driver-app/app/driver/settings.tsx | Destination row rendered only when available | Hide the entry point |
| driver-app/app/driver/destination-mode.tsx | On/off from `active`; unavailable notice; GPS-biased typeahead pick list; distinct error messages | Fix the defects so the feature can be re-enabled |
| driver-app/i18n/en.json | Accurate explainer and clear-confirm copy; new `destinationMode.*` keys | Copy said "prioritize"; it is a filter |
| driver-app/i18n/es.json | Same as en | Same |
| driver-app/i18n/fr.json | Same as en | Same |
| driver-app/__tests__/app/destinationModeScreen.test.tsx | Rewritten for pick list, `active`, `enabled`, error states | Coverage |
| driver-app/__tests__/app/driverSettingsScreen.test.tsx | Row hidden when disabled or unknown; shown when enabled | Coverage |
| driver-app/__tests__/components/DestinationModeBanner.test.tsx | Hidden when `enabled=false`; helper table | Coverage |
| driver-app/__tests__/hooks/usePlacesAutocompleteError.test.ts | Real hook: error vs empty vs pending; bias params | Coverage for the shared hook change |
| ACTION_ITEMS.md | C136 status line | Tracking |
| docs/prds/driver-app-edge-cases.md | 2h fixed TTL, hard filter, behind the switch | The doc said "configurable, default 8h" |
| docs/change-log/2026-09-25-destination-mode-flag.md | This log | Required gate |

## 7. Before / after

```python
# Before (dispatch_service.filter_and_rank_drivers)
if not _ride_brings_driver_closer_to_destination(d, ride):
    exclusion_counts["destination"] += 1
    ...
# After
if destination_mode_enabled and not _ride_brings_driver_closer_to_destination(d, ride):
    ...
# matching.py
_destination_mode_enabled = app_settings.get("destination_mode_enabled") is True
```

```python
# Before (POST /drivers/destination) — always wrote the destination
# After
if not await _destination_mode_enabled():
    raise HTTPException(status_code=409, detail="Destination mode is not available.")
# GET: "active": enabled and is_destination_mode_active(driver), "enabled": enabled
```

```python
# Before (maps_proxy)
logger.info("... input=%r location=%s ...", input, location, ...)
logger.info("... results=%d top=%s", len(predictions), predictions[0]["description"][:60])
# After
logger.info("... input_len=%d radius=%s restricted=%s", len(input), ...)
logger.info("... results=%d top_has_place_id=%s", len(predictions), bool(predictions[0].get("place_id")))
```

```tsx
// Before (destination-mode.tsx): state.destination_mode drove on/off; geocodeAddress() took
// predictions[0] with no bias and `catch {}` → "Address not found".
// After: isDestinationActive(state) drives on/off; usePlacesAutocomplete(addressInput, gpsBias)
// + pick list; error === 'unavailable' → searchUnavailableMsg, searched && [] → noMatchingAddress.
```

## 8. Rollback plan

- **Behavioural (no deploy):**
  - `UPDATE settings SET destination_mode_enabled = true WHERE id = 'app_settings';` restores the pre-change dispatch filter and lets drivers set destinations again. Allow up to 60 s for the settings cache.
  - The same can be done with `PUT /api/admin/settings {"destination_mode_enabled": true}`.
- **Schema:** `ALTER TABLE settings DROP COLUMN IF EXISTS destination_mode_enabled;`. This is safe because code reads the key with a False default. Note that dropping the column is equivalent to leaving the feature off.
- **Live data:** no data is mutated by this change. Audit rows are append-only and harmless. No money, ride-state or insurance-period rows are touched.
- **Code:** reverting the commits restores the old behaviour. The driver-app UI changes only reach users through an EAS build, so a bad app build is rolled back by not promoting it. The backend 409 and the flag still protect dispatch.

## 9. Verification performed

- Backend, the required selection: `cd backend && python -m pytest -q -p no:cacheprovider --no-cov --ignore=tests/rls -k "destination or dispatch or matching or maps or settings or profile"` — **1956 passed, 26 skipped, 0 failed** (15328 deselected, 337 s).
- Backend, targeted suites run during development, all green:
  - `tests/services/test_dispatch_service.py`
  - `test_dispatch_destination_mode_flag.py`
  - `test_dispatch_expanded_radius.py`
  - `test_rides_matching_coverage.py`
  - `test_destination_mode_flag_endpoints.py`
  - `test_drivers_extended.py`
  - `test_drivers_shared_status_profile_coverage.py`
  - `test_go_offline_clears_destination.py`
  - `test_maps_proxy*.py`
  - `test_admin_settings_write_allowlist_drift.py`
  - `test_settings_column_parity.py`
- Dry run of the dispatch path against `mock_supabase_client` fixtures, in both directions:
  - A driver at (52.0, -106.0) with an active destination to the north and a southbound ride is **included** when the switch is off (default or explicit False).
  - The same driver is **excluded** when the switch is on, which is the existing C136 test.
  - The matching wiring test drives `_match_driver_to_ride_attempt` through both primary and cascade pools with `{}`, `False`, `None`, `"true"` (all off) and `True` (on).
- `ruff check` and `ruff format --check` pass on every changed `.py` file.
- driver-app:
  - `yarn install --frozen-lockfile` was run.
  - `npx tsc --noEmit` is clean.
  - `npx eslint` on the changed files: 0 errors. The warnings are pre-existing style warnings.
  - jest for touched suites: `destinationModeScreen` (19), `driverSettingsScreen` plus `DestinationModeBanner` (51 together), `usePlacesAutocompleteError` (5).
  - Full driver-app jest: 167 suites, 1975 tests: 1974 passed, 1 failed, 4 suites failed. All 4 failing suites are unrelated to this diff: `__tests__/store/authStore.initialize.test.ts`, `__tests__/store/authStore.refreshRace.test.ts` and `__tests__/auth/refreshProposal.test.ts` are auth refresh-proposal tests from recent `main` work, and `utils/__tests__/alwaysLocationGate.test.ts` fails with an `expo-location` import error at suite load. None of them imports a file changed here. They were not re-run on a clean `origin/main` checkout, so "pre-existing" is inferred, not proven. All suites touching destination mode, settings or the places hook pass.
- `BASE_SHA=origin/main HEAD_SHA=HEAD python scripts/check_change_impact.py`: see the final report.
- Old-app behaviour on 409 was verified by reading `destination-mode.tsx` on `origin/main` and `getApiErrorMessage` in `shared/api/client.ts`. It was not run on a device.

## What was NOT verified

- **No production build was run.** Neither `eas build` nor `expo export` was run for driver-app. Only `tsc --noEmit` and jest were. **The driver-app UI hiding reaches drivers only after an EAS build** (a commit containing `[build]`). Until then, old apps rely on the server-side 409 and the `active=false` behaviour described above.
- **driver-app has no visual regression tooling.** The new pick list, the unavailable notice and the hidden Settings row were reasoned about, not screenshotted.
- Not run against live Supabase. Migration 482 was not applied anywhere, and the settings flip and audit-row inserts were exercised only against mocks.
- rider-app: `tsc` and jest were **not** run. The shared-hook change is additive, but rider-app has its own `node_modules`, which was not installed here. Its tests mock the hook anyway.
- `driver-app/app/driver/addresses.tsx` still uses the first-result, `catch {}` geocoder. Converting that modal form to the typeahead is not a small change and is left as a **follow-up**.
- No admin-dashboard toggle was added for the switch, only API and SQL. Adding one would touch the seeded `dashboard-settings` visual baseline, so it is a follow-up needing a human baseline re-capture.
- The Places API behaviour when biased (strictbounds within 50 km) is unchanged backend logic and was not re-tested against Google.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (settings flip, no deploy)
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
