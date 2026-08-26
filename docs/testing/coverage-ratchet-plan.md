# Coverage ratchet plan

Tracks the milestone plan referenced by `ACTION_ITEMS.md` B37: how each
surface's CI-enforced coverage threshold is raised over time as real test
coverage improves, toward the user's stated 100% target. Mirrors
`backend/pytest.ini`'s existing ratchet-history comment style, extended to
the three frontend surfaces.

## Why a doc, not just inline comments

Each surface's config file already carries an inline comment with its most
recent measurement and threshold change. This doc exists so the *next*
session doesn't have to re-derive the current baseline across four files
and a several-thousand-line `ACTION_ITEMS.md` entry — it's the single place
that states, as of one date, what's measured, what's gated, and what the
next step is.

## Current state (measured 2026-08-24)

All four numbers below come from actually running each surface's coverage
tool locally, not from re-reading old config comments.

| Surface | Command | Suite result | Statements | Branches | Functions | Lines | Gate before this pass | Gate after this pass |
|---|---|---|---|---|---|---|---|---|
| rider-app | `npx jest --coverage` | 122/122 suites, 1241/1241 tests | 74.73% | 65.68% | 71.77% | 76.42% | `lines:20, functions:16, branches:15` | `lines:73, functions:69, branches:63` |
| driver-app | `npx jest --coverage` | 115/115 suites, 1243/1243 tests | 65.73% | 57.45% | 63.25% | 67.37% | `lines:33, functions:26, statements:32` | `lines:65, functions:60, statements:63` |
| admin-dashboard | `npx vitest run --coverage` | 36/36 files, 351/351 tests | 19.11% | 13.05% | 11.71% | 20.75% | `branches:11, functions:10, lines:19, statements:18` | unchanged (already ~1-2pts below measured) |
| backend | `pytest --cov` (see `backend/pytest.ini` ratchet comment) | not re-run here — CLAUDE.md's Testing Conventions already documents current per-module minimums | aggregate `--cov-fail-under=60`, target ceiling 80 | — | — | — | 60% | 60% (no change; backend already has its own mature ratchet, see below) |

Each surface's config file (`rider-app/jest.config.js`,
`driver-app/jest.config.js`, `admin-dashboard/vitest.config.ts`) carries an
inline comment next to its `coverageThreshold`/`thresholds` block with the
same numbers and the date they were set — that comment is the source of
truth if this doc and the config ever drift; update both together.

**rider-app and driver-app were the real finding here.** Both apps had an
enormous amount of screen-by-screen test-authoring land under B37 (see
`ACTION_ITEMS.md`'s B37 entry — dozens of previously-0%-coverage `app/`
screens got real tests across several follow-up PRs) without the numeric
threshold ever being raised to track it. The gate had drifted from "tight
regression tripwire" to "rubber stamp 30-50 points below actual" — passing
trivially regardless of what a PR did to coverage. This pass closes that
gap by re-measuring and tightening both to within a few points of today's
real ceiling.

**admin-dashboard didn't move.** Its last B37 measurement (2026-08-22:
19%/12.98%/11.66%/20.64%) and today's (19.11%/13.05%/11.71%/20.75%) are
within noise of each other — no comparable test-authoring pass has touched
this surface yet. Its threshold already sits ~1-2 points below current
measured on every metric, so no change was made this round; it's already
at the target gap this plan calls for.

**Backend already has a working ratchet and isn't touched by this plan.**
`backend/pytest.ini`'s own comment documents its history (6% → 40% → 50% →
60%, target ceiling 80, next planned step 65 → 70) and enforces it via
`--cov-fail-under=60` in `addopts`. CLAUDE.md's Testing Conventions section
also documents live per-module minimums for the modules the org cares most
about (`routes/payments.py`, `services/fare_service.py`, `utils/crypto.py`
≥90%; `routes/rides.py`, `services/dispatch_service.py` ≥80%;
`routes/corporate_*.py`/`services/corporate_*.py` ≥80%, already met at
~92% aggregate; admin routes/utilities ≥70%). This plan doesn't duplicate
that — it exists to give the three frontend surfaces the same discipline
backend already has.

