# Change Impact & Risk: validate sensitive-surface records

## Issue / root cause
CI accepts five phrases anywhere in the PR body or any added log, even when fields are empty or the record is unrelated. It checks substrings, not field structure or changed paths.

## Fix
Add a validator and tests requiring filled root-cause, impact, UX, rollback, and verification sections and each sensitive path. CI wiring follows separately. This is a structural check, not proof the stated risks or evidence are true.

## Risk & impact
CI merge gating only; incomplete or placeholder records fail. No runtime, database, payment, or user-facing paths change. Blast radius: PRs touching the sensitive-path list.

## User experience
No runtime effect; PR authors see failures for incomplete records.

## Files modified
| File path | Change | Purpose |
|---|---|---|
| `scripts/check_change_impact.py` | Validate fields and path coverage | Replace substring test |
| `scripts/test_check_change_impact.py` | CLI path and sensitive-marker cases | Prevent false acceptance |
| This record | Document scope and evidence | Required impact record |

## Before / after
Before, phrase presence passes; after, fields and affected paths are required.

## Rollback plan
Revert this tooling commit; no live data or runtime state is affected.

## Verification performed / not verified
- [x] `/tmp/pr5725-venv/bin/python -m pytest scripts/test_check_change_impact.py -q` — 3 passed; `git diff --check` clean.
- [x] Reviewed existing path markers and `docs/templates/CHANGE_IMPACT_LOG.md`.
- [ ] GitHub Actions merge-blocking behavior — CI wiring is a later commit; no PR event was run.
