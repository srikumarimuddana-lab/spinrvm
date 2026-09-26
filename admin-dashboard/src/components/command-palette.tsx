"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useAuthStore } from "@/store/authStore";
import { getCommandPaletteRoutes, type CommandPaletteRoute } from "@/lib/command-palette-routes";
import { openKeyboardShortcuts, useSingleKeyShortcuts } from "@/components/keyboard-shortcuts-sheet";
import { cn } from "@/lib/utils";

/**
 * Cmd+K / Ctrl+K route jumper, gated behind `admin_command_palette_enabled`
 * (see hooks/useFeatureFlag.tsx). Only mounted by dashboard/layout.tsx when
 * the flag is on, so the keybinding listener below is itself dark until a
 * super admin flips the flag on the Settings page.
 *
 * Routes come from lib/admin-nav-config.ts — the sidebar's own config and
 * visibility rules (W5.1) — so the palette never surfaces a route the
 * sidebar hides from a given admin.
 */

/** A palette row that does something other than open a page. */
interface PaletteAction {
    action: "keyboard-shortcuts";
    label: string;
    group: string;
}
type PaletteItem = CommandPaletteRoute | PaletteAction;

// Listed after the sidebar's routes, under their own heading.
const PALETTE_ACTIONS: PaletteAction[] = [
    // Keeps the shortcut sheet reachable when single-key shortcuts ("?")
    // are switched off (WCAG 2.1.4).
    { action: "keyboard-shortcuts", label: "Keyboard shortcuts", group: "Help" },
];

const itemKey = (item: PaletteItem) => ("href" in item ? item.href : item.action);

// Simple case-insensitive scorer: exact/prefix/substring match first, then a
// typo-tolerant subsequence fallback. No fuzzy-matching library — a request
// this small doesn't need one.
function fuzzyScore(query: string, target: string): number {
    const q = query.trim().toLowerCase();
    const t = target.toLowerCase();
    if (!q) return 0;
    if (t === q) return 1000;
    if (t.startsWith(q)) return 800;
    const idx = t.indexOf(q);
    if (idx !== -1) return 600 - idx;

    let qi = 0;
    let hits = 0;
    for (let ti = 0; ti < t.length && qi < q.length; ti++) {
        if (t[ti] === q[qi]) {
            qi++;
            hits++;
        }
    }
    return qi === q.length ? hits : -1;
}

