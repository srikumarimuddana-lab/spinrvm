# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-19 |
| Author | Claude Code (agent), for ittalenthire.ca@gmail.com |
| Surface(s) | backend, rider-app, driver-app |
| Domain (Sentry tag) | rides (notification inbox is rides/drivers-adjacent, not its own listed domain — closest existing tag) |
| PR / commit link | branch `mvapps/focused-tesla-0o88ku` (not yet pushed/opened as a PR by this session) |
| Related issue or gap ID | none filed — ad hoc feature request ("notification-system revamp") |

## 1. Issue / gap identified

The existing in-app notification inbox (backend `GET/PUT /notifications*`, rider/driver inbox
screens) had no way to delete a notification or clear the inbox, and the unread bell badge on
both apps' home/dashboard screens was pure REST polling with no real-time push, so a new
notification could sit unseen on the badge for up to a full poll interval.

## 2. Root cause

Not a bug — a gap in original scope. `backend/routes/notifications.py` was built with list/read/
preferences endpoints only; no delete path was ever added. No WebSocket event type for in-app
notifications existed anywhere (`backend/socket_manager.py`, `backend/routes/websocket.py`) —
confirmed by the ground-truth research pass before this task began — so badge counts had no
push path to piggyback on.

## 3. Fix / remediation

**Backend** (`backend/routes/notifications.py`):
- `DELETE /notifications/{notification_id}` — deletes one notification owned by the caller;
  404 if not found/not owned (ownership check mirrors the existing `mark_as_read`/`mark_all_read`
  filter-by-`user_id` pattern).
- `DELETE /notifications?read_only=true` — clears read-only or all (default) notifications for
  the caller, single filtered `delete_many` call (no N+1).
- `create_notification()` now emits a best-effort `new_notification` WebSocket event (`{"type":
  "new_notification", "notification": {...}, "unread_count": N}`) to both `rider_{user_id}` and
  `driver_{user_id}` connection keys via `socket_manager.manager.send_personal_message` — the
  same targeted-send primitive `broadcast_ride_status` already uses, not a new transport. The DB
  write always happens first and is never rolled back or blocked by a WS-send failure. The push
  itself is scheduled via `asyncio.create_task` (fire-and-forget), not awaited inline — see the
  "Post-implementation self-review catch" note below.

**Post-implementation self-review catch**: the first version of this change `await`ed the WS
push inline inside `create_notification()`. Since that function is called synchronously from many
request-handling paths across the codebase (some latency-sensitive), a slow/stuck socket send
(up to ~2s per candidate connection key, two keys tried per notification) could have added
latency to any of those callers — the opposite of "best-effort," and the same class of anti-
pattern CLAUDE.md's Performance SLAs section flags (awaiting a slow side-effect inline in a
request handler). Fixed before this branch was reported done: the push is now scheduled via
`asyncio.create_task`, with a strong reference held in a module-level set
(`_background_ws_tasks`, discarded via `add_done_callback` on completion) so the event loop
can't garbage-collect it mid-run — a documented asyncio footgun for a fire-and-forget task with
no other reference. Tests updated accordingly (await the tracked task before asserting on the
mocked send).

**Shared** (`shared/hooks/queries/notificationQueries.ts`): `useDeleteNotification()` and
`useClearNotifications(readOnly)`, same optimistic-update + `refetchType:'none'` pattern as the
existing `useMarkNotificationRead`/`useMarkAllNotificationsRead`.

**Rider-app**: `app/notifications.tsx` migrated from ad-hoc `useState`+`api.get/put` onto the
shared hooks; added per-item delete (trash icon, same interaction `saved-places.tsx` already
uses) and a "Clear all" header action, both confirmed via the existing `ConfirmSheet` component;
added category tabs (All/Rides/Promotions/Safety/General) built only from `type` values already
in the codebase's own taxonomy. `useRiderSocket.ts` merges an incoming `new_notification` event
into the shared query cache; `app/(tabs)/index.tsx`'s bell badge now reads that cache
(`useNotifications(1)`) with an explicit 3-minute reconciliation poll (was: fetched on every
screen focus with no fixed interval).

**Driver-app**: `app/driver/notifications.tsx` (already on the shared hooks) gets the same
delete/clear-all/category-tabs treatment, confirmed via the app's existing `showAlert` dialog.
`useDriverDashboard.ts` merges `new_notification` the same way; `app/driver/(tabs)/index.tsx`'s
badge now reads `useNotifications(1)`, poll widened from a fixed 60s interval to 5 minutes.

