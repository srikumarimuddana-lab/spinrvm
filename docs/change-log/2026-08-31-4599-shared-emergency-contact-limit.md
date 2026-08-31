# Change Impact & Risk Log — share the emergency-contact count constant (#4599 Finding 4)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Surface(s) | backend |
| Domain (Sentry tag) | safety |
| Related issue | #4599 (safety/SOS swarm audit) Finding 4 |

## Issue/gap identified

`routes/rides/safety.py`'s SOS fan-out (`trigger_emergency`, `trigger_emergency_rideless`) read emergency
contacts with a hardcoded `limit=5`, while `routes/users.py::add_emergency_contact` enforces a separate
hardcoded `MAX_EMERGENCY_CONTACTS = 3` at insert time. The two numbers were never linked.

## Root cause

No shared constant existed between the insert-time cap and the SOS read limit — they happened to agree
(3 < 5) only because nobody had changed either number since both were written.

## Fix/remediation

Hoisted `MAX_EMERGENCY_CONTACTS` to module level in `routes/users.py` and imported it into
`routes/rides/safety.py` (dual-import pattern) for both SOS read call sites, replacing the literal `5`.

## Risk & impact on existing functionality

- **Blast radius: isolated to 3 call sites**, all now reading the same value (3) they effectively already
  used in practice (3 real contacts max, well under the old `limit=5`). Grepped every other reader of the
  `emergency_contacts` table — no other function reads it with a hardcoded count that this change touches.
- Behavior is unchanged today: a user can have at most 3 contacts, so `limit=5` and `limit=3` returned
  identical rows for every real account. This is a **safety-net fix for a future edit**, not a behavior
  change — it prevents `MAX_EMERGENCY_CONTACTS` and the SOS read limit from ever silently diverging again
  (which would reintroduce the old "extra contacts never notified" bug if the insert-time cap were ever
  raised without updating the SOS read limit to match).
- No circular import: `routes/users.py` imports nothing from `routes/rides/safety.py` or its package.

## User experience effect

None. No behavior change for any rider/driver — the constant's value is identical to what was already the
practical ceiling.

## Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/users.py` | Hoisted `MAX_EMERGENCY_CONTACTS = 3` to module level | Single source of truth |
| `backend/routes/rides/safety.py` | Import `MAX_EMERGENCY_CONTACTS`, use it instead of literal `limit=5` at both SOS call sites | Keep the SOS read limit in lockstep with the insert-time cap |

## Before/after

```python
# Before (routes/rides/safety.py, both SOS handlers)
contacts_rows = await _deps.db_supabase.get_rows("emergency_contacts", {"user_id": current_user["id"]}, limit=5)

# After
contacts_rows = await _deps.db_supabase.get_rows(
    "emergency_contacts", {"user_id": current_user["id"]}, limit=MAX_EMERGENCY_CONTACTS
)
```

## Rollback plan

`git-revert-safe` — no data, no migration, no schema change; pure code constant sharing.

## Verification performed

- `pytest -k "safety or emergency_contact or sos or users"` → 366 passed (pre-existing RLS-needs-real-Postgres
  tests self-error without `TEST_DATABASE_URL`, unrelated to this change, same as documented in CLAUDE.md).
- `ruff check` on both changed files → clean.
- Full backend suite (`pytest tests/`, no filter) run separately this session → 13,379 passed, only the
  same pre-existing RLS-needs-Postgres errors and zero failures related to this change.

## What was NOT verified

- Not exercised against a real Supabase instance — mocked `db_supabase.get_rows` only, per this repo's
  standard unit-test convention.
- No manual device/SOS-flow repro (would require a live phone number + Twilio sandbox).
