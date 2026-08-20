# Change Impact & Risk Log — Android Auto rich ride offer

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | Nighil (via Claude Code) |
| Surface(s) | driver-app (Android Auto head-unit surface only) |
| Domain (Sentry tag) | dispatch |
| PR / commit link | branch `claude/android-auto-screens-2katir` — commits `979a88e`, `adb90bd`, `a8db173` |
| Related issue or gap ID | Reported in live testing: "ride accept is not rich like what we have in app, it's very simple in android auto" |

## 1. Issue / gap identified

The ride-offer presentation on Android Auto was two lines of system-styled text and
led with the **base fare**, while the phone's `RideOfferPanel` leads with **fare +
bonus** at 52px. On a bonused ride the car therefore advertised the same offer as
worth *less* than the phone did, on the one screen where the driver decides whether
to take it. Reported by the product owner during live app testing.

## 2. Root cause

Two distinct causes, not one:

1. **Wrong number.** `carCard.ts` only ever exposed `fareLabel` (`money(offer.fare)`),
   the bare fare. `total_bonus` was present in the same payload and rendered as a
   separate `bonusLabel` chip, but nothing ever summed them, so neither the surface
   nor the alert could show what the phone shows.
2. **Nowhere to be rich.** The `NavigationAlert` API accepts a title string, a
   subtitle string and two actions — we cannot set size, colour or layout. The car
   *surface* (a React root we fully control) deliberately rendered **nothing** during
   an offer: `carSurface.tsx` had `card.leg !== 'offer'` guarding `CarTripCard`,
   added earlier because a duplicate bar under the alert produced "two overlapping
   panels with no clear target". That reasoning was sound for a duplicate panel, but
   it left the only stylable surface unused, so the offer had no rich presentation
   available to it at all.

## 3. Fix / remediation

- `carCard.ts` gains `totalEarningsLabel` (fare + bonus), `fareBreakdownLabel`,
  `perKmLabel`, `pickupLabel`/`dropoffLabel`, and `quietMode`/`isScheduled`/
  `cashPayment` flags — all additive.
- New `CarOfferPanel.tsx` renders the offer on the surface: hero earnings at
  `carType.hero` (46px), the fare+bonus split, `$/km`, the **100% YOURS · $0
  COMMISSION** badge, surge/WAV/quiet/pre-booked/cash badges, and both route ends.
  Three columns, because head units are wide and short (~2.6:1) — the same
  constraint documented in `CarTripCard.tsx`.
- Alert title now leads with the money: `$17.50 · Sarah ★4.9` instead of
  `New ride · Sarah ★4.9`.
- The alert-text builder moved out of `register.ts` into
  `carCard.buildOfferAlertText` so it is testable without the native module.

The panel deliberately repeats **neither** button. The alert still solely owns
Accept/Decline, so the "no clear target" failure mode does not return: the two are
layers (information vs. action), not rivals.

## 4. Risk & impact on existing functionality

**Blast radius: isolated to the Android Auto surface of driver-app.**

Grepped every consumer of the changed module
(`grep -rn "from './carCard'|buildOfferCard|buildTripCard|TripCard"`). `carCard.ts`
has exactly four consumers, all under `lib/androidAuto/`:

| Consumer | Effect |
|---|---|
| `carSurface.tsx` | Intended change — renders the new panel during `offer`. |
| `register.ts` | Intended change — alert title/subtitle. |
| `CarTripCard.tsx` | **None.** Reads only pre-existing fields; every new field is additive. Its stale doc comment was corrected. |
| `__tests__/carCard.test.ts` | Extended. |

Nothing on rider-app, admin-dashboard, or backend imports `carCard`. The phone's
`RideOfferPanel.tsx` is untouched.

- **No money is moved or computed.** These are display strings. Authoritative fare
  stays backend-side in `Decimal` and settles from there. `totalEarnings` uses the
  *same* plain-float `baseFare + totalBonus` as `RideOfferPanel.tsx:150` **on
  purpose** — a "more correct" rounding here would make the car and phone quote the
  same ride differently, which is a worse bug than the imprecision it fixes.
- **Ride state machine untouched.** No transition, no WS event, no dispatch change.
- **Accept/Decline path byte-for-byte unchanged** — same `acceptRide(rideId)` /
  `declineRide(rideId)` calls, same `durationMs`, same `onDidDismiss` semantics. The
  store keeps owning the offer countdown and auto-decline.
