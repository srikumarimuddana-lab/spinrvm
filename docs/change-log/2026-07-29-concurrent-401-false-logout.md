# Change Impact & Risk Log — concurrent 401 falling through to the G2 hard logout

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code (session: driver sign-out investigation) |
| Surface(s) | `shared/` (consumed by driver-app + rider-app) |
| Domain (Sentry tag) | `auth` |
| PR / commit link | _(pending — subtask 3 of 6)_ |
| Related issue or gap ID | Driver-app frequent sign-out, **root cause 2 of 3 — this is the one that actually signs drivers out** |

## 1. Issue / gap identified

Drivers are signed out of the driver app frequently while riders stay signed in
for weeks. Root causes 1 (missing `expires_in`) and 3 (reuse-cascade blast
radius) contribute; **this is the defect that performs the sign-out.**

Two concurrent requests to the same `method + url` that both receive a 401: the
first starts a silent refresh, and the second is signed out by the G2 backstop —
while the first request's refresh is succeeding.

## 2. Root cause

`handleApiError` bounds refresh-retries to one per logical request path via
`_inflight401Retries`, keyed on `` `${method} ${url}` ``. The silent-refresh
branch is gated on `!_inflight401Retries.has(key)`:

```ts
if (response.status === 401 && _refreshCallback && retryFn && !isSosUrl(url)
    && !_inflight401Retries.has(`${method} ${url}`)) {
  refreshAttempted = true;          // ← only set INSIDE the branch
```

When a second concurrent request hits the same key, the whole branch is skipped,
so `refreshAttempted` stays `false` — and 150 lines later:

```ts
if (response.status === 401 && !isSosUrl(url) && !refreshAttempted) {
  useAuthStore.getState().logout();     // G2 backstop
}
```

G2's documented intent is "backstop ONLY for 401s where no silent refresh could
be attempted (no `_refreshCallback` at cold start, or no `retryFn`)". A skipped
*duplicate* is not that case, but the `refreshAttempted` flag could not tell the
two apart.

**Why this is driver-specific.** The driver app fires duplicate identical
requests on essentially every resume — `initialize()`, `refreshProfile()`, and
the TanStack refetch-on-focus all hit `GET /auth/me` and `GET /drivers/me`
simultaneously — at exactly the moment its access token is stale. Root cause 1
guaranteed staleness on resume by disabling proactive refresh. The rider app
rarely has two identical requests in flight, so it never tripped it.

**This is a regression of a failure mode already fixed once.** The docblock on
`rider-app/__tests__/api-client-401-refresh.test.ts` records the earlier round:
G2 used to hard-sign-out a driver whose refresh failed *transiently*, and
`refreshAttempted` was introduced to stop it. That fix covered the
transient-failure path into G2 and left the concurrency path into G2 open.

**Why CI never caught it.** `shared/api/__tests__/client.refresh.test.ts`
contains a test — "deduplicates concurrent refresh calls — only one refresh
in-flight" — that fires `api.get('/rides/active')` twice concurrently and
therefore exercises this exact path. It has been **failing**, invisibly, because
that entire directory is run by no jest project and no CI job (see §9, and the
follow-up task). Its `console.log` output before this fix contained the smoking
gun verbatim:

```
[API-ERR] GET /rides/active → 401 | Token expired | req=req-001
[API] 401 Unauthorized — clearing session
[API] In-memory token: CLEARED
```

That line is absent after the fix.

## 3. Fix / remediation

Hoist the retry key and set `refreshAttempted = true` when the refresh branch is
skipped *because a refresh for this exact path is already in flight*:

```ts
const refreshRetryKey = `${method} ${url}`;
if (response.status === 401 && !isSosUrl(url) && _inflight401Retries.has(refreshRetryKey)) {
  refreshAttempted = true;
}
```

The losing request still rejects with its original 401 — every caller already
handles that. What changes is that the *session* survives, and the logout
decision belongs entirely to whoever owns the in-flight refresh
(`refreshTokens`), which logs out on a definitive rejection and keeps the session
on a transient one.

**Deliberately NOT done:** making the second request subscribe to the in-flight
refresh and retry, so it succeeds instead of failing. That is better UX but
reintroduces the re-entrancy risk `_inflight401Retries` exists to prevent — an
endpoint that persistently 401s for a non-expiry reason (suspended user, role
mismatch) while `/auth/refresh` succeeds can loop (see the comment at its
declaration). On a live-tested auth surface the minimal diff is the right trade;
the upgrade is a separate, testable change.

## 4. Risk & impact on existing functionality

**Blast radius: `shared/api/client.ts::handleApiError` is on the error path of
every authenticated request in both mobile apps.** The change is a single
additional `if` in that error path. It cannot affect any 2xx response, and it
cannot affect a non-401 error.

What could regress, and why it does not:

