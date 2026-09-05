# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-05 |
| Author | Claude Code (session_01DGsCUrCNqpH6kns8TQVooy) |
| Surface(s) | admin-dashboard (CI-tooling config only, no app code) |
| Domain (Sentry tag) | admin |
| PR / commit link | see PR opened from branch `claude/fix-gitleaks-allowlist-blindspot-4216` |
| Related issue or gap ID | #4216 (follow-up to PR #4226, which partially fixed this on 2026-08-18) |

## 1. Issue / gap identified

`admin-dashboard/.gitleaks.toml`'s "Next.js Server Actions payload encryptionKey field" `[[allowlists]]` entry (Entry B) used a `paths` list naming all 4 build-generated manifest files (`prerender-manifest.json`, both `middleware-manifest.json` variants, `server-reference-manifest.json`). In gitleaks 8.27.2, `paths` is a full pre-scan file exclusion — it blinds the gate to *any* secret in those 4 files, not just the `encryptionKey` pattern the entry's regex targets. PR #4226 (2026-08-18) already split the original single entry into two and made 3 of the 4 legitimate patterns regex-only/repo-wide, but left Entry B's `paths` covering all 4 files and explicitly disclosed the residual 4-file blind spot as unfixable rather than closing it.

## 2. Root cause

Two overlapping causes:
1. (Already root-caused in PR #4226) gitleaks 8.27.2 has no allowlist mechanism that suppresses one specific regex match without excluding the whole file from every other rule — `paths` is the only file-scoping primitive it offers and it is structurally all-or-nothing, independent of `condition`/`regexes`.
2. (New finding, this fix) Entry B's `paths` list was broader than the actual data required: only `.next/server/server-reference-manifest.json` ever contains the bare `"encryptionKey"` JSON field this entry exists to suppress. `prerender-manifest.json` and both `middleware-manifest.json` variants never contain it — they only carry the three field names already covered by Entry A's paths-free regexes. Grepping a real build's output for a bare `"encryptionKey"` key in each of the 4 files confirms this. The other 3 files were included in Entry B's `paths` unnecessarily, which is what caused their otherwise-avoidable blind spot.

## 3. Fix / remediation

Narrowed Entry B's `paths` list from all 4 manifest files down to just `.next/server/server-reference-manifest.json` — the one file that actually contains the bare `encryptionKey` field. `prerender-manifest.json` and both `middleware-manifest.json` variants are no longer named in any `paths` list in this file and are therefore fully scanned again by every default gitleaks rule. Updated both the block comment and Entry B's own comment to describe this narrowing and its verification, replacing the earlier "cannot safely drop paths" framing (which was true for the whole 4-file list, but not for 3 of those 4 files individually).

`server-reference-manifest.json` itself remains excluded via `paths` — this one file's blind spot is unavoidable with gitleaks 8.27.2's allowlist model (see PR #4226's analysis, unchanged) and stays disclosed, not silently accepted.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** `admin-dashboard/.gitleaks.toml` has exactly one reader — the `deploy-admin` job's "Scan build output for accidentally bundled secrets ([17-5])" step in `.github/workflows/ci.yml`. No other CI job, script, or app code reads this file. Grepped the repo for other `.gitleaks.toml` references; only that one workflow step and this file's own header comment mention it.
- **Could this regress a working flow?** The only way this regresses `deploy-admin` is if `prerender-manifest.json` or either `middleware-manifest.json` variant, in some future Next.js version, starts emitting a bare `"encryptionKey"` field (currently they don't — verified against the pinned Next.js version in this repo's `package.json`). If that ever happens, `deploy-admin` would newly fail on a legitimate build artifact and need Entry B's `paths` (or regex) widened again — a visible, loud CI failure, not a silent gap, so it fails safe.
- **No ride/dispatch/payment/auth/corporate/safety code path touched** — this is CI secret-scanning config only.
- **Background loops:** none affected.

## 5. User-experience effect

None. Internal CI-tooling change; no rider, driver, corporate-admin, or internal-admin-facing behavior changes. Not visible mid-session to anyone — it only runs during the `deploy-admin` GitHub Actions job.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/.gitleaks.toml` | Narrowed Entry B's `paths` from 4 manifest files to 1 (`.next/server/server-reference-manifest.json` only); updated both the block comment and Entry B's own comment to describe and justify the narrowing | Closes 3 of the 4 blind-spot files identified in issue #4216 with no loss of the false-positive protection those 3 files' legitimate values need (they're already covered by Entry A's regex-only patterns) |
| `docs/change-log/2026-09-05-gitleaks-allowlist-paths-blind-spot-fix.md` | New Change Impact Log entry (this file) | Required by `CLAUDE.md` for any fix to a live-tested-adjacent gate; documents what was and wasn't verified |

## 7. Before / after

```toml
# Before
[[allowlists]]
description = "Next.js Server Actions payload encryptionKey field — cannot safely drop paths, see CR #4216"
condition = "AND"
regexTarget = "match"
paths = [
  '''\.next/prerender-manifest\.json$''',
  '''\.next/(server/)?middleware-manifest\.json$''',
  '''\.next/server/middleware/middleware-manifest\.json$''',
  '''\.next/server/server-reference-manifest\.json$''',
]
regexes = [
  '''encryptionKey"\s*:''',
]
```

```toml
# After
[[allowlists]]
description = "Next.js Server Actions payload encryptionKey field — cannot safely drop paths, see CR #4216"
condition = "AND"
regexTarget = "match"
paths = [
  '''\.next/server/server-reference-manifest\.json$''',
]
regexes = [
  '''encryptionKey"\s*:''',
]
```

## 8. Rollback plan

`git revert` — this is a CI-tooling config file only, not application code, not a migration, not live data. No Stripe charge, wallet delta, or ride state is touched. Reverting the commit restores the prior (wider, but already-disclosed-blind) 4-file `paths` list; no data-level remediation is needed either way.

## 9. Verification performed

- [x] Automated tests run: none exist for this config file (same as PRs #4217/#4226's precedent — no test harness targets `.gitleaks.toml`).
- [x] **Real production build run**: `cd admin-dashboard && node_modules/.bin/next build` (equivalent to CI's `npm run build`) — completed successfully (`✓ Compiled successfully in 33.1s`), producing a real `.next/` output (372 MB total, 90.29 MB after the existing `.next/cache/` exclusion).
- [x] Ran the **exact pinned CI gitleaks version** (8.27.2, downloaded from the same GitHub Releases URL `ci.yml` uses) against the real build output with `--no-git`, no `--redact`, using the **new** config:
  - Clean build (no canaries): **0 findings**, 90.29 MB scanned — no new false positives from narrowing `paths`.
  - Planted a `cloudflare_api_token`-shaped 40-char high-entropy canary (matching none of this file's allowlisted regexes) in **each of the 4 target files** simultaneously, then re-ran: **3 findings** — `prerender-manifest.json`, `.next/server/middleware-manifest.json`, and `.next/server/middleware/middleware-manifest.json` were all caught (proving their blind spot is closed); `.next/server/server-reference-manifest.json`'s canary was **not** caught (proving the residual, disclosed gap is unchanged, not silently widened or narrowed further).
  - Confirmed the **legitimate** generated values in all 4 files (the `previewModeSigningKey`/`previewModeEncryptionKey`, `NEXT_SERVER_ACTIONS_ENCRYPTION_KEY`, `__NEXT_PREVIEW_MODE_SIGNING_KEY`/`__NEXT_PREVIEW_MODE_ENCRYPTION_KEY` values, plus the real `encryptionKey` value in `server-reference-manifest.json`) were **not** flagged in the same scan runs that did catch the injected canaries in the same files — proving the narrowing didn't reintroduce the original false positives.
  - Ran an unconfigured scan (no `.gitleaks.toml`) for comparison: 382.76 MB scanned, 14 findings — confirmed which specific field names appear in which of the 4 files, which is what justified narrowing Entry B's `paths` to just `server-reference-manifest.json` (the only one of the 4 with a bare `"encryptionKey"` field).
- [x] Blast-radius grep performed: `admin-dashboard/.gitleaks.toml` is read only by `deploy-admin`'s secret-scan step in `.github/workflows/ci.yml`; no other job or script references it.
- [x] TOML syntax validated (`python3 -c "import tomllib; tomllib.load(...)"`) — parses cleanly.
- [x] Reviewed against `CLAUDE.md`'s "Do not silently swallow errors" convention (N/A to this file, no error handling touched) and the migration/RLS/PIPEDA conventions (none apply — CI config only).
- [x] Feature flag: not applicable — no user-visible behavior, config-only, isolated blast radius.

## 10. What was NOT verified

- **This is genuinely empirical, not static-only** — a working gitleaks 8.27.2 binary was available (downloaded directly from the pinned GitHub Releases URL) and a real `admin-dashboard` production build was produced and scanned, matching the issue's implementation plan exactly. This is *not* a "static regex review" fallback.
- Did **not** attempt a live Fly/Vercel deploy or an actual `deploy-admin` GitHub Actions run — no deploy credentials or Actions-dispatch access in this session (same limitation noted in PRs #4217 and #4226). The local build + pinned-binary scan is the closest available proxy and is what those prior PRs also relied on.
- Did **not** re-evaluate whether a newer gitleaks version has a better allowlist primitive (e.g. a match-scoped-only exclusion) that could close `server-reference-manifest.json`'s residual gap too — out of scope for this fix, which only restructures the existing entry's `paths` list; flagged as a possible future follow-up, not attempted here.
- Did **not** change anything about the `.next/cache/` allowlist entry or the Cloudflare D1 / jsPDF entries elsewhere in this file — out of scope for issue #4216.
- Assumption carried over from PR #4226, unchanged: the verification is a snapshot of the current build's real content. A future Next.js version could in principle change what these manifest files contain; if `prerender-manifest.json` or either `middleware-manifest.json` variant ever starts emitting a bare `"encryptionKey"` field, that would need to be re-verified and Entry B's `paths` widened again (this would fail loudly as a new `deploy-admin` gitleaks finding, not silently).