- **No background loop interaction.**
- **Backend-compat:** `quiet_mode` / `is_scheduled` / `payment_method` are optional
  with falsy defaults, so an offer payload from a backend that omits them renders
  exactly as today (badge simply absent) rather than throwing. Covered by a test.

Residual risk is **visual only, and only on the head unit**: the panel could crowd
the alert on an unusually small or differently-proportioned head unit. See §11.

## 5. User-experience effect

- **Who sees it:** drivers with Android Auto connected, at the moment a ride is
  offered. Nobody else — riders, corporate admins and internal admins see nothing.
- **Mid-session visibility:** yes — an online driver sees the new panel on their very
  next offer after updating. This is the intended change, not a side effect.
- **Copy change:** yes. Alert title `New ride · Sarah ★4.9` → `$17.50 · Sarah ★4.9`,
  and the new on-surface strings (`YOUR EARNINGS`, `100% YOURS · $0 COMMISSION`,
  `PICK-UP` / `DROP-OFF`, badge labels). All are specific and non-technical.
  `100% YOURS · $0 COMMISSION` restates Spinr's actual 0%-commission model
  (`CLAUDE.md` → "What Spinr Is NOT"), so it is a factual claim, not marketing puff.
- **Not a hidden-fee risk:** the panel *adds* disclosure (the fare/bonus split is now
  shown explicitly), it does not mask any line item.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/lib/androidAuto/carCard.ts` | Added `totalEarningsLabel`/`fareBreakdownLabel`/`perKmLabel`/`pickupLabel`/`dropoffLabel`/`quietMode`/`isScheduled`/`cashPayment` + `buildOfferAlertText`; widened `OfferLike.fare` to `string \| number` | The model had no concept of "what the driver banks"; alert text was untestable inline in `register.ts` |
| `driver-app/lib/androidAuto/CarOfferPanel.tsx` | **New.** Rich three-column offer panel | The only surface we can actually style was going unused during an offer |
| `driver-app/lib/androidAuto/carSurface.tsx` | Render `CarOfferPanel` when `card.leg === 'offer'` | Was rendering nothing at all in that state |
| `driver-app/lib/androidAuto/CarTripCard.tsx` | Doc comment only | Its comment asserted "nothing is shown during an offer", now false |
| `driver-app/lib/androidAuto/register.ts` | Alert title/subtitle now from `buildOfferAlertText` | Money leads the title; removes an inline copy a test could only mirror |
| `driver-app/lib/androidAuto/__tests__/carCard.test.ts` | +21 cases | Cover the new money math, badges, and alert text |

## 7. Before / after

```
# Before — alert, bonused ride ($14.50 fare + $3.00 bonus)
TITLE: New ride · Sarah ★4.9          <- largest text spent on a label
 SUB : $14.50 · +$3.00 bonus          <- base fare; phone said $17.50
 SUB : 8 min away · 3.2 km · 1.5× surge · WAV
 SUB : → 202 Dropoff Ave
# Surface during offer: nothing rendered.
```

```
# After
TITLE: $17.50 · Sarah ★4.9            <- agrees with the phone
 SUB : $14.50 fare + $3.00 bonus · $5.40/km
 SUB : 8 min away · 3.2 km · 1.5× surge · WAV
 SUB : → 202 Dropoff Ave
