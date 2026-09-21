# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code |
| Surface(s) | rider-app, driver-app, shared |
| Domain (Sentry tag) | drivers, rides |
| PR / commit link | (branch `claude/notifications-tap-jitter-and-detail-view`) |
| Related issue or gap ID | Live-testing report: tapping a notification jitters the screen and, for most notification types, does nothing |

## 1. Issue / gap identified

Two defects reported live from the driver-app Notifications inbox, confirmed to also exist (in a related form) in rider-app's own inbox:

1. Tapping any notification row visibly "jittered" the screen — content shifted down then snapped back over ~1s.
2. For most notification `type`s, tapping did nothing beyond marking the row read: no navigation, no way to read the message past its 2-line-truncated body. On driver-app this affects `auto_offline`, `quota_exhausted`, `ride_cancelled`, `ride_noshow`, `safety`, `general`, `system` — i.e. exactly the two types shown in the reporting screenshot ("You're now offline", "Ride Cancelled"). On rider-app the same gap exists for any unmapped `type`, plus for `chat_message`/`ride_completed`/`driver_accepted`/`driver_arrived` when their required `ride_id` is absent (pre-existing, separately pinned by that screen's own tests).

## 2. Root cause

1. **Jitter**: `useMarkNotificationRead`'s mutation (`shared/hooks/queries/notificationQueries.ts`) called `invalidateQueries` on success/error despite its own comment claiming to be "optimistic." Since the inbox screen's `useNotifications(50)` is an actively-mounted query, invalidation triggers an immediate refetch, flipping `isFetching` true→false. The driver-app screen binds `isFetching` directly to its pull-to-refresh `SafeRefreshControl`, so every tap visibly flashed the refresh spinner in and back out — not a real optimistic update, just a fast round-trip disguised as one.
2. **No detail view**: `handleNotificationPress` in both apps was built to *navigate* for a specific list of actionable `type`s only. Nothing was ever added for the (larger) set of purely informational types, or as a fallback when a required id is missing — the tap simply had no `else` branch, so it silently did nothing beyond the mark-as-read call.

## 3. Fix / remediation

1. `useMarkNotificationRead` and `useMarkAllNotificationsRead` now do a true optimistic update: `onMutate` writes `is_read`/`unread_count` straight into the cached list before the request resolves, `onError` rolls back from a snapshot, and `onSettled` reconciles with `invalidateQueries({ refetchType: 'none' })` — marks the cache stale without forcing an immediate, visible refetch. The next natural refetch (screen focus, explicit pull-to-refresh, `staleTime` elapse) still picks up the true server state.
2. Both `driver-app/app/driver/notifications.tsx` and `rider-app/app/notifications.tsx` now show a fallback detail Modal (icon, title, relative time, full untruncated body, Close button) whenever a tap doesn't resolve to a navigation destination, instead of doing nothing. Existing navigation behavior for every already-handled `type`/id combination is unchanged — the fallback only fires on the same "no case matched" path that previously did nothing.
3. Added a `docs/known-forks.md` entry for the two `notifications.tsx` screens — this is the second time a fix to one has had to be hand-ported to the other (first: 2026-08-18), so it's now a registered, pre-commit-hook-flagged pair rather than an implicit expectation.

## 4. Risk & impact on existing functionality

- **Blast radius, `useMarkNotificationRead`/`useMarkAllNotificationsRead`**: grepped for all consumers — `driver-app/app/driver/notifications.tsx` and its two test files only (confirmed both by grep and by the 2026-08-18 change log's own note that rider-app doesn't use this hook). Single-surface.
- **Blast radius, the two screen files**: each is pushed from exactly one place (driver dashboard bell icon; rider equivalent) per the 2026-09-11 change log's own confirmation for driver-app; no other screen imports either file.
- **Could this regress a working flow?** The optimistic update changes *when* `is_read` visually flips (immediately vs. after a round trip) but not the end state, and rollback-on-error is preserved. The `refetchType: 'none'` settle means a failed background reconciliation is silent until the next natural refetch — acceptable since `onError` already rolls back the specific mutation that failed; this only affects reconciling with *other* concurrent changes (e.g. another device marking the same notification read), which was already eventually-consistent before.
- **No ride-state-machine, money, wallet, dispatch, or insurance-period code paths touched.** No background loop, migration, or API contract changed.

## 5. User-experience effect

- **Driver- and rider-facing**, visible mid-session to anyone with the inbox open. Tapping a notification now updates instantly with no spinner flash, and a notification with no destination screen opens a modal showing its full text instead of appearing unresponsive.
- No copy changes beyond one new, static "Close" label (already an existing shared string on driver-app via `t('common.close')`; inline English literal on rider-app, matching that screen's existing un-translated pattern).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/hooks/queries/notificationQueries.ts` | `useMarkNotificationRead`/`useMarkAllNotificationsRead` rewritten as true optimistic mutations (`onMutate`/`onError`/`onSettled` with `refetchType: 'none'`) | Stop the mark-as-read round trip from flipping `isFetching` and flashing the refresh spinner on every tap |
| `driver-app/app/driver/notifications.tsx` | Added fallback detail Modal + `selectedNotification` state; `handleNotificationPress` falls through to it instead of doing nothing; stale comment on `markAsRead` corrected | Show full text for notification types with no destination screen |
| `driver-app/__tests__/app/driverNotificationsScreen.test.tsx` | Updated the "unmapped type" test to assert the modal opens with the full body; added a modal-close test | Pin the new behavior |
| `rider-app/app/notifications.tsx` | Same fallback detail Modal, mirrored for this app's own icon/theme helpers and inline-English copy | Same gap existed here (unmapped types, and mapped types missing their id) |
| `rider-app/__tests__/notificationsScreen.test.tsx` | Extended the existing "does not navigate" tests to also assert the modal opens; added an explicit `ride_cancelled` + close-button test | Pin the new behavior against the exact reported scenario |
| `docs/known-forks.md` | New registry row for the two `notifications.tsx` screens | Second one-way-fix risk on this exact pair; flag it mechanically going forward |

## 7. Before / after

```ts
// Before — invalidates and waits; the active query's refetch flips isFetching
export const useMarkNotificationRead = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async (notificationId: string) => {
            const res = await api.put(`/notifications/${notificationId}/read`);
            return res.data;
        },
        onSuccess: () => { queryClient.invalidateQueries({ queryKey: queryKeys.notifications.list }); },
        onError: () => { queryClient.invalidateQueries({ queryKey: queryKeys.notifications.list }); },
    });
};
```

```ts
// After — writes the cache directly on tap; reconciles later without a visible refetch
export const useMarkNotificationRead = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async (notificationId: string) => {
            const res = await api.put(`/notifications/${notificationId}/read`);
            return res.data;
        },
        onMutate: async (notificationId: string) => {
            await queryClient.cancelQueries({ queryKey: queryKeys.notifications.list });
            const previous = queryClient.getQueriesData({ queryKey: queryKeys.notifications.list });
            queryClient.setQueriesData({ queryKey: queryKeys.notifications.list }, (old: any) => /* flip is_read, decrement unread_count */);
            return { previous };
        },
        onError: (_err, _id, context) => { context?.previous?.forEach(([k, d]) => queryClient.setQueryData(k, d)); },
        onSettled: () => { queryClient.invalidateQueries({ queryKey: queryKeys.notifications.list, refetchType: 'none' }); },
    });
};
```

```tsx
// Before (driver-app) — falls through to nothing
if (item.type === 'lost_and_found' || item.type === 'lost_and_found_message') { /* ... */ }
// (no else — tap silently does nothing for every other type)
```

```tsx
// After — falls through to a detail modal instead
if (item.type === 'lost_and_found' || item.type === 'lost_and_found_message') { /* ...; return; */ }
setSelectedNotification(item); // opens the fallback Modal
```

## 8. Rollback plan

`git-revert-safe`. No migration, no persisted server-side state, no flag. All three changes are render-path/query-config only — a `git revert` (plus an OTA/redeploy) is a complete rollback, since nothing here writes data.

## 8b. Adversarial review (ran before committing, per CLAUDE.md pre-merge gate #10)

Ran `spinr-accessibility-reviewer` and `spinr-design-consistency-reviewer` against this diff. Accessibility findings, all applied:

- **Backdrop wasn't tap-to-dismiss** — inconsistent with this app's own established pattern (`rider-app/app/settings.tsx:210-211`'s language-picker modal uses a `Pressable style={StyleSheet.absoluteFill}` sibling behind the card). Fixed identically in both files.
- **Close button touch target undersized** (~37-40pt, under the ~44pt guideline) — more significant here than the pre-existing `retryBtn` it copied from, since it was the *only* working dismiss path before the backdrop fix above. `paddingVertical` widened 10→14 in both files.
- **Modal's icon glyph had no `accessible={false}`** — an `Ionicons` glyph is a `Text` node and accessible by default; a screen reader could stop on it before the title with no meaningful label. Marked decorative (`accessible={false}` + `importantForAccessibility="no-hide-descendants"`) on the modal's icon wrapper only (the pre-existing row-level icon is unchanged, out of scope).
- **Not applied — flagged instead**: the Close button's white-on-primary text contrast (`modalCloseText` on `modalCloseBtn`) reasons out to ~3.4-3.6:1 against both light/dark primary tokens, under the 4.5:1 AA floor for normal-weight 14px text. This is a copy of this same file's pre-existing `retryText`/`retryBtn` pairing (driver-app, rider-app both already had it before this change) — a pre-existing, app-wide contrast gap being propagated into a second location, not a new regression introduced here. Not fixed in this PR since it would mean re-tuning a shared color token used well beyond this screen; worth its own ticket.
- **Not verified**: no VoiceOver/TalkBack pass was run (no device access this session) — the backdrop/touch-target/icon fixes above are concrete and don't need one to justify; whether focus actually lands on the modal content on open and returns to the row on close is asserted by RN's native `Modal` behavior, not app code, and remains unverified on-device.

Design-consistency review findings:

- Independently flagged the same backdrop-tap-to-dismiss gap as the accessibility review above (already fixed) — confirms it against a second existing in-app precedent (`rider-app/app/(tabs)/account.tsx`'s photo viewer, `rider-app/app/ride-options.tsx`'s documented dismiss contract), in addition to the `settings.tsx` pattern the fix actually follows.
- Reiterated the known rider-app/shared-hook divergence already disclosed in this PR's `docs/known-forks.md` row: rider-app's own mark-read failure path (`rider-app/app/notifications.tsx`, pre-existing, not touched by this diff) rolls back the optimistic UI with no user-visible error (no `Alert`/toast), unlike driver-app's `Alert.alert` on the same failure. Pre-existing, out of scope for this fix (which only touches the navigation-fallthrough path, not the read-marking error path) — flagged as a follow-up, not fixed here.
- All color/typography/animation choices in the new modal confirmed to match existing (if in some cases pre-existing-and-scale-violating) convention — no new drift introduced.
- No blockers.

## 9. Verification performed

- [x] Automated tests run — driver-app: `npx jest` full suite, **150/150 suites, 1728/1728 tests** passing (includes 2 new/updated notification tests). rider-app: `npx jest` full suite, **151/151 suites, 2092/2092 tests** passing (includes 2 new/updated notification tests).
- [x] `npx tsc --noEmit` clean on both driver-app and rider-app.
- [x] Blast-radius grep performed: `useMarkNotificationRead`, `useNotifications(`, `queryKeys.notifications` across the repo (excluding `node_modules`).
- [x] Reviewed against `CLAUDE.md` conventions: no state-machine/money/RLS/PIPEDA surface touched; this is a pure client render/query-cache change.
- [x] Adversarial review: ran `spinr-design-consistency-reviewer` and `spinr-accessibility-reviewer` against the diff before committing (see their findings folded into this entry / addressed inline).
- [ ] **Not run**: a real production build (`npx expo export`) for either app — not performed this session; only `tsc --noEmit` + full Jest. Flagging explicitly per this repo's rule that a passing dev-server/`tsc` check alone is not equivalent.
- [ ] **Not verified on a real device.** rider-app and driver-app have no automated visual-regression tooling — the modal's layout/contrast was reasoned about against existing theme tokens and mirrored existing card styling, not screenshotted or manually tapped through on-device.

## 10. What was NOT verified

- Not tested against a live backend/Supabase — all coverage is through the existing mocked-`api`/mocked-hook Jest suites.
- Not run through a real production build (`expo export`) on either app this session.
- Not confirmed on a physical device — this environment has no device access; the fix is reasoned from the existing, working rider-app optimistic-update pattern (which does not exhibit the jitter) applied to driver-app's hook, not from a live repro-then-fix cycle on hardware.
- A separate, more serious issue was found while auditing the broader notification pipeline for this task (not touched by this PR): `backend/utils/push_retry.py`'s FCM sender hardcodes `channel_id="ride-offers"` for every retried push regardless of `target_app`, unlike the primary send path (`backend/features.py:1356-1359`) which explicitly branches on it and documents why ("Android silently drops notifications to channels that don't exist on the receiving app"). A rider-targeted push (including a `priority="safety"` one) that fails its first delivery attempt and falls into the retry queue would silently never reach the rider on Android. Flagged for a separate, properly-scoped backend fix — not included here.

## 10b. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-layer cleanup needed)
- [x] Blast radius is stated, not assumed (grepped, single-surface for the shared hook; single-consumer-route for each screen)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 states the visible change plainly)
