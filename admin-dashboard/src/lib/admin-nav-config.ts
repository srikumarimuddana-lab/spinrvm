/**
 * The admin dashboard's navigation config: one source of truth for the
 * sidebar (components/sidebar.tsx) and the Cmd+K / Ctrl+K command palette
 * (lib/command-palette-routes.ts). UX program W5.1 moved this here verbatim
 * from sidebar.tsx, where the palette previously kept its own hand-synced
 * copy that had drifted (missing entries, and an Audit Logs gate weaker
 * than the sidebar's).
 *
 * Visibility is decided only by isNavItemVisible / isNavChildVisible below,
 * which both consumers call, so a page the sidebar hides from an admin can
 * never show up in their palette.
 */

import {
    LayoutDashboard, Car, Users, DollarSign, Settings, MapPin, Ticket,
    Flame, Building2, LifeBuoy, HelpCircle,
    Shield, ShieldAlert, Cloud, Trophy, Activity,
    Inbox, Clock, Headphones, BarChart3, Sparkles, Gift, Upload, FileText, Bug, Mail, Gavel,
    PackageSearch, Flag, FileWarning, ScrollText, BookOpen, Zap, CreditCard, Compass,
    Receipt,
} from "lucide-react";

export interface NavItem {
    href: string;
    label: string;
    icon: any;
    module: string;
    /** Sub-navigation rendered indented under the parent. The current
     *  pattern: parent route is still its own page (e.g. /drivers shows
     *  the list); children are deeper triage views. Active highlight on
     *  the parent uses startsWith() so any child path keeps it lit. */
    children?: NavItem[];
    /** Hide this item when the user also holds this module (or is a super
     *  admin) — for pages whose content lives inside another module's page,
     *  so the entry only shows for staff who can't reach it there. */
    hideIfModule?: string;
    /** Strict role == "super_admin" gate, matching the backend's
     *  require_super_admin dependency exactly (not the module system, and
     *  NOT satisfied by role "admin" the way the normal isSuperAdmin bypass
     *  is). Use for pages whose backend routes use require_super_admin
     *  instead of require_module — otherwise an "admin"-role user sees the
     *  nav entry, clicks it, and gets 403'd on every API call. `module` is
     *  still required by the type but ignored when this is set. */
    superAdminOnly?: boolean;
    /** Backend requires ALL of these modules together (an AND, not the
     *  usual single-module OR-with-super-admin check) — set this instead of
     *  relying on `module` alone when the endpoint's mount-level module
     *  differs from its per-handler one. Without this, a role holding only
     *  `module` (but not the other required grants) sees the link and gets
     *  a 403 on every call (#4605 finding 2 — audit-logs endpoints require
     *  both "audit" and "dashboard", but the nav only checked "audit").
     *  `module` is still required by the type but ignored when this is set. */
    requiresAllModules?: string[];
    /** Gives the icon a distinct (amber) color even when not the active
     *  route, instead of the default muted grey every other item shares.
     *  Reserved for genuinely higher-severity destinations (currently just
     *  Safety) that shouldn't visually blend into an otherwise flat list
     *  of same-weight items like Notifications in the same group. */
    emphasize?: boolean;
}

export interface NavGroup {
    title: string;
    items: NavItem[];
}

