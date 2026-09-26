/**
 * UX program W5.1: "?" opens the keyboard shortcut sheet — a titled,
 * focus-trapping dialog that Escape closes — and is ignored while the
 * admin is typing in a field or another dialog is already open.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { KeyboardShortcutsSheet, openKeyboardShortcuts } from "@/components/keyboard-shortcuts-sheet";

function setup(extra?: React.ReactNode) {
    const user = userEvent.setup();
    render(
        <>
            <button type="button">Page button</button>
            {extra}
            <KeyboardShortcutsSheet />
        </>,
    );
    return user;
}

beforeEach(() => localStorage.clear());

describe("KeyboardShortcutsSheet", () => {
    it("opens on ? as a titled dialog listing the existing shortcuts", async () => {
        const user = setup();
        expect(screen.queryByRole("dialog")).toBeNull();
        await user.keyboard("?");
        const dialog = await screen.findByRole("dialog", { name: "Keyboard shortcuts" });
        expect(dialog).toHaveTextContent("Open or close the page jumper");
        expect(dialog).toHaveTextContent("Go to the highlighted page");
        expect(dialog).toHaveTextContent("Approve a pending document");
        // Glyph keys carry a spoken name for screen readers.
        expect(dialog).toHaveTextContent("Command");
    });

    it("moves focus in, traps Tab inside, and Escape closes it", async () => {
        const user = setup();
        screen.getByRole("button", { name: "Page button" }).focus();
        await user.keyboard("?");
        const dialog = await screen.findByRole("dialog", { name: "Keyboard shortcuts" });
        await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
        for (let i = 0; i < 3; i++) {
            await user.tab();
            expect(dialog.contains(document.activeElement)).toBe(true);
        }
        expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
        await user.keyboard("{Escape}");
        await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    });

    it("returns focus to the previously focused element after Escape", async () => {
        const user = setup();
        const pageButton = screen.getByRole("button", { name: "Page button" });
        pageButton.focus();
        await user.keyboard("?");
        const dialog = await screen.findByRole("dialog", { name: "Keyboard shortcuts" });
        await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
        await user.keyboard("{Escape}");
        await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
        await waitFor(() => expect(pageButton).toHaveFocus());
    });

    it.each([
        ["an input", <input key="f" aria-label="field" />],
        ["a textarea", <textarea key="f" aria-label="field" />],
        ["a contenteditable", <div key="f" aria-label="field" role="textbox" contentEditable suppressContentEditableWarning />],
    ])("ignores ? typed into %s", async (_label, field) => {
        const user = setup(field);
        await user.click(screen.getByRole("textbox", { name: "field" }));
        await user.keyboard("?");
        expect(screen.queryByRole("dialog")).toBeNull();
    });

    it("ignores ? while another dialog is already open", async () => {
        const user = setup(<div role="dialog" aria-label="Document reviewer" />);
        await user.keyboard("?");
        expect(screen.queryByRole("dialog", { name: "Keyboard shortcuts" })).toBeNull();
    });

    it("ignores ? with Ctrl or Meta held", async () => {
        const user = setup();
        await user.keyboard("{Control>}?{/Control}");
        await user.keyboard("{Meta>}?{/Meta}");
        expect(screen.queryByRole("dialog")).toBeNull();
    });
});

describe("single-key shortcuts switch (WCAG 2.1.4)", () => {
    const STORAGE_KEY = "spinr-admin-single-key-shortcuts";

    it("is on by default; switching it off stops ? from opening the sheet", async () => {
        const user = setup();
        await user.keyboard("?");
        const toggle = await screen.findByRole("switch", { name: "Single-key shortcuts" });
        expect(toggle).toHaveAttribute("aria-checked", "true");
        await user.click(toggle);
        expect(toggle).toHaveAttribute("aria-checked", "false");
        expect(localStorage.getItem(STORAGE_KEY)).toBe("off");
        await user.keyboard("{Escape}");
        await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

        await user.keyboard("?");
        expect(screen.queryByRole("dialog")).toBeNull();
    });

    it("survives a remount, and the sheet still opens without ?", async () => {
        localStorage.setItem(STORAGE_KEY, "off");
        const user = userEvent.setup();
        const first = render(<KeyboardShortcutsSheet />);
        first.unmount();
        render(<KeyboardShortcutsSheet />);

        await user.keyboard("?");
        expect(screen.queryByRole("dialog")).toBeNull();

        act(() => openKeyboardShortcuts());
        await screen.findByRole("dialog", { name: "Keyboard shortcuts" });
        const toggle = screen.getByRole("switch", { name: "Single-key shortcuts" });
        expect(toggle).toHaveAttribute("aria-checked", "false");
        // Switching back on works from the same place.
        await user.click(toggle);
        expect(localStorage.getItem(STORAGE_KEY)).toBe("on");
    });

    it("defaults to on and still switches off for the session when storage is blocked", async () => {
        const blocked = () => {
            throw new Error("storage blocked");
        };
        const getItem = vi.spyOn(window.localStorage, "getItem").mockImplementation(blocked);
        const setItem = vi.spyOn(window.localStorage, "setItem").mockImplementation(blocked);
        try {
            const user = setup();
            await user.keyboard("?");
            const toggle = await screen.findByRole("switch", { name: "Single-key shortcuts" });
            expect(toggle).toHaveAttribute("aria-checked", "true");
            await user.click(toggle);
            expect(toggle).toHaveAttribute("aria-checked", "false");
            await user.keyboard("{Escape}");
            await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
            await user.keyboard("?");
            expect(screen.queryByRole("dialog")).toBeNull();
        } finally {
            getItem.mockRestore();
            setItem.mockRestore();
        }
    });
});
