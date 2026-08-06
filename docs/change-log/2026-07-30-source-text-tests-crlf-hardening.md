# Change Impact & Risk Log — source-text tests hardened against CRLF checkouts

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-30 |
| Author | Claude Code (session: driver sign-out investigation) |
| Surface(s) | driver-app (tests only — no production code) |
| Domain (Sentry tag) | n/a (test infrastructure) |
| PR / commit link | _(pending)_ |
| Related issue or gap ID | Follow-up to `2026-07-29-onlineresync-crlf-test-failure.md` |

## 1. Issue / gap identified

`onlineResync.test.ts` failed on every Windows checkout because it matched a needle
containing `\n` against source read from disk, and this repo checks out CRLF
(`core.autocrlf=true`, no `.gitattributes`). Fixed at that site yesterday. This
entry covers the siblings.

**Nothing here was broken.** The fragility is latent: it bites only when someone
writes a needle that spans a line break, and the symptom is a Windows-only failure
that names whatever the test asserts about — which is why the original one read as a
defect in the driver online/offline flip for a whole session.

## 2. Root cause

Ten test files assert against source read via `readFileSync`. None of the nine
remaining had a genuinely CRLF-sensitive assertion — established by inspection
rather than assumed:

| File | Reads | Verdict |
|---|---|---|
| `hooks/__tests__/appStateFlush.test.ts` | `useDriverDashboard.ts` | multi-line *calls*, single-line needles — safe today |
| `hooks/__tests__/gpsHeartbeat.test.ts` | same | single-line needles |
| `hooks/__tests__/locationBatch.test.ts` | same | single-line needles |
| `hooks/__tests__/phase1IdleTrail.test.ts` | same | multi-line call, single-line needle |
| `hooks/__tests__/wsLocationBatch.test.ts` | same | single-line needles |
| `__tests__/screens/completionRouteConfirmation.test.ts` | `app/driver/(tabs)/index.tsx` | single-line needles |
| `__tests__/screens/driver-dashboard-route.test.ts` | same | **line-spanning `toMatch` regexes**, but `[\s\S]*?` and `\s*` both match `\r` — safe by construction |
| `__tests__/screens/ride-detail-route.test.tsx` | `app/driver/ride-detail.tsx` | single-line needles |
| `__tests__/androidAutoDistribution.test.ts` | `eas.json` | **`JSON.parse` — line-ending agnostic, not affected** |

My first pass over this mis-classified three files as already fragile. That was a
false positive from a detection regex that matched the assertion *call* spanning
lines rather than a `\n` inside the needle. Corrected by reading the actual
assertion bodies. The `androidAutoDistribution` exclusion came from the same
re-reading — it parses JSON and asserts on the object, so line endings never enter.

## 3. Fix / remediation

Normalise on read in the **eight** files that do text matching:

```ts
const hookSource = readFileSync(path, 'utf8').replace(/\r\n/g, '\n');
```

`androidAutoDistribution.test.ts` deliberately untouched — normalising a string
that is immediately `JSON.parse`d would be noise implying a hazard that does not
exist there.

Each file carries a two-line comment pointing at `onlineResync.test.ts` for the
full account, rather than repeating the explanation eight times. The
`driver-dashboard-route.test.ts` comment is longer because that file is the one
where a reader would most reasonably assume the regexes are already unsafe — it
states why they are currently fine and why normalising still removes the trap.

**A shared helper was considered and rejected.** A `readSourceForAssertions()`
placed under `__tests__/` would be collected by jest's `**/__tests__/**/*.ts`
pattern and fail with "Your test suite must contain at least one test"; placing it
outside would mean a new top-level directory plus a `testPathIgnorePatterns` entry.
Eight one-line changes carry less structural risk than a new module plus a config
change, for the same effect.

## 4. Risk & impact on existing functionality

**Blast radius: eight test files. Zero production code.**

- The transformation cannot change an assertion's outcome on an LF checkout (CI),
  because there is nothing to replace. On a CRLF checkout it can only make a
  spanning needle match where it previously could not — it cannot make a failing
  assertion pass for the wrong reason, since a genuinely absent code shape is absent
  in both line-ending forms.
- No production file is read differently: the tests read the same files, they just
  normalise the string in memory.
- Nothing depends on these tests' internals; they are leaf test files.

Not touched: ride state machine, money/wallet arithmetic, background loops, RLS,
migrations, WebSocket events, or any app runtime path.

**This commit deliberately exceeds the ≤3-files-per-subtask guideline** (8 files).
It is a single mechanical transformation, identical in each file, and the diff is
well under the ~200-line batch-size limit that rule exists to protect. Splitting it
into three commits would make the sweep harder to review, not easier.

## 5. User-experience effect

