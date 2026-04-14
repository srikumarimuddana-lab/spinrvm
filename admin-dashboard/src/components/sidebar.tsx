"use client";

import Link from "next/link";
import { useRouter, usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
    LayoutDashboard, Car, Users, DollarSign, Settings, MapPin, Ticket,
    HelpCircle, Flame, Building2, LifeBuoy,
    LogOut, Menu, FileText, X, CreditCard, ChevronLeft, ChevronRight,
    Sun, Moon, Shield, Cloud, Radar,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useState, useEffect } from "react";
import { useAuthStore } from "@/store/authStore";
import { useTheme } from "next-themes";

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
            { href: "/dashboard/monitoring", label: "Live Monitor", icon: Radar, module: "dashboard" },
        ],
    },
    {
        title: "Configuration",
        items: [
            { href: "/dashboard/service-areas", label: "Service Areas", icon: MapPin, module: "service_areas" },
            { href: "/dashboard/vehicle-types", label: "Vehicle Types", icon: Car, module: "pricing" },
            { href: "/dashboard/pricing", label: "Pricing & Billing", icon: DollarSign, module: "pricing" },
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
            { href: "/dashboard/support", label: "Support & Issues", icon: LifeBuoy, module: "support" },
            { href: "/dashboard/cloud-messaging", label: "Cloud Messaging", icon: Cloud, module: "notifications" },
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
    const { logout, user } = useAuthStore();
    const { theme, setTheme } = useTheme();

    const userModules = user?.modules || [];
    const isSuperAdmin = user?.role === 'super_admin' || user?.role === 'admin';

    useEffect(() => {
        const sc = localStorage.getItem('spinr-sidebar-collapsed');
        if (sc === 'true') setCollapsed(true);
    }, []);

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

    return (
        <>
            <Button variant="ghost" size="icon" className="fixed top-4 left-4 z-50 md:hidden" onClick={() => setMobileOpen(!mobileOpen)}>
                {mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
            </Button>

            {mobileOpen && <div className="fixed inset-0 z-40 bg-black/50 md:hidden" onClick={() => setMobileOpen(false)} />}

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
                                    return (
                                        <Link key={item.href} href={item.href} onClick={() => setMobileOpen(false)}
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
                </div>
            </aside>
        </>
    );
}
