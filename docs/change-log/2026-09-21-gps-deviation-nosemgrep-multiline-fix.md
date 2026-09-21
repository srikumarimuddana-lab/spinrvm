# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | Claude Code (session `session_01173usfHtfdzMMzYpWeWmVm`) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | see PR description this file is linked from (branch `fix/gps-deviation-nosemgrep-multiline`) |
| Related issue or gap ID | none filed — pre-existing CI-gate breakage found while babysitting PR #5612 |

## 1. Issue / gap identified

`backend/routes/rides/payments.py`'s GPS-spoof deviation-threshold `float()`
conversion carries a `# nosemgrep: spinr-no-float-in-money` suppression
comment that stopped anchoring to the right line after an unrelated PR
(#5602, commit `9731279c75`) reformatted the call across multiple lines.
Both `.semgrep/spinr-rules.yml`'s `spinr-no-float-in-money` rule (repo-wide,
blocking) and its GitHub-hosted "Semgrep OSS" Action counterpart now flag
this line on every PR in the repo, regardless of that PR's own diff — this
was first discovered blocking PR #5612 (an unrelated RLS-test-coverage PR)
via the "Security Gates / G3 · Semgrep" check.

## 2. Root cause

`spinr-no-float-in-money` is a full-repo scan (not diff-scoped), so a break
anywhere in its 12 in-scope files fails the check for every open PR. The
suppression comment on the original 3-line call:

```python
_deviation_threshold = float(
    _app_settings.get("gps_spoof_deviation_hold_threshold_pct", 40.0)
)  # nosemgrep: spinr-no-float-in-money
```

put the `# nosemgrep` comment on the closing-paren line, not the line
`ruff format` (via this repo's `ruff.toml` `line-length = 120`) treats as
the match anchor. This is a pure suppression-comment-placement bug — the
underlying `float()` call itself is legitimate and already documented as a
known false-positive class (the same GPS-deviation-percentage case is
already suppressed the same way in `email_receipt.py`); the value converted
is a route-deviation percentage read from `app_settings`, never money.

A first fix attempt (commit `1560eeb0f`, this same branch) moved the
comment to the call's opening line instead. That passed local `semgrep`
1.177.0 with 0 findings, but GitHub's hosted "Semgrep OSS" Action still
flagged the same code (re-citing the closing-paren line) — a real,
only-partially-understood local-vs-hosted-engine discrepancy. Rather than
keep guessing at exact suppression-comment anchoring across two semgrep
engine versions, this fix eliminates the ambiguity entirely by restructuring
the call so it never wraps across multiple lines in the first place.

## 3. Fix / remediation

Split the single multi-line `float(...)` call into two short single-line
statements, each well under the 120-char `ruff format` wrap threshold, so
`ruff format` never re-wraps them and the `# nosemgrep` comment stays a
same-line trailing comment on the line it suppresses:

```python
_deviation_threshold_raw = _app_settings.get("gps_spoof_deviation_hold_threshold_pct", 40.0)
_deviation_threshold = float(_deviation_threshold_raw)  # nosemgrep: spinr-no-float-in-money
```

No behavior change: `_deviation_threshold`'s value and type are identical
before and after — this is a pure statement-splitting refactor to fix a
CI-gate false-positive, not a logic change.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one local variable in one function.**
  Grepped `backend/` for `_deviation_threshold` — the only two references
  are the definition (this diff) and its use three lines below in the same
  `if _gps_validation.get("verdict") == "likely_spoofed" and _deviation_pct
  > _deviation_threshold:` guard, unchanged by this diff. No other file
  reads this variable; it is not exported, cached, or passed anywhere.
- The GPS-spoof deviation-hold gate's actual behavior (threshold value read
  from `app_settings`, comparison against `_deviation_pct`, hold logic) is
  byte-for-byte unchanged — only the two-step vs. one-step assignment of an
  intermediate value changed.
- No interaction with money arithmetic, Stripe, the ride state machine, or
  any DB write — this file's surrounding function still uses `Decimal`
  throughout for the actual fare/payment amounts; this variable was never
  money to begin with (a percentage), which is exactly why it's
  nosemgrep-suppressed rather than converted to Decimal.

