# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | (opened alongside this file) |
| Related issue or gap ID | #5357 |

## 1. Issue / gap identified

The GPS-plausibility chain (`utils/breadcrumbs.py`, both `persist_trip_location_batch` and
`persist_ride_breadcrumbs`) seeds its "previous point" boundary check from `driver_last_known`'s
`updated_at` — a generic row-modified timestamp any write to the `drivers` row bumps, not one
scoped to when `lat`/`lng` actually changed. A driver who goes offline (stale lat/lng, `updated_at`
bumped) and later goes online again without an immediate fresh GPS fix (lat/lng still stale,
`updated_at` bumped again) produces a seed pair that looks like "this exact stale position, but
seconds ago" — understating `elapsed_seconds` enough to falsely reject the next trip's first real
breadcrumb(s) as a teleport, right at trip start (Period 2/3 SGI audit-trail territory).

## 2. Root cause

`backend/routes/drivers/status.py`'s go-online write path writes `lat`/`lng` (when the client
supplies a fresh fix) and `updated_at` in the same `_base` dict, but never a location-specific
timestamp. A column that IS scoped to lat/lng — `drivers.location_captured_at` (timestamptz) —
already exists (migration 445, added for an unrelated feature: the `update_live_driver_marker`
monotonic-ordering RPC) and is already correctly written by every other lat/lng write path
(`routes/drivers/location.py`'s live-ping/batch marker writes, via `_write_marker_if_due`). Only
`status.py`'s go-online block was missing it — a verified-complete grep audit (see §4) found no
other `drivers.lat`/`lng` write path outside `location.py`'s two marker-write sites and this one
gap. The chain-seeding code in `breadcrumbs.py` also still read `updated_at` instead of this
already-existing column.

No new migration is needed — the fix reuses migration 445's column with no schema change.

## 3. Fix / remediation

1. `routes/drivers/status.py`: the go-online block that conditionally sets `_base["lat"]`/`["lng"]`
   now also sets `_base["location_captured_at"]` in the same conditional (never touched on
   go-offline or a fix-less go-online).
2. `routes/drivers/location.py`: the `driver_last_known` dict built for `persist_trip_location_batch`
   now passes `location_captured_at` instead of `updated_at`. The other call site
   (`persist_ride_breadcrumbs(driver_id, points, driver_last_known=driver_row)`) already passes the
   full, unrestricted-columns driver row through, so it needed no change once the read side moved.
3. `utils/breadcrumbs.py`: both chain-seeding blocks now read `driver_last_known.get("location_captured_at")`
   instead of `.get("updated_at")` — a clean swap, not a fallback. A NULL/missing value means the
   chain starts cold (no seed), matching the issue's own documented safe degrade and the pre-fix v1
   behavior.
4. `routes/drivers/status.py`'s existing PGRST204 "missing intent-timestamp column" retry fallback
   (adversarial review finding, not in the original issue): the retry payload now explicitly drops
   `location_captured_at` too, since that column is now baked into `_base` itself — without this,
   an environment where `location_captured_at` was the actual missing column would have the retry
   fail identically and raise unhandled (500) instead of degrading gracefully like the fallback's own
   comment implies.

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface (backend), isolated within the driver-location/insurance-chain path.**
  Grepped every `drivers`-table lat/lng write (`update_one("drivers", ...)` across `routes/`,
  `services/`) and every `driver_last_known=` construction site — confirmed exactly the 3 files
  changed here are the complete set; no admin driver-location-correction endpoint, import script, or
  WebSocket handler writes `drivers.lat`/`lng` directly. `routes/drivers/profile.py`'s registration
  path always seeds `lat=0, lng=0` (unaffected — not a real-coordinate write).
- **Who else reads `location_captured_at`**: only `update_live_driver_marker` (Postgres RPC, migration
  445) and now this fix's two `breadcrumbs.py` seeding blocks. No other reader found.
- **Cold-start question** (explicitly checked via adversarial review): could a driver row ever have
  real, non-placeholder `lat`/`lng` with a NULL `location_captured_at`, losing seed coverage that the
  old `updated_at`-based seed provided? No — every non-zero-coordinate write path already sets
  `location_captured_at` (post this fix); a NULL only occurs pre-migration-445 legacy rows or a
  driver who has never had a location write, both of which degrade safely to "chain starts cold",
  not a loss of real detection coverage.
- **Fraud/spoofing implication** (explicitly checked): `location_captured_at` is exclusively
  server-stamped (`_now_iso`/server `captured_at`), never client-supplied. Toggling offline/online to
  force a cold start doesn't help a driver spoof a jump past the check — it only resets the seed to
  require a fresh first point, which becomes the new seed for the very next check.

## 5. User-experience effect

Backend-only. No rider/driver/admin-facing copy, endpoint contract, or screen changes. The
observable effect (indirect, not mid-session-visible) is fewer false-positive "teleport" rejections
of legitimate breadcrumbs right after a driver starts a trip following a go-online with no immediate
fresh fix — i.e. more complete Period 2/3 location history for the same insurance-audit trail that
was already being written, not a new audit surface.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/status.py` | Go-online block now also stamps `_base["location_captured_at"]`; PGRST204 retry payload drops it | Close the one write-path gap; keep the existing fallback safe now that this column lives in `_base` |
| `backend/routes/drivers/location.py` | `driver_last_known` dict for `persist_trip_location_batch` now carries `location_captured_at` instead of `updated_at` | Feed the correct seed timestamp into the chain |
| `backend/utils/breadcrumbs.py` | Both chain-seeding blocks read `location_captured_at` instead of `updated_at` | Root-cause fix — the actual bug |
| `backend/tests/test_breadcrumb_persistence.py` | Updated 2 existing tests' `driver_last_known` fixtures to the new key; added `test_driver_last_known_with_no_location_captured_at_starts_cold` | Keep tests honest to the new contract; regression-guard the fix |
| `backend/tests/test_location_batch.py` | Updated 1 assertion's expected dict key | Keep test honest to the new contract |
| `backend/tests/test_go_online_location_captured_at.py` (new) | 4 tests: fresh-fix stamps the column, fix-less go-online doesn't, go-offline doesn't, PGRST204 retry drops it too | Cover the write-side of the fix directly |

## 7. Before / after

```python
# Before (utils/breadcrumbs.py, both persist_trip_location_batch and persist_ride_breadcrumbs)
_last_ts = parse_iso_utc(driver_last_known.get("updated_at"))
```

```python
# After
_last_ts = parse_iso_utc(driver_last_known.get("location_captured_at"))
```

```python
# Before (routes/drivers/status.py)
if is_online and lat is not None and lng is not None and (lat != 0 or lng != 0):
    _base["lat"] = lat
    _base["lng"] = lng
```

```python
# After
if is_online and lat is not None and lng is not None and (lat != 0 or lng != 0):
    _base["lat"] = lat
    _base["lng"] = lng
    _base["location_captured_at"] = _now_iso
```

## 8. Rollback plan

`git revert` is sufficient and safe here — no migration, no data backfill, no Stripe/wallet/ride-state
mutation. Worst case on revert: the chain-seeding bug returns (false-positive teleport rejections
resume at the same pre-fix rate); nothing about currently-stored `driver_location_history` rows or
`driver_insurance_periods` rows needs remediation, since a rejected-then-later-accepted point was
always additive to begin with (rejection just means the point wasn't inserted).

## 9. Verification performed

- [x] Automated tests run (unit only — no integration/e2e touched): `pytest tests/test_breadcrumb_persistence.py tests/test_location_batch.py tests/test_go_online_location_captured_at.py tests/test_go_online_availability.py tests/test_driver_status_insurance_periods.py tests/test_driver_status_live_offer_period.py tests/test_go_offline_live_offer_guard.py tests/test_p1_driver_offline.py` — 99/99 pass
- [x] Blast-radius grep performed: every `update_one("drivers", ...)` call site repo-wide, every `driver_last_known=` construction site, `routes/admin/drivers.py`'s location-adjacent endpoints, `routes/drivers/profile.py`'s registration writes
- [x] Reviewed against CLAUDE.md conventions: no ride-state-machine or money path touched; this is the insurance-audit-trail-adjacent GPS-plausibility check, reviewed by dispatching `spinr-fraud-auditor` (owns this exact surface) before commit — verdict: fix correct and complete, one real gap found (the PGRST204 retry item) and fixed per its recommendation
- [ ] Manual repro steps followed in staging — not done; no staging environment reachable from this session
- [ ] Feature-flagged — not applicable; this corrects an existing, already-shipped code path's seed source, not a new user-facing behavior; nothing to dark-ship

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-level cleanup needed)
- [x] Blast radius is stated, not assumed (single-surface backend, exhaustive grep of all `drivers.lat`/`lng` write paths and all `driver_last_known=` call sites, confirmed complete both by direct investigation and independent adversarial review)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in — §5 above states the (indirect, backend-only) effect explicitly

## What was NOT verified

Not tested against a real Postgres/Supabase instance or staging — only against `mock_supabase_client`-mocked unit tests, per this repo's standard unit-tier coverage for this kind of change. The `migration 445`-added `update_live_driver_marker` RPC and its `location_captured_at` writes were reasoned about by reading the migration file and the calling code, not exercised end-to-end here. No production build was run — this is a Python backend change; `tsc`/`npm run build` are not applicable.
