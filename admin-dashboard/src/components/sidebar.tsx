"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { cn } from "@/lib/utils";
import {
    LayoutDashboard, Car, Users, DollarSign, Settings, MapPin, Ticket,
    Flame, Building2, LifeBuoy, HelpCircle,
    Menu, X,
    Shield, ShieldAlert, Cloud, Trophy, TrendingUp, Activity,
    Inbox, Clock, Headphones, BarChart3, Send, Sparkles, Gift, Upload, FileText, Bug, Mail, Gavel,
    PackageSearch, Flag, FileWarning, ScrollText, BookOpen,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Suspense, useState, useEffect } from "react";
import { useAuthStore } from "@/store/authStore";
import { useSidebarStore } from "@/store/sidebarStore";
import { getApprovalQueue, getExpiringDocs } from "@/lib/api";

interface NavItem {
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
    /** Gives the icon a distinct (amber) color even when not the active
     *  route, instead of the default muted grey every other item shares.
     *  Reserved for genuinely higher-severity destinations (currently just
     *  Safety) that shouldn't visually blend into an otherwise flat list
     *  of same-weight items like Notifications in the same group. */
    emphasize?: boolean;
}

interface NavGroup {
    title: string;
    items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
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
            { href: "/dashboard/rides", label: "Rides", icon: Car, module: "rides" },
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
            { href: "/dashboard/analytics", label: "Analytics", icon: LayoutDashboard, module: "dashboard" },
            { href: "/dashboard/driver-offers", label: "Driver Offers", icon: Send, module: "dashboard" },
            // Referrals live inside Earnings & Payouts → Referrals tab. This
            // entry (to the still-existing standalone page) shows ONLY for
            // staff with drivers but not earnings — e.g. the "operations"
            // role — who would otherwise lose all navigable referral access.
            { href: "/dashboard/referrals", label: "Referrals", icon: Gift, module: "drivers", hideIfModule: "earnings" },
            { href: "/dashboard/forecast", label: "Demand Forecast", icon: TrendingUp, module: "dashboard" },
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
                    { href: "/dashboard/support?tab=disputes", label: "Disputes & Refunds", icon: HelpCircle, module: "support" },
                    { href: "/dashboard/support?tab=complaints", label: "Complaints", icon: FileWarning, module: "support" },
                    { href: "/dashboard/support?tab=lost-found", label: "Lost & Found", icon: PackageSearch, module: "support" },
                    { href: "/dashboard/support?tab=flags", label: "Flags", icon: Flag, module: "support" },
                    { href: "/dashboard/support?tab=faqs", label: "FAQs", icon: BookOpen, module: "support" },
                    { href: "/dashboard/support?tab=legal", label: "Legal", icon: ScrollText, module: "support" },
                ],
            },
            {
                href: "/dashboard/support-tickets",
                label: "Help Desk",
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
            // superAdminOnly: the backend mounts /api/admin/sentry under
            // require_super_admin (raw production error data), so an
            // "admin"-role user would see the entry and 403 on every call.
            { href: "/dashboard/sentry-logs", label: "Sentry Issues", icon: Bug, module: "settings", superAdminOnly: true },
            { href: "/dashboard/audit-logs", label: "Audit Logs", icon: Shield, module: "audit" },
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
            // Tab-level permissions differ (Data Transfer/Bulk
            // Operations/Export Approvals require strict super_admin; the
            // Compliance tab is grantable to non-super-admin staff via the
            // "compliance" module) — this single nav entry is intentionally
            // visible to EITHER group, matching /dashboard/records/page.tsx's
            // own per-tab visibility logic exactly: `module: "compliance"`
            // with no `superAdminOnly` means isSuperAdmin (role ===
            // "super_admin" only, see NAV_GROUPS filter above) OR the
            // compliance module makes this entry visible — the page itself
            // then shows only the tabs that specific user can actually
            // use, or a "no access" state if none apply (e.g. a staff
            // member with no compliance module and no super_admin role
            // never sees this entry at all).
            { href: "/dashboard/records", label: "Records & Compliance", icon: Upload, module: "compliance" },
            { href: "/dashboard/staff", label: "Staff", icon: Users, module: "staff" },
        ],
    },
];