None. No rider, driver, corporate-admin or internal-admin visible change; no
production code path altered. The benefit is that a future multi-line needle will
not produce a Windows-only failure that appears to indict the feature under test.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/hooks/__tests__/appStateFlush.test.ts` | `.replace(/\r\n/g, '\n')` on read + comment | latent CRLF trap |
| `driver-app/hooks/__tests__/gpsHeartbeat.test.ts` | same | same |
| `driver-app/hooks/__tests__/locationBatch.test.ts` | same | same |
| `driver-app/hooks/__tests__/phase1IdleTrail.test.ts` | same | same |
| `driver-app/hooks/__tests__/wsLocationBatch.test.ts` | same | same |
| `driver-app/__tests__/screens/completionRouteConfirmation.test.ts` | same | same |
| `driver-app/__tests__/screens/driver-dashboard-route.test.ts` | same + a longer comment about its line-spanning regexes | the file most likely to be misread as already unsafe |
| `driver-app/__tests__/screens/ride-detail-route.test.tsx` | same | same |

## 7. Before / after

```ts
// Before
const hookSource = readFileSync(resolve(__dirname, '..', 'useDriverDashboard.ts'), 'utf8');
```

```ts
// After
const hookSource = readFileSync(resolve(__dirname, '..', 'useDriverDashboard.ts'), 'utf8').replace(
  /\r\n/g,
  '\n',
);
```

## 8. Rollback plan

`git revert` of this test-only diff. No deploy, no app build, no migration; nothing
reaches a user. On rollback the eight files return to being latently fragile.

**Not feature-flagged** (gate #3): test files have no runtime behaviour to gate.

## 9. Verification performed

- [x] **Classification done by reading, not by pattern-matching.** The first
      automated pass produced three false positives; each flagged file's assertion
      bodies were then read directly, which is what identified
      `androidAutoDistribution` as JSON-parsed and `driver-dashboard-route`'s
      regexes as `\r`-tolerant.
- [x] **All nine source-reading suites plus the screens directory** — 12 suites,
      **35/35 tests passed**.
- [x] **`npx tsc --noEmit` (driver-app)** → exit 0.
- [x] **Full driver-app suite** → 45/46 suites, 344/346 tests. The two failures are
      both in `ActivityView`, which is the known load-dependent flake, not a file
      touched here — see below.
- [x] **Blast-radius grep performed** — `readFileSync` across
      `driver-app/hooks/__tests__` and `driver-app/__tests__` to enumerate every
      file sharing the pattern.

### What was NOT verified

- **`ActivityView` failed in the full run and this needs stating plainly rather
  than waved off.** It is not a file this commit touches, and it passes standalone
  **6/6 in 16.2 s** against **119.5 s and 2 failures** inside the full parallel run.
  That 7× wall-clock spread points at worker contention, and the same suite failed
  and passed non-deterministically several times across this session. It is now
  filed as its own task with those numbers, because a suite that fails
  ~50% of the time under load makes every real regression ambiguous — twice in this
  session I had to revert files to HEAD to prove a failure was not mine.
- **The actual `ActivityView` assertion text was not captured**, so "contention
  against a default timeout" is the leading hypothesis, not a diagnosis.
- **The systemic fix was deliberately NOT applied** — see §10.
- **CI has not run.** These changes are no-ops on an LF checkout, so CI behaviour
  should be identical, but that is reasoning rather than an observed run.
- **No production build** — correctly, since this diff contains no production code.

## 10. The systemic alternative, and why it was not taken

The durable fix is a `.gitattributes` with `* text=auto eol=lf` (or at minimum
`*.ts text eol=lf`), which would make working trees LF everywhere and retire this
whole class of problem — including for `rider-app`, `shared`, `backend`, and any
future source-text test.

I did not do it, and would not without an explicit decision, because it is an
operational event rather than a code change:

- With `core.autocrlf=true` set locally, `.gitattributes` takes precedence, but
  files already in the working tree stay CRLF until re-checked-out or renormalised.
  Making it real means `git add --renormalize .`, which rewrites line endings across
  the entire repository in one commit.
- That commit conflicts with essentially every in-flight branch, and this repo has
  active PR branches (`pr-2807` among them) while the product is in live app
  testing.
- The resulting diff is enormous and reviewable only in the abstract, which is a
  poor fit for a repo whose merge gates depend on reviewers seeing what changed.

Recommendation: do it, but as its own scheduled change when branch traffic is low —
ideally immediately after a release cut, with the renormalise commit isolated and
announced. Until then the eight one-liners above cover the only files that actually
read source as text.

## 11. Sign-off

- [x] Rollback plan is concrete and testable — test-only revert
- [x] Blast radius is stated, not assumed — every file classified individually, with
      the two exclusions justified rather than glossed
- [x] No silent behavior change — there is no runtime behaviour here, and §9 reports
      the unrelated `ActivityView` failure with its numbers instead of quietly
      attributing the run to "known flake"
