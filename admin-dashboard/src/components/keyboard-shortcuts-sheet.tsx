"use client";

import { Fragment, useEffect, useState } from "react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";

/**
 * "?" keyboard shortcut sheet (UX program W5.1). Mounted by
 * dashboard/layout.tsx only while `admin_command_palette_enabled` is on,
 * next to the command palette, so this component's keydown listener is
 * itself dark until a super admin flips that flag on the Settings page.
 *
 * Lists the shortcuts that exist in the code today — when adding one,
 * add its row here:
 * - Ctrl/Cmd+K and the palette's own keys: components/command-palette.tsx
 * - J/K/A/R/Esc: app/dashboard/drivers/_components/document-reviewer.tsx
 *   (used from Drivers, Drivers → Approvals and Licence Backfill)
 */

interface Shortcut {
    /** Alternative key combos ("Ctrl K" or "⌘ K"); each combo is its keys. */
    keys: string[][];
    description: string;
}

interface ShortcutSection {
    title: string;
    shortcuts: Shortcut[];
}

const SECTIONS: ShortcutSection[] = [
    {
        title: "Anywhere in the dashboard",
        shortcuts: [
            { keys: [["Ctrl", "K"], ["⌘", "K"]], description: "Open or close the page jumper" },
            { keys: [["?"]], description: "Show keyboard shortcuts" },
        ],
    },
    {
        title: "Page jumper",
        shortcuts: [
            { keys: [["↑"], ["↓"]], description: "Move through matching pages" },
            { keys: [["Enter"]], description: "Go to the highlighted page" },
            { keys: [["Esc"]], description: "Close the page jumper" },
        ],
    },
    {
        title: "Driver document reviewer",
        shortcuts: [
            { keys: [["J"]], description: "Next document" },
            { keys: [["K"]], description: "Previous document" },
            { keys: [["A"]], description: "Approve a pending document (press A again to confirm)" },
            { keys: [["R"]], description: "Reject a pending document (opens the reason box)" },
            { keys: [["Esc"]], description: "Close the reviewer" },
        ],
    },
];

/** Spoken names for keys whose glyph a screen reader reads poorly. */
const SPOKEN_KEY: Record<string, string> = { "⌘": "Command", "↑": "Up arrow", "↓": "Down arrow", "?": "Question mark" };

function Key({ name }: { name: string }) {
    const spoken = SPOKEN_KEY[name];
    return (
        <kbd className="inline-flex h-5 min-w-5 items-center justify-center rounded border bg-muted px-1.5 font-mono text-[11px] font-medium text-foreground">
            {spoken ? (
                <>
                    <span aria-hidden="true">{name}</span>
                    <span className="sr-only">{spoken}</span>
                </>
            ) : (
                name
            )}
        </kbd>
    );
}

/** True when the keystroke is going into a text field, where "?" is text. */
function isTypingTarget(target: EventTarget | null): boolean {
    if (!(target instanceof HTMLElement)) return false;
    const tag = target.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
    return target.isContentEditable || target.closest("[contenteditable]:not([contenteditable='false'])") !== null;
}

export function KeyboardShortcutsSheet() {
    const [open, setOpen] = useState(false);

    useEffect(() => {
        const onKeyDown = (e: KeyboardEvent) => {
            if (e.key !== "?" || e.ctrlKey || e.metaKey || e.altKey || e.defaultPrevented) return;
            if (isTypingTarget(e.target)) return;
            // Never stack over a dialog that is already open (the palette,
            // or the document reviewer, which runs its own focus trap).
            if (document.querySelector("[role='dialog'], [role='alertdialog']")) return;
            e.preventDefault();
            setOpen(true);
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, []);

    // Radix Dialog supplies the rest: focus moves in and is trapped while
    // open, Escape / the close button / an overlay click close it, and
    // focus returns to where it was.
    return (
        <Dialog open={open} onOpenChange={setOpen}>
            <DialogContent className="max-h-[85vh] overflow-y-auto">
                <DialogHeader>
                    <DialogTitle>Keyboard shortcuts</DialogTitle>
                    <DialogDescription>Shortcuts are off while you are typing in a field.</DialogDescription>
                </DialogHeader>
                {SECTIONS.map((section) => (
                    <section key={section.title}>
                        <h3 className="pb-1 text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                            {section.title}
                        </h3>
                        <dl className="divide-y">
                            {section.shortcuts.map((shortcut) => (
                                <div key={shortcut.description} className="flex items-center justify-between gap-4 py-1.5 text-sm">
                                    <dt className="text-foreground/80">{shortcut.description}</dt>
                                    <dd className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
                                        {shortcut.keys.map((combo, i) => (
                                            <Fragment key={combo.join("+")}>
                                                {i > 0 && <span>or</span>}
                                                {combo.map((k) => (
                                                    <Key key={k} name={k} />
                                                ))}
                                            </Fragment>
                                        ))}
                                    </dd>
                                </div>
                            ))}
                        </dl>
                    </section>
                ))}
            </DialogContent>
        </Dialog>
    );
}
