/**
 * UX program W5.1: the command palette (Ctrl/Cmd+K) and the "?" shortcut
 * sheet are both mounted only while `admin_command_palette_enabled` is on.
 * With the flag off (the default) neither key does anything.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const getSettings = vi.fn();

vi.mock("next/navigation", () => ({
    useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
    usePathname: () => "/dashboard",
}));
vi.mock("@/store/authStore", () => {
    const s = { isAuthenticated: true, isLoading: false, user: { role: "super_admin", modules: [] } };
    return { useAuthStore: (selector?: (st: typeof s) => unknown) => (selector ? selector(s) : s) };
});
vi.mock("@/lib/api", () => ({ getSettings: () => getSettings() }));
// The shell's own chrome is covered by its own tests; stub it out here.
vi.mock("@/components/sidebar", () => ({ Sidebar: () => <nav aria-label="Admin navigation" /> }));
vi.mock("@/components/topbar", () => ({ Topbar: () => <header /> }));

import DashboardLayout from "@/app/dashboard/layout";

async function renderWithFlag(enabled: boolean) {
    getSettings.mockResolvedValue({ admin_command_palette_enabled: enabled });
    const user = userEvent.setup();
    const view = render(
        <DashboardLayout>
            <p>page</p>
        </DashboardLayout>,
    );
    await waitFor(() => expect(getSettings).toHaveBeenCalled());
    await act(async () => {});
    return { user, view };
}

describe("dashboard layout: admin_command_palette_enabled", () => {
    beforeEach(() => getSettings.mockReset());

    it("flag off: neither Ctrl+K nor ? opens anything, and the shell is unchanged", async () => {
        const { user, view } = await renderWithFlag(false);
        await user.keyboard("{Control>}k{/Control}");
        await user.keyboard("?");
        expect(screen.queryByRole("dialog")).toBeNull();
        // Shell = sidebar + content column only; nothing extra mounted.
        expect(view.container.firstElementChild?.childElementCount).toBe(2);
    });

    it("flag on: Ctrl+K opens the palette and ? opens the shortcut sheet", async () => {
        const { user } = await renderWithFlag(true);
        await user.keyboard("{Control>}k{/Control}");
        expect(await screen.findByRole("dialog", { name: "Jump to a page" })).toBeInTheDocument();
        await user.keyboard("{Escape}");
        await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

        await user.keyboard("?");
        expect(await screen.findByRole("dialog", { name: "Keyboard shortcuts" })).toBeInTheDocument();
        await user.keyboard("{Escape}");
        await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    });
});
