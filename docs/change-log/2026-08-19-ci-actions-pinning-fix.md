# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | vikas@ngitservices.com |
| Surface(s) | backend (CI/CD config only — `.github/workflows/`, no app surface touched) |
| Domain (Sentry tag) | n/a (this is CI infrastructure, not a runtime code path; closest audit tag is `cicd-infra`) |
| PR / commit link | (local worktree commit — not yet pushed/PR'd) |
| Related issue or gap ID | audit `docs/audit/2026-08-18-full-fleet-whole-app-audit.md` ranked blockers #3, #17; NEW findings N3, N4 |

## 1. Issue / gap identified

Five GitHub Actions workflow files referenced third-party and first-party GitHub Actions by a
mutable tag or branch ref (`@v7`, `@v8`, `@master`, `@main`) instead of a pinned commit SHA. A
mutable ref lets the action's maintainer (or anyone who compromises their account/repo) silently
change what code runs in our CI on the next workflow run, with no diff for us to review — a
supply-chain risk. The highest-severity instance, `deploy-metrics-agent.yml`, runs with
`FLY_API_TOKEN` in scope, so an unpinned `actions/checkout@v7` or
`superfly/flyctl-actions/setup-flyctl@master` there is a live deploy-credential exposure path, not
just a hygiene nit.

## 2. Root cause

An earlier sweep (referenced in the audit as "C18b") pinned most of the repo's GitHub Actions to
commit SHAs but missed `deploy-metrics-agent.yml` entirely (N3 — a regression against the earlier
"fixed" status), left one line still unpinned in `bootstrap-metrics-agent.yml` despite pinning the
other `uses:` line in the same file (N4), and never touched `claude-audit.yml`'s shellcheck step,
`deploy-driver-play-testing.yml`'s three action refs, or the `trufflehog` step inside
`ci-guardrails.yml`'s own security gate (ranked #17) — likely because that sweep was scoped to a
subset of workflow files rather than a full repo-wide grep for `uses: .*@(master|main|v\d+)`.

## 3. Fix / remediation

For each unpinned `uses:` line, replaced the mutable ref with the full 40-character commit SHA
that the tag/branch currently resolves to (verified via `git ls-remote`), keeping a trailing
`# <tag-or-branch>` comment for human readability — matching the exact convention already used
elsewhere in this repo's own workflow files (e.g. `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7`
in `ci-guardrails.yml`/`claude-audit.yml`). Where the same tag/branch was already pinned elsewhere
in the repo, reused that exact SHA for consistency instead of re-resolving independently (`actions/checkout@v7`,
`actions/setup-node@v7`, and `superfly/flyctl-actions/setup-flyctl@master` all match pins already
present in sibling workflow files).

Resolved SHAs (all verified via `git ls-remote` on 2026-08-19):

| Action | Ref | Resolved SHA |
|---|---|---|
| `actions/checkout` | `v7` | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| `actions/setup-node` | `v7` | `820762786026740c76f36085b0efc47a31fe5020` |
| `superfly/flyctl-actions/setup-flyctl` | `master` | `ed8efb33836e8b2096c7fd3ba1c8afe303ebbff1` |
| `ludeeus/action-shellcheck` | `master` | `00b27aa7cb85167568cb48a3838b75f4265f2bca` |
| `expo/expo-github-action` | `v8` | `c7b66a9c327a43a8fa7c0158e7f30d6040d2481e` |
| `trufflesecurity/trufflehog` | `main` | `e12da3c72f1fa4bd17a7345467d735c5aae1fbcf` |

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to CI/CD workflow files, config-only, no application code path touched.**
  This does not read/write any Supabase table, wallet, ride state, or background loop.
- Each edited `uses:` line pins to the exact commit the mutable ref resolves to *today*. This is
  purely additive-safe from a behavior standpoint: pinning does not downgrade any action to an
  older version — the SHA is the current tip of the tag/branch, so the next workflow run executes
  byte-for-byte the same action code it would have run unpinned, today. The only behavior change is
  forward-looking: future upstream commits to `master`/`main` on `flyctl-actions`, `action-shellcheck`,
  or `trufflehog`, or a future `v7`/`v8` tag republish, will no longer silently flow into our CI —
  a deliberate SHA bump (with review) will be required instead. That is the intended fix, not a
  side effect.
- Checked for any reusable-workflow (`workflow_call`) or matrix reference from another workflow
  file into these 5 files: `grep -r "uses:.*workflows/(deploy-metrics-agent|bootstrap-metrics-agent|claude-audit|deploy-driver-play-testing|ci-guardrails)\.yml"` across
  `.github/workflows/` — no matches. None of the 5 edited files are referenced as a reusable
  workflow elsewhere, so there is no indirect caller to consider.
