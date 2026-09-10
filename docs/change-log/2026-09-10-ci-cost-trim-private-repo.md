# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code session |
| Surface(s) | backend (CI/CD config only — `.github/workflows/ci.yml`, `.github/workflows/pr-checks.yml`, `.github/workflows/test-env.yml`, `scripts/ci-audit/error_classifier.py`) |
| Domain (Sentry tag) | admin (closest fit — deploy-pipeline/CI change, not application code) |
| PR / commit link | (filled in on push) |
| Related issue or gap ID | User request: "help trim the CI workflow set" (repo made private, Actions minutes now billed) |

## 1. Issue / gap identified

The repo was recently made private, so GitHub Actions minutes that were free/unmetered on the public repo are now billed against the account's monthly quota. A full-repo workflow audit found several genuinely redundant or over-broad CI jobs running on every push/PR with no corresponding safety benefit.

## 2. Root cause

Several workflows were written independently over time (some pre-dating a related job elsewhere) without cross-checking for duplicate coverage, and one job (`ci.yml`'s `security-scan`) was simply never given the `detect-changes` path-filter every other job in that file already has.

## 3. Fix / remediation

Three changes landed; a fourth was considered and reverted after a review pass found it unsafe (see below).

1. **`ci.yml`'s `docker-image-scan`**: removed the "Scan image with Trivy" / "Upload SARIF report" / "Upload image scan results to GitHub Security" steps. `security-gates.yml`'s `container-scan` (G6) already builds the identical image from the identical Dockerfile and runs the identical `exit-code: 1` Trivy scan (same severity, same `skip-files` exclusion) on the same trigger surface, and is the actually-enforced gate. Kept the Docker build, the CR-2026-002 diagnostic step (updated its comment to point at G6 instead of "the scan below," which no longer exists in this job), and the cosign-sign/GHCR-push steps (main-only, unchanged). Removed the now-unused `security-events: write`/`actions: read` permissions.
2. **`test-env.yml`**: removed the `backend-check` job — it duplicated `ci.yml`'s real `backend-test` and was already `continue-on-error: true`, so it wasn't actually gating anything. Removed the now-orphaned `PYTHON_VERSION` env var. Fixed `staging-health-check`'s `needs: [backend-check]` (would have been a dangling reference) by dropping the `needs:` entirely — that job is a live-URL health probe that never depended on `backend-check`'s pass/fail outcome. Renumbered the `# ─── N. ───` section comments to close the gap.
3. **`pr-checks.yml`**: gated `auto-label`, `size-advisory`, `merge-conflict-detect` to skip on a pure `edited` (title/body/base) PR event, since all three depend only on the diff/commits, not the description. Left `required-fields`, `expand-sections`, `auto-summary` untouched — those genuinely react to body content. Documented a known, accepted gap: `edited` also fires on a base-branch retarget, which does change the effective diff; these three jobs go stale until the next `synchronize` in that specific low-frequency case rather than re-running immediately.
4. **Cleanup**: `scripts/ci-audit/error_classifier.py`'s `SURFACE_JOBS["backend"]` still listed the deleted `backend-check` job name — removed.

**Reverted before merge**: a fourth change gated `ci.yml`'s `security-scan` job behind the same `detect-changes` filter as everything else. A `spinr-cicd-infra-reviewer` pass caught this as a real blocker: `ci.yml` already has an explicit, pre-existing comment stating `security-scan` "is deliberately NOT gated: secret-scanning must never be path-filtered — a secret can leak in any file type." My own justification for gating it (that `security-gates.yml`'s `gitleaks` would still catch a leak on an excluded path) was wrong — `gitleaks` (G5a) is `continue-on-error: true`, advisory only, not blocking. Gating `security-scan` would have silently converted this repo's only genuinely-blocking, unconditional secret scan into a path-filtered one for any PR touching only docs/config/root files — a real coverage gap, not a safe cost cut. Reverted to the original ungated state with a comment recording why it was considered and rejected.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to CI/CD trigger and step config across 4 files.** No application code, no runtime behavior, no data path touched.
- **`docker-image-scan` dedup**: the enforced CVE gate for the backend Docker image is now solely `security-gates.yml`'s `container-scan` (G6) — verified it runs on the same trigger surface this job did, so no PR that was previously scanned goes unscanned.
- **`test-env.yml`'s `backend-check` removal**: `ci.yml`'s `backend-test` already runs the real, blocking backend test suite on the same `staging` push/PR trigger, in a separate but concurrent workflow run. Kept `rider-app-check`/`driver-app-check` — those are the *only* local gate ahead of `test-env.yml`'s own expensive `build-mobile-test`/`ota-update-test` EAS jobs (cross-workflow blocking isn't possible in GitHub Actions, so `ci.yml`'s parallel checks can't substitute for these two staying in place).
- **`pr-checks.yml` `edited` gating**: purely advisory/hygiene jobs (labels, size comment, merge-commit detection) — no blocking check is affected.
- **Known follow-up, not resolved by this change**: this session cannot check GitHub's branch-protection UI for `staging`. If `backend-check` is listed there by name as a required status check, its removal would leave that check permanently "Expected, never reported" and block all future PRs to `staging` until the branch protection rule is updated to drop it. **Recommend checking Settings → Branches → staging's protection rule before or immediately after this merges.**

