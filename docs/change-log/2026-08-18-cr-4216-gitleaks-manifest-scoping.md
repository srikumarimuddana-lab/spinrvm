# Change Impact & Risk Log — CR #4216: gitleaks manifest-entry blind-spot investigation

**Date:** 2026-08-18
**Files:** `admin-dashboard/.gitleaks.toml`

## Issue/gap identified

The "Next.js per-build generated keys" allowlist entry in `admin-dashboard/.gitleaks.toml` used `paths` + `condition = "AND"` + `regexes`, with a comment claiming this scoped suppression to matching content within 4 specific manifest files only. Testing (during the linked Cloudflare D1 false-positive fix, PR #4217) showed this was inaccurate: `paths` fully excludes a matching file from scanning by **every** rule, not just the allowlisted one.

## Root cause

gitleaks 8.27.2 has no allowlist mechanism that suppresses one specific regex match without excluding the whole file from every other rule too. `paths` is a file-level pre-filter, evaluated before any regex/condition logic runs. `stopwords` (the only other content-adjacent allowlist check gitleaks documents) matches against the secret's own value, which is a fresh random value on every build here — no stable substring to anchor on.

## What was investigated (this CR's implementation plan, followed step by step)

1. Rebuilt `admin-dashboard` locally (`npm run build`), ran gitleaks 8.27.2 (the pinned CI version) against the real output, without `--redact`, to see real values.
2. For each of the 3 specific-named regexes (`previewMode(Signing|Encryption)Key`, `__NEXT_PREVIEW_MODE_(SIGNING|ENCRYPTION)_KEY`, `NEXT_SERVER_ACTIONS_ENCRYPTION_KEY`): confirmed via a whole-bundle scan (82.93 MB, `paths` stripped from the entry) that none of them accidentally match anything else in the real build — 0 residual findings. These are safe to make regex-only, repo-wide.
3. For the 4th, generic regex (`encryptionKey"\s*:`): planted a canary using its *exact* literal shape (`{"encryptionKey":"<real-40-char-token>"}`) outside the 4 manifest files, with `paths` removed — it was **silently suppressed**, proving this specific pattern is genuinely unsafe to make regex-only. Also confirmed directly in the real built file (`server-reference-manifest.json`) that Next.js does use the bare JSON field name `"encryptionKey"` — not the more specific `NEXT_SERVER_ACTIONS_ENCRYPTION_KEY` name — so this pattern is load-bearing, not removable.
4. Confirmed the severity of keeping `paths` for that one entry: planted an *unrelated* secret shape (`sk_live_...`, matching no allowlist regex at all) inside one of the 4 manifest files, alongside a benign `encryptionKey` value — it was also silently missed (`scanned ~0 bytes`), confirming the blind spot really is file-wide, not finding-wide.
5. Concluded: CR #4216's hoped-for full fix (drop `paths` entirely) is **not achievable** for this one pattern with gitleaks 8.27.2's actual allowlist system. No fix was forced through anyway — the finding is disclosed honestly below rather than papered over.

## Fix/remediation (partial, disclosed as such)

Split the single entry into two:
- **Entry A** (new): the 3 specific-named regexes, now regex-only, no `paths`. Verified safe and now enforced **repo-wide** — a real improvement, not just a reshuffle, since these were previously also fully gated behind the same file exclusion.
- **Entry B**: only the generic `encryptionKey"\s*:` pattern, `paths` kept (unavoidable), with the entry's own comment corrected to accurately state what `paths` actually does (full-file exclusion for all rules) instead of the previous, disproven claim that `condition = "AND"` scoped it to matching content only.

## Risk & impact on existing functionality

- **Improved:** the 3 specific-named regexes are no longer coupled to the 4-file exclusion at all — they'd now catch a real `previewModeSigningKey`/`NEXT_SERVER_ACTIONS_ENCRYPTION_KEY`-named secret leak anywhere else in the bundle, which the old combined entry would have missed just as badly (same file-wide-exclusion bug, just never exercised for content outside those 4 files before).
- **Unchanged, now honestly documented:** the 4 manifest files (`prerender-manifest.json`, both middleware-manifest variants, `server-reference-manifest.json`) remain fully excluded from `deploy-admin`'s secret scan for any secret shape, not just `encryptionKey`. This is the exact same residual exposure that existed before this PR — nothing regressed, nothing newly introduced. Other gates (G5a's git-history scan, G5b's bundle scan on the *rest* of the build output) still apply to any secret that isn't confined to exactly these 4 files.
- **Blast radius:** `admin-dashboard/.gitleaks.toml` has one reader — `deploy-admin`'s secret-scan step in `.github/workflows/ci.yml`. No other job or script reads this file.

## User experience effect

None — internal CI-tooling only.

## Files modified

| File | What changed | Why |
|---|---|---|
| `admin-dashboard/.gitleaks.toml` | Split one `[[allowlists]]` entry into two; corrected the inaccurate scoping claim in the comments | 3/4 patterns made safely repo-wide; the 4th's unavoidable file-exclusion now documented honestly instead of inaccurately |

## Rollback plan

`git-revert-safe` — CI-tooling config only, no application code, no data, no live deploy touched.

## Verification performed

1. Real `npm run build` of `admin-dashboard`, gitleaks 8.27.2 (the exact pinned CI version).
2. Confirmed the real build passes clean (0 findings) with the new split config, same as with the original.
3. Confirmed each of the 3 specific-named patterns' real content (verified directly in the built manifest files, not guessed) is still correctly suppressed.
4. Confirmed the whole 82.93 MB non-cache bundle has 0 residual findings once `paths` is removed from the 3-regex entry — no accidental over-suppression.
5. Confirmed (canary, exact literal-shape match) that the generic `encryptionKey"\s*:` pattern genuinely cannot be made regex-only-safe.
6. Confirmed (canary, unrelated `sk_live_`-shaped secret) that the residual `paths`-based exclusion really is file-wide for the one entry that still needs it.

## What was NOT verified / NOT fixed

- **The core blind spot CR #4216 set out to close is not closed** — the 4 manifest files remain fully unscanned by this gate for any secret shape other than what the 3 now-regex-only patterns happen to also cover. This is disclosed here and in the PR, not implied as resolved.
- No alternative gitleaks version or third-party scanner was evaluated as a potential fix for the underlying `paths`-is-file-wide limitation — out of scope for this CR, which was specifically about restructuring the existing config.
- Did not attempt a live Fly/Vercel deploy — same limitation as PR #4217, no deploy credentials in this session.
