# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-30 |
| Author | Claude Code (session: rider-textbox-visibility) |
| Surface(s) | backend, driver-app |
| Domain (Sentry tag) | drivers (dispatch + payments adjacent) |
| PR / commit link | branch `claude/rider-textbox-visibility-d4w9lv` |
| Related issue or gap ID | Live-testing report: in-trip "YOUR EARNINGS" excludes incentives |

## 1. Issue / gap identified

During an active ride the driver app's "YOUR EARNINGS" figure showed the bare
fare with no incentive bonus, even when the offer the driver accepted had
promised one. Separately, the bonus quoted at offer time could be larger than
the bonus settlement would ever pay.

## 2. Root cause

Two independent defects, both in the direction of misquoting the driver.

**(a) The projection stopped at acceptance.** `rides.driver_earnings` is
fare-only by design — the bonus lives in `ride_incentive_claims`, written at
completion (`routes/drivers/ride_complete.py`). `GET /drivers/rides/active`
computed the incentive projection only under
`if ride.get("status") == RideStatus.DRIVER_ASSIGNED.value`, so from
`driver_accepted` onward it returned `incentives: null, total_bonus: null` and
every post-acceptance surface fell back to the fare alone. The phone panel
never read the field at all; the Android Auto trip card
(`lib/androidAuto/carCard.ts`) already read `activeRide?.total_bonus` and was
simply never given a value.

**(b) Five copies of one matching rule, two of them authoritative.** The
"which incentives apply to this ride" filter was reimplemented in
`ride_complete.py` and `rides/lifecycle.py` (which *write* claims) and in
`rides/matching.py`, `offer_card.py` and `drivers/ride_reads.py` (which only
*display* a projection). The display copies had drifted looser:

| | zero-value incentive | ride with no `service_area_id` |
|---|---|---|
| settlement paths | skipped (`bonus_amount <= 0`) | `service_area_id IS NULL` only |
| display paths | counted → `+$0.00` chip | no area filter → **every area's** incentives |

So an incentive disabled by zeroing still rendered a chip, and an area-less
ride was quoted bonuses it could never claim.

## 3. Fix / remediation

New `backend/services/incentive_service.py` holds the rule once, encoding the
**settlement** semantics verbatim; `matching.py`, `offer_card.py` and
`ride_reads.py` call it. It takes the db module as an argument — the reason
`offer_card.py` gave for duplicating dispatch's lookup rather than sharing it.
`ride_reads.py` now runs the projection for every active status.

Driver-app `ActiveRidePanel` adds the projected bonus to the headline and
renders a `$X + $Y bonus` split **only when a bonus applies** — same wording as
`RideOfferPanel`, so the figure the driver accepted is the figure they keep
seeing. With no incentive the panel is unchanged.

## 4. Risk & impact on existing functionality

- **Blast radius: cross-surface, but money-read-only.** The two claim-writing
  paths (`ride_complete.py`, `rides/lifecycle.py`) are deliberately untouched,
  so **no change to what any driver is paid** — only to what they are told.
  Also untouched: `repositories/ride_repo.py` and `routes/rides/queries.py`
  (both read `ride_incentive_claims` after the fact) and
  `routes/admin/incentives.py` (CRUD).
- **Consumers of the changed display paths**, all greppped: the dispatch WS
  `new_ride_assignment` payload and FCM push title (`matching.py`), the
  offer-card notification banner (`offer_card.py`), and
  `GET /drivers/rides/active` → driver-app `RideOfferPanel` (pre-accept),
  `ActiveRidePanel` (new), and `lib/androidAuto/carCard.ts` (already wired).
- **Behaviour change for existing rides:** a driver on a ride with a zeroed
  incentive, or on a ride with no service area, will now see a **smaller**
  quoted bonus than before — the correct one. This corrects an over-quote; it
  can look like a regression to anyone comparing against the old number.
