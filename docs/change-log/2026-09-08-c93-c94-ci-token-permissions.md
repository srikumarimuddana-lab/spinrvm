# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | Claude Code session |
| Surface(s) | backend (CI/CD config only — `.github/workflows/ci.yml`, `.github/workflows/security-gates.yml`) |
| Domain (Sentry tag) | admin (closest fit — deploy-pipeline/CI change, not application code) |
| PR / commit link | (filled in on push) |
| Related issue or gap ID | `ACTION_ITEMS.md` C93, C94 |

## 1. Issue / gap identified

Two GitHub Actions jobs across `ci.yml` and `security-gates.yml` were missing GITHUB_TOKEN
permission scopes their own steps need, causing them to fail on every PR:

- `ci.yml`'s `detect-changes` job: `dorny/paths-filter`'s Files-API lookup 403s for lack of
  `pull-requests: read`.
- All 4 `codeql-action/upload-sarif` call sites in the repo (`ci.yml`'s `security-scan` and
  `docker-image-scan` jobs; `security-gates.yml`'s `semgrep` (G3) and `container-scan` (G6)
  jobs): `wait-for-processing: true`'s own workflow-run status poll 403s for lack of
  `actions: read`.

## 2. Root cause

Discovered while investigating `check_run.completed` failure wakes on PR #5128 (a 2-file
docs-only change with no plausible causal link to a CI token-permissions error — traced
instead of assumed innocent).

- **C93**: `ci.yml` has no top-level `permissions:` block, and its `detect-changes` job (a
  `workflow_call` job with no job-level override) falls through to this repo's restrictive
  default token scope (`contents: read`, `metadata: read`, `packages: read` — no
  `pull-requests`). `ci-guardrails.yml` and `security-gates.yml` do **not** have this bug —
  both already grant `pull-requests: write` at the workflow level, which is a superset of
  `read` for the same scope. Confirmed directly (not assumed): pulled `security-gates.yml`'s
  own job list for this PR's head commit — its `detect-changes` job shows
  `conclusion: "success"`.
- **C94**: `wait-for-processing: true` on `codeql-action/upload-sarif` polls
  `GET /repos/.../actions/runs/{run_id}` to confirm the SARIF finished processing — a
  different token scope (`actions: read`) than the one that authorizes the upload itself
  (`security-events: write`, which was already present at all 4 sites — an earlier pass at
  this diagnosis wrongly claimed it was missing too, due to a context-truncated grep;
  corrected in `ACTION_ITEMS.md` before this fix was written).

Both are pre-existing, repo-wide defects, not introduced by any single PR — every push to
every PR reproduces them identically.

## 3. Fix / remediation

Added the two missing permission grants, each scoped as narrowly as possible:

1. `ci.yml`'s `detect-changes` job: added a job-level `permissions:` block
   (`contents: read`, `pull-requests: read`).
2. `ci.yml`'s `security-scan` and `docker-image-scan` jobs: added `actions: read` to each
   job's existing `permissions:` block.
3. `security-gates.yml`'s workflow-level `permissions:` block: added `actions: read` (covers
   both `semgrep`/G3 and `container-scan`/G6, neither of which has a job-level override).

Considered dropping `wait-for-processing: true` instead (removes the failing poll entirely,
no permission change needed) but rejected it: that would remove real verification that the
SARIF upload actually completed, trading a loud CI failure for a silent unknown. Granting
the specific missing permission is the smaller, more correct fix — it doesn't reduce what
gets checked.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to GITHUB_TOKEN permission scopes on 4 job definitions across 2
  workflow files.** No application code, no runtime behavior, no data path is touched.
