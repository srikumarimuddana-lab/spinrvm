# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | srikumarimuddana@gmail.com (with Claude Code) |
| Surface(s) | backend / rider-app / driver-app / shared |
| Domain (Sentry tag) | rides (Lost & Found), drivers |
| PR / commit link | branch `claude/lost-found-chat-bugs-n4qodp` |
| Related issue or gap ID | Live-testing bug report: L&F chat + notification inbox |

## 1. Issue / gap identified

Five defects reported from live app testing of Lost & Found:

1. Tapping an L&F notification in the **in-app inbox** did nothing (both apps) — it marked the row read but never navigated.
2. Tapping "Found an item" twice on a completed ride created a **second, duplicate** L&F case.
3. Messages sent by one party did not appear for the other until the chat screen was fully remounted.
4. The driver's L&F list did not open an existing case when re-entered via `rideId` — it re-showed the report form.
5. **The driver notification inbox showed "N unread" on the bell badge while the list rendered "You're all caught up!"** — reported again after the first four fixes shipped, which is what prompted the second investigation.

## 2. Root cause

Defects 1–4 were straightforward missing logic (no navigation branch; no idempotency guard; no refresh mechanism; no existing-case lookup).

Defect 5 is the interesting one and had a different root cause than assumed:

- `/api/v1/notifications` is **App-Check-enforced** — it is not in `_APP_CHECK_EXEMPT_PREFIXES` (`backend/core/middleware.py`).
- The dashboard bell badge (`driver-app/app/driver/(tabs)/index.tsx`) already knew this: it gates on `isAppCheckTokenReady()` and re-polls every 60 s, so it reliably obtains `unread_count`.
- The inbox **screen** used `useNotifications()`, which had **no such gate**. On a cold start it fired before Firebase App Check had minted a token and received a **401**.
- The shared `queryClient` sets **`retry: false`**, so that single 401 left the query in a terminal error state.
- The screen never read `isError` — it rendered the identical "No notifications / You're all caught up!" empty state for *a failed fetch* and *a genuinely empty inbox*.

Net effect: badge said 6, list said "all caught up", and nothing surfaced the 401. The count and the list were never actually disagreeing at the database level — one request succeeded and the other failed, and the failure was invisible.

## 3. Fix / remediation

- Notification inbox rows now route by `type` + `data.case_id` / `data.ride_id` (both apps).
- `driver_report_found_item` gained an idempotency guard, backed by a **unique index** on `(ride_id, driver_id)` plus a `DuplicateRecordError` catch that returns the existing case (closes the read-then-write race, not just the common path).
- Both L&F chat screens poll messages every 10 s while focused and foregrounded.
- Driver chat resolves an existing case when entered with `rideId`.
- `useNotifications` now gates on App Check readiness (with a 10 s cap so a misconfigured App Check surfaces as a visible error, not an endless spinner) and overrides the global `retry: false` with a bounded backoff.
- **Both inbox screens now distinguish loading / error / empty**, with a retry affordance on error.

## 4. Risk & impact on existing functionality

**Blast radius — stated, not assumed:**

- `useNotifications` (shared hook, the highest-risk edit): grepped for all consumers — **`driver-app/app/driver/notifications.tsx` only**, plus `driver-app/__tests__/screens/notifications.test.tsx` which mocks it. The rider app does *not* use this hook (it calls `api.get` directly). Single-surface.
- `isAppCheckTokenReady` is read-only and already used by `driver-app/(tabs)/index.tsx`, `rider-app/(tabs)/index.tsx`, and `driver-app/lib/androidAuto/carSession.ts`; this change adds a caller and does not alter the helper.
- `backend/routes/lost_and_found.py`: `driver_report_found_item` is called only from the driver chat screen. The new `get_rows` call adds one indexed read per report — not on a latency-SLA path.
- Migration 328 adds a **partial unique index** on `lost_and_found (ride_id, driver_id) WHERE ride_id IS NOT NULL AND driver_id IS NOT NULL`. It is `CONCURRENTLY`, so no write lock. **Risk: it fails if historical duplicates already exist** — de-dup SQL is in the migration header comment.
- No ride-state-machine, money, wallet, dispatch, or insurance-period code paths are touched. No background loop changed.

**Could this regress a working flow?** The `enabled: appCheckReady` gate means the inbox now shows a spinner for up to ~1 s longer on cold start before its first request. If `isAppCheckTokenReady()` were to hang, the 10 s cap releases the query anyway.

## 5. User-experience effect

