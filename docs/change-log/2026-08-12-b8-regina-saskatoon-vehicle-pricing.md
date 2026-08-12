# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Author | Claude (session_01Wk3M9NdQJWqgpATtogSjD8) |
| Surface(s) | backend (fares) — production data only, no code change |
| Domain (Sentry tag) | rides |
| PR / commit link | branch `claude/b8-vehicle-pricing-differentiation` |
| Related issue or gap ID | ACTION_ITEMS.md B8 |

## 1. Issue / gap identified

Regina and Saskatoon's `service_areas.vehicle_pricing` had every vehicle type (Economy, XL, Premium) quoting the same fare — the vehicle-type picker in the rider app had no effect on price in these two areas. Explicitly stopgap-scoped per user direction: apply the drafted 1.4×/1.8× industry-standard tier multipliers now to fix the visible "all tiers cost the same" defect; the separate question of whether Spinr's absolute price level should undercut Uber (raised directly by the user — 0% driver commission gives real room to price below Uber's list price, distinct from chasing Uber's unsustainable promotional discounts) is explicitly deferred to a follow-up, pending real comparative fare data this session doesn't have.

## 2. Root cause

Data-only, not a code bug — `routes/fares.py::build_fares_for_area`'s per-vehicle-type JSONB lookup is correct and already covered by `backend/tests/test_fares.py` (6/6 passing, re-verified this change touched no code). The `vehicle_pricing` rows for Regina/Saskatoon were seeded with identical rate numbers across vehicle types (likely copy-pasted a base row and only changed `vehicle_type`, never the rate fields).

**Live data had drifted since the original B8 investigation (2026-08-11) — the drafted proposal's premise was stale by the time this session ran it, so it was NOT applied as originally drafted:**

- **Regina**: doc assumed Economy `base_fare=2, per_km=2`; live value was actually `base_fare=0.02, per_km=0.02` — two orders of magnitude smaller, not a viable fare. Confirmed with the user this reads as a decimal-entry error, not intentional; corrected to `2.00` as part of this fix (see §7).
- **Saskatoon**: doc assumed Economy and XL both at `per_km=1`; live Economy `per_km` was actually `1.2` (XL stayed `1.0`) — Economy costing *more* per km than XL, an inversion not documented in B8 and not part of its original scope. Confirmed with the user to correct in the same pass rather than ship a second visible pricing oddity.
- **Schema mismatch in the original drafted SQL**: the B8 entry's drafted `jsonb_set(vehicle_pricing, '{XL}', ...)` statements assume `vehicle_pricing` is a JSON object keyed by vehicle-type name. Live schema is actually a JSON **array** of `{vehicle_type: "...", ...}` objects (confirmed against `routes/fares.py:249-254`'s read path, which iterates the array and keys off each element's `vehicle_type` field). Running the drafted SQL as-written would have been a **silent no-op** — `jsonb_set` doesn't match a non-numeric path segment against a JSON array, and would have returned the array unchanged with no error. Replaced with a full-array-literal `UPDATE ... SET vehicle_pricing = '[...]'::jsonb`, which is deterministic and matches the confirmed read contract.
- **Discovered but out of scope this pass**: a 6th service area, "Saskatoon Airport" (created 2026-07-30, after the last full area inventory), has the identical all-tiers-same-price defect and was never caught by the original B8 investigation. Not touched here — flagged as a residual gap in ACTION_ITEMS.md (see §6). A "Regina Airpot" row (note the typo — missing the second 'r') also carries the defect; the name typo itself may be a separate, unrelated bug (could cause area-name lookups elsewhere in the codebase to miss it) — also flagged, not fixed, here.

## 3. Fix / remediation

Two `UPDATE` statements run directly against production Supabase (`soavhtdhefowwvforzwb`, `ca-central-1`), replacing `service_areas.vehicle_pricing` wholesale for Regina and Saskatoon with a 3-element array (Economy/XL/Premium), each genuinely priced per the 1.4×/1.8× stopgap multiplier off a corrected Economy baseline:

