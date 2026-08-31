# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude (session on behalf of vikas@ngitservices.com) |
| Surface(s) | rider-app |
| Domain (Sentry tag) | rides (rewards/loyalty is not a distinct listed domain — closest fit is the general rider-facing domain) |
| PR / commit link | (branch `claude/map-vehicle-tracking-animation-3e85y2`, commit `2ae08c9`) |
| Related issue or gap ID | Finding from a design-consistency audit (`spinr-design-consistency-reviewer`) run across rider-app/driver-app/admin-dashboard, 2026-08-31 |

## 1. Issue / gap identified

`rider-app/app/loyalty.tsx`'s data-fetch had a bare `catch {}` around its `GET /loyalty` + `GET /loyalty/history` calls. A failed fetch rendered the identical "No points history yet" copy as a rider who genuinely has zero reward history — no error indication, no retry.

## 2. Root cause

The catch block was written to prevent a crash but never populated any failure-tracking state, unlike the near-identical `notifications.tsx` screen in the same app, which already solved this exact problem with a `loadFailed` flag and a distinct error+retry empty state.

## 3. Fix / remediation

Added a `loadFailed` boolean, set on catch (and cleared on success), logged via `console.error` instead of silently swallowed. `ListEmptyComponent` now branches three ways instead of two: loading → spinner (unchanged), `loadFailed` → new "Couldn't load your rewards" state with a retry button, otherwise → the existing "No points history yet" copy. Directly mirrors `notifications.tsx`'s existing pattern rather than inventing a new one.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Only `loyalty.tsx` and its own test file changed. `loadData`'s success path, the `Promise.all` parallel fetch, the tier-card/progress-bar rendering, and pull-to-refresh are all unchanged — grepped for other importers of anything touched here; none exist (this screen owns its own state, no shared hook extracted).
- No backend, schema, or API contract change — purely a client-side rendering branch on an already-fetched response's success/failure.
- No money/wallet/ride-state path touched; loyalty points display is read-only here (no redemption action on this screen per the header comment "the redeem action is gone").

## 5. User-experience effect

- **Rider-facing.** Previously: a network blip or backend error on this screen silently looked identical to having zero reward history. Now: the rider sees "Couldn't load your rewards / Check your connection and try again" with a retry button, and can actually recover instead of believing they have no points.
- Visible only when this specific fetch fails — not a change to any success-path rendering, so nothing changes for the common case.
- Not mid-session-disruptive: this is a standalone screen entered via navigation, not a background state a rider is already relying on mid-ride.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app/loyalty.tsx` | Added `loadFailed` state; distinct error+retry `ListEmptyComponent` branch; added `retryBtn`/`retryText` styles | Fix the silent-swallow gap |
| `rider-app/__tests__/loyaltyScreen.test.tsx` | Replaced the test asserting the old swallow-and-show-empty-copy behavior with two tests (distinct error copy on failure; retry re-fetches and recovers); added `danger` to the mocked theme colors | Test coverage for the new behavior |

## 7. Before / after

```tsx
// Before
} catch {}
// ...
ListEmptyComponent={
  !loading ? (
    <View style={styles.empty}>
      <Text style={styles.emptyTitle}>No points history yet</Text>
      ...
    </View>
  ) : null
}
```
```tsx
// After
} catch (err) {
  console.error('[loyalty]', err);
  setLoadFailed(true);
}
// ...
ListEmptyComponent={
  loading ? null : loadFailed ? (
    <View style={styles.empty}>
      <Text style={styles.emptyTitle}>Couldn't load your rewards</Text>
      <TouchableOpacity style={styles.retryBtn} onPress={() => loadData()}>
        <Text style={styles.retryText}>Retry</Text>
      </TouchableOpacity>
    </View>
  ) : (
    <View style={styles.empty}>
      <Text style={styles.emptyTitle}>No points history yet</Text>
      ...
    </View>
  )
}
```

## 8. Rollback plan

`git revert` of commit `2ae08c9` is a complete rollback — client-side only, no data touched, no migration.

## 9. Verification performed

- [x] Automated tests: full rider-app suite **138 suites / 1937 tests, all passing**. `loyaltyScreen.test.tsx` specifically: 16/16 (14 pre-existing + 2 new/replaced for the failure/retry paths).
- [x] `npx tsc --noEmit` clean.
- [x] `npx eslint` clean (0 errors, 0 warnings) on both changed files.
- [x] Blast-radius grep: confirmed no other file imports from or depends on `loyalty.tsx`'s internal state.
- [ ] Manual repro on staging/device — not performed; no device/emulator in this environment.
- [x] Reviewed against CLAUDE.md's "Do not silently swallow errors" convention — this fix directly implements that rule on a screen that was violating it.

## 10. What was NOT verified

- No on-device/visual verification of the new error state's appearance — neither app has visual-regression tooling (standing gap, `ACTION_ITEMS.md`).
- The other findings from the same design-consistency audit pass (hardcoded colors, reduce-motion gaps across rider-app/driver-app, admin-dashboard's audit-logs silent-swallow, status-badge token migration #2816) were **not** addressed in this change — this fix is scoped to the one blocker-severity finding; the rest were left as tracked follow-ups per the user's own choice not to bundle them into this pass.
