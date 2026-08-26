# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (agent), approved by @vikas |
| Surface(s) | backend |
| Domain (Sentry tag) | safety |
| PR / commit link | #4320, commit `8471a5d` |
| Related issue or gap ID | Ranked blocker #13 / decision-log item #13; PIA `docs/audit/2026-08-21-emergency-contact-pia-memo.md` risk R-001 |

## 1. Issue / gap identified

A rider's emergency contact (name, phone) is a third-party PIPEDA data
subject whose data was stored as plain `TEXT` in `emergency_contacts` with
no encryption at rest — unlike driver PII (`license_number`, `vehicle_vin`),
which has been encrypted via Supabase Vault since migration 32/78/137/138.

## 2. Root cause

`emergency_contacts` was added after the driver-PII encryption pattern
already existed, but the pattern was never applied to it — an oversight
surfaced by the 2026-08-18 full-fleet audit (ranked blocker #13), not a new
regression.

## 3. Fix / remediation

`backend/migrations/357_encrypt_emergency_contacts.sql` adds
`encrypt_emergency_contact_pii()` / `decrypt_emergency_contact_pii()`
Postgres RPCs, mirroring the corrected, twice-patched final form of the
driver-PII pattern (migration 138's `vault.create_secret()` API,
`search_path` pinned, `OWNER TO supabase_admin`, service_role-only execute
grants) rather than migration 32's original (buggy) draft. No schema
change — `name`/`phone` stay `TEXT` columns; the app calls these RPCs on
every write/read going forward.

**This migration alone is a no-op**: it only creates the two functions and
revokes column-level `SELECT` from `anon`/`authenticated` (a hardening step
with no user-facing effect, since the backend reads via `service_role`,
which is unaffected by column grants). No caller invokes the new functions
yet — `backend/routes/users.py`'s emergency-contacts GET/POST/DELETE
handlers still read/write the columns directly via the existing
`insert_one`/`get_rows` helpers. That app-code wiring is a separate,
already-planned follow-up subtask (tracked in this session's `/plan`
decomposition, subtasks 2-7) and will carry its own Change Impact Log entry
appended to this same file when it lands, per CLAUDE.md's "one logical
change per commit" rule — this entry covers migration 357 only.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped `backend/` for all callers of
  `emergency_contacts` (`backend/routes/users.py` GET/POST/DELETE handlers,
  `backend/routes/rides/safety.py`'s SOS disclosure read at line ~315) —
  none of them call the two new RPCs yet, so none of them are affected by
  this migration. The two `REVOKE SELECT` statements only remove
  `anon`/`authenticated` column access; the backend's `service_role` key
  bypasses column grants entirely (same as it bypasses RLS), so existing
  reads/writes are unaffected.
- No interaction with the ride state machine, background loops, or money/
  wallet deltas — this is a safety/PII-domain migration only.
- Nothing currently reads `emergency_contacts.name`/`.phone` via a rider's
  own JWT (no PostgREST direct-table access is exposed to clients for this
  table per `backend/migrations/120_ensure_emergency_contacts_and_gps_column.sql`'s
  RLS policies — only owner-scoped `SELECT`/`INSERT`/`DELETE`, no client
  bypass of the backend), so the `REVOKE` is defense-in-depth with no
  expected behavioral change even for that path.

## 5. User-experience effect

None. No caller invokes the new functions yet, so no rider, driver,
corporate admin, or internal admin sees any difference. This will change
once the app-code wiring subtask lands (existing plaintext rows keep
working via the dual-read fallback in `decrypt_emergency_contact_pii()`, so
even that follow-up is expected to be invisible to riders under normal use).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/357_encrypt_emergency_contacts.sql` | New migration: adds `encrypt_emergency_contact_pii()`/`decrypt_emergency_contact_pii()` RPCs, ownership/grants, column-level `REVOKE SELECT` on `emergency_contacts.name`/`.phone` | Close PIA risk R-001 (safeguards) for emergency-contact PII at rest |

## 7. Before / after

Pure additive migration (new functions only, no existing behavior changed) — before/after not applicable per the template's own exemption for additive-only diffs.

## 8. Rollback plan

```sql
DROP FUNCTION IF EXISTS encrypt_emergency_contact_pii(text);
DROP FUNCTION IF EXISTS decrypt_emergency_contact_pii(text);
```

Safe to run at any point before the app-code wiring subtask ships, since no
caller depends on these functions yet. **After** that subtask ships, any
row written or re-added post-migration will hold an opaque vault-secret
UUID in `name`/`phone` — dropping the functions at that point makes those
specific rows unreadable without a data-level remediation (re-decrypt via
the RPC and write plaintext back before dropping). Rows that predate the
app-code change, or that are never re-saved, are untouched plain text and
unaffected either way. The column-level `REVOKE SELECT` grants can be
reversed with the matching `GRANT SELECT (name), SELECT (phone) ON TABLE
emergency_contacts TO anon, authenticated;` if ever needed, though there is
no reason to.

## 9. Verification performed

- [x] Reviewed against relevant `backend/migrations/CLAUDE.md` conventions (append-only, RLS/grants, reversibility, `SECURITY DEFINER` + `search_path` pinning for money/PII functions) via two independent `spinr-migration-reviewer` subagent passes — first draft flagged **BLOCKER** (reproduced two known historical bugs: raw `vault.secrets` INSERT permission-denied error, unpinned `search_path` privilege-escalation gap); rewrite confirmed all 5 checks **PASS** with one non-blocking note (search_path intentionally narrower than migration 137's intermediate value, matching 138's actual production end state — flagged so a future PR doesn't "fix" it back incorrectly).
- [x] Blast-radius grep performed: searched `backend/` for every reader/writer of `emergency_contacts` (`backend/routes/users.py`, `backend/routes/rides/safety.py`) — confirmed none call the new RPCs yet, so this migration cannot regress any existing path.
- [ ] Automated tests run — none apply; this is a pure DDL/function-creation migration with no application code calling it yet, so there is nothing for a Python test to exercise. Coverage will be added alongside the app-code wiring subtask.
- [ ] Manual repro / staging check — **not performed**. No live Supabase project was available in this session; the migration was reviewed against the exact known-good pattern already running in production for driver PII (migrations 78/137/138), but was not applied against a real Postgres/pgsodium instance.
- [ ] Feature-flagged — not applicable; this migration alone has no observable effect (see Section 3), so there is nothing to flag yet. The follow-on app-code wiring subtask will be evaluated for flagging on its own merits when it lands.

## 10. What was NOT verified

- Not run against a real Supabase/Postgres instance — no live project access in this session. Correctness is based on matching migration 138's already-production-proven pattern function-for-function, not a fresh empirical run.
- No automated visual/regression tooling applies (backend-only, no UI surface).
- The paired app-code change (encrypt/decrypt RPC calls in `backend/routes/users.py`) has not yet landed — this migration is verified as a no-op in isolation, not as part of the full closed-loop encryption flow, which is out of scope for this entry.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (see Section 8)
- [x] Blast radius is stated, not assumed (isolated — no current callers)
- [x] No silent behavior change to an already-shipped flow — this migration has zero observable effect until the follow-up app-code subtask ships
