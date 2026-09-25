# Change Impact & Risk Log — driver "Upcoming trips" screen layout

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (session on `claude/focused-lamport-gu2je5`) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/focused-lamport-gu2je5` |
| Related issue or gap ID | User report: "in driver app there is some issue with the upcoming rides, it's not showing good" |

## 1. Issue / gap identified

The driver-app **Upcoming trips** screen (`Profile → Upcoming trips`) rendered its title under the status bar/notch, had no back button, showed the error message **and** "No upcoming scheduled trips." at the same time when loading failed, and printed the pickup time as one cramped `2026-09-25, 3:30:00 p.m.` string.

## 2. Root cause

`driver-app/app/driver/_layout.tsx` sets `headerShown: false` for the whole driver Stack, so every detail screen must draw its own safe-area-aware header. `upcoming.tsx` was a minimal stub that never did: a plain `View` with `padding: 16` and a `Text` title — no top inset, no back control. The FlatList's `ListEmptyComponent` rendered whenever `rides` was empty, including after a failed load, so it stacked under the error text. The time used `toLocaleString('en-CA')` with no options (includes seconds).

## 3. Fix / remediation

- Use the shared `ScreenHeader` (owns the top safe-area inset + back button); list adds `insets.bottom` padding like its siblings, with the same `ScreenHeader` + sibling `FlatList` shape `lost-and-found.tsx` already uses.
- Error and empty are now mutually exclusive; the error state has a "Try Again" button (filled, 44pt min height).
- Pull-to-refresh via the existing `SafeRefreshControl`.
- Cards: date and time (no seconds) on one row, pickup/drop-off on separate rows with a coloured dot, theme `surface` background.

No API or data change — same `GET /drivers/rides/upcoming` call, same response fields.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated, single screen.** `grep -rn "driver/upcoming"` → only caller is `driver-app/app/driver/(tabs)/profile.tsx:711` (`router.push('/driver/upcoming')`). The backend endpoint (`backend/routes/drivers/ride_reads.py` `get_upcoming_scheduled_rides`) is untouched.
- `ScreenHeader` and `SafeRefreshControl` are consumed, not modified — no effect on their other importers (faq, settings, lost-and-found, subscription, profile, documents, vehicle-info).
- No ride state, dispatch, insurance-period, or money interaction.
- `docs/known-forks.md` has no entry for this screen; rider-app has no sibling copy.

## 5. User-experience effect

- **Driver only.** The screen now has the standard red gradient header with a back arrow, like other Profile sub-screens.
- Visible on next app update (JS bundle); no mid-session effect beyond the screen looking different the next time it's opened.
- Copy: adds a "Try Again" retry button (same filled shape as Lost & Found); existing strings unchanged.
- Not feature-flagged: this is a layout bug fix on a single, previously broken screen with one entry point, using the app's existing standard header — not new behaviour.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/driver/upcoming.tsx` | ScreenHeader, error/empty split + retry, pull-to-refresh, restyled cards | Fix layout under status bar, missing back button, overlapping states |
| `driver-app/__tests__/app/upcomingRidesScreen.test.tsx` | New test | Pins header, list rendering, empty-only, error-only + retry |
| `docs/change-log/2026-09-25-driver-upcoming-trips-layout.md` | This entry | Change Impact log policy |

## 7. Before / after

```tsx
// Before
<View style={[styles.wrap, { backgroundColor: colors.background }]}>   // padding: 16, no top inset
  <Text style={styles.title}>Upcoming trips</Text>                    // no back button
  {error ? <Text style={{ color: colors.error }}>{error}</Text> : null}
  <FlatList data={rides} ListEmptyComponent={<Text>No upcoming scheduled trips.</Text>} … />
```

```tsx
// After
<View style={[styles.wrap, { backgroundColor: colors.background }]}>
  <ScreenHeader title="Upcoming trips" />                             // safe area + back
  <FlatList data={error ? [] : rides} ListEmptyComponent={renderEmpty} // error XOR empty, retry
            refreshControl={<SafeRefreshControl … />} … />
```

## 8. Rollback plan

Pure client-side presentational change with no data writes and no flag mechanism on this screen — rollback is reverting the commit and shipping the next JS bundle. Acceptable because the change is isolated to one screen and cannot corrupt data.

## 9. Verification performed

- [ ] Automated tests run — **NOT run.** `driver-app/__tests__/app/upcomingRidesScreen.test.tsx` was written but could not be executed: this session's egress policy blocks `registry.npmjs.org`/`registry.yarnpkg.com`, so `node_modules` could not be installed (no jest, no `tsc` against RN types). Must be run in CI / locally before merge.
- [ ] Manual repro in staging — not done.
- [x] Blast-radius grep performed (`driver/upcoming`, `rides/upcoming`, `ScreenHeader`, `docs/known-forks.md`).
- [x] Reviewed against CLAUDE.md conventions (no state machine / money / PIPEDA impact; theme tokens from `shared/theme`).
- [x] Feature-flag decision justified in §5.

## What was NOT verified

- No production build (`npm run build` / EAS) was run, and no dev server either — deps could not be installed.
- driver-app has **no visual-regression tooling**; the layout was reasoned about against `ScreenHeader.tsx`'s documented usage and the identical `lost-and-found.tsx` layout, not screenshotted on a device.
- Not checked whether any rides are missing from the list (data correctness) — the backend endpoint was read but not changed; only the rendering was fixed.
