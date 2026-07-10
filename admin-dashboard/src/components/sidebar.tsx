"use client";

import Link from "next/link";
import { useRouter, usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
    LayoutDashboard, Car, Users, DollarSign, Settings, MapPin, Ticket,
    Flame, Building2, LifeBuoy, HelpCircle,
    LogOut, Menu, X, ChevronLeft, ChevronRight,
    Sun, Moon, Shield, ShieldAlert, Cloud, Trophy, TrendingUp, Activity,
    Inbox, Clock, Headphones, BarChart3, Send, Sparkles,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useState, useEffect } from "react";
import { useAuthStore } from "@/store/authStore";
import { logoutAllAdmin, getApprovalQueue, getExpiringDocs } from "@/lib/api";
import { useTheme } from "next-themes";

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
                    { href: "/dashboard/drivers/expiring", label: "Expiring Docs", icon: Clock, module: "drivers" },
                ],
            },
            { href: "/dashboard/users", label: "Users", icon: Users, module: "users" },
            { href: "/dashboard/heatmap", label: "Heat Map", icon: Flame, module: "heatmap" },
            { href: "/dashboard/analytics", label: "Analytics", icon: LayoutDashboard, module: "dashboard" },
            { href: "/dashboard/driver-offers", label: "Driver Offers", icon: Send, module: "dashboard" },
            // Referrals now live inside Earnings & Payouts → Referrals tab
            // (finance context), so no standalone sidebar entry.
            { href: "/dashboard/forecast", label: "Demand Forecast", icon: TrendingUp, module: "dashboard" },
        ],
    },
    {
        title: "Configuration",
        items: [
            { href: "/dashboard/service-areas", label: "Service Areas", icon: MapPin, module: "service_areas" },
            { href: "/dashboard/venues", label: "Pickup Venues", icon: MapPin, module: "service_areas" },
            { href: "/dashboard/vehicle-types", label: "Vehicle Types", icon: Car, module: "pricing" },
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
            { href: "/dashboard/support", label: "Support & Issues", icon: LifeBuoy, module: "support" },
            {
                href: "/dashboard/support-tickets",
                label: "Help Desk",
                icon: Headphones,
                module: "support_tickets",
                children: [
                    { href: "/dashboard/support-tickets/tickets", label: "Tickets", icon: Inbox, module: "support_tickets" },
                    { href: "/dashboard/support-tickets/trends", label: "Trends", icon: BarChart3, module: "support_tickets" },
                ],
            },
            { href: "/dashboard/safety", label: "Safety", icon: ShieldAlert, module: "support" },
            { href: "/dashboard/disputes", label: "Disputes & Refunds", icon: Shield, module: "support" },
            { href: "/dashboard/faqs", label: "FAQs", icon: HelpCircle, module: "support" },
            { href: "/dashboard/cloud-messaging", label: "Notifications", icon: Cloud, module: "notifications" },
        ],
    },
    {
        title: "System",
        items: [
            { href: "/dashboard/monitoring/redis", label: "Redis & Infra", icon: Activity, module: "settings" },
            { href: "/dashboard/audit-logs", label: "Audit Logs", icon: Shield, module: "settings" },
            { href: "/dashboard/settings", label: "Settings", icon: Settings, module: "settings" },
            // module "ai_console" is granted to no staff role — combined with
            // the isSuperAdmin bypass this makes the entry super-admin-only;
            // the page and the backend re-check the role themselves.
            { href: "/dashboard/ai-console", label: "AI Console", icon: Sparkles, module: "ai_console" },
            { href: "/dashboard/staff", label: "Staff", icon: Users, module: "staff" },
        ],
    },
];

