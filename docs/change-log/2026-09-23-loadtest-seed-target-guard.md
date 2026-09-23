# Change Impact & Risk: Loadtest Seed Supabase Target Guard

Date: 2026-09-23 · Surface: developer tooling · Domain: loadtest data safety · PR 5725

## Issue / gap

The loadtest bot seeder allowed `ENV=staging` but did not independently prove
that `SUPABASE_URL` pointed to the intended nonproduction Supabase project.
An operator could set staging mode while retaining production credentials.

## Root cause

The script's existing ENV allowlist and known production-reference checks did
not compare the configured database endpoint with an independently supplied
project identity. It also imported `db_supabase` at module load, before
`main()` could apply its safety interlocks; importing that module initializes
the Supabase client.

## Fix / remediation

Require `EXPECTED_SUPABASE_PROJECT_REF` independently of `SUPABASE_URL`, parse
and validate a canonical HTTPS `<project-ref>.supabase.co` URL, reject the
known production project even under staging/development ENV, reject malformed
or noncanonical URLs, and require exact ref equality. Keep the existing ENV
allowlist and service-role-key requirement. Load `.env`, validate all target
configuration, and only then lazily import `db_supabase` and initialize its
client. The same guard runs for seeding and `--cleanup`.

## Risk & impact on existing functionality

The seeder now refuses to run unless the new expected-ref variable is set and
matches the hosted Supabase URL. Existing invocations need to configure
`EXPECTED_SUPABASE_PROJECT_REF` to the same nonproduction target. This is
deliberately fail-closed; local Supabase URLs and custom domains are rejected
until a separate identity-validation design is approved. No other seeder,
application startup, API origin, or loadtest behavior changes.

## User experience effect

Operators must set one additional nonsecret project-ref variable before
seeding or cleanup. Invalid targets fail with a short reason before client
initialization or writes; no bot account or service-area data is created.

## Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/scripts/seed_loadtest_bots.py` | Require project-ref match and defer database import until guards pass | Prevent staging misconfiguration from reaching production data |
| `backend/tests/test_seed_loadtest_bots_guard.py` | Test required/matching identity, production denial, malformed URLs, and fail-before-client behavior | Verify no DB client or write path is reached on rejection |
| `docs/change-log/2026-09-23-loadtest-seed-target-guard.md` | Record behavior, compatibility impact, rollback, and verification limits | Required change-impact record |

## Before / after

```python
# Before
from backend import db_supabase  # client initializes during module import
if ENV in allowed_envs:
    seed_rows()

# After
load_dotenv()
project_ref = validate_env_and_supabase_url(
    ENV, SUPABASE_URL, EXPECTED_SUPABASE_PROJECT_REF
)
db_supabase = initialize_database_client()
seed_rows()
```

## Rollback plan

Revert the target guard and test/doc changes together. The safer operational
fallback is to keep this guard and configure the expected staging project ref;
do not remove the guard to make an old invocation work.

## Verification performed

- `/tmp/pr5725-venv/bin/python -m pytest backend/tests/test_seed_loadtest_bots_guard.py -q --no-cov` — 18 passed.
- `git diff --check` clean.
- Tests cover wrong/missing expected ref, exact staging match, known production ref under staging, malformed URLs, existing ENV allowlist, and refusal before DB client initialization or seed work.

## What was NOT verified

No live Supabase credentials, URL, client, database, seed, or cleanup operation
was used. The test project ref is synthetic. The seeder supports canonical
hosted Supabase URLs only; local/custom-domain identity remains unverified.
