/**
 * Route index for the admin command palette (Cmd+K / Ctrl+K), gated behind
 * the `admin_command_palette_enabled` feature flag — see
 * hooks/useFeatureFlag.tsx and components/command-palette.tsx.
 *
 * Derived from lib/admin-nav-config.ts's NAV_GROUPS — the same config and
 * the same isNavItemVisible / isNavChildVisible rules the sidebar renders
 * from (UX program W5.1). This used to be a hand-maintained duplicate of
 * the sidebar's list and had drifted from it; a sidebar route change now
 * reaches the palette with no second edit.
 */

import { NAV_GROUPS, isNavChildVisible, isNavItemVisible, type NavAccess } from "@/lib/admin-nav-config";

export interface CommandPaletteRoute {
    href: string;
    /** Sidebar label; a child is prefixed with its parent's label
     *  ("Drivers → Approvals"), as the collapsed sidebar rail names it. */
    label: string;
    /** Section heading, shown as a group in the palette results — the
     *  sidebar's NAV_GROUPS `title` for the same entry. */
    group: string;
}

/**
 * Every route the sidebar shows this admin, in sidebar order: each visible
 * item, followed by its visible children. A child is only reachable through
 * a visible parent, exactly as the sidebar only renders children under a
 * parent it rendered.
 */
export function getCommandPaletteRoutes(access: NavAccess): CommandPaletteRoute[] {
    const routes: CommandPaletteRoute[] = [];
    for (const group of NAV_GROUPS) {
        for (const item of group.items) {
            if (!isNavItemVisible(item, access)) continue;
            routes.push({ href: item.href, label: item.label, group: group.title });
            for (const child of item.children ?? []) {
                if (!isNavChildVisible(child, access)) continue;
                routes.push({ href: child.href, label: `${item.label} → ${child.label}`, group: group.title });
            }
        }
    }
    return routes;
}