export const NAV_GROUPS: NavGroup[] = [
    {
        title: "",
        items: [
            { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard, module: "dashboard" },
        ],
    },
    {
        title: "Operations",
        items: [
            { href: "/dashboard/monitoring", label: "Live Monitoring", icon: LayoutDashboard, module: "rides" },
            {
                href: "/dashboard/rides",
                label: "Rides",
                icon: Car,
                module: "rides",
                children: [
                    // Completed rides whose fare was never collected. The
                    // backend list endpoint predates this entry by a long way
                    // and had no UI at all, so these reached an admin only as
                    // a transient push/WS alert — miss it and the ride was
                    // invisible. This is the queue.
                    { href: "/dashboard/rides/unpaid", label: "Unpaid Rides", icon: Receipt, module: "rides" },
                ],
            },
            {
                href: "/dashboard/drivers",
                label: "Drivers",
                icon: Car,
                module: "drivers",
                children: [
                    { href: "/dashboard/drivers/queue", label: "Approvals", icon: Inbox, module: "drivers" },
                    { href: "/dashboard/drivers/appeals", label: "Appeals", icon: Gavel, module: "drivers" },
                    { href: "/dashboard/drivers/expiring", label: "Expiring Docs", icon: Clock, module: "drivers" },
                    { href: "/dashboard/drivers/decals", label: "Welcome Letters", icon: Mail, module: "drivers" },
                    { href: "/dashboard/driver-license-backfill", label: "Licence Backfill", icon: FileText, module: "drivers" },
                ],
            },
            { href: "/dashboard/users", label: "Users", icon: Users, module: "users" },
            // Gated on "rides", not the former "heatmap" module: this page's
            // primary content is /rides/heatmap-data, which require_module("rides")
            // already enforces. "heatmap" gated no backend route at all — it only
            // showed or hid this link — so an admin holding it without "rides" saw
            // the link and then a page whose map request 403'd. Removed from the
            // grantable list; see the note on AVAILABLE_MODULES in
            // backend/routes/admin/staff.py.
            { href: "/dashboard/heatmap", label: "Heat Map", icon: Flame, module: "rides" },
            // Dispatch Offers and Demand Forecast are tabs on this page
            // (?tab=offers / ?tab=forecast); their old standalone routes
            // redirect here, so they no longer need their own nav entries.
            { href: "/dashboard/analytics", label: "Analytics", icon: LayoutDashboard, module: "dashboard" },
            // Referrals live inside Earnings & Payouts → Referrals tab. This
            // entry (to the still-existing standalone page) shows ONLY for
            // staff with drivers but not earnings — e.g. the "operations"
            // role — who would otherwise lose all navigable referral access.
            { href: "/dashboard/referrals", label: "Referrals", icon: Gift, module: "drivers", hideIfModule: "earnings" },
        ],
    },
    {
        title: "Configuration",
        items: [
            { href: "/dashboard/service-areas", label: "Service Areas", icon: MapPin, module: "service_areas" },
            { href: "/dashboard/venues", label: "Pickup Venues", icon: MapPin, module: "service_areas" },
            { href: "/dashboard/vehicle-types", label: "Vehicle Types", icon: Car, module: "vehicle_types" },
            // Pricing & Billing, including surge, is managed per-area under
            // Service Areas → Vehicle Pricing. No standalone pricing page.
            { href: "/dashboard/promotions", label: "Promotions", icon: Ticket, module: "promotions" },
            { href: "/dashboard/quests", label: "Quests & Bonuses", icon: Trophy, module: "promotions" },
        ],
    },
    {
        title: "Finance",
        items: [
            { href: "/dashboard/earnings", label: "Earnings", icon: DollarSign, module: "earnings" },
            // Was reachable only by typing the URL — no sidebar entry existed
            // at all (IA audit, design/UX review 2026-08-28). Gated on
            // "earnings" to match the backend mount exactly: subscriptions_router
            // is require_module("earnings") in routes/admin/__init__.py, so this
            // can't show a link a grant can't back.
            { href: "/dashboard/subscriptions", label: "Subscriptions", icon: CreditCard, module: "earnings" },
            { href: "/dashboard/corporate-accounts", label: "Corporate", icon: Building2, module: "corporate_accounts" },
        ],
    },
    {
        title: "Support",
        items: [
            {
                href: "/dashboard/support",
                label: "Support & Issues",
                icon: LifeBuoy,
                module: "support",
                // All 7 sub-views are now real nav children (IA audit,
                // Finding G). Disputes and FAQs were excluded when this was
                // first added — each still had its own top-level entry, and
                // both were covered by a documented "don't merge" product
                // decision. That decision was escalated and approved for a
                // full merge (Findings A/B follow-up): support/_tabs/
                // {disputes,faqs}.tsx now render the same components their
                // old standalone pages did, so those two top-level entries
                // were removed in favour of the children below — one nav
                // path per view, matching the rest of this group.
                children: [
                    { href: "/dashboard/support?tab=tickets", label: "Support Tickets", icon: LifeBuoy, module: "support" },
                    { href: "/dashboard/support?tab=disputes", label: "Chargebacks", icon: HelpCircle, module: "support" },
                    { href: "/dashboard/support?tab=complaints", label: "Complaints", icon: FileWarning, module: "support" },
                    { href: "/dashboard/support?tab=lost-found", label: "Lost & Found", icon: PackageSearch, module: "support" },
                    { href: "/dashboard/support?tab=flags", label: "Flags", icon: Flag, module: "support" },
                    { href: "/dashboard/support?tab=faqs", label: "FAQs", icon: BookOpen, module: "support" },
                    { href: "/dashboard/support?tab=legal", label: "Legal", icon: ScrollText, module: "support" },
                ],
            },
            {
                href: "/dashboard/support-tickets",
                // "Help Desk" alone sat next to "Support & Issues" with no
                // legible line between them (design/UX review 2026-08-28) —
                // an admin had to already know this one is the Zoho
                // integration and the other is internal tickets/disputes/
                // complaints/etc. Matches the label staff/page.tsx's role
                // picker already uses for this exact module ("support_tickets"
                // → "Help Desk (Zoho)"), so this is adopting an existing
                // naming precedent, not inventing a new one.
                label: "Help Desk (Zoho)",
                icon: Headphones,
                module: "support_tickets",
                children: [
                    { href: "/dashboard/support-tickets/tickets", label: "Zoho Tickets", icon: Inbox, module: "support_tickets" },
                    { href: "/dashboard/support-tickets/trends", label: "Trends", icon: BarChart3, module: "support_tickets" },
                ],
            },
            // emphasize: Safety (SOS, insurance-period audit trail) is the
            // one P0-severity destination in this group — visually flat
            // next to same-weight siblings like Notifications previously
            // undersold what it's for. (Disputes & FAQs moved under
            // Support & Issues as children — see above.)
            { href: "/dashboard/safety", label: "Safety", icon: ShieldAlert, module: "support", emphasize: true },
            { href: "/dashboard/cloud-messaging", label: "Notifications", icon: Cloud, module: "notifications" },
        ],
    },
    {
        title: "System",
        items: [
            { href: "/dashboard/monitoring/redis", label: "Redis & Infra", icon: Activity, module: "settings" },
            { href: "/dashboard/monitoring/dispatch-geo", label: "Dispatch Geo Status", icon: Compass, module: "settings" },
            // superAdminOnly: the backend mounts /api/admin/sentry under
            // require_super_admin (raw production error data), so an
            // "admin"-role user would see the entry and 403 on every call.
            { href: "/dashboard/sentry-logs", label: "Sentry Issues", icon: Bug, module: "settings", superAdminOnly: true },
            { href: "/dashboard/stripe-events", label: "Stripe Events", icon: Zap, module: "settings", superAdminOnly: true },
            {
                href: "/dashboard/audit-logs", label: "Audit Logs", icon: Shield, module: "audit",
                // #4605 finding 2: the two audit-log endpoints require
                // require_module("audit") per-handler while their router is
                // mounted under require_module("dashboard") — FastAPI ANDs
                // them. A role granted "audit" alone previously saw this
                // link and 403'd on every call.
                requiresAllModules: ["audit", "dashboard"],
            },
            { href: "/dashboard/settings", label: "Settings", icon: Settings, module: "settings" },
            // Super-admin-only, stated with the flag rather than implied by a
            // module string no role can hold. The previous spelling —
            // module: "ai_console", granted to nobody — produced the right
            // outcome for the wrong reason: it depended on that module NEVER
            // being added to AVAILABLE_MODULES, so someone adding it for an
            // unrelated feature would have silently exposed impersonation and
            // rider chat-history reads in the nav. Same shape as Sentry above;
            // the router is mounted under require_super_admin to match.
            { href: "/dashboard/ai-console", label: "AI Console", icon: Sparkles, module: "settings", superAdminOnly: true },
            // Records & Compliance consolidates 4 formerly-separate entries
            // (Data Transfer, Compliance, Bulk Operations, Export Approvals)
            // into one page with 4 tabs — they all do the same underlying
            // job (move or report on regulated driver/rider data) and were
            // scattered across this System group with no obvious relation
            // to each other. Old routes still work (next.config.ts redirects
            // to /dashboard/records?tab=<slug>), so nothing bookmarked or
            // linked from an old audit-log entry breaks.
            //
            // All 4 tabs (Data Transfer, Compliance, Bulk Operations, Export
            // Approvals) are super-admin-only: the backend mounts
            // compliance_router under require_super_admin, same as the other
            // three (decision log 2026-08-19, section 2, option B —
            // "compliance" was never in AVAILABLE_MODULES/ROLE_PRESETS, so no
            // non-super-admin could ever reach it; this just states that
            // restriction explicitly instead of via a dead module string).
            // Same shape as Sentry/AI Console above.
            { href: "/dashboard/records", label: "Records & Compliance", icon: Upload, module: "settings", superAdminOnly: true },
            { href: "/dashboard/staff", label: "Staff", icon: Users, module: "staff" },
        ],
    },
];

