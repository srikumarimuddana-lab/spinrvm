# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-30 |
| Author | Claude Code (session: rider-textbox-visibility) |
| Surface(s) | backend, driver-app |
| Domain (Sentry tag) | drivers (payments-adjacent) |
| PR / commit link | branch `claude/rider-textbox-visibility-d4w9lv` |
| Related issue or gap ID | Codex-style review of this branch — 6 findings acted on, 3 escalated |

## 1. Issue / gap identified

An adversarial review of this branch found that the in-trip incentive fix
(`834dd38` + `7f32f05`) **relocated** the earnings drop rather than removing it,
plus five smaller defects. Six are fixed here; three pre-existing money-semantics
gaps are escalated rather than fixed (§10).

## 2. Root cause

**(a) The drop moved to trip end.** `rides.driver_earnings` is fare-only. The
in-trip panel now adds the bonus, but `POST /drivers/rides/{id}/complete`
returns `serialize_ride_for_driver(ride)` plus four location fields — no
`incentive_amount`, no `total_earned`. `TripCompletedPanel` renders
`completedRide?.driver_earnings`, so a driver quoted $20.00 at offer time and
for the whole trip saw $15.00 the instant they tapped Complete.
`driver-app/app/driver/ride-detail.tsx:435` already did this correctly.

**(b) `null` conflated two meanings.** `get_active_ride` only assigned
`incentives`/`total_bonus` when the match was non-empty, so "no bonus" and
"lookup failed" were both `null`. The post-accept store branch overwrote
`activeRide` wholesale, so a transient DB blip made the headline drop by the
bonus and pop back. The pre-accept branch already guarded this; nothing read
the field post-accept before this branch, so the gap was newly reachable.

**(c)** Four smaller items: `offer_card.py` logged a DB error at `warning`
(CLAUDE.md forbids it) — and the new matcher raises deliberately so the caller
can decide; `incentive_service` re-derived `utils/money.to_decimal`;
`INCENTIVE_SELECT` fetched an `id` no caller reads, with a comment describing a
refactor the diff declined to do; `lifecycle.py` built its or-clause without
`_postgrest_or_value`, unlike its sibling settlement path.

## 3. Fix / remediation

