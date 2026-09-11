# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | Claude Code |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (branch `claude/vehicle-icon-movement-animation-8tys8o`) |
| Related issue or gap ID | Follow-up to `docs/change-log/` FlatList zero-height fix (2026-09-10, commit `3158e97`) — that fix was confirmed live on a real device via OTA update ID comparison, but the bug still reproduced live, disproving it as a complete fix |

## 1. Issue / gap identified

Live-testing report: on at least one real Android device, `app/driver/notifications.tsx` shows the header (back button, title, "N unread notifications") but the notification list below it renders nothing — no rows, no loading spinner, no error state, no "all caught up" empty state. Pull-to-refresh on the blank area does nothing at all, indicating the FlatList's rendered area has no usable height or touch surface. iOS is unaffected. Other FlatList screens in the same app (confirmed: Activity/ride-history) render correctly on the same device.

## 2. Root cause

The screen used a `View (flex:1)` → `LinearGradient` header → `FlatList (flex:1)` sibling layout. A prior fix (2026-09-10) added `style={{ flex: 1 }}` to the FlatList and `flexGrow: 1` to its `contentContainerStyle`, reasoning from known Android/Yoga flex behavior that this should make the FlatList fill the remaining space after its header sibling. That fix was never verified against a real device (documented as such in its own Change Impact Log) and, per this report, does not hold universally — the exact device/OS combination reported here still collapses the FlatList's rendered area despite both properties being present and unmodified since.

The Activity screen (`components/activity/ActivityView.tsx`), which renders correctly on the same device, does not use this sibling-header shape at all: its header/stats/filter content is passed as the FlatList's own `ListHeaderComponent`, so there is exactly one flex-managed component (the FlatList itself) rather than two siblings needing coordinated flex resolution.

## 3. Fix / remediation

Restructured `notifications.tsx` to match the Activity screen's proven-working shape: the `FlatList` is now the screen's root element, and the header (LinearGradient + back button + title + mark-all-read + unread count) is passed as `ListHeaderComponent` instead of a sibling `View`. `stickyHeaderIndices={[0]}` keeps that header pinned at the top while scrolling, so the back button and "Mark All Read" stay reachable exactly as before (this was the main UX risk of moving the header into scrollable content, and is why a plain "move it into the header prop" change alone would not have been acceptable).

Two style properties moved to compensate for `contentContainerStyle` no longer wrapping the header:
- `notifCard` gained `marginHorizontal: 16` (previously the 16px side inset came from the FlatList's `contentContainerStyle.paddingHorizontal`, which would now double up against the header's own `paddingHorizontal: SPACING.md` if left in place).
- `emptyState` gained `paddingHorizontal: 16` for the same reason (the loading/error/empty states previously inherited their side inset from the same container padding).

## 4. Risk & impact on existing functionality

- **Blast radius: single file** (`driver-app/app/driver/notifications.tsx`). This screen has exactly one consumer path (pushed from the driver dashboard bell icon via `router.push('/driver/notifications')`); no other screen imports or renders it.
- **No prop/type/API change** — `Notification` interface, the `useNotifications`/`useMarkNotificationRead`/`useMarkAllNotificationsRead` hooks, and `handleNotificationPress`'s routing table are all untouched.
- **Existing tests unaffected**: both dedicated test files (`__tests__/app/driverNotificationsScreen.test.tsx`, `__tests__/screens/notifications.test.tsx`) pass unmodified — including the back-button and pull-to-refresh tests, which exercise exactly the two behaviors (`stickyHeaderIndices` on the header, `refreshControl` unchanged) most at risk from this restructuring.
- **Visual-only change on this one screen**: card and empty-state horizontal insets should be pixel-identical to before (16px), since the padding moved from the container to the individual elements rather than being removed.
- **New behavior**: the header + "N unread" line now sticks to the top while scrolling a long inbox instead of always being static (previously it was static because the whole screen never scrolled as one unit in the same way) — this is the sticky-header mechanism, functionally equivalent to "always visible," so no regression, but it is a different rendering mechanism than before (worth noting for anyone debugging this screen later).

