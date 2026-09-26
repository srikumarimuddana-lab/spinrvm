/**
 * UX program W2.1: "Sign out everywhere" confirms in an in-app dialog opened
 * from the account menu. Cancelling must not call the API and must leave the
 * page clickable (a dialog opened from a Radix menu can otherwise leave
 * `pointer-events: none` on <body>).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const push = vi.fn();
const logout = vi.fn();
const logoutAllAdmin = vi.fn();

vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("next-themes", () => ({ useTheme: () => ({ theme: "light", setTheme: vi.fn() }) }));
vi.mock("@/store/authStore", () => ({
    useAuthStore: () => ({ logout, user: { first_name: "Ada", email: "ada@example.com", role: "admin" } }),
}));
vi.mock("@/store/sidebarStore", () => ({
    useSidebarStore: (selector: (s: { collapsed: boolean; toggleCollapsed: () => void }) => unknown) =>
        selector({ collapsed: false, toggleCollapsed: vi.fn() }),
}));
vi.mock("@/lib/api", () => ({ logoutAllAdmin: () => logoutAllAdmin() }));

import { Topbar } from "@/components/topbar";

async function openSignOutEverywhere() {
    const user = userEvent.setup();
    render(<Topbar />);
    await user.click(screen.getByRole("button", { name: /Ada/ }));
    await user.click(await screen.findByRole("menuitem", { name: /Sign out everywhere/ }));
    await screen.findByRole("alertdialog");
    return user;
}

describe("Topbar sign out everywhere", () => {
    beforeEach(() => {
        push.mockClear();
        logout.mockClear();
        logoutAllAdmin.mockReset().mockResolvedValue(undefined);
    });

    it("does nothing on Cancel and leaves the page clickable", async () => {
        const user = await openSignOutEverywhere();
        await user.click(screen.getByRole("button", { name: "Cancel" }));
        await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
        await waitFor(() => expect(document.body.style.pointerEvents).not.toBe("none"));
        expect(logoutAllAdmin).not.toHaveBeenCalled();
        expect(logout).not.toHaveBeenCalled();
    });

    it("signs out everywhere on confirm", async () => {
        const user = await openSignOutEverywhere();
        await user.click(screen.getByRole("button", { name: "Sign out everywhere" }));
        await waitFor(() => expect(logoutAllAdmin).toHaveBeenCalledTimes(1));
        await waitFor(() => expect(push).toHaveBeenCalledWith("/login"));
        expect(logout).toHaveBeenCalledTimes(1);
    });
});
