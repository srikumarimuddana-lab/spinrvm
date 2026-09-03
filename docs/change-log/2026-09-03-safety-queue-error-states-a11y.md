# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | Claude, at user request — "start with the safety incident queue fix", following item #1 of the prioritized findings from the 59-page admin-dashboard UI/UX + accessibility audit |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin (page is safety/SOS-adjacent — it's the triage tool for SOS/safety incidents, not the SOS flow itself) |
| PR / commit link | branch `claude/admin-portal-heatmaps-audit-gm8fbn` |
| Related issue or gap ID | Audit finding, batch 5 (safety/page.tsx): "load failures are silent; a stuck-loading incident drawer has no retry" (BLOCKER) + table-row keyboard-inaccessibility and unlabeled-form-field findings on the same page |

## 1. Issue / gap identified

On `admin-dashboard/src/app/dashboard/safety/page.tsx` (the safety-incident triage queue support staff use to work SOS/safety reports):

1. **Silent load failures.** Both the queue list fetch (`load()`) and the per-incident detail fetch (`openSelected()`) caught errors with only a toast — no persistent error state. A failed queue fetch rendered the same "No incidents match this filter" empty state as a genuinely empty queue, indistinguishable from each other once the toast disappeared. A failed detail fetch left the triage Sheet permanently stuck on a spinner ("Loading incident…") with no retry — the only way out was closing the Sheet.
2. **Keyboard-inaccessible queue rows.** Each `TableRow` was `onClick`-only with no `role`, `tabIndex`, or key handler — a keyboard-only user could not open an incident from the queue at all.
3. **Unlabeled form controls.** Every `Label` in `CreateIncidentDialog` and in the Triage card / merge field of `IncidentDetailDrawer` was a bare sibling of its `Input`/`Textarea`/`SelectTrigger`, with no `htmlFor`/`id` pairing — screen readers announce these as unlabeled controls.
4. **Redundant `aria-describedby={undefined}` on the Sheet.** Overrode Radix's automatic wiring to the existing `sr-only` `SheetDescription`, so the Sheet had no accessible description despite one already being rendered.
5. (Lower priority) Pickup/dropoff markers on the related-ride card were color-only dots with no text distinguishing which is which for a screen-reader user.

## 2. Root cause

The page was built incrementally (base queue view → detail Sheet → create dialog → merge feature) without an error-state pass; each `catch` block was written to satisfy "don't let it crash," not "let the operator recover." The keyboard/label gaps are the same systemic pattern flagged app-wide by the audit — `Label` used as a styled `<span>` rather than a real form label, and `TableRow` used as a de facto button without button semantics.

## 3. Fix / remediation

- Added `loadError: string | null` state, set on a failed `load()` (and cleared on success), and a dedicated error branch in the queue table area — distinct from the empty-queue state — with the failure message and a **Retry** button that re-runs `load`.
- Added `detailError: string | null` state, set on a failed `openSelected()` (and cleared on a new selection / successful load), and a third render branch in the Sheet (alongside the loading spinner and the populated drawer) showing the failure with a **Retry** button that re-runs `openSelected(selectedId)`.
- Queue `TableRow`: added `role="button"`, `tabIndex={0}`, an `aria-label` describing the incident, an `onKeyDown` handler firing on Enter/Space, and a `focus-visible` outline.
- Every `Label`/control pair in `CreateIncidentDialog` and the Triage card + merge field now has a matching `htmlFor`/`id`; the `role`/`severity` `SelectTrigger`s already had (or now have, via the `id`) an accessible name through the paired `Label`.
- Removed the `aria-describedby={undefined}` override on `SheetContent` so Radix wires it to the existing `sr-only` `SheetDescription` automatically.
- Pickup/dropoff dots: `aria-hidden="true"` on the color dot, `sr-only` "Pickup:"/"Dropoff:" text prefix added to the address text next to it.

## 4. Risk & impact on existing functionality

- **Blast radius**: single file, `admin-dashboard/src/app/dashboard/safety/page.tsx`. `load`, `openSelected`, `CreateIncidentDialog`, and `IncidentDetailDrawer` are all local to this page — grepped, no other importers of these functions/components exist elsewhere in the app.
- All existing success-path behavior is unchanged: filters, sort, pagination, create-incident, triage save, and merge all work exactly as before when a request succeeds. The new states only render on the error path (previously: broken/misleading UI) or add non-visual attributes (`role`, `tabIndex`, `aria-*`, `htmlFor`/`id`) that don't change layout or visible styling for a mouse/sighted user.
- The `TableRow` becoming `role="button"` with a nested `<button>` (the ride-ID link, which already calls `e.stopPropagation()`) is a pre-existing pattern (clickable-row-with-nested-link), not new; this change only adds keyboard operability to the row's own click behavior, it doesn't change the nested button's behavior.
- Nothing here touches ride state, dispatch, payments, or the actual SOS trigger flow — this is the admin triage/queue UI layered on top of already-created incidents.