- **G2's legitimate cold-start backstop.** Still fires. The new guard only
  triggers when `_inflight401Retries` holds the key, which requires a prior
  request to have entered the refresh branch, which requires `_refreshCallback`
  to be registered. At cold start it is not, so no key is ever added.
  Pinned by the pre-existing test "G2 backstop still clears the session when no
  refresh callback is registered (cold start)".
- **A genuinely dead session no longer being torn down.** It still is, by
  `refreshTokens`, exactly once. Verified by a new test: before the fix, a
  definitive rejection with two concurrent requests called `logout` **twice**
  (once correctly by `refreshTokens`, once spuriously by G2); after, once.
- **SOS.** Untouched — the new condition carries the same `!isSosUrl(url)`
  exemption, so an emergency request still never triggers a sign-out.
- **The 503 retry path** below it declares its own `retryKey`; the hoisted
  constant is named `refreshRetryKey` specifically to avoid shadowing it.

Not touched: ride state machine, money/wallet arithmetic, the 16 background
loops, RLS, migrations, WebSocket events. No PII, no new logging.

**Direction of risk is toward keeping sessions alive.** If the guard were ever
wrong, the failure mode is "a session that should have been cleared stays until
the next definitive refresh rejection" — not "a user is signed out
unexpectedly". Given a live driver fleet, that is the correct direction to err.

## 5. User-experience effect

- **Driver:** this is the fix that stops the sign-outs. A driver whose app
  resumes with a stale token now gets a quiet refresh instead of being bounced to
  the OTP screen — including mid-shift, while online, potentially mid-ride.
- **Rider:** same protection; rarely reached in practice.
- **Corporate / internal admin:** none — neither uses this client.
- **Visible mid-session:** yes, and that is the point. No UI, copy, or
  notification change. One request that previously triggered a sign-out now
  simply fails, and its caller's existing error handling applies.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/api/client.ts` | Hoisted `refreshRetryKey`; added a guard that sets `refreshAttempted = true` when the refresh branch is skipped due to an in-flight retry for the same path; main branch and its `finally` reuse the hoisted key | Stop a skipped duplicate 401 from reaching the G2 backstop and signing the user out mid-refresh |
| `rider-app/__tests__/api-client-401-refresh.test.ts` | Added 2 cases: concurrent 401 on the same URL must not log out (and the winner must still recover); definitive rejection with concurrent duplicates must log out exactly once | Pin both directions — the fix must not suppress a warranted logout. Placed here because this suite actually runs in CI |

## 7. Before / after

```ts
// Before
if (response.status === 401 && _refreshCallback && retryFn && !isSosUrl(url)
    && !_inflight401Retries.has(`${method} ${url}`)) {
  refreshAttempted = true;
  const retryKey = `${method} ${url}`;
  ...
// second concurrent request with the same key: branch skipped,
// refreshAttempted stays false → G2 below calls logout()
```

```ts
// After
const refreshRetryKey = `${method} ${url}`;

if (response.status === 401 && !isSosUrl(url) && _inflight401Retries.has(refreshRetryKey)) {
  refreshAttempted = true;   // hand the logout decision to the in-flight refresh
}

if (response.status === 401 && _refreshCallback && retryFn && !isSosUrl(url)
    && !_inflight401Retries.has(refreshRetryKey)) {
  refreshAttempted = true;
  const retryKey = refreshRetryKey;
  ...
```

## 8. Rollback plan

`git revert` of this diff, then an app build (OTA update or EAS). **Not instant** —
this is client code, like subtask 2.

No feature flag, no `app_settings` value, no migration. Nothing is written to the
database; the change is one branch in an in-memory error path.

