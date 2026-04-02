"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
    LayoutDashboard,
    Car,
    Users,
    DollarSign,
    Settings,
    MapPin,
    Ticket,
    HelpCircle,
    Bell,
    Map as MapIcon,
    Flame,
    Building2,
    LifeBuoy,
    TrendingUp,
    Banknote,
    LogOut,
    Menu,
    FileText,
    X,
    CreditCard,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useState } from "react";
import { useAuthStore } from "@/store/authStore";

// Each nav item maps to a module key for access control
const NAV = [
    { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard, module: "dashboard" },
    { href: "/dashboard/users", label: "Users", icon: Users, module: "users" },
    { href: "/dashboard/drivers", label: "Drivers", icon: Users, module: "drivers" },
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
    { href: "/dashboard/settings", label: "Settings", icon: Settings, module: "settings" },
    { href: "/dashboard/staff", label: "Staff", icon: Users, module: "staff" },
];

export function Sidebar() {
    const pathname = usePathname();
    const router = useRouter();
    const [open, setOpen] = useState(false);
    const { logout, user } = useAuthStore();

    // Filter nav items based on user's module access
    const userModules = user?.modules || [];
    const isSuperAdmin = user?.role === 'super_admin' || user?.role === 'admin';
    const filteredNav = isSuperAdmin ? NAV : NAV.filter(item => userModules.includes(item.module));

    const handleLogout = () => {
        logout();
        router.push('/login');
    };

    return (
        <>
            {/* Mobile toggle */}
            <Button
                variant="ghost"
                size="icon"
                className="fixed top-4 left-4 z-50 md:hidden"
                onClick={() => setOpen(!open)}
            >
                {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
            </Button>

            {/* Overlay */}
            {open && (
                <div
                    className="fixed inset-0 z-40 bg-black/50 md:hidden"
                    onClick={() => setOpen(false)}
                />
            )}

            {/* Sidebar */}
            <aside
                className={cn(
                    "fixed inset-y-0 left-0 z-40 flex w-64 flex-col transform border-r border-border bg-sidebar transition-transform duration-200 md:translate-x-0",
                    open ? "translate-x-0" : "-translate-x-full"
                )}
            >
                {/* Brand */}
                <div className="flex shrink-0 h-16 items-center gap-2 border-b border-border px-6">
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-violet-600 to-indigo-600">
                        <span className="text-sm font-bold text-white">S</span>
                    </div>
                    <span className="text-lg font-semibold tracking-tight">
                        Spinr Admin
                    </span>
                </div>

                {/* Nav links */}
                <div className="flex-1 overflow-y-auto">
                    <nav className="flex flex-col gap-1 p-3">
                        {filteredNav.map((item) => {
                        const active =
                            pathname === item.href ||
                            (item.href !== "/dashboard" && pathname.startsWith(item.href));
                        return (
                            <Link
                                key={item.href}
                                href={item.href}
                                onClick={() => setOpen(false)}
                                className={cn(
                                    "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors",
                                    active
                                        ? "bg-sidebar-accent text-sidebar-accent-foreground"
                                        : "text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground"
                                )}
                            >
                                <item.icon className="h-4 w-4" />
                                {item.label}
                            </Link>
                        );
                        })}
                    </nav>
                </div>

                {/* Logout */}
                <div className="shrink-0 border-t border-border p-3">
                    <button
                        onClick={handleLogout}
                        className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium text-destructive hover:bg-destructive/10 transition-colors"
                    >
                        <LogOut className="h-4 w-4" />
                        Sign Out
                    </button>
                </div>
            </aside>
        </>
    );
}
