# Change Impact & Risk: DAST target and report validation

## Issue / root cause
The ZAP workflow skipped successfully when `STAGING_URL` was missing, accepted any configured host, and did not prove a target report existed.

## Fix
The workflow previously treated missing `STAGING_URL` as success and did not validate the scan report. Require both `STAGING_URL` and `STAGING_ALLOWED_ORIGIN`; validate their exact HTTPS-origin match; generate an anchored ZAP context include regex and pass it to baseline with `-n` before spidering. After the pinned action writes its workspace report, require nonempty valid JSON whose every site is that same host, port, and TLS state.

## Risk & impact
This is CI-only. Manual/weekly ZAP runs will fail until staging URL and allowlist variables are configured. Known production API/Fly hosts are denied even if allowlisted. No application traffic, credentials, or data are changed by this helper.

## User experience
No app runtime effect. CI shows a failed DAST check for missing or mismatched configuration/report.

## Files modified
| File | Change | Purpose |
|---|---|---|
| `.github/workflows/dast-zap-baseline.yml` | Fail on missing config, scope before spidering, validate report | No successful no-op or out-of-scope scan |
| `scripts/validate_dast.py` | Validate exact target/report and emit scoped context | Reject unsafe/no-op scans |
| `scripts/test_validate_dast.py` | Exercise target/report and workflow constraints | Pin validation |
| This record | Scope and verification boundary | Required impact record |

## Before / after
Before, missing target exited successfully and any scan target could spider beyond the intended origin. After, missing/mismatched configuration fails before ZAP; a context restricts the spider to the allowlisted origin, and absent/malformed/mixed-target report fails after scanning.

## Rollback plan
Revert the isolated helper commit; it does not affect runtime or persisted state.

## Verification performed / not verified
- [x] `/tmp/pr5725-venv/bin/python -m pytest scripts/test_validate_dast.py -q` — 8 passed; `git diff --check` clean.
- [x] Tests use synthetic URLs and reports only; no network scan ran.
- [x] Generated include regex is anchored to HTTPS + exact host/port and rejects a host suffix; mixed-target reports fail. XML uses ZAP's exported `.context` element shape (`<incregexes>regex</incregexes>`), verified against the ZAP upstream fixture.
- [x] Workflow test asserts preflight/context generation runs before ZAP and report validation follows it; missing-target skip removed.
- [ ] No ZAP scan or report artifact was produced locally; the staging variables and live staging target are not available here.
