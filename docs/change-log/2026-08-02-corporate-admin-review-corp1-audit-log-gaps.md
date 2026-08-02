# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + Admin Portal Review — Corporate #1 |

## 1. Issue / gap identified

Five corporate-admin mutation paths had no audit trail at all, despite
every structurally similar action in the same files (member status
changes, policy replace/patch, member invites) already being logged:

1. Member **role** change (`PATCH /company/{id}/members/{id}` with
   `role`) — a privilege escalation/de-escalation with zero audit record.
2. Member **policy_override** change (same endpoint, `policy_override`
   field) — whether a member is exempt from company fare/surge policy,
   unlogged.
3. **Allowance-request decisions** (`POST
   /company/{id}/allowance-requests/{id}/decide`) — approve/deny of a
   rider's top-up request, unlogged (the money movement itself is
   captured by `apply_grant`'s wallet ledger, but *who approved it and
   when* was not).
4. **Allowed-domain** add/remove (`POST`/`DELETE
   /company/{id}/allowed-domains`) — the email-domain allowlist that
   auto-qualifies riders to join a company, unlogged both directions.
5. **Corporate section** create/update/archive
   (`routes/corporate_company_bookings.py`) — departments/cost-centers
   used for booking attribution, unlogged for all three mutations; this
   file didn't even import the audit logger.

## 2. Root cause

`routes/corporate_company.py` already had the `log_user_action` import
and the established try/except-wrapped call pattern (used for invites,
status transitions, and policy CRUD — several of which cite this exact
review as their own gap-closure precedent), but role/policy_override
changes and allowance-request decisions were added to the same file over
time without the same treatment being applied consistently.
`routes/corporate_company_bookings.py` (sections) never had the audit
logger wired in at all — a separate file with its own import block that
was never extended when sections were added.

## 3. Fix / remediation

- `routes/corporate_company.py`:
  - New helper `_maybe_log_role_or_policy_override_change`, called from
    `update_member` after the existing status-transition audit calls.
    Fires `corporate_member_role_changed` / `corporate_member_policy_
    override_changed` only when the new value actually differs from the
    stored value (no-op resubmission does not spam the log).
  - `add_domain`/`remove_domain` each log `corporate_allowed_domain_
    added`/`_removed` after the underlying DB write succeeds.
  - `decide_allowance_request` logs `corporate_allowance_request_decided`
    with the decision, amount (approve only), and note, after
    `update_allowance_request` succeeds.
- `routes/corporate_company_bookings.py`:
  - Added the `log_user_action` import (both dual-import branches) and a
    module logger.
  - `create_section`/`update_section`/`archive_section` each log
    `corporate_section_created`/`_updated`/`_archived` after their
    respective DB write.
- Every new call follows the codebase's established pattern exactly:
  wrapped in `try/except Exception: logger.error(..., exc_info=True)` so
  an audit-write failure can never break the underlying mutation (same
  fail-open contract as every existing audit call in these files).

## 4. Risk & impact on existing functionality

- **Blast radius: the 5 named mutation functions plus one new import
  block.** No change to any read path, to `apply_grant`'s wallet-ledger
  logic, or to `update_corporate_member`/`update_allowance_request`/
  `add_allowed_domain`/`delete_allowed_domain`'s own behavior — this
  fix only adds a logging call after each already succeeds.
- Two pre-existing tests broke as a *test-infrastructure* side effect
  (not a functional regression) because they share a single mocked
  `db_supabase.insert_one` across both the primary write and the new
  audit-log write:
  - `test_create_section_scopes_to_company` asserted on
    `insert_one.call_args` (the *last* call), which became the new
    audit-log insert instead of the section insert once both go through
    the same mock. Fixed by mocking `log_user_action` directly in that
    test (matching how every other audit-covered test in this codebase
    isolates the audit call), so `insert_one` is called exactly once
    again.
  - My own new `test_add_allowed_domain_writes_audit_log` initially
    asserted the wrong `resource_id` — `AllowedDomainCreate`'s
    `field_validator` already lowercases the domain before the route
    ever sees it, so `body.domain` was already `"acme.com"`, not
    `"Acme.COM"`. Fixed the assertion, not the code.
- Grepped every test file touching `update_member`, `add_domain`/
  `remove_domain`, `decide_allowance_request`, and the three section
  handlers (`test_corporate_company_routes.py`,
  `test_corporate_allowance_requests.py`, `test_corporate_sections.py`,
  `test_corporate_company_gap_coverage.py`, `test_corporate_e2e_
  members.py`) — 98 tests across all five files, all passing after the
  two fixes above. Every other pre-existing test in this set that
  exercises these code paths without mocking `log_user_action` still
  passes unmodified, because the conftest autouse fixture already mocks
  the underlying `db_supabase`/`repositories._base` Supabase client
  globally, so the new (unmocked-in-those-tests) audit calls resolve
  against the mock instead of a real DB and never raise.

## 5. User-experience effect