**Not feature-flagged** (gate #3): the `app_settings` flag mechanism is an
authenticated HTTP read, so gating the 401-recovery path behind it is circular —
the flag lookup would itself need a valid token to decide how expired tokens are
handled, and would 401 into the very code path being gated. Stated rather than
silently skipped.

Mitigating the non-instant rollback: the change is 4 lines in one error path,
with the failure direction biased toward keeping sessions alive (§4), and both
directions are covered by tests.

## 9. Verification performed

- [x] **Failing test first.** Both new cases were written before the fix and
      confirmed RED:
      - concurrent 401 → `expect(mockLogout).not.toHaveBeenCalled()` →
        **"Received number of calls: 1"**
      - definitive rejection → `expect(mockLogout).toHaveBeenCalledTimes(1)` →
        **"Received number of calls: 2"**
      After the fix: **6/6 passed** in that suite.
- [x] **Independent corroboration from the orphaned suite.** The pre-existing
      "deduplicates concurrent refresh calls" test's output contained
      `[API] 401 Unauthorized — clearing session` before the fix and **zero**
      occurrences after (grep-counted). That test still fails, but now only on its
      own harness bug — it awaits a timer before attaching `Promise.allSettled`,
      so the losing request's rejection is briefly unhandled and Node reports
      `unhandledRejection`. Tracked in the follow-up task, not fixed here.
- [x] **Full rider-app suite** — `npx jest --ci --forceExit` → 48/51 suites,
      **432/436 tests passed**. The 4 failures all pass on `--onlyFailures`
      re-run (**12/12 passed**) → flaky under parallel load, not regressions.
- [x] **Full driver-app suite** — `npx jest --ci --forceExit` → 43/45 suites,
      **328/330 tests passed**. Both failures triaged against HEAD (see below);
      neither is caused by this diff.
- [x] **Failure triage done by reverting, not by assertion.** Both changed
      `shared/` files were replaced with their `git show HEAD:` versions and the
      two failing driver suites re-run:
      - `onlineResync.test.ts › toggleOnline sets and clears the toggle guard
        around its request` — **fails on HEAD too** ⇒ pre-existing. Filed as its
        own task (gate #8: a permanently-red gate is decay, not "not my problem").
      - `ActivityView.test.tsx › keeps ride history visible when earnings loading
        fails` — passes on HEAD, and passes **twice in a row standalone with this
        diff applied** (6/6 each time). It took 68 s inside the full parallel run
        versus ~20 s standalone ⇒ resource-starvation flake, not a regression.
      Both `shared/` files were then restored from byte-for-byte backups and
      re-verified by grepping for the fix markers (`refreshRetryKey` ×4,
      `usableTtlSeconds` ×3) and re-checking `git diff --stat`.
- [x] **`npx tsc --noEmit`** — driver-app: exit 0, zero diagnostics. rider-app:
      exit 0, zero diagnostics.
- [x] **Real production bundle run** — `npx expo export --platform web`
      (driver-app) → **exit 0**, bundle emitted. Production Metro/babel pipeline,
      not a dev server and not `tsc --noEmit`.
- [x] **Blast-radius grep performed** — `_inflight401Retries`, `refreshAttempted`,
      `_signOutCallback|setSignOutCallback|\.logout\(\)` across `shared`,
      `driver-app`, `rider-app`; plus `retryKey` to confirm the 503 path's own
      declaration is not shadowed.
- [x] **Reviewed against `CLAUDE.md` conventions** — the "do not silently swallow
      auth errors" rule is respected: nothing is softened. The 401 still
      propagates to the caller as a `SpinrApiError`; only the *side effect* of
      tearing down an unrelated healthy session is removed. No new logging, no
      PII, no money arithmetic, no state machine, no RLS, no migration.
- [x] **Feature-flag decision justified** — see §8.

### What was NOT verified

- **`shared/api/__tests__/` is run by no jest project and no CI job.** Confirmed:
  `driver-app` jest lists 45 test files, **zero** matching "shared"; `rider-app`
  reports `0 matches` for that path; the root `package.json` has no `scripts`
  block; `ci.yml` runs `yarn test` per app; `pr-checks.yml` only *detects* that
  `shared/` was touched for labelling. Three auth suites have been rotting there.
  **This is a standing gate-decay finding (gate #8)** and is filed as its own
  task — it is not fixed by this commit, so this diff does not restore coverage
  for `client.sos.test.ts` or `client.authHeader.test.ts`.
- **`shared/utils/__tests__/pii.test.ts` "redactCoords rounds to one decimal
  place" also fails** in that orphaned run. Pre-existing, unrelated to this diff,
  triaged into the same follow-up task. It is a PIPEDA-adjacent test, so it should
  not sit red for long.
- **No real-device or staging confirmation** that a driver's resume no longer
  signs them out. That is the check that would prove §5, and it needs an installed
  build against a deployed backend. The mechanism is proven at unit level only.
- **The subscribe-and-retry upgrade was not implemented** (§3), so the losing
  duplicate request still fails. Callers surface an error or retry; no session
  impact.
- **No native build.** See the note in
  `2026-07-29-client-token-expiry-derivation.md` — `expo export --platform web`
  exercises the production Metro/babel pipeline but not Hermes or native modules.
- **One pre-existing driver-app test failure is left red** —
  `onlineResync.test.ts › toggleOnline sets and clears the toggle guard around its
  request`. Confirmed pre-existing by reverting, not fixed here (out of scope for
  an auth diff, and it concerns the `is_online` flip, so it deserves its own
  look — the `is_available ⇒ is_online` invariant is in play). Filed as a task.
- **Suite flakiness under parallel load was observed but not fixed.** Four
  rider-app and one driver-app test fail in a full parallel run and pass on
  re-run. This is a real hazard for this kind of work: a genuine regression can
  hide inside that noise. Noted in the same task.
- **Root cause 3 remains open** (reuse-cascade blast radius, subtask 5). A driver
  whose rotation response is lost in a coverage dead zone for more than the grace
  window still gets an all-device cascade.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — and explicitly flagged as
      requiring an app build, not a deploy
- [x] Blast radius is stated, not assumed — every path into G2 enumerated, with
      the cold-start backstop and SOS exemption confirmed still intact by test
- [x] No silent behavior change to an already-shipped flow without the UX field
      filled in — §5 states the mid-session effect; §4 names the direction of
      residual risk rather than claiming there is none
