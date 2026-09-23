# Change Impact & Risk: DAST target and report validation

## Issue / root cause
The ZAP workflow skipped successfully when `STAGING_URL` was missing, accepted any configured host, did not prove a target report existed, and did not fail on findings.

## Fix
Require `STAGING_URL`, `STAGING_ALLOWED_ORIGIN`, and `DAST_EGRESS_ORIGIN_ATTESTATION`; validate their exact canonical HTTPS-origin match in a hosted preflight before the scanner can queue. Run ZAP only on a runner labeled `dast-egress-locked`, then repeat validation and generate an anchored ZAP context include regex before baseline spidering. Configure findings as failures, retain raw reports on failed scans, and require nonempty valid JSON whose every site is that same host, port, and TLS state. Target parsing rejects whitespace/control characters before URL parsing, denies exact known production hosts, and still allows a separately allowlisted `staging-api.spinr.ca`.

## Risk & impact
This is CI-only. Manual/weekly ZAP runs fail in hosted preflight until staging URL, allowlist, and origin-bound attestation are configured; the scan remains queued until the labeled self-hosted runner is available. Known production API/Fly hosts are denied even if allowlisted. A ZAP context scopes crawl selection but does not prove redirects cannot contact another origin. The runner label and origin attestation are operator prerequisites, not proof that network enforcement exists. Operators must enforce an exact-host-and-port proxy/firewall around the ZAP container, including denial of direct egress bypasses; that infrastructure is not provisioned or verified by this code. No application traffic, credentials, or data are changed by this helper.

## User experience
No app runtime effect. CI shows a failed DAST check for missing or mismatched configuration/report.

## Files modified
| File | Change | Purpose |
|---|---|---|
| `.github/workflows/dast-zap-baseline.yml` | Hosted fail-closed preflight, locked-runner selector, scanner scope and report validation | No successful no-op; scans cannot use a hosted runner |
| `scripts/validate_dast.py` | Validate exact target/report, reject parser normalization hazards, validate canonical egress attestation, emit scoped context | Reject unsafe/no-op targets and mismatched origin configuration |
| `scripts/test_validate_dast.py` | Exercise target/report, exact staging subdomain allowance, attestation and workflow constraints | Pin validation |
| `docs/runbooks/dast-and-pentest.md` | Document preflight, runner prerequisites, real redirect limitation, and activation boundary | Prevent false claims of live coverage or network isolation |
| This record | Scope and verification boundary | Required impact record |

## Before / after
Before, missing target exited successfully, findings could be green, and URL parsing could normalize whitespace/control characters. After, missing/mismatched configuration fails in a hosted job, the context gives the crawler an exact-origin scope, and only a labeled self-hosted runner can start ZAP. ZAP WARN/FAIL findings fail the job, raw reports remain available, and absent/malformed/mixed-target reports fail after scanning. The runner label and matching attestation do not prove network enforcement; the scan remains gated on operator-provisioned exact-origin egress controls.

```python
# Before
parsed = urlsplit(value)

# After
if any(char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F for char in value):
    raise ValueError("target URL contains whitespace or control characters")
parsed = urlsplit(value)
```

```yaml
# Before
runs-on: ubuntu-latest

# After
runs-on: [self-hosted, linux, x64, dast-egress-locked]
```

## Rollback plan
Revert the isolated helper commit; it does not affect runtime or persisted state.

## Verification performed / not verified
- [x] `/tmp/pr5725-venv/bin/python -m pytest scripts/test_validate_dast.py -q` — 10 passed after attestation, workflow, whitespace/control, and exact-host denylist coverage; `git diff --check` clean.
- [x] Tests use synthetic URLs and reports only; no network scan ran.
- [x] Generated include regex is anchored to HTTPS + exact host/port and rejects a host suffix; mixed-target reports fail. XML uses ZAP's exported `.context` element shape (`<incregexes>regex</incregexes>`), verified against the ZAP upstream fixture.
- [x] This commit does not claim network isolation or run a live scan; workflow integration and operator enforcement remain separate gates.
- [x] Workflow test asserts preflight/context generation runs before ZAP and report validation follows it; missing-target skip removed.
- [x] Workflow test requires `fail_action: true` and `always()` post-scan report/artifact steps after successful preflight.
- [x] Runbook now distinguishes configuration preflight from actual network enforcement and makes no claim that runner labels/attestation prove isolation.
- [ ] No ZAP scan or report artifact was produced locally; the staging variables and live staging target are not available here.
