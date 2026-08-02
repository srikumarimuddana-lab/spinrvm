# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin, corporate |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + Admin Portal Review — High #5 |

## 1. Issue / gap identified

The dual-approval gate for large PII-bearing exports (`dual_approval_exports_enabled`)
was already fully implemented on the read side — migration 268 added the
column, `routes/admin/compliance.py` and `routes/admin/data_transfer_export.py`
both branch on `settings.get("dual_approval_exports_enabled")` to require a
second super_admin's sign-off before a large driver/rider export or a
>1,000-row compliance report runs — but the field was missing from
`SettingsUpdateRequest` in `routes/admin/settings.py` and had no control in
the admin-dashboard settings UI. There was no way to turn the gate on
without a direct SQL update against `app_settings`, so in practice it has
never been enabled.

## 2. Root cause

The gate was built end-to-end for its enforcement path (migration + two
call sites already read and branch on the flag) but the settings-write
surface was never extended to expose it — an oversight in the original
implementation, not a deliberate decision to keep it SQL-only.

## 3. Fix / remediation

- Added `dual_approval_exports_enabled: Optional[bool] = None` to
  `SettingsUpdateRequest` in `backend/routes/admin/settings.py`, right after
  `admin_theme_v2_enabled`.
- Deliberately did **not** add it to `_SUPER_ADMIN_ONLY_FIELDS`: it is a
  plain feature flag, not a destination credential (nothing to leak, no
  masked-preview concern like the LMS/Meta/SOS-paging/payment-credential
  fields H4 closed). The actual approval action it gates is already
  `require_super_admin`-enforced at the point of use in
  `routes/admin/export_approvals.py`, so restricting who can flip the
  *toggle* itself would add friction without closing a real gap — any admin
  with the `settings` module can already change dozens of other
  operational flags of similar risk shape (e.g. `admin_theme_v2_enabled`).
- Added a "Data Export Approvals" card to the `security` tab of
  `admin-dashboard/src/app/dashboard/settings/page.tsx`, next to the
  existing Two-Factor Authentication card, following the same
  `<Switch>`/`<Label>`/`checked={... ?? false}`/`onCheckedChange` pattern
  used throughout the file. Off by default (matches the column default and
  the current de-facto behavior, so flipping this PR in has no effect until
  an admin explicitly turns it on).

## 4. Risk & impact on existing functionality

- **Blast radius: one new optional Pydantic field, one new UI card.** No
  change to `_SUPER_ADMIN_ONLY_FIELDS`, no change to `_CREDENTIAL_FIELDS`,
  no change to the enforcement logic in `compliance.py` or
  `data_transfer_export.py` (both already read this key with the same
  `settings.get(...)` pattern used for every other boolean flag — no code
  change needed there).
- Grepped every reader of `dual_approval_exports_enabled`:
  `routes/admin/compliance.py` (line ~162, gates compliance reports
  >1,000 rows), `routes/admin/data_transfer_export.py` (line ~128, gates
  bulk driver/rider exports with `entity_count > 0`). Both already have
  passing tests (`test_compliance_reports_http.py`,
  `test_data_transfer_export_route.py`) that mock this key directly via
  `get_app_settings` — confirming the enforcement path is independent of
  and unaffected by this change.
- No other `SettingsUpdateRequest` field or test references this name, so
  adding it cannot collide with or shadow an existing field.
- Frontend: `settings` state and the `update()` helper are both loosely
  typed (`any`), so no TypeScript interface needed updating; ran
  `tsc --noEmit` against the whole `admin-dashboard` project — 27
  pre-existing errors, all in unrelated test files
  (`driver-statements-panel.test.tsx`, `companyApi.test.ts`,
  `route-segments.test.ts` — missing Jest/Vitest type defs), none in
  `settings/page.tsx`.

## 5. User-experience effect

