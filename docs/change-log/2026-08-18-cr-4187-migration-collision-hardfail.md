# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | Claude Code session (vikas@ngitservices.com) |
| Surface(s) | CI (`.github/workflows/migration-check.yml`) + `CLAUDE.md` docs |
| Domain (Sentry tag) | n/a — CI/CD gate, not application code |
| PR / commit link | (this PR) |
| Related issue or gap ID | CR #4187, ACTION_ITEMS.md C36 |

## 1. Issue / gap identified

`migration-check.yml`'s numeric-prefix collision check (CHECK B) only ever appended to a `warnings` list, never `errors` — so it could never actually block a merge, even when a PR's migration file number collides with one already on `main` at the time the PR's CI runs. This contradicted `CLAUDE.md`'s claim that "a CI prefix-uniqueness check blocks them."

## 2. Root cause

When CHECK B was originally written, the `file_num < expected` (true collision) branch and the `else` (sequence-gap, missing numbers) branch were both treated as advisory and both routed to `STATUS_WARN` / `warnings.append(...)`. Only two *other* checks in the same script (naming, append-only) were ever wired to `STATUS_FAIL` / `errors.append(...)`, which is the only list that triggers `sys.exit(1)`. The collision branch was never upgraded to match, so it silently stayed non-blocking. This was invisible until a human/agent manually diffed `main`'s migration directory: two unrelated PRs, #4126 and #4129, both landed `327_*.sql` around the same time because neither PR's own CI run ever saw the collision as a hard failure (each only saw a warning, or in the true cross-PR race, saw nothing at all since the colliding file didn't exist yet at that PR's CI-run time).

## 3. Fix / remediation

In `.github/workflows/migration-check.yml`'s CHECK B, the `elif file_num < expected:` branch (true collision — the new file's prefix collides with or precedes a number that already exists elsewhere in the repo) now uses `STATUS_FAIL` and appends to `errors` instead of `warnings`, making it a hard failure that blocks the PR. The `else` branch (sequence gap — missing numbers, no collision) is unchanged: still `STATUS_WARN` / `warnings`, since gaps are cosmetic and an accepted historical pattern (this repo already has migrations 319, 321, and 327 each existing twice under different names, per `CLAUDE.md`).

`CLAUDE.md`'s Database & Migration Conventions section was updated to describe this accurately: a true collision visible to the PR's own CI run is now a hard failure; a pure sequence gap still only warns; and the residual gap (a true cross-PR race where both PRs' branches predate each other's merge) is explicitly named as not fully closed by this change, per CR #4187's own risk analysis.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `.github/workflows/migration-check.yml`.** No other workflow, job, or script reads CHECK B's output — `errors`/`warnings` are local Python lists inside this one heredoc, only consumed by this same step's `sys.exit(1)`/`sys.exit(0)` at the end.
- **What else reads/writes the same "table, state, or code path":** nothing. This is a CI gate, not application code — no Supabase table, background loop, or runtime code path is touched.
- **Who else's PRs could this newly block:** any future PR whose new migration file's numeric prefix collides with (or precedes) a number that already exists in the migrations directory *at the time that PR's CI runs* — this is the intended behavior change (closing the gap CR #4187 identifies), not a regression. Previously such a PR would see a warning and could still merge; now it must rename to the next free number before merging. This is the same cheap `git mv` + sed renumbering already done twice this session (PR #4133: 323→328, PR #4134: 327→329) — now enforced by CI instead of relying on a human/agent noticing.
- **What is NOT newly blocked:** (a) the true cross-PR race — two PRs whose branches both predate the other's merge, so neither's CI run ever sees the other's file — is structurally unclosed by a per-PR check; CR #4187 explicitly defers that to a possible future option (b), a post-merge/`push`-to-`main` check, as out of scope for this PR. (b) Sequence gaps (skipped numbers, no collision) still only warn.
- **Retroactive-safety of the 3 existing historical duplicate pairs (319, 321, 327):** verified NOT to retroactively fail this check for any future unrelated PR — see the dedicated section below with the traced code path.

## 5. User-experience effect