- **Dispatch SLA:** unchanged. The shared matcher issues the same single query
  in the same parallel-enrichment slot, and selects two fewer columns
  (`bonus_type`, `conditions` were fetched by `matching.py` and never used).
- **Error handling:** the matcher raises rather than returning `[]` on a DB
  failure — an empty result and a failed lookup mean different things to a
  driver being quoted money. Each caller keeps its own existing fail-open
  handling and log level, so no path's degradation behaviour changed.
- No ride-state transition, WS contract field, wallet delta, Stripe call, or
  background loop is touched. No migration.

## 5. User-experience effect

- **Driver-facing, and visible mid-session** to a driver already on a trip.
- With an incentive: the in-trip headline rises from fare to fare+bonus, and a
  small green `$15.00 + $5.00 bonus` line appears under YOUR EARNINGS. Android
  Auto's trip card starts showing the bonus it was already coded to show.
- Without an incentive (the common case): **no visible change at all** — no
  `+$0.00` line, identical layout.
- Pre-accept offer panel/banner: unchanged except that an over-quoted bonus
  (zeroed incentive, or an area-less ride) now shows the real, smaller figure.
- No rider-facing change. No copy or notification-text change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/incentive_service.py` | **New.** `match_ride_incentives()` + `incentive_display_payload()` | One source of truth for the matching rule, encoding settlement semantics |
| `backend/routes/drivers/ride_reads.py` | Uses the shared matcher; projection no longer gated on `driver_assigned` | The bonus is claimed at completion, so it belongs on the panel for the whole ride |
| `backend/routes/rides/matching.py` | `_fetch_incentives` delegates to the matcher | Offer quote == settlement quote |
| `backend/routes/offer_card.py` | Banner bonus delegates to the matcher | Same, for the notification image |
| `driver-app/components/dashboard/ActiveRidePanel.tsx` | New `totalBonus` prop; headline = fare + bonus; conditional split line + a11y label | Surface the bonus in-trip, and only when there is one |
| `driver-app/app/driver/(tabs)/index.tsx` | Passes `activeRide?.total_bonus` | Wire the store field the panel now reads |
| `backend/tests/test_incentive_service.py` | **New.** 10 unit tests | Pin the settlement semantics the shared rule encodes |
| `backend/tests/test_driver_ride_flow_coverage.py` | `.is_()` on the chain stubs; new in-progress incentive test | See §10 — the stub gap made two assertions vacuous |
| `backend/tests/test_offer_card_route.py`, `test_dispatch_notify_loop_branches.py` | `.is_()` on the chain stubs | Same |
| `backend/tests/test_active_ride_rider_pii.py` | Comment only | `driver_accepted` no longer skips the incentive lookup |
| `driver-app/__tests__/components/ActiveRidePanel.test.tsx` | 4 new tests | Pin show-when-present / hide-when-absent |

## 7. Before / after

```python
# Before — ride_reads.py: projection dies at acceptance, looser than settlement
if ride.get("status") == RideStatus.DRIVER_ASSIGNED.value:
    iq = supabase.table("ride_incentives").select(...).eq("is_active", True)
    if sa_id:
        iq = iq.or_(f"service_area_id.is.null,service_area_id.eq.{sa_id}")
    # no else -> an area-less ride matches EVERY area's incentives
    for inc in ir.data or []:
        ...
        _bonus += float(inc.get("bonus_amount") or 0)   # counts a 0.00 incentive
```

```python
# After — every active status, settlement's own filter
try:
    _matched = await match_ride_incentives(db_supabase, ride)
    if _matched:
        incentives, total_bonus = incentive_display_payload(_matched)
except Exception as e:
    logger.error(f"get_active_ride: incentive lookup failed: {e}", exc_info=True)
```

```tsx
// Before — ActiveRidePanel: fare only
const earnings = ride.driver_earnings ?? ride.total_fare ?? 0;

