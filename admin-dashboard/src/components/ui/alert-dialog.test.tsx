/**
 * An AlertDialog opened inside a Sheet (e.g. deleting a driver note in the
 * driver detail sheet) must take focus, and Escape must close only the
 * dialog. Both break when AlertDialog and Sheet use different copies of
 * Radix's focus-scope and dismissable-layer; alert-dialog.tsx imports from
 * "radix-ui" like sheet.tsx so they share one.
 */
import { describe, it, expect } from "vitest";
import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";

let sheetClosed = false;
let deleted = false;

function DriverSheetWithDelete() {
    const [confirmOpen, setConfirmOpen] = useState(false);
    const [sheetOpen, setSheetOpen] = useState(true);
    return (
        <Sheet open={sheetOpen} onOpenChange={(open) => { setSheetOpen(open); if (!open) sheetClosed = true; }}>
            <SheetContent>
                <SheetTitle>Driver</SheetTitle>
                <SheetDescription>Details</SheetDescription>
                <button onClick={() => setConfirmOpen(true)}>Delete note</button>
                <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
                    <AlertDialogContent>
                        <AlertDialogTitle>Delete this note?</AlertDialogTitle>
                        <AlertDialogDescription>This cannot be undone.</AlertDialogDescription>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={() => { deleted = true; }}>Delete</AlertDialogAction>
                    </AlertDialogContent>
                </AlertDialog>
            </SheetContent>
        </Sheet>
    );
}

async function openConfirm() {
    sheetClosed = false;
    deleted = false;
    const user = userEvent.setup();
    render(<DriverSheetWithDelete />);
    await user.click(screen.getByRole("button", { name: "Delete note" }));
    await screen.findByRole("alertdialog");
    return user;
}

describe("AlertDialog inside a Sheet", () => {
    it("moves focus into the dialog", async () => {
        await openConfirm();
        await waitFor(() => expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus());
    });

    it("closes only the dialog on Escape", async () => {
        const user = await openConfirm();
        await user.keyboard("{Escape}");
        await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
        expect(screen.getByRole("dialog", { name: "Driver" })).toBeInTheDocument();
        expect(sheetClosed).toBe(false);
    });

    it("runs the action and keeps the sheet open", async () => {
        const user = await openConfirm();
        await user.click(screen.getByRole("button", { name: "Delete" }));
        await waitFor(() => expect(deleted).toBe(true));
        expect(sheetClosed).toBe(false);
    });
});
