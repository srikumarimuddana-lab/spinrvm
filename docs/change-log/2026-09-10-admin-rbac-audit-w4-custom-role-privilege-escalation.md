# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code (admin portal security/RBAC audit, requested by user) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| Related issue or gap ID | `docs/audit/2026-09-10-admin-portal-security-rbac-audit.md`, finding W4 |

## 1. Issue / gap identified

A super_admin could set `role="custom"` + every `AVAILABLE_MODULES` string
via `POST /staff` or `PUT /staff/{id}`, reaching super_admin-equivalent
access on every `require_module()`-gated router (`require_module()`'s check
is `module in modules`, so a full modules list passes all of them), without
the account ever being flagged `role=="super_admin"` — meaning it skips both
the `password_confirmation` re-auth an actual super_admin promotion requires
(A-P3-6) and any distinct audit-log signal calling out what happened.

## 2. Root cause

The A-P3-6 promotion safeguard only checked `req.role == "super_admin"`; it
never considered that `role="custom"` with a full module set is functionally
identical access without the explicit role label.

## 3. Fix / remediation

- Extracted the existing promotion re-auth logic (inline in `update_staff`)
  into a shared `_require_actor_password_confirmation(admin, password_confirmation, *, reason)`
  helper — same behavior, reused in two places now.
- `create_staff`: if `role != "super_admin"` and the resulting modules set
  equals `AVAILABLE_MODULES`, require the same password confirmation before
  creating the account; audit log gets a `"super_admin_equivalent": true` flag.
- `update_staff`: if the resulting role/modules after this update reach full
  parity (`resulting_role != "super_admin" and resulting_modules == AVAILABLE_MODULES`)
  **and** the staffer wasn't already at full parity before this request (a
  transition check, mirroring the promotion check's own `s.get("role") != "super_admin"`
  guard — so an unrelated edit to an already-full-parity account doesn't
  re-prompt every time), require the same confirmation; audit log gets the
  same flag.
- `StaffCreateRequest` gained a `password_confirmation: Optional[str]` field
  (didn't have one at all before).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `create_staff`/`update_staff` in this one
  file.** Grepped for other callers — none found outside this router and
  its own tests.
- **What could regress:** the pre-existing super_admin-promotion re-auth
  path (`test_promotion_*` tests) — confirmed unaffected, all pass unchanged
  after the extraction into a shared helper (behavior is byte-identical,
  just deduplicated).
- **Frontend consideration (not fixed in this PR, flagged deliberately):**
  `admin-dashboard/src/app/dashboard/staff/page.tsx` never sends
  `password_confirmation` in its `createStaff`/`updateStaff` calls today —
  this is a **pre-existing** gap: selecting `role="super_admin"` via the
  role dropdown already 422s against A-P3-6 with no way to complete it from
  the UI, confirmed by reading the page's submit handler before this fix.
  My change adds a second, narrower path to the same pre-existing class of
  gap: an operator manually checking all 17 "Module Access" checkboxes
  while `role="custom"` would now also 422. Not fixing the frontend here —
  that's a separate UI change (a `window.prompt()`-style confirmation,
  mirroring the `tax_justification` prompt precedent in
  `2026-08-12-a29-tax-config-audit-justification.md`) outside this audit's
  scope, and the backend gate is strictly safer than not having it even
  while the UI can't drive it yet.

## 5. User-experience effect

**Internal admin only.** A super_admin attempting to grant a `custom`-role
staffer every module (via API directly, or via the dashboard's checkboxes
once/if a frontend confirmation prompt is added) now needs to re-enter
their own password first — same UX as promoting someone to `super_admin`
already requires.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/staff.py` | Extracted `_require_actor_password_confirmation()` helper; wired into `create_staff` (new) and `update_staff` (replaces inline duplicate); added `password_confirmation` to `StaffCreateRequest`; added `super_admin_equivalent` audit-log flag to both `staff_created` and `staff_updated` | Close the privilege-escalation-by-composition gap; give it the same re-auth + audit signal as an explicit promotion |
| `backend/tests/test_admin_staff_coverage.py` | 7 new tests: create/update full-parity requires confirmation, succeeds-with-confirmation flags audit, partial modules unaffected, editing an already-full-parity account doesn't re-prompt, super_admin promotion isn't double-gated | Cover both the new gate and its edge cases (transition-only, no double-prompt) |

## 7. Before / after

```python
# Before (update_staff) — only checked the explicit role="super_admin" case
if req.role == "super_admin" and s.get("role") != "super_admin":
    if not req.password_confirmation:
        raise HTTPException(422, "password_confirmation required for super_admin promotion")
    # ... inline actor-password verification ...

# After — same check via the shared helper, PLUS a new transition check
if req.role == "super_admin" and s.get("role") != "super_admin":
    await _require_actor_password_confirmation(admin, req.password_confirmation, reason="super_admin promotion")

# ... after `updates` (role/modules) is fully built ...
if _resulting_role != "super_admin" and _resulting_modules == _full_modules and _prior_modules != _full_modules:
    await _require_actor_password_confirmation(
        admin, req.password_confirmation, reason="a custom role granting every available module"
    )
```

## 8. Rollback plan

`git revert`-safe. Pure authorization/validation addition — no data written
or migrated by this change itself. Reverting restores the narrower
pre-existing gate (promotion-only), re-opening the composition path.

## 9. Verification performed

- [x] `pytest tests/test_admin_staff_coverage.py tests/test_admin_staff_mfa_reset.py tests/test_admin_staff_schema_cache_reload.py tests/test_admin_rbac.py tests/test_admin_routes_auth.py tests/test_admin_security.py -q` — 96 passed (89 pre-existing + 7 new), 0 failed.
- [x] `ruff check` + `ruff format --check` — clean.
- [x] Blast-radius grep for other callers of `create_staff`/`update_staff` — none outside this file and its tests.
- [x] Confirmed the pre-existing super_admin-promotion tests still pass unchanged after extracting the shared helper (behavior-preserving refactor).
- [x] Checked the admin-dashboard frontend directly (not just reasoned about) before writing the "frontend consideration" note above — confirmed via reading `staff/page.tsx`'s submit handler that `password_confirmation` is never sent today, for either the pre-existing super_admin-promotion case or the new custom-role-parity case.

## What was NOT verified

- Not tested against live Supabase — mocked dependencies only.
- No `npm run build` / frontend verification — no admin-dashboard files were
  modified by this fix (deliberately left as a documented follow-up, see §4).
- Whether any production `custom`-role staff account currently holds every
  `AVAILABLE_MODULES` string (and would therefore be flagged/blocked on its
  *next* edit, not retroactively) — not checked, no production DB access
  from this session.
- Full backend test suite not run in this pass; deferred to the final
  combined verification pass across all 9 audit fixes.