## 5. User-experience effect

None. Pure refactor of an internal variable assignment with no change to
control flow, values, or timing. No rider/driver/corporate-admin/internal-
admin-facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/payments.py` | Split one 3-line `float(...)` call with a trailing `# nosemgrep` comment into two single-line statements | Fix the repo-wide-blocking Semgrep suppression-comment anchoring bug (introduced by PR #5602's reformat) so it can't recur regardless of which semgrep engine/version re-wraps it |
| `docs/change-log/2026-09-21-gps-deviation-nosemgrep-multiline-fix.md` (new) | This Change Impact Log | Required per CLAUDE.md for any commit changing existing behavior on a live-tested surface (payments) |

## 7. Before / after

```python
# Before (broken — comment anchors to the wrong line after ruff format wraps it):
_deviation_threshold = float(
    _app_settings.get("gps_spoof_deviation_hold_threshold_pct", 40.0)
)  # nosemgrep: spinr-no-float-in-money

# After (fixed — no wrap possible, comment stays on the line it suppresses):
_deviation_threshold_raw = _app_settings.get("gps_spoof_deviation_hold_threshold_pct", 40.0)
_deviation_threshold = float(_deviation_threshold_raw)  # nosemgrep: spinr-no-float-in-money
```

## 8. Rollback plan

`git revert` is sufficient and safe — this changes only local-variable
assignment structure inside one function, with no migration, no config/flag,
no live data written or read differently. Reverting restores the prior
(broken-suppression, but functionally identical) code; the only regression
from reverting is the CI gate failing again, not a runtime behavior change.

## 9. Verification performed

- [x] `ruff format --check backend/routes/rides/payments.py` — passes,
  confirming the new lines are already in ruff's canonical format and will
  not be re-wrapped by the pre-commit hook or CI.
- [x] `ruff check backend/routes/rides/payments.py` — passes.
- [x] `semgrep --config .semgrep/spinr-rules.yml backend/routes/rides/payments.py`
  (local CLI 1.177.0) — **0 findings**.
- [x] Blast-radius grep performed: see section 4 above.
- [ ] Automated backend test suite — not re-run for this change specifically
  (no test exercises this exact line; the surrounding
  `process_payment`/GPS-spoof-hold tests are unaffected since the value and
  control flow are unchanged). Relying on the broader `backend-test` CI job
  already running on this PR for regression coverage.
- **What was NOT verified yet:** the real GitHub-hosted "Semgrep OSS" /
  "Security Gates / G3 · Semgrep" checks have **not yet re-run against this
  restructured version** — the first fix attempt on this same PR passed
  every local check identically to this one and still failed in real CI, so
  local verification alone is explicitly **not** being treated as sufficient
  confirmation this time. This PR will be watched post-push and this log
  updated (or a follow-up commit pushed) if the hosted check still flags it.
- **Review used:** this is a mechanical, single-statement-split fix with no
  logic change; given the narrow scope, a full `spinr-*` reviewer agent
  pass was judged unnecessary beyond the manual blast-radius grep and
  ruff/semgrep verification above — the entire change is auditable in the
  before/after snippet in section 7.

## What was NOT verified

- The real hosted Semgrep check's outcome against this exact restructuring —
  see above. This is the primary open unknown for this PR.
- `backend-test` CI failures reported on this branch's earlier commit
  (`1560eeb0f`) have not yet been root-caused — tracked separately, not
  assumed related to this change (this diff touches no test-exercised
  logic).

## Assumptions

- GitHub's hosted "Semgrep OSS" Action and this repo's local
  `.semgrep/spinr-rules.yml` config target the same rule semantics; the
  discrepancy between local-pass/hosted-fail on the first attempt is
  attributed to suppression-comment line-anchoring differences between
  semgrep engine versions, not a difference in what the rule itself
  matches (the rule's `pattern-either`/`pattern-not` logic was not
  observed to differ — only which line a same-file, same-code match was
  attributed to for comment-suppression purposes).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed (isolated to one local variable)
- [x] No silent behavior change to an already-shipped flow — no behavior
  change at all, this is a CI-gate/suppression-comment fix only
