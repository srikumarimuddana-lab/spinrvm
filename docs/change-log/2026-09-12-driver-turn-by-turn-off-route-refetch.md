# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Author | Claude Code |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (branch `claude/vehicle-icon-movement-animation-8tys8o`) |
| Related issue or gap ID | PR D of Phase 1, `docs/proposals/2026-09-01-driver-in-app-turn-by-turn-navigation.md` §7.3 — first-party in-app turn-by-turn navigation. |

## 1. Issue / gap identified

After PR C, the turn-by-turn step list is fetched once per ride leg and cached server-side for 30 minutes (PR A). If a driver genuinely leaves the planned route mid-leg, the cached step list still describes turns for the route they're no longer on, and nothing refetches it until the cache TTL expires.

## 2. Root cause

Not a regression — a gap left open by design in PR A/C, which deliberately scoped Phase 1 to "turn-list + camera reaction only, no live re-route" (per the `AskUserQuestion` decision recorded in the proposal §7.2). This PR closes the one piece of that gap Phase 1 was always meant to cover: invalidating a now-wrong cached step list on a genuine deviation, not adding live re-routing itself.

## 3. Fix / remediation

- **`driver-app/app/driver/(tabs)/index.tsx`**: added `refreshNavigationStepsOnDeviation(rid)`, a small `useCallback` that calls `GET /rides/{id}/navigation-steps?force_refresh=true` (PR A's existing bypass-the-cache query param) and replaces `navSteps`/resets the step-index continuity ref on success. Wired into the **existing** route-deviation-detection effect (`OFF_ROUTE_M`/`offRouteStreakRef`, previously toast-only) — when a genuine deviation trips the toast, this now also fires, reusing that same 60-second cooldown gate rather than introducing a second debounce mechanism for the same trigger event.
- Deliberately does **not** clear `navSteps` on a failed refresh (unlike the initial per-leg fetch, which does clear on failure) — a transient network hiccup during a recovery attempt shouldn't blank out a still-possibly-useful instruction the driver is already looking at. Documented inline at the callback's definition.

## 4. Risk & impact on existing functionality

- **Blast radius**: single-surface, and the only existing behavior touched is the route-deviation-detection effect's dependency array and one new branch inside its existing `if` block — the pre-existing toast fires exactly as before, unconditionally alongside the new call, not replaced by it (per the proposal's own "instead of (or in addition to) the toast" wording — chose "in addition to," the toast stays because it's independently useful driver-facing feedback regardless of the turn-by-turn feature).
- **No new call volume risk beyond what PR A's endpoint already bounds**: this reuses the exact same 60s cooldown the toast already enforces, so at most one extra Directions-budget-gated call per minute of sustained deviation — same shape as any other call to this endpoint (budget-gated, and a no-op response while the flag is off).
- **Grepped**: `refreshNavigationStepsOnDeviation` and the `force_refresh` query param have no other callers/consumers — isolated to this one wiring point.

## 5. User-experience effect

**None today** — `driver_turn_by_turn_enabled` is off by default, so this call always returns `{"steps": [], "destination": null}` regardless of whether it fires. Once enabled: a driver who genuinely leaves the route gets an updated (rather than stale) turn banner after the same off-route toast already shows today — no new UI surface, no new copy.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/driver/(tabs)/index.tsx` | Added `refreshNavigationStepsOnDeviation`; wired it into the existing off-route-detection effect (added `activeRide?.ride?.id` and the new callback to that effect's dependency array) | Closes the "stale cached steps after a real deviation" gap Phase 1 always intended to cover |

## 7. Before / after

```tsx
// Before — deviation only ever showed a toast.
offRouteStreakRef.current += 1;
if (offRouteStreakRef.current >= 3 && Date.now() - offRouteToastMsRef.current > 60_000) {
  offRouteToastMsRef.current = Date.now();
  showToast('info', 'Off Route', 'You have left the planned route.');
}
```

```tsx
// After — same toast, plus a bounded force-refresh of the cached step list.
offRouteStreakRef.current += 1;
if (offRouteStreakRef.current >= 3 && Date.now() - offRouteToastMsRef.current > 60_000) {
  offRouteToastMsRef.current = Date.now();
  showToast('info', 'Off Route', 'You have left the planned route.');
  const rid = activeRide?.ride?.id;
  if (rid) void refreshNavigationStepsOnDeviation(rid);
}
```

## 8. Rollback plan

`git-revert-safe` — the new call is a no-op while `driver_turn_by_turn_enabled` is off (its default); reverting this commit simply removes the extra fetch call and returns the effect to toast-only, with zero other code depending on the new callback.

## 9. Verification performed

- [x] `npx tsc --noEmit` — clean.
- [x] Full driver-app suite re-run: `yarn test --ci --forceExit` — 147 suites / 1662 tests, all passing (no regression in the touched effect's existing behavior).
- [x] Blast-radius grep performed: `refreshNavigationStepsOnDeviation` and `force_refresh` have exactly the one call site each described above.

**What was NOT verified:** there is no unit test for this specific wiring, or for the route-deviation-detection effect it extends. The pre-existing off-route-toast logic this PR builds on already had zero test coverage before this change — this repo's `index.tsx` (the driver map screen) is a large, mostly-untested "god file" with no per-effect test harness, and building one for this single small addition would be disproportionate scope creep for a one-file, 32-line, fully-gated wiring change. Real-device behavior (does a driver who actually drives off-route get a materially different, correct banner after the refetch) was not observed — no physical device or simulator in this environment; reasoned about from PR A's already-tested endpoint contract and PR C's already-tested step-tracking/banner behavior, not screenshotted. driver-app has no automated visual-regression tooling at all (per CLAUDE.md), so this disclosure is mandatory.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (flag off by default; new call is provably a no-op until it flips)
- [x] Blast radius is stated, not assumed (grepped both new identifiers; full regression suite green)
- [x] No silent behavior change to an already-shipped flow (§5: zero visible effect while the flag is off; the pre-existing toast is unchanged, only additive behavior sits alongside it)
