# Change Impact & Risk: validate sensitive-surface records

## Issue / root cause
CI accepts five phrases anywhere in the PR body or any added log, even when fields are empty or the record is unrelated. It checks substrings, not field structure or changed paths.

## Fix
Add a validator and tests requiring filled root-cause, impact, UX, rollback, and verification fields and exact sensitive paths. It accepts existing heading/table layouts, unions valid records, and skips deleted logs. CI runs tests and the validator on PR, merge-group, and manual events. This structural check does not prove risk statements or evidence are true.

## Risk & impact
CI merge gating only; incomplete or placeholder records fail. No runtime, database, payment, or user-facing paths change. Blast radius: PRs touching the sensitive-path list.

## User experience
No runtime effect; PR authors see failures for incomplete records.

## Files modified
| File path | Change | Purpose |
|---|---|---|
| `scripts/check_change_impact.py` | Validate record layouts, exact paths, and deleted logs | Preserve valid existing records |
| `scripts/test_check_change_impact.py` | CLI and six existing-record cases | Prevent false rejection/acceptance |
| `.github/workflows/ci-guardrails.yml` | Run tests and validator with event-specific base/head SHAs | Make the check merge-blocking |
| This record | Document scope and evidence | Required impact record |

## Before / after
Before, phrase presence passed. After, valid records must jointly cover exact sensitive paths; the blocking workflow also runs validator tests.

## Rollback plan
Revert this tooling commit; no live data or runtime state is affected.

## Verification performed / not verified
- [x] `/tmp/pr5725-venv/bin/python -m pytest scripts/test_check_change_impact.py -q` — 5 passed; `git diff --check` clean.
- [x] Reviewed existing path markers and `docs/templates/CHANGE_IMPACT_LOG.md`.
- [ ] Actual PR, merge-group, and manual workflow execution — no Actions run was triggered here.
- [x] Existing payment-retry, preauth, orphaned-hold, auto-payout, autotopup and strict-Redis impact records pass.
