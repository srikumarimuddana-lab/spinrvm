# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Author | Claude (session_01Wk3M9NdQJWqgpATtogSjD8) |
| Surface(s) | CI/CD infra (`.github/workflows/`) — not a live-tested rider/driver/corporate surface |
| Domain (Sentry tag) | n/a (infra, not app code) |
| PR / commit link | branch `claude/c18-pin-action-shas` |
| Related issue or gap ID | ACTION_ITEMS.md C18 |

## 1. Issue / gap identified

Every `uses:` step across `.github/workflows/*.yml` referenced a GitHub Action by a mutable major-version tag (`@v7`, `@v3`, etc.) instead of an immutable commit SHA. A compromised or malicious action maintainer can silently repoint a version tag to different code — the exact mechanism behind the real-world `tj-actions/changed-files` and `reviewdog/action-setup` supply-chain incidents. GitHub Advanced Security / Semgrep (`yaml.github-actions.security.github-actions-mutable-action-tag`) flagged this on every new workflow line.

## 2. Root cause

No prior pinning convention existed for this repo's workflows; every action reference was added as a plain `@vN` tag as workflows were authored over time. Not a regression — a standing gap, previously undone because a prior investigation (documented in this same C18 entry) hit a real blocker: this session's GitHub access is scoped to `srikumarimuddana-lab/spinrvm` only, so a direct `api.github.com/repos/<action>/...` call to resolve a tag to a SHA was rejected, and the only fallback (scraping a rendered release page) was correctly rejected as unreliable for a character-exact 40-hex-digit value.

## 3. Fix / remediation

Resolved the blocker: this session's git proxy serves anonymous, read-only `git clone`/`git ls-remote` access to any public GitHub repository even when it isn't in this session's attached repository scope (confirmed via the `add_repo` tool against a public repo — it reports read access is already available with no attachment needed). Used that channel to `git clone --depth 1` all 19 distinct action repositories referenced across the workflows (18 originally enumerated + `github/codeql-action`, found during the sweep — see §4) and resolve each pinned tag to its exact commit SHA via `git ls-remote --tags --refs origin refs/tags/<tag>` against the real upstream repo — not a scrape, a direct read of the actual git ref database. Every resolved SHA was independently cross-checked as a live ref tip on `origin` before use (see §9).

All 176 `uses:` references across 23 workflow files were then rewritten from `uses: <action>@vN` to `uses: <action>@<40-char-sha> # vN`, preserving the human-readable version as a trailing comment per the C18 entry's own stated acceptance bar.

One tag resolved to something worth flagging on its own: `8398a7/action-slack@v3` has no `v3` **tag** in that repo at all — `v3` resolves to a **branch**, which is more mutable than even an unpinned major-version tag normally is (a tag can still be re-pointed, but a branch is *expected* to move on every push). This makes that reference the single riskiest one pinned in this sweep.

## 4. Risk & impact on existing functionality

**Blast radius: repo-wide but purely mechanical and additive-in-effect** — no workflow's *behavior*, trigger conditions, job structure, or `with:` inputs changed. Every edit is `@vN` → `@<sha> # vN` on an existing `uses:` line; nothing else on any line moved. Verified three ways before treating this as safe:

1. **Every action resolves to the exact commit the tag pointed to at resolution time** — cross-checked programmatically (see §9), 176/176 references match the resolved SHA map with zero typos or transpositions.
2. **YAML syntax validated** — all 24 files in `.github/workflows/` (23 edited + 1 untouched) parse cleanly via `yaml.safe_load` after the edit.
3. **No unpinned `uses:` line survives the sweep** — a repo-wide grep for the un-pinned pattern after editing returns zero matches.

Grepped for every other consumer of these workflow files: there are none inside the repo (workflow YAML is not imported or referenced by application code) — the only "consumer" is GitHub Actions itself resolving each `uses:` line at run time, which is exactly the pinned commit that the tag pointed to when this sweep ran (i.e., functionally identical to what the tag would have resolved to on this exact day). The only way this diff changes behavior going forward is the intended one: future upstream tag repoints (malicious or benign) no longer silently take effect — a *reduction* in blast radius, not an expansion of it.

**Named risk (not hypothetical, called out on purpose):** pinning to a SHA freezes each action at today's `@vN` tip. A benign upstream bugfix released under the same `@vN` tag after today will **not** be picked up automatically anymore — Dependabot's `github-actions` ecosystem update (already configured in `dependabot.yml` for this repo, unaffected by this change) is what will now surface those as version-bump PRs, each carrying its own new verified SHA. This is the standard, intended trade-off of SHA-pinning, not a defect introduced here.

## 5. User-experience effect

None. Purely CI/CD infrastructure — no rider, driver, corporate-admin, or internal-admin-facing behavior changes. Not visible to anyone mid-session; this only affects what commit of each GitHub Action a workflow run resolves at trigger time.

## 6. Files modified

23 files under `.github/workflows/`, each changed only on existing `uses:` lines (`@vN` → `@<sha> # vN`, no other content touched):