export function CommandPalette() {
    const router = useRouter();
    const { user } = useAuthStore();
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState("");
    const [activeIndex, setActiveIndex] = useState(0);
    const inputRef = useRef<HTMLInputElement>(null);
    // Radix Dialog returns focus to its DialogTrigger on close, and the
    // palette has none (it opens from Ctrl/Cmd+K), so focus used to fall to
    // <body>. Remember where it was and put it back ourselves.
    const returnFocusRef = useRef<HTMLElement | null>(null);
    // Set when an action row is chosen; run once the palette has closed and
    // put focus back (onCloseAutoFocus below), so a dialog the action opens
    // records that element as the place to return to.
    const pendingActionRef = useRef<(() => void) | null>(null);
    const [singleKeyOn] = useSingleKeyShortcuts();

    const isSuperAdmin = user?.role === "super_admin";
    const userModules = useMemo(() => user?.modules ?? [], [user?.modules]);

    // The routes the sidebar shows this admin, in sidebar order.
    const visibleRoutes = useMemo(
        () => getCommandPaletteRoutes({ isSuperAdmin, modules: userModules }),
        [isSuperAdmin, userModules]
    );

    const items = useMemo<PaletteItem[]>(() => [...visibleRoutes, ...PALETTE_ACTIONS], [visibleRoutes]);

    const ranked = useMemo(() => {
        if (!query.trim()) return items;
        return items
            .map((route) => ({ route, score: fuzzyScore(query, `${route.group} ${route.label}`) }))
            .filter((r) => r.score >= 0)
            .sort((a, b) => b.score - a.score)
            .map((r) => r.route);
    }, [query, items]);

    // Group results for display while preserving each group's own order.
    const grouped = useMemo(() => {
        const map = new Map<string, PaletteItem[]>();
        for (const r of ranked) {
            const key = r.group;
            if (!map.has(key)) map.set(key, []);
            map.get(key)!.push(r);
        }
        return Array.from(map.entries());
    }, [ranked]);

    // The rows in the order they render. `activeIndex` indexes this list for
    // the highlight, arrow keys, aria-activedescendant AND Enter. Enter used
    // to index the score-sorted list instead, which interleaves sections
    // (e.g. "set": Settings, Live Monitoring, ...), so it could open a
    // different row than the one highlighted.
    const results = useMemo(() => grouped.flatMap(([, items]) => items), [grouped]);

    // Opening (whether via the shortcut or Radix's own onOpenChange, e.g.
    // Escape/overlay-click) always starts from a blank query. Handled here
    // as a plain event-callback, not an effect reacting to `open` state, so
    // there's no cascading-render setState-in-effect.
    const handleOpenChange = (next: boolean) => {
        if (next) {
            setQuery("");
            setActiveIndex(0);
            if (!returnFocusRef.current && document.activeElement instanceof HTMLElement) {
                returnFocusRef.current = document.activeElement;
            }
        }
        setOpen(next);
    };

    // Tracks `open` for the keydown listener below without re-subscribing
    // it on every toggle — the listener itself is registered once on mount.
    const openRef = useRef(open);
    useEffect(() => {
        openRef.current = open;
    }, [open]);

    useEffect(() => {
        const onKeyDown = (e: KeyboardEvent) => {
            if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
                e.preventDefault();
                handleOpenChange(!openRef.current);
            }
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, []);

    const navigate = (href: string) => {
        setOpen(false);
        router.push(href);
    };

    const select = (item: PaletteItem) => {
        if ("href" in item) {
            navigate(item.href);
            return;
        }
        pendingActionRef.current = openKeyboardShortcuts;
        setOpen(false);
    };

    const onInputKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
        if (e.key === "ArrowDown") {
            e.preventDefault();
            setActiveIndex((i) => Math.min(i + 1, results.length - 1));
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActiveIndex((i) => Math.max(i - 1, 0));
        } else if (e.key === "Enter") {
            e.preventDefault();
            const target = results[activeIndex];
            if (target) select(target);
        }
        // Escape is handled by Radix Dialog's default close-on-Escape behavior.
    };

    let flatIndex = -1;

    return (
        <Dialog open={open} onOpenChange={handleOpenChange}>
            <DialogContent
                showCloseButton={false}
                className="top-[20%] max-w-lg translate-y-0 gap-0 overflow-hidden p-0"
                onOpenAutoFocus={(e) => {
                    e.preventDefault();
                    inputRef.current?.focus();
                }}
                onCloseAutoFocus={(e) => {
                    e.preventDefault();
                    returnFocusRef.current?.focus();
                    returnFocusRef.current = null;
                    const run = pendingActionRef.current;
                    pendingActionRef.current = null;
                    run?.();
                }}
            >
                <DialogTitle className="sr-only">Jump to a page</DialogTitle>
                <div className="flex items-center border-b px-3">
                    <Input
                        ref={inputRef}
                        value={query}
                        onChange={(e) => {
                            setQuery(e.target.value);
                            setActiveIndex(0);
                        }}
                        onKeyDown={onInputKeyDown}
                        placeholder="Jump to a page..."
                        aria-label="Search admin dashboard pages"
                        aria-controls="command-palette-results"
                        aria-activedescendant={results[activeIndex] ? `command-palette-option-${activeIndex}` : undefined}
                        className="h-12 border-0 px-0 shadow-none focus-visible:ring-0"
                    />
                </div>
                <div
                    id="command-palette-results"
                    role="listbox"
                    aria-label="Matching pages"
                    className="max-h-80 overflow-y-auto py-2"
                >
                    {results.length === 0 && (
                        <p className="px-4 py-6 text-center text-sm text-muted-foreground">No matching pages.</p>
                    )}
                    {grouped.map(([group, items]) => (
                        <div key={group || "general"} className="px-2 py-1">
                            {group && (
                                <p className="px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                                    {group}
                                </p>
                            )}
                            {items.map((item) => {
                                flatIndex += 1;
                                const idx = flatIndex;
                                return (
                                    <button
                                        key={itemKey(item)}
                                        id={`command-palette-option-${idx}`}
                                        type="button"
                                        role="option"
                                        aria-selected={idx === activeIndex}
                                        onMouseEnter={() => setActiveIndex(idx)}
                                        onClick={() => select(item)}
                                        className={cn(
                                            "flex w-full items-center rounded-md px-2.5 py-2 text-left text-sm transition-colors",
                                            idx === activeIndex
                                                ? "bg-accent text-accent-foreground"
                                                : "text-foreground/80 hover:bg-accent hover:text-accent-foreground"
                                        )}
                                    >
                                        {item.label}
                                    </button>
                                );
                            })}
                        </div>
                    ))}
                </div>
                {/* Where to find the shortcut list: "?" while single-key
                    shortcuts are on, otherwise this palette's own entry. */}
                <p className="border-t px-3 py-2 text-xs text-muted-foreground">
                    {singleKeyOn ? "Press ? for shortcuts" : <>Type &ldquo;shortcuts&rdquo; to see keyboard shortcuts</>}
                </p>
            </DialogContent>
        </Dialog>
    );
}