| # | Fix |
|---|---|
| a | `ride_complete.py` returns `incentive_amount` and `total_earned` (the frozen snapshot's exact Decimal `total`); `TripCompletedPanel` prefers `total_earned`, falls back to `driver_earnings + incentive_amount`, and shows a Bonus row when there is one. New `tripCompleted.bonus` key in all three locales. |
| b | `get_active_ride` returns `[]`/`0.0` on a successful empty match and `null` only on failure; the store's post-accept branch keeps the last known value on `null`, mirroring the pre-accept branch. |
| c | `offer_card.py` → `logger.error` (still fails open); `incentive_service` imports `utils.money.to_decimal`; `id` dropped from `INCENTIVE_SELECT` with the comment corrected; `lifecycle.py` escapes `sa_id`. |

## 4. Risk & impact on existing functionality

- **One money-path file is now touched**: `lifecycle.py`'s escaping change. It
  only *escapes* an id that was previously interpolated raw, so for any
  well-formed UUID the PostgREST filter string is byte-identical and the same
  claim rows are written. A malformed id previously widened the or-clause; it
  now cannot. No other settlement logic changed.
- **`ride_complete.py`** gains two response fields; nothing existing is
  removed or renamed, and no stored value changes. `_earnings_snapshot` is
  initialised to `None` before its `try`, so a snapshot failure omits
  `total_earned` and the client falls back — the pre-fix behaviour.
- **Response-shape change:** `incentives` can now be `[]` where it was `null`.
  Checked every consumer: `RideOfferPanel` (`?.length ?? 0`), `carCard.bonusLabel`
  (`total > 0`), `perkLabel`, and both store branches (`?? existing`) all treat
  `[]`/`0` identically to `null`. The one backend test asserting `is None` is the
  **failure** case and still passes.
- **`TripCompletedPanel`** is rendered only from the driver dashboard; grepped
  for other importers — none.
- No ride-state, dispatch, wallet, Stripe, or insurance-period path touched. No
  migration.

## 5. User-experience effect

- **Driver-facing, visible mid-session.** With an incentive: the post-trip
  headline and shared receipt now show fare+bonus instead of fare only, with a
  Bonus row in the breakdown — the same figure the offer and the in-trip panel
  showed. Without one: unchanged.
- The in-trip headline no longer flickers when a poll's incentive lookup fails.
- Offer-card banner behaviour unchanged; only its log level moved.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/ride_complete.py` | Response carries `incentive_amount` + `total_earned` | The post-trip panel had only the fare to render |
| `backend/routes/drivers/ride_reads.py` | `[]`/`0.0` on empty match, `null` only on failure | Let the client tell "no bonus" from "unknown" |
| `backend/routes/offer_card.py` | `logger.warning` → `logger.error` | CLAUDE.md: never warn-and-continue on a DB error |
| `backend/routes/rides/lifecycle.py` | `_postgrest_or_value(sa_id)` + dual import | Matches the sibling settlement path and the query-filter rule |
| `backend/services/incentive_service.py` | Uses `utils.money.to_decimal`; dropped unread `id`; comment corrected | One money primitive, honest comment |
| `driver-app/components/dashboard/TripCompletedPanel.tsx` | Prefers `total_earned`; Bonus row; receipt line | The headline must not drop at trip end |
| `driver-app/components/dashboard/ActiveRidePanel.tsx` | Round each part once, sum the rounded parts | `driver_earnings` is a FLOAT column; split must equal headline |
| `driver-app/store/driverStore.ts` | Post-accept branch keeps last known bonus on `null` | Stop the headline flickering on a failed poll |
| `driver-app/i18n/{en,fr,es}.json` | `tripCompleted.bonus` | New label, all locales |
| `backend/tests/…`, `driver-app/__tests__/…` | 5 new tests | Pin each fix |

## 7. Before / after

```python
# Before — completion response: fare only
response = serialize_ride_for_driver(completed_ride)
response["location_ack"] = …

# After
response["incentive_amount"] = float(_total_bonus.quantize(Decimal("0.01")))
if _earnings_snapshot is not None:
    response["total_earned"] = _earnings_snapshot["total"]
```

```tsx
// Before — post-trip headline
${money(completedRide?.driver_earnings)}          // $15.00 after a $20.00 trip

// After
const earned = completedRide?.total_earned != null
  ? n(completedRide.total_earned)
  : n(completedRide?.driver_earnings) + bonus;
${earned.toFixed(2)}                              // $20.00
```

## 8. Rollback plan

`git revert` of this commit. The only durable-state-adjacent change is
`lifecycle.py`'s escaping, which does not alter which claims are written for a
well-formed id, so nothing needs data remediation. Everything else is a response
field or presentation. No migration, no flag.

Reverting this alone leaves the branch in its reviewed-but-unfixed state, where
the earnings drop happens at trip end instead of at acceptance — worse than
either endpoint. Revert the incentive series together or none of it.

## 9. Verification performed

- [x] **Every finding verified against the code before acting**, not taken at
      face value: read migration 96 for the schema claims, `TripCompletedPanel`
      lines 126/193/267, `ride_complete.py`'s actual response construction,
      `utils/money.py::to_decimal`, `lifecycle.py:236` vs `ride_complete.py:773`,
      and `build_earnings_snapshot`'s return shape.
- [x] Traced the `[]`-vs-`null` contract change through every consumer (§4),
      including the existing backend test that asserts `is None`.
- [x] `ruff check` clean on all changed backend files; whole-suite error count
      identical before and after (43 pre-existing, 0 new).
- [x] `py_compile` on all changed Python; `tsc --noEmit --noResolve` parse pass
      on all changed `.tsx`/`.ts`.
- [x] i18n edits verified as pure one-line additions (`git diff` shows `1 +` per
      locale, no reformatting).
- [ ] **No test executed.** npm, yarn and PyPI remain blocked by this session's
      egress policy. All 5 new tests are committed unexecuted.
- [ ] Manual repro — not performed.

## 10. What was NOT verified — and three findings deliberately NOT fixed

Three review findings are **real, pre-existing, and out of scope for a display
fix, because fixing them changes what drivers are PAID.** They are not addressed
here and need owner sign-off:

1. **`end_date` / `start_date` are ignored.** `is_active` is the only time gate,
   so a `time_limited` campaign whose `end_date` passed keeps quoting *and
   paying* forever.
2. **`max_budget` / `budget_used` are ignored, and `budget_used` is never
   incremented anywhere in the backend** (grep: only the `CREATE TABLE`). A
   campaign capped at $500 pays out unbounded, while the admin dashboard renders
   a budget bar as if it were enforced.
3. **`bonus_type='percentage'` and the `conditions` JSONB are ignored.** A
   `percentage` incentive's `bonus_amount` is treated as dollars; a
   `min_distance` incentive with `conditions.min_distance_km = 20` pays on a
   2 km ride. `incentive_type` is CHECK-constrained to five semantic variants,
   none of which any code path honours.

All three predate this branch and affect the settlement paths as much as the
display paths — which is exactly why centralising the rule made them worth
naming: `incentive_service.py`'s docstring says it "encodes the settlement rule
verbatim", and that rule was never complete. **Consider that docstring's claim
scoped to the four filters it does implement.** Fixing any of the three is a
money-behaviour change that belongs in its own reviewed commit.

Also not verified:
- Nothing was executed; every assertion is reasoned.
- `total_earned` is taken from the frozen snapshot, which is written in the same
  request. If the snapshot `update_one` succeeds but a later step fails, the
  response and the stored row could in principle disagree — not exercised.
- The new Bonus row and the changed headline were not screenshotted; driver-app
  has no visual-regression tooling.
- `fr`/`es` translations for "Bonus" ("Prime"/"Bono") were not reviewed by a
  speaker.
