# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | safety |
| PR / commit link | (this branch, subtask 1 of 4 for PIA finding R-002) |
| Related issue or gap ID | PIA finding R-002 — `docs/audit/2026-08-21-emergency-contact-pia-memo.md` |

## 1. Issue / gap identified

Spinr sends an unsolicited SOS SMS to a rider's emergency contacts (third
parties, not Spinr users) with no consent and no opt-out mechanism.

## 2. Root cause

The SOS flow was built before the PIA review; no suppression list or
STOP-keyword handling exists for third-party contacts (only the CASL
marketing-consent system, `marketing_suppressions`, exists — and that is
keyed to a Spinr `user_id`, which emergency contacts don't have).

## 3. Fix / remediation

This subtask (1 of 4 parallel subtasks) adds ONLY the storage layer and a
new service:
- `backend/migrations/358_sos_contact_suppressions.sql` — new
  `sos_contact_suppressions` table (phone-keyed, unique index, RLS
  enabled/no policies — service-role-only) plus a new nullable
  `emergency_contacts.consent_notice_sent_at` column.
- `backend/services/sos_contact_consent.py` — `normalize_phone`,
  `is_suppressed` (fail-open on error), `suppress`, `unsuppress`.

**This subtask does not wire the service into the SOS send path, the
opt-out SMS webhook, or contact creation** — those are three other
parallel subtasks (`routes/rides/safety.py`, `routes/users.py`,
`routes/webhooks.py`) explicitly out of scope here to avoid merge
conflicts. Until those land, this commit is inert: a new unused table and
an unused service module.

## 4. Risk & impact on existing functionality

- Blast radius: **isolated**. New table, new nullable column (default
  NULL, no backfill), new service module with no importers yet. No
  existing code path reads or writes `sos_contact_suppressions` or
  `emergency_contacts.consent_notice_sent_at` today — grepped for both
  names across `backend/` and found only this commit's own files.
- No interaction with the ride state machine, wallet/money deltas, or the
  16 background loops.
- `emergency_contacts` gains a column via `ADD COLUMN IF NOT EXISTS`
  (nullable, no default computation) — additive, non-blocking under live
  traffic, does not touch the existing encrypted `name`/`phone` columns
  from migration 357.

## 5. User-experience effect

None yet — nothing reads this table or column. The opt-out itself (STOP
SMS handling, notice-on-contact-creation) ships in the other three
subtasks; this is plumbing only.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/358_sos_contact_suppressions.sql` | New table + new nullable column on `emergency_contacts` | Storage for the STOP opt-out list (PIA R-002) |
| `backend/services/sos_contact_consent.py` | New service: normalize/suppress/unsuppress/is_suppressed | Fail-open suppression-check gate for the (future) SOS send path |
| `backend/tests/test_sos_contact_consent.py` | New unit tests | Cover fail-open behavior, idempotent suppress, hard-delete unsuppress |

## 7. Before / after

Pure additive — no existing behavior changed. Skipped per template guidance.

## 8. Rollback plan

Migration is reversible on paper (top-of-file comment):
```sql
ALTER TABLE public.emergency_contacts DROP COLUMN IF EXISTS consent_notice_sent_at;
DROP TABLE IF EXISTS public.sos_contact_suppressions;
```
No live data is written to either object until the follow-up subtasks
wire the service in, so rollback here carries zero data-loss risk.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_sos_contact_consent.py -v` — 7/7 passed
- [ ] Manual repro steps followed in staging — N/A, nothing reads this yet
- [x] Blast-radius grep performed: `sos_contact_suppressions` and `consent_notice_sent_at` — no hits outside this commit's own files
- [x] Reviewed against relevant CLAUDE.md conventions: RLS pattern (mirrors migration 191), migration numbering/append-only, fail-open error handling per the safety-critical override of the "never swallow errors" rule (logged loudly, not silently)
- [ ] Feature-flagged — not applicable, nothing calls this code yet

**Migration review**: the `spinr-migration-reviewer` Agent tool was not
available in this subagent's toolset (no `Agent`/`Task` tool was present to
dispatch it). Self-reviewed against the same checklist the `/migration-check`
skill documents (numbering, append-only, RLS, reversibility, forward-compat,
indexes, money safety, retention) — all pass; no money columns. Flagging
this explicitly per CLAUDE.md's "what was NOT verified" requirement rather
than claiming a review that didn't happen.

## What was NOT verified

- No independent `spinr-migration-reviewer` agent pass (see above) — self-reviewed only.
- Migration not applied against a real Supabase instance (no `DATABASE_URL` in this environment) — reviewed for syntax/idempotency only (`IF NOT EXISTS` guards throughout).
- No integration test against real Postgres RLS enforcement — RLS policy absence was verified by reading the SQL, not by attempting an anon-role query.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed (isolated — no consumers yet)
- [x] No silent behavior change — this commit changes nothing observable