**Internal admin-facing only, and inert by default.** Any admin with the
`settings` module now sees a new "Data Export Approvals" toggle in
Settings → Security; flipping it on requires a second super_admin to
approve large exports/compliance reports going forward (existing behavior
for anyone below the row-count threshold is unchanged either way). Because
the flag currently defaults to off in the DB, this commit changes nothing
for any admin unless someone deliberately turns it on afterward — not
visible mid-session to anyone, since nobody could have relied on this gate
being active before it was even reachable.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/settings.py` | Added `dual_approval_exports_enabled: Optional[bool] = None` to `SettingsUpdateRequest` | Only field missing to let this already-built gate be turned on without direct SQL |
| `admin-dashboard/src/app/dashboard/settings/page.tsx` | Added a "Data Export Approvals" Switch card to the Security tab | Give admins a UI path to the new field |

## 7. Before / after

```python
# Before — no way to set this via the API
class SettingsUpdateRequest(BaseModel):
    ...
    admin_theme_v2_enabled: Optional[bool] = None
    # dual_approval_exports_enabled not present
```

```python
# After
class SettingsUpdateRequest(BaseModel):
    ...
    admin_theme_v2_enabled: Optional[bool] = None
    dual_approval_exports_enabled: Optional[bool] = None
```

```tsx
{/* After — new card in the security tab */}
<Card className="border-border/50">
  <CardHeader><CardTitle className="text-base">Data Export Approvals</CardTitle></CardHeader>
  <Separator />
  <CardContent className="pt-4 space-y-4">
    <div className="flex items-center justify-between">
      <div className="space-y-0.5">
        <Label htmlFor="dual_approval_exports_enabled">
          Require a second super admin to approve large PII exports
        </Label>
        <p className="text-xs text-muted-foreground">...</p>
      </div>
      <Switch
        id="dual_approval_exports_enabled"
        checked={settings.dual_approval_exports_enabled ?? false}
        onCheckedChange={(v) => update("dual_approval_exports_enabled", v)}
      />
    </div>
  </CardContent>
</Card>
```

## 8. Rollback plan

Plain code change, no migration (column already exists from migration 268),
no data written by this commit. `git revert` fully restores the prior
state — the flag would again default to off and be unreachable from the
UI, which is the exact behavior every admin has experienced up to now. If
the toggle is turned on in production and causes unexpected friction, an
admin can flip it back off from the same UI in one click — no deploy or SQL
needed either direction.

## 9. Verification performed

- [x] Automated tests: `test_compliance_reports_http.py` (37),
      `test_data_transfer_export_route.py` (15),
      `test_admin_settings_payment_credential_gate.py` (14),
      `test_admin_settings_lms_gate.py` (19) — 85 passed, run from repo
      root via the session's `/tmp/spinr_venv` venv.
- [x] `ruff check` on `backend/routes/admin/settings.py` — clean.
- [x] `tsc --noEmit -p tsconfig.json` on `admin-dashboard` — 27
      pre-existing errors, none in `settings/page.tsx` (confirmed via
      `grep "settings/page.tsx"` on the output — zero matches — and by
      manually reading every error line).
- [ ] Manual repro in staging / real `npm run build` on `admin-dashboard` —
      not performed, no staging access; `tsc --noEmit` was used as a
      lighter-weight compile check, not a substitute for a production
      build.
- [x] Blast-radius grep performed (see §4): both enforcement call sites,
      both their test files, no other `SettingsUpdateRequest` collision.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — both readers of the flag
      grepped and confirmed already-tested and unaffected
- [x] No silent behavior change to a working flow — the flag defaults off,
      so no admin's current export/compliance-report experience changes
      until someone opts in via the new toggle

## What was NOT verified

Did not run a real production build (`npm run build`) of `admin-dashboard`
— only `tsc --noEmit`, which type-checks but does not catch bundler-level
or build-time-only failures. Did not visually screenshot the new Settings
card in a running dev server (no browser verification performed in this
session for this fix); the Switch/Card/Label composition was reasoned
about by matching the exact pattern already used for
`admin_theme_v2_enabled` a few lines above it, but is not itself
pixel-verified. Did not add a dedicated backend test asserting
`dual_approval_exports_enabled` round-trips through
`admin_update_settings` and persists — the two existing test files that
already mock and depend on this exact key
(`test_compliance_reports_http.py`, `test_data_transfer_export_route.py`)
cover the read side; the write side follows the identical, already-tested
pattern as every other plain boolean field in this model (e.g.
`admin_theme_v2_enabled`, none of which have a dedicated persistence test
either), so a new test was judged to add coverage duplication rather than
close a real gap.
