# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | Claude, at user request — "check the settings page and monitoring for other bugs too" |
| Surface(s) | admin-dashboard, backend |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/admin-portal-heatmaps-audit-gm8fbn` |
| Related issue or gap ID | Follow-up to this session's earlier settings-page fixes (state-wipe, heat-map card no-op, 422 field-level detail) |

## 1. Issue / gap identified

A systematic audit of `/dashboard/settings` (beyond the bugs already fixed this session) found four further, verified issues:

1. **Silent initial-fetch failures.** `getSettings()`, `mfaStatus()`, `getHeatMapSettings()`, and `getAiCatalog()` all had empty (or effectively empty) `.catch()` handlers with no toast, banner, or retry affordance. A `getSettings()` failure collapsed the entire page to just the header (`{settings && (...)}` renders nothing); an `mfaStatus()` failure left `mfaEnabled` at its initial `false`, actively misreporting an account's two-factor status as off when it might genuinely be on.
2. **No save-in-progress lock.** Only the "Save Changes" button itself was disabled (`disabled={saving}`) while a save was in flight — every input/switch/select on the page stayed editable. An edit made during the PUT-then-refetch round trip was silently discarded when the refetch's `setSettings(await getSettings())` replaced state with the server's pre-edit snapshot, with no error and a "Saved!" confirmation still showing.
3. **`heat_map_default_range`'s Python-side fallback default (`"month"`) was never a valid option** in either UI that renders it (`today | 7d | 30d | 90d | 1y`) — on a fresh install (no `heatmap_settings` row saved yet), the Select showed nothing selected instead of reflecting the real default.
4. **Numeric inputs silently dropped an in-progress edit.** Nine numeric fields (AI daily message cap, max output tokens, MCP daily tool cap, simultaneous offers, offer timeout, search radius, min driver rating, heat map radius, heat map blur) called `parseInt`/`parseFloat` directly on the raw input value with no empty-string guard. Clearing a field mid-edit produces `NaN`, which `JSON.stringify` silently turns into `null`, which the backend's `model_dump(exclude_none=True)` then silently omits from the update — the save reports success but that one field's edit never took effect.

## 2. Root cause

1. Every fetch in the page's mount `useEffect` used `.catch(() => {})` (or, for `mfaStatus`, a catch that intentionally left state untouched) — a pattern this repo's own conventions explicitly forbid for DB/auth-adjacent paths ("Do not silently swallow errors" in CLAUDE.md), but this page's initial-load effect predates that convention being enforced consistently here.
2. The save flow was designed around the single "Save Changes" button, without considering that a save's own round trip (PUT + refetch) takes long enough for a second, concurrent edit to land in between — a race introduced by the earlier state-wipe fix's refetch-after-save pattern, not present before that fix existed.
3. `backend/routes/admin/settings.py`'s `_DEFAULT_HEATMAP_SETTINGS` dict (a Python-level fallback used only when no `heatmap_settings` row exists yet) was written independently of the frontend's actual enum and never cross-checked against it — the DB column's own SQL `DEFAULT` (`'30d'`, migration `03_corporate_accounts_heatmap.sql`) was already correct; only this in-Python dict drifted.
4. The one field that already had the correct empty-string guard (`corporate_wallet_admin_adjust_daily_cap`) established the right pattern, but it was never applied to the other nine numeric inputs added before and after it.

## 3. Fix / remediation

1. Every initial fetch now surfaces a destructive-variant toast on failure. `getSettings()`'s failure additionally sets a new `settingsLoadError` state, rendering a retryable error card (`Couldn't load settings` + a Retry button calling a new `retrySettings()`) instead of a bare header. `mfaStatus()`'s catch deliberately still does not touch `mfaEnabled`/`mfaAvailable`/`mfaEnforced` — it only adds a toast warning the Security tab may be showing stale status, so a transient fetch failure can no longer misreport actual account security state.
2. The entire settings form (everything inside the `<Tabs>`) is now wrapped in `<fieldset disabled={saving}>`, which natively disables every descendant native `<button>`/`<input>`/`<select>` (confirmed Radix `Switch`/`Select` render as native `<button>` under the hood) for the duration of a save — eliminating the concurrent-edit race entirely rather than trying to merge/preserve in-flight edits.
3. `_DEFAULT_HEATMAP_SETTINGS["heat_map_default_range"]` changed from `"month"` to `"30d"`, matching both the frontend's enum and the DB column's own SQL default.
4. All nine numeric `onChange` handlers now use the same `e.target.value === "" ? null : parseInt/parseFloat(e.target.value)` guard already correct on `corporate_wallet_admin_adjust_daily_cap` — an intentionally-cleared field now saves as `null` (a real, explicit "unset" the backend accepts for every affected Optional field) instead of being silently dropped from the update.

## 4. Risk & impact on existing functionality