## The ratchet mechanism

1. **Land the first step now (this change):** for a surface whose gate has
   drifted well below its actual measured coverage, tighten the threshold
   to sit **2-3 points below today's real measured number** on each
   tracked metric (not below the historical config value — below what the
   tool reports right now). This turns the gate back into a real
   regression floor: a PR that ships new, meaningfully untested code can
   drop coverage by a couple of points and still not fail CI, but can't
   quietly erode it by tens of points the way an 50-point-stale gate
   allowed.
2. **Cadence for raising it further:** revisit a surface's threshold
   — quarterly, or immediately after any PR/session that measurably
   raises that surface's aggregate coverage by more than ~5 points
   (whichever comes first). "Revisit" means: re-run the coverage tool,
   compare the fresh number to the current gate, and if there's now more
   than ~3-5 points of slack, tighten the gate to close most of it (same
   2-3-points-below-actual target), updating both the config's inline
   comment and this doc's table together.
3. **Never tighten past measured.** A threshold change must always follow
   a coverage *increase* (new tests), never precede one — bumping the
   number alone with no new tests just makes CI red for no benefit. This
   mirrors the existing caution already written into `ACTION_ITEMS.md`'s
   B37 entry and `backend/pytest.ini`'s ratchet comment.
4. **Directory scope is a separate axis from the numeric threshold** and
   is already done for the frontend surfaces — every top-level source
   directory in rider-app and driver-app (`store/`, `hooks/`, `utils/`,
   `app/`, `components/`, `lib/`, `services/`, and driver-app's `api/`)
   and all of admin-dashboard's `src/lib/`, `src/store/`,
   `src/components/`, `src/app/**` are measured. This plan only concerns
   raising the *number*, not widening *what's counted* — a future
   decision to also change what's counted (e.g. per-directory thresholds
   instead of one global number) is out of scope here.

## Why 100% is aspirational, not a literal target

The user has stated a 100% coverage expectation, and this plan treats that
as the ceiling to work toward, not a near-term milestone, and not
literally "every line the tool can count":

- **Platform-conditional code** (React Native `Platform.OS` branches for
  iOS vs. Android-only paths, e.g. `driver-app/services/notifeeService.ts`
  and `backgroundMessaging.ts`) has branches that are only reachable on
  one platform at a time in a real device/CI run; testing both sides
  means mocking the platform, which is legitimate but sometimes leaves a
  residual defensive branch (e.g. a `.catch(() => undefined)` no-op guard)
  not worth a contrived test to hit.
- **Error-boundary / defensive fallback code** — catch blocks around
  conditions the current JS engine doesn't actually throw for (documented
  example: `formatDate`'s catch branch in a rider-app screen, where
  `new Date('not-a-date').toLocaleDateString()` returns `"Invalid Date"`
  rather than throwing under Hermes/Jest) is real, intentional defensive
  code, not dead code — but isn't exercisable without contriving an input
  shape the real code never produces.
- **Dev-only tooling and thin re-exports** (e.g. driver-app's
  `api/client.ts`, noted in `ACTION_ITEMS.md`'s B37 entry as having zero
  measurable lines) can legitimately show 0% while being effectively
  fully covered by what it re-exports.
- **Native shims and platform bootstrap files** (e.g. rider-app's
  `app/_layout.tsx`, currently 0%) often need integration/E2E-level
  testing (a real navigation mount) rather than unit tests to exercise
  meaningfully, and are lower priority than screen logic.

The working definition going forward, matching `ACTION_ITEMS.md`'s own
B37 wording: **100% means 100% of reachable, meaningfully-testable code,
with every exclusion justified inline** (a code comment explaining why a
line/branch is intentionally left uncovered, as several B37 fixes already
do) — not literal 100% of every line the tool can count.

## Next planned step

Re-run all three frontend suites (and re-check backend's own ratchet
comment) at or after the next quarterly checkpoint, or sooner if a session
does another round of screen/component test-authoring on rider-app,
driver-app, or admin-dashboard. Whichever surface moved by more than ~5
points gets its threshold tightened again per the mechanism above.
