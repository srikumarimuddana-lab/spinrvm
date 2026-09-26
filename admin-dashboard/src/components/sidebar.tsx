"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useTheme } from "next-themes";
import { cn } from "@/lib/utils";
import { Menu, X, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Suspense, useState, useEffect } from "react";
import { useAuthStore } from "@/store/authStore";
import { useSidebarStore } from "@/store/sidebarStore";
import { useFeatureFlag } from "@/hooks/useFeatureFlag";
import { getApprovalQueue, getExpiringDocs } from "@/lib/api";
// Nav config (NAV_GROUPS) and its visibility rules live in a shared module
// so the command palette derives its routes from the same source (W5.1).
import { NAV_GROUPS, isNavItemVisible, isNavChildVisible } from "@/lib/admin-nav-config";
import { setVisibleInterval } from "@/lib/visible-interval";

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
    // Theme-adaptive brand mark below — same resolvedTheme-from-useTheme
    // pattern already used throughout (e.g. analytics/page.tsx,
    // driver-charts.tsx) rather than a bespoke hydration guard.
    const { resolvedTheme } = useTheme();
    // Quiet Console Stage 2 (epic #2785 Phase 3+): thin left-rule active
    // indicator instead of the filled pill, gated the same way
    // dashboard/layout.tsx gates the `.theme-v2` shell class — off by
    // default, so this must be a no-op until the flag is flipped.
    const themeV2Enabled = useFeatureFlag("admin_theme_v2_enabled");

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
    const navAccess = { isSuperAdmin, modules: userModules };

    // Live counts for sidebar badges. Fetched once on mount + every 60s
    // so the admin sees an up-to-date backlog without reloading. Only
    // fetched when the user has the drivers module — saves a needless
    // API call for staff that can't access those pages anyway.
    const [approvalsCount, setApprovalsCount] = useState<number | null>(null);
    const [expiringCount, setExpiringCount] = useState<number | null>(null);

    // Sidebar simplification (design review 2026-09-04): sub-items used to
    // always render expanded, so Drivers' 5 children + Support & Issues' 7 +
    // Help Desk's 2 were always on screen even for an admin who never opens
    // them, pushing the whole nav well past one screen. Collapsed by default
    // now; a parent auto-expands only while it (or a child) is the active
    // route, so navigating straight to a nested page never hides where you
    // are. Per-admin state persists in localStorage, keyed by href, so
    // whichever groups an admin actually lives in stay open across visits.
    // Purely a disclosure/rendering change — no route, permission, or data
    // change; the collapsed icon-rail mode (collapsed store state) already
    // flattened children as sibling icons and is untouched by this.
    const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});
    // #4998: a deterministic, DOM-observable signal that the localStorage
    // read above has run (successfully or not) — set on both paths so a
    // waiter never blocks forever on a thrown/blocked read. `isOpen` below
    // is already correct on the very first render (expandedGroups starts
    // `{}`, so it falls back to the route-based default before this effect
    // ever fires), but a test capturing a screenshot has no way to know
    // that from the outside without either reading application internals
    // or waiting on a fixed timeout — which is exactly what let a
    // CI-runner-specific rendering discrepancy go undetected (see
    // e2e/visual-regression.spec.ts's use of this attribute).
    const [navHydrated, setNavHydrated] = useState(false);
    useEffect(() => {
        try {
            const raw = localStorage.getItem("spinr-admin-nav-expanded");
            if (raw) setExpandedGroups(JSON.parse(raw));
        } catch {
            // Private-window/blocked storage: falls back to the route-based
            // default computed at render time (see isOpen below).
        } finally {
            setNavHydrated(true);
        }
    }, []);
    const toggleGroup = (href: string, next: boolean) => {
        setExpandedGroups((prev) => {
            const updated = { ...prev, [href]: next };
            try {
                localStorage.setItem("spinr-admin-nav-expanded", JSON.stringify(updated));
            } catch {}
            return updated;
        });
    };

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
        const stopPolling = setVisibleInterval(load, 60_000);
        return () => { cancelled = true; stopPolling(); };
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

            <nav aria-label="Admin navigation" data-nav-hydrated={navHydrated} className={cn(
                "fixed inset-y-0 left-0 z-40 flex flex-col border-r border-sidebar-border bg-sidebar transition-all duration-200 md:translate-x-0",
                collapsed ? "w-[68px]" : "w-60",
                mobileOpen ? "translate-x-0 w-60" : "-translate-x-full md:translate-x-0"
            )}>
                {/* Brand — real wordmark (was a fake "S" placeholder square).
                    The mark already renders "spinr" as part of the image, so
                    there's no separate text label alongside it. Two
                    pre-generated assets carry the theme-adaptive ink color
                    (dark ink for light mode, --sidebar-foreground-equivalent
                    light ink for dark mode); the red mark is unchanged
                    between them. Native 384x156 intrinsic size passed to
                    Image, display size constrained via className so the
                    aspect ratio is preserved automatically. Collapsed rail
                    (w-[68px], px-2 padding either side ⇒ ~52px available)
                    shows the same full wordmark scaled down rather than a
                    cropped icon-only slice — avoids brittle pixel-crop math
                    tied to this specific asset's layout. */}
                <div className={cn("flex shrink-0 h-14 items-center border-b border-sidebar-border", collapsed ? "justify-center px-2" : "px-4")}>
                    <Image
                        src={resolvedTheme === "dark" ? "/spinr-logo-dark.png" : "/spinr-logo-light.png"}
                        alt="Spinr"
                        width={384}
                        height={156}
                        priority
                        className={collapsed ? "h-[18px] w-auto" : "h-7 w-auto"}
                    />
                </div>

                {/* Nav */}
                <div className="flex-1 overflow-y-auto scrollbar-thin">
                    {NAV_GROUPS.map((group, gi) => {
                        const visibleItems = group.items.filter(item => isNavItemVisible(item, navAccess));
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
                                    const childItems = (item.children || []).filter(child => isNavChildVisible(child, navAccess));
                                    const hasChildren = childItems.length > 0;
                                    const groupHasActiveChild = hasChildren && childItems.some(c => isActiveHref(c.href));
                                    // Default open while this item (or a child) is the active
                                    // route, so landing directly on a nested page never hides
                                    // where you are; otherwise falls back to the admin's last
                                    // explicit choice, then closed.
                                    const isOpen = expandedGroups[item.href] ?? (active || groupHasActiveChild);
                                    const navLinkClassName = cn(
                                        "flex items-center rounded-lg text-[13px] font-medium transition-colors",
                                        collapsed ? "justify-center p-2.5 my-0.5" : "gap-2.5 px-2.5 py-[7px] my-[1px]",
                                        active
                                            ? (themeV2Enabled
                                                // Quiet Console Stage 2: thin left-edge rule
                                                // instead of the filled pill. An inset
                                                // box-shadow (not a border) draws the rule
                                                // without adding to the box model, so it
                                                // can't shift the icon/label the way a real
                                                // border would — no padding compensation
                                                // needed.
                                                ? "shadow-[inset_2px_0_0_0_var(--sidebar-primary)] text-sidebar-primary bg-transparent"
                                                : "bg-sidebar-primary/10 text-sidebar-primary")
                                            // Was text-sidebar-foreground/60 — computed
                                            // ~4.0:1 against the light-mode sidebar
                                            // background, short of the 4.5:1 AA floor for
                                            // 13px text. --sidebar-foreground-muted is the
                                            // same solid token already used (and
                                            // contrast-verified) for the group-title labels
                                            // just above, at ~4.8:1 on light / ~5.1:1 on
                                            // dark. Design/UX review 2026-08-28.
                                            : "text-sidebar-foreground-muted hover:bg-sidebar-accent hover:text-sidebar-foreground"
                                    );
                                    const navIcon = (
                                        <item.icon
                                            className={cn(
                                                "shrink-0",
                                                collapsed ? "h-[18px] w-[18px]" : "h-4 w-4",
                                                item.emphasize && !active && "text-warning",
                                            )}
                                        />
                                    );
                                    return (
                                        <div key={item.href}>
                                            {/* Sidebar simplification: a parent with children gets
                                                a chevron toggle alongside its own Link, sharing the
                                                same row styling as a plain item — collapsed
                                                (icon-rail) mode and childless items are completely
                                                unchanged below. */}
                                            {!collapsed && hasChildren ? (
                                                <div className={navLinkClassName}>
                                                    <Link
                                                        href={item.href}
                                                        onClick={() => setMobileOpen(false)}
                                                        className="flex items-center gap-2.5 flex-1 min-w-0"
                                                    >
                                                        {navIcon}
                                                        <span className="truncate">{item.label}</span>
                                                    </Link>
                                                    <button
                                                        type="button"
                                                        onClick={() => toggleGroup(item.href, !isOpen)}
                                                        aria-expanded={isOpen}
                                                        aria-label={`${isOpen ? "Collapse" : "Expand"} ${item.label}`}
                                                        className="shrink-0 -m-1 p-1 rounded hover:bg-sidebar-accent"
                                                    >
                                                        <ChevronRight className={cn("h-3.5 w-3.5 transition-transform", isOpen && "rotate-90")} />
                                                    </button>
                                                </div>
                                            ) : (
                                                <Link href={item.href} onClick={() => setMobileOpen(false)}
                                                    title={collapsed ? item.label : undefined}
                                                    aria-label={collapsed ? item.label : undefined}
                                                    className={navLinkClassName}
                                                >
                                                    {navIcon}
                                                    {!collapsed && item.label}
                                                </Link>
                                            )}
                                            {/* Children. In expanded mode they're indented under
                                                the parent with a guide line, and only rendered
                                                while the parent is toggled open. In collapsed mode
                                                we flatten them as sibling icons since there's
                                                no horizontal room to nest visually — tooltip
                                                still names them. */}
                                            {childItems.length > 0 && (
                                                collapsed ? (
                                                    childItems.map(child => {
                                                        const childActive = isActiveHref(child.href);
                                                        const childBadge = badgeFor(child.href);
                                                        // A bare colored dot (below) has no text of its
                                                        // own; the count previously only survived in
                                                        // `title`, which most browsers use as a
                                                        // fallback accessible name but which has no
                                                        // touch-device support and inconsistent AT
                                                        // behaviour. Set it as both `title` (visible
                                                        // hover tooltip) and `aria-label` (real
                                                        // accessible name) so it doesn't depend on that
                                                        // fallback. Design/UX review 2026-08-28.
                                                        const childAccessibleName =
                                                            childBadge && childBadge > 0
                                                                ? `${item.label} → ${child.label} (${childBadge} pending)`
                                                                : `${item.label} → ${child.label}`;
                                                        return (
                                                            <Link
                                                                key={child.href}
                                                                href={child.href}
                                                                onClick={() => setMobileOpen(false)}
                                                                title={childAccessibleName}
                                                                aria-label={childAccessibleName}
                                                                className={cn(
                                                                    "relative flex items-center rounded-lg text-[13px] font-medium transition-colors",
                                                                    "justify-center p-2.5 my-0.5",
                                                                    childActive
                                                                        ? (themeV2Enabled
                                                                            ? "shadow-[inset_2px_0_0_0_var(--sidebar-primary)] text-sidebar-primary bg-transparent"
                                                                            : "bg-sidebar-primary/10 text-sidebar-primary")
                                                                        : "text-sidebar-foreground-muted hover:bg-sidebar-accent hover:text-sidebar-foreground"
                                                                )}
                                                            >
                                                                <child.icon className="shrink-0 h-[18px] w-[18px]" />
                                                                {/* Collapsed badge: indicator dot in
                                                                    the top-right corner. Tooltip
                                                                    above carries the actual count. */}
                                                                {childBadge != null && childBadge > 0 && (
                                                                    <span className="absolute top-1 right-1 w-2 h-2 rounded-full bg-warning ring-2 ring-sidebar" />
                                                                )}
                                                            </Link>
                                                        );
                                                    })
                                                ) : isOpen ? (
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
                                                                            ? (themeV2Enabled
                                                                                ? "shadow-[inset_2px_0_0_0_var(--sidebar-primary)] text-sidebar-primary bg-transparent"
                                                                                : "bg-sidebar-primary/10 text-sidebar-primary")
                                                                            // Was text-sidebar-foreground/50 (~3.1:1 on
                                                                            // light, below the 4.5:1 AA floor for this
                                                                            // 12px text) — same fix as the parent links
                                                                            // above. Design/UX review 2026-08-28.
                                                                            : "text-sidebar-foreground-muted hover:bg-sidebar-accent hover:text-sidebar-foreground"
                                                                    )}
                                                                >
                                                                    <child.icon className="shrink-0 h-3.5 w-3.5" />
                                                                    <span className="flex-1">{child.label}</span>
                                                                    {/* Expanded badge: amber pill
                                                                        with the count. Only shown
                                                                        when > 0 so a clean queue
                                                                        doesn't visually nag. */}
                                                                    {childBadge != null && childBadge > 0 && (
                                                                        <span className="ml-auto bg-warning/15 text-warning text-[10px] font-bold px-1.5 py-0.5 rounded-full tabular-nums">
                                                                            {childBadge > 99 ? "99+" : childBadge}
                                                                        </span>
                                                                    )}
                                                                </Link>
                                                            );
                                                        })}
                                                    </div>
                                                ) : null
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
            </nav>
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
