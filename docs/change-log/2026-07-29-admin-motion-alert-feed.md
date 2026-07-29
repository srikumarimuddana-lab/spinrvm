# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (this PR) |
| Related issue or gap ID | #2785 (admin-dashboard visual-refresh epic), Phase 4 |

## 1. Issue / gap identified

Epic #2785 Phase 4 calls for a "purposeful motion layer ... on high-signal screens only (dispatch/surge monitoring, heatmap, SOS)," gated on `prefers-reduced-motion`. Today `admin-dashboard` has no JS motion library and no animated state transitions anywhere — new items in the live monitoring event feed (`AlertFeed`) simply pop into the list with no visual cue that something just happened.

## 2. Root cause

Not a bug — new capability. `AlertFeed` (`admin-dashboard/src/app/dashboard/monitoring/alert-feed.tsx`) renders a live WebSocket-driven event feed (driver online/offline, ride created/completed/cancelled) but had zero motion affordance for new arrivals.

## 3. Fix / remediation

- Added the `motion` npm package (`motion/react` subpath — the maintained successor API surface to `framer-motion`).
- Wrapped the event list in `AnimatePresence` and each row in `motion.button`, animating new rows in with a short opacity/translate-y transition (`{ opacity: 0, y: -6 } → { opacity: 1, y: 0 }`, 150ms).
- Gated via `motion/react`'s built-in `useReducedMotion()`: under `prefers-reduced-motion: reduce`, `initial` is set to `false`, meaning entries render at their final state immediately with no animation — not just a shorter/subtler one.
- Scoped narrowly to this one component rather than all three screens named in the epic (monitoring/heatmap/safety) — see "Not changing but considered" below for why.

### Why only `AlertFeed`, not heatmap or the safety queue too

- `AlertFeed` is a small (105-line), self-contained, already-list-rendering component — a natural, low-risk fit for an entrance animation with a real signal purpose ("something just happened" in a live dispatch feed).
- The safety-queue page (`dashboard/safety/page.tsx`) deliberately does *not* optimistically prepend new incidents on the `safety_incident_opened` WS event — it does a full `load()` re-fetch instead, with an existing code comment explaining why (avoiding a bare row before the backend enriches with `reporter_name`). Adding a meaningful "new incident" animation there would mean changing that data flow, which is a larger, separate change on a safety-critical surface — out of scope for a motion-only pass.
- Heatmap (`dashboard/heatmap/page.tsx`) is a map-based visualization without an obvious analogous "an item just changed" list-entry point; a good motion treatment there would need its own design pass, not a mechanical port of this pattern.
- Consistent with this epic's own precedent (Phase 2's status/badge work): scoped to what's concretely verified and low-risk today, with the rest flagged as a follow-up rather than forced into one PR.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** `alert-feed.tsx` has exactly one consumer — grepped for `AlertFeed` usage and confirmed it's only imported by `dashboard/monitoring/page.tsx`.
- No prop, callback, or data-shape change: `AlertFeed`'s props (`events`, `onClear`, `onEventClick`) and behavior (toggle open/closed, unread count, clear-all) are untouched — only the *presentation* of the list changed.
- `motion` is a new dependency (`^12.43.0`, exact version resolved as `12.43.0`) but adds zero new `npm audit` findings — verified by running `npm audit --audit-level=high` before and after: the same 11 pre-existing vulnerabilities (2 moderate, 9 high), all in the `eslint-plugin-jsx-a11y`/`eslint-plugin-react`/`minimatch`/`brace-expansion` chain already tracked in #2376, appear either way; `motion` itself doesn't show up in any finding's dependency path.
- `AnimatePresence`'s exit-animation behavior only applies when items are *removed* from the array; `AlertFeed` never removes individual items (only "Clear all," which unmounts the whole list, not an animated removal) — so there's no exit-animation edge case to reason about here.

## 5. User-experience effect

- Internal-admin facing only (the live monitoring dashboard's event feed). New events sliding/fading in is a visible, but small and additive, change — no existing interaction (opening/closing the feed, clicking an event, clearing) changes.
- Respects `prefers-reduced-motion` at the OS/browser level automatically via `motion/react`'s `useReducedMotion()` — no separate app-level setting needed for this.
- Not gated behind the `admin_theme_v2_enabled` flag from Phase 3: this is page-local (one component, one page) rather than a shared-shell change touching all 34 routes, so the blast-radius rationale that justified flagging Phase 3 doesn't apply here in the same way. If this becomes the pattern used more broadly across other screens, revisit whether a `motion_enabled`-style flag becomes worthwhile.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/package.json` | Added `"motion": "^12.43.0"` dependency | Provides `AnimatePresence`/`motion.*`/`useReducedMotion` |
| `admin-dashboard/package-lock.json` | Lockfile update for the new dependency tree | npm-managed, no manual edits |
| `admin-dashboard/src/app/dashboard/monitoring/alert-feed.tsx` | Wrapped the event-row list in `AnimatePresence`/`motion.button` with a reduced-motion-aware entrance transition | The actual Phase 4 motion addition |

## 7. Before / after

```
# Before
<div className="flex flex-col-reverse gap-0.5">
    {events.map((evt) => (
        <button key={evt.id} onClick={() => onEventClick(evt)} className="...">
            ...
        </button>
    ))}
</div>
```

```
# After
<div className="flex flex-col-reverse gap-0.5">
    <AnimatePresence initial={false}>
        {events.map((evt) => (
            <motion.button
                key={evt.id}
                onClick={() => onEventClick(evt)}
                className="..."
                initial={shouldReduceMotion ? false : { opacity: 0, y: -6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.15 }}
            >
                ...
            </motion.button>
        ))}
    </AnimatePresence>
</div>
```

## 8. Rollback plan

- `git revert` is fully safe here — no data, migration, or config touched, and the dependency addition is additive (no other file imports `motion` yet).
- If the `motion` dependency itself needs removing: revert this PR, then `npm uninstall motion` (removes the now-unused package cleanly, since nothing else in the repo depends on it yet).

## 9. Verification performed

- [x] `npm run build` — clean, all 34 routes (including `/dashboard/monitoring`) compile with no type/import errors.
- [x] `npm run lint` — 0 errors; confirmed the changed file (`alert-feed.tsx`) introduces zero new warnings by grepping the lint output for it directly. Note: the *total* problem count on this branch is 319, not the 183 seen in earlier phases this session — verified this is pre-existing drift on `main` (other unrelated PRs merged in the interim added new lint warnings elsewhere), **not** something this PR's diff caused, by running lint against the unmodified `main` tip (stashing all Phase 4 changes) and getting the identical 319 count.
- [x] `npm audit --audit-level=high` run before and after adding `motion`: identical 11 pre-existing findings (2 moderate, 9 high), all pre-dating this PR and tracked in #2376; `motion` itself introduces none.
- [x] Blast-radius grep: `AlertFeed` has exactly one import site (`dashboard/monitoring/page.tsx`).

## What was NOT verified

- Not tested against a live WebSocket feed with real driver/ride events in a logged-in admin session — this sandbox has no real Supabase/admin credentials to reach the authenticated monitoring page end-to-end. Verified via `npm run build`'s type-checking/SSR pre-render (which does catch import/JSX errors) rather than a live browser render of the actual animation.
- No screenshot or video of the animation in motion (no visual-regression tooling exists for this yet, tracked separately as #2809) — reasoned from the `motion/react` API being a well-documented, widely-used library rather than hand-rolled animation logic.
- Did not extend this treatment to the heatmap or safety-queue screens named in the epic's Phase 4 description — see section 3 for the explicit reasoning on why those are deferred rather than silently dropped.
