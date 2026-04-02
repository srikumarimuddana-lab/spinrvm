"use client";

import Link from "next/link";
import { useRouter, usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
    LayoutDashboard, Car, Users, DollarSign, Settings, MapPin, Ticket,
    HelpCircle, Bell, Flame, Building2, LifeBuoy,
    LogOut, Menu, FileText, X, CreditCard, ChevronLeft, ChevronRight,
    Sun, Moon, Shield, Activity,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useState, useEffect } from "react";
import { useAuthStore } from "@/store/authStore";

interface NavItem {
    href: string;
    label: string;
    icon: any;
    module: string;
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
            { href: "/dashboard/rides", label: "Rides", icon: Car, module: "rides" },
            { href: "/dashboard/drivers", label: "Drivers", icon: Car, module: "drivers" },
            { href: "/dashboard/users", label: "Users", icon: Users, module: "users" },
            { href: "/dashboard/heatmap", label: "Heat Map", icon: Flame, module: "heatmap" },
        ],
    },
    {
        title: "Configuration",
        items: [
            { href: "/dashboard/service-areas", label: "Service Areas", icon: MapPin, module: "service_areas" },
            { href: "/dashboard/subscriptions", label: "Spinr Pass", icon: CreditCard, module: "pricing" },
            { href: "/dashboard/promotions", label: "Promotions", icon: Ticket, module: "promotions" },
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
            { href: "/dashboard/support", label: "Tickets", icon: LifeBuoy, module: "support" },
            { href: "/dashboard/disputes", label: "Disputes", icon: HelpCircle, module: "disputes" },
            { href: "/dashboard/notifications", label: "Notifications", icon: Bell, module: "notifications" },
        ],
    },
    {
        title: "System",
        items: [
            { href: "/dashboard/audit-logs", label: "Audit Logs", icon: Shield, module: "settings" },
            { href: "/dashboard/settings", label: "Settings", icon: Settings, module: "settings" },
            { href: "/dashboard/staff", label: "Staff", icon: Users, module: "staff" },
        ],
    },
];

export function Sidebar() {
    const pathname = usePathname();
    const router = useRouter();
    const [mobileOpen, setMobileOpen] = useState(false);
    const [collapsed, setCollapsed] = useState(false);
    const [darkMode, setDarkMode] = useState(false);
    const { logout, user } = useAuthStore();

    const userModules = user?.modules || [];
    const isSuperAdmin = user?.role === 'super_admin' || user?.role === 'admin';

    useEffect(() => {
        const saved = localStorage.getItem('spinr-theme');
        if (saved === 'dark') { setDarkMode(true); document.documentElement.classList.add('dark'); }
        const sc = localStorage.getItem('spinr-sidebar-collapsed');
        if (sc === 'true') setCollapsed(true);
    }, []);

    const toggleTheme = () => {
        const next = !darkMode;
        setDarkMode(next);
        localStorage.setItem('spinr-theme', next ? 'dark' : 'light');
        document.documentElement.classList.toggle('dark', next);
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

    return (
        <>
            <Button variant="ghost" size="icon" className="fixed top-4 left-4 z-50 md:hidden" onClick={() => setMobileOpen(!mobileOpen)}>
                {mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
            </Button>

            {mobileOpen && <div className="fixed inset-0 z-40 bg-black/50 md:hidden" onClick={() => setMobileOpen(false)} />}

            <aside className={cn(
                "fixed inset-y-0 left-0 z-40 flex flex-col border-r border-border bg-sidebar transition-all duration-200 md:translate-x-0",
                collapsed ? "w-[68px]" : "w-60",
                mobileOpen ? "translate-x-0 w-60" : "-translate-x-full md:translate-x-0"
            )}>
                {/* Brand */}
                <div className={cn("flex shrink-0 h-14 items-center border-b border-border", collapsed ? "justify-center px-2" : "gap-2.5 px-4")}>
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-red-500 to-red-600 shrink-0">
                        <span className="text-sm font-bold text-white">S</span>
                    </div>
                    {!collapsed && <span className="text-base font-bold tracking-tight">Spinr</span>}
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
                                {collapsed && gi > 0 && <div className="border-t border-border my-1" />}
                                {visibleItems.map((item) => {
                                    const active = pathname === item.href ||
                                        (item.href !== "/dashboard" && pathname.startsWith(item.href));
                                    return (
                                        <Link key={item.href} href={item.href} onClick={() => setMobileOpen(false)}
                                            title={collapsed ? item.label : undefined}
                                            className={cn(
                                                "flex items-center rounded-lg text-[13px] font-medium transition-colors",
                                                collapsed ? "justify-center p-2.5 my-0.5" : "gap-2.5 px-2.5 py-[7px] my-[1px]",
                                                active
                                                    ? "bg-red-50 text-red-600 dark:bg-red-900/20 dark:text-red-400"
                                                    : "text-sidebar-foreground/60 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground"
                                            )}
                                        >
                                            <item.icon className={cn("shrink-0", collapsed ? "h-[18px] w-[18px]" : "h-4 w-4")} />
                                            {!collapsed && item.label}
                                        </Link>
                                    );
                                })}
                            </div>
                        );
                    })}
                </div>

                {/* Footer */}
                <div className={cn("shrink-0 border-t border-border", collapsed ? "p-1.5" : "p-2")}>
                    <button onClick={toggleTheme}
                        className={cn("flex w-full items-center rounded-lg text-[13px] font-medium text-sidebar-foreground/50 hover:bg-sidebar-accent/50 transition-colors",
                            collapsed ? "justify-center p-2.5" : "gap-2.5 px-2.5 py-[7px]")}>
                        {darkMode ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
                        {!collapsed && (darkMode ? "Light Mode" : "Dark Mode")}
                    </button>

                    <button onClick={toggleCollapse}
                        className={cn("hidden md:flex w-full items-center rounded-lg text-[13px] font-medium text-sidebar-foreground/50 hover:bg-sidebar-accent/50 transition-colors",
                            collapsed ? "justify-center p-2.5" : "gap-2.5 px-2.5 py-[7px]")}>
                        {collapsed ? <ChevronRight className="h-4 w-4" /> : <><ChevronLeft className="h-4 w-4" />Collapse</>}
                    </button>

                    {!collapsed && (
                        <div className="flex items-center gap-2 px-2.5 py-2 mt-1 rounded-lg bg-sidebar-accent/30">
                            <div className="w-7 h-7 rounded-full bg-red-100 dark:bg-red-900/30 flex items-center justify-center text-red-600 dark:text-red-400 text-xs font-bold shrink-0">
                                {user?.first_name?.[0] || user?.email?.[0]?.toUpperCase() || 'A'}
                            </div>
                            <div className="flex-1 min-w-0">
                                <p className="text-xs font-semibold text-sidebar-foreground truncate">{user?.first_name || user?.email}</p>
                                <p className="text-[10px] text-sidebar-foreground/40 truncate">{user?.role?.replace('_', ' ')}</p>
                            </div>
                        </div>
                    )}

                    <button onClick={handleLogout}
                        className={cn("flex w-full items-center rounded-lg text-[13px] font-medium text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors mt-1",
                            collapsed ? "justify-center p-2.5" : "gap-2.5 px-2.5 py-[7px]")}>
                        <LogOut className="h-4 w-4" />
                        {!collapsed && "Sign Out"}
                    </button>
                </div>
            </aside>
        </>
    );
}
