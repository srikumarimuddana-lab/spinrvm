# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code (session), approved by vikas@ngitservices.com |
| Surface(s) | infra / CI |
| Domain (Sentry tag) | n/a (CI tooling, not an application code path) |
| PR / commit link | PR #4781, commit `6c89b0d46` |
| Related issue or gap ID | GitHub #4642 (option 3) |

## 1. Issue / gap identified

`migration-check.yml`'s CHECK B only validates a PR's new migration number against its own merge-base, so it cannot see a true cross-PR race where two PRs both branch before either merges — each PR's own CI looks clean in isolation even though both land the same numeric prefix. This actually happened: a third file landed at prefix `370` from two separate PRs, undetected by CHECK B on either one.

## 2. Root cause

CHECK B's diff is scoped to `git diff <this PR's merge-base> <this PR's head>` — it only ever sees files this PR itself adds, compared against whatever was on `main` when this PR's branch forked. If PR A and PR B both fork from the same point on `main` and each independently add a migration at prefix `370`, neither PR's CI run can see the other PR's new file, so both pass. The collision is only visible once both are merged into `main`'s actual state — a check scoped to a single PR's diff structurally cannot catch that.

## 3. Fix / remediation

Added a new scheduled workflow, `.github/workflows/migration-duplicate-nightly.yml`, that runs nightly against `main`'s actual current state (not a PR diff) and scans `backend/migrations/*.sql` for numeric prefixes shared by more than one file. Since this repo already carries ~65 historical duplicate prefixes that pre-date CHECK B entirely, the sweep compares against a committed baseline (`backend/migrations/.known_duplicate_prefixes.json`, generated from the repo's current state) and only fails when a duplicate is new or has gained an additional colliding file since that baseline was captured — the pre-existing historical duplicates never trip it.

## 4. Risk & impact on existing functionality

- **No application code touched.** This is a new CI workflow file plus one new static JSON baseline file — nothing in `backend/migrations/*.sql`, the migration runner (`backend/scripts/run_migrations.py`), or any application route reads or is affected by either new file.
- **Blast radius: isolated to CI.** Grepped for consumers of `.known_duplicate_prefixes.json` — none exist yet outside the new workflow itself; it is not imported or read by any Python module.
- **Does not touch `migration-check.yml`'s existing CHECK B logic** — that per-PR gate is unmodified; this is purely additive, a second, independent check with a different trigger (schedule vs. PR).
- **No effect on `schema_migrations` or any applied migration** — this sweep only reads filenames on disk; it never applies, modifies, or reverts a migration.
- **False-positive risk**: if a legitimate new migration happens to land on a prefix already in the baseline (e.g. reusing a number after a rename), the sweep would flag it as "worsened" even though it's an accepted, deliberate reuse. Mitigated by making the baseline file easy to update (documented in the workflow's own inline comments) — a maintainer confirms the reuse is safe (checking `schema_migrations` in production first, per CLAUDE.md's migration-numbering rules) and updates the JSON file in the same PR.

## 5. User-experience effect

None. This is an internal CI/CD tooling change with no rider/driver/corporate-admin/internal-admin-facing surface. It runs on a schedule, not in the request path of any live traffic.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/migration-duplicate-nightly.yml` | New nightly scheduled workflow | Detects cross-PR migration-prefix collisions CHECK B structurally cannot see |
| `backend/migrations/.known_duplicate_prefixes.json` | New baseline file listing the ~65 currently-known historical duplicate prefixes | Lets the sweep flag only new/worsened duplicates, not the accepted historical ones |

## 7. Before / after

Not applicable in the usual sense — this is new, additive tooling with no prior behavior to diff against. The nightly sweep either runs (new) or doesn't exist (before); no existing behavior changes.

## 8. Rollback plan

Delete `.github/workflows/migration-duplicate-nightly.yml` (or `git revert` this commit). The baseline JSON file is inert without the workflow that reads it, so removing just the workflow is a complete, immediate rollback with zero residual state — nothing else references either file.

## 9. Verification performed

- [x] Automated tests run: the embedded Python detection logic was extracted and run directly (not via an actual GitHub Actions dispatch, which isn't possible from this environment) against the real `backend/migrations/` directory — passes cleanly, reporting all 65 baseline duplicates unchanged. Then a fake duplicate file was injected (copied an existing migration under a new colliding filename at prefix 375) and the same script correctly failed, naming both colliding files; the test artifact was deleted before committing.
- [ ] Manual repro steps followed in staging — not applicable; there is no staging deployment of GitHub Actions workflows to test against beyond running the logic directly as above.
- [x] Blast-radius grep performed: searched for any other consumer of `.known_duplicate_prefixes.json` (none) and confirmed `migration-check.yml`'s own CHECK B is untouched.
- [x] Reviewed against relevant CLAUDE.md conventions: the append-only/never-rename-an-applied-migration rule is explicitly called out in the workflow's own failure message so a maintainer resolving a real finding doesn't reach for a rename without checking production's `schema_migrations` table first.
- [ ] Feature-flagged — not applicable; this is an observability-only CI addition (it can only fail a scheduled workflow run, never block or alter a PR merge), so a flag was judged unnecessary.

## What was NOT verified

- No actual GitHub Actions run of this workflow (scheduled or `workflow_dispatch`) has executed yet — first real execution will be the nightly cron after merge, or a manual `workflow_dispatch` trigger.
- Whether this repo's CI-audit auto-issue-filer (which files `[CI Audit]` issues for other failing scheduled workflows) will pick up a failure from this specific new workflow name was not confirmed — if it doesn't, a failing nightly run would only be visible in the Actions tab, not as an auto-filed issue, until confirmed otherwise.
