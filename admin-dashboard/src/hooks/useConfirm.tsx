"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { buttonVariants } from "@/components/ui/button";

export interface ConfirmOptions {
    title: string;
    /** Plain text; "\n" line breaks are kept. */
    description?: string;
    confirmLabel?: string;
    cancelLabel?: string;
    /** Red confirm button, for actions that delete, cancel or expose data. */
    destructive?: boolean;
}

/**
 * In-app replacement for window.confirm(). `await confirm(options)` opens an
 * AlertDialog and resolves true only on the confirm button. Cancel, Escape, a
 * newer confirm() call and unmounting all resolve false, so a caller written
 * as `if (!(await confirm(...))) return;` keeps window.confirm()'s control flow.
 * Render `dialog` once in the calling component.
 */
export function useConfirm() {
    const [open, setOpen] = useState(false);
    // Kept after closing so the exit animation doesn't render an empty dialog.
    const [options, setOptions] = useState<ConfirmOptions | null>(null);
    const resolverRef = useRef<((ok: boolean) => void) | null>(null);

    const settle = useCallback((ok: boolean) => {
        resolverRef.current?.(ok);
        resolverRef.current = null;
        setOpen(false);
    }, []);

    const confirm = useCallback((next: ConfirmOptions) => {
        resolverRef.current?.(false);
        setOptions(next);
        setOpen(true);
        return new Promise<boolean>((resolve) => {
            resolverRef.current = resolve;
        });
    }, []);

    useEffect(() => () => resolverRef.current?.(false), []);

    const dialog = (
        <AlertDialog open={open} onOpenChange={(next) => { if (!next) settle(false); }}>
            <AlertDialogContent
                // Long previews (e.g. statement totals) scroll instead of pushing the buttons off-screen.
                className="max-h-[90vh] overflow-y-auto"
                {...(options?.description ? {} : { "aria-describedby": undefined })}
            >
                <AlertDialogHeader>
                    <AlertDialogTitle>{options?.title}</AlertDialogTitle>
                    {options?.description && (
                        <AlertDialogDescription className="whitespace-pre-line">
                            {options.description}
                        </AlertDialogDescription>
                    )}
                </AlertDialogHeader>
                <AlertDialogFooter>
                    <AlertDialogCancel>{options?.cancelLabel ?? "Cancel"}</AlertDialogCancel>
                    <AlertDialogAction
                        onClick={() => settle(true)}
                        // The Button's own destructive variant: `text-destructive-foreground` isn't a
                        // theme colour, so that class would drop the button's white text.
                        className={options?.destructive ? buttonVariants({ variant: "destructive" }) : undefined}
                    >
                        {options?.confirmLabel ?? "Confirm"}
                    </AlertDialogAction>
                </AlertDialogFooter>
            </AlertDialogContent>
        </AlertDialog>
    );

    return { confirm, dialog };
}
