# Change Impact & Risk Log — Android Auto earnings privacy + engaged-leg bar

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | srikumarimuddana@gmail.com (Claude Code) |
| Surface(s) | driver-app (Android Auto head-unit surface only) |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/android-auto-earnings-privacy-2nzgpp` |
| Related issue or gap ID | Live-testing report from the car: money legible to everyone in the vehicle |

## 1. Issue / gap identified

On the Android Auto surface the driver's day total was rendered in plain text
("TODAY $16.74") in the top-left pill for the whole session, and the trip fare
was rendered again in the bottom status bar for every engaged leg — including
while the paying rider was in the car. A head unit is a shared screen; both
numbers are the driver's personal financial information and were readable by
any passenger, and the mid-ride fare in particular invites a fare negotiation
the driver never asked for.

## 2. Root cause

Both were designed against the phone's threat model, where the screen belongs
to one person. `carSurface.tsx` printed `todayEarnings` unconditionally, and
`CarTripCard.tsx` rendered `card.fareLabel` on every leg (only the type size
changed between legs). Nothing in the car layer distinguished "the driver is
looking" from "the car is looking".

## 3. Fix / remediation

- New `lib/androidAuto/carEarningsPrivacy.ts`: an in-memory store (`hidden`,
  default **true**) plus the pure `displayEarnings(amount, hidden)` helper.
  While hidden the amount is never produced — the pill renders `••••`, not a
  styled-out number.
- The TODAY pill now carries an eye glyph stating the current state and shows
  the mask until revealed.
- Reveal/re-hide is a new **eye map button** in the MapTemplate action strip
  (the surface is non-interactive, so this is the only interaction channel
  Android Auto allows). Android Auto caps the strip at 4 and a ride already
  fills it (Navigate + Recenter + 2 zoom), so the toggle appears only when
  there is no route — i.e. idle and trip-completed, the legs the pill is
  actually read on. Mid-ride the total simply stays masked.
- Every `didConnect` re-masks (`useCarEarningsPrivacy.reset()`), so a reveal
  never carries into the next drive or the next person in the car.
- `CarTripCard` no longer renders any money on `pickup`/`dropoff` legs.
  Distance leads the right-hand column with ETA under it. The `complete` leg
  keeps its total — that card exists to report it — behind the same mask.
- The on-surface debug fact no longer echoes the amount (`today masked/shown`).

Money remains at full hero scale at the one moment it is a decision: the ride
offer (`CarOfferPanel` + the offer alert), which the driver sees before anyone
is in the car. That path is untouched.

## 4. Risk & impact on existing functionality

Blast radius: **isolated to the Android Auto surface of driver-app.**

- `carEarningsPrivacy.ts` is new; consumers are exactly `carSurface.tsx`,
  `CarTripCard.tsx`, `register.ts` (grepped: no others).
- `CarTripCard` importers: `carSurface.tsx` only. `CarOfferPanel` (the offer
  moment) is a separate component and is unchanged.
- `carCard.ts` / `buildTripCard` are untouched — `fareLabel`,
  `totalEarningsLabel` and the rest are still computed exactly as before; this
  change is purely about which of them the engaged-leg bar renders. The offer
  alert text (`buildOfferAlertText`) is unchanged.
- No backend call, no ride-state transition, no wallet/Stripe path, no money
  arithmetic changed. `fetchEarnings('day')` is called exactly as before.
- Map-button strip: the in-ride strip is byte-for-byte the previous 4 buttons;
  only the no-route strip gained an entry (3 → 4, still at the cap).
- Phone app, rider app, admin: not touched.

## 5. User-experience effect

Driver-facing, and visible **mid-session** to a driver currently connected to a
head unit:

- The TODAY pill shows `👁 ••••` instead of the amount until the driver presses
  the new eye button. Discoverability risk is the real cost here: a driver used
  to seeing the number may read the mask as a bug. The eye glyph and the eye
  map button are the mitigation.
- The bottom bar during pickup/trip shows distance + ETA where the fare used to
  be. No information the driver needs to drive the leg was removed.
- Rider-facing: a rider glancing at the dash no longer sees what the driver is
  paid. No rider-app change.
- No notification or backend copy changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/lib/androidAuto/carEarningsPrivacy.ts` | New store + `displayEarnings` | Single, testable place for the "masked by default" rule |
| `driver-app/lib/androidAuto/carSurface.tsx` | Pill masks the amount, adds eye glyph; debug fact no longer echoes it | The pill was the main leak |
| `driver-app/lib/androidAuto/CarTripCard.tsx` | No fare on engaged legs; distance/ETA lead; completed total masked | Fare beside the paying rider |
| `driver-app/lib/androidAuto/register.ts` | Eye map button (no-route strips only); re-mask on connect | Only interaction channel the surface has |
| `driver-app/assets/images/eye.png`, `eye_off.png` | New 72×72 monochrome glyphs | Match the existing map-button icon set |
| `driver-app/lib/androidAuto/__tests__/carEarningsPrivacy.test.ts` | New | Locks the privacy rule, not the styling |
| `driver-app/lib/androidAuto/__tests__/register.test.ts` | Idle strip is now 4; toggle + no-toggle-in-ride cases | Strip cap is a hard Android Auto limit |
| `docs/change-log/2026-08-20-android-auto-earnings-privacy.md` | This entry | Policy |