## 5. User-experience effect

None — internal CI/CD pipeline only. No rider/driver/admin-facing surface.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/ci.yml` | Removed duplicate Trivy scan/upload steps from `docker-image-scan`; trimmed its now-unused permissions; considered and reverted a `security-scan` gate | Dedup against `security-gates.yml`'s `container-scan`; avoid creating a real secret-scan coverage gap |
| `.github/workflows/test-env.yml` | Removed `backend-check` job, its `PYTHON_VERSION` env var, and the now-dangling `needs:` reference to it; renumbered section comments | Dedup against `ci.yml`'s real `backend-test`; job was already non-blocking |
| `.github/workflows/pr-checks.yml` | Added `github.event.action != 'edited'` to 3 diff-dependent jobs' `if:` | Skip redundant re-runs on a pure PR-description edit |
| `scripts/ci-audit/error_classifier.py` | Removed stale `backend-check` job-name reference | Job no longer exists |

## 7. Before / after

```yaml
# Before (ci.yml docker-image-scan, abbreviated)
      - name: Scan image with Trivy
        uses: aquasecurity/trivy-action@...
        with: { exit-code: '1', ... }
      - name: Upload SARIF report
        ...
      - name: Upload image scan results to GitHub Security
        ...
      - name: Install cosign
        ...
```

```yaml
# After
      - name: Install cosign
        ...
```

```yaml
# Before (test-env.yml)
  staging-health-check:
    runs-on: ubuntu-latest
    needs: [backend-check]
```

```yaml
# After
  staging-health-check:
    runs-on: ubuntu-latest
```

## 8. Rollback plan

`git revert`-safe for all 4 files. No live data or in-flight workflow-run entanglement — reverting restores the prior (more expensive, but functionally identical) CI behavior. The one thing a revert does *not* undo automatically: if the `staging` branch protection rule was manually updated to drop `backend-check` as a required check (per the follow-up above), that GitHub Settings change would need to be manually restored too — flagged, not something this diff can control.

## 9. Verification performed

- [x] `yaml.safe_load` against all 3 edited workflow files, both before and after the correction — all parse clean.
- [x] `ast.parse` against the edited Python file — syntax clean.
- [x] Blast-radius grep performed: confirmed no job anywhere in `ci.yml` has `needs: [security-scan]` or `needs: [docker-image-scan]`; confirmed every remaining job's `needs:` in `test-env.yml` is internally consistent (no dangling references); confirmed no other file references the deleted `backend-check` job name besides the one cleaned up here.
- [x] Two full `spinr-cicd-infra-reviewer` passes run — one on the full-repo audit that proposed the original 4 candidates (one of which, deleting `test-env.yml`'s `rider-app-check`/`driver-app-check`, was independently caught as unsafe during implementation, before the reviewer was even asked), and a second, final pass on the actual diff, which caught the `security-scan` gating blocker described above. Both findings were fixed before this was written up, not shipped and discovered later.
- [ ] **No real GitHub Actions run observed for any of these changes** — not possible from this session (no billing/Actions-run-triggering access beyond what a real push provides). This PR's own CI run is the first real-world test.
- [ ] `actionlint` — not available in this session (same disclosed gap as prior CI-config changes in this repo). Only generic YAML parsing was performed.

## What was NOT verified

- **Whether `staging`'s branch protection rule lists `backend-check` as a required status check by name** — this session has no access to GitHub's branch-protection UI/API scope to check. Flagged explicitly in Section 4 as a follow-up for a human to confirm before/after merge, rather than assumed safe.
- **The `pr-checks.yml` base-branch-retarget edge case** was identified, not fixed — accepted as a documented, low-frequency gap rather than adding guard logic, per the comment now in that file.
- **No real Actions-minutes savings were measured** — this change is based on removing genuinely duplicate job execution (confirmed via line-by-line comparison of the duplicated jobs' actual steps/parameters, not estimated), not a billing-dashboard before/after comparison, which this session cannot access.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no live-data entanglement, one flagged exception noted explicitly)
- [x] Blast radius is stated, not assumed (every claim above backed by an actual grep/read, not inference — including the one that reversed an earlier, incorrect trim candidate)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (none — Section 5 states plainly there is none)