export function Sidebar() {
    const pathname = usePathname();
    const router = useRouter();
    const [mobileOpen, setMobileOpen] = useState(false);
    const [collapsed, setCollapsed] = useState(false);
    const { logout, user } = useAuthStore();
    const { theme, setTheme } = useTheme();

    const userModules = user?.modules || [];
    const isSuperAdmin = user?.role === 'super_admin' || user?.role === 'admin';

    // Live counts for sidebar badges. Fetched once on mount + every 60s
    // so the admin sees an up-to-date backlog without reloading. Only
    // fetched when the user has the drivers module — saves a needless
    // API call for staff that can't access those pages anyway.
    const [approvalsCount, setApprovalsCount] = useState<number | null>(null);
    const [expiringCount, setExpiringCount] = useState<number | null>(null);

    useEffect(() => {
        const sc = localStorage.getItem('spinr-sidebar-collapsed');
        if (sc === 'true') setCollapsed(true);
    }, []);

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

    const toggleTheme = () => {
        setTheme(theme === 'dark' ? 'light' : 'dark');
    };

    const toggleCollapse = () => {
        const next = !collapsed;
        setCollapsed(next);
        localStorage.setItem('spinr-sidebar-collapsed', String(next));
    };

    useEffect(() => {
        document.documentElement.style.setProperty('--sidebar-width', collapsed ? '68px' : '240px');
    }, [collapsed]);

    const handleLogout = () => { logout(); router.push('/login'); };

    // Sign out of every admin session for this account. Bumps
    // admin_staff.token_version (kills in-flight access tokens) and
    // revokes every refresh token. Refused server-side for the env-var
    // super admin (admin-001) — rotate ADMIN_PASSWORD instead.
    // See docs/runbooks/auth-tokens.md for incident-response context.
    const handleLogoutEverywhere = async () => {
        if (!window.confirm('Sign out every admin session for this account? You will be signed out everywhere this admin is logged in. Use this if your laptop was lost or you suspect compromise.')) return;
        try {
            await logoutAllAdmin();
        } catch (e: any) {
            // Surface server-side rejection (e.g. super-admin guard) but
            // still tear down the local session — leaving the operator on
            // a screen that thinks they're signed in is the worse failure.
            window.alert(e?.message || 'Sign-out-everywhere request failed; clearing this session anyway.');
        } finally {
            logout();
            router.push('/login');
        }
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
                        const visibleItems = isSuperAdmin
                            ? group.items
                            : group.items.filter(item => userModules.includes(item.module));
                        if (visibleItems.length === 0) return null;

                        return (
                            <div key={gi} className={cn(collapsed ? "px-1.5 py-1" : "px-3 py-1")}>
                                {group.title && !collapsed && (
                                    <p className="text-[10px] font-bold text-sidebar-foreground/40 uppercase tracking-wider px-2 pt-3 pb-1">
                                        {group.title}
                                    </p>
                                )}
                                {collapsed && gi > 0 && <div className="border-t border-sidebar-border my-1" />}
                                {visibleItems.map((item) => {
                                    const active = pathname === item.href ||
                                        (item.href !== "/dashboard" && pathname.startsWith(item.href));
                                    // Filter children the same way we filtered the parent group
                                    // — admin/super_admin always see them; other staff only see
                                    // children whose module they hold.
                                    const childItems = (item.children || []).filter(child =>
                                        isSuperAdmin || userModules.includes(child.module)
                                    );
                                    return (
                                        <div key={item.href}>
                                            <Link href={item.href} onClick={() => setMobileOpen(false)}
                                                title={collapsed ? item.label : undefined}
                                                className={cn(
                                                    "flex items-center rounded-lg text-[13px] font-medium transition-colors",
                                                    collapsed ? "justify-center p-2.5 my-0.5" : "gap-2.5 px-2.5 py-[7px] my-[1px]",
                                                    active
                                                        ? "bg-primary/10 text-primary"
                                                        : "text-sidebar-foreground/60 hover:bg-sidebar-accent hover:text-sidebar-foreground"
                                                )}
                                            >
                                                <item.icon className={cn("shrink-0", collapsed ? "h-[18px] w-[18px]" : "h-4 w-4")} />
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
                                                        const childActive = pathname === child.href || pathname.startsWith(child.href);
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
                                                                        ? "bg-primary/10 text-primary"
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
                                                            const childActive = pathname === child.href || pathname.startsWith(child.href);
                                                            const childBadge = badgeFor(child.href);
                                                            return (
                                                                <Link
                                                                    key={child.href}
                                                                    href={child.href}
                                                                    onClick={() => setMobileOpen(false)}
                                                                    className={cn(
                                                                        "flex items-center gap-2 rounded-lg text-[12px] font-medium transition-colors px-2.5 py-[6px] my-[1px]",
                                                                        childActive
                                                                            ? "bg-primary/10 text-primary"
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

                {/* Footer */}
                <div className={cn("shrink-0 border-t border-sidebar-border", collapsed ? "p-1.5" : "p-2")}>
                    <button onClick={toggleTheme}
                        className={cn("flex w-full items-center rounded-lg text-[13px] font-medium text-sidebar-foreground/50 hover:bg-sidebar-accent transition-colors",
                            collapsed ? "justify-center p-2.5" : "gap-2.5 px-2.5 py-[7px]")}>
                        {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
                        {!collapsed && (theme === 'dark' ? "Light Mode" : "Dark Mode")}
                    </button>

                    <button onClick={toggleCollapse}
                        className={cn("hidden md:flex w-full items-center rounded-lg text-[13px] font-medium text-sidebar-foreground/50 hover:bg-sidebar-accent transition-colors",
                            collapsed ? "justify-center p-2.5" : "gap-2.5 px-2.5 py-[7px]")}>
                        {collapsed ? <ChevronRight className="h-4 w-4" /> : <><ChevronLeft className="h-4 w-4" />Collapse</>}
                    </button>

                    {!collapsed && (
                        <div className="flex items-center gap-2 px-2.5 py-2 mt-1 rounded-lg bg-sidebar-accent/50">
                            <div className="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center text-primary text-xs font-bold shrink-0">
                                {user?.first_name?.[0] || user?.email?.[0]?.toUpperCase() || 'A'}
                            </div>
                            <div className="flex-1 min-w-0">
                                <p className="text-xs font-semibold text-sidebar-foreground truncate">{user?.first_name || user?.email}</p>
                                <p className="text-[10px] text-sidebar-foreground/40 truncate">{user?.role?.replace('_', ' ')}</p>
                            </div>
                        </div>
                    )}

                    <button onClick={handleLogout}
                        className={cn("flex w-full items-center rounded-lg text-[13px] font-medium text-destructive hover:bg-destructive/10 transition-colors mt-1",
                            collapsed ? "justify-center p-2.5" : "gap-2.5 px-2.5 py-[7px]")}>
                        <LogOut className="h-4 w-4" />
                        {!collapsed && "Sign Out"}
                    </button>
                    {!collapsed && (
                        <button onClick={handleLogoutEverywhere}
                            className="flex w-full items-center gap-2.5 px-2.5 py-[6px] mt-0.5 rounded-lg text-[11px] font-medium text-sidebar-foreground/60 hover:bg-destructive/10 hover:text-destructive transition-colors"
                            title="Bumps token_version + revokes all refresh tokens for this admin account">
                            <Shield className="h-3.5 w-3.5" />
                            Sign out everywhere
                        </button>
                    )}
                </div>
            </aside>
        </>
    );
}
