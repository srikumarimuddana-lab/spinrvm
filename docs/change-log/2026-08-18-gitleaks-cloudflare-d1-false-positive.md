# Change Impact & Risk Log — gitleaks `cloudflare-api-key` false positive blocking `deploy-admin`

**Date:** 2026-08-18
**Files:** `admin-dashboard/.gitleaks.toml`

## Issue/gap identified

Every `deploy-admin` run on `main` since at least 15:46 UTC today has failed at the "Scan build output for accidentally bundled secrets" step, blocking the admin-dashboard's CI/CD deploy path entirely. Confirmed identical across 3 consecutive runs (15:46, 17:04, 18:04 UTC — commits `c3be41bc9d0e`, `86470dce31ba`, `9829ceafa04`).

## Root cause

Gitleaks' built-in `cloudflare-api-key` rule (`(?i)[\w.-]{0,50}?(?:cloudflare)...([a-z0-9_-]{40})...`) matches on `.next/server/edge/chunks/admin-dashboard_0r2ddr6._.js.map`, line 1 — a server edge-runtime sourcemap. The CI step runs gitleaks with `--redact`, so the failure report only ever showed the secret as the literal string `"REDACTED"`. **I initially misread that placeholder as the real content and nearly shipped a false-positive allowlist entry anchored on the wrong string** — caught and corrected by rebuilding `admin-dashboard` locally and running gitleaks *without* `--redact` against the real output to see the actual matched value: `CLOUDFLARE_DURABLE_OBJECT_QUERY_BINDINGS`, paired with the field name `CLOUDFLARE_D1_ROWS_WRITTEN` immediately before it — an all-caps, enum-shaped Cloudflare D1/Durable Objects telemetry field-name pair bundled by Vercel's edge-runtime instrumentation, not a credential.

## Fix/remediation

Added a new `[[allowlists]]` entry to `admin-dashboard/.gitleaks.toml` matching the exact real (unredacted) field-name pair via `regexTarget = "match"`, with **no `paths` key**.

## Second, more serious finding discovered mid-investigation (not part of this fix's scope, but disclosed here)

While verifying this fix's precision with a planted canary secret, discovered that gitleaks 8.27.2's `paths` key inside an `[[allowlists]]` block acts as a **full pre-scan file exclusion**, regardless of `condition = "AND"` and a co-present `regexes` list — not a "match must satisfy both path and regex" scope as this same file's pre-existing "Next.js per-build generated keys" entry's own comment claims. Verified empirically:
- A canary secret (`cloudflare_api_token = '<real 40-char high-entropy token>'`) planted in a file under `.next/server/edge/chunks/` (my new entry's would-be path scope) was silently skipped entirely (`scanned ~0 bytes`) when tested with a `paths`-based version of this fix — the file was never read, let alone regex-matched.
- The same canary planted in `.next/prerender-manifest.json` — one of the 4 paths the **pre-existing** manifest entry (lines 58–75) targets — was also silently skipped entirely under the current, already-merged config.

**This means the pre-existing manifest allowlist entry currently blinds `deploy-admin`'s secret scan to any real secret accidentally baked into any of its 4 target files** (`prerender-manifest.json`, both middleware-manifest variants, `server-reference-manifest.json`), not just the specific preview-mode/encryption-key lines it was written to scope to. Not fixed in this PR — restructuring that entry safely (its regexes, e.g. `encryptionKey"\s*:`, are more generic and were presumably paired with `paths` specifically to keep them narrow; removing `paths` needs each regex re-reviewed for accidental over-matching elsewhere in the bundle) is a distinct, more involved change than this PR's narrow false-positive fix. Filed as its own CR — see linked issue.

## Risk & impact on existing functionality

- **This fix (new entry):** isolated to gitleaks' allowlist evaluation for one specific literal field-name pair. Verified with a real canary secret in the same directory that it does NOT create a scanning blind spot (the risk the second finding above describes) — my new entry deliberately omits `paths` for exactly this reason.
- **Blast radius:** `admin-dashboard/.gitleaks.toml` has one reader — the `deploy-admin` job's "Scan build output for accidentally bundled secrets" step in `.github/workflows/ci.yml`. Grepped for other references; none found. `admin-dashboard/.gitleaks.toml` is explicitly NOT read by the root `.gitleaks.toml`'s G5a/G5b scans (per that file's own header comment) or vice versa.
- **The disclosed-but-unfixed manifest-entry gap** is a pre-existing condition, not introduced by this PR — it has been present since that entry was added (2026-08-03 per the file's own verification date). Not made worse or better by this change.

## User experience effect

None — internal CI-tooling only, no rider/driver/corporate-admin/internal-admin-facing surface touched.

## Files modified

| File | What changed | Why |
|---|---|---|
| `admin-dashboard/.gitleaks.toml` | Added one `[[allowlists]]` entry (regex-only, no `paths`) | Suppress the confirmed-benign Cloudflare D1 field-name-pair false positive without creating a scanning blind spot |

## Before/after

Before: `deploy-admin`'s secret-scan step exits 1 on every run touching this sourcemap chunk (i.e., every run, since it's Next.js's own generated edge-runtime instrumentation, present in every build).

After: exit 0, verified against a real local `npm run build` of `admin-dashboard`, using the exact gitleaks version (8.27.2) and exact CLI invocation (`detect --source .next --no-git --redact --config .gitleaks.toml`) the CI step runs.

## Rollback plan

`git-revert-safe` — a single allowlist-entry addition to a CI-only config file. Reverting restores the prior (failing) state exactly; no data, deploy, or application-code impact either way.

## Verification performed

1. Downloaded gitleaks 8.27.2 (the exact version `ci.yml` pins for this step) and 8.18.4 (the version the root config's comments reference) locally.
2. Ran `npm run build` in `admin-dashboard` to produce the real production bundle (not a synthetic reproduction).
3. Ran the exact CI command against the real build output with the **original** (pre-fix) config: reproduced the exact 1 finding, `cloudflare-api-key`, same file/line as all 3 real CI failures.
4. Ran the same command **without** `--redact` to see the real matched value (not the redaction placeholder) — this is what caught my own initial mistake.
5. Ran the exact CI command against the real build output with the **fixed** config: 0 findings, exit 0.
6. Planted a real 40-character high-entropy canary secret (`secrets.choice`-generated, not a low-entropy/repetitive string) in a file under the same target directory (`.next/server/edge/chunks/`) and confirmed it is still caught (exit 1) with the fix in place — proving the fix doesn't create a scanning blind spot, unlike a `paths`-based version of the same fix would have (verified that failure mode too, deliberately, before settling on the regex-only approach).
7. Also confirmed (canary #2) that the pre-existing manifest-path allowlist entry has the same blind-spot behavior on its own 4 target files — see "Second, more serious finding" above.

## What was NOT verified

- Did not attempt a live Fly/Vercel deploy of the fixed admin-dashboard build — no deploy credentials in this session. The verification above (real `npm run build` + real gitleaks binary + real CI command) is the closest available proxy without live deploy access.
- Did not audit the rest of the bundle for other Cloudflare-D1-adjacent field-name pairs that might trigger the same rule under slightly different content (e.g., a different ordering or an additional field in the list) — only the one specific pair actually observed across the 3 real failures is allowlisted. If Next.js's bundled instrumentation code changes this field ordering/list in a future dependency bump, a new (correctly scoped) allowlist entry would be needed.
- Did not restructure or fix the pre-existing manifest-entry blind spot (see above) — flagged as a separate CR, not attempted here to keep this PR's diff to one logical change.
