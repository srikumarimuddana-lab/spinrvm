# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Claude (agent session), on behalf of vikas@ngitservices.com |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (local worktree, not pushed) `57d0e4a`, `c829584`, `58f996c`, `862c9d8` |
| Related issue or gap ID | `docs/audit/2026-08-19-legacy-migration-data-quality-audit.md` — "Not fixed — admin-dashboard display" section |

## 1. Issue / gap identified

Four presentation-layer gaps left an admin unable to tell "known migration gap" from
"broken/incomplete data" when looking at a legacy-imported driver or rider:

1. No admin screen marked a driver or rider *profile* record as legacy-imported — only
   ride rows had the existing A30 "Imported" badge.
2. The driver list and detail slideout rendered a blank name with no fallback
   (`{driver.first_name} {driver.last_name}`), unlike the rider-facing `users/page.tsx`.
3. `DriverRidesTab`'s `driverName` prop had no fallback, unlike its sibling
   `DriverPayoutsTab` — producing an empty "Driver" table column and a subject-less
   zero-state sentence for a legacy driver's ride history.
4. "No payout method" copy was identical for a new driver mid-onboarding and a legacy
   driver whose old-app banking data was never imported (a permanent, expected gap,
   `ACTION_ITEMS.md` A34) — an admin couldn't tell which remediation applied.

## 2. Root cause

- (1) The A30 badge pattern (added for ride rows) was never extended to driver/rider
  *profile* rows, even though `legacy_import_metadata` already reaches the frontend
  on every driver row (`admin_get_drivers` has no restrictive `columns=`) and on the
  per-rider DETAIL fetch (`admin_get_user_details` does `select("*")` via
  `get_user_by_id`). The data was present; nothing rendered it.
- (2)–(3) The blank-name and empty-column bugs are reachable because the legacy driver
  importer writes `""` (not skipped, not null) for a blank `full_name`, and two
  render sites in `drivers/page.tsx` never had a fallback chain, while a sibling
  component (`users/page.tsx`, `DriverPayoutsTab`) already did — the fallback logic
  existed in the codebase but wasn't applied consistently.
- (4) The payout-method-missing copy was written once, before the legacy-import
  distinction existed as a concept in this UI, and was never revisited when
  `legacy_import_metadata` was added.

## 3. Fix / remediation

1. Added a shared `driverDisplayName()` helper in `drivers/page.tsx` (mirrors the
   `|| email || phone` pattern already in `users/page.tsx`) and applied it to the
   driver list row and detail header, with an "Unnamed driver" italic placeholder as
   the final fallback.
