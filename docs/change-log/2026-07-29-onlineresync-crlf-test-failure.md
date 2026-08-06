# Change Impact & Risk Log — `onlineResync` test failed on Windows checkouts only

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code (session: driver sign-out investigation) |
| Surface(s) | driver-app (test only — no production code changed) |
| Domain (Sentry tag) | `drivers` (by subject matter of the test) |
| PR / commit link | _(pending)_ |
| Related issue or gap ID | Triage of the one failure left red across this session's work |

## 1. Issue / gap identified

`driver-app/hooks/__tests__/onlineResync.test.ts › toggleOnline sets and clears the
toggle guard around its request` failed on every run in this session, including on
`HEAD` with all session changes reverted. It was the only failure I could not
attribute to my own diffs.

**Correction to how I first characterised it.** I filed it as a pre-existing red
gate under CLAUDE.md gate #8, and flagged that because it concerns the `is_online`
flip, the `is_available ⇒ is_online` invariant might be in play. Both framings were
wrong in an important way: it is **not a CI failure at all**, and the invariant is
not involved. It fails only on a Windows working tree and passes on Linux CI. The
production code is correct.

## 2. Root cause

The test reads the hook's source as a string and asserts against literal text:

```ts
const hookSource = readFileSync(resolve(__dirname, '..', 'useDriverDashboard.ts'), 'utf8');
...
expect(toggle).toContain('} finally {\n      isTogglingRef.current = false;\n    }');
```

The needle uses `\n`. The file on disk does not:

```
CRLF lines: 1711 | bare LF lines: 0
contains LF-form needle  : False
contains CRLF-form needle: True
```

`core.autocrlf` is `true` and there is **no `.gitattributes`**, so a Windows
checkout materialises CRLF while `ubuntu-latest` CI gets LF. A needle spanning more
than one line therefore matches in CI and cannot match locally on Windows.

The five sibling assertions in the same file pass because their needles are
single-line, which makes them CRLF-safe **by accident, not by design**.

The misleading part is the symptom: the failure names the online/offline toggle
guard, so it reads as a defect in the flip that puts drivers online — one of the
most safety-relevant paths in the app. It is nothing of the sort. The `finally
{ isTogglingRef.current = false; }` block is present and correct at
`useDriverDashboard.ts:1469-1471`.

## 3. Fix / remediation

Normalise line endings on read:

```ts
const hookSource = readFileSync(resolve(__dirname, '..', 'useDriverDashboard.ts'), 'utf8')
  .replace(/\r\n/g, '\n');
```

Also added a header comment stating plainly what this file is: source-**text**
assertions, a cheap structural tripwire, not behavioural coverage. It can catch a
deletion or a reordering; it cannot catch the guard failing at runtime.

**Deliberately NOT done:** changing `useDriverDashboard.ts`. There was never
anything wrong with it. A "fix" that touched production code here would have been
the bad outcome — forcing a green check by changing the thing the check was
wrongly accusing.

## 4. Risk & impact on existing functionality

**Blast radius: one test file. Zero production code.**

- No runtime behaviour changes anywhere. `useDriverDashboard.ts` is untouched.
- The `is_available ⇒ is_online` invariant is unaffected — nothing about the
  `is_online` flip or `PUT /drivers/{id}/status` changed.
- The normalisation only affects the string this test matches against. It cannot
  make an assertion pass that should fail: if the `finally` block were deleted, the
  needle would be absent in either line-ending form.
- **Nine sibling source-text suites share the latent hazard** and were deliberately
  left alone (they pass): touching nine files for a latent issue does not belong in
  this commit. Filed as its own task, which also weighs the systemic alternative
  (`.gitattributes` with `eol=lf`, whose blast radius is every developer's working
  tree and therefore needs its own decision).

Not touched: ride state machine, money/wallet arithmetic, background loops, RLS,
migrations, WebSocket events, dispatch.

## 5. User-experience effect

