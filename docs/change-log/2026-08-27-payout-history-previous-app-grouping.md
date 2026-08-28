# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | Claude Code (interactive session), user request: "fix the payout-history bug too" |
| Surface(s) | driver-app |
| Domain (Sentry tag) | payments |
| PR / commit link | commit following this log |
| Related issue or gap ID | `docs/migration/2026-08-27-legacy-data-full-migration-approach.md` §6, second bullet |

## 1. Issue / gap identified

`driver-app/app/driver/payout-history.tsx` groups payout rows into a regular Spinr list and a
"Previous app" footer section using `payout_type !== 'stripe_sync'` / `=== 'stripe_sync'`. Two
other payout types the backend actually writes to a driver's payout history — `legacy_import`
(the legacy-booking importer's offsetting payout) and `legacy_outstanding_correction` (the legacy
payout-correction service) — were falling through into the regular Spinr section instead of the
"Previous app" one, flagged but not yet confirmed in the migration plan doc.

## 2. Root cause

Grepped the whole backend for every `payout_type` value ever written to the `payouts` table:
`auto` and `instant` (real Spinr-native payouts), and three previous-app types — `stripe_sync`
(`services/stripe_payout_sync_service.py`), `legacy_import` (`utils/legacy_rides.py`'s
`LEGACY_OFFSET_PAYOUT_TYPE`, written by `services/booking_import_service.py`), and
`legacy_outstanding_correction` (`services/legacy_payout_correction_service.py`). The frontend's
binary `stripe_sync`-only filter only ever accounted for the first previous-app type; the other
two are real rows the backend's `routes/drivers/payouts.py::get_payout_history` endpoint returns
completely unfiltered by type (it returns every `payouts` row for the driver, and always has — the
grouping logic lives entirely on the frontend).

Also found while fixing: the file's own comment claimed "the server stops sending these rows after
the transition cutoff (Aug 31, 2026)... so this section retires itself with no app release
needed." That was true when written, but stale — `routes/drivers/payouts.py`'s own comment
documents a 2026-08-13 "blended lifetime earnings" decision that removed the
`previous_app_history_visible()` date filter entirely: previous-app payout rows are now shown
permanently (hiding a driver's own real payout rows after a date would make their history look
like it lost entries, the same trust problem an earlier fix addressed for trip counts). Corrected
the comment alongside the type-grouping fix rather than leaving it to mislead the next reader.

## 3. Fix / remediation

- `driver-app/app/driver/payout-history.tsx`: replaced the binary `=== 'stripe_sync'` /
  `!== 'stripe_sync'` filter with an explicit `PREVIOUS_APP_PAYOUT_TYPES` set (`stripe_sync`,
  `legacy_import`, `legacy_outstanding_correction`), hoisted to module scope (avoids recreating
  the `Set` every render and avoids a `react-hooks/exhaustive-deps` warning on the two `useMemo`
  calls that reference it).
  - `spinrHistory` = rows whose `payout_type` is NOT in that set.
  - `previousAppHistory` = rows whose `payout_type` IS in that set.
- Corrected the stale "retires itself" comment to describe the actual, current (permanent) display
  behavior, with a pointer to `routes/drivers/payouts.py`'s own comment as the source of truth.
- `driver-app/__tests__/app/payoutHistoryScreen.test.tsx`: added fixtures and tests for both newly
  handled types, plus a test confirming all three previous-app types render under one shared
  "Previous app" header (not three separate ones).

## 4. Risk & impact on existing functionality

- **Blast radius:** one screen (`payout-history.tsx`) and its own test file. Grepped the app for
  other consumers of `spinrHistory`/`previousAppHistory` — both are local to this component, not
  exported or shared. No other screen reads `payout_type` this way — checked `earnings.py`
  (backend) and confirmed its own `_is_paid_previous_app_row` helper (used for a *sum*, not
  display grouping) already treats `stripe_sync` and `legacy_outstanding_correction` specially,
  by design excludes `legacy_import` from that particular sum (it's an offset/zeroing row, not
  confirmed-received money) — that backend logic is untouched by this fix and serves a different
  purpose (an earnings total, not a payout-history *list* grouping).
- **No `stripe_sync` behavior change** — that type's rows still land in "Previous app" exactly as
  before; only `legacy_import` and `legacy_outstanding_correction` rows move sections.
- **Reversible, additive-in-spirit change**: no data written, no backend touched, pure client-side
  display grouping.

## 5. User-experience effect

Driver-facing. A driver with a `legacy_import` offset payout or a `legacy_outstanding_correction`
payout in their history will now see those rows under the "Previous app" footer instead of mixed
into their regular Spinr payout list — a more honest display (these were never real new Spinr
payouts issued today), not a change to any dollar amount, status, or date shown.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/driver/payout-history.tsx` | Grouping filter widened from 1 to 3 previous-app `payout_type` values; stale "retires itself" comment corrected. | `legacy_import`/`legacy_outstanding_correction` rows were mis-grouped into the regular Spinr list. |
| `driver-app/__tests__/app/payoutHistoryScreen.test.tsx` | Added fixtures + 3 new tests for the two newly handled types and the shared-header case. | Regression coverage for the fix. |

## 7. Before / after

```tsx
// Before
const spinrHistory = useMemo(
    () => filteredHistory.filter((p: any) => p.payout_type !== 'stripe_sync'),
    [filteredHistory],
);
const previousAppHistory = useMemo(
    () => filteredHistory.filter((p: any) => p.payout_type === 'stripe_sync'),
    [filteredHistory],
);
```

```tsx
// After
const PREVIOUS_APP_PAYOUT_TYPES = new Set(['stripe_sync', 'legacy_import', 'legacy_outstanding_correction']);
// ...
const spinrHistory = useMemo(
    () => filteredHistory.filter((p: any) => !PREVIOUS_APP_PAYOUT_TYPES.has(p.payout_type)),
    [filteredHistory],
);
const previousAppHistory = useMemo(
    () => filteredHistory.filter((p: any) => PREVIOUS_APP_PAYOUT_TYPES.has(p.payout_type)),
    [filteredHistory],
);
```

## 8. Rollback plan

`git revert` is complete and sufficient. No data, schema, or migration component — pure
client-side display grouping.

## 9. Verification performed

- [x] Grepped the entire backend for every `payout_type` value ever written (`auto`, `instant`,
      `stripe_sync`, `legacy_import`, `legacy_outstanding_correction`) — confirmed the fix's set
      is complete, not a guess.
- [x] `yarn jest __tests__/app/payoutHistoryScreen.test.tsx` — 13/13 pass (was 10, +3 new).
- [x] Full driver-app suite: 116/116 suites, 1307/1307 tests — no new failures.
- [x] `npx tsc --noEmit` clean (real build type-check, not just the touched files).
- [x] `npx eslint` clean on both touched files — the initial `react-hooks/exhaustive-deps`
      warning from an inline `Set` was fixed by hoisting the constant to module scope, not
      suppressed.
- [x] Blast-radius check performed (§4) — confirmed no other consumer of the two local variables;
      confirmed `earnings.py`'s similarly-named-but-different-purpose helper is unaffected.

## What was NOT verified

- Not run on a real device/simulator — verified via the existing unit-test harness only,
  consistent with this codebase's standing convention for changes of this size.
- No live/staging data was checked for how many real drivers currently have `legacy_import`
  or `legacy_outstanding_correction` rows mis-grouped in production today — this fix corrects
  the display logic going forward; it does not retroactively notify or re-surface anything to
  a driver who already saw the mis-grouped view.