## 4. Risk & impact on existing functionality

**Blast radius — backend.** Grepped for every other caller of `create_notification()` and the
`notifications` table:
- `create_notification()` is called from many places across `backend/routes/` and
  `backend/utils/` (ride lifecycle, corporate, safety, driver document/earnings flows, etc.) —
  all of them get the new WS push automatically and unconditionally. This is intentional (the
  point of the feature) but means the blast radius for the WS-emit change is **every existing
  caller of `create_notification()`**, not just this task's own call sites. Mitigation: the push
  is wrapped in its own try/except, is fully best-effort, and the function's return value/shape
  (the notification dict) is unchanged, so no caller's existing logic is affected even if the
  push silently no-ops.
- No other code reads/writes the `notifications` table directly outside `routes/notifications.py`
  (confirmed by grep) — the new DELETE endpoints are additive and don't touch any other
  consumer's filter shape.
- `NOTIFICATION_DEEPLINKS` and the `type` column's existing values/meaning are untouched.

**Blast radius — shared hooks.** `notificationQueries.ts` is imported by both apps'
`app/notifications.tsx` (rider) / `app/driver/notifications.tsx` (driver), `app/settings.tsx`
and `app/privacy-settings.tsx` (rider, preferences only — untouched by this change), and now also
`app/(tabs)/index.tsx` (rider) / `app/driver/(tabs)/index.tsx` (driver) for the badge. No existing
export was removed or renamed; `useNotifications`/`useMarkNotificationRead`/
`useMarkAllNotificationsRead`/`useNotificationPreferences`/`useUpdateNotificationPreferences` are
byte-for-byte unchanged.

**Blast radius — `useDriverDashboard.ts`.** This hook has exactly one production consumer
(`app/driver/(tabs)/index.tsx`) plus its own test file — confirmed by grep. The only change is
one new `switch` case (`new_notification`) and one new import; every other case/branch is
untouched.

**Blast radius — `useRiderSocket.ts`.** Four consumers per the hook's own code comment
(`ai-assistant.tsx`, `app/_layout.tsx`, `ride-status.tsx`, `store/rideStore.ts`) — only one new
`switch` case added, no change to the hook's public API (`{ connectionState, wsConnected,
sendMessage }`), no change to connect/disconnect/reconnect logic.

**Cross-surface**: isolated to notifications; no interaction with the ride state machine,
money/wallet deltas, or a background loop in `lifespan.py`.

**Known limitation surfaced during this work**: `useRiderSocket` only opens a WebSocket while the
rider has an active ride (by design, pre-existing). A rider with no active ride gets no WS fast
path for the bell badge — only the widened poll / query staleness. Driver-app's socket has a
broader online-lifecycle connection window, so this gap is rider-specific.

## 5. User-experience effect

- **Rider**: new "Clear all" button and per-item delete (trash icon) in the notifications
  screen, each behind a confirmation sheet — this is new, additive UI, not a change to any
  existing flow's behavior. New category tabs at the top of the inbox. Bell badge on the home tab
  now updates instantly while a WS connection is live (i.e., while on an active ride); otherwise
  behavior is a same-or-better poll cadence than before.
- **Driver**: same additive delete/clear-all/tabs on the driver notifications screen. Bell badge
  on the dashboard now updates instantly via WS; REST poll widened from 60s to 5 minutes as a
  fallback.
- **Corporate admin / internal admin**: no visible change.
- **Mid-session visibility**: yes — a rider mid-ride or a driver online will see the badge/inbox
  update live via the new WS path; this is the intended improvement, not an unexpected mid-session
  behavior change to anything else.
- No notification copy changed; no new validation rules that could reject previously-valid input.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/notifications.py` | Added `DELETE /notifications/{id}`, `DELETE /notifications?read_only=`, best-effort WS push in `create_notification()` | New delete/clear endpoints + real-time badge push |
| `backend/tests/test_notifications_delete.py` | New test file | Cover delete-one, clear-all, clear-read-only, WS push best-effort behavior |
| `shared/hooks/queries/notificationQueries.ts` | Added `useDeleteNotification`, `useClearNotifications` | Shared mutation hooks for both apps |
| `rider-app/app/notifications.tsx` | Migrated to shared hooks; added delete/clear-all/category tabs | Feature parity + consistency with shared hooks pattern |
| `rider-app/__tests__/notificationsScreen.test.tsx` | Rewritten to mock `@shared/hooks/queries` instead of `@shared/api/client`; added delete/clear/tab coverage | Match new implementation |
| `rider-app/hooks/useRiderSocket.ts` | Added `new_notification` WS case | Instant badge/list update while socket connected |
| `rider-app/app/(tabs)/index.tsx` | Badge now reads `useNotifications(1)`; poll widened to 3 min | Single source of truth with WS + reconciliation fallback |
| `rider-app/__tests__/homeScreen.test.tsx` | Added `@shared/hooks/queries` mock | Prevent real-QueryClient crash under test |
| `driver-app/app/driver/notifications.tsx` | Added delete/clear-all/category tabs | Feature parity with rider-app |
| `driver-app/i18n/en.json`, `fr.json`, `es.json` | Added `notifications.deleteTitle/deleteBody/clearAllTitle/clearAllBody/clearAll` keys | i18n coverage for new UI copy |
| `driver-app/__tests__/screens/notifications.test.tsx`, `__tests__/app/driverNotificationsScreen.test.tsx` | Added delete/clear/tab coverage; fixed FlatList lookups for the new nested tabs list | Match new implementation |
| `driver-app/hooks/useDriverDashboard.ts` | Added `new_notification` WS case | Instant badge/list update |
| `driver-app/app/driver/(tabs)/index.tsx` | Badge now reads `useNotifications(1)`; poll widened to 5 min | Single source of truth with WS + reconciliation fallback |
| `driver-app/__tests__/app/driverDashboardScreen.test.tsx` | Mocks `@shared/hooks/queries` `useNotifications` instead of asserting raw `api.get` | Match new implementation |
| `driver-app/hooks/__tests__/useDriverDashboard.socketLifecycle.test.ts` | Mocks `@shared/api/queryClient` | Avoid a module-load-time `AppState.addEventListener` side effect this test's minimal RN mock doesn't support |