| File path | References pinned |
|---|---|
| `apply-supabase-schema.yml` | 1 (checkout) |
| `bootstrap-fly.yml` | 1 (checkout) |
| `ci-error-audit.yml` | 8 (checkout, setup-python, download-artifact, upload-artifact, action-slack) |
| `ci-guardrails.yml` | 10 (checkout, setup-python, setup-node, github-script) |
| `ci.yml` | 66 (all 12 actions used in this repo's largest workflow) |
| `claude-audit.yml` | 3 (checkout, github-script) |
| `claude-review.yml` | 2 (checkout, claude-code-action) |
| `dependabot-auto-merge.yml` | 1 (fetch-metadata) |
| `deploy-backend.yml` | 1 (checkout) |
| `deploy-fly.yml` | 1 (checkout) |
| `eas-build.yml` | 6 (checkout, setup-node, paths-filter, expo-github-action) |
| `eas-native-build.yml` | 3 (checkout, setup-node, expo-github-action) |
| `migration-check.yml` | 2 (checkout, setup-python) |
| `mobile-bundle-smoke.yml` | 6 (checkout, setup-node, cache) |
| `mobile-dep-check.yml` | 8 (checkout, setup-node, cache) |
| `pip-compile-check.yml` | 2 (checkout, setup-python) |
| `pr-checks.yml` | 6 (labeler, github-script, checkout) |
| `security-gates.yml` | 22 (checkout, setup-python, setup-node, upload-artifact, trivy-action, codeql-action, cosign-installer, gitleaks-action) |
| `subprocessor-audit.yml` | 2 (checkout, setup-python) |
| `subprocessor-monitor.yml` | 1 (checkout) |
| `sync-mobile-lockfiles.yml` | 2 (checkout, setup-node) |
| `test-env.yml` | 15 (checkout, setup-python, setup-node, cache, expo-github-action) |
| `update-visual-baselines.yml` | 2 (checkout, setup-node, upload-artifact) |

Plus `ACTION_ITEMS.md` (C18 entry closed) and this file.

## 7. Before / after

```yaml
# Before (every file, representative line)
- uses: actions/checkout@v7
```

```yaml
# After
- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
```

## 8. Rollback plan

`git revert` is fully sufficient and safe here — this is a pure text substitution on CI config with no data, migration, or feature-flag dimension. Reverting restores the mutable `@vN` tags exactly as they were; no second deploy or data cleanup needed. If a specific SHA turns out to be wrong (e.g. a workflow starts failing with "action not found"), the fix is a single-line edit back to that one `@vN` tag while the rest stay pinned — no need to revert the whole sweep.

## 9. Verification performed

- [x] **SHA resolution verified against the real upstream repo, not guessed or scraped.** All 19 action repos were `git clone --depth 1`'d via this session's anonymous public-repo git-proxy read access (confirmed available via `add_repo`, which reported "read access already available" without needing repository-scope attachment). Each `@vN` tag was resolved with `git ls-remote --tags --refs origin refs/tags/<tag>` directly against that clone's `origin` remote — a read of the actual git ref database, the same information `gh` or the GitHub API would return, not a rendered-page scrape.
- [x] **Every resolved SHA independently cross-checked as a live ref tip on origin** before use — `git ls-remote origin | grep <sha>` returned ≥1 match for all 19 SHAs (see script output in this session's transcript), confirming each SHA is a real, currently-existing commit on that repo, not a typo or hallucination.
- [x] **Post-edit cross-check**: a script re-scanned all 23 edited files and confirmed every pinned `<sha>` in the repo matches the resolved SHA map exactly — 176/176 references, 0 mismatches.
- [x] **Zero unpinned references remain** — repo-wide grep for the unpinned `uses: owner/repo@vN` pattern (and the subpath form `owner/repo/subdir@vN`, which the original C18 grep's pattern would have missed — see §4's `github/codeql-action` note) returns no matches after the edit.
- [x] **YAML syntax validated** — `yaml.safe_load` succeeds on all 24 files in `.github/workflows/` after the edit (23 changed + 1 with no matching pattern).
- [ ] Manual repro / staging run — not performed; these are CI-only config changes with no staging environment distinct from "the next PR's own CI run." The first real-world verification is this change's own PR running its own CI (self-referential but appropriate — if a pinned SHA were wrong, that PR's own checks would fail to resolve the action and turn red immediately, a fail-loud failure mode by design, not a silent one).

## 10. What was NOT verified

- No live GitHub Actions run was triggered from a sandboxed/test context before this PR — the first real exercise of every pinned SHA is this PR's own CI run. A wrong SHA fails loudly (action does not resolve) rather than degrading silently, which is the standard, accepted risk profile of SHA-pinning.
- Did not audit whether any of these 19 actions' current tagged commit differs in *behavior* from an earlier point release under the same tag (e.g. whether `codecov/codecov-action@v6`'s tip today matches what was running before this repo last touched that line) — pinning intentionally freezes at "whatever the tag currently resolves to," which was also true (just silently, and re-resolved on every run) before this change.
- Did not extend this sweep to `dependabot.yml`'s own action-version references (if any) or to any `uses:` reference inside non-workflow YAML (e.g. `.gitleaks.toml` is not a workflow); scope was `.github/workflows/*.yml` per the C18 entry's own stated scope.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, single-line fix for one wrong SHA if ever needed)
- [x] Blast radius is stated, not assumed (§4 — repo-wide but behaviorally inert; only future tag-repoint resistance changes)
- [x] No silent behavior change to an already-shipped flow — this is additive-only pinning of what already resolves today; §5 confirms zero user-facing effect
