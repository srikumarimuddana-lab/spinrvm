/**
 * UX program W2.1: useConfirm() replaces window.confirm(). Callers written as
 * `if (!(await confirm(...))) return;` must only proceed on the confirm
 * button; every other way out of the dialog resolves false.
 */
import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useConfirm, type ConfirmOptions } from "@/hooks/useConfirm";

const results: boolean[] = [];

function Harness({ options }: { options: ConfirmOptions }) {
    const { confirm, dialog } = useConfirm();
    return (
        <>
            <button onClick={async () => results.push(await confirm(options))}>Open</button>
            {dialog}
        </>
    );
}

async function openDialog(options: ConfirmOptions = { title: "Delete venue?", confirmLabel: "Delete" }) {
    results.length = 0;
    const user = userEvent.setup();
    const view = render(<Harness options={options} />);
    await user.click(screen.getByRole("button", { name: "Open" }));
    await screen.findByRole("alertdialog");
    return { user, view };
}

describe("useConfirm", () => {
    it("resolves true on the confirm button", async () => {
        const { user } = await openDialog();
        await user.click(screen.getByRole("button", { name: "Delete" }));
        await waitFor(() => expect(results).toEqual([true]));
    });

    it("resolves false on Cancel", async () => {
        const { user } = await openDialog();
        await user.click(screen.getByRole("button", { name: "Cancel" }));
        await waitFor(() => expect(results).toEqual([false]));
    });

    it("resolves false on Escape", async () => {
        const { user } = await openDialog();
        await user.keyboard("{Escape}");
        await waitFor(() => expect(results).toEqual([false]));
    });

    it("focuses Cancel, so Enter does not confirm", async () => {
        const { user } = await openDialog();
        expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
        await user.keyboard("{Enter}");
        await waitFor(() => expect(results).toEqual([false]));
    });

    it("resolves false when the component unmounts while open", async () => {
        const { view } = await openDialog();
        view.unmount();
        await waitFor(() => expect(results).toEqual([false]));
    });

    it("shows the title, the description with line breaks, and a destructive button", async () => {
        await openDialog({ title: "Reveal SIN?", description: "Line one\n\nLine two", confirmLabel: "Reveal", destructive: true });
        expect(screen.getByRole("alertdialog")).toHaveAccessibleName("Reveal SIN?");
        expect(screen.getByText(/Line one/)).toHaveClass("whitespace-pre-line");
        expect(screen.getByRole("button", { name: "Reveal" })).toHaveClass("bg-destructive");
    });
});
