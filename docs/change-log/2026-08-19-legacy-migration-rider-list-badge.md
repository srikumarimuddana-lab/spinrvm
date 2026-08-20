# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Claude (agent session), on behalf of vikas@ngitservices.com |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (local worktree, not pushed) — see `git log` in session report |
| Related issue or gap ID | `docs/change-log/2026-08-19-legacy-migration-transparency-admin-dashboard.md` ("Rider list-view badge: blocked, not implemented") and `docs/change-log/2026-08-19-legacy-migration-transparency-backend.md` (the backend fix that unblocked it) |

## 1. Issue / gap identified

The admin Users page's main rider *list* table (the row-per-rider table, not the
detail dialog) had no way for an admin to tell a legacy-imported rider profile
apart from a normally-created one, even though the same "Imported" badge already
exists on the driver list, the driver detail header, and the rider *detail dialog*
in this same file.

## 2. Root cause

This was previously blocked by a real backend gap (`_USER_LIST_COLUMNS` in
`backend/routes/admin/users.py` didn't project `legacy_import_metadata`),
documented in the prior session's change-log as "blocked, not implemented." That
backend gap was closed separately (see the backend change-log entry above) by
adding `legacy_import_metadata` to `_USER_LIST_COLUMNS`.

With the backend now returning the field, a second, previously-invisible gap
surfaced on the frontend: `fetchUsers()` in `users/page.tsx` does not spread the
raw API row into `users` state — it builds a brand-new object literal per row,
field by field (`{ id: u.id, name: ..., first_name: u.first_name, ... }`), and
that literal never listed `legacy_import_metadata`. So simply fixing the backend
would **not** have been enough on its own: the new field would have reached the
frontend's fetch response and then been silently dropped before it ever reached
`users` state or the table row. This is exactly the "confirm end-to-end, don't
assume" case CLAUDE.md's query-filter guidance warns about in spirit — a value
present in the API response is not the same as a value present in the rendered
row.

## 3. Fix / remediation

1. Added `legacy_import_metadata: u.legacy_import_metadata ?? null` to the
   per-row transform in `fetchUsers()` (`admin-dashboard/src/app/dashboard/users/page.tsx`),
   so the field survives from the API response into `users` state.
2. Added the same "Imported" badge (identical visual pattern already used on the
   driver list row, the driver detail header, and this file's own rider detail
   dialog: `bg-muted`, `text-[10px]`, `font-medium`, `rounded px-1.5 py-0.5`) next
   to the rider's name in the main table's Name cell, gated on
   `user.legacy_import_metadata && Object.keys(user.legacy_import_metadata).length > 0`
   — same truthiness check already used everywhere else in this codebase for this
   field (`{}` = not imported, non-empty object = imported).

No new visual style was invented; both existing patterns (rider detail dialog
badge, driver list badge) use the same classes, so there was no choice to make
between them.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one file, two edit sites, both purely additive.**
  - Edit site 1 (`fetchUsers()` per-row transform): adds one new key to an
    object literal that is otherwise unchanged. Grepped every consumer of the
    `users` state array in this file (`grep -n "users\."` and `grep -n "setUsers"`):
    three `.filter()` calls for the summary stat cards (rider count, driver
    count, both-count — none reference the new key, unaffected), the
    `useTableSort(users)` call (sorts by existing sortable columns only — `name`,
    `email`, `role`, `status`, `created_at` — the new field is not sortable and
    was not made sortable), and four `setUsers(prev => prev.map(...))` optimistic-update
    call sites (status change, role-flag toggles) that all spread `{...u, ...patch}`
    — spreading preserves whatever key set `u` already has, so none of them needed
    to change and none of them will drop the new field on a subsequent optimistic
    update.
  - Edit site 2 (table row JSX): a new `<span>` badge inside the existing Name
    `<TableCell>`, wrapped in the same conditional pattern already used three other
    places in this exact file. No existing element removed or restructured beyond
    wrapping the name text in a flex row for alignment (`<p className="font-medium">`
    → `<p className="font-medium flex items-center gap-1.5">`), which does not
    change the name text itself, only adds a flex container around it.
- The users table row is inline JSX inside `UsersPage`'s single default export,
  not a separately exported/imported component — grepped for other consumers
  of this file's default export (`grep -rn "from.*dashboard/users/page"` across
  `admin-dashboard/src`): none found; Next.js App Router pages are route-mounted,
  not imported elsewhere.
- `admin-dashboard/src/lib/api/users-wallet.ts`'s `getUsersPaginated` return type
  is `Promise<any[]>` (untyped) — no TS interface exists to update; nothing was
  silently narrowed away at the type level. The actual drop this session found
  and fixed was a **runtime** one (the object-literal transform), not a
  compile-time one — confirmed by reading `fetchUsers()` end-to-end rather than
  assuming a typed interface existed to check.
