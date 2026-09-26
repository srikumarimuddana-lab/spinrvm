/**
 * UX program W5.1: the command palette's routes are derived from the
 * sidebar's own nav config, so for any admin they must be exactly the
 * routes the sidebar renders for that admin — never a page the sidebar
 * hides (an RBAC leak in the palette) and never one it omits.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

type TestUser = { role: string; modules: string[] };
const state: { user: TestUser | null } = { user: null };
const push = vi.fn();

vi.mock("next/navigation", () => ({
    usePathname: () => "/dashboard",
    useSearchParams: () => new URLSearchParams(),
    useRouter: () => ({ push }),
}));
vi.mock("next-themes", () => ({ useTheme: () => ({ resolvedTheme: "light" }) }));
vi.mock("@/store/authStore", () => ({ useAuthStore: () => ({ user: state.user }) }));
vi.mock("@/store/sidebarStore", () => ({
    useSidebarStore: (selector: (s: { collapsed: boolean; hydrate: () => void }) => unknown) =>
        selector({ collapsed: false, hydrate: () => {} }),
}));
vi.mock("@/lib/api", () => ({
    getApprovalQueue: () => Promise.resolve({ stats: { total_pending: 0 } }),
    getExpiringDocs: () => Promise.resolve({ items: [] }),
}));

import { Sidebar } from "@/components/sidebar";
import { CommandPalette } from "@/components/command-palette";
import { KeyboardShortcutsSheet } from "@/components/keyboard-shortcuts-sheet";
import { getCommandPaletteRoutes } from "@/lib/command-palette-routes";

const ALL_MODULES = [
    "dashboard", "rides", "drivers", "users", "service_areas", "vehicle_types", "promotions", "earnings",
    "corporate_accounts", "support", "support_tickets", "notifications", "settings", "audit", "staff", "disputes",
];

const PROFILES: Record<string, TestUser> = {
    super_admin: { role: "super_admin", modules: [] },
    // role "admin" is module-scoped: every module, but no super-admin-only pages.
    admin_every_module: { role: "admin", modules: ALL_MODULES },
    operations: { role: "operations", modules: ["dashboard", "rides", "drivers", "service_areas", "vehicle_types"] },
    support: { role: "support", modules: ["dashboard", "support", "support_tickets", "disputes", "notifications", "users"] },
    finance: { role: "finance", modules: ["dashboard", "earnings", "promotions", "corporate_accounts", "audit"] },
    audit_settings: { role: "custom", modules: ["audit", "settings"] },
    drivers_and_earnings: { role: "custom", modules: ["drivers", "earnings"] },
    nothing: { role: "custom", modules: [] },
};

const accessFor = (u: TestUser) => ({ isSuperAdmin: u.role === "super_admin", modules: u.modules });

async function sidebarHrefs(user: TestUser): Promise<string[]> {
    localStorage.setItem(
        "spinr-admin-nav-expanded",
        JSON.stringify({ "/dashboard/rides": true, "/dashboard/drivers": true, "/dashboard/support": true, "/dashboard/support-tickets": true }),
    );
    state.user = user;
    render(<Sidebar />);
    const nav = screen.getByRole("navigation", { name: "Admin navigation" });
    await waitFor(() => expect(nav).toHaveAttribute("data-nav-hydrated", "true"));
    const hrefs = Array.from(nav.querySelectorAll("a")).map((a) => a.getAttribute("href") ?? "");
    cleanup();
    return hrefs;
}

async function openPalette(user: TestUser) {
    state.user = user;
    const ue = userEvent.setup();
    render(<CommandPalette />);
    await ue.keyboard("{Control>}k{/Control}");
    await screen.findByRole("dialog");
    return ue;
}

const optionNames = () => screen.getAllByRole("option").map((o) => o.textContent);

describe("command palette routes = sidebar routes", () => {
    beforeEach(() => {
        localStorage.clear();
        push.mockClear();
    });

    it.each(Object.entries(PROFILES))("%s", async (_name, user) => {
        const palette = getCommandPaletteRoutes(accessFor(user)).map((r) => r.href);
        expect(palette).toEqual(await sidebarHrefs(user));
    });

    it("super admin gets every entry once, including the two the old copy lacked", () => {
        const hrefs = getCommandPaletteRoutes(accessFor(PROFILES.super_admin)).map((r) => r.href);
        expect(new Set(hrefs).size).toBe(hrefs.length);
        expect(hrefs).toHaveLength(43);
        expect(hrefs).toContain("/dashboard/rides/unpaid");
        expect(hrefs).toContain("/dashboard/subscriptions");
        // hideIfModule: super admins reach Referrals inside Earnings instead.
        expect(hrefs).not.toContain("/dashboard/referrals");
    });

    it("child labels carry the parent label and the sidebar group", () => {
        const routes = getCommandPaletteRoutes(accessFor(PROFILES.operations));
        expect(routes.find((r) => r.href === "/dashboard/drivers/queue")).toEqual({
            href: "/dashboard/drivers/queue",
            label: "Drivers → Approvals",
            group: "Operations",
        });
    });
});

describe("CommandPalette (rendered)", () => {
    beforeEach(() => {
        localStorage.clear();
        push.mockClear();
    });

    it("restricted admin: only the sidebar's entries, no Audit Logs without dashboard", async () => {
        await openPalette(PROFILES.audit_settings);
        // Routes first, then the one non-route action (see the last describe).
        expect(optionNames()).toEqual(["Redis & Infra", "Dispatch Geo Status", "Settings", "Keyboard shortcuts"]);
    });

    it("role admin with every module still gets no super-admin-only page", async () => {
        await openPalette(PROFILES.admin_every_module);
        const names = optionNames();
        expect(names).toContain("Audit Logs");
        for (const hidden of ["Sentry Issues", "Stripe Events", "AI Console", "Records & Compliance"]) {
            expect(names).not.toContain(hidden);
        }
    });

    it("Enter opens the highlighted row even when matches span sections", async () => {
        // "set" ranks Settings (System) first and Live Monitoring
        // (Operations) second, but rows render grouped by section, so the
        // second row on screen is another System row. Highlight and Enter
        // must agree on every row.
        const ue = await openPalette(PROFILES.super_admin);
        await ue.keyboard("set");
        const labels = optionNames();
        expect(labels[0]).toBe("Settings");
        expect(labels.indexOf("Live Monitoring")).toBeGreaterThan(1);
        const hrefByLabel = new Map(
            getCommandPaletteRoutes(accessFor(PROFILES.super_admin)).map((r) => [r.label, r.href]),
        );
        for (let k = 0; k < labels.length; k++) {
            if (k > 0) {
                await ue.keyboard("{Control>}k{/Control}");
                await screen.findByRole("dialog");
                await ue.keyboard("set");
            }
            for (let i = 0; i < k; i++) await ue.keyboard("{ArrowDown}");
            const selected = screen.getAllByRole("option").find((o) => o.getAttribute("aria-selected") === "true");
            expect(selected?.textContent).toBe(labels[k]);
            expect(screen.getByLabelText("Search admin dashboard pages")).toHaveAttribute("aria-activedescendant", selected?.id);
            await ue.keyboard("{Enter}");
            expect(push).toHaveBeenLastCalledWith(hrefByLabel.get(labels[k]));
        }
    });

    it("Escape returns focus to where it was before the palette opened", async () => {
        state.user = PROFILES.operations;
        const ue = userEvent.setup();
        render(
            <>
                <button type="button">Page button</button>
                <CommandPalette />
            </>,
        );
        const pageButton = screen.getByRole("button", { name: "Page button" });
        pageButton.focus();
        await ue.keyboard("{Control>}k{/Control}");
        await screen.findByRole("dialog", { name: "Jump to a page" });
        await ue.keyboard("{Escape}");
        await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
        await waitFor(() => expect(pageButton).toHaveFocus());
    });

    it("finds a child route by name and navigates to it", async () => {
        const ue = await openPalette(PROFILES.operations);
        await ue.keyboard("unpaid");
        expect(optionNames()[0]).toBe("Rides → Unpaid Rides");
        await ue.keyboard("{Enter}");
        expect(push).toHaveBeenCalledWith("/dashboard/rides/unpaid");
    });
});

describe("Keyboard shortcuts palette entry (WCAG 2.1.4 fallback)", () => {
    beforeEach(() => {
        localStorage.clear();
        push.mockClear();
    });

    it("opens the shortcut sheet with single-key shortcuts off, then focus returns to the page", async () => {
        localStorage.setItem("spinr-admin-single-key-shortcuts", "off");
        state.user = PROFILES.operations;
        const ue = userEvent.setup();
        render(
            <>
                <button type="button">Page button</button>
                <CommandPalette />
                <KeyboardShortcutsSheet />
            </>,
        );
        const pageButton = screen.getByRole("button", { name: "Page button" });
        pageButton.focus();

        // "?" is off...
        await ue.keyboard("?");
        expect(screen.queryByRole("dialog")).toBeNull();

        // ...but the palette entry still gets there, and it is not a route.
        await ue.keyboard("{Control>}k{/Control}");
        await screen.findByRole("dialog", { name: "Jump to a page" });
        await ue.keyboard("shortcuts");
        expect(optionNames()).toEqual(["Keyboard shortcuts"]);
        await ue.keyboard("{Enter}");
        await screen.findByRole("dialog", { name: "Keyboard shortcuts" });
        expect(screen.queryByRole("dialog", { name: "Jump to a page" })).toBeNull();
        expect(push).not.toHaveBeenCalled();

        await ue.keyboard("{Escape}");
        await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
        await waitFor(() => expect(pageButton).toHaveFocus());
    });
});
