/**
 * UX program W5.1: pins what the admin sidebar renders — group titles, item
 * labels, hrefs and icons, in DOM order — for a spread of real role grants.
 *
 * The sidebar sits on 5 merge-blocking visual baselines
 * (e2e/visual-regression.spec.ts). W5.1 moves its nav config into a shared
 * module so the command palette can derive its routes from the same source;
 * this test is the unit-level proof that the move changed nothing the
 * sidebar renders, and keeps catching list/order/label/icon drift before a
 * screenshot diff would.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

type TestUser = { role: string; modules: string[] } | null;
const state: { user: TestUser; collapsed: boolean } = { user: null, collapsed: false };

vi.mock("next/navigation", () => ({
    usePathname: () => "/dashboard",
    useSearchParams: () => new URLSearchParams(),
}));
vi.mock("next-themes", () => ({ useTheme: () => ({ resolvedTheme: "light" }) }));
vi.mock("@/store/authStore", () => ({ useAuthStore: () => ({ user: state.user }) }));
vi.mock("@/store/sidebarStore", () => ({
    useSidebarStore: (selector: (s: { collapsed: boolean; hydrate: () => void }) => unknown) =>
        selector({ collapsed: state.collapsed, hydrate: () => {} }),
}));
// Zero counts: no badge text gets appended to the Approvals/Expiring labels.
vi.mock("@/lib/api", () => ({
    getApprovalQueue: () => Promise.resolve({ stats: { total_pending: 0 } }),
    getExpiringDocs: () => Promise.resolve({ items: [] }),
}));

import { Sidebar } from "@/components/sidebar";

// Every parent that has children, opened so its children render.
const ALL_GROUPS_OPEN = {
    "/dashboard/rides": true,
    "/dashboard/drivers": true,
    "/dashboard/support": true,
    "/dashboard/support-tickets": true,
};

/** Role grants as ROLE_PRESETS in backend/routes/admin/staff.py mints them,
 *  plus two custom shapes that exercise the edge rules. */
const PROFILES = {
    super_admin: { role: "super_admin", modules: [] },
    operations: { role: "operations", modules: ["dashboard", "rides", "drivers", "service_areas", "vehicle_types"] },
    support: { role: "support", modules: ["dashboard", "support", "support_tickets", "disputes", "notifications", "users"] },
    finance: { role: "finance", modules: ["dashboard", "earnings", "promotions", "corporate_accounts", "audit"] },
    // "audit" without "dashboard": Audit Logs needs both (requiresAllModules).
    audit_settings: { role: "custom", modules: ["audit", "settings"] },
} satisfies Record<string, TestUser>;

async function renderSidebar(user: TestUser, { collapsed = false, allOpen = true } = {}) {
    localStorage.clear();
    if (allOpen) localStorage.setItem("spinr-admin-nav-expanded", JSON.stringify(ALL_GROUPS_OPEN));
    state.user = user;
    state.collapsed = collapsed;
    render(<Sidebar />);
    const nav = screen.getByRole("navigation", { name: "Admin navigation" });
    // The expanded-groups localStorage read runs in an effect; wait for it.
    await waitFor(() => expect(nav).toHaveAttribute("data-nav-hydrated", "true"));
    return nav;
}

/** "## Title" for each group heading, "Label | href | icon" for each link. */
function navLines(nav: HTMLElement): string[] {
    return Array.from(nav.querySelectorAll("p, a")).map((el) => {
        if (el.tagName === "P") return `## ${el.textContent}`;
        const icon = el.querySelector("svg")?.getAttribute("class")?.match(/lucide-([a-z0-9-]+)/)?.[1];
        const label = el.getAttribute("aria-label") ?? el.textContent;
        return `${label} | ${el.getAttribute("href")} | ${icon}`;
    });
}

const hrefs = (nav: HTMLElement) => Array.from(nav.querySelectorAll("a")).map((a) => a.getAttribute("href"));