/** Who is looking: `isSuperAdmin` is strictly role === "super_admin" (see
 *  the note on it in sidebar.tsx — role "admin" is module-scoped like any
 *  other role); `modules` is the admin's module grant list. */
export interface NavAccess {
    isSuperAdmin: boolean;
    modules: string[];
}

/** Top-level item visibility — the rule sidebar.tsx has always applied to
 *  each NAV_GROUPS item. */
export function isNavItemVisible(item: NavItem, { isSuperAdmin, modules }: NavAccess): boolean {
    // Suppressed when the user can already reach this
    // content inside another module's page.
    if (item.hideIfModule && (isSuperAdmin || modules.includes(item.hideIfModule))) return false;
    if (item.superAdminOnly) return isSuperAdmin;
    if (item.requiresAllModules) {
        return isSuperAdmin || item.requiresAllModules.every(m => modules.includes(m));
    }
    return isSuperAdmin || modules.includes(item.module);
}

/** Child item visibility, checked only for children of a visible parent.
 *  Deliberately the narrower rule the sidebar has always used for children
 *  (superAdminOnly, else module) — hideIfModule / requiresAllModules are
 *  parent-level rules and no child sets them. */
export function isNavChildVisible(child: NavItem, { isSuperAdmin, modules }: NavAccess): boolean {
    return child.superAdminOnly
        ? isSuperAdmin
        : (isSuperAdmin || modules.includes(child.module));
}
