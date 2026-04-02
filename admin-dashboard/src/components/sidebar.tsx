"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
    LayoutDashboard, Car, Users, DollarSign, Settings, MapPin, Ticket,
    HelpCircle, Bell, Flame, Building2, LifeBuoy, TrendingUp,
    LogOut, Menu, FileText, X, CreditCard, ChevronLeft, ChevronRight,
    Sun, Moon, Shield,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useState, useEffect } from "react";
import { useAuthStore } from "@/store/authStore";

const NAV = [
    { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard, module: "dashboard" },
    { href: "/dashboard/users", label: "Users", icon: Users, module: "users" },
    { href: "/dashboard/drivers", label: "Drivers", icon: Car, module: "drivers" },
    { href: "/dashboard/rides", label: "Rides", icon: Car, module: "rides" },
    { href: "/dashboard/earnings", label: "Earnings", icon: DollarSign, module: "earnings" },
    { href: "/dashboard/service-areas", label: "Service Areas", icon: MapPin, module: "service_areas" },
    { href: "/dashboard/subscriptions", label: "Spinr Pass", icon: CreditCard, module: "pricing" },
    { href: "/dashboard/promotions", label: "Promotions", icon: Ticket, module: "promotions" },
    { href: "/dashboard/corporate-accounts", label: "Corporate", icon: Building2, module: "corporate_accounts" },
    { href: "/dashboard/heatmap", label: "Heat Map", icon: Flame, module: "heatmap" },
    { href: "/dashboard/support", label: "Support", icon: LifeBuoy, module: "support" },
    { href: "/dashboard/disputes", label: "Disputes", icon: HelpCircle, module: "disputes" },
    { href: "/dashboard/documents", label: "Documents", icon: FileText, module: "documents" },
    { href: "/dashboard/notifications", label: "Notifications", icon: Bell, module: "notifications" },
    { href: "/dashboard/audit-logs", label: "Audit Logs", icon: Shield, module: "settings" },
    { href: "/dashboard/settings", label: "Settings", icon: Settings, module: "settings" },
    { href: "/dashboard/staff", label: "Staff", icon: Users, module: "staff" },
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
    const filteredNav = isSuperAdmin ? NAV : NAV.filter(item => userModules.includes(item.module));

    // Load theme preference
    useEffect(() => {
        const saved = localStorage.getItem('spinr-theme');
        if (saved === 'dark') {
            setDarkMode(true);
            document.documentElement.classList.add('dark');
        }
        const savedCollapsed = localStorage.getItem('spinr-sidebar-collapsed');
        if (savedCollapsed === 'true') setCollapsed(true);
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

    const handleLogout = () => { logout(); router.push('/login'); };

    const sidebarWidth = collapsed ? 'w-[68px]' : 'w-64';
    const mainMargin = collapsed ? 'md:ml-[68px]' : 'md:ml-64';

    // Expose mainMargin for layout
    useEffect(() => {
        document.documentElement.style.setProperty('--sidebar-width', collapsed ? '68px' : '256px');
    }, [collapsed]);

    return (
        <>
            {/* Mobile toggle */}
            <Button variant="ghost" size="icon" className="fixed top-4 left-4 z-50 md:hidden" onClick={() => setMobileOpen(!mobileOpen)}>
                {mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
            </Button>

            {mobileOpen && <div className="fixed inset-0 z-40 bg-black/50 md:hidden" onClick={() => setMobileOpen(false)} />}

            {/* Sidebar */}
            <aside className={cn(
                "fixed inset-y-0 left-0 z-40 flex flex-col border-r border-border bg-sidebar transition-all duration-200 md:translate-x-0",
                sidebarWidth,
                mobileOpen ? "translate-x-0 w-64" : "-translate-x-full md:translate-x-0"
            )}>
                {/* Brand */}
                <div className={cn("flex shrink-0 h-14 items-center border-b border-border", collapsed ? "justify-center px-2" : "gap-2 px-4")}>
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-red-500 to-red-600 shrink-0">
                        <span className="text-sm font-bold text-white">S</span>
                    </div>
                    {!collapsed && <span className="text-base font-bold tracking-tight">Spinr</span>}
                </div>

                {/* Nav */}
                <div className="flex-1 overflow-y-auto">
                    <nav className={cn("flex flex-col gap-0.5", collapsed ? "p-1.5" : "p-2")}>
                        {filteredNav.map((item) => {
                            const active = pathname === item.href || (item.href !== "/dashboard" && pathname.startsWith(item.href));
                            return (
                                <Link key={item.href} href={item.href} onClick={() => setMobileOpen(false)}
                                    title={collapsed ? item.label : undefined}
                                    className={cn(
                                        "flex items-center rounded-lg text-sm font-medium transition-colors",
                                        collapsed ? "justify-center p-2.5" : "gap-3 px-3 py-2",
                                        active ? "bg-red-50 text-red-600 dark:bg-red-900/20 dark:text-red-400"
                                            : "text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground"
                                    )}
                                >
                                    <item.icon className={cn("shrink-0", collapsed ? "h-5 w-5" : "h-4 w-4")} />
                                    {!collapsed && item.label}
                                </Link>
                            );
                        })}
                    </nav>
                </div>

                {/* Footer */}
                <div className={cn("shrink-0 border-t border-border", collapsed ? "p-1.5" : "p-2")}>
                    {/* Theme toggle */}
                    <button onClick={toggleTheme}
                        className={cn(
                            "flex w-full items-center rounded-lg text-sm font-medium text-sidebar-foreground/60 hover:bg-sidebar-accent/50 transition-colors",
                            collapsed ? "justify-center p-2.5" : "gap-3 px-3 py-2"
                        )}
                    >
                        {darkMode ? <Sun className={cn(collapsed ? "h-5 w-5" : "h-4 w-4")} /> : <Moon className={cn(collapsed ? "h-5 w-5" : "h-4 w-4")} />}
                        {!collapsed && (darkMode ? "Light Mode" : "Dark Mode")}
                    </button>

                    {/* Collapse toggle (desktop only) */}
                    <button onClick={toggleCollapse}
                        className={cn(
                            "hidden md:flex w-full items-center rounded-lg text-sm font-medium text-sidebar-foreground/60 hover:bg-sidebar-accent/50 transition-colors",
                            collapsed ? "justify-center p-2.5" : "gap-3 px-3 py-2"
                        )}
                    >
                        {collapsed ? <ChevronRight className="h-5 w-5" /> : <><ChevronLeft className="h-4 w-4" />Collapse</>}
                    </button>

                    {/* User info + Logout */}
                    {!collapsed && (
                        <div className="flex items-center gap-2 px-3 py-2 mt-1">
                            <div className="w-7 h-7 rounded-full bg-red-100 flex items-center justify-center text-red-600 text-xs font-bold shrink-0">
                                {user?.first_name?.[0] || user?.email?.[0]?.toUpperCase() || 'A'}
                            </div>
                            <div className="flex-1 min-w-0">
                                <p className="text-xs font-semibold text-sidebar-foreground truncate">{user?.first_name || user?.email}</p>
                                <p className="text-[10px] text-sidebar-foreground/50 truncate">{user?.role?.replace('_', ' ')}</p>
                            </div>
                        </div>
                    )}

                    <button onClick={handleLogout}
                        className={cn(
                            "flex w-full items-center rounded-lg text-sm font-medium text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors",
                            collapsed ? "justify-center p-2.5" : "gap-3 px-3 py-2"
                        )}
                    >
                        <LogOut className={cn(collapsed ? "h-5 w-5" : "h-4 w-4")} />
                        {!collapsed && "Sign Out"}
                    </button>
                </div>
            </aside>
        </>
    );
}
