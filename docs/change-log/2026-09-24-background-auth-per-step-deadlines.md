# Change Impact & Risk Log — background token refresh's shared deadline let one slow step starve the next

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-24 |
| Author | Claude Code session (daily `/sentry-triage --severity-only` scan) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | auth |
| PR / commit link | (this branch, `fix/background-auth-per-step-deadlines`) |
| Related issue or gap ID | Sentry CRIMSON-SMOKE-7445-121 |

## 1. Issue / gap identified

`Error: Background authentication deadline exceeded` fired for a driver's background token
refresh, correlated (same session, trace_id, and driver_id, 13 seconds apart) with
CRIMSON-SMOKE-7445-120 (a separate SQLite contention bug, already fixed on `main`). The
refresh failed outright rather than completing, even though the failure wasn't obviously
caused by any single step being pathologically slow.

## 2. Root cause

`createBackgroundTokenProvider` (`driver-app/utils/backgroundAuth.ts`) raced three sequential
async steps — Firebase App Check, the `/api/v1/auth/refresh` POST, and `response.json()` —
against ONE shared 10-second deadline created once at the top of the block. If App Check took
most of that 10s, the POST inherited whatever time was left, which could be far too little for
a real network round trip — so the whole refresh could fail even though neither step
individually was unreasonably slow. This composition bug is a plausible contributor to the
correlated incident (the same session's SQLite contention, per -120, could itself have added
delay before this code path even started racing).

## 3. Fix / remediation

Per an explicit choice made with the user (via `AskUserQuestion`, offered against the
investigator's own primary recommendation of starting the shared clock before the session
lock instead): each of the three steps now gets its own independent deadline via a
`withStepDeadline<T>(promise, ms)` helper, rather than sharing one clock.

**First attempt was wrong, caught before committing:** an initial split (5s/4s/1s) merely
divided the *original* 10s total across the three steps. A git-stash regression test proved
this couldn't actually fix anything — any per-step duration that fit under the smaller new
caps would also have fit the old shared 10s budget, so the fix changed nothing observable.
Corrected to give each step room close to its own realistic independent budget instead: App
Check 8s, the POST 8s, `response.json()` 2s. Worst-case total is 18s, chosen to stay
comfortably under iOS's documented ~30s background-execution ceiling even with the
session-lock wait (itself bounded by a separate 10s deadline in the already-fixed
`nativeSessionLock.ts`) and other overhead added on top — this repo's own investigation
explicitly flagged naively raising a shared timeout as a real risk of exceeding that ceiling,
and this fix avoids the same mistake in the per-step version.

The regression test (`gives the refresh POST its own full deadline instead of inheriting
whatever App Check left behind`) simulates App Check taking 7s (within its own new 8s budget,
but would have left only 3s of the old shared 10s pool) followed by a 5s POST (exceeds that
old 3s leftover, fits easily in the new independent 8s budget). Confirmed via `git stash` on
just `backgroundAuth.ts` that this test fails (returns `null`) without the fix and passes
(returns the new token) with it.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `createBackgroundTokenProvider`'s internal deadline
  composition.** No change to the function's external behavior for the common case (all
  three steps fast) — same three `await` calls in the same order, same abort-on-timeout
  wiring via the same shared `AbortController`.
- Removed the old single `finally { clearTimeout(timeout!); }` in favor of each
  `withStepDeadline` call's own `.finally(() => clearTimeout(timeout))` — equivalent cleanup,
  now scoped per-step instead of once at the end, verified by all existing tests passing
  (no leaked-timer warnings/flakiness observed).
