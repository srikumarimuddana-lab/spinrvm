/**
 * Static route index for the admin command palette (Cmd+K / Ctrl+K),
 * gated behind the `admin_command_palette_enabled` feature flag — see
 * hooks/useFeatureFlag.tsx and components/command-palette.tsx.
 *
 * This is a deliberate DUPLICATE of the {label, href, module,
 * superAdminOnly, hideIfModule} shape in components/sidebar.tsx's
 * NAV_GROUPS, not an import of it — sidebar.tsx is actively being changed
 * by another workstream and NAV_GROUPS isn't exported (it also mixes in
 * icon components and live badge-count wiring the palette doesn't need).
 * Keep this list in sync with sidebar.tsx by hand when routes change.
 */

export interface CommandPaletteRoute {
    href: string;
    label: string;
    /** Section heading, shown as a group in the palette results — matches
     *  the sidebar's NAV_GROUPS `title` for the same entry. */
    group: string;
    /** Module grant required to see this entry, mirroring sidebar.tsx's
     *  NavItem.module. Ignored when `superAdminOnly` is set. */
    module: string;
    /** Same semantics as sidebar.tsx's NavItem.superAdminOnly: strict
     *  role === "super_admin" gate for pages whose backend route uses
     *  require_super_admin instead of require_module. */
    superAdminOnly?: boolean;
    /** Same semantics as sidebar.tsx's NavItem.hideIfModule: suppressed
     *  when the admin also holds this module (content is reachable via
     *  that other module's page instead). */
    hideIfModule?: string;
}

export const COMMAND_PALETTE_ROUTES: CommandPaletteRoute[] = [
    { href: "/dashboard", label: "Dashboard", group: "", module: "dashboard" },

    { href: "/dashboard/monitoring", label: "Live Monitoring", group: "Operations", module: "rides" },
    { href: "/dashboard/rides", label: "Rides", group: "Operations", module: "rides" },
    { href: "/dashboard/drivers", label: "Drivers", group: "Operations", module: "drivers" },
    { href: "/dashboard/drivers/queue", label: "Drivers → Approvals", group: "Operations", module: "drivers" },
    { href: "/dashboard/drivers/appeals", label: "Drivers → Appeals", group: "Operations", module: "drivers" },
    { href: "/dashboard/drivers/expiring", label: "Drivers → Expiring Docs", group: "Operations", module: "drivers" },
    { href: "/dashboard/drivers/decals", label: "Drivers → Welcome Letters", group: "Operations", module: "drivers" },
    { href: "/dashboard/driver-license-backfill", label: "Drivers → Licence Backfill", group: "Operations", module: "drivers" },
    { href: "/dashboard/users", label: "Users", group: "Operations", module: "users" },
    { href: "/dashboard/heatmap", label: "Heat Map", group: "Operations", module: "rides" },
    { href: "/dashboard/analytics", label: "Analytics", group: "Operations", module: "dashboard" },
    { href: "/dashboard/referrals", label: "Referrals", group: "Operations", module: "drivers", hideIfModule: "earnings" },

    { href: "/dashboard/service-areas", label: "Service Areas", group: "Configuration", module: "service_areas" },
    { href: "/dashboard/venues", label: "Pickup Venues", group: "Configuration", module: "service_areas" },
    { href: "/dashboard/vehicle-types", label: "Vehicle Types", group: "Configuration", module: "vehicle_types" },
    { href: "/dashboard/promotions", label: "Promotions", group: "Configuration", module: "promotions" },
    { href: "/dashboard/quests", label: "Quests & Bonuses", group: "Configuration", module: "promotions" },

    { href: "/dashboard/earnings", label: "Earnings", group: "Finance", module: "earnings" },
    { href: "/dashboard/corporate-accounts", label: "Corporate", group: "Finance", module: "corporate_accounts" },

    { href: "/dashboard/support", label: "Support & Issues", group: "Support", module: "support" },
    { href: "/dashboard/support?tab=tickets", label: "Support & Issues → Support Tickets", group: "Support", module: "support" },
    { href: "/dashboard/support?tab=disputes", label: "Support & Issues → Disputes & Refunds", group: "Support", module: "support" },
    { href: "/dashboard/support?tab=complaints", label: "Support & Issues → Complaints", group: "Support", module: "support" },
    { href: "/dashboard/support?tab=lost-found", label: "Support & Issues → Lost & Found", group: "Support", module: "support" },
    { href: "/dashboard/support?tab=flags", label: "Support & Issues → Flags", group: "Support", module: "support" },
    { href: "/dashboard/support?tab=faqs", label: "Support & Issues → FAQs", group: "Support", module: "support" },
    { href: "/dashboard/support?tab=legal", label: "Support & Issues → Legal", group: "Support", module: "support" },
    { href: "/dashboard/support-tickets", label: "Help Desk", group: "Support", module: "support_tickets" },
    { href: "/dashboard/support-tickets/tickets", label: "Help Desk → Zoho Tickets", group: "Support", module: "support_tickets" },
    { href: "/dashboard/support-tickets/trends", label: "Help Desk → Trends", group: "Support", module: "support_tickets" },
    { href: "/dashboard/safety", label: "Safety", group: "Support", module: "support" },
    { href: "/dashboard/cloud-messaging", label: "Notifications", group: "Support", module: "notifications" },

    { href: "/dashboard/monitoring/redis", label: "Redis & Infra", group: "System", module: "settings" },
    { href: "/dashboard/sentry-logs", label: "Sentry Issues", group: "System", module: "settings", superAdminOnly: true },
    { href: "/dashboard/stripe-events", label: "Stripe Events", group: "System", module: "settings", superAdminOnly: true },
    { href: "/dashboard/audit-logs", label: "Audit Logs", group: "System", module: "audit" },
    { href: "/dashboard/settings", label: "Settings", group: "System", module: "settings" },
    { href: "/dashboard/ai-console", label: "AI Console", group: "System", module: "settings", superAdminOnly: true },
    { href: "/dashboard/records", label: "Records & Compliance", group: "System", module: "settings", superAdminOnly: true },
    { href: "/dashboard/staff", label: "Staff", group: "System", module: "staff" },
];