## 5. User-experience effect

**Driver-facing.** On the affected device class, the notification list should now actually render (rows, or the appropriate loading/error/empty state) instead of appearing entirely blank with an unresponsive pull-to-refresh. On unaffected devices (where the prior fix already worked, e.g. this session's own Jest/tsc environment), the screen should look and behave identically — same spacing, same back-button/mark-all-read placement, same scroll behavior, now via a sticky header instead of a fixed sibling.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/driver/notifications.tsx` | Header moved from a sibling `View` to the `FlatList`'s `ListHeaderComponent`, with `stickyHeaderIndices={[0]}` to keep it pinned; `notifCard` and `emptyState` gained explicit horizontal padding/margin to replace what the removed `contentContainerStyle.paddingHorizontal` used to provide | The sibling-header + `flex:1` FlatList shape reproduced a zero-height Android bug live, on a real device, even after the prior attempted fix; this adopts the exact structural pattern already proven working on that same device by another screen in this app |

## 7. Before / after

```tsx
// Before — header and FlatList as flex siblings inside a wrapping View
<View style={styles.container}>
    <LinearGradient style={[styles.header, { paddingTop: insets.top + 12 }]}>
        {/* back button, title, mark-all-read, unread count */}
    </LinearGradient>
    <FlatList
        style={{ flex: 1 }}
        contentContainerStyle={{ flexGrow: 1, paddingHorizontal: 16, paddingBottom: insets.bottom + 40 }}
        data={notifications}
        ...
    />
</View>
```

```tsx
// After — FlatList is the root; header is its (sticky) ListHeaderComponent
<FlatList
    style={styles.container}
    data={notifications}
    ListHeaderComponent={
        <LinearGradient style={[styles.header, { paddingTop: insets.top + 12 }]}>
            {/* back button, title, mark-all-read, unread count — unchanged */}
        </LinearGradient>
    }
    stickyHeaderIndices={[0]}
    contentContainerStyle={{ flexGrow: 1, paddingBottom: insets.bottom + 40 }}
    ...
/>
// notifCard: + marginHorizontal: 16
// emptyState: + paddingHorizontal: 16
```

## 8. Rollback plan

`git-revert-safe` — pure client-rendering restructuring, no data written anywhere, no schema/API/prop changes. Reverting restores the prior sibling-header layout (which is known-broken on at least one real device, so a revert is a downgrade, not a neutral rollback — but it is mechanically safe).

## 9. Verification performed

- [x] `driver-app/__tests__/app/driverNotificationsScreen.test.tsx` (16 tests) and `driver-app/__tests__/screens/notifications.test.tsx` (5 tests) — 21/21 passing, unmodified.
- [x] Full driver-app suite: `npx jest` — 141/141 suites, 1597/1597 tests passing.
- [x] `npx tsc --noEmit` on driver-app — clean, no errors.
- [x] Live device evidence gathered before this fix: OTA update-ID timestamp comparison (via CI publish logs) confirmed the device was running a bundle published after the prior, insufficient fix merged, ruling out "stale bundle" as the explanation before concluding the prior fix itself was incomplete.
- [x] Structural comparison against `components/activity/ActivityView.tsx`, confirmed by the user to render correctly on the same device, to identify the specific pattern (ListHeaderComponent vs. sibling) that differs between the working and broken screens.
- [ ] **Not verified on the actual reporting device** — this environment has no real Android hardware. The fix is reasoned from a real, working comparison point in this same codebase on that exact device, not purely from general RN/Android theory (unlike the prior attempt), but final confirmation requires the reporting device to receive this OTA update and re-check.

**What was NOT verified:** whether this specific restructuring resolves the exact device's rendering bug — this can only be confirmed once the fix reaches that device via OTA and the user re-checks. If it does not, the next step would be to gather Sentry/native logs from that device rather than continue reasoning from other screens' behavior alone, since two rounds of theory-based fixes for this screen have now been needed.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-layer cleanup needed)
- [x] Blast radius is stated, not assumed (single file, single consumer route, grepped)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 states the only intentional new mechanism — sticky header — and why it's not a regression)
