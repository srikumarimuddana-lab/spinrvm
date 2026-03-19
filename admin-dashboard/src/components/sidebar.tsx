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
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useState } from "react";
import { useAuthStore } from "@/store/authStore";

const NAV = [
    { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
    { href: "/dashboard/drivers", label: "Drivers", icon: Users },
    { href: "/dashboard/corporate-accounts", label: "Corporate Accounts", icon: Building2 },
    { href: "/dashboard/rides", label: "Rides", icon: Car },
    { href: "/dashboard/earnings", label: "Earnings", icon: DollarSign },
    { href: "/dashboard/promotions", label: "Promotions", icon: Ticket },
    { href: "/dashboard/disputes", label: "Disputes", icon: HelpCircle },
    { href: "/dashboard/users", label: "Users", icon: Users },
    { href: "/dashboard/surge", label: "Surge Pricing", icon: TrendingUp },
    { href: "/dashboard/heatmap", label: "Heat Map", icon: Flame },
    { href: "/dashboard/pricing", label: "Pricing", icon: Banknote },
    { href: "/dashboard/service-areas", label: "Service Areas", icon: MapPin },
    { href: "/dashboard/vehicle-types", label: "Vehicle Types", icon: Car },
    { href: "/dashboard/support", label: "Support", icon: LifeBuoy },
    { href: "/dashboard/documents", label: "Documents", icon: FileText },
    { href: "/dashboard/notifications", label: "Notifications", icon: Bell },
    { href: "/dashboard/settings", label: "Settings", icon: Settings },
];

export function Sidebar() {
    const pathname = usePathname();
    const router = useRouter();
    const [open, setOpen] = useState(false);
    const { logout } = useAuthStore();

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
                        {NAV.map((item) => {
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
