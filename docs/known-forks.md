# Known Forks Registry

Some files in this repo are **intentionally** duplicated rather than shared, usually because
one app needs behavior the other must not have by default (e.g. a course-up camera that only
makes sense while driving). That's a legitimate design choice — the risk isn't the fork
existing, it's a fix landing on one side and silently never reaching the other.

**Why this file exists:** the 2026-09-12 ride-experience industry-benchmark audit
(`docs/audit/ride-experience/REPORT.md`, `ROADMAP.md`) found `shared/components/CarMarker.tsx`
and `driver-app/components/CarMarker.tsx` had diverged five separate times, with fixes ported
one-way by hand and no mechanism catching when a port was missed. One of those misses — a
"car drives sideways" bug driver-app fixed for itself on 2026-09-11 — was still live for every
rider-app user a day later, discovered only because an audit happened to look. This registry,
plus the pre-commit check in `.claude/hooks/pre-commit` that reads it, exists so the *next*
one-way fix gets a reminder instead of a silent gap.

**This is a stopgap, not a fix.** A registry entry only nudges a human to check the sibling —
it can't verify the sibling actually needs (or got) the same change. Where a mechanical parity
guard exists (a test that fails on undeclared capability divergence — see the CarMarker entry
below), that guard is the real protection; the registry entry is what tells you the guard
exists and where to find it.

## How to use this file

- **Adding a new intentional fork:** add a row below. Say why it's forked (not just that it
  is), and whether a mechanical parity guard exists yet.
- **Touching a file listed here:** the pre-commit hook will warn (not block) if you stage one
  side without the other. Read the "why forked" column before assuming the change doesn't
  apply to the sibling — most of the time it does.
- **Reconciling a fork** (merging it back into one shared implementation): remove the row here
  once done, and note the reconciling commit/PR in the removal commit message for history.

## Registry

| File A | File B | Why forked | Parity guard | Tracking |
|---|---|---|---|---|
| `shared/components/CarMarker.tsx` | `driver-app/components/CarMarker.tsx` | driver-app needs course-up-camera bearing/heading callbacks (`onBearingChange`, `mapHeadingRef`) that rider-app must NOT get by default — north-up is the correct rider-side convention. Everything else (GPS smoothing, playback buffer, route-snapping, Android rotation animation) should be identical between the two and has drifted only by omission, not by design. | Not yet — tracked as `docs/audit/ride-experience/ROADMAP.md` item R11 (recommended: a test diffing both files' capability surface, failing on undeclared divergence). Until R11 lands, this row is the only guard. | `ACTION_ITEMS.md` C90 (3 prior ports) + the 2026-09-12 audit (2 more found: route-rebase, Android rotation interpolation) |

## Adding a mechanical parity guard

When a pair above gets a real guard (a test, a lint rule, a build-time diff check), link it
in the "Parity guard" column and keep the row — the registry is still useful as the "why
forked" explanation even once a guard exists to enforce it mechanically.
