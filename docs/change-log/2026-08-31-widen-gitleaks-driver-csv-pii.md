# Change Impact & Risk Log

## 0. Superseded (2026-08-31)

**The actual `.gitleaks.toml` rule addition in this branch was dropped.** A parallel session
independently landed an equivalent rule (`spinr-driver-export-pii`, PR #4733) closing the
same detection gap while this branch was in flight — merged to `main` first. On merging
`main` into this branch, `.gitleaks.toml` was resolved to `main`'s version and this branch's
now-duplicate `spinr-driver-csv-pii` rule was dropped rather than reintroduced (same pattern
as #4597 Finding 1's resolution earlier this session, PR #4729/#4732).

This log is kept, unmodified below, as a record of the independent verification work done
against the real gitleaks binary (§4, §9) — useful supplementary evidence even though the
rule itself didn't ship from this branch. The gap it describes (§1) is closed; see
`main`'s current `.gitleaks.toml` for the rule that actually shipped.

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code (session) |
| Surface(s) | CI / security config |
| Domain (Sentry tag) | — (not runtime code; CI gate config) |
| PR / commit link | security/widen-gitleaks-driver-csv-pii (superseded — see §0) |
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
- **Update (same day):** the original `{0,4000}` regex was verified only via a Python `re`
  approximation, flagged below as an open gap — and that gap was real. CI's G5a run on this
  PR's own commit hit `panic: regexp: Compile(...): error parsing regexp: bad repitition
  argument: {0,4000}` — Go's RE2 engine (which gitleaks uses) caps repetition counts at 1000
  by default, and `{0,4000}` doesn't just fail this rule, it crashes the whole gitleaks
  binary at startup, taking `spinr-sin-bank-pii` down with it too. Fixed by reducing to
  `{0,800}` (see §7). Downloaded the real pinned gitleaks 8.18.4 binary directly in this
  session afterward and re-verified: config now compiles cleanly, fires 10/10 against the
  real incident file, zero false positives against ~460 real files (including every current
  `backend/migrations/*.sql`) and against a full working-tree scan (16 pre-existing findings,
  none from this rule).

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
regex = '''(?i)insert\s+into\s+\w*driver\w*[\s\S]{0,800}?['"][^'",]*@[^'",]*\.[^'",]{2,}['"]'''
# (originally {0,4000} -- see §4 update: RE2 caps repetition at 1000 and
# panics the whole binary above that, caught by CI's own G5a run)
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
- [x] **Run against the real `gitleaks` binary** — downloaded gitleaks 8.18.4 (the exact
  version pinned in `security-gates.yml`) directly in this session after CI's own G5a run
  caught a real compile-time bug the Python approximation missed (see §4 update). Re-verified
  with the real binary after the fix: config compiles cleanly, fires correctly against the
  real incident file, zero false positives against ~460 real files and a full working-tree
  scan.

## 10. Sign-off

- [x] Rollback plan concrete and testable (git revert)
- [x] Blast radius stated, not assumed (§4)
- [x] No silent behavior change to a live-tested rider/driver/admin flow (§5 — none exists)

**What was NOT verified:** whether this new rule produces any false positives against the
*full* git history (not just the current working tree and a synthetic incident-file copy)
was not checked directly — the real `git log -p`/history scan wasn't run against this rule
in this session, only a working-tree scan. The existing G5a history scan in
`security-gates.yml` is advisory-only (not blocking), so a history false positive here would
surface as CI noise, not a hard failure, consistent with how `spinr-sin-bank-pii` was rolled
out — but CI's own run on this PR is still the first true full-history check.