function SidebarInner() {
    const pathname = usePathname();
    // Only needed for the Support & Issues nav children added in Finding G
    // (IA audit) — their href carries a `?tab=` query param, which
    // usePathname() strips, so highlighting them needs the actual query too.
    const searchParams = useSearchParams();
    const [mobileOpen, setMobileOpen] = useState(false);
    const collapsed = useSidebarStore((s) => s.collapsed);
    const hydrateSidebar = useSidebarStore((s) => s.hydrate);
    const { user } = useAuthStore();

    const userModules = user?.modules || [];
    // Corporate + admin portal review, Admin #4: this used to also treat
    // role === "admin" as a full-bypass super admin. "admin" is a real,
    // separate role in the backend's _admin_roles set (dependencies/
    // __init__.py) that — unlike super_admin — does NOT bypass
    // require_module() checks; it's scoped by its own `modules` grant
    // exactly like operations/support/finance/custom. The bootstrap
    // legacy admin (admin-001) is minted with role: "super_admin", not
    // "admin", so this fallback was never even covering that case — it
    // was just showing every nav entry to any "admin"-role staff member
    // regardless of their actual module grants, which the backend would
    // then 403 on click.
    const isSuperAdmin = user?.role === 'super_admin';

    // Live counts for sidebar badges. Fetched once on mount + every 60s
    // so the admin sees an up-to-date backlog without reloading. Only
    // fetched when the user has the drivers module — saves a needless
    // API call for staff that can't access those pages anyway.
    const [approvalsCount, setApprovalsCount] = useState<number | null>(null);
    const [expiringCount, setExpiringCount] = useState<number | null>(null);

    useEffect(() => {
        hydrateSidebar();
    }, [hydrateSidebar]);

    useEffect(() => {
        const canSee = isSuperAdmin || userModules.includes("drivers");
        if (!canSee) return;
        let cancelled = false;
        const load = async () => {
            try {
                // Approval queue endpoint returns stats.total_pending even
                // on a limit=1 request, so we keep the JSON small.
                const res = await getApprovalQueue({ limit: 1 });
                if (!cancelled) setApprovalsCount(res?.stats?.total_pending ?? 0);
            } catch {}
            try {
                // Expiring docs has no separate stats counter — count the
                // items in the default 30-day window.
                const res = await getExpiringDocs({ window_days: 30 });
                if (!cancelled) setExpiringCount(res?.items?.length ?? 0);
            } catch {}
        };
        load();
        const t = setInterval(load, 60_000);
        return () => { cancelled = true; clearInterval(t); };
    }, [isSuperAdmin, userModules]);

    // Map href → numeric badge count. Centralised so we only update one
    // dict when adding a new badge later.
    const badgeFor = (href: string): number | null => {
        if (href === "/dashboard/drivers/queue") return approvalsCount;
        if (href === "/dashboard/drivers/expiring") return expiringCount;
        return null;
    };

    // Shared active-route check for both parents and children. Handles two
    // shapes: a plain route (existing behaviour, unchanged — exact match or
    // the current pathname starts with it) and a `?tab=` query-param route
    // (Support & Issues' children, Finding G) — those share one pathname
    // with 6 other tabs, so highlighting needs the query too, not just the
    // path.
    const isActiveHref = (href: string): boolean => {
        const [path, query] = href.split("?");
        if (!query) {
            return pathname === href || (href !== "/dashboard" && pathname.startsWith(href));
        }
        if (pathname !== path) return false;
        const wantTab = new URLSearchParams(query).get("tab");
        return wantTab != null && searchParams.get("tab") === wantTab;
    };

    return (
        <>
            <Button variant="ghost" size="icon" className="fixed top-4 left-4 z-50 md:hidden" onClick={() => setMobileOpen(!mobileOpen)}>
                {mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
            </Button>

            {mobileOpen && <div className="fixed inset-0 z-40 bg-black/50 md:hidden" role="button" tabIndex={0} aria-label="Close navigation menu" onClick={() => setMobileOpen(false)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setMobileOpen(false); } }} />}

            <aside className={cn(
                "fixed inset-y-0 left-0 z-40 flex flex-col border-r border-sidebar-border bg-sidebar transition-all duration-200 md:translate-x-0",
                collapsed ? "w-[68px]" : "w-60",
                mobileOpen ? "translate-x-0 w-60" : "-translate-x-full md:translate-x-0"
            )}>
                {/* Brand */}
                <div className={cn("flex shrink-0 h-14 items-center border-b border-sidebar-border", collapsed ? "justify-center px-2" : "gap-2.5 px-4")}>
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary shrink-0">
                        <span className="text-sm font-bold text-primary-foreground">S</span>
                    </div>
                    {!collapsed && <span className="text-base font-bold tracking-tight text-sidebar-foreground">Spinr</span>}
                </div>

                {/* Nav */}
                <div className="flex-1 overflow-y-auto scrollbar-thin">
                    {NAV_GROUPS.map((group, gi) => {
                        const visibleItems = group.items.filter(item => {
                            // Suppressed when the user can already reach this
                            // content inside another module's page.
                            if (item.hideIfModule && (isSuperAdmin || userModules.includes(item.hideIfModule))) return false;
                            if (item.superAdminOnly) return user?.role === "super_admin";
                            return isSuperAdmin || userModules.includes(item.module);
                        });
                        if (visibleItems.length === 0) return null;

                        return (
                            <div key={gi} className={cn(collapsed ? "px-1.5 py-1" : "px-3 py-1")}>
                                {group.title && !collapsed && (
                                    <p className="text-[10px] font-bold text-sidebar-foreground-muted uppercase tracking-wider px-2 pt-3 pb-1">
                                        {group.title}
                                    </p>
                                )}
                                {collapsed && gi > 0 && <div className="border-t border-sidebar-border my-1" />}
                                {visibleItems.map((item) => {
                                    const active = isActiveHref(item.href);
                                    // Filter children the same way we filtered the parent group
                                    // — admin/super_admin always see them; other staff only see
                                    // children whose module they hold.
                                    const childItems = (item.children || []).filter(child =>
                                        child.superAdminOnly
                                            ? user?.role === "super_admin"
                                            : (isSuperAdmin || userModules.includes(child.module))
                                    );
                                    return (
                                        <div key={item.href}>
                                            <Link href={item.href} onClick={() => setMobileOpen(false)}
                                                title={collapsed ? item.label : undefined}
                                                className={cn(
                                                    "flex items-center rounded-lg text-[13px] font-medium transition-colors",
                                                    collapsed ? "justify-center p-2.5 my-0.5" : "gap-2.5 px-2.5 py-[7px] my-[1px]",
                                                    active
                                                        ? "bg-sidebar-primary/10 text-sidebar-primary"
                                                        : "text-sidebar-foreground/60 hover:bg-sidebar-accent hover:text-sidebar-foreground"
                                                )}
                                            >
                                                <item.icon
                                                    className={cn(
                                                        "shrink-0",
                                                        collapsed ? "h-[18px] w-[18px]" : "h-4 w-4",
                                                        item.emphasize && !active && "text-amber-600 dark:text-amber-500",
                                                    )}
                                                />
                                                {!collapsed && item.label}
                                            </Link>
                                            {/* Children. In expanded mode they're indented under
                                                the parent with a guide line. In collapsed mode
                                                we flatten them as sibling icons since there's
                                                no horizontal room to nest visually — tooltip
                                                still names them. */}
                                            {childItems.length > 0 && (
                                                collapsed ? (
                                                    childItems.map(child => {
                                                        const childActive = isActiveHref(child.href);
                                                        const childBadge = badgeFor(child.href);
                                                        return (
                                                            <Link
                                                                key={child.href}
                                                                href={child.href}
                                                                onClick={() => setMobileOpen(false)}
                                                                title={
                                                                    childBadge && childBadge > 0
                                                                        ? `${item.label} → ${child.label} (${childBadge} pending)`
                                                                        : `${item.label} → ${child.label}`
                                                                }
                                                                className={cn(
                                                                    "relative flex items-center rounded-lg text-[13px] font-medium transition-colors",
                                                                    "justify-center p-2.5 my-0.5",
                                                                    childActive
                                                                        ? "bg-sidebar-primary/10 text-sidebar-primary"
                                                                        : "text-sidebar-foreground/60 hover:bg-sidebar-accent hover:text-sidebar-foreground"
                                                                )}
                                                            >
                                                                <child.icon className="shrink-0 h-[18px] w-[18px]" />
                                                                {/* Collapsed badge: indicator dot in
                                                                    the top-right corner. Tooltip
                                                                    above carries the actual count. */}
                                                                {childBadge != null && childBadge > 0 && (
                                                                    <span className="absolute top-1 right-1 w-2 h-2 rounded-full bg-amber-500 ring-2 ring-sidebar" />
                                                                )}
                                                            </Link>
                                                        );
                                                    })
                                                ) : (
                                                    <div className="ml-[18px] pl-3 border-l border-sidebar-border/50 my-0.5">
                                                        {childItems.map(child => {
                                                            const childActive = isActiveHref(child.href);
                                                            const childBadge = badgeFor(child.href);
                                                            return (
                                                                <Link
                                                                    key={child.href}
                                                                    href={child.href}
                                                                    onClick={() => setMobileOpen(false)}
                                                                    className={cn(
                                                                        "flex items-center gap-2 rounded-lg text-[12px] font-medium transition-colors px-2.5 py-[6px] my-[1px]",
                                                                        childActive
                                                                            ? "bg-sidebar-primary/10 text-sidebar-primary"
                                                                            : "text-sidebar-foreground/50 hover:bg-sidebar-accent hover:text-sidebar-foreground"
                                                                    )}
                                                                >
                                                                    <child.icon className="shrink-0 h-3.5 w-3.5" />
                                                                    <span className="flex-1">{child.label}</span>
                                                                    {/* Expanded badge: amber pill
                                                                        with the count. Only shown
                                                                        when > 0 so a clean queue
                                                                        doesn't visually nag. */}
                                                                    {childBadge != null && childBadge > 0 && (
                                                                        <span className="ml-auto bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 text-[10px] font-bold px-1.5 py-0.5 rounded-full tabular-nums">
                                                                            {childBadge > 99 ? "99+" : childBadge}
                                                                        </span>
                                                                    )}
                                                                </Link>
                                                            );
                                                        })}
                                                    </div>
                                                )
                                            )}
                                        </div>
                                    );
                                })}
                            </div>
                        );
                    })}
                </div>

                {/* Account info, theme toggle, collapse control, and sign-out
                    moved to Topbar (top-right / top-left of the header) —
                    see components/topbar.tsx. Kept out of the sidebar
                    footer entirely rather than duplicated in both places. */}
            </aside>
        </>
    );
}

export function Sidebar() {
    // useSearchParams (added for the Support & Issues query-param children,
    // Finding G) requires a Suspense boundary in the App Router. The
    // sidebar renders on every /dashboard/* route already, so a null
    // fallback here would only ever show for one initial paint before
    // hydration, same tradeoff records/page.tsx already accepts.
    return (
        <Suspense fallback={null}>
            <SidebarInner />
        </Suspense>
    );
}