- **Blast radius**: isolated to `admin-dashboard/src/app/dashboard/settings/page.tsx` (three of the four fixes) and one Python dict literal in `backend/routes/admin/settings.py` (the fourth). No shared component, no other page, no API contract changed.
- The `<fieldset disabled={saving}>` wrapper also disables the Heat Map Configuration card's own inputs/save button while the *main* settings save is in flight (that card has its own independent `heatMapSaving` state from an earlier fix in this session) — a deliberately conservative choice: locking the whole form during any save is simpler and safer to reason about than trying to prove which specific races are and aren't possible between the two independent save flows.
- The `getRows_batched_in`-style empty-string-to-`null` change for the nine numeric fields means clearing a field and saving now explicitly unsets that setting (falls back to its documented default) rather than being a no-op. This is arguably *more* correct (the operator's clear action now does something) but is a behavior change from "silently ignored" to "explicitly unset" — called out here since it's the one part of this batch that changes what a save actually does, not just what it reports.
- `_DEFAULT_HEATMAP_SETTINGS`'s default only affects a fresh install with no `heatmap_settings` row yet — any environment that has already saved heat map settings (which stores a real row) is unaffected.

## 5. User-experience effect

Admin-facing only. (1) A failed initial load now shows a clear, retryable error instead of a page that looks empty/broken with no explanation; a failed MFA-status refresh no longer risks showing "MFA not enabled" for an account that has it on. (2) Every field on the page is now visibly disabled (standard grayed-out form-lock affordance) for the ~1-2s a save is in flight, instead of silently discarding an edit made during that window. (3) The heat map's time-range dropdown shows a real selected value on a fresh install instead of appearing blank. (4) Clearing a numeric field and saving now genuinely resets that setting instead of quietly doing nothing.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/settings/page.tsx` | Toast + retry-card on initial-fetch failures; `<fieldset disabled={saving}>` around the whole form; empty-string guard on 9 numeric `onChange` handlers | Fix silent failures, the save-race, and the NaN-drops-edit bug found in audit |
| `backend/routes/admin/settings.py` | `_DEFAULT_HEATMAP_SETTINGS["heat_map_default_range"]`: `"month"` → `"30d"` | Match the frontend's actual enum and the DB column's own default |

## 7. Before / after

```tsx
// Before — every fetch failure is silently swallowed
getSettings().then(setSettings).catch(() => { }).finally(() => setLoading(false));

// After — surfaced, with a retry affordance for the page-collapsing one
const fetchSettingsData = () => {
    getSettings()
        .then((d) => { setSettings(d); setSettingsLoadError(false); })
        .catch(() => {
            setSettingsLoadError(true);
            toast({ title: "Couldn't load settings", description: "...", variant: "destructive" });
        })
        .finally(() => setLoading(false));
};
```

```tsx
// Before — only the Save button is locked; every field stays editable mid-save
<Button onClick={handleSave} disabled={saving}>...</Button>
{settings && <Tabs>...</Tabs>}

// After — the whole form locks for the duration of a save
{settings && (
    <fieldset disabled={saving} className="min-w-0 border-0 p-0 m-0">
        <Tabs>...</Tabs>
    </fieldset>
)}
```

```tsx
// Before — clearing the field sends NaN -> null -> silently dropped by the backend
onChange={(e) => update("max_simultaneous_offers", parseInt(e.target.value))}

// After — clearing the field explicitly unsets it
onChange={(e) => update("max_simultaneous_offers", e.target.value === "" ? null : parseInt(e.target.value))}
```

## 8. Rollback plan

Plain `git revert` on either commit — no data, no migration, no schema/API contract change either way.

## 9. Verification performed

- [x] Dispatched a dedicated read-only audit of the whole settings page + backend model against the four already-fixed bugs (explicitly excluded from re-reporting), then independently re-verified each of its findings by reading the exact cited lines myself before acting on any of them.
- [x] Confirmed the MFA-disable-form/backend mismatch and the `/settings/heatmap` module-gate mismatch (two further findings from that audit) are real but are auth/access-control decisions, not mechanical bugs — flagged to the user rather than fixed unilaterally.
- [x] `tsc --noEmit` — clean.
- [x] `eslint` on the changed file — 0 errors; 1 new warning (`react-hooks/exhaustive-deps` on the mount effect, an accepted/common pattern for a deliberately mount-only effect) plus the same 4 pre-existing entity-escaping warnings on untouched lines.
- [x] Real production build (`npm run build`) — exit code 0, confirmed via full-log grep for "error".
- [x] Ran the existing `/dashboard/settings` smoke test — passes.
- [x] `python3 -c "import ast; ast.parse(...)"` on the backend file — syntax valid.
- [x] Confirmed via source reading that Radix `Switch`'s underlying primitive renders a native `<button>` (`Primitive.button`), so `fieldset[disabled]`'s native HTML behavior correctly disables it (and Select/Button, both also button-based) — not assumed.

## What was NOT verified

- **No live browser reproduction of the fieldset-disable UX** — reasoned about from Radix's own primitive source and standard HTML `fieldset` semantics, not screenshotted (no visual-regression tooling exists for admin-dashboard).
- **Whether any other numeric-input pattern in the wider admin-dashboard shares this same NaN-drop bug** — this fix is scoped to the nine fields found on the settings page specifically, not a codebase-wide sweep.
- **The MFA disable-form UX/backend mismatch and the heatmap-page module-gate mismatch** (found in the same audit) are real but were **not fixed** in this batch — see the accompanying chat message for why these were flagged for a decision instead.