2. Added an "Imported" badge (same visual pattern as the A30 ride-row badge:
   `bg-muted`, `text-[10px]`, `rounded px-1.5 py-0.5`) next to the driver name on the
   list row and detail header, and next to the Rider/Driver role badges in the rider
   detail dialog, gated on `legacy_import_metadata` being non-empty (`{}` = not
   imported, per this repo's exclusion-predicate convention).
3. `DriverRidesTab`'s call site now uses the same `driverDisplayName()` helper instead
   of the bare, fallback-less trim/join expression.
4. Added an `isLegacyImported` prop to `DriverPayoutsTab`, computed at the call site
   from `selected.legacy_import_metadata`, and a second "No payout method" copy branch
   for legacy drivers that explains the historical-data gap and points at re-adding a
   payout method instead of implying the driver forgot to add one.

**Rider list-view badge: blocked, not implemented.** `admin_get_users`
(`backend/routes/admin/users.py`) projects through `_USER_LIST_COLUMNS`, which does
**not** include `legacy_import_metadata` — confirmed by reading the backend route
before writing any code. Adding the badge to the rider *list* table would require a
backend column-list change, which is out of this session's admin-dashboard-only,
frontend-only scope (see "strict scope boundary" in the task brief — no backend
files touched). The rider *detail* dialog does have the field (its endpoint selects
the full row) and got the badge; the list/table view did not.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to two files, four independent render sites.**
  `driverDisplayName()` is a new function with exactly two call sites added in this
  session's first commit (driver list row, driver detail header) plus two more reused
  in later commits (`DriverRidesTab`, `DriverPayoutsTab` call sites) — all four are
  in `drivers/page.tsx`, all four are this session's own additions, so there is no
  pre-existing caller to break.
- Grepped for every other importer of `DriverRidesTab` and `DriverPayoutsTab`
  (`grep -rn "DriverRidesTab\|DriverPayoutsTab" admin-dashboard/src`): both are
  locally-scoped functions defined and consumed only inside
  `drivers/page.tsx`, each with exactly one call site (the Rides tab, the Payouts
  tab). No other page imports them.
- Grepped for every other importer of the badge block edited in `users/page.tsx`
  (the `selectedUser`/`userDetail` dialog): it is inline JSX inside `UsersPage`'s
  single default export, not extracted to a shared component — no other consumer.
- No table, endpoint, or background loop was touched — this is a pure rendering
  change reading fields (`legacy_import_metadata`, `first_name`, `last_name`,
  `email`, `phone`) that were already present in the API responses these pages
  already fetch. No new network calls were added.
- `isLegacyImported` is a newly-added *required* prop on `DriverPayoutsTab`; since
  the component has a single call site (updated in the same commit), this cannot
  leave any other caller with a missing-prop TypeScript error — confirmed by a clean
  `tsc --noEmit` after each commit.
- Risk is limited to: (a) the "Unnamed driver" placeholder text being visible to an
  admin for a driver row with truly no name/email/phone (extremely rare — should be
  data-quality-flag-worthy on its own, not a regression); (b) the new payout-method
  copy branch showing for any driver whose `legacy_import_metadata` is non-empty
  AND has no bank account/Stripe Connect linked, which is a widening of *visible
  detail*, not a change to *any underlying payout logic* — no payout computation,
  Stripe call, or money-moving code path was touched.

## 5. User-experience effect

- **Internal-admin-facing only.** No rider, driver, or corporate-admin-facing surface
  changed. Nothing in `rider-app/`, `driver-app/`, or backend was touched.
- Visible only inside the admin dashboard's Drivers and Users pages — not mid-session
  to any rider or driver already using the consumer/driver apps, since those apps
  don't render this UI at all.
- Copy change (finding 4) is additive information for admins investigating a
  specific driver's payout state — it does not change any button, action, or flow;
  the existing "add a payout method" guidance is still present in both branches.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/drivers/page.tsx` | Added `driverDisplayName()` helper; applied it (with an "Unnamed driver" fallback) to the driver list row and detail header; added the "Imported" badge next to the name in both spots; reused the helper for `DriverRidesTab`'s and `DriverPayoutsTab`'s `driverName` props; added `isLegacyImported` prop + a second "no payout method" copy branch for legacy drivers | Findings 1 (driver side), 2, 3, 4 |
| `admin-dashboard/src/app/dashboard/users/page.tsx` | Added the "Imported" badge to the rider detail dialog, next to the Rider/Driver role badges, reading `userDetail.legacy_import_metadata` | Finding 1 (rider side, detail view only) |

## 7. Before / after

**Driver list/detail name (finding 2):**
```tsx
// Before
<p className="text-sm font-semibold truncate">{driver.first_name} {driver.last_name}</p>
// ...
<h2 className="text-xl font-bold">{selected.first_name} {selected.last_name}</h2>

// After
<p className="text-sm font-semibold truncate flex items-center gap-1.5">
    {driverDisplayName(driver) || <span className="text-muted-foreground/60 italic">Unnamed driver</span>}
    {driver.legacy_import_metadata && Object.keys(driver.legacy_import_metadata).length > 0 && (
        <span className="inline-block text-[10px] font-medium text-muted-foreground bg-muted rounded px-1.5 py-0.5 shrink-0">Imported</span>
    )}
</p>
```

**`DriverRidesTab` driverName (finding 3):**
```tsx
// Before
driverName={`${selected.first_name || ""} ${selected.last_name || ""}`.trim()}

// After
driverName={driverDisplayName(selected) || "this driver"}
```

**No payout method copy (finding 4):**
```tsx
// Before
<div className="col-span-full text-sm text-muted-foreground py-2">
    {driverName} has not added a payout method yet. Payouts cannot be processed until a bank account or Stripe Connect account is linked.
</div>

// After
{isLegacyImported ? (
    <div className="col-span-full text-sm text-muted-foreground py-2">
        {driverName}&rsquo;s payout method wasn&rsquo;t migrated from the previous app -- its raw
        banking data was never imported (a known, permanent gap for legacy drivers, not a bug).
        Ask {driverName} to add a bank account or Stripe Connect account to resume payouts.
    </div>
) : (
    <div className="col-span-full text-sm text-muted-foreground py-2">
        {driverName} has not added a payout method yet. Payouts cannot be processed until a bank account or Stripe Connect account is linked.
    </div>
)}
```

## 8. Rollback plan

No feature flag exists for this admin-dashboard surface (per CLAUDE.md's guidance to
use the `app_settings` pattern where it exists — it does not cover admin-dashboard
rendering toggles today, a standing gap, not newly discovered here). All four changes
are:
- Purely additive/rendering-only (no writes, no migrations, no Stripe/payment calls).
- Reachable only by reading fields already present in already-fetched API responses.

Rollback is a plain `git revert` of the relevant commit(s) followed by a normal
Vercel redeploy — acceptable here specifically because **no live data was written or
mutated** by any of these four changes (unlike a Stripe charge, wallet delta, or ride
state change, where CLAUDE.md correctly requires more than a revert). Each of the
four fixes is its own commit, so a partial rollback (e.g. reverting only the payout
copy change) is possible without touching the other three.

## 9. Verification performed

- [x] `tsc --noEmit -p tsconfig.json` run on the full admin-dashboard project after
      every commit — clean (0 errors) each time.
- [x] `npm run build` (real production build, not just dev server or `tsc --noEmit`)
      run once after all four commits — **exit 0**, all routes including
      `/dashboard/drivers` and `/dashboard/users` built successfully.
- [x] `eslint` run on both touched files after each commit — 0 errors both times
      (pre-existing warnings only, e.g. `react-hooks/set-state-in-effect` on
      unrelated lines that predate this session; none appear on or near any line
      this session touched).
- [x] Blast-radius grep performed: `grep -rn "DriverRidesTab\|DriverPayoutsTab"` across
      `admin-dashboard/src` (single call site each); `grep -rn "legacy_import_metadata"`
      across `admin-dashboard/src` and the relevant `backend/routes/admin/*.py` files
      to confirm which endpoints do/don't project the field before writing any UI code
      for it.
- [x] Read `backend/routes/admin/users.py` and `backend/routes/admin/drivers.py`
      (read-only — no backend files modified) to confirm the exact column-projection
      difference between the rider list, rider detail, and driver list endpoints
      before claiming the rider-side badge was blocked, rather than assuming.
- [ ] No manual click-through in a running browser was performed (no dev server was
      launched against a live/staging backend in this session).
- [ ] No automated visual/snapshot regression tooling exists in this repo for
      admin-dashboard (standing gap, tracked in `ACTION_ITEMS.md` — not re-litigated
      here). The "Unnamed driver" placeholder and "Imported" badge placement were
      reasoned about from the JSX/CSS classes and the established A30 badge's
      screenshot-free precedent, not visually confirmed.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert; no live-data writes to
      undo).
- [x] Blast radius is stated, not assumed (see §4 — grepped every consumer of every
      shared piece touched).
- [x] No silent behavior change to an already-shipped flow: all four changes only
      *add* a badge/fallback/copy branch for a state (`legacy_import_metadata` is
      non-empty, or all name fields are empty) that previously rendered incorrectly
      or blank — no existing correctly-rendering case changes its output.

## What was NOT completed / deferred

- **Rider list-view "Imported" badge is blocked**, not implemented: the backend
  `admin_get_users` list endpoint's `_USER_LIST_COLUMNS` projection omits
  `legacy_import_metadata`. Fixing this requires a one-line backend column-list
  change (`backend/routes/admin/users.py`, `_USER_LIST_COLUMNS`), which is out of
  this session's frontend-only, admin-dashboard-only scope and was not attempted.
  The rider *detail* dialog badge (finding 1, rider side) is fully implemented,
  since that endpoint already selects the full row.
- Two lower-severity items from the same audit section were **not** in this
  session's assigned scope and were left untouched: the "Total Rides" vs "Earnings"
  stat-card inconsistent legacy policy (tracked separately as the still-open
  ACTION_ITEMS A28 "unreconciled totals" item), and
  `admin-dashboard/src/app/dashboard/rides/_components/ride-invoice.tsx` (explicitly
  out of scope — owned by a parallel entity-name track per the task brief).
