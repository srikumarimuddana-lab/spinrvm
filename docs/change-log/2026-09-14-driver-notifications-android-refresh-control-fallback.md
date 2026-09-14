# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (branch `claude/adoring-edison-h77qzc`) |
| Related issue or gap ID | Third live report on `app/driver/notifications.tsx`: 2026-09-10 (`flex: 1`), 2026-09-11 (FlatList-as-root restructure, `docs/change-log/2026-09-11-driver-notifications-flatlist-restructure.md`), 2026-09-13 (screen-local ErrorBoundary, #5341). This report: **Android only, iOS works.** |

## 1. Issue / gap identified

Live-testing report: on Android, tapping the bell icon on the driver dashboard opens the Notifications screen but it is empty — no rows, no spinner, no error state, no "all caught up" state. iOS shows the inbox correctly. Production data confirms the inbox is not actually empty: driver users hold ~2,500 inbox rows (`ride_cancelled`, `auto_offline`, `rating_received`, document reminders, …), and the list request (`GET /notifications?limit=50&offset=0`) reaches Supabase and returns 200 — yet only 27 rows have *ever* been marked read, none since 2026-08-20.

## 2. Root cause

`driver-app/components/SafeRefreshControl.tsx` is a drop-in for `RefreshControl` that, when it decides the native refresh component is not renderable (a guarded `require()` of an RN-internal native-component path — fails on `catch` as well as on a non-renderable export), renders a plain spinner instead — and rendered **nothing at all** when `refreshing` was false. That fallback ignored `props.children`.

React Native's `ScrollView` (which `FlatList` renders through) treats the `refreshControl` element differently per platform:

- **iOS**: the element is rendered as a *sibling* inside the scroll view. `children` is undefined; a spinner-or-null fallback is harmless. → iOS works.
- **Android**: `ScrollView.js` wraps the scroll view *in* the refresh control — `React.cloneElement(refreshControl, { style }, <NativeScrollView …>{content}</NativeScrollView>)`. The refresh control is the **parent** of the whole list. A fallback that drops `children` drops the entire FlatList. → Android renders nothing where the list should be, and there is no touch surface for pull-to-refresh.

This is also consistent with the 2026-09-11 report's exact Android symptom (header visible, list area blank, pull-to-refresh not registering) — the header at that time was a sibling *outside* the FlatList, so it survived while the FlatList vanished. After the 2026-09-11 restructure moved the header inside the FlatList, the same failure takes the header with it, which matches "empty page".

Why the previous two fixes could not work: both changed the FlatList's layout, but the FlatList was never being mounted on Android in the first place.

## 3. Fix / remediation

`SafeRefreshControl`'s fallback now always renders `props.children` (and forwards the layout `style` ScrollView hands the wrapper on Android, plus `flex: 1` so the wrapped list fills the screen — what `AndroidSwipeRefreshLayout` would otherwise contribute). Spinner behaviour is unchanged: shown while `refreshing`, hidden otherwise. With no children (the iOS sibling shape) the output is identical to before (`null` when idle, spinner when refreshing).

The fallback is exported as `FallbackRefreshControl` purely so the regression test can exercise it directly; screens keep using the default export.

No change to the native-detection logic — whether the detection is a false negative on the reporting device is a separate question this fix does not depend on: with children preserved, the fallback is now safe on Android either way, and the native path was never the broken one.

## 4. Risk & impact on existing functionality

- **Blast radius: one shared component, five consumers** (grepped): `app/driver/notifications.tsx`, `app/driver/quests.tsx`, `app/driver/lost-and-found.tsx`, `app/driver/tax-documents.tsx`, `app/driver/payout-history.tsx`. Every one of them is a scrolling list with `refreshControl={<SafeRefreshControl …/>}`, so every one of them had the same Android blank-list failure whenever the fallback path was taken, and every one of them is fixed by this change. No rider-app or shared copy exists (`docs/known-forks.md` has no entry; grep confirms).
- **Native path untouched**: when the native `RefreshControl` is usable, `SafeRefreshControl` still renders `<RefreshControl {...props} />` exactly as before. Devices where the fallback never triggered (iOS, per the report) see no change.
- **Fallback path, iOS shape (no children)**: byte-for-byte same output as before (`null` idle / spinner refreshing).
- **Fallback path, Android shape (children present)**: previously dropped the list; now renders it inside a `flex: 1` `View`. The pull-to-refresh *gesture* is still unavailable on the fallback path (unchanged limitation — the native component is what provides it); every affected screen already has its own retry/refetch affordance or refetches on focus.
- **Tests**: the existing screen tests mock `SafeRefreshControl` as `() => null` (zero real coverage of this component — stated per CLAUDE.md gate 1), and RN's Jest `ScrollView` mock does not reproduce the Android `cloneElement` wrapping, so the screen tests could not have caught this and are left unmodified. A new dedicated test covers the fallback directly.

## 5. User-experience effect

**Driver-facing, Android only.** The Notifications screen (and quests, lost-and-found, tax-documents, payout-history) renders its list / loading / error / empty state instead of a blank area; notifications can be tapped and marked read. Visible immediately to a driver mid-session once the OTA update lands and the screen is reopened. iOS: no change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/components/SafeRefreshControl.tsx` | Fallback extracted to `FallbackRefreshControl`; it now renders `children` (with forwarded `style` + `flex: 1` when children exist) alongside the optional spinner | On Android the refresh control is the parent of the scroll view; dropping children dropped the whole list |
| `driver-app/__tests__/components/SafeRefreshControl.test.tsx` | New — 5 tests over the fallback: Android shape (children rendered idle and while refreshing, style forwarded) and iOS shape (null idle, spinner-only refreshing) | Regression coverage for the exact failure; the component previously had none |
| `docs/change-log/2026-09-14-driver-notifications-android-refresh-control-fallback.md` | This entry | Required by CLAUDE.md for a live-tested-surface fix |

## 7. Before / after

```tsx
// Before — fallback ignores children; on Android `children` IS the scroll view
const { refreshing, tintColor, colors } = props;
if (!refreshing) return null;
return (
  <View style={{ alignItems: 'center', justifyContent: 'center', paddingVertical: 12 }}>
    <ActivityIndicator size="small" color={color} />
  </View>
);
```

```tsx
// After — children (the wrapped list on Android) always render
const { refreshing, tintColor, colors, children, style } = props;
const hasChildren = children != null;
if (!refreshing && !hasChildren) return null;
return (
  <View style={[hasChildren ? { flex: 1 } : null, style]}>
    {refreshing ? (<View …><ActivityIndicator size="small" color={color} /></View>) : null}
    {children}
  </View>
);
```

Concrete scenario — Android, fallback path, idle inbox with 12 rows:
- Before: `FlatList` → `ScrollView` → `cloneElement(<SafeRefreshControl refreshing={false}/>, …, <NativeScrollView>…12 rows…</NativeScrollView>)` → `SafeRefreshControl` returns `null` → nothing on screen.
- After: same tree → `FallbackRefreshControl` returns `<View flex:1>{NativeScrollView with 12 rows}</View>` → inbox visible.

## 8. Rollback plan

`git revert` of the single commit — pure client rendering change, no data written, no schema/API/prop-contract change (the new named export is additive). Reverting restores the known-broken Android behaviour, so it is a downgrade, but mechanically safe and needs no data cleanup. Ships via the normal driver-app OTA path; no flag exists for this component and none is warranted for a one-file rendering fix of an already-broken screen.

## 9. Verification performed

- [x] Root cause established from evidence, not layout theory: (a) Supabase `notifications` table — driver users hold ~2,500 rows, 27 ever read, none since 2026-08-20; (b) Supabase API logs — the exact list query the screen issues (`order=created_at.desc&offset=0&limit=50`) returns 200 in the last 24 h, so backend + data are fine; (c) the working comparison screen on the same device (`ActivityView`) uses **no** `refreshControl` at all, which is the one structural difference from every broken screen; (d) React Native's `ScrollView` Android branch wraps the scroll view in the `refreshControl` element via `cloneElement` — the platform asymmetry that matches "Android only".
- [x] New unit test written (`SafeRefreshControl.test.tsx`, 5 cases) against the fallback.
- [ ] **Not run locally**: `yarn install` cannot complete in this environment (the npm registry is blocked both through the session proxy and directly, HTTP 403), so neither the new test, the existing 21 notification-screen tests, nor `tsc --noEmit` were executed here. CI's driver-app job (`yarn tsc --noEmit` + `yarn test`) is the first execution of both. **No real production build was run.**
- [ ] **Not verified on a real Android device** — no hardware in this environment. Unlike the two prior attempts, the fix targets a platform-specific mechanism (Android's `refreshControl` wrapping) that explains the Android/iOS split directly.

**What was NOT verified:** which of the two detection failure modes (`require()` throwing vs. a non-renderable export) is actually firing on the reporting Android build — the fix is correct under either, but if the reporting device *does* take the native path, this change is a no-op there and the cause lies elsewhere. rider-app/driver-app have no visual-regression tooling; the visual outcome is reasoned about, not screenshotted.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-layer cleanup)
- [x] Blast radius is stated, not assumed (five consumers, grepped and named)
- [x] No silent behaviour change to an already-shipped flow without the UX field filled in (§5)
