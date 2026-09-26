# Change Impact & Risk Log: admin nav single source + "?" shortcut sheet (W5.1)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-26 |
| Author | Claude Code session (claude.ai/code) |
| Surface(s) | admin-dashboard (internal staff only) |
| Domain (Sentry tag) | admin |
| PR / commit link | UX program W5.1, branch `wip/w5-1` |
| Related issue or gap ID | Plan W5.1; follows the palette's original ship in `2026-08-30-admin-dashboard-design-ux-audit-fixes.md` (item L) |

## 1. Issue / gap identified

- The Cmd+K / Ctrl+K command palette's route list (`lib/command-palette-routes.ts`) was a second, hand-maintained copy of the sidebar's `NAV_GROUPS`, and it had drifted:
  - it was missing Rides → Unpaid Rides and Subscriptions;
  - it still said "Help Desk" where the sidebar says "Help Desk (Zoho)";
  - it gated Audit Logs on `audit` alone, where the sidebar requires `audit` **and** `dashboard`.
- There was no way to find out which keyboard shortcuts exist.

## 2. Root cause

- The palette shipped (2026-08-30, item L) with a deliberate copy of `NAV_GROUPS` rather than an import. `sidebar.tsx` was being edited by another workstream at the time, and it didn't export `NAV_GROUPS`.
- Later sidebar changes never reached the copy:
  - the Subscriptions entry (IA audit);
  - the Unpaid Rides child;
  - the Help Desk rename;
  - `requiresAllModules` on Audit Logs (#4605 finding 2).
- The copy's filter also never implemented `requiresAllModules`, so the Audit Logs gap came from the filter as well as the data.

## 3. Fix / remediation

1. **One nav config.** `NavItem`, `NavGroup` and `NAV_GROUPS` moved verbatim from `components/sidebar.tsx` to the new `lib/admin-nav-config.ts`. The only edit was adding `export`, and a line diff of the moved block confirms that. The sidebar's two inline visibility filters moved with it, unchanged, as named functions that the sidebar now calls:
   - `isNavItemVisible` for parent items: `hideIfModule`, `superAdminOnly`, `requiresAllModules`, then `module`;
   - `isNavChildVisible` for children: `superAdminOnly`, else `module`, checked only under a visible parent.
2. **The palette derives from it.** `getCommandPaletteRoutes(access)` walks `NAV_GROUPS` with those two functions, in sidebar order: each visible item, then its visible children. It labels a child "Parent → Child", the same name the collapsed sidebar rail uses. `command-palette.tsx` calls it instead of re-implementing the filter. The old static `COMMAND_PALETTE_ROUTES` export is gone; it had no other consumer.
3. **A "?" shortcut sheet.** `components/keyboard-shortcuts-sheet.tsx` is a Radix `Dialog` (the existing `ui/dialog` primitive) titled "Keyboard shortcuts". It lists the shortcuts that exist in code today:
   - Ctrl/⌘ K;
   - the palette's ↑ ↓ / Enter / Esc;
   - `?` itself;
   - the driver document reviewer's J / K / A / R / Esc (`document-reviewer.tsx`).

   The `?` key is ignored in these cases:
   - while typing in an input, textarea, select or contenteditable;
   - with Ctrl, ⌘ or Alt held;
   - while another dialog is already open, so the sheet never stacks over the palette or over the reviewer's own hand-rolled focus trap.
4. **Same flag.** `dashboard/layout.tsx` mounts the sheet next to `CommandPalette` under the existing `admin_command_palette_enabled` flag.

**Alternatives considered (gate 10):**
- *Keep both lists and add a drift test that asserts they match.* This has zero sidebar risk, but every route change would still need two hand edits. It also wouldn't fix the missing `requiresAllModules` in the palette filter unless the rule were copied a second time. Rejected: it doesn't give one source of truth.
- *Export `NAV_GROUPS` from `sidebar.tsx` and import it into the palette.* The palette would then import a client component module that also pulls in `next/image`, `next-themes` and the badge-count API calls. The visibility rules would also stay inline in the sidebar JSX, where the palette can't call them. Rejected in favour of a plain data-plus-predicates module that both consumers call.

## 4. Risk & impact on existing functionality

**Blast radius** (grep over `admin-dashboard/src`): single surface, internal admin only, no backend or API change.

| Symbol / module | Consumers |
|---|---|
| `components/sidebar.tsx` (`Sidebar`) | `app/dashboard/layout.tsx` only. The pre-commit hook's "referenced by ~24 files" count is a word match on "sidebar", which includes `components/ui/sidebar.tsx`, `store/sidebarStore.ts` and comments. None of those import this component. |
| `lib/command-palette-routes.ts` | `components/command-palette.tsx` only. The removed `COMMAND_PALETTE_ROUTES` export had no other importer. |
| `components/command-palette.tsx` | `app/dashboard/layout.tsx` only |
| `lib/admin-nav-config.ts` (new) | `components/sidebar.tsx`, `lib/command-palette-routes.ts` |
| `components/keyboard-shortcuts-sheet.tsx` (new) | `app/dashboard/layout.tsx` |

**Sidebar (merge-blocking visual baselines).** The sidebar renders on 5 merge-blocking Playwright baselines: `dashboard-home`, `-drivers`, `-monitoring`, `-settings` and `-rides`. It is not flag-gated; the refactor is live for everyone on deploy. Two checks back the claim that nothing changed:
- A throwaway jsdom harness rendered the sidebar's full `innerHTML` before and after the refactor. The two dumps are byte-identical across 256 variants:
  - 8 access profiles (super admin, admin with every module, operations, support, finance, audit+settings, no modules, no user);
  - 4 routes (`/dashboard`, `/dashboard/drivers/queue`, `/dashboard/support?tab=faqs`, `/dashboard/rides/unpaid`);
  - collapsed and expanded rail;
  - `admin_theme_v2_enabled` on and off;
  - default and all-open disclosure;
  - live badge counts included.

  The harness was deleted rather than committed, because it compares against a captured dump rather than anything that lives in the repo.
- `components/__tests__/sidebar.test.tsx` was committed before the refactor and passes before and after. It pins the rendered item list: group titles, labels, hrefs and lucide icon, in order.

**RBAC: who sees which palette entries.** This only matters with the flag on. Palette entries are links: the backend's `require_module` / `require_super_admin` still gates every page's API, so no entry ever granted access by itself. Each row below was measured exhaustively over every combination of the 16 grantable module strings × super-admin or not (131,072 combinations), comparing the old copy with its old filter against the new derivation:

| Palette entry | Before | After | Why |
|---|---|---|---|
| Rides → Unpaid Rides `/dashboard/rides/unpaid` | nobody | holders of `rides`, super admins | Already in the sidebar for exactly these admins; the copy lacked it |
| Subscriptions `/dashboard/subscriptions` | nobody | holders of `earnings`, super admins | Same: in the sidebar, missing from the copy |
| Audit Logs `/dashboard/audit-logs` | holders of `audit` | holders of `audit` **and** `dashboard`, super admins | The old copy was wrong. The sidebar already hid it from `audit`-without-`dashboard` admins, and the backend 403s them (#4605 finding 2). No role preset is affected: the finance preset holds both. Only a custom grant of `audit` without `dashboard` loses the entry. |
| Help Desk `/dashboard/support-tickets` (+ Zoho Tickets, Trends) | "Help Desk", "Help Desk → …" | "Help Desk (Zoho)", "Help Desk (Zoho) → …" | Label only; visibility unchanged |

No other entry changed visibility, label or group for any combination. Palette order now follows sidebar order, which the old copy already followed except for the missing entries.

**Keyboard conflicts**
- The `?` listener is on `window` and refuses to act inside text fields or over an open dialog, so it can't interfere with typing, the palette or the document reviewer.
- A pre-existing quirk, noted but not changed: the document reviewer's J/K handler ignores modifier keys. With the palette flag on, Ctrl+K inside the reviewer both toggles the palette and steps to the previous document. This predates W5.1.
- Ctrl/⌘+K while the "?" sheet is open opens the palette on top of it, because the palette's listener has no open-dialog check. Radix handles the two stacked dialogs: Escape closes the top one first. This is awkward but not broken, and was left as is.
- **Pre-existing palette bug, found in review, not fixed here.** In `command-palette.tsx`, the highlight can disagree with the Enter target:
  - Results are sorted by score but displayed grouped by section. The highlighted row (`aria-selected`) follows display order, but Enter opens `results[activeIndex]`, which follows score order.
  - When a query's matches span interleaved sections, the highlighted row and the page Enter opens can differ.
  - It is the same on `main`; W5.1 doesn't change the ranking or grouping code. Worth a separate small fix before the flag is flipped on.

**Not touched:** backend, ride, money and auth paths, `ui/**`, `topbar.tsx`, `globals.css`.

## 5. User-experience effect

- **Flag off** (the default, and production today): no change for anyone. Neither the palette nor the sheet mounts, and neither `keydown` listener is registered. The sidebar DOM is byte-identical.
- **Flag on**, internal admins only:
  - the palette gains Unpaid Rides and Subscriptions for admins who can open them;
  - custom `audit`-without-`dashboard` admins no longer see Audit Logs, which already 403'd for them;
  - Help Desk entries read "Help Desk (Zoho)";
  - pressing `?` outside a text field opens the shortcut list.
- **Mid-session:** a flag flip reaches an admin on the next full load. `FeatureFlagsProvider` reads settings once per authenticated mount, and the backend caches settings for 60 s.
- **Copy:** new strings live only in the sheet. The title is "Keyboard shortcuts", the description is "Shortcuts are off while you are typing in a field.", and each row describes one shortcut. The rows mirror the reviewer's existing in-app hint "A approve · R reject · J/K next/prev · Esc close".

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/admin-nav-config.ts` (new) | `NavItem`/`NavGroup`/`NAV_GROUPS` moved verbatim, plus `NavAccess`, `isNavItemVisible` and `isNavChildVisible` | Single source of nav config and its visibility rules |
| `admin-dashboard/src/components/sidebar.tsx` | Imports the config; the two inline filters became calls to the shared predicates; the lucide imports now used only by the config moved with it | Consume the shared module with no render change |
| `admin-dashboard/src/lib/command-palette-routes.ts` | Static copy replaced by `getCommandPaletteRoutes(access)`, derived from `NAV_GROUPS` | Remove the drifting duplicate |
| `admin-dashboard/src/components/command-palette.tsx` | Calls `getCommandPaletteRoutes` instead of its own filter | Same gating as the sidebar, by construction |
| `admin-dashboard/src/components/keyboard-shortcuts-sheet.tsx` (new) | The "?" dialog | W5.1 item 2 |
| `admin-dashboard/src/app/dashboard/layout.tsx` | Mounts the sheet under `admin_command_palette_enabled` | Same flag as the palette |
| `admin-dashboard/src/components/__tests__/sidebar.test.tsx` (new) | Pins the sidebar's rendered item list, 7 cases | Visual-baseline safety for the refactor |
| `admin-dashboard/src/components/__tests__/command-palette.test.tsx` (new) | Palette hrefs equal the rendered sidebar hrefs for 8 grants, plus rendered-palette RBAC and navigation checks, 13 cases | RBAC parity |
| `admin-dashboard/src/components/__tests__/keyboard-shortcuts-sheet.test.tsx` (new) | `?` opens, focus trap, Escape, ignored in input/textarea/contenteditable, ignored over a dialog or with modifiers, 7 cases | Accessibility and guard behaviour |
| `admin-dashboard/src/app/dashboard/layout.test.tsx` (new) | Flag off: nothing opens and no extra shell children. Flag on: both open. 2 cases | Flag gating |

## 7. Before / after

```ts
// Before: command-palette.tsx (its own copy of the rules, no requiresAllModules)
return COMMAND_PALETTE_ROUTES.filter((r) => {
    if (r.hideIfModule && (isSuperAdmin || userModules.includes(r.hideIfModule))) return false;
    if (r.superAdminOnly) return isSuperAdmin;
    return isSuperAdmin || userModules.includes(r.module);
});
```

```ts
// After: command-palette.tsx; the routes come from the sidebar's own config and rules
const visibleRoutes = useMemo(
    () => getCommandPaletteRoutes({ isSuperAdmin, modules: userModules }),
    [isSuperAdmin, userModules]
);
```

```tsx
// Before: sidebar.tsx, inline
const visibleItems = group.items.filter(item => {
    if (item.hideIfModule && (isSuperAdmin || userModules.includes(item.hideIfModule))) return false;
    if (item.superAdminOnly) return user?.role === "super_admin";
    if (item.requiresAllModules) {
        return isSuperAdmin || item.requiresAllModules.every(m => userModules.includes(m));
    }
    return isSuperAdmin || userModules.includes(item.module);
});
// After: the same rule, moved verbatim into lib/admin-nav-config.ts
const visibleItems = group.items.filter(item => isNavItemVisible(item, navAccess));
```

## 8. Rollback plan

- **Palette and "?" sheet:** turn `admin_command_palette_enabled` off. Use the Settings page toggle, or run `UPDATE settings SET admin_command_palette_enabled = false WHERE id = 'app_settings';`. No redeploy is needed; it takes effect on each admin's next load, after the backend's 60 s settings cache expires.
- **Sidebar refactor:** not flag-gated, and it doesn't need to be. It is a proven no-op for the rendered DOM: 256 variants are byte-identical and the pinned list passes before and after. If it had to be undone anyway, `git revert` of the refactor commits plus a redeploy is complete. No data, state or stored preference is involved: the `spinr-admin-nav-expanded` and `spinr-sidebar-collapsed` localStorage keys are untouched.

## 9. Verification performed

- [x] **Unit tests.** Full `npx vitest run`: 101 files and 826 tests passed, 0 failed. The new tests are 29 cases in 4 files:
  - sidebar pin, 7 cases, passing on the unmodified sidebar and after the refactor;
  - palette parity and RBAC, 13 cases;
  - shortcut sheet, 7 cases;
  - layout flag gating, 2 cases.
- [x] **Mutation checks.** Removing the sheet's text-field and open-dialog guards fails 4 of its 7 tests. Mounting the sheet without the flag fails the flag-off layout test.
- [x] **Exhaustive palette diff.** Old copy vs derived routes over 131,072 grant combinations; results in §4.
- [x] **Full-DOM equality.** Sidebar DOM compared before and after across 256 variants: byte-identical, re-checked on the final branch state.
- [x] **Type check.** `npx tsc --noEmit` exits 0.
- [x] **ESLint on the 10 changed or new files.** 0 errors and 2 warnings, both pre-existing in `sidebar.tsx`: `react-hooks/exhaustive-deps` on `userModules` and `react-hooks/set-state-in-effect` on the localStorage read. Both are unchanged and only shifted line numbers.
- [x] **Production build.** `npm run build` (`next build`, Next.js 16.3.5 Turbopack) exited 0: "Compiled successfully", 80/80 static pages generated. It was run against a plain copy of the checkout's `node_modules`, because Turbopack refuses a `node_modules` symlink that points outside the project root. No dependency was installed or changed.
- [x] **Blast-radius grep** as in §4.
- [x] **Adversarial review (gate 10).** `/code-review` at medium effort over `origin/main...wip/w5-1`: no bugs in the diff. Its two side observations are recorded in §4.
- [x] **Feature flag.** Everything user-visible stays behind `admin_command_palette_enabled`. The sidebar refactor is invisible by construction.
- [ ] **Flag flip.** Not done. Turning `admin_command_palette_enabled` on in staging, then production, is a human step, via the Settings page toggle or the SQL in §8.

## 10. What was NOT verified

- **Playwright visual regression was not run locally.** jsdom DOM equality is the proxy for the 5 sidebar baselines. CI's `visual-regression-test` job is the real gate on the PR. If it shows a sidebar diff, that contradicts the DOM-equality result and should be investigated, not re-baselined.
- **No real browser run.**
  - The `?` key was tested with `user-event` in jsdom, not on physical keyboards or non-US layouts. A layout where `?` needs AltGr (Alt held) is deliberately ignored by the modifier guard.
  - jsdom doesn't implement `isContentEditable`, so the contenteditable guard was exercised through its `closest('[contenteditable]')` fallback.
- **No screen-reader or axe pass on the sheet.** Its semantics are reasoned from the markup: a Radix dialog with a title and description, `h3` section headings under the `h2` title, a `dl` per section, and spoken names for ⌘ ↑ ↓ ?. It was not listened to.
- **The sheet has no visual baseline.** It only exists with the flag on and after pressing `?`. Light and dark appearance was reasoned from existing tokens (`bg-muted`, `text-muted-foreground`, `text-foreground/80`, the same classes the palette uses), not screenshotted.
- **Migration status unknown.** Whether migration `374_settings_admin_command_palette.sql` has been applied anywhere was not checked; see the 2026-08-30 log. Until it is, the flag reads as `false` and none of this is reachable.
- **Discoverability.** Nothing in the UI mentions `?`. Adding a hint (for example a palette footer) was out of scope.
