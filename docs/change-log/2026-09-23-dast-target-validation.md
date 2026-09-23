# Change Impact & Risk: DAST target and report validation

## Issue / root cause
The ZAP workflow skipped successfully when `STAGING_URL` was missing, accepted any configured host, and did not prove a target report existed.

## Fix
Add pure checks for an exact HTTPS staging-origin allowlist and a nonempty JSON report whose every site is the exact scanned host, port, and TLS state. Generate an anchored ZAP context include regex for that origin; the next workflow change passes it with baseline `-n` so the spider is scoped before scanning.

## Risk & impact
This is CI-only. Manual/weekly ZAP runs will fail until staging URL and allowlist variables are configured. Known production API/Fly hosts are denied even if allowlisted. No application traffic, credentials, or data are changed by this helper.

## User experience
No app runtime effect. CI shows a failed DAST check for missing or mismatched configuration/report.

## Files modified
| File | Change | Purpose |
|---|---|---|
| `scripts/validate_dast.py` | Validate exact target and report | Reject unsafe/no-op scans |
| `scripts/test_validate_dast.py` | Exercise target/report failures and valid zero-alert report | Pin validation |
| This record | Scope and verification boundary | Required impact record |

## Before / after
Before, missing target exited successfully. After, invalid/missing target or report returns a failure.

## Rollback plan
Revert the isolated helper commit; it does not affect runtime or persisted state.

## Verification performed / not verified
- [x] `/tmp/pr5725-venv/bin/python -m pytest scripts/test_validate_dast.py -q` — 6 passed; `git diff --check` clean.
- [x] Tests use synthetic URLs and reports only; no network scan ran.
- [x] Generated include regex is anchored to HTTPS + exact host/port and rejects a host suffix; mixed-target reports fail. XML uses ZAP's exported `.context` element shape (`<incregexes>regex</incregexes>`), verified against the ZAP upstream fixture.
- [ ] Workflow integration and a real ZAP artifact remain for the next commit/run.