## 7. Before / after

```tsx
// Before — carSurface.tsx (pill), always the number
<Text style={styles.earningsPillValue}>{todayEarnings}</Text>

// Before — CarTripCard.tsx, every engaged leg
{card.fareLabel && (
  <Text style={moneyLeads ? styles.fareLead : styles.fare}>{card.fareLabel}</Text>
)}
```

```tsx
// After — carSurface.tsx
<Text style={...}>{earningsHidden ? '●⃠' : '◉'}</Text>
<Text style={styles.earningsPillValue}>
  {displayEarnings(todayEarnings, earningsHidden)}
</Text>

// After — CarTripCard.tsx
const fareText = moneyLeads ? displayEarnings(card.fareLabel, earningsHidden) : null;
const lead = moneyLeads ? null : card.distanceLabel;   // distance replaces the fare
```

## 8. Rollback plan

No flag, no migration, no live data touched — this is display-only code inside
one surface, shipped in the driver-app binary, so there is no server-side
switch that could change it and none is warranted. Reverting the commit and
shipping the next driver-app build restores the previous rendering exactly;
nothing persists that a revert would have to unwind (the privacy state is
in-memory and per-session by design). Interim mitigation if a driver dislikes
the mask before a rebuild: press the eye button — reveal is one press.

## 9. Verification performed

- [x] Automated tests run — `carEarningsPrivacy.test.ts` (new) and the full
      `lib/androidAuto` Jest suite including the updated `register.test.ts`.
- [ ] Manual repro in staging — **not done**: this renders only on a physical
      Android Auto head unit or the DHU, neither available in this environment.
- [x] Blast-radius grep — `displayEarnings`, `useCarEarningsPrivacy`,
      `CarTripCard`, `earningsPill`, `fareLabel`, `todayEarnings` across
      `driver-app/` (excluding `node_modules`).
- [x] Reviewed against `CLAUDE.md` — PIPEDA (data minimization on a shared
      screen), Car app quality guidelines (no animation added, static glyph),
      surgical-change rule (money computation untouched).
- [ ] Feature-flagged — no. Display-only, one surface, reversible by the driver
      in one press; a flag mechanism does not exist for this surface.

## What was NOT verified

- **Nothing was run on hardware.** The whole Android Auto surface is already
  marked UNPROVEN ON HARDWARE in `carSurface.tsx`; this change inherits that.
  The eye glyphs' legibility at arm's length, and the 4-button strip actually
  accepting the new entry on a real head unit, are unconfirmed.
- No production build was run (`eas build` / an Android release build) — the
  change is TS/TSX inside driver-app with no native or config surface, but
  `npm run build` has no equivalent for this app and none was executed.
- This repo has **no visual-regression tooling for driver-app**, so the pill
  and bar layouts were reasoned about, not screenshotted. Standing gap.
- Whether masking-by-default is the right default for drivers who drive alone
  is a product judgement, not something a test can settle; if it proves
  annoying, the alternative is persisting the last choice, which weakens the
  privacy property.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] UX field filled in for a behavior change to an already-shipped screen