- `exportUsers()` / CSV export (`handleExport`) calls a separate backend export
  endpoint (`GET .../export`) and builds its CSV columns from that response, not
  from `users` state — confirmed by reading `handleExport()`; unaffected by this
  change, and out of scope (CSV export was not in this session's ask).
- No table, endpoint, WebSocket event, or background loop was touched. No new
  network request was added — this reads a field the list endpoint already
  returns as of the prior backend fix.

## 5. User-experience effect

- **Internal-admin-facing only.** No rider, driver, or corporate-admin-facing
  surface changed; `rider-app/`, `driver-app/`, and the backend were not touched
  in this session.
- Visible only inside the admin dashboard's Users page main table — not mid-session
  to any rider using the consumer app, since the rider app doesn't render this UI.
- Purely additive: a small "Imported" label now appears next to a legacy-imported
  rider's name in the list. No existing column, action, click behavior, or sort
  behavior changed for any row.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/users/page.tsx` | Added `legacy_import_metadata` to `fetchUsers()`'s per-row transform; added the "Imported" badge next to the rider name in the main table's Name cell | Close the previously-blocked rider list-view badge gap, now that the backend list endpoint projects the field |

## 7. Before / after

**`fetchUsers()` per-row transform:**
```tsx
// Before
status: u.status || "active",
status_reason: u.status_reason ?? null,
suspended_until: u.suspended_until ?? null,
}));

// After
status: u.status || "active",
status_reason: u.status_reason ?? null,
suspended_until: u.suspended_until ?? null,
legacy_import_metadata: u.legacy_import_metadata ?? null,
}));
```

**Main table Name cell:**
```tsx
// Before
<TableCell>
    <div>
        <p className="font-medium">{user.name}</p>
        <p className="text-xs text-muted-foreground font-mono">{user.id?.slice(0, 8)}</p>
    </div>
</TableCell>

// After
<TableCell>
    <div>
        <p className="font-medium flex items-center gap-1.5">
            {user.name}
            {user.legacy_import_metadata && Object.keys(user.legacy_import_metadata).length > 0 && (
                <span className="inline-block text-[10px] font-medium text-muted-foreground bg-muted rounded px-1.5 py-0.5 shrink-0">
                    Imported
                </span>
            )}
        </p>
        <p className="text-xs text-muted-foreground font-mono">{user.id?.slice(0, 8)}</p>
    </div>
</TableCell>
```

## 8. Rollback plan

No feature flag exists for this admin-dashboard rendering surface (same standing
gap noted in the prior session's change-log — the `app_settings` DB-flag pattern
does not cover admin-dashboard rendering toggles today). This change is:

- Purely additive/rendering-only — no writes, no migrations, no Stripe/payment
  calls, no new network request.
- Reachable only by reading a field already present in an already-fetched API
  response (the list endpoint has projected `legacy_import_metadata` since the
  prior backend fix).

Rollback is a plain `git revert` of this session's commit followed by a normal
Vercel redeploy — acceptable because **no live data is written or mutated** by
this change (unlike a Stripe charge, wallet delta, or ride state change, where
CLAUDE.md correctly requires more than a revert).

## 9. Verification performed

- [x] `tsc --noEmit -p tsconfig.json` run on the full admin-dashboard project —
      clean, 0 errors.
- [x] `eslint` run on the touched file (`src/app/dashboard/users/page.tsx`) —
      0 errors; 3 pre-existing `react-hooks/set-state-in-effect` warnings at
      lines 89, 202, 224, none on or near either line this session touched
      (the transform edit is ~line 178, the badge edit is ~line 505).
- [x] `npm run build` (real production build, not just dev server or
      `tsc --noEmit`) run once after the change — **exit 0**, all routes
      including `/dashboard/users` built successfully.
- [x] Blast-radius grep performed: every `users.`/`setUsers` consumer in
      `admin-dashboard/src/app/dashboard/users/page.tsx` (three `.filter()` stat
      cards, `useTableSort(users)`, four optimistic-update `setUsers` call sites);
      grepped for other importers of this page's default export (none — Next.js
      App Router route file); read `handleExport()` to confirm CSV export uses a
      separate backend call, not `users` state.
- [x] Read `backend/routes/admin/users.py`'s `_USER_LIST_COLUMNS` (read-only, no
      backend files modified) to confirm the list endpoint genuinely returns
      `legacy_import_metadata` end-to-end before writing any frontend code for it,
      rather than assuming the prior backend change-log entry was accurate.
- [x] Read `admin-dashboard/src/lib/api/users-wallet.ts` to confirm
      `getUsersPaginated`/`getUserDetails` are untyped (`any`/`any[]`) — there is
      no TS interface for a table row in this file or its API client to update;
      the actual field-drop this session found and fixed was in the runtime
      per-field object-literal transform inside `fetchUsers()`, not a compile-time
      type omission.
- [ ] No manual click-through in a running browser was performed (no dev server
      was launched against a live/staging backend in this session).
- [ ] No automated visual/snapshot regression tooling exists in this repo for
      admin-dashboard (standing gap, tracked in `ACTION_ITEMS.md`, not
      re-litigated here). The badge placement and flex-wrap alignment were
      reasoned about from the JSX/CSS classes and the three other existing badge
      instances in this same file/its sibling, not visually confirmed.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert; no live-data writes
      to undo).
- [x] Blast radius is stated, not assumed (see §4 — grepped every consumer of
      the touched state array and the touched JSX).
- [x] No silent behavior change to an already-shipped flow: this only *adds* a
      badge for a state (`legacy_import_metadata` non-empty) that previously had
      no visual representation in the list view at all — no existing
      correctly-rendering row changes its output for a rider whose
      `legacy_import_metadata` is empty/absent.
