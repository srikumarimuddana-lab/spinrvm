# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | Claude Code (interactive session), user request: address the legacy-migration plan's Section 6 display gaps |
| Surface(s) | backend (rider ride-history endpoint, driver ride-history endpoint) |
| Domain (Sentry tag) | rides |
| PR / commit link | commit following this log |
| Related issue or gap ID | `docs/migration/2026-08-27-legacy-data-full-migration-approach.md` §6, first bullet |

## 1. Issue / gap identified

`show_legacy_badge` (the flag rider/driver apps read to show an "Imported" badge + no-GPS
disclaimer on a legacy-imported ride) was only computed by `GET /rides/{ride_id}`
(`backend/routes/rides/queries.py`) and its driver-side equivalent. Neither app's ride *list*
screen computed it — a rider or driver scrolling their trip history saw no visual distinction
until tapping into a specific ride, even with `legacy_ride_badge_enabled` on.

## 2. Root cause

The field was added only to the single-ride detail path when the Imported-badge feature shipped
(2026-08-19/2026-08-25 sessions) — the list endpoints (`GET /rides/history` for riders,
`GET /drivers/rides/history` for drivers) were never revisited. Not a regression, an incomplete
rollout, already correctly flagged in the migration plan doc as a known gap rather than
discovered fresh here.

## 3. Fix / remediation

- `backend/routes/rides/queries.py`'s `get_ride_history`: now fetches `legacy_ride_badge_enabled`
  from the same `_settings` object it already fetches once per page for `fare_lock_enabled` (no
  extra DB round-trip), and sets `r["show_legacy_badge"] = bool(flag_enabled and
  r.get("legacy_import_metadata"))` per row in the loop that already runs for every ride —
  identical gating condition to the detail endpoint.
- `backend/routes/drivers/ride_reads.py`'s `get_ride_history`: same computation, added as a new
  per-page settings fetch (this function didn't already fetch settings for anything else) using
  the same dual-import pattern (`try: from ...settings_loader import get_app_settings / except
  ImportError: from settings_loader import get_app_settings`) already used elsewhere in this
  file's sibling module (`routes/drivers/location.py`).
- Both changes are purely additive — no existing field, filter, or ordering touched.

## 4. Risk & impact on existing functionality

- **Blast radius:** both functions are single-purpose route handlers with one caller each (the
  route itself). Grepped both files for other internal callers of `get_ride_history` — none.
  `_settings` (rider-side) is reused, not newly introduced — the existing `fare_lock_enabled`
  read is untouched, only a second key is read off the same already-fetched dict.
- **What's still explicitly NOT done** (scoped out of this fix, stated so it isn't mistaken for
  complete): the frontend list-screen rows (rider-app's activity screen, driver-app's ride-history
  screen) do not yet render the badge using this new field — this fix makes the data available;
  wiring the list-row UI is the next, separate step. Shipping the field alone is safe and
  reversible (an unread response field has zero behavioral effect on any client today) and matches
  this codebase's established dark-ship-then-wire pattern for `legacy_ride_badge_enabled` itself.
- **No change for a ride with no `legacy_import_metadata`** — `show_legacy_badge` evaluates to
  `False` for every real, non-legacy ride, same as it already does on the detail endpoint.

## 5. User-experience effect

None yet, by design (§4) — the API now returns a field neither app's list screen reads. Once the
frontend wiring lands, riders/drivers with the flag on will see the same "Imported" distinction on
their trip list that they already see on a ride's detail screen.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/queries.py` | `get_ride_history` now sets `show_legacy_badge` per row, reusing the page's already-fetched settings. | Close the rider-side list/detail parity gap. |
| `backend/routes/drivers/ride_reads.py` | `get_ride_history` now fetches settings once per page and sets `show_legacy_badge` per row. | Close the driver-side list/detail parity gap. |
| `backend/tests/test_ride_history_pagination.py` | 2 new tests: flag+metadata → `True`; flag off → `False`; also covers empty-dict/missing-key metadata as falsy. | Regression coverage for the new field. |
| `backend/tests/test_driver_ride_flow_coverage.py` | 3 new tests: flag+metadata → `True`; flag off → `False`; metadata absent → `False`. | Same, driver side. |

## 7. Before / after

```python
# Before -- rider-side get_ride_history, no show_legacy_badge at all
r["actual_duration_minutes"] = _actual_duration_minutes(r)
```

```python
# After
r["actual_duration_minutes"] = _actual_duration_minutes(r)
r["show_legacy_badge"] = bool(_legacy_badge_enabled and r.get("legacy_import_metadata"))
```

## 8. Rollback plan

`git revert` is complete and sufficient. No data, schema, or migration component — this is a pure
additive response-field computation with no client reading it yet.

## 9. Verification performed

- [x] New tests: 5 added (2 rider-side, 3 driver-side), all pass.
- [x] Full affected test files: `test_ride_history_pagination.py` (13/13),
      `test_driver_ride_flow_coverage.py` (102/102) — no new failures.
- [x] Broader collateral sweep: `pytest -k "rides or driver_ride or ride_history or ride_reads"`
      — 966 passed, 1 pre-existing skip, no new failures.
- [x] `ruff check` clean on all 4 touched files. `ruff format --check` clean on all 4. One
      pre-existing, unrelated `F841` finding in `test_driver_ride_flow_coverage.py` (line 1376,
      `git blame`-confirmed to a 2026-08-22 dependabot commit, outside this diff's hunks) — left
      alone per surgical-changes convention.
- [x] Blast-radius check performed (§4) — single caller each, no other internal consumer.

## What was NOT verified

- Frontend list-screen wiring is explicitly not part of this fix (§4) — not tested because it
  doesn't exist yet.
- Not run against real Supabase dev — verified via the existing mocked-unit-test harness only,
  consistent with this module's existing test conventions.