describe("Sidebar rendered nav (pinned)", () => {
    beforeEach(() => {
        state.user = null;
        state.collapsed = false;
    });

    it("super admin, every group open: full list in order", async () => {
        const nav = await renderSidebar(PROFILES.super_admin);
        expect(navLines(nav)).toEqual([
            "Dashboard | /dashboard | layout-dashboard",
            "## Operations",
            "Live Monitoring | /dashboard/monitoring | layout-dashboard",
            "Rides | /dashboard/rides | car",
            "Unpaid Rides | /dashboard/rides/unpaid | receipt",
            "Drivers | /dashboard/drivers | car",
            "Approvals | /dashboard/drivers/queue | inbox",
            "Appeals | /dashboard/drivers/appeals | gavel",
            "Expiring Docs | /dashboard/drivers/expiring | clock",
            "Welcome Letters | /dashboard/drivers/decals | mail",
            "Licence Backfill | /dashboard/driver-license-backfill | file-text",
            "Users | /dashboard/users | users",
            "Heat Map | /dashboard/heatmap | flame",
            "Analytics | /dashboard/analytics | layout-dashboard",
            "## Configuration",
            "Service Areas | /dashboard/service-areas | map-pin",
            "Pickup Venues | /dashboard/venues | map-pin",
            "Vehicle Types | /dashboard/vehicle-types | car",
            "Promotions | /dashboard/promotions | ticket",
            "Quests & Bonuses | /dashboard/quests | trophy",
            "## Finance",
            "Earnings | /dashboard/earnings | dollar-sign",
            "Subscriptions | /dashboard/subscriptions | credit-card",
            "Corporate | /dashboard/corporate-accounts | building2",
            "## Support",
            "Support & Issues | /dashboard/support | life-buoy",
            "Support Tickets | /dashboard/support?tab=tickets | life-buoy",
            "Chargebacks | /dashboard/support?tab=disputes | circle-question-mark",
            "Complaints | /dashboard/support?tab=complaints | file-exclamation-point",
            "Lost & Found | /dashboard/support?tab=lost-found | package-search",
            "Flags | /dashboard/support?tab=flags | flag",
            "FAQs | /dashboard/support?tab=faqs | book-open",
            "Legal | /dashboard/support?tab=legal | scroll-text",
            "Help Desk (Zoho) | /dashboard/support-tickets | headphones",
            "Zoho Tickets | /dashboard/support-tickets/tickets | inbox",
            "Trends | /dashboard/support-tickets/trends | chart-column",
            "Safety | /dashboard/safety | shield-alert",
            "Notifications | /dashboard/cloud-messaging | cloud",
            "## System",
            "Redis & Infra | /dashboard/monitoring/redis | activity",
            "Dispatch Geo Status | /dashboard/monitoring/dispatch-geo | compass",
            "Sentry Issues | /dashboard/sentry-logs | bug",
            "Stripe Events | /dashboard/stripe-events | zap",
            "Audit Logs | /dashboard/audit-logs | shield",
            "Settings | /dashboard/settings | settings",
            "AI Console | /dashboard/ai-console | sparkles",
            "Records & Compliance | /dashboard/records | upload",
            "Staff | /dashboard/staff | users",
        ]);
    });

    it("default disclosure state hides every child group on /dashboard", async () => {
        const nav = await renderSidebar(PROFILES.super_admin, { allOpen: false });
        expect(hrefs(nav)).toEqual([
            "/dashboard",
            "/dashboard/monitoring",
            "/dashboard/rides",
            "/dashboard/drivers",
            "/dashboard/users",
            "/dashboard/heatmap",
            "/dashboard/analytics",
            "/dashboard/service-areas",
            "/dashboard/venues",
            "/dashboard/vehicle-types",
            "/dashboard/promotions",
            "/dashboard/quests",
            "/dashboard/earnings",
            "/dashboard/subscriptions",
            "/dashboard/corporate-accounts",
            "/dashboard/support",
            "/dashboard/support-tickets",
            "/dashboard/safety",
            "/dashboard/cloud-messaging",
            "/dashboard/monitoring/redis",
            "/dashboard/monitoring/dispatch-geo",
            "/dashboard/sentry-logs",
            "/dashboard/stripe-events",
            "/dashboard/audit-logs",
            "/dashboard/settings",
            "/dashboard/ai-console",
            "/dashboard/records",
            "/dashboard/staff",
        ]);
    });

    it("operations role", async () => {
        const nav = await renderSidebar(PROFILES.operations);
        expect(hrefs(nav)).toEqual([
            "/dashboard",
            "/dashboard/monitoring",
            "/dashboard/rides",
            "/dashboard/rides/unpaid",
            "/dashboard/drivers",
            "/dashboard/drivers/queue",
            "/dashboard/drivers/appeals",
            "/dashboard/drivers/expiring",
            "/dashboard/drivers/decals",
            "/dashboard/driver-license-backfill",
            "/dashboard/heatmap",
            "/dashboard/analytics",
            // drivers without earnings: the standalone Referrals entry shows.
            "/dashboard/referrals",
            "/dashboard/service-areas",
            "/dashboard/venues",
            "/dashboard/vehicle-types",
        ]);
    });

    it("support role", async () => {
        const nav = await renderSidebar(PROFILES.support);
        expect(hrefs(nav)).toEqual([
            "/dashboard",
            "/dashboard/users",
            "/dashboard/analytics",
            "/dashboard/support",
            "/dashboard/support?tab=tickets",
            "/dashboard/support?tab=disputes",
            "/dashboard/support?tab=complaints",
            "/dashboard/support?tab=lost-found",
            "/dashboard/support?tab=flags",
            "/dashboard/support?tab=faqs",
            "/dashboard/support?tab=legal",
            "/dashboard/support-tickets",
            "/dashboard/support-tickets/tickets",
            "/dashboard/support-tickets/trends",
            "/dashboard/safety",
            "/dashboard/cloud-messaging",
        ]);
    });

    it("finance role", async () => {
        const nav = await renderSidebar(PROFILES.finance);
        expect(hrefs(nav)).toEqual([
            "/dashboard",
            "/dashboard/analytics",
            "/dashboard/promotions",
            "/dashboard/quests",
            "/dashboard/earnings",
            "/dashboard/subscriptions",
            "/dashboard/corporate-accounts",
            "/dashboard/audit-logs",
        ]);
    });

    it("custom audit + settings grant: no Audit Logs without dashboard, no super-admin pages", async () => {
        const nav = await renderSidebar(PROFILES.audit_settings);
        expect(navLines(nav)).toEqual([
            "## System",
            "Redis & Infra | /dashboard/monitoring/redis | activity",
            "Dispatch Geo Status | /dashboard/monitoring/dispatch-geo | compass",
            "Settings | /dashboard/settings | settings",
        ]);
    });

    it("collapsed icon rail flattens children with parent-prefixed names", async () => {
        const nav = await renderSidebar(PROFILES.operations, { collapsed: true, allOpen: false });
        expect(navLines(nav)).toEqual([
            "Dashboard | /dashboard | layout-dashboard",
            "Live Monitoring | /dashboard/monitoring | layout-dashboard",
            "Rides | /dashboard/rides | car",
            "Rides → Unpaid Rides | /dashboard/rides/unpaid | receipt",
            "Drivers | /dashboard/drivers | car",
            "Drivers → Approvals | /dashboard/drivers/queue | inbox",
            "Drivers → Appeals | /dashboard/drivers/appeals | gavel",
            "Drivers → Expiring Docs | /dashboard/drivers/expiring | clock",
            "Drivers → Welcome Letters | /dashboard/drivers/decals | mail",
            "Drivers → Licence Backfill | /dashboard/driver-license-backfill | file-text",
            "Heat Map | /dashboard/heatmap | flame",
            "Analytics | /dashboard/analytics | layout-dashboard",
            "Referrals | /dashboard/referrals | gift",
            "Service Areas | /dashboard/service-areas | map-pin",
            "Pickup Venues | /dashboard/venues | map-pin",
            "Vehicle Types | /dashboard/vehicle-types | car",
        ]);
    });
});
