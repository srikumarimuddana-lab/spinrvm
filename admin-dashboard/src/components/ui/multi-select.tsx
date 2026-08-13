"use client";

import * as React from "react";
import { ChevronDownIcon } from "lucide-react";

import {
    DropdownMenu,
    DropdownMenuCheckboxItem,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

export interface MultiSelectOption {
    value: string;
    label: string;
}

/**
 * Checkbox-list dropdown for picking zero-or-more options — the multi-value
 * counterpart to `<Select>`, which Radix's Select primitive cannot do (it is
 * single-value by design). Built on DropdownMenuCheckboxItem rather than a new
 * primitive so it inherits the kit's existing focus/keyboard/portal behaviour
 * and needs no new dependency (the admin kit has no Command/Popover).
 *
 * Empty selection means "no filter applied", rendered as `allLabel` — the
 * distinction matters on filter surfaces, where zero ticked boxes must read as
 * "everything" and never as "nothing".
 *
 * Trigger styling deliberately mirrors ui/select.tsx's SelectTrigger so a
 * multi-select sitting in the same filter row as single-selects doesn't read
 * as a different kind of control.
 */
export function MultiSelect({
    options,
    selected,
    onChange,
    allLabel = "All",
    itemNoun = "selected",
    ariaLabel,
    className,
    disabled,
    loading,
}: {
    options: MultiSelectOption[];
    selected: string[];
    onChange: (next: string[]) => void;
    /** Shown on the trigger when nothing is selected, i.e. "no filter". */
    allLabel?: string;
    /** Pluralised in the trigger summary, e.g. "2 areas selected". */
    itemNoun?: string;
    ariaLabel?: string;
    className?: string;
    disabled?: boolean;
    loading?: boolean;
}) {
    const selectedSet = React.useMemo(() => new Set(selected), [selected]);

    const toggle = (value: string) => {
        // Preserve `options` order rather than click order, so the trigger
        // summary and the value handed to callers are stable regardless of
        // the order an admin ticked the boxes.
        const next = options.map((o) => o.value).filter((v) => (v === value ? !selectedSet.has(v) : selectedSet.has(v)));
        onChange(next);
    };

    const summary = React.useMemo(() => {
        if (selected.length === 0) return allLabel;
        if (selected.length === 1) {
            return options.find((o) => o.value === selected[0])?.label ?? `1 ${itemNoun}`;
        }
        return `${selected.length} ${itemNoun}`;
    }, [selected, options, allLabel, itemNoun]);

    return (
        <DropdownMenu>
            <DropdownMenuTrigger
                disabled={disabled || loading}
                // aria-label carries the control's name; the visible trigger
                // text is the current value, so a screen reader announces
                // both rather than just "3 areas".
                aria-label={ariaLabel}
                className={cn(
                    "border-input focus-visible:border-ring focus-visible:ring-ring/50 dark:bg-input/30 dark:hover:bg-input/50",
                    "flex h-9 w-fit items-center justify-between gap-2 rounded-md border bg-transparent px-3 py-2 text-sm",
                    "whitespace-nowrap shadow-xs transition-[color,box-shadow] outline-none focus-visible:ring-[3px]",
                    "disabled:cursor-not-allowed disabled:opacity-50",
                    selected.length === 0 && "text-muted-foreground",
                    className,
                )}
            >
                <span className="line-clamp-1 text-left">{loading ? "Loading…" : summary}</span>
                <ChevronDownIcon className="size-4 shrink-0 opacity-50" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="max-h-72 w-56 overflow-y-auto">
                {options.length === 0 ? (
                    <DropdownMenuItem disabled>No options available</DropdownMenuItem>
                ) : (
                    <>
                        <DropdownMenuItem
                            disabled={selected.length === 0}
                            onSelect={() => onChange([])}
                            className="text-xs"
                        >
                            Clear selection ({allLabel})
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        {options.map((o) => (
                            <DropdownMenuCheckboxItem
                                key={o.value}
                                checked={selectedSet.has(o.value)}
                                // Radix closes the menu on select by default,
                                // which would force a reopen per option and
                                // make a multi-select feel broken.
                                onSelect={(e) => e.preventDefault()}
                                onCheckedChange={() => toggle(o.value)}
                            >
                                {o.label}
                            </DropdownMenuCheckboxItem>
                        ))}
                    </>
                )}
            </DropdownMenuContent>
        </DropdownMenu>
    );
}