**None visible to riders/drivers.** Internal-only: a SOC analyst or
company admin reviewing the audit-logs page can now see who changed a
member's role, who flipped policy_override, who approved/denied an
allowance request, who added/removed an allowed domain, and who created/
edited/archived a section — actions that previously left no trace beyond
the row's own state (with no record of who changed it or when, only its
current value).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/corporate_company.py` | New `_maybe_log_role_or_policy_override_change` helper wired into `update_member`; audit calls added to `add_domain`, `remove_domain`, `decide_allowance_request` | Close 3 of the 5 named audit gaps |
| `backend/routes/corporate_company_bookings.py` | Added `log_user_action` import + module logger; audit calls added to `create_section`, `update_section`, `archive_section` | Close the remaining 2 gaps; this file had no audit logger wired in at all |
| `backend/tests/test_corporate_company_routes.py` | New tests for role-change, no-op-role, policy_override-change, add/remove-domain audit logging | Cover the new behavior |
| `backend/tests/test_corporate_allowance_requests.py` | New tests for approve/deny decision audit logging | Cover the new behavior |
| `backend/tests/test_corporate_sections.py` | New tests for create/update/archive audit logging; fixed `test_create_section_scopes_to_company` to isolate `log_user_action` so its pre-existing `insert_one` assertion targets the right call again | Cover the new behavior; fix a test-infra side effect of adding a second `insert_one` caller |

## 7. Before / after

```python
# Before — update_member: role/policy_override changes silently persisted
updated = await update_corporate_member(member_id, patch) or existing
if "status" in patch:
    await _maybe_revoke_access_on_removal(...)
    await _maybe_log_reactivation(...)
return updated
```

```python
# After
updated = await update_corporate_member(member_id, patch) or existing
if "status" in patch:
    await _maybe_revoke_access_on_removal(...)
    await _maybe_log_reactivation(...)
await _maybe_log_role_or_policy_override_change(
    company_id=company_id, member_id=member_id,
    existing=existing, patch=patch, actor=guard["user"],
)
return updated
```

```python
# Before — sections: no audit logger imported anywhere in this file
inserted = await db_supabase.insert_one("corporate_sections", row)
return inserted or row
```

```python
# After
inserted = await db_supabase.insert_one("corporate_sections", row)
try:
    await log_user_action(
        user=ctx["user"], action="corporate_section_created",
        resource="corporate_section", resource_id=row["id"],
        details={"company_id": ctx["company_id"], "name": row["name"]},
    )
except Exception:
    logger.error("Audit log failed for corporate_section_created section=%s", row["id"], exc_info=True)
return inserted or row
```

## 8. Rollback plan

Plain code change, no migration, no data written beyond new rows in the
already-existing, append-only `audit_logs` table. `git revert` fully
restores the prior (silent) behavior. No feature flag — closing an
observability gap on already-shipped mutation endpoints has no
meaningful dark-ship version; every new call is fail-open (wrapped in
try/except) so it cannot regress the underlying mutation even if the
audit write itself fails.

## 9. Verification performed

- [x] Automated tests: `test_corporate_company_routes.py` (41),
      `test_corporate_allowance_requests.py` (5),
      `test_corporate_sections.py` (17),
      `test_corporate_company_gap_coverage.py` (34),
      `test_corporate_e2e_members.py` (1) — 98 passed, run via the
      session's `/tmp/spinr_venv` venv from repo root.
- [x] `ruff check` on all 5 touched files — clean.
- [x] Blast-radius grep performed (see §4): every test file touching the
      5 fixed functions.
- [ ] Manual repro in staging — not performed, no staging access.
- [x] Dry-run scenario: a company admin promotes a member from `member`
      to `admin` via `PATCH /company/{id}/members/{id}`. Before this fix:
      the member row's `role` column changes with no trace of who did it
      or when. After this fix: an `audit_logs` row
      (`corporate_member_role_changed`, `old_role=member`,
      `new_role=admin`) is written in the same request, alongside the
      mutation.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — every dependent test file
      grepped, run, and (where it broke on an infra side effect, not a
      functional regression) fixed
- [x] No silent behavior change to a working flow — every new call is a
      pure addition (an audit-log write) that cannot alter the response
      or fail the request; the one behavior addition (role/policy_override
      is now compared against the stored value to detect true changes)
      only affects whether a NEW audit row is written, not the mutation
      itself

## What was NOT verified

Not tested against a live/staging Supabase — only mocked
`db_supabase`/`log_user_action` calls. Did not extend this fix to every
other corporate mutation in the codebase — the review named these 5
specific gaps, and I did not do a fresh from-scratch audit of every
corporate route for missing logging beyond what was already identified
in the source review; a broader sweep is a reasonable follow-up but out
of scope for this specific finding. Did not add a UI element in the
admin-dashboard audit-logs page to specially render these 5 new action
types (they'll appear as raw `action`/`details` JSON like every other
audit-log action already does — no dedicated formatting was added or
judged necessary).
