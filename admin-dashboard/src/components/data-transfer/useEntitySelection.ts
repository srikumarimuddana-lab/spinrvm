"use client";

import { useCallback, useMemo, useState } from "react";
import type { DataTransferEntityRow, DataTransferExportEntityRef, DataTransferSearchParams } from "@/lib/api";

/** Resolve a search-result row's entity type. Driver-scoped searches return
 * drivers-table rows (no `role` column selected); rider/all searches return
 * users-table rows where `role` is authoritative. */
export function inferEntityType(row: DataTransferEntityRow): "driver" | "rider" {
    if (row.role === "driver") return "driver";
    if (row.role) return "rider";
    // No role column at all means this came from the drivers-scoped query.
    return "driver";
}

/**
 * Shared selection state for the Data Transfer module's Search/Export/SGI
 * Forms tabs. Supports two selection modes:
 *   - explicit: a Map of individually checked rows, keyed by id, storing the
 *     resolved {entity_type, entity_id} the Export/SGI-forms tabs need
 *     directly — avoids re-deriving type from an id elsewhere.
 *   - selectAllMatching: every row matching the *current search filter*,
 *     not just the currently-loaded page — used by "select all N matching
 *     this filter" so a 5,000-row filter doesn't require paging through
 *     100 pages checking boxes one at a time.
 *
 * selectAllMatching stores the filter criteria itself (not resolved refs);
 * consumers (Export/SGI tabs) re-query the search endpoint with this filter
 * to resolve the full ref set at request time.
 */
export interface EntitySelectionState {
    selectedRefs: Map<string, DataTransferExportEntityRef>;
    selectAllMatching: DataTransferSearchParams | null;
    isSelected: (id: string) => boolean;
    toggle: (row: DataTransferEntityRow) => void;
    toggleAll: (rows: DataTransferEntityRow[]) => void;
    selectAllMatchingFilter: (params: DataTransferSearchParams) => void;
    clear: () => void;
    selectionCount: (totalMatchingFilter: number) => number;
}

export function useEntitySelection(): EntitySelectionState {
    const [selectedRefs, setSelectedRefs] = useState<Map<string, DataTransferExportEntityRef>>(new Map());
    const [selectAllMatching, setSelectAllMatching] = useState<DataTransferSearchParams | null>(null);

    const isSelected = useCallback(
        (id: string) => selectAllMatching !== null || selectedRefs.has(id),
        [selectedRefs, selectAllMatching],
    );

    const toggle = useCallback((row: DataTransferEntityRow) => {
        setSelectAllMatching(null); // an individual toggle after "select all" narrows back to explicit mode
        setSelectedRefs((prev) => {
            const next = new Map(prev);
            if (next.has(row.id)) next.delete(row.id);
            else next.set(row.id, { entity_type: inferEntityType(row), entity_id: row.id });
            return next;
        });
    }, []);

    const toggleAll = useCallback((rows: DataTransferEntityRow[]) => {
        setSelectAllMatching(null);
        setSelectedRefs((prev) => {
            const allSelected = rows.length > 0 && rows.every((r) => prev.has(r.id));
            const next = new Map(prev);
            if (allSelected) {
                rows.forEach((r) => next.delete(r.id));
            } else {
                rows.forEach((r) => next.set(r.id, { entity_type: inferEntityType(r), entity_id: r.id }));
            }
            return next;
        });
    }, []);

    const selectAllMatchingFilter = useCallback((params: DataTransferSearchParams) => {
        setSelectedRefs(new Map());
        setSelectAllMatching(params);
    }, []);

    const clear = useCallback(() => {
        setSelectedRefs(new Map());
        setSelectAllMatching(null);
    }, []);

    const selectionCount = useCallback(
        (totalMatchingFilter: number) => (selectAllMatching !== null ? totalMatchingFilter : selectedRefs.size),
        [selectAllMatching, selectedRefs],
    );

    return useMemo(
        () => ({
            selectedRefs,
            selectAllMatching,
            isSelected,
            toggle,
            toggleAll,
            selectAllMatchingFilter,
            clear,
            selectionCount,
        }),
        [selectedRefs, selectAllMatching, isSelected, toggle, toggleAll, selectAllMatchingFilter, clear, selectionCount],
    );
}
