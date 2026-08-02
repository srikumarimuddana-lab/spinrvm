# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + Admin Portal Review — Admin #3 |

## 1. Issue / gap identified

The admin dashboard's Demand Forecast page rendered a colored badge
reading "high confidence" / "medium confidence" / "low confidence".
`backend/utils/demand_forecast.py`'s own module docstring says it is a
"heuristic-based prediction engine" — a plain historical average by
day-of-week/hour, with a static fallback pattern when no history exists.
There is no trained model, no error/uncertainty estimate, nothing that
statistically justifies the word "confidence." An admin reading "high
confidence" would reasonably assume a rigor the underlying calculation
never had.

## 2. Root cause

The field was named `confidence` with values driven by a data-
availability check (`has_data`) and a lookback-length threshold
(`lookback_days >= 14`), not by any actual uncertainty computation —
i.e. the field always measured "how much historical data do we have,"
mislabeled as if it measured "how sure are we this number is right."

## 3. Fix / remediation

- Backend (`utils/demand_forecast.py`): renamed the field from
  `confidence` to `data_basis` in both `forecast_demand` (per-hour) and
  `get_forecast_summary` (top-level), and renamed its values to describe
  data provenance instead of implying statistical confidence:
  `"high"` → `"historical_average"`, `"medium"` → `"limited_history"`,
  `"low"` → `"default_pattern"`. No change to the underlying computation
  — same three states, same trigger conditions, only the name and values
  changed to accurately describe what they measure.
- Frontend (`dashboard/forecast/page.tsx`):
  renamed `CONFIDENCE_COLORS` to `DATA_BASIS_COLORS`, added
  `DATA_BASIS_LABELS` mapping each value to an honest, plain-language
  description ("Based on historical data" / "Based on limited history" /
  "Estimated (no history yet)"), and removed the word "confidence" from
  the badge entirely.
- Confirmed via grep that `demand_forecast.py`'s `confidence`
  field had exactly one consumer in the whole codebase (this forecast
  page) — no other backend or frontend code reads it, so this is a safe,
  atomic rename with no other call sites to update. (Separately, other
  modules — `utils/route_validation.py`, `utils/trip_distance.py` — have
  their own, genuinely statistical `confidence` fields for OSRM route-
  matching and GPS-trace quality; those are untouched, correctly named,
  and out of scope for this finding, which named the demand-forecast
  badge specifically.)

## 4. Risk & impact on existing functionality

- **Blast radius: 1 backend module, 1 frontend page.** Grepped every
  reference to `demand_forecast.py`'s `confidence` field across both
  backend and frontend — only the one consumer found and updated in the
  same commit.
- Both `getDemandForecast`/`getDemandForecastSummary` TS client functions
  are typed `any` (no interface to update), and the page component's
  `summary` state is also `any` — so this is a pure rename with no
  TypeScript type surface to break.
- Existing backend tests for the forecast routes
  (`test_forecast_endpoint_delegates_to_util`,
  `test_forecast_summary_endpoint_delegates_to_util` in
  `test_admin_analytics_coverage.py`) mock the entire util function and
  never assert on the `confidence`/`data_basis` field — confirmed
  unaffected, ran and passing.
- Added a new dedicated test file (`test_demand_forecast.py`, 5 tests)
  directly exercising `forecast_demand`/`get_forecast_summary`'s three
  `data_basis` states plus a regression guard asserting the old
  `"confidence"` key is absent from every response shape.

## 5. User-experience effect