None. No rider, driver, corporate-admin or internal-admin visible change; no
production code path altered. The effect is that the driver-app suite is runnable on
a Windows dev machine, and that a future real failure in this file will not be
dismissed as "the usual red one".

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/hooks/__tests__/onlineResync.test.ts` | Normalise CRLF→LF on read; header comment stating these are source-text assertions rather than behavioural coverage, and an inline comment explaining the line-ending trap | The needle could never match on a Windows checkout, and the failure looked like a defect in the online/offline flip |

## 7. Before / after

```ts
// Before
const hookSource = readFileSync(resolve(__dirname, '..', 'useDriverDashboard.ts'), 'utf8');
// Windows checkout → CRLF → the multi-line needle below can never match
```

```ts
// After
const hookSource = readFileSync(resolve(__dirname, '..', 'useDriverDashboard.ts'), 'utf8')
  .replace(/\r\n/g, '\n');
```

## 8. Rollback plan

`git revert` of this single test-file diff. No deploy, no app build, no migration,
no `app_settings` value — nothing ships to a user. On rollback the suite returns to
failing on Windows and passing on CI.

**Not feature-flagged** (gate #3): a test file has no runtime behaviour to gate.

## 9. Verification performed

- [x] **Root cause proven, not inferred** — byte-level check of the file:
      1711 CRLF lines, 0 bare LF; the LF-form needle absent and the CRLF-form needle
      present; `git config core.autocrlf` = `true`; no `.gitattributes` `eol` rule.
- [x] **`npx jest hooks/__tests__/onlineResync.test.ts`** → **6/6 passed**
      (was 5 passed / 1 failed).
- [x] **Full driver-app suite** → **46/46 suites, 346/346 tests passed.** This is the
      first fully-green driver-app run in this session; the `ActivityView` flake did
      not recur in this run, and every other suite was already green.
- [x] **Confirmed the assertion still has teeth** — the needle matches real source
      content at `useDriverDashboard.ts:1469-1471`, and normalisation cannot mask a
      missing `finally` block: the needle would be absent in either line-ending form.
- [x] **Blast-radius grep performed** — `readFileSync` across
      `driver-app/hooks/__tests__` and `driver-app/__tests__` to find every sibling
      sharing the pattern (10 files, enumerated in the follow-up task), and confirmed
      only this one carries a multi-line needle.
- [x] **Reviewed against `CLAUDE.md` conventions** — gate #8 explicitly warns against
      forcing a fix that breaks something else to turn a check green; the production
      file was left alone precisely because it was not at fault.

### What was NOT verified

- **The behaviour this test claims to cover is still untested.** That
  `isTogglingRef` is genuinely cleared at runtime — including when
  `updateDriverStatus` throws, when background-permission checks reject, or when
  `stopGeofenceRecovery` hangs past its 8s timeout — is asserted only by the presence
  of the source text. If the guard leaked `true`, the driver's GO button would stop
  responding, and no test in the repo would catch it. `useDriverDashboard` has no
  render-based harness at all.
- **Nine sibling source-text suites remain line-ending fragile** (they pass today).
  Not fixed here; see the follow-up task, which also covers the `.gitattributes`
  option.
- **The `ActivityView` flake is not fixed**, only absent from this run. It failed
  under full parallel load earlier in the session and passes standalone; the cause
  (resource starvation / timeouts) is unaddressed.
- **CI has not been re-run.** This test was already green on CI before the change and
  should remain so; the normalisation is a no-op where input is already LF.
- **No production build run** — correctly, since this diff contains no production
  code. Earlier commits in this working tree already bundled successfully.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — single test-file revert, nothing
      user-facing
- [x] Blast radius is stated, not assumed — root cause established at byte level, and
      the nine sibling files sharing the pattern enumerated rather than left implicit
- [x] No silent behavior change to an already-shipped flow — no production code was
      touched, and §1 corrects the record on how this failure was first characterised
      rather than quietly restating it
