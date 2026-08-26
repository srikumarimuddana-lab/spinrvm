# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (session, vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR) |
| Related issue or gap ID | Reverts commit `5883f7865` / PR #4324, merged to `main` despite being explicitly titled "🚫 DO NOT MERGE" |

## 1. Issue / gap identified

PR #4324 — a throwaway branch built solely to produce a live, internet-connected
Vercel preview of epic #2785 Phase 3 (typography + radius refresh) for visual
review — was merged into `main`. It hardcodes `useFeatureFlag()` to always
return `true`, unconditionally bypassing the live `app_settings.admin_theme_v2_enabled`
row. With that commit on `main`, every admin loading the dashboard now gets
Phase 3's visual change forced on, with no way to turn it off via the actual
feature flag — exactly the outcome the PR's own body said was unsafe
("Only safe because this is never meant to merge").

## 2. Root cause

The PR was merged (by a human, `merged_by` recorded on the PR) despite its
title and body explicitly saying not to. The mechanism (`useFeatureFlag()`
short-circuited to `true`) was intentionally unsafe outside of a preview
context — it was designed to be visually obvious and trivially revertible
specifically for this scenario. No code or process change is needed beyond
reverting; the PR body itself pre-authorized this exact remedy: "Real
implementation is commented inline in the diff for a one-line revert if this
is ever accidentally merged."

## 3. Fix / remediation

`git revert` of commit `5883f7865` (the only commit in #4324). Restores
`useFeatureFlag()` to read from `FeatureFlagsContext` (which is populated
from the real `GET /api/admin/settings` response) instead of the hardcoded
`true`. Also removes `docs/change-log/2026-08-21-phase3-preview-branch.md`,
which only documented the preview branch's own now-reverted existence.

No conflicts with the two commits merged after #4324 (#4327 — `records/page.tsx`,
#4325 — `globals.css`) since neither touches `useFeatureFlag.tsx`.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `useFeatureFlag.tsx`**, restoring it to
  exactly its pre-#4324 state (verified via diff — this is a straight
  revert, not a hand-edit).
- **Every consumer of `useFeatureFlag("admin_theme_v2_enabled")`** goes
  back to reading the real DB-backed flag (default `false`, per
  `DEFAULTS` in the same file) instead of the hardcoded override. The
  actual production `app_settings` row is untouched by either the
  original merge or this revert — this only changes which code path
  admin-dashboard reads at runtime.
- Between #4324 merging (2026-08-21T17:02:31Z) and this revert landing,
  any admin who loaded the dashboard against `main`'s deployed build saw
  Phase 3 forced on. This revert stops that; it does not retroactively
  undo anything already rendered client-side (nothing persisted server-side).

## 5. User-experience effect

- **Internal admin only.** Before this revert: every admin saw the
  Phase 3 visual refresh (16px radius, Plus Jakarta Sans) unconditionally,
  regardless of the `admin_theme_v2_enabled` setting. After this revert:
  admin-dashboard appearance returns to whatever the real flag says
  (currently off — Phase 3 stays dormant pending the intended staged
  rollout via the actual Settings → "Admin Dashboard Appearance (Beta)"
  toggle, per CLAUDE.md's feature-flag rollout convention).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/hooks/useFeatureFlag.tsx` | Reverted to read the real `FeatureFlagsContext` instead of hardcoded `true` | Undo the preview-only override that reached `main` |
| `docs/change-log/2026-08-21-phase3-preview-branch.md` | Removed | Documented only the now-reverted preview branch |

## 7. Before / after

```tsx
// Before (as merged to main via #4324)
export function useFeatureFlag(key: FlagKey): boolean {
  // PREVIEW-ONLY OVERRIDE — DO NOT MERGE. ...
  void key;
  return true;
}

// After (this revert)
export function useFeatureFlag(key: FlagKey): boolean {
  const flags = useContext(FeatureFlagsContext);
  return flags[key] ?? false;
}
```

## 8. Rollback plan

`git-revert-safe` — this commit is itself a revert of a single-file,
no-schema-change commit; reverting this revert would simply restore the
override (not recommended).

## 9. Verification performed

- [x] Real production build (`npm run build`) — succeeded.
- [x] `npx tsc --noEmit` — clean.
- [x] `npx vitest run` — 339/339 passed.
- [x] Diffed the reverted file against its state immediately before #4324 merged — byte-identical, confirming this is a clean revert with no drift from the two commits merged on top.
- [ ] Not verified against the live production `app_settings` row — that data was never touched by either the original merge or this revert, so nothing to re-check there; the flag's default (`false`) takes effect immediately since the hardcoded override is gone.

## 10. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated, not assumed — single file, verified byte-identical to pre-#4324 state.
- [x] This directly restores intended behavior after an accidental merge; the "no silent behavior change to a live-tested flow" gate is what this revert is enforcing, not violating.