- **Driver**: bell badge and inbox list now agree. A failed load says so and offers Retry instead of falsely claiming "all caught up". L&F notifications open the chat.
- **Rider**: same error/empty distinction; L&F and chat notifications now navigate.
- **Visible mid-session?** Yes — a driver already online will see the new inbox error/loading states. No ride, earnings, or money-facing behavior changes.
- **Copy change**: three new strings (`loadFailed`, `loadFailedBody`, `retry`) added in en/fr/es. Non-technical and actionable ("Check your connection and try again." / "Retry"). The rider app's equivalents are inline English, matching that screen's existing un-translated copy.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/lost_and_found.py` | Duplicate-case guard + `DuplicateRecordError` catch | Stop duplicate cases; close the TOCTOU race |
| `backend/migrations/328_lost_and_found_ride_driver_unique.sql` | New partial unique index | DB-level guarantee behind the app-level check |
| `backend/tests/test_lost_and_found_route_coverage.py` | Updated 2 mocks; added early-return test | Cover the new idempotency path |
| `shared/hooks/queries/notificationQueries.ts` | App Check gate + bounded retry | Stop the cold-start 401 emptying the inbox |
| `driver-app/app/driver/notifications.tsx` | L&F routing, icons, loading/error states | Root fix for "6 unread, list empty" |
| `driver-app/app/driver/lost-and-found-chat.tsx` | Existing-case lookup, 10 s polling | Reopen instead of re-report; live messages |
| `driver-app/i18n/{en,fr,es}.json` | 3 new keys | Error-state copy |
| `driver-app/__tests__/screens/notifications.test.tsx` | 2 new regression tests | Lock in error ≠ empty |
| `rider-app/app/notifications.tsx` | Routing, non-blocking mark-read, error state | Tap navigates; failures visible |
| `rider-app/app/lost-and-found-chat.tsx` | 10 s polling | Live messages |

## 7. Before / after

```tsx
// Before — a failed fetch and an empty inbox render identically
const { data, isFetching, refetch } = useNotifications(50);
...
ListEmptyComponent={
  <View style={styles.emptyState}>
    <Text>{t('notifications.noNotifications')}</Text>
    <Text>{t('notifications.allCaughtUp')}</Text>
  </View>
}
```

```tsx
// After — loading, error, and empty are three distinct states
const { data, isFetching, isPending, isError, refetch } = useNotifications(50);
...
ListEmptyComponent={
  isPending ? <ActivityIndicator />
  : isError ? (
      <>
        <Text>{t('notifications.loadFailed')}</Text>
        <TouchableOpacity onPress={onRefresh}>
          <Text>{t('notifications.retry')}</Text>
        </TouchableOpacity>
      </>
    )
  : <Text>{t('notifications.allCaughtUp')}</Text>
}
```

```ts
// Before — fires immediately; a cold-start App Check 401 is terminal (retry:false)
export const useNotifications = (limit = 50) =>
  useQuery({ queryKey: [...], queryFn: ..., staleTime: 30_000 });

// After — waits for the App Check token, retries a bounded number of times
export const useNotifications = (limit = 50) => {
  const appCheckReady = useAppCheckReady();
  return useQuery({
    queryKey: [...], queryFn: ...,
    enabled: appCheckReady, staleTime: 30_000,
    retry: 2, retryDelay: (a) => Math.min(1_000 * 2 ** a, 8_000),
  });
};
```

## 8. Rollback plan

- **Frontend (both apps)**: no migration, no persisted state, no flag. `git revert` is a complete rollback — these are render-path and query-config changes only. Requires a redeploy/OTA; acceptable because nothing writes data.
- **Backend route change**: `git revert` is complete — the guard is a read plus an early return; it writes nothing new.
- **Migration 328**: rollback SQL is in the file header — `DROP INDEX CONCURRENTLY IF EXISTS lost_and_found_ride_driver_uniq;`. Dropping the index is safe at any time; the application-level guard still prevents the common duplicate path. The index creates no rows and mutates no data, so there is no data-level remediation to plan.

## 9. Verification performed

- [x] Automated tests run — backend `pytest tests/test_lost_and_found_route_coverage.py tests/test_lost_found.py` (**36 passed**, incl. the new idempotency test); driver-app `jest` (**531 passed, 63 suites**, incl. 2 new inbox regression tests); rider-app `jest` (**523 passed**, 1 pre-existing flake — see below).
- [x] `tsc --noEmit` clean on driver-app and rider-app.
- [x] `eslint` clean on all changed `.tsx` files (caught and fixed two `eslint-disable` comments I had removed in the earlier commit).
- [x] **Real production build run**: `npx expo export --platform android` succeeded for **both** driver-app (8.8 MB bundle) and rider-app (8.6 MB bundle). Not a dev server, not `tsc` alone.
- [x] Blast-radius grep performed — searched `useNotifications`, `isAppCheckTokenReady`, `/notifications` route registrations (checked for shadowing), `lost_and_found` migrations, and all `driver_report_found_item` callers.
- [x] Reviewed against `CLAUDE.md` conventions — migration naming/append-only/CONCURRENTLY/rollback comment; "do not silently swallow errors" (this change *removes* a silent swallow); no money/state-machine/RLS surface touched.
- [ ] Feature-flagged — **not** flagged. Justification: the changes are additive UI states and a query gate on a single screen; a flag would add a second code path to an already-broken surface. The rollback above is a clean revert.

## 10. What was NOT verified

State it plainly rather than letting silence imply coverage:

- **Not tested against live Supabase or a real App Check token.** The 401 root cause was established by reading `core/middleware.py`'s exempt list, the `isAppCheckTokenReady` contract, and `queryClient`'s `retry: false` — it was **not** reproduced against a live device with App Check enforcement on. The error/empty split is correct regardless of cause; the App Check gate is the part that rests on inference.
- **Migration 328 has not been applied anywhere.** It has not been run against production or staging, so the "no pre-existing duplicates" assumption is **unverified** — if duplicates exist the migration will fail on apply and the de-dup SQL in its header must be run first. This should be checked before deploy.
- **No visual/snapshot regression tooling exists for these surfaces** (standing gap), so the new loading/error/retry states were reasoned about and unit-tested by text content, not screenshotted.
- **The 10 s polling interval was not load-tested.** With many concurrent open L&F chats it adds one request per chat per 10 s; this is well under any stated SLA but was not measured.
- **Defect 5's fix was not confirmed end-to-end by the reporter** at the time of writing — the driver should re-check that the bell badge and inbox now agree on a cold app start.
