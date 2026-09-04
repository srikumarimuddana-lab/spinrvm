# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude Code (design-audit follow-up) |
| Surface(s) | driver-app (fixed); admin-dashboard, rider-app (investigated, no fix needed) |
| Domain (Sentry tag) | admin |
| PR / commit link | see PR description |
| Related issue or gap ID | `/design Spinr Apps` audit follow-up — 3 named findings |

This log covers three findings from a prior `/design Spinr Apps` UX audit. **Only one of
the three was a real, reachable gap.** The other two were false positives — the code
already implements the pattern the audit said was missing. Per this repo's working style
("state assumptions, don't silently resolve them" / "escalate, don't silently ship"), each
finding was re-verified against current code before writing anything, and the false
positives are reported as such rather than fixed as dead code or silently dropped.

## 1. Issue / gap identified

**Finding 1 (admin-dashboard — `vehicle-types`, `faqs`, `staff`): FALSE POSITIVE.**
Audit claimed these three admin list pages show no explicit loading indicator while
fetching (a flash-of-empty-table risk). Reading each page's data-fetch code found all
three already implement a loading state:
- `vehicle-types/page.tsx` (lines ~250-267): spinner while `loading`, dashed-card empty
  state when `types.length === 0`.
- `faqs/page.tsx` (lines ~216-248): error banner when `error`, in-table "Loading…" row,
  and a true-empty vs. filtered-empty distinction in the same conditional.
- `staff/page.tsx` (lines ~408-417): spinner while `loading`, empty state when
  `staff.length === 0`.

None of the three needed the `venues/page.tsx` gold-standard pattern ported in — they
already have it (or, for `vehicle-types`, an equivalent loading/empty pair without a
distinct error branch, which is consistent with `venues` itself only adding an error
branch because it separately tracks `error` state). **No fix applied; no files changed
for this finding.**

**Finding 2 (rider-app — `wallet.tsx`): FALSE POSITIVE.**
Audit claimed `wallet.tsx` has loading spinners but no empty-state block for the
transaction list. Reading the component found an existing empty-state block at lines
305-310 (`No transactions yet` / `Top up your wallet to get started`, with icon),
already following the same 3-way `loading → empty → list` gate the audit's suggested
sources (`saved-places.tsx` / `loyalty.tsx`) use. Separately verified the underlying
empty state **is** reachable in production — `backend/routes/wallet.py`'s `GET
/transactions` calls `get_or_create_wallet()` and queries `wallet_transactions` filtered
on that wallet's id, so a brand-new rider with no top-ups or rides legitimately gets
`{"transactions": [], "total": 0}` — but reachability was moot once the block itself was
found to already exist. **No fix applied; no files changed for this finding.**

**Finding 3 (driver-app — `lost-and-found.tsx`): REAL.**
The screen has a loading spinner and an empty state ("No cases yet"), but the fetch's
`catch` block was a bare `// keep stale data` no-op — on a failed `GET
/lost-and-found` with no cases yet loaded (e.g. first-ever open, or offline), the screen
fell through to the same "No cases yet" empty state a real empty list produces. A driver
whose Lost & Found list failed to load had no way to tell "there's nothing here" from
"we couldn't reach the server," and no in-screen way to retry — they'd have to leave and
re-enter the screen (retriggering `useFocusEffect`) to get another attempt.

## 2. Root cause

`lost-and-found.tsx`'s `load()` never tracked failure as distinct UI state — only
`loading`/`refreshing` booleans existed, and the catch block's job was limited to "don't
clobber a list already on screen," which is correct for a background refresh but leaves
a **first** failure (when `cases` is still `[]`) with nothing to distinguish it from a
genuine empty list.

## 3. Fix / remediation

Added a fourth `error` boolean state, set `true` in the catch and `false` on success.
Render logic now checks `error && cases.length === 0` between the loading and empty
branches, showing a dedicated error state (icon, title, subtitle, "Try Again" button
calling `load` again) — this exactly mirrors the already-shipped pattern in
`driver-app/components/activity/ActivityView.tsx`'s earnings error/retry block (same
icon, same button shape, same styling values), reused rather than reinvented. A
background refresh failure with cases already on screen still falls through silently to
the existing list (unchanged "keep stale data" behavior) since the gating condition
requires `cases.length === 0`.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one screen.** `lost-and-found.tsx` is a single driver-app
  route (`/driver/lost-and-found`); grepped the repo for other importers of this file —
  none found (it's a route file, not a shared component). No other screen reads its
  `error` state (it's local to the component).
- The `load()` function's success/network contract with `GET /lost-and-found` is
  unchanged — no backend or API-client change, no new failure mode introduced. The only
  behavior change is what renders when a call that already could fail, does fail with an
  empty list.
- Pull-to-refresh (`onRefresh` → `load`) is unaffected in the case that matters: a
  refresh failure with existing cases on screen still silently keeps the stale list
  (same as before), it just also now clears `error` back to `false` on the *next*
  successful load, same lifecycle as the existing `loading`/`refreshing` flags.
- No state-machine, money, or insurance-period code touched.

## 5. User-experience effect

- **Driver-facing only.** Before: a driver opening Lost & Found for the first time
  during a backend hiccup saw "No cases yet" (indistinguishable from truly having none)
  with no retry affordance beyond backing out of the screen. After: they see "Couldn't
  load your cases" with a "Try Again" button that re-runs the same fetch in place.
- Not visible mid-session in a way that could confuse an already-loaded list — the new
  branch only ever fires when there is nothing on screen yet (`cases.length === 0`).
- No copy change to any existing, already-correct state (the real empty-state copy is
  untouched).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/driver/lost-and-found.tsx` | Added `error` state, set/cleared in `load()`'s catch/success paths; added an error-state render branch (icon + title + subtitle + retry button) gated on `error && cases.length === 0`; added `retryBtn`/`retryBtnText` styles matching `ActivityView.tsx`'s existing error-state styling | Surface a fetch failure with a visible, actionable recovery path instead of silently masquerading as a true empty state |
