# Change Impact & Risk Log — admin pollers pause while the tab is hidden (W5.3, part a)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-26 |
| Author | Claude Code (agent session) |
| Surface(s) | admin-dashboard: staff dashboard, company portal, public `/track` page |
| Domain (Sentry tag) | admin |
| PR / commit link | Branch `claude/spinr-animations-admin-ux-x7nl5x`: setVisibleInterval plus three commits moving the pages onto it |
| Related issue or gap ID | UX enhancement program W5.3 (`.claude/plans/2026-09-25-ux-enhancement-program.md`), "pollers pause while the tab is hidden and refresh on focus". The shared `EmptyState` half of W5.3 is separate (part b). |

## 1. Issue / gap identified

Every admin polling loop kept running in a background tab. A dashboard left open all day polled the backend every 5–60 s for a screen no one was looking at, and nothing refreshed on return beyond the next scheduled tick.

## 2. Root cause

Each page used a bare `setInterval`. Browsers throttle timers in background tabs but don't stop them, and no page listened for `visibilitychange`. `grep` found no visibility handling anywhere in `admin-dashboard/src`.

## 3. Fix / remediation

- **New `lib/visible-interval.ts`:** `setVisibleInterval(callback, ms)` is a drop-in for `setInterval` that returns its own stop function.
  - While the page is visible it is a plain `setInterval`.
  - While hidden, the interval is cleared.
  - On return, `callback` runs at once if a full interval has passed since the last run. Otherwise it runs after the rest of that interval, so quick tab switches add no requests. The interval then resumes.
  - Like `setInterval`, it doesn't run immediately; the caller's own first load counts as the last run.
  - A callback that throws doesn't stop it.
  - Outside a browser it falls back to `setInterval`.
- **All 10 `setInterval` polling sites, in 8 files, moved to it.** Each effect's first load, dependencies and cleanup are otherwise unchanged.

| Site | Interval |
|---|---|
| Sidebar badge counts (`components/sidebar.tsx`), shown on every dashboard page | 60 s |
| Live-ride tracking (`dashboard/rides/live/[id]/page.tsx`) | 5 s |
| Company portal bookings (`company-portal/[id]/bookings/page.tsx`) | `POLL_MS` |
| Stripe events, only while auto-refresh is on | 30 s |
| Dispatch geo status | `STATUS_POLL_MS` |
| Redis & infra: stats, infra, WebSocket | 10 s each |
| Monitoring REST poll (`dashboard/monitoring/page.tsx`) | 5 min, faster while its WebSocket is down |
| Public tracking link (`track/[rideId]/page.tsx`) | 5 s |

- **Not changed:** the monitoring page's demand-overlay poll, and the heatmap page's demand poll. Both are jittered 2 min `setTimeout` chains, and `setVisibleInterval` would drop the jitter that spreads load when many tabs open at once.

**Alternatives considered (gate 10):**
- *A React hook (`usePolling`) that owns the first load too.* Rejected: sites differ in where their first load happens (same effect, separate effect, or behind a toggle). A hook would reshape each effect. The drop-in keeps every site's diff to one or two lines.
- *Pause and resume inside each page.* Rejected: ten copies of the same visibility logic.
- *A `setTimeout` chain inside the utility.* The first version did this. It broke the `/track` test harness, which fakes only `setInterval`. It also changed cadence semantics (fixed delay instead of fixed rate). Staying on `setInterval` while visible keeps it a true drop-in.

## 4. Risk & impact on existing functionality

- **Blast radius: admin-dashboard only; 8 files plus the new utility, which has no other importer.**
  - The sidebar is rendered only by `app/dashboard/layout.tsx`. The pre-commit hook's "~26 files" count is a word match, as the W5.1 log found.
  - The monitoring page and the sidebar appear on merge-blocking visual baselines. Nothing they render changes, so no baseline should move.
- **Nothing that must keep running in a hidden tab is paused:**
  - No poller drives a sound, a browser notification or the page title (checked by grep).
  - The monitoring page's live alert feed comes from its WebSocket, which keeps running while hidden.
  - **Session lifetime is unchanged in practice**, although polls do touch the session.
    - Every admin request refreshes `admin_staff.last_activity_at`, coalesced to once a minute (`backend/dependencies/__init__.py`). The server returns `ERR_IDLE_TIMEOUT` after 30 minutes without one.
    - The sidebar's 60 s badge poll therefore kept that timestamp fresh while any dashboard tab was open, even a hidden one. After this change it stops advancing while every dashboard tab is hidden.
    - The client already logs a tab out after 30 minutes without mouse, keyboard, touch or scroll in that tab (`store/authStore.ts`, F-19), and none of those can happen in a hidden tab. So a tab hidden for 30 minutes was already logged out before, and the server's check now matches it. A visible tab still polls, so the session stays fresh exactly as before.
    - Chrome can delay a hidden tab's timers by up to about a minute. The server may therefore answer the first request after a 30-minute absence with `ERR_IDLE_TIMEOUT` slightly before the client's own timer fires. The outcome is the same logout.
    - The company portal has no server-side idle timeout (grep of `backend/dependencies` and the corporate routes), so pausing its bookings poll ends no company session.
