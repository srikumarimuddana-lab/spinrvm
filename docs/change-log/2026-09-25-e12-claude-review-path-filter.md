# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | TeamSpinr (session-assisted) |
| Surface(s) | backend (CI/CD config only — no application code) |
| Domain (Sentry tag) | admin (CI/governance, not a runtime domain) |
| PR / commit link | see `docs/audit/2026-09-25-contributor-governance-baseline.md` E12 |
| Related issue or gap ID | `ACTION_ITEMS.md` C7, E12 |

## 1. Issue / gap identified

`claude-review.yml`'s automatic PR trigger ran on every non-doc PR with no domain filter, so re-enabling it (currently blocked since 2026-08-01 on cost grounds, per C7) would review low-risk PRs (e.g. a UI copy tweak) at the same per-PR cost as a payments/auth change.

## 2. Root cause

The workflow's trigger only excluded `**/*.md`, `docs/**`, `.claude/**` — a volume filter, not a risk filter. It never distinguished "touches money/auth/dispatch/schema/safety" from "touches anything else."

## 3. Fix / remediation

Added a `paths:` allowlist to the `pull_request` trigger, scoped to the same domain paths `.github/CODEOWNERS` already treats as sensitive (payments, fare service, corporate, wallet, migrations, auth, config/middleware, crypto, rate limiter, security-gates workflow, dispatch service, rides routes, safety routes). The on-demand `/claude review` comment trigger is unchanged (still works on any PR, unfiltered) — this only narrows the *automatic* trigger.

## 4. Risk & impact on existing functionality

- No other workflow reads or is triggered by `claude-review.yml`'s trigger config — isolated to this one file.
- Blast radius: **isolated**. The workflow is currently inert in practice (`ANTHROPIC_API_KEY`/`CLAUDE_CODE_OAUTH_TOKEN` both unset — confirmed via the file's own `HAS_ANTHROPIC_KEY` guard, which still short-circuits to a no-op "Report that AI review is disabled" step). This change only affects *when the workflow would fire*, not what it does — since it can't do anything today (no secret set), this change has zero observable effect until someone sets the secret.
- No interaction with background loops, the ride state machine, or money/wallet deltas — this is CI trigger configuration, not application code.

## 5. User-experience effect

None. No rider/driver/corporate-admin/internal-admin-facing change. Not visible mid-session to anyone — this only affects which future PRs get an automatic AI review comment.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/claude-review.yml` | Replaced `paths-ignore` (doc-only exclusion) with a `paths` allowlist of CODEOWNERS' sensitive-domain globs on the `pull_request` trigger; updated header comment | Bounds automatic-review cost to the highest-risk paths, per E12's recommendation, without touching the on-demand comment trigger |
| `docs/ci/required-status-checks-2026-09-25.md` | New — reference list of required-status-check names for `main` branch protection | Separate deliverable for the "stale required-checks list" half of E12; requires repo-admin Settings access this session doesn't have |
| `docs/compliance/2026-08-13-sk-pst-rideshare-determination-needed.md` | Appended a dated note recording new input, explicitly not treating it as a resolution | Unrelated to this CI change but bundled in the same review pass; see that file for full reasoning |

## 7. Before / after

```yaml
# Before
on:
  pull_request:
    types: [opened, reopened, ready_for_review]
    paths-ignore:
      - '**/*.md'
      - 'docs/**'
      - '.claude/**'
```

```yaml
# After
on:
  pull_request:
    types: [opened, reopened, ready_for_review]
    paths:
      - 'backend/routes/payments*'
      - 'backend/services/fare_service.py'
      # ... (full list mirrors .github/CODEOWNERS' domain paths)
      - 'backend/routes/safety*'
```

## 8. Rollback plan

`git revert` is a complete rollback here — this is CI trigger config only, no data or live state involved, and the workflow has no observable runtime effect while the API key remains unset. Reverting restores the doc-only exclusion.

## 9. Verification performed

- [x] Reviewed against relevant CLAUDE.md convention: CODEOWNERS' own domain path list reused verbatim (not re-derived), so this doesn't introduce a third, drifting definition of "sensitive path" alongside CODEOWNERS and `labeler.yml`.
- [x] Confirmed via the file's own `HAS_ANTHROPIC_KEY` guard logic that the workflow is currently a no-op regardless of trigger scope (secret unset).
- [ ] Not run through an actual GitHub Actions trigger-evaluation dry run (no such tool available in this session) — the YAML `paths:` syntax matches GitHub's documented format and mirrors patterns already used elsewhere in this same file's own `paths-ignore` block, but the very first PR that touches one of the listed paths after this merges should be watched to confirm the trigger actually fires as expected.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated: isolated, currently a no-op in practice
- [x] No silent behavior change to an already-shipped flow — the workflow already isn't running today; this only changes trigger conditions for if/when it's turned on