**Internal admin-facing only, cosmetic.** No change to what data is
shown or how the forecast is computed — only the wording and color-badge
labels changed from a tri-level "confidence" claim to an honest
description of what the number is based on. An admin who previously saw
"low confidence" (implying the forecast might be wrong) now sees
"Estimated (no history yet)" (accurately describing that there's no
historical data for this area/hour yet, so the static default pattern is
being used) — same situation, more accurate framing.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/demand_forecast.py` | `confidence` field renamed to `data_basis`; values renamed to describe data provenance (`historical_average`/`limited_history`/`default_pattern`) | Stop implying statistical rigor a historical-average lookup doesn't have |
| `backend/tests/test_demand_forecast.py` (new) | 5 tests covering all three `data_basis` states + absence of the old `confidence` key | Cover the rename with a regression guard |
| `admin-dashboard/src/app/dashboard/forecast/page.tsx` | `CONFIDENCE_COLORS` → `DATA_BASIS_COLORS` + new `DATA_BASIS_LABELS`; badge text no longer says "confidence" | Render the relabeled backend field honestly |

## 7. Before / after

```python
# Before
if has_data:
    predicted = historical.get(day, {}).get(hour, 0)
    confidence = "high" if lookback_days >= 14 else "medium"
else:
    predicted = DEFAULT_HOURLY_PATTERN.get(hour, 0.3) * DAY_MULTIPLIERS.get(day, 1.0) * 10
    confidence = "low"
...
"confidence": confidence,
```

```python
# After
if has_data:
    predicted = historical.get(day, {}).get(hour, 0)
    data_basis = "historical_average" if lookback_days >= 14 else "limited_history"
else:
    predicted = DEFAULT_HOURLY_PATTERN.get(hour, 0.3) * DAY_MULTIPLIERS.get(day, 1.0) * 10
    data_basis = "default_pattern"
...
"data_basis": data_basis,
```

```tsx
// Before
<Badge className={CONFIDENCE_COLORS[summary.confidence] || "bg-gray-100"}>
  {summary.confidence} confidence
</Badge>
```

```tsx
// After
<Badge className={DATA_BASIS_COLORS[summary.data_basis] || "bg-gray-100"}>
  {DATA_BASIS_LABELS[summary.data_basis] || summary.data_basis}
</Badge>
```

## 8. Rollback plan

Plain code + copy change, no migration, no data written. `git revert`
fully restores the prior field name/labels on both sides atomically
(single commit touches both). No feature flag — this is a wording/naming
correction with no behavioral surface; there's no meaningful dark-ship
version of "stop mislabeling a heuristic as a confidence score."

## 9. Verification performed

- [x] Backend automated tests: `test_demand_forecast.py` (5 new),
      `test_admin_analytics_coverage.py` (23, unaffected) — 28 passed,
      run via the session's `/tmp/spinr_venv` venv from repo root.
- [x] `ruff check` on both touched backend files — clean.
- [x] Frontend: `eslint` on the touched page — 2 pre-existing warnings on
      unrelated lines (confirmed via diff — neither warning's line
      number falls inside my changes); `tsc --noEmit` — 27 pre-existing,
      unrelated errors only (confirmed via `grep` for the touched file
      path — zero matches).
- [ ] Did not run a real production build (`npm run build`) of
      `admin-dashboard` — only `tsc --noEmit` + `eslint`, consistent with
      this review's established lighter-weight verification for
      admin-dashboard changes; no staging access, no live browser
      click-through/screenshot.
- [x] Blast-radius grep performed (see §4): every reference to the old
      `confidence` field name across backend and frontend, confirming a
      single consumer.
- [x] Dry-run scenario: a service area with zero historical completed
      rides. Before this fix: the badge reads "low confidence" (implying
      the number might be statistically unreliable). After this fix: the
      badge reads "Estimated (no history yet)" (accurately stating why —
      no historical data exists for this area, so the static default
      demand pattern is in use).

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — single consumer confirmed by
      grep across both surfaces
- [x] No silent behavior change to a working flow — the underlying
      computation, thresholds, and three-state structure are byte-for-
      byte unchanged; only the field name and display copy changed

## What was NOT verified

Not tested against a live/staging Supabase — the new unit tests mock
`db.get_rows` directly. Did not visually screenshot the relabeled badge
in a running dev server (no browser access in this environment; no
automated visual-regression tooling exists in this repo per CLAUDE.md's
standing gap — reasoned about the rendering via source reading, not
screenshotted). Did not extend this relabeling exercise to any other
admin-dashboard page — the review finding named the demand-forecast
badge specifically; a broader "does the admin portal have other
misleadingly-labeled metrics" sweep is a reasonable follow-up, not
performed here.
