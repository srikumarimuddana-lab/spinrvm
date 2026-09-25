# Change Impact & Risk Log — block real phone numbers at commit time

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (AI-assisted) |
| Surface(s) | dev tooling only — `.claude/hooks/pre-commit`, `.gitleaks.toml` (CI G5a/G5b), `tests/hooks/` |
| Domain (Sentry tag) | n/a (no runtime code) — PIPEDA / PII hygiene |
| PR / commit link | branch `claude/block-real-phone-numbers` |
| Related issue or gap ID | Follow-up to PR #5812 (real driver phone numbers removed from docs and test fixtures) |

## 1. Issue / gap identified

Real Saskatchewan driver phone numbers had been committed to `ACTION_ITEMS.md`, two audit docs and three backend test fixtures (removed by PR #5812). Nothing in the commit path or CI caught them.

## 2. Root cause

No detector for phone numbers existed anywhere:
- The pre-commit hook's check 4 ("PII in logs") only greps for log/print calls that name a phone *variable*. It never looks at literal numbers, and it ignores `.md` files.
- gitleaks' built-in ruleset targets secret shapes (API keys, tokens). The two custom PII rules in `.gitleaks.toml` (`spinr-sin-bank-pii`, `spinr-driver-export-pii`) only fire in files that also contain one of their column-name keywords, so a phone number in a doc or a test fixture never matches.

## 3. Fix / remediation

- **Pre-commit check 12/12 (blocking).** It scans only the *added* lines of staged files (`git diff --cached -U0`) for a North-American (NANP) number in any common shape: `+1 AAA EEE LLLL`, `(AAA) EEE-LLLL`, `AAA-EEE-LLLL`, `AAA.EEE.LLLL`, `AAAEEELLLL`, `+1AAAEEELLLL`, `1-AAA-EEE-LLLL`. It blocks the commit unless the number falls in the range reserved for fiction, 555-0100..555-0199, with any area code (for example `306-555-0142`).
  - **How false positives are kept down.** The area code and exchange must start with 2-9 (the NANP rule), which keeps 10-digit Unix timestamps (`1xxxxxxxxx`) out. A match must not touch a letter, a digit, `_`, or a leading `.`. That excludes hex, UUID and Stripe-ID fragments, numeric IDs longer than 11 digits, and decimal fractions. A bare digit run that follows `-` is allowed (URL IDs such as `issuecomment-NNNNNNNNNN`), and so are the int32 and uint32 limits. A bare run after `/` is deliberately *not* allowed: links such as `wa.me/<number>` carry real numbers. Only 3 URL-path IDs in the whole tree hit this.
  - **Skipped files.** Lockfiles (`package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `*.lock` …) and binary or asset extensions (`svg`, `png`, `jpg`, `pdf`, fonts, `map` …) are not scanned. Binary diffs have no `+` lines anyway.
  - **Masked output.** The hook prints `file:line: ***-***-LLLL`. It never shows the full number.
  - **Fails closed.** If `git diff` or `awk` fails, the check blocks and explains why. It does not report "clean".
  - **No new bypass.** The hook has no per-check escape hatch today. Its only bypass is git's own `--no-verify` ("Emergency bypass only"), and none was added. A per-line "allow" marker would sit right next to a pasted real number, which defeats the purpose.
  - **Portability.** The check uses POSIX awk only, with no `{n}` intervals and no `grep -P`, so it runs on mawk, gawk and macOS awk.
- **`.gitleaks.toml` rule `spinr-nanp-phone-number`.** It uses the same regex and the same allowlist, applied through `regexTarget = "match"` so that the `-` in front of a bare ID is visible. `secretGroup = 1` keeps the boundary character out of the reported secret.
- **Tests.** `tests/hooks/test_pre_commit_phone_numbers.sh` has 30 assertions covering block and allow cases, masking, file:line reporting and fail-closed behaviour.

## 4. Risk & impact on existing functionality

- **Runtime blast radius: none.** No backend, rider-app, driver-app, admin-dashboard or shared code changed.
- **Developer blast radius (hook).** Every commit made with the hook installed now runs check 12. The hook is installed by `scripts/setup-claude.sh` and `.claude/settings.json` SessionStart. The check reads only added lines, so existing content is never re-flagged unless a line containing it is edited or re-added.
  - **Friction: the existing 555-xxxx fixture style is blocked.** The current tree has **604** phone-shaped strings that use the 555 exchange outside 01xx (the `…555` + 4-digit fixture style used across `backend/tests/`, `rider-app/__tests__/`, `driver-app/__tests__/` and `shared/**/__tests__/`). Editing one of those *lines*, or copying the pattern into a new test, will now block. The fix is to switch that fixture to 555-01xx while touching it, as PR #5812 did. This follows the rule as specified (only 555-0100..0199 is reserved for fiction; other 555 numbers are assignable). If this proves too noisy, the owner can widen the allowlist to all 555 exchanges in one line in each file.
- **Full current-tree measurement.** Tracked files, hook logic: **688 matches in 158 files**. The gitleaks rule finds 689; the one extra is a chunk-boundary artifact on an 11-digit Actions run ID in the very large `ACTION_ITEMS.md`. The matches break down as:

  | Category | Count | Where |
  |---|---|---|
  | 555-exchange fixtures outside 01xx | 604 | backend/tests (most), rider-app/driver-app/shared tests, a few docs |
  | Obvious sequential/repeated fixtures (`…1234567`, `…2222`, `…9999`, including far-future JWT `exp` values in tests) | 37 | backend tests, rider-app/driver-app emergency-contact tests |
  | 10-digit GitHub review-comment IDs in tables | 23 | `docs/change-log/2026-09-23-pr5722-review-verification.md`, `docs/change-log/2026-09-24-pr5748-codex-review-fixes.md` |
  | Bare Sentry issue IDs in prose | 4 | a stripe-preauth change-log, one driver-app test, one backend test |
  | URL path IDs after `/` (help-center article IDs, a Sentry issue URL) | 3 | `docs/superpowers/specs/…`, `docs/incidents/…`, `docs/audit/clean-sheet/03-benchmark.md` |
  | 10-digit JWT `exp` values after 2033 | 2 | `reports/` |
  | App Store Connect app IDs | 2 | `rider-app/eas.json`, `driver-app/eas.json` |
  | Legacy numeric driver IDs | 2 | `backend/tests/test_dual_run_monitor.py` |
  | Docstring regex examples | 2 | `backend/ai/stream_filter.py`, its test |
  | Published vendor support line (toll-free) | 1 | `docs/runbooks/security-incident.md` |
  | **Non-555 Saskatchewan-area numbers — need a human look (possibly real)** | 8 | see below |

  The 8 Saskatchewan-area (306/639) matches outside the 555 exchange are shown masked. PR #5812 did not cover them, and this PR's file scope does not allow editing them:
  - `backend/diagnose_driver_ws.py` lines 17 and 209, `***-***-3307`. This is a CLI help/docstring example.
  - `docs/audit/2026-08-16-legacy-ride-count-drop-investigation.md` line 77, `***-***-3307`. It is the same number, in a table row the doc itself labels "real".
  - `backend/tests/test_legacy_mongo_driver_import_service.py` lines 618, 701, 722 and 735, plus `backend/tests/test_admin_legacy_driver_import.py` line 344, all `***-***-8526`. These are legacy-import fixtures.
- **CI — G5a (gitleaks git history, `security-gates.yml`).** gitleaks-action auto-loads `.gitleaks.toml`, so the new rule applies. The job is `continue-on-error: true`, so the new findings cannot turn it red.
  - Pull request and push runs scan only the new commits. A PR that adds a phone-shaped line gets a (non-blocking) finding, and gitleaks-action may post a PR comment for it.
  - Scheduled and dispatch runs scan full history, so they will now also list historical numbers, including the ones PR #5812 removed (still in history) and the 555-xxxx fixtures.
  - **Follow-up for whoever flips G5a to blocking:** those findings will need a gitleaks baseline (`--baseline-path`) or a history rewrite first. Otherwise this rule alone keeps G5a red.
- **CI — G5b (admin bundle scan, blocking).** Compiled, minified output under `.next/` contains coincidentally phone-shaped numeric literals, so the new rule carries a *rule-scoped* `paths = ['''\.next/''']` allowlist. Every other rule still scans `.next/` exactly as before. This is not the global `.next/` exclusion the file header warns against.
  - Checked with gitleaks 8.18.4 (G5b's pinned version) on a synthetic `admin/.next/static/*.js` containing a phone-shaped literal: no finding.
  - **Not checked:** a real `npm run build` of admin-dashboard followed by a G5b scan (see §9).
- **CI — ci.yml `deploy-admin` scan.** Unaffected. It uses `admin-dashboard/.gitleaks.toml`, which extends only gitleaks' defaults, not this file.
- **Hook check 1.** If gitleaks is installed locally, `gitleaks protect --staged` now also reports this rule, which duplicates check 12. Both block, and both are redacted or masked.

## 5. User-experience effect

Nobody who uses the apps sees a difference (riders, drivers, corporate admins, internal admins). Developers and agents committing with the hook installed now get a blocking check 12 when an added line contains a phone number outside 555-0100..0199.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.claude/hooks/pre-commit` | New check 12 (blocking phone-number scan of added lines); step counters `N/11` → `N/12` | Catch real numbers before they reach git |
| `.gitleaks.toml` | New `spinr-nanp-phone-number` rule, with an allowlist that mirrors the hook | CI backstop (G5a) for commits made with `--no-verify` or without the hook |
| `tests/hooks/test_pre_commit_phone_numbers.sh` | New: 30 assertions | Regression coverage for block/allow/masking/fail-closed |
| `docs/change-log/2026-09-25-block-real-phone-numbers.md` | This entry | CLAUDE.md Change Impact Log requirement |

## 7. Before / after

```
# Before — commit with an added line like  DRIVER_PHONE = "<real 10-digit number>"
11/11 Checking known-fork registry (warning only)...
  ✅ Clean
✅ All spinr pre-commit checks passed          # exit 0 — number committed
```

```
# After
12/12 Scanning added lines for real phone numbers...
  BLOCKED — phone number in added line: backend/tests/test_x.py:42: ***-***-LLLL
  Use the fictional range 555-0100..555-0199 (e.g. +1 306 555 0142) for fixtures and examples. Never commit a real person's number.
COMMIT BLOCKED — fix issues above              # exit 1
```

## 8. Rollback plan

This is dev tooling with no live data involved. To roll back:
- **Hook:** delete the check-12 block from `.claude/hooks/pre-commit`, or in an emergency commit with `--no-verify` (the hook's existing convention). Nothing is deployed.
- **gitleaks:** delete the `[[rules]] id = "spinr-nanp-phone-number"` block from `.gitleaks.toml`. G5a is non-blocking, so this is needed only if the noise is unwanted. For G5b, the rule is path-allowlisted for `.next/`.
- **Revert:** `git revert` is safe because nothing here touches live data.

## 9. Verification performed

- [x] `bash tests/hooks/test_pre_commit_phone_numbers.sh`: 30 passed, 0 failed, run several times.
- [x] Real scratch commit in the worktree with an added made-up non-fictional number: the hook blocked it with exit 1 and masked output. With `306-555-0142` the same commit passed. The scratch commit was then reset away.
- [x] gitleaks 8.18.4 (G5b's pinned version) against synthetic fixtures: exactly the same block/allow decisions as the hook. The rule-scoped `.next/` path allowlist was confirmed.
- [x] Full-tree measurement with the hook's awk logic and with gitleaks (`--no-git` over `git ls-files`): 688 and 689 matches, reconciled line by line. The single difference is explained in §4.
- [x] gitleaks `--log-opts origin/main..HEAD` over this branch's own commits: 0 findings from the new rule. The test file builds every non-fictional number at runtime from 3- and 4-digit pieces.
- [x] Blast-radius grep: `.gitleaks.toml` consumers are `security-gates.yml` G5a (auto-loaded by gitleaks-action) and G5b (`--config .gitleaks.toml`), plus hook check 1. `ci.yml` uses `admin-dashboard/.gitleaks.toml` instead.
- [x] `bash -n` on the hook.

## 10. What was NOT verified

- **G5b on a real build.** A real admin-dashboard `npm run build` followed by a G5b scan was not run. The `.next/` exclusion was verified only on a synthetic bundle file. If the path allowlist behaved differently on real build paths, G5b (blocking) could turn red on minified numeric literals. Reverting §8's gitleaks block fixes that immediately.
- **gitleaks-action v3 PR comments.** Its behaviour for the new rule (whether it comments, and what it redacts) was reasoned about, not observed on a live PR run.
- **awk and shellcheck coverage.** The check was run on mawk (Linux) only. macOS BWK awk and gawk were not exercised, though the program avoids interval expressions and gawk-only functions for that reason. `shellcheck` is not installed in this environment, so the hook was not linted.
- **Pre-existing test hazard, not fixed here (out of scope).** `tests/hooks/test_pre_commit.sh`, the pre-existing hook test, is now a fork bomb. Its git shim falls back to `command git`, which resolves to the shim itself, so it recurses forever as soon as the hook makes an un-intercepted call (check 9's `git rev-parse --show-toplevel`). It hung and spawned about 27k processes here before being killed. The new test resolves the real git path first. The old file should get the same one-line fix.
