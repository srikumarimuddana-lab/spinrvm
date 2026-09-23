# Preserve ISO timestamp eligibility values

The go-online parser continues accepting date-only ISO strings and ISO datetime strings from existing database rows while rejecting malformed values. This preserves the route's prior datetime compatibility and closes the malformed-date fail-open path. Blast radius: flag-gated driver go-online eligibility. No feature flag or data changes.

Verification: targeted go-online and profile tests passed (143 tests before this compatibility case); added a timestamp regression and reran the go-online suite. `py_compile` and `git diff --check` passed. No staging or live database check. Rollback by reverting the parser compatibility change.