// After — fare + projected bonus, split shown only when there is one
const fareEarnings = ride.driver_earnings ?? ride.total_fare ?? 0;
const bonusEarnings = totalBonus ?? 0;
const hasBonus = bonusEarnings > 0;
const earnings = fareEarnings + bonusEarnings;
```

## 8. Rollback plan

`git revert` of the two commits is a complete rollback. Nothing here writes to
the database, moves money, or changes a stored value — the backend half is a
read/projection change and the app half is presentation. No migration, no
`app_settings` value, no live-data remediation needed.

Not feature-flagged. The over-quote correction is a truthfulness fix that
should not ship dark, and the app half is inert without an incentive
configured. Note the split rollout: the backend half reverts on deploy, while
the driver-app half needs an OTA/EAS update like any other app change. That
ordering is safe in both directions — an old app simply ignores `total_bonus`,
and a new app with the old backend sees `null` and renders exactly as it does
today.

## 9. Verification performed

- [x] Blast-radius grep performed — `ride_incentives` (all 5 backend query
      sites, enumerated in §2/§4), `total_bonus` / `incentives` /
      `incentive_amount` across `backend/`, `driver-app/`; every consumer named
      in §4.
- [x] Reviewed against `CLAUDE.md` conventions: dual-import pattern used for
      both new imports; money summed in `Decimal` and converted to float only
      at the JSON boundary; `_postgrest_or_value()` escaping on the `or_` clause
      per the query-filter rule; no error silently swallowed (the matcher
      raises; callers keep their existing, deliberate fail-open handling).
- [x] `ruff check` clean on every file changed. Test-suite lint error count is
      identical before and after (21 pre-existing, 0 new).
- [x] All changed Python compiles (`py_compile`); both changed `.tsx` files
      parse under a standalone `tsc --noEmit --noResolve` pass.
- [x] Read every existing test that stubs these query paths and traced my
      change through it by hand (see §10 for what that found).
- [ ] **No test was executed — Python or JavaScript.** `registry.npmjs.org`,
      `registry.yarnpkg.com` and PyPI are all blocked by this session's egress
      policy (403 on CONNECT), so neither `pytest` nor `jest` could be
      installed. `npm run build` was likewise not run.
- [ ] Manual repro / staging check — not performed (no device or backend
      instance in this environment).

## 10. What was NOT verified

- **Nothing was run.** The 14 new tests (10 Python, 4 TSX) are committed
  unexecuted and must pass in CI before merge. The same applies to every
  existing test I reasoned about below.
- Tracing by hand did find one real problem, which is fixed here: three test
  chain stubs implemented `or_` but not `is_`, so the moment the shared matcher
  took the area-less branch the stub returned a non-list, the lookup raised into
  its own non-fatal handler, and
  `test_driver_assigned_status_includes_incentives_and_quest_hint` and
  `test_banner_skips_incentive_for_other_vehicle_type` would have passed
  vacuously. That this needed catching by reading is exactly the risk of not
  being able to run the suite — there may be more of it that I did not catch.
- The `ride_incentives` matching rule is pinned to what the two settlement paths
  do **today**; I did not verify against production data that those two agree
  with each other in every case, only that they are byte-identical in source.
- No visual check of the new bonus line. Per `CLAUDE.md` release gate #6,
  driver-app has no visual-regression tooling, so the layout of the extra line
  inside `earningsBox` was reasoned about, not screenshotted. It is a 9px line
  under an existing label in a fixed-width box — if it wraps awkwardly on a
  small screen, that will only show on a device.
- Android Auto's trip card is stated to benefit "for free" from the backend fix
  because it already reads `activeRide?.total_bonus`; that was read from
  `carCard.ts`, not observed on a head unit. Note it computes its total from
  `ride?.total_fare` where the phone panel uses `driver_earnings ?? total_fare`
  — a pre-existing inconsistency between the two surfaces that I did not touch.
- Whether any incentive is currently configured with `bonus_amount = 0`, or any
  live ride has a null `service_area_id`, was not checked against production —
  so the real-world size of the over-quote being corrected is unknown.
