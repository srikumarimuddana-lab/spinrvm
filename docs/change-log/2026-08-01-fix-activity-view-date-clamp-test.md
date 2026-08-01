# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-01 |
| Author | Claude Code |
| Surface(s) | driver-app (test-only) |
| Domain (Sentry tag) | drivers |
| PR / commit link | (this branch: `claude/fix-driver-app-activity-date-test`) |
| Related issue or gap ID | none — found via a CI audit today, no filed issue |

## 1. Issue / gap identified

`driver-app/__tests__/components/ActivityView.test.tsx` — `'shows load more for a month when period stats say more rides exist'` builds its fixture ride date via `thisMonth.setDate(Math.max(1, thisMonth.getDate() - 10))`. On the 1st of any calendar month this collapses the fixture date to today's own date, so the test's first assertion (`waitFor(() => expect(getByText('No Rides Found')).toBeTruthy())`, expecting the ride to be excluded by the default `period='today'` filter) fails — the fixture ride wrongly renders instead. This is the same class of bug as the backend fix in `239c7048` (#2981): a calendar-date-dependent test fixture that only breaks on certain days of the month.

## 2. Root cause

`ActivityView` (`driver-app/components/activity/ActivityView.tsx`) defaults to `period='today'` (line 46) and filters `rideHistory` client-side with an exact-day match — `date.getDate() !== today.getDate() || date.getMonth() !== today.getMonth() || date.getFullYear() !== today.getFullYear()` (lines 148–149) — for that period.

The test fixture computed "10 days ago, same month" as `today.getDate() - 10`, clamped with `Math.max(1, ...)` to avoid running off the start of the month. For `today.getDate()` in `1..10`, `getDate() - 10 <= 0`, so the clamp always produces day **1** of the current month, regardless of the actual subtraction. That value only numerically equals `today.getDate()` when today itself is the 1st — verified by simulation across all 365 days of 2026 (`node` script run locally): the old logic collides with "today" on exactly 12 days/year, one per month (the 1st of each month), while never accidentally recreating a real "~10 days ago" offset on the 2nd–10th either (it always lands on day 1, not day 1-of-original-day-minus-10). So the fixture was silently wrong for the whole 1st–10th window and outright test-breaking specifically on the 1st.

## 3. Fix / remediation

Replaced the `today.getDate() - 10` + clamp approach with an anchor-to-start-of-month construction: build the fixture date at day 1 of the current month, then nudge forward exactly one day only in the single case where day 1 would collide with `today.getDate()` (i.e., when today itself is the 1st). This guarantees, for every day of every month: (a) the fixture stays in the current calendar month (required by the later `'This Month'` pill assertion), and (b) the fixture's day-of-month is never equal to today's, so the default `'today'` period always excludes it. No production code changed — `ActivityView.tsx` is untouched.

Searched the rest of this test file and every other driver-app test file for the same `Math.max(1, ...getDate() - N)` pattern (`grep -rn "Math\.max(1,.*getDate() -"` across the whole repo): no other instances found — this was the only occurrence.

## 4. Risk & impact on existing functionality

- Blast radius: **isolated** — single test file (`ActivityView.test.tsx`), single `it()` block's fixture-date construction. No production code, no shared test-utils, no other test file references this fixture.
- `ActivityView.tsx` itself, `driverStore`, and every other consumer of the store mock in this file are unchanged; the other 5 tests in the same `describe` block use their own independent fixture dates and were re-run unmodified.
- Grepped for other importers of `ActivityView` and for any shared "freeze time" / date-fixture test-utils convention in the repo: none exist for this component; no sibling file relies on this test's date-construction helper (it's inline, not exported).
- No interaction with money, ride state, background loops, or any live-tested backend behavior — this is a driver-app RN-Testing-Library unit test's mock data only.

## 5. User-experience effect

None — test-only change, no production code path touched, nothing observable to a driver, rider, corporate admin, or internal admin, mid-session or otherwise.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/__tests__/components/ActivityView.test.tsx` | Replaced `thisMonth.setDate(Math.max(1, thisMonth.getDate() - 10))` with a start-of-month anchor (`new Date(year, month, 1, …)`) that nudges forward one day only when day 1 would collide with today's actual date | Old construction clamped to day 1 for `today.getDate()` in `1..10`, which equals `today.getDate()` exactly when today is the 1st, collapsing the "not today" fixture back onto today and failing the test's `No Rides Found` assertion on the 1st of any month |
| `docs/change-log/2026-08-01-fix-activity-view-date-clamp-test.md` | New change-log entry | Required by CLAUDE.md for any commit closing a gap on a live-tested surface's test suite |

## 7. Before / after

```js
// Before
const thisMonth = new Date();
thisMonth.setDate(Math.max(1, thisMonth.getDate() - 10));
```

```js
// After
const today = new Date();
const thisMonth = new Date(
  today.getFullYear(),
  today.getMonth(),
  1,
  today.getHours(),
  today.getMinutes(),
  today.getSeconds()
);
if (thisMonth.getDate() === today.getDate()) {
  thisMonth.setDate(thisMonth.getDate() + 1);
}
```

## 8. Rollback plan

`git revert` — pure test-only change, no live data, no migration, no feature flag involved.

## 9. Verification performed

- [x] Automated tests run: `npx jest __tests__/components/ActivityView.test.tsx` — 6/6 passed (all tests in the file, not just the fixed one)
- [x] Full driver-app suite run: `npx jest` — 45/45 test suites, 343/343 tests passed, no regressions
- [x] Blast-radius grep performed: `Math\.max\(1,\s*\w+\.getDate\(\)\s*-` across the entire repo (not just driver-app) — only the one instance fixed here, no sibling occurrences
- [x] Reasoned through day-of-month coverage explicitly (not just today's actual run date, 2026-08-01): wrote and ran a standalone Node script simulating both the old and new logic for every day of every month in 2026 (365 days). Old logic collides with "today" on exactly the 1st of each of the 12 months (12/365 days); new logic collides on 0/365 days and stays in the current month on all 365. Also printed the day-by-day trace for August 2026 days 1–10 specifically (the exact window named in the bug report): none collide, all stay in-month.
- [ ] Manual repro in staging — n/a, this is a Jest unit test with mocked `driverStore`, not a live app flow
- [ ] `npm run build` / production build — n/a, test-only change with no production bundle impact; explicitly not run (see below)

## 10. What was NOT verified

- No production build (`expo export` / EAS build equivalent) was run — this change touches only a test file, not any file included in the app bundle, so a build was judged unnecessary. Stated explicitly per CLAUDE.md rather than left implied.
- Not verified against real device/simulator rendering — relies on `@testing-library/react-native` + jsdom-style rendering only, consistent with this repo's existing test tier for this file.
- Did not attempt to actually roll the system clock to the 1st–10th of a month and re-run under real wall-clock time (no time-mocking harness like `MockDate`/`jest.useFakeTimers` was introduced for this fixture, matching the file's existing convention of plain `new Date()` fixtures with no freeze-time pattern). Coverage of the 1st–10th window instead comes from the standalone day-by-day simulation described above, which exercises the identical date-arithmetic logic outside the test runner.
