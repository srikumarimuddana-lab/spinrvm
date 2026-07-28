"use client";

import { useEffect, useState } from "react";
import { Loader2, Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Table, TableHeader, TableBody, TableHead, TableRow, TableCell } from "@/components/ui/table";
import { useToast } from "@/components/ui/use-toast";
import {
    searchDataTransferEntities,
    type DataTransferEntityRow,
    type DataTransferSearchParams,
} from "@/lib/api";
import type { EntitySelectionState } from "./useEntitySelection";

const PAGE_SIZE = 50;

export function EntitySearchTable({ selection }: { selection: EntitySelectionState }) {
    const { toast } = useToast();
    const [q, setQ] = useState("");
    const [entityType, setEntityType] = useState<"all" | "driver" | "rider">("all");
    const [dateFrom, setDateFrom] = useState("");
    const [dateTo, setDateTo] = useState("");
    const [page, setPage] = useState(1);
    const [rows, setRows] = useState<DataTransferEntityRow[]>([]);
    const [totalCount, setTotalCount] = useState(0);
    const [loading, setLoading] = useState(false);

    const currentParams: DataTransferSearchParams = {
        q: q.trim() || undefined,
        entityType: entityType === "all" ? undefined : entityType,
        dateFrom: dateFrom || undefined,
        dateTo: dateTo || undefined,
        page,
        pageSize: PAGE_SIZE,
    };

    const runSearch = async () => {
        setLoading(true);
        try {
            const result = await searchDataTransferEntities(currentParams);
            setRows(result.rows);
            setTotalCount(result.total_count);
        } catch (e: any) {
            toast({ title: "Search failed", description: e?.message ?? "Unknown error", variant: "destructive" });
        } finally {
            setLoading(false);
        }
    };

    // Re-run on page change only — text/date/entity-type changes require the
    // explicit Search button so every keystroke doesn't fire a request.
    useEffect(() => {
        runSearch();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [page]);

    const onSearchClick = () => {
        setPage(1);
        // page is already 1 if unchanged, so the effect above won't refire —
        // run directly to guarantee the new filters are applied immediately.
        void runSearch();
    };

    const allOnPageSelected = rows.length > 0 && rows.every((r) => selection.isSelected(r.id));
    const totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE));

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap gap-2 items-end">
                <div className="flex-1 min-w-[200px]">
                    <Input
                        placeholder="Search name, email, or phone…"
                        value={q}
                        onChange={(e) => setQ(e.target.value)}
                        onKeyDown={(e) => e.key === "Enter" && onSearchClick()}
                    />
                </div>
                <Select value={entityType} onValueChange={(v) => setEntityType(v as typeof entityType)}>
                    <SelectTrigger className="w-[140px]">
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All</SelectItem>
                        <SelectItem value="driver">Drivers</SelectItem>
                        <SelectItem value="rider">Riders</SelectItem>
                    </SelectContent>
                </Select>
                <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="w-[160px]" />
                <span className="text-sm text-muted-foreground self-center">to</span>
                <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="w-[160px]" />
                <Button onClick={onSearchClick} disabled={loading}>
                    {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
                    Search
                </Button>
            </div>

            {selection.selectAllMatching !== null ? (
                <div className="flex items-center justify-between rounded-md border bg-muted/40 px-3 py-2 text-sm">
                    <span>All {totalCount} record(s) matching this filter are selected.</span>
                    <Button variant="ghost" size="sm" onClick={selection.clear}>
                        Clear selection
                    </Button>
                </div>
            ) : totalCount > rows.length && rows.length > 0 ? (
                <div className="flex items-center justify-between rounded-md border px-3 py-2 text-sm">
                    <span>
                        {selection.selectionCount(totalCount)} selected on this page. {totalCount} record(s) match
                        this filter.
                    </span>
                    <Button variant="ghost" size="sm" onClick={() => selection.selectAllMatchingFilter(currentParams)}>
                        Select all {totalCount} matching
                    </Button>
                </div>
            ) : null}

            <Table>
                <TableHeader>
                    <TableRow>
                        <TableHead className="w-10">
                            <input
                                type="checkbox"
                                checked={allOnPageSelected}
                                onChange={() => selection.toggleAll(rows)}
                                aria-label="Select all on this page"
                            />
                        </TableHead>
                        <TableHead>Name</TableHead>
                        <TableHead>Email</TableHead>
                        <TableHead>Phone</TableHead>
                        <TableHead>Type</TableHead>
                        <TableHead>Created</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {rows.length === 0 && !loading && (
                        <TableRow>
                            <TableCell colSpan={6} className="text-center text-muted-foreground py-8">
                                No results. Try adjusting your search.
                            </TableCell>
                        </TableRow>
                    )}
                    {rows.map((row) => (
                        <TableRow key={row.id}>
                            <TableCell>
                                <input
                                    type="checkbox"
                                    checked={selection.isSelected(row.id)}
                                    onChange={() => selection.toggle(row)}
                                    aria-label={`Select ${row.full_name ?? row.id}`}
                                />
                            </TableCell>
                            <TableCell>{row.full_name ?? "—"}</TableCell>
                            <TableCell>{row.email ?? "—"}</TableCell>
                            <TableCell>{row.phone ?? "—"}</TableCell>
                            <TableCell>{row.role ?? (row.vehicle_plate ? "driver" : "—")}</TableCell>
                            <TableCell>{row.created_at ? new Date(row.created_at).toLocaleDateString() : "—"}</TableCell>
                        </TableRow>
                    ))}
                </TableBody>
            </Table>

            {totalPages > 1 && (
                <div className="flex items-center justify-between text-sm">
                    <span>
                        Page {page} of {totalPages}
                    </span>
                    <div className="flex gap-2">
                        <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                            Previous
                        </Button>
                        <Button
                            variant="outline"
                            size="sm"
                            disabled={page >= totalPages}
                            onClick={() => setPage((p) => p + 1)}
                        >
                            Next
                        </Button>
                    </div>
                </div>
            )}
        </div>
    );
}