# Surface during offer: CarOfferPanel —
#   $17.50 hero (46px) | 3.2 km / 8 min / $5.40 per km | PICK-UP → DROP-OFF
#   + 100% YOURS · $0 COMMISSION, + surge/WAV/quiet/pre-booked/cash badges
```

## 8. Rollback plan

No feature flag, and this is stated deliberately rather than glossed:

- **Nothing is persisted and nothing is applied to live data.** The change is
  render-only — no DB write, no wallet delta, no Stripe call, no ride-state
  transition. So there is no data-level remediation to plan: a code revert *is* a
  complete rollback here, which is the narrow case where `CLAUDE.md` accepts one.
- **Revert without a store release:** the driver-app is Expo, so `eas update` on the
  previous bundle rolls every connected head unit back on next launch — no
  redeploy and no app-store round trip.
- **Partial rollback:** reverting `adb90bd` alone removes the on-surface panel and
  restores the previous "alert only" behavior while keeping the corrected money in
  the alert (`a8db173`). Reverting `a8db173` restores the old alert text. The three
  commits are independently revertible in either order.
- **Flag deferred, with reason:** `app_settings` is a backend-read mechanism; the car
  surface reads `useDriverStore`, not `app_settings`, so wiring a flag would mean
  adding a new plumbing path to a surface that has none — more new code than the
  change itself, on a render-only diff. Recorded as a judgement call, not an
  oversight. If we add a driver-app remote-config path later, this panel is a
  reasonable first consumer.

## 9. Verification performed

- [x] **Type-check:** `npx tsc --noEmit` on driver-app — clean. (Two pre-existing
      errors in `shared/config/firebaseConfig.ts` for missing `firebase/*` types are
      unrelated and present on `main`.)
- [x] **Lint:** `npx eslint` on all five changed files — 0 errors. One pre-existing
      unused-import warning (`HeatmapCell`, `carSurface.tsx:27`) on a line not touched.
- [x] **Logic verified by execution:** 63 assertions run against the compiled module
      (50 on the card model, 13 on `buildOfferAlertText`) — all pass, including the
      pre-existing offer-card labels, confirming no regression. See the caveat in §11
      about *how* these were run.
- [x] **Blast-radius grep performed:** searched `from './carCard'`, `../carCard`,
      `androidAuto/carCard`, `buildOfferCard`, `buildTripCard`, `TripCard` across all
      `.ts`/`.tsx` outside `node_modules`. Four consumers found, all listed in §4.
      Also grepped `store/driverStore.ts` to confirm `quiet_mode` / `payment_method`
      exist on `ActiveRide.ride` before reading them.
- [x] **Reviewed against `CLAUDE.md`:** money (display-only, matches phone
      deliberately), ride state machine (untouched), PIPEDA (no new PII — addresses
      are rendered to the driver who is being dispatched to them, and nothing new is
      logged), "What Spinr Is NOT" (0%-commission claim is accurate; no hidden fee
      introduced; surge shown before booking, never retroactively).
- [ ] **Feature-flagged** — no; justified in §8.
- [ ] **Manual staging repro** — not performed; see §11.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`eas update` to previous bundle;
      per-commit revert paths named)
- [x] Blast radius is stated, not assumed (grep listed, four consumers named)
- [x] No silent behavior change — UX field (§5) filled in, including the alert
      title copy change

## 11. What was NOT verified

Stating the real boundary rather than letting the checkmarks above imply full coverage:

- **The jest suite was never run, because it cannot run in this container.**
  `node_modules/firebase/package.json` is missing from the install, so `jest.setup.js:67`
  (`jest.mock('firebase/auth')`) fails module resolution and **every** driver-app
  suite dies before any test body executes — verified by running the untouched
  `__tests__/androidAutoDistribution.test.ts`, which fails identically. This is a
  broken container install, not a repo-config defect, so it was left alone rather
  than "fixed" by editing `jest.config.js` (that would risk breaking real CI to make
  a local symptom go away). **The 21 new test cases in `carCard.test.ts` are
  therefore committed but unexecuted by jest.** They must be run in CI or on a clean
  install before this is trusted as covered. The 63 assertions cited in §9 were run
  by compiling `carCard.ts` standalone with `tsc` and executing the real exported
  functions in node — that validates the logic, but not the jest wiring, and it does
  not prove the new test file itself passes under jest.
- **No production build was run** (`npx expo export` / EAS). `CLAUDE.md` requires a
  real build for `admin-dashboard`/`rider-app`/`driver-app` changes; only
  `tsc --noEmit` and eslint were run here. A native build was not attempted in this
  environment.
- **Never rendered on hardware.** No head unit, no Android Auto DHU, no emulator. The
  three-column layout, the 46px hero, and whether the panel visually crowds the
  system alert are **reasoned about, not observed**. `carSurface.tsx` already carries
  an "UNPROVEN ON HARDWARE" banner for exactly this reason and it still applies. The
  real-hardware photo the report came from shows an engaged trip, not an offer, so
  even the reported state was not re-photographed after the change.
- **No visual/snapshot regression tooling exists for this surface** (standing gap —
  `CLAUDE.md` release gate #6). There is no automated way to catch a layout
  regression on the car surface, and this change is almost entirely layout.
- **Overlap with the alert is unmeasured.** The claim that panel-and-alert no longer
  compete rests on the panel omitting both button labels — an argument about intent,
  not a measurement of pixels on a real head unit at real proportions.
- **Untested badge combinations on hardware.** The all-badges case (surge + WAV +
  quiet + pre-booked + cash) is asserted in string form only; whether five badges fit
  the header strip without wrapping on a narrow unit is unknown.