- Worst-case total time increases from 10s to up to 18s if every step is maximally slow —
  a deliberate tradeoff (per the user's chosen approach) to stop one slow-but-tolerable step
  from starving the next, weighed against iOS's ~30s background-execution ceiling with margin.

## 5. User-experience effect

Driver-facing: background token refresh should now survive a slow-but-reasonable App Check or
network step without failing the whole chain, as long as no single step exceeds its own
generous budget. Not visible mid-session as a UI change — a background-reliability fix.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/utils/backgroundAuth.ts` | Single shared 10s deadline replaced with independent per-step deadlines (App Check 8s, POST 8s, json 2s) via a new `withStepDeadline` helper | Stop one slow step from starving the others |
| `driver-app/__tests__/utils/backgroundAuth.test.ts` | 2 existing timing tests updated to the new 8s-per-step values; 1 new regression test added | Pin the fixed behavior; the old shared-clock assumption no longer holds |

## 7. Before / after

```ts
// Before
const controller = new AbortController();
let timeout: ReturnType<typeof setTimeout>;
const deadline = new Promise<never>((_resolve, reject) => {
  timeout = setTimeout(() => { controller.abort(); reject(new Error('Background authentication deadline exceeded')); }, 10_000);
});
try {
  const appCheck = await Promise.race([initFirebaseServices().then(() => getAppCheckToken()), deadline]);
  const response = await Promise.race([fetch(...), deadline]);
  ...
  const data = await Promise.race([response.json(), deadline]);
  ...
} finally { clearTimeout(timeout!); }

// After
const controller = new AbortController();
const withStepDeadline = <T>(promise: Promise<T>, ms: number): Promise<T> => {
  let timeout: ReturnType<typeof setTimeout>;
  const deadline = new Promise<never>((_resolve, reject) => {
    timeout = setTimeout(() => { controller.abort(); reject(new Error('Background authentication deadline exceeded')); }, ms);
  });
  return Promise.race([promise, deadline]).finally(() => clearTimeout(timeout));
};
try {
  const appCheck = await withStepDeadline(initFirebaseServices().then(() => getAppCheckToken()), 8_000);
  const response = await withStepDeadline(fetch(...), 8_000);
  ...
  const data = await withStepDeadline(response.json(), 2_000);
  ...
}
```

## 8. Rollback plan

`git revert` is a complete rollback — no data migration, no config/flag, no schema change.
Reverting returns to the prior (already-affected-by-this-bug, not newly-broken) shared-deadline
behavior.

## 9a. Adversarial review

`spinr-realtime-reliability-reviewer` reviewed the diff before commit. Verdict: **SHIP WITH
SMALL CHANGE**. Found a real doc/code mismatch: the code's comment claimed an 18s worst-case
total (8+8+2s) but `response.json()`'s deadline was actually `1_000` not `2_000` — corrected
by bumping it to `2_000` to match. Also flagged (non-blocking, documented rather than fixed):
stacking this fix's ~17-18s step budget with `nativeSessionLock.ts`'s own independent 10s
lock-wait deadline yields a true worst case of ~27-28s against iOS's ~30s
background-execution ceiling — a thin (~2-3s) but deliberate margin, since the per-step
approach was chosen over the investigator's alternative ("start one shared clock before the
lock", which would never stack two independent ceilings) per an explicit user decision. Noted
directly in the code comment for the next reader. All other checks (abort-callback inertness
outside the POST step, timer-cleanup equivalence after removing the old shared `finally`, new
regression test's timing math, fast-path behavior unchanged) passed clean.

## 9. Verification performed

- [x] Automated tests run — `npx jest __tests__/utils/backgroundAuth.test.ts
  __tests__/utils/nativeSessionLock.test.ts` (24 passed)
- [x] Regression proof — the new test confirmed to FAIL without the fix (`git stash` on
  `backgroundAuth.ts` alone, returns `null` instead of the expected token) and PASS with it
- [ ] Manual repro steps followed in staging/device — not performed; no physical device or
  staging environment access in this sandboxed session
- [x] Blast-radius grep performed — `createBackgroundTokenProvider`'s only export is
  `renewBackgroundAuthToken`, used by the headless background task registration; no other
  caller depends on the old shared-deadline timing internals
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — background-loop/auth reliability
  on a live-tested surface; dispatched `spinr-realtime-reliability-reviewer` before commit

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow — the common (all-steps-fast) case
  is unchanged; only the degenerate-timing case's outcome changes, which is the point of the fix

## What was NOT verified

- Not tested against a real physical device or real Firebase App Check/network timing —
  verified via the existing mocked fake-timer test harness only.
- Whether 8s/8s/2s is the objectively "correct" split for real-world App Check/network
  latency distributions was not measured against production timing data (none available in
  this sandboxed session) — chosen as a reasoned default that fixes the demonstrated
  starvation pattern while staying under the ~30s background-execution ceiling, not derived
  from real latency percentiles.