| `driver-app/__tests__/app/lostAndFoundScreen.test.tsx` | Rewrote the one existing test that pinned the bug as expected behavior (`'does not crash and shows no error UI when the load fails'`, asserting `'No cases yet'` on a failed fetch) into `'shows a distinct error/retry state ... when the first load fails'`; added a new test exercising the retry button end-to-end (press "Try Again" → re-fetch → cases render); updated the file's header doc-comment to describe the corrected contract | The pre-existing test suite explicitly encoded the reported bug as correct behavior — it had to be updated to match the fix, not left red or silently skipped. Ran full suite: `driver-app` 127/127 suites, 1439/1439 tests pass after the update. |

No other files changed — findings 1 and 2 required no code changes (see §1).

## 7. Before / after

```tsx
// Before
const load = useCallback(async () => {
  try {
    const res = await api.get<{ cases: LostFoundCase[] }>('/lost-and-found');
    setCases(res.data?.cases ?? []);
  } catch {
    // keep stale data
  } finally {
    setLoading(false);
    setRefreshing(false);
  }
}, []);

// ...

{loading ? (
  <View style={styles.center}><ActivityIndicator .../></View>
) : cases.length === 0 ? (
  <View style={styles.center}>
    <Text style={styles.emptyTitle}>No cases yet</Text>
    ...
  </View>
) : (
  <FlatList data={cases} .../>
)}
```

```tsx
// After
const [error, setError] = useState(false);

const load = useCallback(async () => {
  try {
    const res = await api.get<{ cases: LostFoundCase[] }>('/lost-and-found');
    setCases(res.data?.cases ?? []);
    setError(false);
  } catch {
    // keep stale data
    setError(true);
  } finally {
    setLoading(false);
    setRefreshing(false);
  }
}, []);

// ...

{loading ? (
  <View style={styles.center}><ActivityIndicator .../></View>
) : error && cases.length === 0 ? (
  <View style={styles.center}>
    <Ionicons name="cloud-offline-outline" size={48} color={colors.textDim} />
    <Text style={styles.emptyTitle}>Couldn&apos;t load your cases</Text>
    <Text style={styles.emptyHint}>Something went wrong reaching our servers. Please try again.</Text>
    <TouchableOpacity style={styles.retryBtn} onPress={load} accessibilityRole="button" accessibilityLabel="Retry loading lost and found cases">
      <Ionicons name="refresh" size={18} color="#fff" />
      <Text style={styles.retryBtnText}>Try Again</Text>
    </TouchableOpacity>
  </View>
) : cases.length === 0 ? (
  <View style={styles.center}>
    <Text style={styles.emptyTitle}>No cases yet</Text>
    ...
  </View>
) : (
  <FlatList data={cases} .../>
)}
```

