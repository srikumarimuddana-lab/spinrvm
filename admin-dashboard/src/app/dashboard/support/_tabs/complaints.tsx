"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getComplaints, resolveComplaint, deleteComplaint, createRideComplaint } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import { Pagination } from "@/components/ui/pagination";
import { FileWarning, Search, CheckCircle, XCircle, Plus, Trash2, Eye, RefreshCw } from "lucide-react";
import { formatDate } from "@/lib/utils";
import { useServiceAreas, ServiceAreaFilter, ServiceAreaSelect } from "../_components/service-area-select";
import { useToast } from "@/components/ui/use-toast";
import {
    AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
    AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";

const PAGE_SIZE = 50;

const S_CFG: Record<string, { l: string; c: string }> = {
    open: { l: "Open", c: "bg-warning/15 text-warning" },
    // eslint-disable-next-line no-restricted-syntax -- "investigating" (in progress, neither good nor bad) has no semantic-token equivalent; must stay distinct from open/resolved/dismissed (#2816)
    investigating: { l: "Investigating", c: "bg-blue-500/15 text-blue-600" },
    resolved: { l: "Resolved", c: "bg-success/15 text-success" },
    dismissed: { l: "Dismissed", c: "bg-muted text-muted-foreground" },
};
const CATS = ["rude_behavior", "unsafe_driving", "vehicle_condition", "route_issue", "overcharge", "harassment", "other"];

export default function ComplaintsTab() {
    const { toast } = useToast();
    const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
    const { areas } = useServiceAreas();
    const [items, setItems] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [statusFilter, setStatusFilter] = useState("all");
    const [areaFilter, setAreaFilter] = useState("all");
    const [selected, setSelected] = useState<any>(null);
    const [resolution, setResolution] = useState("");
    const [dialogOpen, setDialogOpen] = useState(false);
    const [saving, setSaving] = useState(false);
    const [form, setForm] = useState({ ride_id: "", against_type: "driver", category: "other", description: "", service_area_id: "" });
    const [page, setPage] = useState(0);
    const [hasNextPage, setHasNextPage] = useState(false);
    const reqIdRef = useRef(0);

    const load = useCallback(() => {
        setLoading(true);
        const reqId = ++reqIdRef.current;
        const opts: any = { limit: PAGE_SIZE + 1, offset: page * PAGE_SIZE };
        if (statusFilter !== "all") opts.status = statusFilter;
        if (areaFilter !== "all") opts.service_area_id = areaFilter;
        getComplaints(opts)
            .then((rows) => {
                if (reqId !== reqIdRef.current) return;
                const arr = Array.isArray(rows) ? rows : [];
                setHasNextPage(arr.length > PAGE_SIZE);
                setItems(arr.slice(0, PAGE_SIZE));
            })
            .catch(() => { if (reqId === reqIdRef.current) { setItems([]); setHasNextPage(false); } })
            .finally(() => { if (reqId === reqIdRef.current) setLoading(false); });
    }, [page, statusFilter, areaFilter]);
    useEffect(() => { load(); }, [load]);
    useEffect(() => { setPage(0); }, [statusFilter, areaFilter]);

    // Search filters the current page client-side; full-text server search is not implemented.
    const filtered = items.filter((i) => {
        if (!search) return true;
        const q = search.toLowerCase();
        return i.category?.toLowerCase().includes(q) || i.description?.toLowerCase().includes(q);
    });
    const { sorted, sort, toggle } = useTableSort(filtered);
    const areaName = (id: string) => areas.find((a) => a.id === id)?.name || "";

    const handleCreate = async () => {
        if (!form.ride_id?.trim()) { toast({ title: "Ride ID is required", variant: "destructive" }); return; }
        if (!form.description?.trim()) { toast({ title: "Description is required", variant: "destructive" }); return; }
        setSaving(true);
        try {
            await createRideComplaint(form.ride_id, { against_type: form.against_type, category: form.category, description: form.description, service_area_id: form.service_area_id || null });
            toast({ title: "Complaint created" });
            setDialogOpen(false); setForm({ ride_id: "", against_type: "driver", category: "other", description: "", service_area_id: "" }); load();
        }
        catch (e: any) { toast({ title: "Failed to create complaint", description: e.message, variant: "destructive" }); } finally { setSaving(false); }
    };

    const handleResolve = async (status: string) => {
        if (!selected) return;
        try {
            await resolveComplaint(selected.id, { status, resolution: resolution.trim() || status });
            toast({ title: `Complaint marked as ${status}` });
            setSelected(null); setResolution(""); load();
        }
        catch (e: any) { toast({ title: "Failed to resolve complaint", description: e.message, variant: "destructive" }); }
    };

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2 flex-1">
                    <div className="relative flex-1 max-w-xs"><Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" /><Input placeholder="Search..." value={search} onChange={(e) => setSearch(e.target.value)} className="pl-9 h-9" /></div>
                    <Select value={statusFilter} onValueChange={setStatusFilter}><SelectTrigger className="w-36 h-9"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">All</SelectItem><SelectItem value="open">Open</SelectItem><SelectItem value="investigating">Investigating</SelectItem><SelectItem value="resolved">Resolved</SelectItem><SelectItem value="dismissed">Dismissed</SelectItem></SelectContent></Select>
                    <ServiceAreaFilter value={areaFilter} onChange={setAreaFilter} areas={areas} />
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={load}><RefreshCw className={`mr-1.5 h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />Refresh</Button>
                    <Button size="sm" onClick={() => setDialogOpen(true)}><Plus className="mr-1.5 h-3.5 w-3.5" />File Complaint</Button>
                </div>
            </div>

            <Card><CardContent className="p-0">
                {loading ? <div className="flex justify-center p-12"><div className="h-7 w-7 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
                : filtered.length === 0 ? <div className="text-center py-12 text-muted-foreground text-sm">No complaints found.</div>
                : <Table><TableHeader><TableRow><SortableHead column="against_type" sort={sort} onSort={toggle}>Against</SortableHead><SortableHead column="category" sort={sort} onSort={toggle}>Category</SortableHead><SortableHead column="service_area_id" sort={sort} onSort={toggle}>Area</SortableHead><SortableHead column="description" sort={sort} onSort={toggle}>Description</SortableHead><SortableHead column="status" sort={sort} onSort={toggle}>Status</SortableHead><SortableHead column="created_at" sort={sort} onSort={toggle}>Date</SortableHead><TableHead className="text-right">Actions</TableHead></TableRow></TableHeader>
                    <TableBody>{sorted.map((c) => (
                        <TableRow key={c.id} className="cursor-pointer hover:bg-muted/50" onClick={() => { setSelected(c); setResolution(""); }}>
                            <TableCell className="text-sm capitalize">{c.against_type || "—"}</TableCell>
                            <TableCell><Badge variant="outline" className="text-[10px]">{c.category?.replace(/_/g, " ") || "other"}</Badge></TableCell>
                            <TableCell className="text-xs text-muted-foreground">{areaName(c.service_area_id) || "—"}</TableCell>
                            <TableCell className="max-w-[200px] truncate text-xs text-muted-foreground">{c.description}</TableCell>
                            <TableCell><Badge className={`text-[10px] ${(S_CFG[c.status] || S_CFG.open).c}`}>{(S_CFG[c.status] || S_CFG.open).l}</Badge></TableCell>
                            <TableCell className="text-[10px] text-muted-foreground">{formatDate(c.created_at)}</TableCell>
                            <TableCell className="text-right"><div className="flex justify-end gap-0.5">
                                <Button variant="ghost" size="icon" className="h-7 w-7" onClick={(e) => { e.stopPropagation(); setSelected(c); setResolution(""); }}><Eye className="h-3.5 w-3.5" /></Button>
                                <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive" onClick={(e) => { e.stopPropagation(); setDeleteTarget(c.id); }}><Trash2 className="h-3.5 w-3.5" /></Button>
                            </div></TableCell>
                        </TableRow>
                    ))}</TableBody></Table>}
            </CardContent></Card>

            <Pagination page={page} pageSize={PAGE_SIZE} hasNextPage={hasNextPage} onPageChange={setPage} />

            {/* Review Dialog */}
            <Dialog open={!!selected && !dialogOpen} onOpenChange={(o) => { if (!o) setSelected(null); }}>
                <DialogContent className="sm:max-w-md"><DialogHeader><DialogTitle className="text-base flex items-center gap-2"><FileWarning className="h-4 w-4 text-amber-500" />Review Complaint</DialogTitle></DialogHeader>
                    {selected && (<div className="space-y-3">
                        <div className="grid grid-cols-2 gap-3 text-sm">
                            <div><Label className="text-[10px] text-muted-foreground">Against</Label><p className="capitalize">{selected.against_type || "—"}</p></div>
                            <div><Label className="text-[10px] text-muted-foreground">Category</Label><p className="capitalize">{selected.category?.replace(/_/g, " ")}</p></div>
                            <div><Label className="text-[10px] text-muted-foreground">Status</Label><Badge className={`text-[10px] ${(S_CFG[selected.status] || S_CFG.open).c}`}>{(S_CFG[selected.status] || S_CFG.open).l}</Badge></div>
                            <div><Label className="text-[10px] text-muted-foreground">Date</Label><p className="text-xs">{formatDate(selected.created_at)}</p></div>
                        </div>
                        <div><Label className="text-[10px] text-muted-foreground">Description</Label><div className="rounded-lg bg-muted/50 p-2.5 text-xs mt-1">{selected.description}</div></div>
                        {selected.resolution && <div><Label className="text-[10px] text-muted-foreground">Resolution</Label><div className="rounded-lg bg-primary/5 border border-primary/10 p-2.5 text-xs mt-1">{selected.resolution}</div></div>}
                        {(selected.status === "open" || selected.status === "investigating") && (<>
                            <div className="space-y-1.5"><Label className="text-xs">Resolution Notes</Label><Textarea placeholder="Notes..." value={resolution} onChange={(e) => setResolution(e.target.value)} rows={2} /></div>
                            <div className="flex gap-2">
                                <Button size="sm" className="flex-1 bg-emerald-600 hover:bg-emerald-700" onClick={() => handleResolve("resolved")}><CheckCircle className="h-3.5 w-3.5 mr-1.5" />Resolve</Button>
                                <Button size="sm" variant="outline" className="flex-1" onClick={() => handleResolve("dismissed")}><XCircle className="h-3.5 w-3.5 mr-1.5" />Dismiss</Button>
                            </div>
                        </>)}
                    </div>)}
                </DialogContent>
            </Dialog>

            {/* Create Dialog */}
            <Dialog open={dialogOpen} onOpenChange={(o) => { if (!o) setDialogOpen(false); }}>
                <DialogContent className="sm:max-w-md"><DialogHeader><DialogTitle className="text-base">File Complaint</DialogTitle></DialogHeader>
                    <div className="space-y-3">
                        <div className="space-y-1.5"><Label className="text-xs">Ride ID *</Label><Input placeholder="Enter ride ID" value={form.ride_id} onChange={(e) => setForm({ ...form, ride_id: e.target.value })} /></div>
                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-1.5"><Label className="text-xs">Against</Label><Select value={form.against_type} onValueChange={(v) => setForm({ ...form, against_type: v })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="driver">Driver</SelectItem><SelectItem value="rider">Rider</SelectItem></SelectContent></Select></div>
                            <div className="space-y-1.5"><Label className="text-xs">Category</Label><Select value={form.category} onValueChange={(v) => setForm({ ...form, category: v })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{CATS.map((c) => <SelectItem key={c} value={c} className="capitalize">{c.replace(/_/g, " ")}</SelectItem>)}</SelectContent></Select></div>
                        </div>
                        <div className="space-y-1.5"><Label className="text-xs">Description *</Label><Textarea placeholder="Describe..." value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} rows={3} /></div>
                        <ServiceAreaSelect value={form.service_area_id} onChange={(v) => setForm({ ...form, service_area_id: v })} areas={areas} />
                        <Button className="w-full" size="sm" onClick={handleCreate} disabled={saving}>{saving ? "Filing..." : "File Complaint"}</Button>
                    </div>
                </DialogContent>
            </Dialog>

            <AlertDialog open={!!deleteTarget} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Delete complaint?</AlertDialogTitle>
                        <AlertDialogDescription>This cannot be undone.</AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={() => { if (deleteTarget) deleteComplaint(deleteTarget).then(load).finally(() => setDeleteTarget(null)); }} className="bg-red-600 hover:bg-red-700">Delete</AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}
