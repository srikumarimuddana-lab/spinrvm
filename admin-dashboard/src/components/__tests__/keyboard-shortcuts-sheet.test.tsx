/**
 * UX program W5.1: "?" opens the keyboard shortcut sheet — a titled,
 * focus-trapping dialog that Escape closes — and is ignored while the
 * admin is typing in a field or another dialog is already open.
 */
import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { KeyboardShortcutsSheet } from "@/components/keyboard-shortcuts-sheet";

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
