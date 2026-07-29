# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (this PR) |
| Related issue or gap ID | #2785 (admin-dashboard visual-refresh epic), Phase 3 prerequisite |

## 1. Issue / gap identified

Epic #2785's Phase 3 (shared shell/typography/radius restyle) touches every admin-dashboard route via shared primitives. Per `CLAUDE.md`'s pre-merge gate #3, a change with that blast radius must ship behind a flag, not big-bang — and admin-dashboard currently has no feature-flag mechanism at all.

## 2. Root cause

Not a bug — a missing prerequisite. Spinr's `app_settings`-in-DB pattern already supports flag-without-redeploy for backend behavior (Stripe keys, `fare_lock_enabled`, `ai_assistant_enabled`, etc.), but nothing on the frontend reads that endpoint as a set of boolean feature flags scoped for component-level gating.

## 3. Fix / remediation

Added one new boolean setting, `admin_theme_v2_enabled`, end-to-end:
- New migration adds the column (default `FALSE`) to `public.settings`.
- `AppSettings` (backend/schemas.py) and `SettingsUpdateRequest` (backend/routes/admin/settings.py) both gain the field so it round-trips through the existing `GET`/`PATCH /api/admin/settings` endpoints with zero special-case code (same treatment as `fare_lock_enabled` — not a credential, no masking/super-admin gate).
- New admin Settings page toggle ("Admin Dashboard Appearance (Beta)") lets a super-admin flip it.
- New `useFeatureFlag.tsx` hook: a `FeatureFlagsProvider` (explicit allowlist of flag keys, not a spread of the raw settings response — that response also carries masked credential fields) fetches `/api/admin/settings` once authenticated and exposes flags via `useFeatureFlag(key)`, defaulting every flag to `false` on fetch failure.
- Mounted `FeatureFlagsProvider` in `dashboard/layout.tsx` (the authenticated boundary), not the root layout, so unauthenticated routes (`/login`, `/company-portal`, etc.) never fire the request.

No component reads `admin_theme_v2_enabled` yet — this PR only builds the mechanism. Phase 3's actual restyle work will gate on it in a follow-up PR.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** `admin_theme_v2_enabled` is a new column and a new optional field on two existing Pydantic models — no existing field, default, or validator changed. `SettingsUpdateRequest` grepped for every other field (~90 fields) — none reused or renamed.
- `GET /api/admin/settings` / `PATCH /api/admin/settings`: only other consumer is the Settings page itself (`admin-dashboard/src/app/dashboard/settings/page.tsx`), which already handles unknown/new fields generically via its `update(key, value)` helper and typed `settings: any` state — no other page calls these endpoints.
- `dashboard/layout.tsx`: the only other logic in this file is the auth-redirect effect and the loading/unauthenticated early returns, both untouched; `FeatureFlagsProvider` wraps children purely additively (a passthrough context provider, no rendering change, no additional loading state gating the UI).
- Settings 60s in-process cache (`settings_loader.py`) is unaffected — no change to cache invalidation logic, only a new column read through the same path.
- No other admin-dashboard code currently imports `useFeatureFlag` or `FeatureFlagsProvider` (new files), so nothing else is affected by their existence.

## 5. User-experience effect

