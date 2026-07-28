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
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { HelpCircle } from "lucide-react";
import {
    searchDataTransferEntities,
    getServiceAreas,
    type DataTransferEntityRow,
    type DataTransferSearchParams,
} from "@/lib/api";
import type { EntitySelectionState } from "./useEntitySelection";

const PAGE_SIZE = 50;
// Typing pauses this long before the search re-runs automatically -- long
// enough that a fast typist doesn't fire a request per keystroke, short
// enough that results still feel "live" as you type.
const SEARCH_DEBOUNCE_MS = 300;

// Status vocabularies differ between drivers and users (drivers has 5 real
// values in production; users is effectively just "active" today) -- offer
// the union so the filter works regardless of which entity_type is active,
// rather than trying to keep two separate option lists in sync with the
// currently-selected entity type.
const STATUS_OPTIONS = ["active", "pending", "needs_review", "suspended", "banned"];

function Hint({ text }: { text: string }) {
    return (
        <Tooltip>
            <TooltipTrigger asChild>
                <HelpCircle className="h-3.5 w-3.5 text-muted-foreground shrink-0 cursor-help" />
            </TooltipTrigger>
            <TooltipContent className="max-w-[260px]">{text}</TooltipContent>
        </Tooltip>
    );
}

// Client-side re-sort so rows whose name starts with what was typed float to
// the top (typing "ja" surfaces "Jane" before "Rajan") -- the backend match
// itself is still substring/ILIKE, this only re-orders the page it returns.
function sortByQueryRelevance(rows: DataTransferEntityRow[], q: string): DataTransferEntityRow[] {
    const needle = q.trim().toLowerCase();
    if (!needle) return rows;
    const rank = (r: DataTransferEntityRow) => {
        const name = (r.full_name ?? "").toLowerCase();
        if (name.startsWith(needle)) return 0;
        if (name.includes(needle)) return 1;
        return 2;
    };
    return [...rows].sort((a, b) => rank(a) - rank(b));
}

export function EntitySearchTable({ selection }: { selection: EntitySelectionState }) {
    const { toast } = useToast();
    const [q, setQ] = useState("");
    const [entityType, setEntityType] = useState<"all" | "driver" | "rider">("all");
    const [status, setStatus] = useState<string>("all");
    const [serviceAreaId, setServiceAreaId] = useState<string>("all");
    const [serviceAreas, setServiceAreas] = useState<{ id: string; name: string }[]>([]);
    const [dateFrom, setDateFrom] = useState("");
    const [dateTo, setDateTo] = useState("");
    const [page, setPage] = useState(1);
    const [rows, setRows] = useState<DataTransferEntityRow[]>([]);
    const [totalCount, setTotalCount] = useState(0);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        getServiceAreas()
            .then((areas) => setServiceAreas(areas ?? []))
            .catch(() => setServiceAreas([]));
    }, []);

    const currentParams: DataTransferSearchParams = {
        q: q.trim() || undefined,
        entityType: entityType === "all" ? undefined : entityType,
        status: status === "all" ? undefined : status,
        serviceAreaId: serviceAreaId === "all" ? undefined : serviceAreaId,
        dateFrom: dateFrom || undefined,
        dateTo: dateTo || undefined,
        page,
        pageSize: PAGE_SIZE,
    };

    const runSearch = async () => {
        setLoading(true);
        try {
            const result = await searchDataTransferEntities(currentParams);
            setRows(sortByQueryRelevance(result.rows, q));
            setTotalCount(result.total_count);
        } catch (e: any) {
            toast({ title: "Search failed", description: e?.message ?? "Unknown error", variant: "destructive" });
        } finally {
            setLoading(false);
        }
    };

    // Re-run on page change immediately.
    useEffect(() => {
        runSearch();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [page]);

    // Re-run as the admin types, debounced -- filters live instead of
    // requiring an explicit Search click for text changes. Dropdown/date
    // filters still apply immediately via onSearchClick / their own
    // onValueChange handlers below.
    useEffect(() => {
        const handle = setTimeout(() => {
            setPage(1);
            void runSearch();
        }, SEARCH_DEBOUNCE_MS);
        return () => clearTimeout(handle);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [q]);

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
                <div className="flex-1 min-w-[200px] flex items-center gap-1.5">
                    <Input
                        placeholder="Search name, email, or phone…"
                        value={q}
                        onChange={(e) => setQ(e.target.value)}
                        onKeyDown={(e) => e.key === "Enter" && onSearchClick()}
                    />
                    <Hint text="Results filter automatically as you type, sorted so the closest name match is first. Press Enter or Search to apply the other filters right away." />
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
                <Select value={status} onValueChange={setStatus}>
                    <SelectTrigger className="w-[160px]">
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All statuses</SelectItem>
                        {STATUS_OPTIONS.map((s) => (
                            <SelectItem key={s} value={s}>
                                {s.replace(/_/g, " ")}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
                <div className="flex items-center gap-1.5">
                    <Select value={serviceAreaId} onValueChange={setServiceAreaId}>
                        <SelectTrigger className="w-[170px]">
                            <SelectValue placeholder="Service area" />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">All service areas</SelectItem>
                            {serviceAreas.map((a) => (
                                <SelectItem key={a.id} value={a.id}>
                                    {a.name}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                    <Hint text="Only narrows driver results — riders aren't assigned to a service area, so this filter has no effect when searching riders." />
                </div>
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
                        <TableHead>Status</TableHead>
                        <TableHead>Created</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {rows.length === 0 && !loading && (
                        <TableRow>
                            <TableCell colSpan={7} className="text-center text-muted-foreground py-8">
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
                            <TableCell>{row.status?.replace(/_/g, " ") ?? "—"}</TableCell>
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
