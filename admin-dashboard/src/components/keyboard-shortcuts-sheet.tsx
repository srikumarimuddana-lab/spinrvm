"use client";

import { Fragment, useEffect, useId, useRef, useState, useSyncExternalStore } from "react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";

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

// ── Single-key shortcuts setting (WCAG 2.1.4 Character Key Shortcuts) ──
// "?" is a one-character shortcut, so an admin must be able to turn it off
// (speech-input users can fire it by accident). Per admin and browser, in
// localStorage, default on. If storage is blocked (private window, site
// data off) the choice still holds for this page session, in memory.
const SINGLE_KEY_STORAGE_KEY = "spinr-admin-single-key-shortcuts";
const SINGLE_KEY_CHANGE_EVENT = "spinr:single-key-shortcuts-change";
const OPEN_EVENT = "spinr:open-keyboard-shortcuts";
let singleKeySessionValue = true;

function readSingleKeyShortcuts(): boolean {
    try {
        return localStorage.getItem(SINGLE_KEY_STORAGE_KEY) !== "off";
    } catch {
        return singleKeySessionValue;
    }
}

function writeSingleKeyShortcuts(on: boolean) {
    singleKeySessionValue = on;
    try {
        localStorage.setItem(SINGLE_KEY_STORAGE_KEY, on ? "on" : "off");
    } catch {
        // Blocked storage: the in-memory value above covers this session.
    }
    window.dispatchEvent(new Event(SINGLE_KEY_CHANGE_EVENT));
}

function subscribeSingleKeyShortcuts(onChange: () => void) {
    window.addEventListener(SINGLE_KEY_CHANGE_EVENT, onChange);
    window.addEventListener("storage", onChange); // changed in another tab
    return () => {
        window.removeEventListener(SINGLE_KEY_CHANGE_EVENT, onChange);
        window.removeEventListener("storage", onChange);
    };
}

/** Whether single-key shortcuts ("?") are on for this admin, and a setter. */
export function useSingleKeyShortcuts(): [boolean, (on: boolean) => void] {
    const enabled = useSyncExternalStore(subscribeSingleKeyShortcuts, readSingleKeyShortcuts, () => true);
    return [enabled, writeSingleKeyShortcuts];
}

/** Opens the sheet without "?" (the command palette's "Keyboard shortcuts"
 *  entry), so it stays reachable when single-key shortcuts are off. */
export function openKeyboardShortcuts() {
    window.dispatchEvent(new Event(OPEN_EVENT));
}

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
    const [singleKeyOn, setSingleKeyOn] = useSingleKeyShortcuts();
    const switchId = useId();
    // Radix Dialog returns focus to its DialogTrigger on close, and this
    // sheet has none (it opens from a key or the palette), so focus would
    // fall to <body>. Remember where it was and put it back ourselves.
    const returnFocusRef = useRef<HTMLElement | null>(null);

    useEffect(() => {
        const openSheet = () => {
            if (!returnFocusRef.current && document.activeElement instanceof HTMLElement) {
                returnFocusRef.current = document.activeElement;
            }
            setOpen(true);
        };
        const onKeyDown = (e: KeyboardEvent) => {
            if (e.key !== "?" || e.ctrlKey || e.metaKey || e.altKey || e.defaultPrevented) return;
            // Turned off with the switch below (WCAG 2.1.4). Read at key
            // time so the listener never works from a stale value.
            if (!readSingleKeyShortcuts()) return;
            if (isTypingTarget(e.target)) return;
            // Never stack over a dialog that is already open (the palette,
            // or the document reviewer, which runs its own focus trap).
            if (document.querySelector("[role='dialog'], [role='alertdialog']")) return;
            e.preventDefault();
            openSheet();
        };
        const onOpenRequest = () => openSheet();
        window.addEventListener("keydown", onKeyDown);
        window.addEventListener(OPEN_EVENT, onOpenRequest);
        return () => {
            window.removeEventListener("keydown", onKeyDown);
            window.removeEventListener(OPEN_EVENT, onOpenRequest);
        };
    }, []);

    // Radix Dialog supplies the rest: focus moves in and is trapped while
    // open, and Escape / the close button / an overlay click close it.
    return (
        <Dialog open={open} onOpenChange={setOpen}>
            <DialogContent
                className="max-h-[85vh] overflow-y-auto"
                onCloseAutoFocus={(e) => {
                    e.preventDefault();
                    returnFocusRef.current?.focus();
                    returnFocusRef.current = null;
                }}
            >
                <DialogHeader>
                    <DialogTitle>Keyboard shortcuts</DialogTitle>
                    <DialogDescription>Shortcuts are off while you are typing in a field.</DialogDescription>
                </DialogHeader>
                <div className="flex items-start justify-between gap-4 rounded-md border p-3">
                    <div className="space-y-1">
                        <Label htmlFor={switchId}>Single-key shortcuts</Label>
                        <p id={`${switchId}-hint`} className="text-xs text-muted-foreground">
                            When off, ? does nothing. Open this list from the page jumper (Ctrl+K or Cmd+K)
                            instead. The document reviewer&apos;s letter keys only work inside the reviewer.
                        </p>
                    </div>
                    <Switch
                        id={switchId}
                        checked={singleKeyOn}
                        onCheckedChange={setSingleKeyOn}
                        aria-describedby={`${switchId}-hint`}
                    />
                </div>
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
