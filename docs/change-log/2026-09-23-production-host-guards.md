# Change Impact & Risk Log: production host guard coverage

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Codex |
| Surface(s) | CI safety checks / load testing |
| Domain (Sentry tag) | backend |
| PR / commit link | https://github.com/srikumarimuddana-lab/spinrvm/pull/5725 |
| Related issue or gap ID | PR #5725 Codex finding |

## 1. Issue / gap identified

The load-test and DAST target guards reject the Fly production host and canonical API hosts, but missed both the live Railway production hostname and its documented alias. An operator could explicitly allowlist one of those production hosts and pass the current guard.

## 2. Root cause

The two scripts maintained independent hardcoded production-host sets, and both omitted the live Railway hostname and documented alias. Host parsing already normalizes case, trailing dots, and HTTPS port 443, but the missing names were absent from both sets.

## 3. Fix / remediation

Both guards now load one repository production-host JSON list relative to their own source file. It includes the live Railway hostname and documented alias while retaining all prior entries. Regression cases exercise case, trailing-dot, and default-port normalization without network requests.

## 4. Risk & impact on existing functionality

- Blast radius: isolated to load-test and DAST target validation; no backend endpoint, data, or runtime behavior changes.
- Load tests and DAST scans against explicitly allowlisted non-production origins behave as before. Explicit attempts to target either Railway production hostname now fail closed.
- `staging-api.spinr.ca` remains allowed when explicitly included in the load-test allowlist and remains usable wherever it exactly matches the DAST staging allowlist.
- Rollback risk is low; reverting the guard changes restores the prior incomplete denylist.

## 5. User-experience effect

No rider, driver, corporate admin, or internal admin sees a change. Operators cannot accidentally direct these guarded test tools at the Railway production hostnames.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `config/production_api_hosts.json` | Central production hostname list | Keep both safety guards synchronized |
| `loadtest/target_guard.py` | Read the shared list relative to the file | Deny production load-test targets |
| `loadtest/test_target_guard.py` | Add Railway production host regression cases | Verify normalization and fail-closed behavior |
| `scripts/validate_dast.py` | Read the shared list relative to the file | Deny production DAST targets |
| `scripts/test_validate_dast.py` | Add Railway production host regression cases | Verify normalization and fail-closed behavior |
| `docs/change-log/2026-09-23-production-host-guards.md` | Record impact and verification | Document the safety fix |

## 7. Before / after

Before: either Railway production hostname could be accepted if supplied as the explicit allowlist.

After: both hostnames are rejected even when explicitly allowlisted; case variants, trailing dots, and port 443 are normalized before the check.

## 8. Rollback plan

Revert the guard and test commits to restore the prior behavior. No live data or deployment state is changed.

## 9. Verification performed

- [x] `/tmp/pr5725-venv/bin/python -m pytest loadtest/test_target_guard.py scripts/test_validate_dast.py -q` — 29 passed.
- [x] Confirmed both newly added host cases are accepted by the old guard implementations (in-memory source check), so they regress the identified gap.
- [x] No live DAST scan or deployment was run; no network probes were used.
- [x] Existing `staging-api.spinr.ca` allowlisting regression retained.
- [x] Host list and both consumers reviewed together.