## 7. Before / after

**Bell badge source (driver-app example; rider-app is the same shape):**

```
# Before
const [unreadNotifCount, setUnreadNotifCount] = useState(0);
useEffect(() => {
  const fetchUnread = async () => {
    if (!(await isAppCheckTokenReady())) return;
    api.get('/notifications?limit=1').then((res) => setUnreadNotifCount(res.data?.unread_count ?? 0));
  };
  fetchUnread();
  const timer = setInterval(fetchUnread, 60 * 1000);
  return () => clearInterval(timer);
}, []);
```

```
# After
const { data: notifData, refetch: refetchNotifications } = useNotifications(1);
const unreadNotifCount = notifData?.unread_count ?? 0;
useEffect(() => {
  const timer = setInterval(() => { refetchNotifications(); }, 5 * 60 * 1000);
  return () => clearInterval(timer);
}, [refetchNotifications]);
```

Plus: `useDriverDashboard.ts`'s WS message handler now has a `new_notification` case that writes
directly into the same TanStack Query cache `useNotifications(1)` reads, via
`queryClient.setQueriesData({ queryKey: queryKeys.notifications.list }, ...)`.

## 8. Rollback plan

Purely additive — no destructive migration, no data backfill, no existing endpoint/hook/column
removed or repurposed. Rollback = `git revert` the commits on this branch (listed newest-first:
"Wire driver bell badge...", "Extend driver notifications screen tests...", "Mirror rider-app
delete/clear/tabs...", "Wire rider bell badge...", "Migrate rider notifications inbox...", "Add
useDeleteNotification/useClearNotifications...", "Add tests for notification delete/clear...",
"Add DELETE /notifications endpoints..."). No Stripe charges, wallet deltas, or ride-state rows
are touched by any of this, so a code revert is a complete and sufficient rollback — the
`notifications` table schema itself is unchanged (no new migration was needed; DELETE uses the
existing table).

## 9. Verification performed

- [x] Automated tests run (all executed in this session, exact counts below) — unit-level backend
  tests (mocked Supabase, no real DB) and RN component tests (react-test-renderer /
  @testing-library/react-native, no real device/simulator).
  - `pytest backend/tests/test_notifications_delete.py backend/tests/test_p3_push_notifications.py backend/tests/test_notification_preferences.py -q` → **65 passed** (7 new + 58 pre-existing, all green).
  - `npx jest __tests__/notificationsScreen.test.tsx __tests__/homeScreen.test.tsx hooks/__tests__/useRiderSocket*` (rider-app) → **78 passed** across 4 suites: `notificationsScreen.test.tsx` 21, `homeScreen.test.tsx` 42, `useRiderSocket.reconnect.test.ts` + `useRiderSocket.chat.test.ts` 15.
  - `npx jest __tests__/screens/notifications.test.tsx __tests__/app/driverNotificationsScreen.test.tsx __tests__/app/driverDashboardScreen.test.tsx hooks/__tests__/useDriverDashboard*` (driver-app) → **118 passed** across 5 suites: `screens/notifications.test.tsx` 3, `app/driverNotificationsScreen.test.tsx` 23, `app/driverDashboardScreen.test.tsx` 63, `hooks/useDriverDashboard.socketLifecycle.test.ts` 21, `hooks/useDriverDashboard.chat.test.ts` 8.
  - No test was skipped or marked xfail to make these numbers pass.