- **What else reads/writes the same jobs?** Grepped every `permissions:` block in both files
  before editing — no other job shares the ones modified here. `ci-guardrails.yml` was left
  untouched (verified it doesn't need either fix — see Root cause above).
- **Could this regress a working flow?** The only behavior change is that 2 GitHub API calls
  that previously 403'd now succeed: `dorny/paths-filter`'s PR-files lookup, and
  `codeql-action/upload-sarif`'s workflow-run status poll. Both are read-only calls against
  GitHub's own API — no write path is newly granted beyond what already existed
  (`pull-requests: read` is strictly narrower than the `pull-requests: write` two of the
  three caller workflows already carry; `actions: read` is a pure read grant).
- **Interaction with background loops / ride state machine / money paths:** none — this is
  CI/CD config only.
- **Second-order effect worth naming:** `detect-changes` succeeding (instead of always
  failing open) means `ci.yml`'s downstream jobs will now actually be path-filtered again on
  PRs that don't touch their watched surface, instead of unconditionally running everything.
  This is the *intended*, designed behavior (see `detect-changes.yml`'s own header comment)
  — not a new risk, but worth stating since it changes what visibly runs on a given PR for
  the first time since this bug existed.

## 5. User-experience effect

None — internal CI pipeline only, no rider/driver/admin-facing surface.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/ci.yml` | Added `permissions: {contents: read, pull-requests: read}` to `detect-changes` job; added `actions: read` to `security-scan` and `docker-image-scan` jobs' existing `permissions:` blocks | Close C93 (pull-requests) and C94 (actions) |
| `.github/workflows/security-gates.yml` | Added `actions: read` to the workflow-level `permissions:` block | Close C94 for `semgrep` (G3) and `container-scan` (G6), neither of which has a job-level override |

## 7. Before / after

```yaml
# Before (ci.yml)
detect-changes:
  uses: ./.github/workflows/detect-changes.yml
```

```yaml
# After (ci.yml)
detect-changes:
  permissions:
    contents: read
    pull-requests: read
  uses: ./.github/workflows/detect-changes.yml
```

```yaml
# Before (ci.yml security-scan)
permissions:
  contents: read
  security-events: write
```

```yaml
# After (ci.yml security-scan)
permissions:
  contents: read
  security-events: write
  actions: read
```

(`docker-image-scan` and `security-gates.yml`'s workflow-level block follow the identical
pattern — one `actions: read` line added to an existing block.)

## 8. Rollback plan

`git revert`-safe. No live data or already-triggered workflow run is affected — this only
changes what GITHUB_TOKEN scope a *future* run of these jobs gets. Reverting the commit
restores the prior (403-ing) behavior exactly, which is strictly worse but not unsafe: the
fail-open design in `detect-changes.yml` and the "SARIF already uploaded before the failing
poll" behavior in `codeql-action` both mean a revert doesn't lose any actual scan coverage,
just the fast-feedback/status-confirmation optimizations these permissions restore.

## 9. Verification performed

- [x] YAML syntax validated (`yaml.safe_load` against both full files after every edit)
- [x] Grepped every `permissions:` block in `ci.yml` and `security-gates.yml` before editing
      to confirm no other job shares the ones modified, and that `ci-guardrails.yml` (left
      untouched) genuinely doesn't need either fix
- [x] Confirmed via live job-run data (not just file reading) that `security-gates.yml`'s
      `detect-changes` job already succeeds without the C93 fix — proof the fix is scoped
      correctly to `ci.yml` only, not applied redundantly elsewhere
- [ ] `actionlint` — **not available in this session** (no Docker daemon reachable; same gap
      disclosed in the prior `2026-09-08-b41-mobile-build-profile-pin.md` change-log entry).
      Only generic YAML parsing was performed.
- [ ] A real GitHub Actions run confirming the 403s are actually gone — not possible from
      this session before push; this PR's own CI run is the first real test of the fix.
      Watching this PR's checks after push is the verification step for this item.

## What was NOT verified

- **No real CI run was observed to confirm the fix end-to-end before this PR's own push** —
  everything here is verified via GitHub's documented `permissions:` schema semantics (a
  job-level block sets the complete grant for that job; a workflow-level block is the
  default for any job without its own override) and live job-run data for the one specific
  claim that needed it (security-gates.yml already working). This PR's own CI run is the
  real-world test.
- **`actionlint` was not available** (no Docker daemon in this session) — only generic YAML
  parsing was performed, which cannot catch GitHub-Actions-specific schema errors the way
  `actionlint` can (this repo's own history — the `security-gates.yml` YAML-quoting bug —
  shows plain YAML parsing has a real, demonstrated blind spot here).
- **Whether the previously-failing SARIF uploads (C94) actually reached GitHub's Code
  Scanning UI despite the reported failures, before this fix** — not checked; irrelevant to
  verifying the fix itself works going forward, but noted since C94's own entry flagged it
  as an open question a human should check.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no live-data entanglement)
- [x] Blast radius is stated, not assumed (grepped every `permissions:` block in both files;
      confirmed via live job-run data, not just reasoning, that the third caller workflow
      needs no change)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
      (none — this is CI/CD config only, User-experience effect section states that plainly)
