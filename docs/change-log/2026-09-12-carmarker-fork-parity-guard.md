# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Author | Claude Code (audit follow-through, roadmap item R11) |
| Surface(s) | shared (test-only; no production code changed) |
| Domain (Sentry tag) | rides |
| PR / commit link | srikumarimuddana-lab/spinrvm#5290 |
| Related issue or gap ID | `docs/audit/ride-experience/ROADMAP.md` R11 (option a); `ACTION_ITEMS.md` C101; `docs/known-forks.md` |

## 1. Issue / gap identified

`shared/components/CarMarker.tsx` and `driver-app/components/CarMarker.tsx` are an intentional
fork with no mechanism catching a missed one-way port. Five fixes have needed manual porting so
far (three tracked by C90, two more found and fixed by the 2026-09-12 audit in this same PR) —
the same shape as the `float()`-on-`NUMERIC` bug closed piecemeal five times across B28→B36 with
no systemic fix.

## 2. Root cause

No test or lint rule ever compared the two files' capability surface. A header comment (R6,
already shipped) helps a human notice the fork exists, but a comment is not a guard — nothing
previously failed a build when the two files silently diverged.

## 3. Fix / remediation

**Decision (user-confirmed 2026-09-12): keep the two files, do not merge into one parameterized
component.** Added a mechanical parity guard —
`shared/components/__tests__/CarMarkerParity.test.ts` — that reads both files' `CarMarkerProps`
interface as source text (no component render, no react-native-maps/expo-image mocking needed)
and fails if:
- `shared/CarMarker.tsx` declares any prop outside its 12-prop common contract, or
- `driver-app/CarMarker.tsx` is missing any of those 12, or
- `driver-app/CarMarker.tsx` has an extra prop not in the reviewed `DRIVER_ONLY_PROPS` allowlist
  (`onBearingChange`, `mapHeadingRef`, `isOnline`, each with a stated reason).

Deliberately scoped to the **public prop API**, not internal implementation logic — a
mechanically diffable, cheap-to-maintain surface, matching the roadmap's own "Effort S–M"
estimate. An internal-only divergence (like R1/R3's route-rebase and Android-rotation fixes)
still needs a human to notice, which is exactly why R6's header comments (already shipped) point
each file at the other and at `docs/known-forks.md`.

## 4. Risk & impact on existing functionality

- **No production code changed.** This commit adds one test file and updates two documentation
  files (`docs/known-forks.md`, `ACTION_ITEMS.md` C101) to record that the guard now exists.
- **What else reads the same files:** the test reads `shared/components/CarMarker.tsx` and
  `driver-app/components/CarMarker.tsx` as plain text via Node's `fs` — it does not import, mock,
  or execute either component, so it cannot affect their runtime behavior.
- **Blast radius:** isolated. The only way this test can ever fail a future PR is if that PR
  itself changes one file's `CarMarkerProps` interface without a corresponding, reviewed change
  to the other or to the test's own allowlists — which is precisely the intended effect.
- **CI wiring:** confirmed (not assumed) that this test actually runs — `rider-app/jest.config.js`
  already sets `roots: ['<rootDir>', '<rootDir>/../shared']` (added 2026-08-24 per `ACTION_ITEMS.md`
  C42-B specifically so `shared/**/__tests__` isn't orphaned), so this new file under
  `shared/components/__tests__/` is picked up by the existing `rider-app` CI test step with no
  workflow change needed. Verified locally: `npx jest ../shared/components/__tests__/CarMarkerParity.test.ts`
  from `rider-app/` — 4/4 pass.
- **Adversarial review finding, fixed before commit:** `/code-review` (medium effort) found the
  guard itself was correct but `docs/known-forks.md`'s registry row and `ACTION_ITEMS.md` C101
  still said the guard was "not yet done" — a future engineer following CLAUDE.md's mandatory
  "check the sibling file named [in known-forks.md]" step would have been told no guard exists
  when one now does. Both docs updated in this commit.

## 5. User-experience effect

None — test-only change, no rider/driver/admin-facing behavior difference.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/components/__tests__/CarMarkerParity.test.ts` (new) | Mechanical parity guard: diffs both CarMarker files' prop interface, fails on undeclared divergence. | R11 |
| `docs/known-forks.md` | Registry row's "Parity guard" column updated from "not yet" to link the new test. | Adversarial-review fix |
| `ACTION_ITEMS.md` | C101's "Not yet done" line updated to record the guard landing. | Adversarial-review fix |

## 7. Before / after

Not applicable in the usual sense (no existing behavior changed) — this is additive-only. The
before/after that matters is the guard's own effect, demonstrated live during verification: with
a test-only injected extra prop on the driver-app side, the new test correctly failed with a
clear diff (`+ "testOnlyUndeclaredProp?"`); reverted, all 4 tests pass.

## 8. Rollback plan

`git revert` — a test file and two doc updates, no data, no migration, no runtime code.

## 9. Verification performed

- [x] Automated test: ran the new test file directly (`npx jest ../shared/components/__tests__/CarMarkerParity.test.ts`
      from `rider-app/`) — 4/4 pass.
- [x] Verified the guard actually guards: temporarily injected an undeclared prop into
      `driver-app/components/CarMarker.tsx`, re-ran the test, confirmed it failed with the exact
      expected diff, then reverted via `git checkout --` and confirmed the file matched the
      committed state again (`git diff` empty).
- [x] Confirmed CI wiring (not assumed) — traced `rider-app/jest.config.js`'s `roots` config and
      its own `ACTION_ITEMS.md` C42-B comment explaining why it was added.
- [x] Adversarial pre-implementation review: alternative considered (per the user's own decision)
      was a full merge into one parameterized component — rejected in favor of the two-file
      + guard approach, matching the roadmap's own "Recommended now" and avoiding a larger
      refactor touching 5 rider-app screens + the driver dashboard simultaneously.
- [x] Adversarial post-implementation review: `/code-review` (medium effort) — found two stale
      documentation cross-references, both fixed in this commit; no code-level issues.
- [ ] Feature-flagged — not applicable (test-only, no runtime behavior).

### What was NOT verified

- This guard covers the prop-level API surface only. It does not and cannot (via a source-text
  diff) catch a future internal-logic-only divergence between the two files' implementations —
  that class of gap still relies on a human reading the header comments R6 already added.
- Not verified under `driver-app`'s own test run — `driver-app/jest.config.js` has no `roots`
  override reaching `shared/`, so this test is exercised only via `rider-app`'s CI step. This
  matches the existing, documented pattern for every other `shared/components/__tests__/*` file
  (none of them run under `driver-app`'s own suite either) — not a new gap this change introduces.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`).
- [x] Blast radius is stated, not assumed (isolated; verified CI wiring rather than assuming it).
- [x] No silent behavior change to an already-shipped flow (none — test-only).
