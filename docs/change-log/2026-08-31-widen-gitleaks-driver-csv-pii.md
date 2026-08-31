# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code (session) |
| Surface(s) | CI / security config |
| Domain (Sentry tag) | — (not runtime code; CI gate config) |
| PR / commit link | security/widen-gitleaks-driver-csv-pii |
| Related issue or gap ID | #4596 (one of three "Still-open items" on the breach record) |

## 1. Issue / gap identified

The `spinr-sin-bank-pii` gitleaks rule (added after the #4547 incident) only fires on files
naming a SIN/bank/institution-shaped column. `driver_csv_migration.sql` (#4596, 189 drivers'
name/email/phone/license_number/latitude/longitude) has none of those column identifiers, so
it went completely undetected by CI from its introduction (2026-08-16) until a later manual
audit found it — 11+ days of exposure the CI backstop should have caught but didn't.

## 2. Root cause

The existing rule's keyword gate was scoped to the *first* incident file's specific column
shape, not the general class of "a hand-authored SQL file with literal PII data rows." The
second file's different column shape was simply outside that rule's detection surface.

## 3. Fix / remediation

Added `spinr-driver-csv-pii`, a second gitleaks rule in `.gitleaks.toml`. Instead of keying
on column-name keywords (too common — `email`/`phone`/`name` alone would gate nearly every
file in the repo and provide no real filtering), it keys on the actual structural signal:
a literal email-address pattern appearing inside an `INSERT INTO ...driver...` SQL
statement. Application code never embeds a literal PII email address in a committed SQL
string — only a hand-authored one-off data-migration dump does, which is exactly the shape
of both #4547 and #4596's incident files.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `.gitleaks.toml`.** No application code, schema, or runtime
  behavior touched — this is purely a CI detection-rule addition.
- **Could this break CI or cause false-positive noise?** Verified before committing:
  - Regex matches the real incident file: `git show 3c336ff:driver_csv_migration.sql` (the
    breach-start commit) — confirmed match.
  - Regex does NOT match a schema-only `CREATE TABLE drivers (...)` snippet with no literal
    INSERT data — confirmed no match.
  - Regex does NOT match any current file under `backend/migrations/*.sql` (every real,
    legitimate migration in the repo) — confirmed zero matches across the whole directory.
  - `.gitleaks.toml` re-parses as valid TOML after the change (`tomllib.load` succeeds,
    all 3 rules present).
- **Could this regress the existing `spinr-sin-bank-pii` rule?** No — purely additive, that
  rule's `[[rules]]` block is untouched.
- **What this does NOT do:** does not scan `gitleaks` locally in this sandbox (the real
  `gitleaks` binary isn't installed here) — verification above used a Python `re`
  approximation of the same regex against the same inputs, which is a reasonable proxy for
  this rule's specific pattern (no backreferences, no lookaheads, nothing Go-regex-specific
  used) but is not a substitute for CI actually running the real binary once merged. Flagged
  under "what was NOT verified" below.

## 5. User-experience effect

None — this is a CI/security-tooling change with no rider/driver/admin-facing surface.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.gitleaks.toml` | Added `spinr-driver-csv-pii` rule | Close the detection gap that let #4596's incident file go unnoticed for 11+ days |

## 7. Before / after

```toml
# Before: only spinr-sin-bank-pii existed, gated on SIN/bank column keywords.
# driver_csv_migration.sql's shape (name/email/phone/license_number/lat/lng)
# has none of those keywords and was invisible to this rule.

# After: additional rule
[[rules]]
id = "spinr-driver-csv-pii"
description = "Possible driver PII data dump (literal email address inside an INSERT INTO ...driver... statement) — verify this isn't a real committed migration export before merging."
regex = '''(?i)insert\s+into\s+\w*driver\w*[\s\S]{0,4000}?['"][^'",]*@[^'",]*\.[^'",]{2,}['"]'''
keywords = [
    "insert into", "license_number", "driver_csv_import", "driver_bank_sin_migration",
]
```

## 8. Rollback plan

`git-revert-safe`. This is a pure CI-config addition — reverting removes the new rule with
zero effect on any other rule, allowlist, or application code.

## 9. Verification performed

- [x] Regex matches the real incident file content (`git show 3c336ff:driver_csv_migration.sql`).
- [x] Regex does not match a schema-only DDL snippet with no literal data.
- [x] Regex produces zero matches against every file in `backend/migrations/*.sql`.
- [x] `.gitleaks.toml` re-parses as valid TOML with all 3 rules present.
- [ ] **Not run against the real `gitleaks` binary** — not installed in this sandbox. The
  Python `re` approximation is a reasonable proxy for this specific pattern but CI's first
  real run against the actual binary (gitleaks 8.18.4, per the pinned version comment
  elsewhere in this file) is the true verification and hasn't happened yet as of this
  commit.

## 10. Sign-off

- [x] Rollback plan concrete and testable (git revert)
- [x] Blast radius stated, not assumed (§4)
- [x] No silent behavior change to a live-tested rider/driver/admin flow (§5 — none exists)

**What was NOT verified:** the real gitleaks binary was not run locally (not installed in
this sandbox); CI's own run on this PR is the first real-binary verification. Whether this
new rule produces any false positives against the *full* git history (not just the current
working tree) was not checked — the existing G5a history scan in `security-gates.yml` is
advisory-only (not blocking), so a history false positive here would surface as CI noise,
not a hard failure, consistent with how `spinr-sin-bank-pii` was rolled out.
