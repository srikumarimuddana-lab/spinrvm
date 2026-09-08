# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | Claude Code session |
| Surface(s) | backend (CI/CD config only — `.github/workflows/label-run-maestro.yml`) |
| Domain (Sentry tag) | admin (closest fit — deploy-pipeline/CI change, not application code) |
| PR / commit link | (filled in on push) |
| Related issue or gap ID | `ACTION_ITEMS.md` C95 |

## 1. Issue / gap identified

`label-run-maestro.yml`'s `detect-and-label` job failed 3 times (across 2
distinct PR commits) with `actions/checkout` returning `Repository not
found` — a 404-class failure — rather than completing.

## 2. Root cause

The job's `permissions:` block listed only `pull-requests: write`. Once any
`permissions:` key is present in a GitHub Actions workflow, every unlisted
scope becomes `none` rather than falling back to the repo/org default — so
`contents` was implicitly `none`. `actions/checkout` needs `contents: read`
to authenticate the git fetch; GitHub returns a 404-shaped "repository not
found" (not a 403) when a token has no read access to a private repo, to
avoid leaking the repo's existence to unauthorized tokens.

Not independently confirmed against a source outside this session's own
observations (2 reproductions, 3 retries each, same token banner both
times) — a second live hypothesis (a transient GitHub-side ref-propagation
race right after a push) was considered and not ruled out, but the fix
applied here is correct under either hypothesis: `contents: read` is the
correct minimum scope for `actions/checkout` regardless of which hypothesis
explains the specific failures observed.

## 3. Fix / remediation

Added `contents: read` to the job's existing `permissions:` block, same
shape as the C93/C94 fix (`ACTION_ITEMS.md` C93/C94,
`docs/change-log/2026-09-08-c93-c94-ci-token-permissions.md`).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this one job's checkout step.** Grepped the
  file: only one `permissions:` block exists (workflow-level), covering
  the file's single job (`detect-and-label`) — no other job to check.
- **What else reads/writes the same job?** Nothing — this workflow only
  ever applies a `run-maestro` label to a PR based on a path filter; no
  other workflow depends on its output beyond `maestro-e2e.yml`'s own
  `pull_request: types: [labeled]` trigger, which is unaffected by this
  change (label-application logic and its `pull-requests: write` scope are
  untouched).
- **Could this regress a working flow?** No — `contents: read` only adds a
  capability `actions/checkout` already needed; nothing about the existing
  `pull-requests: write`-scoped label-apply step changes.

## 5. User-experience effect

None — internal CI pipeline only, no rider/driver/admin-facing surface.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/label-run-maestro.yml` | Added `contents: read` to the workflow-level `permissions:` block | Close C95 — give `actions/checkout` the scope it needs |

## 7. Before / after

```yaml
# Before
permissions:
  pull-requests: write
```

```yaml
# After
permissions:
  contents: read
  pull-requests: write
```

## 8. Rollback plan

`git revert`-safe. No live data or already-triggered run is affected —
this only changes what a *future* run of this job resolves for its token
scope. Reverting restores the prior (potentially-404-ing) behavior exactly.

## 9. Verification performed

- [x] YAML syntax validated (`yaml.safe_load`)
- [x] Grepped the file for other `permissions:` blocks — confirmed only
      one exists, covering the file's single job
- [ ] `actionlint` — not available in this sandboxed session (no Docker
      daemon reachable), same disclosed gap as C93/C94's fix
- [ ] A real GitHub Actions run confirming the failure is actually gone —
      not possible before push; this PR's own CI run of this exact job is
      the real-world test

## What was NOT verified

- **No real CI run was observed to confirm the fix end-to-end before this
  PR's own push.** Everything here is verified via GitHub's documented
  `permissions:` schema semantics, consistent with the same reasoning
  already validated once by the C93/C94 fix.
- **Which of the two original hypotheses (real gap vs. transient race) was
  actually correct** — moot for whether this fix is right (it's right
  either way), but left as an open fact.
- **`actionlint`** — not available in this session.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no
      live-data entanglement)
- [x] Blast radius is stated, not assumed (grepped for other `permissions:`
      blocks in the file; confirmed only one job exists)
- [x] No silent behavior change to an already-shipped flow without the UX
      field filled in (none — this is CI/CD config only)