- [x] Blast-radius grep performed — see section 4 above (every `create_notification()` caller,
  every `notifications` table reader/writer, every consumer of `notificationQueries.ts`,
  `useRiderSocket.ts`, and `useDriverDashboard.ts`).
- [x] Reviewed against relevant CLAUDE.md conventions: WebSocket auth/connection-key pattern
  (`rider_{user_id}`/`driver_{user_id}`, reused not reinvented), "do not silently swallow
  errors" (WS-push failure is debug-logged, not swallowed silently, and is the documented
  exception for an expected disconnected-recipient case; DB errors in the new endpoints still
  propagate as they did before), ownership-scoped DB filters on every new endpoint.
- [ ] Manual repro steps followed in staging — **not done**; no staging environment was available
  in this session.
- [ ] Feature-flagged — **not done**. This is additive, non-breaking UI (new buttons/tabs, no
  existing control removed or repurposed) and a real-time enhancement to an existing poll, not a
  new validation rule or a change to a live-tested money/ride/auth flow, so it was judged not to
  need a flag per CLAUDE.md's "prefer additive/flagged rollout for ... new/changed UX" — reasoned
  as additive-and-safe rather than flagged. If the user wants a flag anyway (e.g. to dark-launch
  the WS push specifically), `app_settings` is the existing mechanism.

**Production build**: **not run**. This task touches `rider-app`/`driver-app` (Expo/React Native),
not `admin-dashboard` — CLAUDE.md's explicit "real production build" requirement is scoped to
`admin-dashboard`/`rider-app`/`driver-app` React web/Next builds; neither mobile app has an
analogous "production build" step distinct from its Jest test run in this environment (no EAS
build was triggered, per the `[build]` commit-message gate, and none should be for this change).
Only `npx jest` (not a dev server, not `tsc --noEmit` alone) was run, as stated above.

## 10. What was NOT verified

- No real device or simulator testing — all coverage is RN component-tree rendering via
  react-test-renderer / @testing-library/react-native with every native module and network call
  mocked. The WebSocket push path in particular was verified only via unit tests asserting the
  right cache-merge function runs on the right message shape, not against a live WS connection
  or Redis pub/sub relay.
- No visual regression tooling exists for rider-app or driver-app (confirmed in CLAUDE.md) — the
  new "Clear all" button, per-item delete icon, and category tabs were reasoned about against the
  `spinr-rider-driver-design-system` skill's guidance and existing sibling patterns
  (`saved-places.tsx`'s delete icon, `ConfirmSheet`/`showAlert` for confirmation), not
  screenshotted or reviewed by a human designer.
- The backend WS push was not tested against a real multi-replica deployment with Redis pub/sub
  active (`ws_pubsub.py`) — only the local best-effort `send_personal_message` call is unit-tested
  with a mocked `manager`.
- `backend/tests/test_notifications_delete.py`'s `count_documents`/`insert_one`/
  `send_personal_message` are all mocked; no test hit a real Supabase instance.
- This session did not push the branch or open a PR — the calling session/user is expected to do
  that and can re-run the checks above against their own environment.
- A pre-existing, unrelated environment issue was found and worked around only locally
  (never committed): this sandbox's `driver-app` node_modules resolves `react-test-renderer` to
  the version its own `package.json`/`resolutions` field pins (19.2.3), which
  `@testing-library/react-native`'s peer-dependency check rejects in favor of 19.3.0 (matching the
  pinned `react` version). This reproduces identically on a completely untouched test file
  (`__tests__/components/ActivityView.test.tsx`), confirming it predates this change and isn't
  something introduced here — but it means every `@testing-library/react-native`-based driver-app
  test in this sandbox needed a local, non-committed version patch to actually execute. Flagging
  this for the user/CI owner since it may also affect other agents' or CI's ability to run this
  suite; it was not something this task's scope covered fixing (touching `package.json`/
  `resolutions` is exactly the kind of unrelated dependency change CLAUDE.md's "surgical changes"
  rule asks not to make without being asked).
