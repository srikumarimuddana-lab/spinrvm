"use client";

import { useCallback, useMemo, useState } from "react";
import type { DataTransferEntityRow, DataTransferSearchParams } from "@/lib/api";

/**
 * Shared selection state for the Data Transfer module's Search/Export/SGI
 * Forms tabs. Supports two selection modes:
 *   - explicit: a Set of individually checked row IDs (the common case)
 *   - selectAllMatching: every row matching the *current search filter*,
 *     not just the currently-loaded page — used by "select all N matching
 *     this filter" so a 5,000-row filter doesn't require paging through
 *     100 pages checking boxes one at a time.
 *
 * selectAllMatching stores the filter criteria itself (not resolved IDs);
 * consumers (Export/SGI tabs) pass that filter to the backend and let it
 * resolve the full ID set server-side at request time.
 */
export interface EntitySelectionState {
    selectedIds: Set<string>;
    selectAllMatching: DataTransferSearchParams | null;
    isSelected: (id: string) => boolean;
    toggle: (id: string) => void;
    toggleAll: (rows: DataTransferEntityRow[]) => void;
    selectAllMatchingFilter: (params: DataTransferSearchParams) => void;
    clear: () => void;
    selectionCount: (totalMatchingFilter: number) => number;
}

export function useEntitySelection(): EntitySelectionState {
    const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
    const [selectAllMatching, setSelectAllMatching] = useState<DataTransferSearchParams | null>(null);

    const isSelected = useCallback(
        (id: string) => selectAllMatching !== null || selectedIds.has(id),
        [selectedIds, selectAllMatching],
    );

    const toggle = useCallback((id: string) => {
        setSelectAllMatching(null); // an individual toggle after "select all" narrows back to explicit mode
        setSelectedIds((prev) => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    }, []);

    const toggleAll = useCallback((rows: DataTransferEntityRow[]) => {
        setSelectAllMatching(null);
        setSelectedIds((prev) => {
            const allSelected = rows.length > 0 && rows.every((r) => prev.has(r.id));
            if (allSelected) {
                const next = new Set(prev);
                rows.forEach((r) => next.delete(r.id));
                return next;
            }
            const next = new Set(prev);
            rows.forEach((r) => next.add(r.id));
            return next;
        });
    }, []);

    const selectAllMatchingFilter = useCallback((params: DataTransferSearchParams) => {
        setSelectedIds(new Set());
        setSelectAllMatching(params);
    }, []);

    const clear = useCallback(() => {
        setSelectedIds(new Set());
        setSelectAllMatching(null);
    }, []);

    const selectionCount = useCallback(
        (totalMatchingFilter: number) => (selectAllMatching !== null ? totalMatchingFilter : selectedIds.size),
        [selectAllMatching, selectedIds],
    );

    return useMemo(
        () => ({
            selectedIds,
            selectAllMatching,
            isSelected,
            toggle,
            toggleAll,
            selectAllMatchingFilter,
            clear,
            selectionCount,
        }),
        [selectedIds, selectAllMatching, isSelected, toggle, toggleAll, selectAllMatchingFilter, clear, selectionCount],
    );
}
