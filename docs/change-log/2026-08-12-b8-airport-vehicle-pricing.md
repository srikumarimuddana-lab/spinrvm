# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Author | Claude (session_01Wk3M9NdQJWqgpATtogSjD8) |
| Surface(s) | backend (fares) — production data only, no code change |
| Domain (Sentry tag) | rides |
| PR / commit link | branch `claude/b8-airport-vehicle-pricing` |
| Related issue or gap ID | ACTION_ITEMS.md B8 (follow-up items discovered during the Regina/Saskatoon fix, PR #3734) |

## 1. Issue / gap identified

Two service areas — **"Saskatoon Airport"** (created 2026-07-30) and **"Regina Airpot"** (name typo — missing the second "r") — quoted the exact same fare (`DEFAULT_FARE`: base_fare 3.50, per_km 1.50, per_min 0.25, min_fare 8, booking_fee 2) across all 4 vehicle types (Economy/XL/Van/Premium). Same defect class as B8's original Regina/Saskatoon fix (PR #3734): the vehicle-type picker had no price effect. These two areas were discovered *during* that fix but explicitly left unfixed and logged as follow-ups, at the user's direction to keep that PR scoped.

## 2. Root cause

Data-only, not a code bug — same root cause as the original B8 finding: `routes/fares.py::build_fares_for_area`'s per-vehicle-type JSONB lookup is correct (unchanged, re-verified via `backend/tests/test_fares.py`, 6/6 passing). These two rows were seeded with all 4 vehicle types carrying the exact `DEFAULT_FARE` fallback values verbatim — the same pattern the original B8 investigation found for `riyadh`/`riyadh airport` (which were confirmed intentional for that international market) and initially for "Regina Airport" before this session discovered the actual row is named "Regina Airpot" (typo). Unlike `riyadh`, both Saskatoon Airport and Regina Airpot are Canadian (Saskatchewan) markets — the same markets Regina/Saskatoon main already serve — so this is very unlikely to be intentional; it reads as an unseeded/copy-pasted row like the original B8 defect, not a deliberate pricing choice.

Live data was re-checked immediately before writing (lesson carried over from the Regina/Saskatoon fix, where live data had drifted since the original investigation) — confirmed unchanged from the state discovered during PR #3734, no surprises this time.

## 3. Fix / remediation

Two `UPDATE` statements run directly against production Supabase (`soavhtdhefowwvforzwb`, `ca-central-1`), replacing `service_areas.vehicle_pricing` wholesale for both areas (full-array-literal replace, same schema-correct approach established in the Regina/Saskatoon fix — `vehicle_pricing` is a JSON **array** of `{vehicle_type: "...", ...}` objects, not an object keyed by vehicle-type name).

Multiplier scheme extends the original B8 proposal (`XL/Van ≈1.4×, Premium ≈1.8×` on base_fare/per_km, `~1.2×/~1.5×` on per_min/booking_fee) to a field these two areas actually have non-zero — `min_fare` — treated the same as per_min/booking_fee (a floor/flat-amount field, not a per-unit rate), since the original proposal didn't explicitly address it (Regina/Saskatoon main had `min_fare=0`, so the question never came up there).

| Vehicle type | base_fare | per_km | per_min | min_fare | booking_fee |
|---|---|---|---|---|---|
| Economy (unchanged baseline) | 3.50 | 1.50 | 0.25 | 8.00 | 2.00 |
| XL / Van (×1.4 / ×1.2) | 4.90 | 2.10 | 0.30 | 9.60 | 2.40 |
| Premium (×1.8 / ×1.5) | 6.30 | 2.70 | 0.38 | 12.00 | 3.00 |

XL and Van share identical values per the original proposal's "XL/Van ≈1.4×" framing (same multiplier tier, not independently priced). Applied identically to both areas — same target numbers for Saskatoon Airport and Regina Airpot.

No application code changed.

## 4. Risk & impact on existing functionality

**Blast radius, stated explicitly:** isolated to fare *display/quoting* for 2 of what is now the full set of 6 known `service_areas` rows. Same sole consumer as the original B8 fix: `backend/routes/fares.py::build_fares_for_area` (re-grepped, no other reader found for either area).

**Airport-specific consideration**: these areas presumably see lower request volume and more price-sensitive/one-off riders (airport pickup/dropoff) than the main city areas — a wrong price here is lower-frequency-impact than a Regina/Saskatoon-main error, but not zero-impact; airport rides often skew toward higher fares already (booking_fee, min_fare both present and non-zero, unlike the main areas), so a mispriced tier here is more visible per-trip in dollar terms even if it affects fewer trips overall.

**Not a mid-ride change** — same reasoning as PR #3734: this affects pre-booking fare estimates only, not an in-progress ride's locked fare.

**The "Regina Airpot" name typo was deliberately NOT touched** in this fix. Renaming a `service_areas.name` value could affect any other code path, config, or admin-dashboard filter that matches on the exact area name string (not grepped for this pass, since no rename was performed) — left as a separate, still-open, lower-priority item rather than bundling an uninvestigated rename into a pricing fix.

## 5. User-experience effect

Rider-facing. Riders booking pickup/dropoff at Saskatoon Airport or the Regina airport-area service area will now see genuinely different prices across Economy/XL/Van/Premium instead of an identical quote regardless of vehicle choice. Same category of fix as PR #3734 — corrects a defect, not a new pricing policy decision. No claim about pricing vs. Uber at these locations; same explicit deferral as the main-area fix.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| Supabase `service_areas` table (production) — `Saskatoon Airport` row | `vehicle_pricing` replaced: all 4 types `3.50/1.50/0.25/8/2` → Economy unchanged, XL/Van `4.90/2.10/0.30/9.60/2.40`, Premium `6.30/2.70/0.38/12.00/3.00` | Fix identical-fares defect (B8 follow-up) |
| Supabase `service_areas` table (production) — `Regina Airpot` row | Same replacement, same target values | Fix identical-fares defect (B8 follow-up) |
| `ACTION_ITEMS.md` | B8 entry updated: both follow-up items marked closed with final values and this log's link | Tracking |
| `docs/change-log/2026-08-12-b8-airport-vehicle-pricing.md` | New Change Impact Log | Required per `CLAUDE.md` — money-touching, live-tested surface |

No backend/frontend code files changed.

## 7. Before / after

```
-- Before (both areas identical — confirmed via SELECT immediately prior to this change)
Economy/XL/Van/Premium: 3.50/1.50/0.25/8/2  (all 4 types identical)
```

```
-- After (confirmed via each UPDATE's RETURNING clause)
Economy:      3.50/1.50/0.25/8.00/2.00
XL, Van:      4.90/2.10/0.30/9.60/2.40
Premium:      6.30/2.70/0.38/12.00/3.00
```
(base_fare/per_km/per_min/min_fare/booking_fee, same values for both Saskatoon Airport and Regina Airpot)

## 8. Rollback plan

Direct SQL, same as PR #3734's precedent — no migration/flag involved (JSONB config column, read per-request):

```sql
UPDATE service_areas SET vehicle_pricing = '[
  {"vehicle_type": "Economy", "base_fare": 3.50, "per_km": 1.50, "per_min": 0.25, "min_fare": 8.00, "booking_fee": 2.00},
  {"vehicle_type": "XL",      "base_fare": 3.50, "per_km": 1.50, "per_min": 0.25, "min_fare": 8.00, "booking_fee": 2.00},
  {"vehicle_type": "Van",     "base_fare": 3.50, "per_km": 1.50, "per_min": 0.25, "min_fare": 8.00, "booking_fee": 2.00},
  {"vehicle_type": "Premium", "base_fare": 3.50, "per_km": 1.50, "per_min": 0.25, "min_fare": 8.00, "booking_fee": 2.00}
]'::jsonb WHERE name = 'Saskatoon Airport';

-- identical statement WHERE name = 'Regina Airpot';
```

Takes effect immediately on the next `/fares` request — no cache to bust, no app deploy needed.

## 9. Verification performed

- [x] Live data verified **before** writing (confirmed unchanged from the state discovered during PR #3734 — no drift this time).
- [x] Live data verified **after** writing via each `UPDATE`'s own `RETURNING` clause.
- [x] `backend/tests/test_fares.py` re-run — **6 passed**.
- [x] Functional simulation of the exact `vp_by_name` read-path logic (mirroring `routes/fares.py:249-254`) run against the just-written JSON for one of the two areas, confirming all 4 tiers resolve correctly and to distinct values (Economy vs. XL/Van vs. Premium).
- [x] Blast-radius re-grep for `vehicle_pricing` consumers — same single reader as the original B8 fix, no new consumers found.

## 10. What was NOT verified

- Not verified against a live rider-app fare-estimate screen — data + backend-function-simulation level only, same as PR #3734.
- Did not investigate whether the "Regina Airpot" name typo causes any functional (not just cosmetic) bug elsewhere — deliberately out of scope, flagged as a still-open separate item.
- Did not revisit the Uber-positioning question — same deferral as PR #3734, unchanged.
- No visual/screenshot verification — same standing gap as PR #3734 for this class of data-only change.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (§8)
- [x] Blast radius is stated, not assumed (§4)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5)