- Other workflow files (`bootstrap-fly.yml`, `ci.yml`, `deploy-backend-staging.yml`, `deploy-fly.yml`)
  also reference `flyctl-actions`/`action-shellcheck`/`trufflehog` action *names* but are separate,
  independently-pinned (or out-of-scope) files — not modified here, per the task's explicit scope
  (only the 5 files named in the audit rows for #3/#17/N3/N4).
- No other workflow depends on these actions resolving to a *specific future* tag/branch release —
  each pin freezes at the current tip, which is a strict narrowing of exposure, not a functional
  downgrade.

## 5. User-experience effect

None. This change touches only internal CI/CD configuration (`.github/workflows/*.yml`). No
rider, driver, corporate-admin, or internal-admin facing surface is affected, mid-session or
otherwise.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/deploy-metrics-agent.yml` | `actions/checkout@v7` → pinned SHA; `superfly/flyctl-actions/setup-flyctl@master` → pinned SHA | Highest severity — runs with `FLY_API_TOKEN` in scope (ranked #3, N3) |
| `.github/workflows/bootstrap-metrics-agent.yml` | `actions/checkout@v7` → pinned SHA (the sibling `flyctl-actions` line was already pinned) | Completes the partial fix flagged as N4 |
| `.github/workflows/claude-audit.yml` | `ludeeus/action-shellcheck@master` → pinned SHA | Literal unpinned `@master` flagged as N4 |
| `.github/workflows/deploy-driver-play-testing.yml` | `actions/checkout@v7`, `actions/setup-node@v7`, `expo/expo-github-action@v8` → pinned SHAs | 3 unpinned refs flagged under ranked #17 |
| `.github/workflows/ci-guardrails.yml` | `trufflesecurity/trufflehog@main` → pinned SHA | Unpinned action inside the security gate itself (ranked #17) |

## 7. Before / after

Highest-severity file (`deploy-metrics-agent.yml`):

```
# Before
      - uses: actions/checkout@v7
      ...
        uses: superfly/flyctl-actions/setup-flyctl@master
```

```
# After
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
      ...
        uses: superfly/flyctl-actions/setup-flyctl@ed8efb33836e8b2096c7fd3ba1c8afe303ebbff1 # master (2026-08-19)
```

## 8. Rollback plan

Pure config change — `git revert` on this commit is a complete and safe rollback. Nothing is
applied to live data, no Stripe charge, wallet delta, ride state, or migration is involved. A
revert simply restores the mutable tag/branch refs; the only consequence of reverting is
re-exposing the original supply-chain risk, not any functional regression.

## 9. Verification performed

- [x] `python3 -c "import yaml; yaml.safe_load(open(...))"` run against all 5 edited files —
      confirmed valid YAML in each.
- [x] Grep swept all 5 edited files for remaining `@master`, `@main`, or bare `@v\d+` refs —
      zero remaining matches in scope.
- [x] Grep across all of `.github/workflows/` for any reusable-workflow (`workflow_call`) reference
      into the 5 edited files — none found, confirming no cross-file coupling was broken.
- [x] Each resolved SHA was independently verified via `git ls-remote --tags` / `git ls-remote
      refs/heads/<branch>` against the upstream GitHub repo on 2026-08-19, and cross-checked
      against the same tag/branch's SHA already pinned elsewhere in this repo's workflow files
      (all matched, confirming no drift between the earlier partial sweep and this fix).
- [ ] No automated tests apply — this is a config-only YAML change with no Python/JS test surface.

## 10. What was NOT verified

- Did **not** trigger an actual workflow run in GitHub Actions CI to confirm each pinned SHA still
  executes correctly end-to-end (e.g. that `flyctl-actions/setup-flyctl` at that SHA still
  installs flyctl correctly, or that the `trufflehog` action at that SHA still runs the expected
  scan). Since each SHA is simply the exact commit the previously-unpinned tag/branch already
  resolved to, behavior should be identical to what was already running — but this was not
  observed via an actual green CI run in this worktree (no push/PR was made per the task's
  instructions). This will be validated by CI itself when this branch's PR runs.
- Did not attempt to independently audit the *content* of the pinned commits (e.g. diffing
  `trufflehog@main` against the previous known-good state) — pinning trusts that the current tip
  of each tag/branch, as resolved via `git ls-remote` on 2026-08-19, is safe to freeze at. If any
  of these upstream repos were already compromised prior to this pin, that compromise would be
  captured, not prevented, by this fix.