- **Regina**: Economy `2.00/2.00` (corrected from live `0.02/0.02`) → XL `2.80/2.80` → Premium `3.60/3.60` (base_fare/per_km each; per_min/min_fare/booking_fee stay `0` across all three, matching Economy's existing `0`s).
- **Saskatoon**: Economy `4.00/1.00` (per_km corrected from live `1.2` back to `1.00`, resolving the Economy>XL per-km inversion) → XL `5.60/1.40` → Premium `7.20/1.80`.

No application code changed — `Files: none (data-only fix)` per the original B8 entry's own scoping, confirmed still accurate.

## 4. Risk & impact on existing functionality

**Blast radius, stated explicitly:** isolated to fare *display/quoting* for exactly 2 of 6 service areas (Regina, Saskatoon main areas — not their airport variants). Grepped every reader of `service_areas.vehicle_pricing`:
- `backend/routes/fares.py::build_fares_for_area` (the only consumer traced) — reads it read-only, per-request, no caching layer found that would serve a stale pre-update value after this change.
- No other backend module, admin-dashboard component, or rider/driver-app code was found constructing fares from this column directly — all fare estimates and ride creation flow through `build_fares_for_area`/`get_fares_for_location`, confirmed by grep for `vehicle_pricing` across `backend/`.

**Not a mid-ride change**: this affects *fare estimates shown before booking*, not an in-progress ride's already-locked fare. A rider mid-booking-flow who already saw the old (identical-across-tiers) quote and hasn't confirmed yet would see the new, differentiated quote on their next `/fares` poll or app refresh — same category of "visible mid-session" as any other pre-confirmation price display, not a fare change on an active/in-progress ride (rides in `in_progress` lock their fare independently of this table).

**Surge/corporate interaction**: unaffected — `build_fares_for_area`'s surge multiplier and corporate-account fare-source logic are applied on top of these base rates unchanged; this only changes the base numbers going into that same existing calculation, not the calculation itself.

## 5. User-experience effect

Rider-facing. Riders in Regina and Saskatoon will now see genuinely different prices when picking Economy vs. XL vs. Premium — previously the picker had no price effect, which could read as broken. This is the intended fix, not a regression. Explicitly **not** a claim that Spinr now undercuts Uber in these markets — that's a separate, deferred question (see §1) with no code or data change made toward it in this pass.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| Supabase `service_areas` table (production, `soavhtdhefowwvforzwb`) — `Regina` row | `vehicle_pricing` replaced: Economy `0.02/0.02`→`2.00/2.00`, XL `0.02/0.02`→`2.80/2.80`, Premium added `3.60/3.60` | Fix identical-fares defect (B8) + correct an implausible 100×-off Economy baseline found live |
| Supabase `service_areas` table (production) — `Saskatoon` row | `vehicle_pricing` replaced: Economy per_km `1.2`→`1.00`, XL `4/1`→`5.60/1.40`, Premium added `7.20/1.80` | Fix identical-fares defect (B8) + correct an Economy>XL per-km inversion found live |
| `ACTION_ITEMS.md` | B8 entry updated: stopgap applied and closed for Regina/Saskatoon; Uber-positioning question, Saskatoon Airport gap, and "Regina Airpot" typo logged as explicit follow-ups | Tracking |
| `docs/change-log/2026-08-12-b8-regina-saskatoon-vehicle-pricing.md` | New Change Impact Log | Required per `CLAUDE.md` — money-touching, live-tested surface |

No backend/frontend code files changed.

## 7. Before / after

```
-- Before (live, confirmed via SELECT immediately prior to this change)
Regina:    Economy 0.02/0.02   XL 0.02/0.02                    (no Premium)
Saskatoon: Economy 4/1.2       XL 4/1                          (no Premium)
```

```
-- After (confirmed via RETURNING on each UPDATE + a follow-up SELECT)
Regina:    Economy 2.00/2.00   XL 2.80/2.80   Premium 3.60/3.60
Saskatoon: Economy 4.00/1.00   XL 5.60/1.40   Premium 7.20/1.80
```

## 8. Rollback plan

Direct SQL, no migration/flag involved (this is a JSONB config column read per-request, not a value baked into any historical record). To revert to the exact pre-change state:

```sql
UPDATE service_areas SET vehicle_pricing = '[
  {"vehicle_type": "Economy", "base_fare": 0.02, "per_km": 0.02, "per_min": 0, "min_fare": 0, "booking_fee": 0},
  {"vehicle_type": "XL",      "base_fare": 0.02, "per_km": 0.02, "per_min": 0, "min_fare": 0, "booking_fee": 0}
]'::jsonb WHERE name = 'Regina';

UPDATE service_areas SET vehicle_pricing = '[
  {"vehicle_type": "Economy", "base_fare": 4, "per_km": 1.2, "per_min": 0, "min_fare": 0, "booking_fee": 0},
  {"vehicle_type": "XL",      "base_fare": 4, "per_km": 1,   "per_min": 0, "min_fare": 0, "booking_fee": 0}
]'::jsonb WHERE name = 'Saskatoon';
```

(Reverting to the pre-change values, not to some "correct" state — the pre-change Regina/Saskatoon numbers were themselves the defect. A genuine rollback target if this change needs to be undone is more likely a different, deliberately-chosen set of numbers than the ones being reverted to; this SQL is provided for completeness/traceability, not as a recommended action.) Takes effect immediately on the next `/fares` request — no cache to bust, no app deploy needed on either side.

## 9. Verification performed

- [x] Automated tests run: `backend/tests/test_fares.py` — **6 passed**, real `pytest` run (not reasoned about; initial run failed on a missing-dependency environment issue unrelated to this change, resolved by reinstalling backend deps with `python3 -m pip install -r requirements-locked.txt --require-hashes`, then re-run clean).
- [x] Blast-radius grep performed — see §4; `build_fares_for_area` confirmed as the sole reader of `service_areas.vehicle_pricing`.
- [x] Live data verified **before** writing (not just trusting the doc's stated premise) — this caught the stale-Regina-value and Saskatoon-inversion issues in §2, which would otherwise have shipped a broken or internally-inconsistent fix.
- [x] Live data verified **after** writing via `SELECT` + each `UPDATE`'s own `RETURNING` clause.
- [x] Functional simulation of the exact read-path logic (`vp_by_name` construction + per-vehicle-type lookup, mirroring `routes/fares.py:249-254` line-for-line) run directly against the just-written live JSON, confirming all 3 tiers resolve to distinct values in both areas and `Van` correctly stays unoffered (matches B8's original scope — no Van row seeded).
- [x] Reviewed against `CLAUDE.md` conventions: Decimal-safety N/A (this is direct SQL against a JSONB column, not app-layer float arithmetic — same reasoning the original B8 draft used); surge-never-retroactive/never-on-corporate rules unaffected (this touches base rates only, not surge or corporate logic).
- [ ] Manual repro in staging — not performed; no staging environment distinct from production exists for this data (`service_areas` is a production-only table per this repo's setup). First real-world confirmation is the next live `/fares` request against these two areas.

## 10. What was NOT verified

- Did **not** re-verify against a live rider-app fare-estimate screen (no device/simulator exercised) — verified at the data + backend-function-simulation level only, per §9.
- Did **not** address the Uber-competitive-positioning question raised by the user — explicitly deferred; the multipliers applied here are a stopgap for internal tier differentiation, not a claim about undercutting Uber.
- Did **not** fix "Saskatoon Airport" (same identical-fares defect, undocumented in the original B8 investigation) or the "Regina Airpot" name typo — both flagged in ACTION_ITEMS.md as residual gaps, out of scope for this pass.
- Did **not** investigate whether the "Regina Airpot" typo causes any area-name-string-matching bug elsewhere in the codebase (e.g., hardcoded area-name checks) — flagged, not investigated.
- No visual/screenshot verification exists for this data-only change (matches this repo's standing gap — no visual regression tooling for data-driven UI content).

## 11. Sign-off

- [x] Rollback plan is concrete and testable (§8) — with the caveat noted that the rollback target is the pre-change defect state, not a recommended end state
- [x] Blast radius is stated, not assumed (§4 — grepped, not guessed)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 — explicit that this is rider-visible and intended)
