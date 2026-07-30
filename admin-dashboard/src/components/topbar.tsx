"use client";

/**
 * Admin-portal header — account identity, theme toggle, and the sidebar
 * collapse control, all pulled out of the sidebar footer into a
 * conventional top bar (account/settings controls in the top-right corner,
 * navigation-affecting controls in the top-left) matching the pattern
 * most admin tools use, instead of a scrollable nav list ending in a
 * grab-bag of unrelated buttons.
 *
 * Split from Sidebar rather than folded into it: the collapse state now
 * lives in a shared store (store/sidebarStore.ts) since both components
 * need to read/toggle it, and this component owns nothing about
 * navigation — it only reads `user` for the identity display and calls
 * the same logout/logout-everywhere flow the old sidebar footer used.
 */

import { useRouter } from "next/navigation";
import { PanelLeftClose, PanelLeftOpen, Sun, Moon, LogOut, Shield, ChevronDown } from "lucide-react";
import { useTheme } from "next-themes";
import { Button } from "@/components/ui/button";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { useAuthStore } from "@/store/authStore";
import { useSidebarStore } from "@/store/sidebarStore";
import { logoutAllAdmin } from "@/lib/api";

export function Topbar() {
    const router = useRouter();
    const { theme, setTheme } = useTheme();
    const { logout, user } = useAuthStore();
    const collapsed = useSidebarStore((s) => s.collapsed);
    const toggleCollapsed = useSidebarStore((s) => s.toggleCollapsed);

    const handleLogout = () => {
        logout();
        router.push("/login");
    };

    // Sign out of every admin session for this account. Bumps
    // admin_staff.token_version (kills in-flight access tokens) and
    // revokes every refresh token. Refused server-side for the env-var
    // super admin (admin-001) — rotate ADMIN_PASSWORD instead.
    // See docs/runbooks/auth-tokens.md for incident-response context.
    const handleLogoutEverywhere = async () => {
        if (
            !window.confirm(
                "Sign out every admin session for this account? You will be signed out everywhere this admin is logged in. Use this if your laptop was lost or you suspect compromise.",
            )
        )
            return;
        try {
            await logoutAllAdmin();
        } catch (e: any) {
            // Surface server-side rejection (e.g. super-admin guard) but
            // still tear down the local session — leaving the operator on
            // a screen that thinks they're signed in is the worse failure.
            window.alert(e?.message || "Sign-out-everywhere request failed; clearing this session anyway.");
        } finally {
            logout();
            router.push("/login");
        }
    };

    const initial = user?.first_name?.[0] || user?.email?.[0]?.toUpperCase() || "A";
    const displayName = user?.first_name || user?.email || "Admin";
    const roleLabel = user?.role?.replace(/_/g, " ") || "";

    return (
        <header className="sticky top-0 z-30 flex h-14 items-center justify-between gap-3 border-b bg-background/95 px-4 backdrop-blur supports-[backdrop-filter]:bg-background/75 md:px-6">
            <Button
                variant="ghost"
                size="icon"
                onClick={toggleCollapsed}
                className="hidden md:inline-flex"
                title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
                aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            >
                {collapsed ? <PanelLeftOpen className="h-4.5 w-4.5" /> : <PanelLeftClose className="h-4.5 w-4.5" />}
            </Button>
            {/* Spacer on mobile — the hamburger button (Sidebar's own fixed
                top-left button) already occupies that corner there. */}
            <div className="md:hidden" />

            <div className="flex items-center gap-1.5">
                <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
                    title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
                    aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
                >
                    {theme === "dark" ? <Sun className="h-4.5 w-4.5" /> : <Moon className="h-4.5 w-4.5" />}
                </Button>

                <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                        <Button variant="ghost" className="gap-2 px-2">
                            <Avatar className="h-7 w-7">
                                <AvatarFallback className="bg-primary/10 text-xs font-bold text-primary">
                                    {initial}
                                </AvatarFallback>
                            </Avatar>
                            <span className="hidden text-left sm:block">
                                <span className="block text-xs font-semibold leading-tight">{displayName}</span>
                                <span className="block text-[10px] capitalize leading-tight text-muted-foreground">
                                    {roleLabel}
                                </span>
                            </span>
                            <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
                        </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-56">
                        <DropdownMenuLabel>
                            <span className="block truncate text-sm font-medium">{displayName}</span>
                            <span className="block truncate text-xs font-normal capitalize text-muted-foreground">
                                {roleLabel}
                            </span>
                        </DropdownMenuLabel>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem onClick={handleLogout}>
                            <LogOut className="h-4 w-4" />
                            Sign out
                        </DropdownMenuItem>
                        <DropdownMenuItem
                            onClick={handleLogoutEverywhere}
                            className="text-destructive focus:text-destructive"
                        >
                            <Shield className="h-4 w-4" />
                            Sign out everywhere
                        </DropdownMenuItem>
                    </DropdownMenuContent>
                </DropdownMenu>
            </div>
        </header>
    );
}
