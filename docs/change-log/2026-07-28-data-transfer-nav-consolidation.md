# Change Impact & Risk Log — Data Transfer module: nav consolidation (Phase 6.1)

## Issue/gap identified
The completed Data Transfer module (`/dashboard/data-transfer`, all four
tabs functional as of Phase 5.3) had no sidebar link — reachable only by
typing the URL directly. The three tools it consolidates (rider import
inside Bulk Operations, driver import at `drivers/import`, and now
export/SGI-forms) were scattered across the nav with no unifying entry.

## Root cause
Deliberate phasing — every prior phase built functionality, this final
subtask makes it discoverable.

## Fix/remediation
- Modified `admin-dashboard/src/components/sidebar.tsx`:
  - Added a new "Data Transfer" entry in the System group, gated by the
    existing `bulk_operations` module (same grant as Bulk Operations).
  - Removed the "Bulk Import" child link under Drivers (superseded by Data
    Transfer's Import tab, which also carries documents/history — the old
    page did CSV-only profile import).
  - **Deliberately kept the "Bulk Operations" entry** rather than replacing
    it, because that page also hosts the legacy Stripe-mapping-import tool
    (drivers + riders Stripe ID mapping), which is unrelated to this module
    and still needed — only its rider-import section was superseded.
- Modified `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx`:
  replaced the `<RiderImportSection />` render call with a card pointing to
  Data Transfer. The `RiderImportSection` function itself is left in the
  file (unused) rather than deleted, so a rollback doesn't need to
  reconstruct it from git history under time pressure. The Stripe-mapping
  tool above it on the same page is completely untouched.
- Modified `admin-dashboard/src/app/dashboard/drivers/import/page.tsx`:
  added a banner pointing to Data Transfer. **Deliberately did NOT gut or
  redirect this page** — the original plan called for a "redirect stub,"
  but this page's CSV-only import format and Data Transfer's ZIP-bundle
  import format aren't the same thing (different backend routes, different
  input format), and deleting 400+ lines of working, tested functionality
  outright is a bigger behavioral removal than a nav-consolidation subtask
  should make unilaterally. Per CLAUDE.md's "prefer additive over
  destructive" guidance: the page stays fully functional for anyone with a
  bookmark or direct link, just unlinked from the sidebar and pointed
  forward.

## Risk & impact on existing functionality
Blast radius: `sidebar.tsx`'s `NAV_GROUPS` is read by every admin dashboard
page load — grepped for other consumers of the `NAV_GROUPS` array: none,
it's only rendered by the `Sidebar` component itself. Removing the "Bulk
Import" child link changes navigation, not functionality — the page still
exists and works, just isn't linked (matching the "kept reachable by direct
URL" note added to the page itself). The Stripe-mapping tool on the Bulk
Operations page — the one functionality on that page NOT being
consolidated — is completely untouched: same component, same imports, same
render tree above the swapped-out rider-import card.

## User experience effect
**Visible to admins with the `bulk_operations` module** (currently
`super_admin`/`admin` only, per the existing gating): a new "Data Transfer"
sidebar entry appears; the Drivers submenu loses its "Bulk Import" child
(the page still works if bookmarked, now with a pointer forward); the Bulk
Operations page's rider-import section is replaced with a "moved" card (the
Stripe tool above it is unaffected). No mid-session disruption — this is
pure navigation, no state or active workflow is interrupted by these
changes.

## Files modified
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/components/sidebar.tsx` | +Data Transfer entry, -Bulk Import child link | Surface the new module; retire the superseded one from nav |
| `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx` | Rider-import section replaced with a pointer card; Stripe tool untouched | Redirect only the superseded part, not the whole page |
| `admin-dashboard/src/app/dashboard/drivers/import/page.tsx` | +banner pointing to Data Transfer | Discoverable pointer without deleting a working, tested import path |

## Before/after snippet
```tsx
// sidebar.tsx, before:
{ href: "/dashboard/bulk-operations", label: "Bulk Operations", icon: Upload, module: "bulk_operations" },

// after (both present):
{ href: "/dashboard/data-transfer", label: "Data Transfer", icon: Upload, module: "bulk_operations" },
{ href: "/dashboard/bulk-operations", label: "Bulk Operations", icon: Upload, module: "bulk_operations" },
```

## Rollback plan
Revert `sidebar.tsx`'s two edits (restores the "Bulk Import" child link,
removes the "Data Transfer" entry — the module itself stays live at its
URL, just unlinked again). Revert the `<RiderImportSection />` swap in
`bulk-operations/page.tsx` (the function was never deleted, so this is a
clean one-line revert). Revert the banner in `drivers/import/page.tsx`. All
three are independent, low-risk, git-revertible edits — no data or backend
state involved.

## Verification performed
- `npx tsc --noEmit -p tsconfig.json` across the whole project — zero errors
  attributable to any file this subtask touched.
- Grepped for other consumers of `NAV_GROUPS`, `RiderImportSection`, and the
  two modified pages' exports — confirmed no other file depends on the
  removed/replaced pieces.
- Confirmed the `Button` component supports the `asChild` prop (used for the
  Data Transfer link inside the replaced card) by checking
  `components/ui/button.tsx` directly rather than assuming shadcn's default.

## What was NOT verified
- Not run in a browser — the actual sidebar rendering, active-link
  highlighting, and the two pointer banners/cards are untested visually.
- No visual regression tooling exists in this repo (a standing gap per
  CLAUDE.md) — the nav layout change (one more item in the System group)
  was reasoned about, not screenshotted, for spacing/overflow at various
  viewport widths.
- This is the final subtask of the 14-subtask plan — the full click-through
  (sidebar → Data Transfer → search → select → export/import/generate) has
  still never been performed against a running dev server in this session.
  That end-to-end manual QA (listed in the original plan's Verification
  section) remains outstanding and should happen before this ships to real
  admins.
