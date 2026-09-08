# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | Claude (session implementing PR #5085's hardening plan) |
| Surface(s) | backend (CI/CD infra — no application code) |
| Domain (Sentry tag) | admin (delivery pipeline; not a runtime domain) |
| PR / commit link | branch `claude/pr-5085-5079-hardening-5a2aj7` |
| Related issue or gap ID | Testing finding, validated in PR #5085; tracked as `ACTION_ITEMS.md` C87 |

## 1. Issue / gap identified

Two raw `curl`-based scanner-binary downloads in CI had no retry and no verification of the downloaded archive before extracting it: `ci.yml`'s `security-scan` job (`Install trufflehog v3`) and `security-gates.yml`'s `bundle-secrets`/G5b job (gitleaks download, piped directly into `tar`). Both had failed once on a transient network/CDN issue (a non-gzip trufflehog download; an HTTP 504 on the gitleaks download) and both passed on the next run with no code change — consistent with flakiness, not a real defect, but with no protection against it recurring.

## 2. Root cause

Neither download step was ever written defensively: a single `curl` attempt with no `--retry`, and no check that the downloaded bytes were actually a valid gzip archive before handing them to `tar`. A truncated or HTML-error-page response produces a cryptic `tar` error instead of a clear "download failed" one. Separately, two Trivy→SARIF→GitHub-Security-upload pairs in `ci.yml` guarded the upload step with `if: always()` but no check that the scan step actually produced the SARIF file — a failed scan cascades into a second, misleading "file not found" failure on the upload step instead of one clear failure.

## 3. Fix / remediation

- `ci.yml`'s "Install trufflehog v3": download to a file with `curl -fsSL --retry 5 --retry-delay 3 --retry-all-errors`, then `tar -tzf ... >/dev/null` to verify the archive before `tar -xzf` extracts it.
- `security-gates.yml`'s `bundle-secrets` gitleaks download: same download-then-verify-then-extract pattern, replacing the previous `curl ... | tar -xz` pipe.
- `ci.yml`'s two SARIF uploads (`security-scan`'s `trivy-results.sarif`, `docker-image-scan`'s `trivy-image.sarif`): both `if: always()` guards gained `&& hashFiles(...) != ''`, so a failed scan step produces one clear failure instead of two.
- `security-gates.yml`'s G5a (git-history gitleaks scan) was deliberately left untouched — it uses the pinned `gitleaks/gitleaks-action` GitHub Action, not a raw `curl` download, so it isn't exposed to this class of flake.

## 4. Risk & impact on existing functionality

- **Blast radius**: two workflow files, four steps total. No application code touched, no change to what any scan actually checks or blocks on — only how the download/upload steps handle a transient failure.
- **Retry does not mask a real problem**: `--retry-all-errors` retries on transient failures (timeouts, 5xx, connection resets); a genuinely broken URL or a real 4xx still fails after retries exhaust, so a persistent breakage (e.g. a yanked release) still fails the job, just after up to ~5 retries with backoff instead of on the first attempt.
- **`hashFiles()` guard changes no currently-passing run's outcome**: it only changes behavior on the already-failing path (scan step failed → SARIF file absent) by producing one failure signal instead of two; a successful scan run's upload is unaffected since the file exists.
- **No change to any blocking/`continue-on-error` semantics** — `security-scan`, `docker-image-scan`, and `bundle-secrets` (G5b) keep their existing pass/fail gating; this change only makes their failure mode clearer on the one specific class of transient network flake it targets.

## 5. User-experience effect

None — CI/delivery-pipeline only, no rider/driver/corporate-admin/internal-admin-facing change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/ci.yml` | trufflehog download: retry + `tar -tzf` verify before extract; both Trivy→SARIF upload steps gained a `hashFiles(...) != ''` guard | Absorb a transient CDN/network flake instead of failing the job on it; avoid a second, misleading failure when the scan step itself already failed |
| `.github/workflows/security-gates.yml` | `bundle-secrets` (G5b) gitleaks download: same retry + verify-before-extract pattern, replacing a bare pipe into `tar` | Same |

## 7. Before / after

```yaml
# Before
- name: Install trufflehog v3
  run: |
    curl -sLo trufflehog.tar.gz \
      https://github.com/trufflesecurity/trufflehog/releases/download/v3.94.3/trufflehog_3.94.3_linux_amd64.tar.gz
    tar -xzf trufflehog.tar.gz trufflehog
    chmod +x trufflehog && sudo mv trufflehog /usr/local/bin/
```

```yaml
# After
- name: Install trufflehog v3
  run: |
    curl -fsSL --retry 5 --retry-delay 3 --retry-all-errors -o trufflehog.tar.gz \
      https://github.com/trufflesecurity/trufflehog/releases/download/v3.94.3/trufflehog_3.94.3_linux_amd64.tar.gz
    tar -tzf trufflehog.tar.gz >/dev/null
    tar -xzf trufflehog.tar.gz trufflehog
    chmod +x trufflehog && sudo mv trufflehog /usr/local/bin/
```

```yaml
# Before
- name: Upload Trivy results to GitHub Security
  if: always()
  with: { sarif_file: 'trivy-results.sarif' }
```

```yaml
# After
- name: Upload Trivy results to GitHub Security
  if: always() && hashFiles('trivy-results.sarif') != ''
  with: { sarif_file: 'trivy-results.sarif' }
```

## 8. Rollback plan

`git revert` — pure workflow-config change, no schema, no data, no flag. Reverting restores the previous download/upload steps exactly (and their original flake exposure).

## 9. Verification performed

- [x] YAML syntax validated with `yaml.safe_load` on both edited files — clean.
- [x] Reasoned through `curl -fsSL --retry 5 --retry-delay 3 --retry-all-errors -o file url` semantics by hand: `-f` fails on non-2xx (so a 404/504 is treated as a failure `curl` can retry, not silently written to `file` as an HTML error body); `-S` shows the error even with `-s`; `--retry-all-errors` (curl ≥7.71, present on `ubuntu-latest`'s stock curl) extends retry to cases plain `--retry` wouldn't cover.
- [x] Grepped `security-gates.yml` for any other raw `curl` scanner-binary download — only the one in `bundle-secrets`; G5a confirmed to use the pinned `gitleaks-action` instead.
- [x] `spinr-cicd-infra-reviewer` subagent review: verdict **SAFE TO MERGE**, no blockers or warnings. Confirmed `-f` is required for `--retry`/`--retry-all-errors` to trigger at all (without it, a non-2xx body is treated as a successful download); confirmed `--retry-all-errors` (curl ≥7.71) is well within what `ubuntu-latest` ships; confirmed the `hashFiles()` guard doesn't mask a real vulnerability finding (the blocking `exit-code: '1'` Trivy scan step is separate from and earlier than the SARIF-upload step); confirmed no `GITHUB_TOKEN` scoping or path-mismatch issue.

## What was NOT verified

Not exercised against a real GitHub Actions run — no Actions-dispatch access from this session to force a transient-failure scenario and observe the retry actually recover, or to confirm the exact `ubuntu-latest` curl version supports every flag used. The original two flakes were one-off and already resolved by simple re-runs before this fix existed, so there is no currently-reproducing failure to re-test against.
