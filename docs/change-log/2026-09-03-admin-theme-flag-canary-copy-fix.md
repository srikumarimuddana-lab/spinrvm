# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | Claude, at user request — follow-up after discussing flipping `admin_theme_v2_enabled` "for a canary group," which surfaced that the flag has no such capability and the docs/UI both said otherwise |
| Surface(s) | admin-dashboard, backend |
| Domain (Sentry tag) | admin |
| PR / commit link | commit `1283d1c56`, branch `claude/admin-theme-flag-copy-fix` |
| Related issue or gap ID | None filed — found and fixed in conversation |

## 1. Issue / gap identified

The admin Settings page's description for "Enable refreshed admin theme" read: *"Canary flag for the in-progress visual refresh... takes effect for all staff within about a minute of toggling"* — calling it a canary flag in the same sentence as saying it affects all staff. Three backend/frontend code comments made the same claim.

## 2. Root cause

The flag was planned as canary-able when the epic (#2785 Phase 3+) was scoped (migration 269's own comment: "toggle via the admin Settings page to canary/roll out"), but what actually shipped is a single global boolean in the `settings` table with no per-user or per-role targeting — true of every flag in this settings system (`driver_discreet_sos_enabled`, `rideless_sos_enabled`, etc. all follow the same all-or-nothing pattern; verified by reading `useFeatureFlag.tsx` and the settings schema). The "canary" wording was never corrected once the simpler global-boolean version shipped instead.

## 3. Fix / remediation

Corrected the description text and three code comments to say what the flag actually does — a single global on/off switch — rather than implying per-user targeting that doesn't exist:
- `admin-dashboard/src/app/dashboard/settings/page.tsx`: the `<p>` shown next to the toggle, plus its surrounding JSX comment.
- `backend/routes/admin/settings.py`: the `AppSettingsUpdate` field comment.
- `backend/schemas.py`: the `AppSettings` field comment (previously said "canary-able flag").

**Deliberately not touched**: `backend/migrations/269_settings_admin_theme_v2.sql`'s own comment/`COMMENT ON COLUMN` also says "canary/rollout" — left as-is per the append-only migration convention (`backend/migrations/CLAUDE.md`); it's historical record of what was planned at the time, not something this fix rewrites.

## 4. Risk & impact on existing functionality

None — this is a documentation/copy-only change. No logic, no schema, no API contract, no rendering behavior changed. Grepped for every other reference to `admin_theme_v2_enabled` (39 files) before editing; none of the others describe it as a canary flag, so no other file needed the same fix.

## 5. User-experience effect

Admin-facing only, and purely textual: staff reading the Settings page now see an accurate description ("for every admin and staff account at once — this switch is global, not a per-person rollout") instead of a self-contradictory one. No functional change to what toggling the switch actually does.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/settings/page.tsx` | Rewrote the toggle's description `<p>` and its JSX comment | User-facing text was self-contradictory |
| `backend/routes/admin/settings.py` | Rewrote the field comment | Matched the corrected framing |
| `backend/schemas.py` | Rewrote the field comment | Matched the corrected framing |

## 7. Before / after

```tsx
// Before
<p className="text-xs text-muted-foreground">
    Canary flag for the in-progress visual refresh (shared nav/shell, typography, spacing).
    Off by default — takes effect for all staff within about a minute of toggling, no redeploy.
</p>

// After
<p className="text-xs text-muted-foreground">
    Turns on the in-progress visual refresh (shared nav/shell, typography, spacing)
    for every admin and staff account at once — this switch is global, not a
    per-person rollout. Off by default; takes effect within about a minute of
    toggling, no redeploy.
</p>
```

## 8. Rollback plan

Plain `git revert` — text-only, no data, no migration.

## 9. Verification performed

- [x] `tsc --noEmit` — no new errors.
- [x] `eslint` on the changed frontend file — 0 errors (same known pre-existing eslint 10.9.1/eslint-plugin-react workaround as this session's other admin-dashboard PRs: linted with a local unsaved `eslint@9.39.5`, then restored the pinned version). All 4 warnings present are pre-existing, at line numbers untouched by this diff.
- [x] Backend files — `ast.parse()` confirms both edited `.py` files remain syntactically valid (comment-only edits, no logic touched).
- [x] Real production build (`npm run build`) — exit code 0, confirmed via full-log grep for "error".
- [x] Grepped all 39 files referencing `admin_theme_v2_enabled` to confirm no other file carries the same "canary" mischaracterization.

## What was NOT verified

- No live browser check that the Settings page renders the new copy correctly — same standing gap as the rest of this session's admin-dashboard work (no visual-regression tooling). Text-only change with a clean build; low risk.
