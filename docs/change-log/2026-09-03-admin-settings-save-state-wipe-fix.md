# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | Claude, at user request — "the changes under the settings screen in the admin portal is not working throws an error do have a look identify the root cause and resolve the issue" |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/admin-portal-heatmaps-audit-gm8fbn` |
| Related issue or gap ID | None filed — found and fixed in conversation |

## 1. Issue / gap identified

On `admin-dashboard/src/app/dashboard/settings/page.tsx` (`/dashboard/settings`), saving any setting change made every field on the page appear to reset/go blank immediately afterward — text inputs empty, toggles falling back to their default states — even though the save itself had actually succeeded and nothing was lost in the database. A page refresh restored the correct values, which made the bug look intermittent/random rather than reliably reproducible on every save.

## 2. Root cause

`handleSave()` called `const updated = await updateSettings(settings); setSettings(updated);`. `PUT /api/admin/settings` (`backend/routes/admin/settings.py`) is an upsert + audit-log endpoint — it has only ever returned `{"message": "Settings updated", "audit_log_id": <uuid>}`, never the full settings row (confirmed by reading the route handler's `return` statement, and independently confirmed by the frontend's own `updateSettings()` type signature in `lib/api/settings-ai.ts`, which already correctly types the response as `{ message: string; audit_log_id?: string }`). Assigning that 2-key response directly to the `settings` state overwrote every other field (`stripe_publishable_key`, `company_name`, every toggle, etc.) with `undefined`, which the JSX's `settings.<field> || ""` / `!!settings.<field>` fallbacks then rendered as empty/off.

This bug has existed since the file was first created (git history traced back to the initial commit `69ba0510` from Feb 2026) — it predates this session entirely and was not introduced by any of today's Label/htmlFor or PageHeader changes to this same file.

## 3. Fix / remediation

After a successful `updateSettings()` call, re-fetch the real settings via `getSettings()` (the same call the page's initial `useEffect` already uses) and use *that* to update state, instead of the PUT response. The re-fetch has its own nested `try`/`catch`, deliberately separate from the outer save `try`/`catch`: if the save itself fails, the existing "Settings not saved" error path still fires correctly; if the save succeeds but the refetch alone hits a network hiccup, the operator is not falsely told the save failed — the save already went through by that point, only the on-screen refresh is delayed until the next manual reload.

## 4. Risk & impact on existing functionality

- **Blast radius**: isolated to `handleSave` in this one file. `getSettings()` and `updateSettings()` are both pre-existing, unmodified API functions (`admin-dashboard/src/lib/api/settings-ai.ts`) — grepped for other callers of `handleSave`; it's a local closure, not exported or reused anywhere else.
- No backend change — the fix is entirely a frontend state-management correction. The backend's response shape is unchanged and was never wrong; the frontend was misusing it.
- The extra `getSettings()` call after every save is one additional GET request, same endpoint the page already calls once on mount — negligible load, no new backend surface.
- Every other piece of `handleSave`'s existing behavior (the `saved`/`saving` UI state, the success toast with the audit ref, the destructive error toast on a real failure) is preserved unchanged.

## 5. User-experience effect

Admin-facing only (`/dashboard/settings`). After this fix, saving a setting correctly leaves every field showing its actual persisted value (including server-side credential masking, exactly as on page load) instead of appearing to wipe the whole form. This closes a bug that made every save look destructive, even though no data was ever actually lost — visible mid-session to any admin using this exact screen.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/settings/page.tsx` | `handleSave()`: replaced `setSettings(updated)` (the PUT response) with a separate `getSettings()` re-fetch in its own try/catch | Root-cause fix for the settings-page state wipe on every save |

## 7. Before / after

```tsx
// Before — settings state collapsed to {message, audit_log_id} on every save
const updated = await updateSettings(settings);
setSettings(updated);
setSaved(true);
...

// After — re-fetch the real settings; a refetch failure doesn't mask a
// successful save as a failed one
const updated = await updateSettings(settings);
setSaved(true);
setTimeout(() => setSaved(false), 2000);
if (updated?.audit_log_id) {
    toast({ title: "Settings saved", description: `Ref: ${updated.audit_log_id}` });
}
try {
    setSettings(await getSettings());
} catch {
    // save succeeded; only the refresh failed
}
```

## 8. Rollback plan

Plain `git revert` — no data, no migration, no feature flag. This is a pure frontend bugfix with no schema or API contract change; reverting restores the prior (broken) behavior with no other side effects.

## 9. Verification performed

- [x] Read the actual backend route handler (`backend/routes/admin/settings.py`'s `admin_update_settings`) to confirm its literal return value, rather than assuming from the frontend alone.
- [x] Cross-checked against the frontend's own `updateSettings()` TypeScript return type (`{ message: string; audit_log_id?: string }`) in `lib/api/settings-ai.ts` — already correctly typed, confirming the bug was in how the *page* used that return value, not a type mismatch that TypeScript could have caught (the page's `settings` state is typed `any`, so this specific misuse was invisible to `tsc`).
- [x] Traced the bug's git history (`git log -S`) to confirm it predates this session and was not introduced by the tier-1/2/3 work already merged today.
- [x] `tsc --noEmit` — clean, no new errors.
- [x] `eslint` on the changed file — 0 errors (pre-existing eslint 10.9.1/eslint-plugin-react workaround: linted with a local unsaved `eslint@9.39.5`, then restored the pinned version). Same 4 pre-existing warnings as before this change, all on unrelated lines (1002, 1393, 1394) far from the edited `handleSave`.
- [x] Real production build (`npm run build`) — exit code 0, confirmed via full-log grep for "error".
- [x] Grepped for other callers/consumers of `handleSave`, `getSettings`, and `updateSettings` — confirmed `handleSave` is a local closure with no other consumers, and neither API function was modified.

## What was NOT verified

- **No live browser reproduction.** This sandbox cannot run the admin-dashboard app against a real backend/Supabase instance, so the bug was diagnosed and the fix verified entirely through static code reading (backend route source, frontend type signatures, git history) rather than by reproducing the blank-fields symptom live and then confirming it's gone. The reasoning chain (PUT response shape → state overwrite → `|| ""`/`!!` fallbacks rendering blank) is direct and mechanical, not inferred from a stack trace, so confidence is high, but this is a real gap to flag per the standing no-visual-regression-tooling caveat for admin-dashboard.
- **Whether the user's "throws an error" was a literal JS exception or the alarming appearance of the settings screen resetting was not separately confirmed** — no browser console output or error message was available to check against. The fix addresses the concrete, verified defect (state overwritten with a 2-key response) regardless of which framing is most accurate; if a distinct hard crash is still reproducible after this fix, that would point to a second, different bug.