## 5. User-experience effect

Admin/support-staff facing only (Safety queue page). Effective immediately for anyone with the `support` module. Behavior changes:
- A failed queue load now clearly says so and offers Retry, instead of silently looking like an empty (all-clear) queue.
- A failed incident-detail load now clearly says so and offers Retry, instead of spinning forever.
- The queue rows and all form fields on this page are now keyboard-operable / properly labeled for screen-reader users — previously unusable via keyboard alone.
No change to what a successful load/save/merge does.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/safety/page.tsx` | Added `loadError`/`detailError` state + retry UI; keyboard-accessible table rows; `htmlFor`/`id` label pairing across `CreateIncidentDialog` and Triage/merge fields; removed `aria-describedby` override; sr-only pickup/dropoff labels | Fix silent failures (SOS-adjacent surface) and close keyboard/screen-reader gaps found in the 59-page audit |

## 7. Before / after

```tsx
// Before — load(): failure only toasts, queue looks "empty" once the toast clears
} catch (e: any) {
    toast({ title: "Could not load safety queue", description: e?.message || "Unknown error", variant: "destructive" });
} finally {
    setLoading(false);
    setRefreshing(false);
}
...
) : items.length === 0 ? (
    <div>... "No incidents match this filter" ...</div>
) : (...)

// After — a failed load renders a distinct, retryable error state
} catch (e: any) {
    if (id !== reqIdRef.current) return;
    const message = e?.message || "Unknown error";
    setLoadError(message);
    toast({ title: "Could not load safety queue", description: message, variant: "destructive" });
}
...
) : loadError ? (
    <div role="alert">
        <p>Couldn't load the safety queue</p>
        <p>{loadError}</p>
        <Button onClick={load}>Retry</Button>
    </div>
) : items.length === 0 ? (...)
```

```tsx
// Before — queue row: mouse-only
<TableRow className="cursor-pointer hover:bg-muted/20" onClick={() => openSelected(it.id)}>

// After — keyboard-operable
<TableRow
    role="button"
    tabIndex={0}
    aria-label={`Open incident ${it.category}, reported ${relativeTime(it.reported_at)}`}
    onClick={() => openSelected(it.id)}
    onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openSelected(it.id); } }}
    className="cursor-pointer hover:bg-muted/20 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-ring"
>
```

## 8. Rollback plan

Plain `git revert` — no data, no migration, no feature flag. This is an error-handling and accessibility fix to an internal admin tool with no external contract; reverting restores the prior (worse) silent-failure/keyboard-gap behavior with no other side effects.

## 9. Verification performed

- [x] `tsc --noEmit` — clean, no new errors.
- [x] `eslint` on the changed file — 0 errors (pre-existing eslint 10.9.1/eslint-plugin-react workaround: linted with local unsaved `eslint@9.39.5`, then restored the pinned version). 3 pre-existing warnings remain, all at line numbers untouched by this diff (an unused `XCircle` import and two `react-hooks/set-state-in-effect` warnings on unrelated `useEffect`s already in the file before this change).
- [x] Real production build (`npm run build`) — exit code 0, confirmed via full-log grep for "error" with none found (not a truncated tail check).
- [x] Grepped for other importers/callers of `load`, `openSelected`, `CreateIncidentDialog`, `IncidentDetailDrawer` — all local to this file, no other consumers.
- [x] Manually traced both new error branches against the existing component tree: `loadError` is cleared on every successful `load()` and is mutually exclusive with the `items.length === 0` branch (checked before it); `detailError` is cleared on every new `openSelected()` call and on Sheet close, so re-opening a different incident after a failure doesn't carry over a stale error.

## What was NOT verified

- **No live browser/screen-reader check.** Same standing gap as the rest of this session's admin-dashboard work — no visual-regression tooling exists for admin-dashboard (baselines not yet seeded, per `ACTION_ITEMS.md` B38), and this sandbox has no way to exercise a running instance with a real screen reader (e.g. VoiceOver/NVDA). The `role="button"`/`tabIndex`/`onKeyDown` and `htmlFor`/`id` changes were reasoned about against Radix/ARIA conventions and the app's own existing patterns elsewhere (Drivers/Monitoring Sheets), not screenshotted or tested with assistive tech.
- **Did not reproduce an actual backend failure** to see the new error UI live (e.g. by pointing at a broken endpoint) — verified by code inspection of the catch/state-transition logic only, not by triggering a real failed `getSafetyIncidents`/`getSafetyIncident` call end-to-end.
- **Description/Reporter/Related ride/Location/Timeline section `Label`s** (used as styled section headers, not paired with a control) were left as-is — they're not form labels in the WCAG sense (no associated control to mislabel), so out of scope for this fix.