- Internal-admin facing only. A new toggle appears on the Settings page ("Admin Dashboard Appearance (Beta)"), defaulting to off. Toggling it currently has **no visible effect** anywhere else in the product — no component reads the flag yet.
- Not visible mid-session to riders/drivers/corporate admins; this surface has no rider/driver/corporate exposure.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/269_settings_admin_theme_v2.sql` | New migration adding `admin_theme_v2_enabled BOOLEAN DEFAULT FALSE` to `public.settings` | Persist the flag; RLS N/A (service-role-only config table, consistent with all other `settings` columns) |
| `backend/schemas.py` | Added `admin_theme_v2_enabled: bool = False` to `AppSettings` | Backend read model for `GET /api/admin/settings` |
| `backend/routes/admin/settings.py` | Added `admin_theme_v2_enabled: Optional[bool] = None` to `SettingsUpdateRequest` | Backend write model for `PATCH /api/admin/settings`; no credential/super-admin-only list entry needed (plain boolean) |
| `admin-dashboard/src/app/dashboard/settings/page.tsx` | New "Admin Dashboard Appearance (Beta)" Card with a Switch bound to `admin_theme_v2_enabled` | Lets a super-admin toggle the flag without direct DB/API access |
| `admin-dashboard/src/hooks/useFeatureFlag.tsx` | New file: `FeatureFlagsContext`, `FeatureFlagsProvider`, `useFeatureFlag(key)` | Frontend mechanism for components to read the flag |
| `admin-dashboard/src/app/dashboard/layout.tsx` | Wrapped the authenticated return block in `<FeatureFlagsProvider>` | Makes flags available to every dashboard route without hitting the endpoint pre-login |

## 7. Before / after

```
# Before (admin-dashboard/src/app/dashboard/layout.tsx)
return (
    <div className="min-h-screen bg-background">
        <Sidebar />
        <main className="transition-all duration-200 md:ml-[var(--sidebar-width,240px)]">
            <div className="p-4 pt-14 md:pt-6 md:p-8">{children}</div>
        </main>
    </div>
);
```

```
# After
return (
    <FeatureFlagsProvider>
        <div className="min-h-screen bg-background">
            <Sidebar />
            <main className="transition-all duration-200 md:ml-[var(--sidebar-width,240px)]">
                <div className="p-4 pt-14 md:pt-6 md:p-8">{children}</div>
            </main>
        </div>
    </FeatureFlagsProvider>
);
```

## 8. Rollback plan

- Frontend: revert the `dashboard/layout.tsx` wrapping and delete `useFeatureFlag.tsx` — the flag becomes unread, no other code path depends on it.
- Backend: the flag itself doesn't need a "rollback" in the live-data sense (it's a config bit defaulting to `false`, moves no money, changes no ride state) — but if needed: `ALTER TABLE public.settings DROP COLUMN IF EXISTS admin_theme_v2_enabled;` (included as a comment in the migration file itself).
- No feature-flag-off step is needed for this PR specifically, since it introduces the flag mechanism itself rather than user-visible behavior gated by it — the flag defaults to off and nothing reads it yet.

## 9. Verification performed

- [x] Automated tests run — none exist yet for this narrow mechanism (no assertions were broken by the change); `npm run lint` (0 errors, pre-existing 183 warnings unchanged, none in the new/changed files) and **`npm run build`** (real production build, not just `tsc --noEmit` — all 34 dashboard routes, including `/dashboard/settings`, compiled successfully) both run for admin-dashboard.
- [x] Backend Pydantic model changes verified via direct interpreter check: `AppSettings().admin_theme_v2_enabled` → `False`; `SettingsUpdateRequest(admin_theme_v2_enabled=True).admin_theme_v2_enabled` → `True`; unset → `None` (matches every other optional field's round-trip behavior).
- [x] Migration reviewed by the `spinr-migration-reviewer` subagent: SAFE TO APPLY, no blockers (RLS N/A, numbering correct, append-only, reversible on paper).
- [x] Blast-radius grep performed: searched for all callers of `getSettings`/`updateSettings` (Settings page only) and all other fields in `SettingsUpdateRequest`/`AppSettings` (no collisions, ~90 existing fields untouched).
- [x] Reviewed against `CLAUDE.md` conventions: `app_settings`-in-DB pattern (followed exactly), migration append-only/reversible rules (followed), feature-flag-for-blast-radius gate #3 (this PR *is* that gate being built).
- [ ] Feature-flagged if user-visible and non-trivial — N/A in the strict sense: this PR *is* the flag; the Settings toggle itself is a small, isolated, non-trivial-but-low-risk addition and was not itself flagged.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (revert two frontend edits, or drop the column — both zero live-data impact).
- [x] Blast radius is stated, not assumed: isolated to two backend Pydantic models (additive optional fields) and three new/edited frontend files, all unread by any other existing code path.
- [x] No silent behavior change to an already-shipped flow: the Settings page gains a new Card (additive), `dashboard/layout.tsx`'s only behavior change is providing an inert context; no existing route's rendering, auth flow, or data changes.

## What was NOT verified

- Not tested against a live Supabase instance — the migration was reviewed by the `spinr-migration-reviewer` subagent and follows the exact template of prior `settings` column additions (e.g. `93_settings_missing_columns.sql`), but was not applied to a running database in this session.
- No visual/E2E check of the new Settings page toggle rendering correctly in a browser (no dev server was started for this specific PR) — reasoned from the identical existing `fare_lock_enabled` toggle pattern in the same file, not screenshotted.
- Full backend test suite was not run (`pytest`) — only a direct interpreter import/validation check of the two changed models, due to this sandbox's Python environment requiring a lengthy dependency install; the two touched models were validated in isolation, not via the full route/integration test suite.
