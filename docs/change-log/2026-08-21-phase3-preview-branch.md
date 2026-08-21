# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (session, vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR — **draft, not intended to merge**) |
| Related issue or gap ID | Admin Portal UX Audit §01/§08 — epic #2785 Phase 3 decision |

## 1. Issue / gap identified

Epic #2785 Phase 3 (Plus Jakarta Sans typography + 16px border-radius) is
fully built behind `admin_theme_v2_enabled`, which defaults off and has
never been promoted. The user asked to see it rendered for real before
deciding whether to turn it on — this sandbox has no internet access to
Google Fonts, so the font half of Phase 3 can't be verified here (confirmed
via `getComputedStyle` — identical `font-family` on/off in this
environment).

## 2. Root cause

N/A — this isn't a bug fix, it's a throwaway vehicle to get a real,
internet-connected Vercel preview build where Plus Jakarta Sans can
actually load.

## 3. Fix / remediation

`useFeatureFlag()` hardcoded to always return `true`, bypassing the real
`GET /api/admin/settings` fetch and its live `admin_theme_v2_enabled`
value entirely. This is deliberate and **only safe because this PR is
never meant to merge**:

- It does not read or write the live `app_settings` row — the flag stays
  off in production regardless of this branch existing.
- Vercel's preview deployment for this branch will render every session
  with Phase 3 on, letting the user click through the real typography/
  radius change with a real internet connection (unlike this sandbox).

## 4. Risk & impact on existing functionality

- **Blast radius: this branch only, and only if merged (which it must
  not be).** If accidentally merged to `main`, every admin session in
  every environment would get Phase 3 forced on, silently bypassing the
  `admin_theme_v2_enabled` flag and the staged-rollout mechanism epic
  #2785 built specifically to avoid that. This is the one real risk of
  this branch existing — mitigated by: draft PR, title/body says DO NOT
  MERGE, this change-log entry, and no other branch depends on it.
- No production data touched. No `app_settings` row read or written by
  this change (the fetch that would read it is simply never reached).

## 5. User-experience effect

- **None in production** — this code never reaches `main`.
- On this branch's own Vercel preview URL only: every admin session sees
  Phase 3 (Plus Jakarta Sans + 16px radius) regardless of their real
  flag setting — expected and intended for this preview.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/hooks/useFeatureFlag.tsx` | `useFeatureFlag()` hardcoded to return `true`; real implementation commented inline for easy revert | Force Phase 3 on for this preview build only |

## 7. Before / after

```
# Before
export function useFeatureFlag(key: FlagKey): boolean {
  const flags = useContext(FeatureFlagsContext);
  return flags[key] ?? false;
}

# After (this branch only)
export function useFeatureFlag(key: FlagKey): boolean {
  void key;
  return true;
}
```

## 8. Rollback plan

**Do not merge this branch.** If it's ever accidentally merged, `git
revert` restores the real flag-reading implementation immediately — no
data was touched, so a code revert is a complete rollback here (unlike
money/ride-state changes).

## 9. Verification performed

- [x] Real production build (`npm run build`) — succeeded.
- [x] Confirmed the override actually changes rendering: `getComputedStyle`
      showed `--radius` flip from `.625rem` to `1rem` and the `.theme-v2`
      class present, using this exact override, before pushing.
- [ ] Font rendering not verifiable in this sandbox (no internet access to
      Google Fonts) — the entire reason this preview branch exists is to
      let the user verify it themselves on the real Vercel preview URL.

## 10. Sign-off

- [x] Rollback plan is concrete: don't merge; revert if it happens anyway.
- [x] Blast radius stated: none if the "don't merge" instruction holds.
- [x] Explicitly marked DO NOT MERGE in the PR title, body, and this log.
