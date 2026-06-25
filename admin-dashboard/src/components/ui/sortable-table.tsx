"use client";

import { useCallback, useMemo, useState } from "react";
import { ChevronUp, ChevronDown, ChevronsUpDown } from "lucide-react";
import { TableHead } from "@/components/ui/table";
import { cn } from "@/lib/utils";

export type SortDir = "asc" | "desc";
export interface SortState {
    key: string | null;
    dir: SortDir;
}

function _get(row: Record<string, any>, key: string): any {
    // Supports dot paths, e.g. "user.first_name".
    return key.split(".").reduce<any>((o, k) => (o == null ? o : o[k]), row);
}

/**
 * Client-side sort for the rows currently loaded in a table.
 *
 * Returns a sorted COPY (never mutates the input), so it composes with any
 * existing filtering/pagination — pass the already-filtered rows in. Comparison
 * is type-aware: numeric when both values parse as numbers, otherwise a
 * locale/numeric-aware string compare; nulls/blanks always sort last. Sorting
 * only reorders the rows already on screen (it does not page across the server).
 */
export function useTableSort<T extends Record<string, any>>(rows: T[], initial?: SortState) {
    const [sort, setSort] = useState<SortState>(initial ?? { key: null, dir: "asc" });

    const toggle = useCallback((key: string) => {
        setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));
    }, []);

    const sorted = useMemo(() => {
        if (!sort.key) return rows;
        const key = sort.key;
        const dir = sort.dir === "asc" ? 1 : -1;
        return [...rows].sort((a, b) => {
            const av = _get(a, key);
            const bv = _get(b, key);
            const aEmpty = av == null || av === "";
            const bEmpty = bv == null || bv === "";
            if (aEmpty && bEmpty) return 0;
            if (aEmpty) return 1; // nulls/blanks last, regardless of direction
            if (bEmpty) return -1;
            const an = typeof av === "number" ? av : Number(av);
            const bn = typeof bv === "number" ? bv : Number(bv);
            if (!Number.isNaN(an) && !Number.isNaN(bn)) return (an - bn) * dir;
            return String(av).localeCompare(String(bv), undefined, { numeric: true }) * dir;
        });
    }, [rows, sort]);

    return { sorted, sort, toggle, setSort };
}

/**
 * Clickable, sortable column header — a drop-in replacement for <TableHead>.
 *
 *   <SortableHead column="created_at" sort={sort} onSort={toggle}>Date</SortableHead>
 *
 * `column` is the row field key to sort by (dot paths allowed). `align="right"`
 * matches numeric/right-aligned columns. Pass the `sort`/`onSort` from useTableSort.
 */
export function SortableHead({
    column,
    sort,
    onSort,
    className,
    align = "left",
    children,
}: {
    column: string;
    sort: SortState;
    onSort: (key: string) => void;
    className?: string;
    align?: "left" | "right" | "center";
    children: React.ReactNode;
}) {
    const active = sort.key === column;
    const Icon = !active ? ChevronsUpDown : sort.dir === "asc" ? ChevronUp : ChevronDown;
    return (
        <TableHead className={cn(align === "right" && "text-right", align === "center" && "text-center", className)}>
            <button
                type="button"
                onClick={() => onSort(column)}
                aria-label={`Sort by ${typeof children === "string" ? children : column}`}
                className={cn(
                    "inline-flex items-center gap-1 select-none transition-colors hover:text-foreground",
                    align === "right" && "flex-row-reverse",
                    align === "center" && "justify-center",
                    active ? "font-semibold text-foreground" : "text-muted-foreground",
                )}
            >
                {children}
                <Icon className="h-3.5 w-3.5 shrink-0 opacity-70" />
            </button>
        </TableHead>
    );
}