None. This is an internal CI gate; no rider, driver, corporate-admin, or internal-admin-facing surface is touched. The only "user" affected is a future PR author whose migration number collides with something already on `main` — they now get a blocking CI failure instead of a warning they could ignore.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/migration-check.yml` | CHECK B's `file_num < expected` (true collision) branch changed from `STATUS_WARN`/`warnings.append` to `STATUS_FAIL`/`errors.append` | Makes a true numeric-prefix collision a hard CI failure instead of an ignorable warning (CR #4187) |
| `CLAUDE.md` | Database & Migration Conventions paragraph rewritten to describe the corrected behavior accurately | Previous wording ("a CI prefix-uniqueness check blocks them") overstated what the code actually did; now matches the fixed behavior and states the residual cross-PR-race gap explicitly |
| `docs/change-log/2026-08-18-cr-4187-migration-collision-hardfail.md` | New Change Impact & Risk Log entry (this file) | Required by `CLAUDE.md`'s mandatory Change Impact & Risk Log policy for a change to a live-tested-adjacent gate (migrations) |

## 7. Before / after

```python
# Before (.github/workflows/migration-check.yml, CHECK B collision branch)
elif file_num < expected:
    msg = f"prefix {file_num} collides with or precedes existing max {max(others)} (expected {expected})"
    record(short, "sequence", STATUS_WARN, msg)
    warnings.append(f"{short}: {msg}")   # <-- never causes exit 1, PR can still merge
```

```python
# After
elif file_num < expected:
    # True collision: this prefix already belongs to (or precedes) a migration
    # that exists elsewhere in the repo. This is a hard failure, not a warning.
    msg = f"prefix {file_num} collides with or precedes existing max {max(others)} (expected {expected})"
    record(short, "sequence", STATUS_FAIL, msg)
    errors.append(f"{short}: {msg}")   # <-- causes exit 1, blocks the merge
```

The sequence-gap `else` branch is byte-for-byte unchanged (`STATUS_WARN` / `warnings`).

## 8. Rollback plan

`git-revert-safe`. This is a pure CI-workflow + docs-wording change — no data migration, no application code, no live state touched. Reverting the commit restores the prior (warning-only) behavior for CHECK B and reverts the `CLAUDE.md` wording. No feature flag needed or applicable — GitHub Actions workflow files take effect on the next PR's CI run with no separate deploy step.

## 9. Verification performed

- [x] **Code-path trace performed by hand** — confirmed CHECK B only evaluates files in the current PR's diff (`for rel_path in changed:`, where `changed` comes from `git diff --name-only --diff-filter=ACDMRT "$MERGE_BASE" "$HEAD"`), and that any file already existing on the PR's base branch is diverted to the append-only check and `continue`s past CHECK B entirely before this fix's branch is ever reached. Full trace in the PR body.
- [x] **Standalone Python simulation run** — mirrored the exact updated CHECK B branch logic in a throwaway script and exercised the three required scenarios: (a) a PR reusing an already-existing prefix (327) → FAIL, (b) a PR picking the correct next number (331) → OK, (c) a PR leaving a gap (333, skipping 331/332) → WARN only. All three assertions passed. Script and output included in the PR body (not committed to the repo — a manual verification aid, not a maintained test).
- [ ] Real GitHub Actions run — **not possible from this environment.** This session cannot trigger a live Actions run against a real PR to observe the exit code directly; verification was performed by tracing the code and by the standalone simulation above, not by observing an actual CI pass/fail.
- [x] Blast-radius grep performed — confirmed no other workflow or script reads `errors`/`warnings` from this step; they are local to this one Python heredoc.
- [x] Reviewed against `CLAUDE.md` conventions — this change *is* the correction of a `CLAUDE.md` convention (Database & Migration Conventions section); the wording was cross-checked against the new code behavior line-by-line.
- [ ] Not applicable: no feature flag exists (or is needed) for a CI-workflow-only change; nothing user-visible to flag.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no data-layer cleanup needed)
- [x] Blast radius is stated, not assumed (isolated to `migration-check.yml`; no other consumer of `errors`/`warnings`)
- [x] No silent behavior change to an already-shipped *application* flow — this changes a CI gate's behavior, and the change (warning → hard failure on true collision) is stated explicitly here and in the PR body, not left implicit