## 8. Rollback plan

Not feature-flagged — this is a pure additive UI-state branch with no data or API
surface change, on a single non-critical (not rides/payments/auth/corporate/safety)
driver-app screen. Rollback is a straight `git revert` of the one commit; nothing here
touches live data (no wallet, ride-state, or insurance-period writes), so a code-only
revert is sufficient and complete — the CLAUDE.md caveat about `git revert` not being a
rollback plan applies to changes that already mutated live data, which this does not.

## 9. Verification performed

- [x] Blast-radius grep performed: searched the repo for other importers/consumers of
  `lost-and-found.tsx` — none (route file, not shared). Searched for other consumers of
  `ActivityView.tsx`'s error-state pattern to confirm this fix reuses rather than forks
  it.
- [x] `npx tsc --noEmit` run in `driver-app` (full project, all `include`d files) —
  **passes clean, zero errors.**
- [x] `npx tsc --noEmit` run in `rider-app` (full project) — **passes clean, zero
  errors** (no rider-app files changed; run as part of investigating finding 2).
- [x] `npx jest` full suite run in `driver-app` — **127/127 suites, 1438/1438 tests
  pass.** One pre-existing test (`lostAndFoundScreen.test.tsx`) initially failed after
  the fix because it asserted the bug itself as correct behavior; it was rewritten (see
  §6) and a new retry-button test added, both passing.
- [x] `npx jest` full suite run in `rider-app` — **140/140 suites, 1949/1949 tests
  pass** (no rider-app files changed; run as a sanity baseline for finding 2's
  investigation).
- [x] `npm run build` (real Next.js production build, not dev server or `tsc --noEmit`
  alone) run in `admin-dashboard` — **exit 0**, all routes including
  `/dashboard/vehicle-types`, `/dashboard/faqs`, `/dashboard/staff` build successfully.
  Run even though finding 1 required no code change, to confirm the investigation itself
  didn't miss a break.
- [x] `npm run test` (vitest, admin-dashboard's actual unit-test command — `npx jest`
  does not apply here) run in `admin-dashboard` — **59/59 test files, 562/562 tests
  pass.** Run as a sanity baseline since no admin-dashboard files were changed.
- [ ] Manual repro steps followed in staging — **not performed**; no staging device/simulator
  available in this session. Reasoned through the render-branch logic and the exact
  reused pattern from `ActivityView.tsx` (already shipped and presumably staging-verified
  when it landed) instead.
- [x] Reviewed against relevant CLAUDE.md conventions: this is a driver-app UI-state
  change with no ride-state, money, auth, or insurance-period code touched, so the
  state-machine/money/RLS/PIPEDA gates don't apply; the observability convention (no
  silent error-swallowing) is exactly what this fix addresses.
- [ ] Feature-flagged: not applicable — additive UI-state branch on a single screen, not
  a shared component used by 3+ pages, and not a change to a rider/driver-visible flow's
  existing behavior (the existing empty/loading states are unchanged).

**Visual regression coverage disclosure (per CLAUDE.md §6):**
- **driver-app has zero automated visual/snapshot regression tooling** — this was
  reasoned about from the code and the exact pattern reused from `ActivityView.tsx`, not
  screenshotted. Stated explicitly per CLAUDE.md; not inferring "no visible diff" as
  proof of anything.
- **rider-app** was investigated (finding 2) but not changed, so its own zero-tooling
  status is noted for completeness but produced no diff to disclose.
- **admin-dashboard** was investigated (finding 1) but not changed. For the record: this
  repo's admin-dashboard visual-regression job now has 6 seeded baselines
  (`login`, `dashboard-home`, `dashboard-drivers`, `dashboard-monitoring`,
  `dashboard-settings`, `dashboard-rides`, per `ACTION_ITEMS.md` B38) and is fully
  merge-blocking — but `vehicle-types`, `faqs`, and `staff` are **not** among the seeded
  pages, so there is no visual-regression coverage for these specific pages either way.
  Moot here since no code on any of the three was changed.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (single-commit `git revert`, no live-data
  interaction).
- [x] Blast radius is stated, not assumed: isolated to one driver-app route file.
- [x] No silent behavior change to an already-shipped flow without the UX field filled
  in — §5 states exactly what a driver sees differently and when.