- **Visible difference on return:** data that was stale while the tab was hidden refreshes at once if a full interval has passed. Before, it waited for the next throttled tick.
- **Monitoring degraded mode:** while the WebSocket is down, the faster REST poll now also pauses in a hidden tab. An operator returning to the tab gets an immediate refresh if the interval has passed.
- **Public `/track`:** a rider's contact who leaves the link in a background tab stops polling the public endpoint, and the page refreshes on return. The marker glide (W4.2) snaps rather than glides after a long gap, as designed.
- **No backend, data, state-machine or money interaction.** No logging added.

## 5. User-experience effect

- **Who sees it:** internal admins, company-portal users and `/track` viewers, after deploy and a reload.
- There is no visible change while a tab is in front. Returning to a tab after a while refreshes it straight away.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/visible-interval.ts` (new) | The utility | Shared pause-while-hidden logic |
| `admin-dashboard/src/lib/__tests__/visible-interval.test.ts` (new) | 10 cases | Coverage |
| `admin-dashboard/src/components/sidebar.tsx` | Badge poll uses it | W5.3 |
| `admin-dashboard/src/app/dashboard/rides/live/[id]/page.tsx` | Poll uses it; unused `intervalRef`/`useRef` removed | W5.3 |
| `admin-dashboard/src/app/company-portal/[id]/bookings/page.tsx` | Poll uses it | W5.3 |
| `admin-dashboard/src/app/dashboard/stripe-events/page.tsx` | Auto-refresh uses it; unused `intervalRef`/`useRef` removed | W5.3 |
| `admin-dashboard/src/app/dashboard/monitoring/dispatch-geo/page.tsx` | Poll uses it | W5.3 |
| `admin-dashboard/src/app/dashboard/monitoring/redis/page.tsx` | Three polls use it | W5.3 |
| `admin-dashboard/src/app/dashboard/monitoring/page.tsx` | REST poll uses it | W5.3 |
| `admin-dashboard/src/app/track/[rideId]/page.tsx` | Status poll uses it | W5.3 |
| `docs/change-log/2026-09-26-admin-pollers-pause-when-hidden.md` (new) | This log | CLAUDE.md change-impact rule |
| `.claude/plans/2026-09-25-ux-enhancement-program.md` | Progress line | Program tracking |

## 7. Before / after

Before:

```tsx
load();
const t = setInterval(load, POLL_MS);
return () => clearInterval(t);
```

After:

```tsx
load();
return setVisibleInterval(load, POLL_MS);
```

## 8. Rollback plan

No feature flag: the plan lists W5.3 as "Gate: none", and the change sends fewer requests without rendering anything differently. Revert the commits and redeploy admin-dashboard, or promote the previous Vercel deployment. Nothing is written to live data.

## 9. Verification performed

- **Utility tests:** 10 cases with fake timers and a controlled `document.hidden`:
  - the interval runs while visible and stops while hidden;
  - on return, the catch-up run fires at once or after the remainder;
  - a tab that starts hidden;
  - a throwing callback on each path;
  - `stop()`;
  - no double schedule.
- **Mutation checks, each caught:**
  - starting the interval in a hidden tab;
  - not clearing it when hidden;
  - calling before starting the interval on the catch-up run;
  - the same on the delayed run.
- **Affected page tests** pass unchanged: sidebar, live ride, company portal, monitoring, and `/track` (whose harness fakes only `setInterval`).
- **Type check:** `npx tsc --noEmit` exit 0.
- **ESLint** on all changed files: the same findings as on `main`, shifted by a line at most. No new ones.
- **Full admin suite:** `npx vitest run`: 108 files and 923 tests passed (the 913 on `main` after #5898 plus these 10).
- **Production build:** `npm run build` (`next build`) exit 0, "Compiled successfully", 80/80 static pages.
- **Edge-case review** (`spinr-edge-case-reviewer`, on the full branch) found no blockers. It verified:
  - no timer or listener outlives an effect cleanup;
  - no double scheduling on repeated visibility events or a StrictMode double-mount;
  - every site returns the stop function as its cleanup;
  - no caller used the raw `setInterval` id;
  - the monitoring page's degraded-mode REST poll is safe to pause, because SOS and critical alerts arrive over the WebSocket.

  Its warnings and what was done with them are in §10.

## 10. What was NOT verified

- **No real browser.** Visibility changes were simulated in jsdom, and real background-tab throttling and bfcache restores weren't exercised.
- **Pages with no tests:** Stripe events, dispatch geo and Redis have no page-level tests. Their change is a one-line swap, covered by the utility's tests.
- **No load measurement.** The reduction in background requests was reasoned, not measured.
- **Overlapping fetches are still possible, as before.** No poll callback sequences or aborts its fetch.
  - If one fetch takes longer than the interval, the next run can overlap it, and an older response could land last. That was already true with `setInterval`.
  - The review raised it for the catch-up run on return. That run only fires once a full interval has passed since the last run, so a quick hide-and-return cannot trigger it.
  - Adding response ordering, such as an `AbortController` or a sequence number per page, is a possible follow-up, not part of this change.
- **Manual refreshes don't count as a run.** The Redis page's refresh button, and similar direct calls, don't update the utility's last-run time. A manual refresh just before hiding the tab can therefore be followed by one catch-up request on return.
- **Back/forward-cache restores** rely on the `visibilitychange` that browsers fire when a restored page becomes visible. This was not checked on a real browser. It matters most for the public `/track` page opened from an SMS link on mobile Safari.
- **Pre-existing, unchanged:** the live-ride page keeps polling after the ride ends. The `/track` page's same behaviour is already logged with W4.2.
