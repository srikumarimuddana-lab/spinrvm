# Required status checks — refreshed list for `main` branch protection (E12)

**Action needed from a human with repo-admin access.** No tool available to any Claude Code session in this repo can read or write GitHub branch-protection rules — this was independently confirmed during the C73/E8 investigation (`ACTION_ITEMS.md`, search "no tool in this session's GitHub MCP toolset exposes branch-protection rules"). This doc is the concrete list to paste into **Settings → Branches → Branch protection rules → `main` → Require status checks to pass before merging**.

## Why aggregator jobs, not every individual job

This repo's workflows run 40+ individual jobs across `ci.yml`, `ci-guardrails.yml`, `security-gates.yml`, and `pr-checks.yml`, many of them conditional on `detect-changes` (they don't run at all on a PR that doesn't touch their surface). Requiring an individual conditional job as a branch-protection status check is a known failure mode: if the job never runs on a given PR (because its path wasn't touched), GitHub can leave that PR blocked indefinitely waiting for a status that will never post — this is exactly the "blocked indefinitely with no actionable error" scenario `ACTION_ITEMS.md` C9 already flagged for a different check. The fix each guardrail workflow already uses internally is a **summary/aggregator job** that always runs (`if: always()`), inspects every constituent job's result, and reports one pass/fail — that's the correct unit for branch protection, not the leaf jobs underneath it.

One of those aggregators had a real bug until 2026-09-08 (`ACTION_ITEMS.md` C75): "Security gates summary" and "Post guard rail summary" used `if: always()` with no check of constituent job results, so they always reported success regardless of what actually failed underneath. That's fixed (both now `exit 1` if any dependency failed or was cancelled) — this list assumes that fix is on `main` (verify the fix commit, referenced in C75, is present before enabling these as required).

## The list

| Check name (as GitHub will show it) | Source workflow | What it gates |
|---|---|---|
| `Post guard rail summary` | `ci-guardrails.yml` | Coverage regression, corporate/money-path/admin coverage floors, lint trend, security posture, migration safety, breaking-change detection, mobile test placement, Change Impact Log presence, offline guard helpers |
| `Security gates summary` | `security-gates.yml` | bandit, eslint-security, semgrep, pip-audit, yarn-audit, npm-audit-admin, gitleaks, bundle-secrets, container-scan, license checks |
| `Migration Safety Check` | `migration-check.yml` | CHECK B (duplicate/colliding migration prefixes) and the other migration-file safety rules |
| `backend-test` | `ci.yml` | Core backend test suite — path-filtered via `detect-changes`, but backend is touched by the overwhelming majority of PRs; verify in Settings whether GitHub's "only require when applicable" option is available for this check before requiring it unconditionally (see caveat below) |
| `required-fields` | `pr-checks.yml` | Blocks a PR whose body is missing a required Tier 1–4 template field |
| `merge-conflict-detect` | `pr-checks.yml` | Flags an unresolved merge conflict against the base branch |

**Caveat on `backend-test`:** unlike the three summary jobs above, this one is genuinely conditional (skipped entirely on a docs-only or frontend-only PR). If your GitHub plan/UI doesn't expose "only require when the check is present" for branch protection, requiring `backend-test` unconditionally will block every docs-only PR (like the one that shipped this file) forever, since the check never posts a status. Two options: (a) if the newer branch-protection UI supports conditional required checks, use it here; (b) if not, leave `backend-test` off the required list and rely on `Post guard rail summary` (which already includes backend-test-dependent coverage gates) as the backend-correctness gate instead.

## What this does NOT include, and why

- **Claude PR review (`claude-review.yml`)** — deliberately advisory, never required. It's designed with `continue-on-error: true` specifically so a missing API key or a model error never blocks merging (see that file's own header comment). Making it a required check would contradict its own design and reintroduce the "blocked indefinitely" failure mode the day the secret is unset again.
- **Individual coverage-floor/lint-trend/security-posture jobs** — already covered by `Post guard rail summary`; requiring them individually is redundant and reintroduces the conditional-job blocking risk above.
- **`Codex` (chatgpt-codex-connector)** — third-party app review, not a GitHub Actions check; branch protection can't require it the same way. Track its activity via the PR review handling process in `CLAUDE.md` instead.

## Also worth doing at the same time (from the same E8/C73 investigation, still open)

These are the same repo-admin-only Settings actions the prior investigation identified and could not close from an engineering session:
1. **Enable "Require review from Code Owners"** in Settings → Branches → `main`, now that `.github/CODEOWNERS` names real, resolvable accounts (fixed 2026-08-28 per that file's own header).
2. **Confirm no branch-protection bypass/admin-merge allowance exists** for any account on `main` — the C73/#5048 incident's root cause was a real branch-protection gap (a PR merged with zero reviews and failing checks), not a code defect, and this can only be checked in Settings → Branches, not from any tool.
3. **Set `ANTHROPIC_API_KEY` (or `CLAUDE_CODE_OAUTH_TOKEN`) with a spend cap** if/when you're ready to turn Claude review back on — the path-filtering change (see `docs/change-log/2026-09-25-e12-claude-review-path-filter.md`) already bounds its automatic trigger to money/auth/dispatch/schema/safety paths, addressing the cost objection that's kept it off since 2026-08-01.

Once applied, update `ACTION_ITEMS.md` E8/C73's status lines to reflect the settings change (with a screenshot or a one-line confirmation of what was enabled) — this doc plus that update closes the "required-checks list is stale" half of E12. The other half (Claude review re-enablement) needs the secret decision above, which is a cost/risk call only the owner can make.
