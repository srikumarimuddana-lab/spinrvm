"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getFlags, deactivateFlag, deleteFlag, flagRideParticipant } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Pagination } from "@/components/ui/pagination";
import { Flag, Search, Plus, Trash2, Eye, RefreshCw, EyeOff, Users, Car } from "lucide-react";
import { formatDate } from "@/lib/utils";
import { useServiceAreas, ServiceAreaFilter, ServiceAreaSelect } from "../_components/service-area-select";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import { useToast } from "@/components/ui/use-toast";
import {
    AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
    AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";

const PAGE_SIZE = 50;
const REASONS = ["inappropriate_behavior", "safety_concern", "fraud", "policy_violation", "spam", "other"];

export default function FlagsTab() {
    const { toast } = useToast();
    const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
    const { areas } = useServiceAreas();
    const [flags, setFlags] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [typeFilter, setTypeFilter] = useState("all");
    const [areaFilter, setAreaFilter] = useState("all");
    const [selected, setSelected] = useState<any>(null);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [saving, setSaving] = useState(false);
    const [form, setForm] = useState({ ride_id: "", target_type: "driver", reason: "other", description: "", service_area_id: "" });
    const [page, setPage] = useState(0);
    const [hasNextPage, setHasNextPage] = useState(false);
    const reqIdRef = useRef(0);

    const load = useCallback(() => {
        setLoading(true);
        const reqId = ++reqIdRef.current;
        const opts: any = { limit: PAGE_SIZE + 1, offset: page * PAGE_SIZE };
        if (typeFilter !== "all") opts.target_type = typeFilter;
        if (areaFilter !== "all") opts.service_area_id = areaFilter;
        getFlags(opts)
            .then((rows) => {
                if (reqId !== reqIdRef.current) return;
                const arr = Array.isArray(rows) ? rows : [];
                setHasNextPage(arr.length > PAGE_SIZE);
                setFlags(arr.slice(0, PAGE_SIZE));
            })
            .catch(() => { if (reqId === reqIdRef.current) { setFlags([]); setHasNextPage(false); } })
            .finally(() => { if (reqId === reqIdRef.current) setLoading(false); });
    }, [page, typeFilter, areaFilter]);
    useEffect(() => { load(); }, [load]);
    useEffect(() => { setPage(0); }, [typeFilter, areaFilter]);

    // Search filters the current page client-side; full-text server search is not implemented.
    const filtered = flags.filter((f) => {
        if (!search) return true;
        const q = search.toLowerCase();
        return f.reason?.toLowerCase().includes(q) || f.description?.toLowerCase().includes(q) || f.target_id?.toLowerCase().includes(q);
    });
    const { sorted, sort, toggle } = useTableSort(filtered);
    const areaName = (id: string) => areas.find((a) => a.id === id)?.name || "";

    const handleCreate = async () => {
        if (!form.ride_id.trim()) { toast({ title: "Missing ride ID", variant: "destructive" }); return; }
        setSaving(true);
        try {
            await flagRideParticipant(form.ride_id, { target_type: form.target_type, reason: form.reason, description: form.description, service_area_id: form.service_area_id || null });
            toast({ title: "Flag created" });
            setDialogOpen(false); setForm({ ride_id: "", target_type: "driver", reason: "other", description: "", service_area_id: "" }); load();
        }
        catch (e: any) { toast({ title: "Failed to create flag", description: e.message, variant: "destructive" }); } finally { setSaving(false); }
    };

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2 flex-1">
                    <div className="relative flex-1 max-w-xs"><Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" /><Input placeholder="Search..." value={search} onChange={(e) => setSearch(e.target.value)} className="pl-9 h-9" /></div>
                    <Select value={typeFilter} onValueChange={setTypeFilter}><SelectTrigger className="w-32 h-9"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">All</SelectItem><SelectItem value="rider">Riders</SelectItem><SelectItem value="driver">Drivers</SelectItem></SelectContent></Select>
                    <ServiceAreaFilter value={areaFilter} onChange={setAreaFilter} areas={areas} />
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={load}><RefreshCw className={`mr-1.5 h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />Refresh</Button>
                    <Button size="sm" onClick={() => setDialogOpen(true)}><Plus className="mr-1.5 h-3.5 w-3.5" />Create Flag</Button>
                </div>
            </div>

            <Card><CardContent className="p-0">
                {loading ? <div className="flex justify-center p-12"><div className="h-7 w-7 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
                : filtered.length === 0 ? <div className="text-center py-12 text-muted-foreground text-sm">No flags found.</div>
                : <Table><TableHeader><TableRow><SortableHead column="target_type" sort={sort} onSort={toggle}>Target</SortableHead><SortableHead column="reason" sort={sort} onSort={toggle}>Reason</SortableHead><SortableHead column="service_area_id" sort={sort} onSort={toggle}>Area</SortableHead><SortableHead column="description" sort={sort} onSort={toggle}>Description</SortableHead><SortableHead column="is_active" sort={sort} onSort={toggle}>Status</SortableHead><SortableHead column="created_at" sort={sort} onSort={toggle}>Date</SortableHead><TableHead className="text-right">Actions</TableHead></TableRow></TableHeader>
                    <TableBody>{sorted.map((f) => (
                        <TableRow key={f.id} className="cursor-pointer hover:bg-muted/50" onClick={() => setSelected(f)}>
                            <TableCell><div className="flex items-center gap-1.5">{f.target_type === "rider" ? <Users className="h-3.5 w-3.5 text-blue-500" /> : <Car className="h-3.5 w-3.5 text-emerald-500" />}<span className="text-sm capitalize">{f.target_type}</span></div></TableCell>
                            <TableCell><Badge variant="outline" className="text-[10px]">{f.reason?.replace(/_/g, " ") || "other"}</Badge></TableCell>
                            <TableCell className="text-xs text-muted-foreground">{areaName(f.service_area_id) || "—"}</TableCell>
                            <TableCell className="max-w-[200px] truncate text-xs text-muted-foreground">{f.description || "—"}</TableCell>
                            <TableCell>{f.is_active === false ? <Badge className="text-[10px] bg-zinc-500/15 text-zinc-600">Inactive</Badge> : <Badge className="text-[10px] bg-amber-500/15 text-amber-600">Active</Badge>}</TableCell>
                            <TableCell className="text-[10px] text-muted-foreground">{formatDate(f.created_at)}</TableCell>
                            <TableCell className="text-right"><div className="flex justify-end gap-0.5">
                                <Button variant="ghost" size="icon" className="h-7 w-7" onClick={(e) => { e.stopPropagation(); setSelected(f); }}><Eye className="h-3.5 w-3.5" /></Button>
                                {f.is_active !== false && <Button variant="ghost" size="icon" className="h-7 w-7" onClick={(e) => { e.stopPropagation(); deactivateFlag(f.id).then(load); }} title="Deactivate"><EyeOff className="h-3.5 w-3.5" /></Button>}
                                <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive" onClick={(e) => { e.stopPropagation(); setDeleteTarget(f.id); }}><Trash2 className="h-3.5 w-3.5" /></Button>
                            </div></TableCell>
                        </TableRow>
                    ))}</TableBody></Table>}
            </CardContent></Card>

            <Pagination page={page} pageSize={PAGE_SIZE} hasNextPage={hasNextPage} onPageChange={setPage} />

            {/* Detail Dialog */}
            <Dialog open={!!selected && !dialogOpen} onOpenChange={(o) => { if (!o) setSelected(null); }}>
                <DialogContent className="sm:max-w-md"><DialogHeader><DialogTitle className="text-base flex items-center gap-2"><Flag className="h-4 w-4 text-amber-500" />Flag Details</DialogTitle></DialogHeader>
                    {selected && (<div className="space-y-3">
                        <div className="grid grid-cols-2 gap-3 text-sm">
                            <div><Label className="text-[10px] text-muted-foreground">Target Type</Label><p className="capitalize">{selected.target_type}</p></div>
                            <div><Label className="text-[10px] text-muted-foreground">Status</Label><p>{selected.is_active === false ? "Inactive" : "Active"}</p></div>
                            <div><Label className="text-[10px] text-muted-foreground">Reason</Label><p className="capitalize">{selected.reason?.replace(/_/g, " ")}</p></div>
                            <div><Label className="text-[10px] text-muted-foreground">Date</Label><p className="text-xs">{formatDate(selected.created_at)}</p></div>
                        </div>
                        {selected.target_id && <div><Label className="text-[10px] text-muted-foreground">Target ID</Label><p className="font-mono text-xs">{selected.target_id}</p></div>}
                        {selected.ride_id && <div><Label className="text-[10px] text-muted-foreground">Ride ID</Label><p className="font-mono text-xs">{selected.ride_id}</p></div>}
                        {selected.description && <div><Label className="text-[10px] text-muted-foreground">Description</Label><div className="rounded-lg bg-muted/50 p-2.5 text-xs mt-1">{selected.description}</div></div>}
                        <div className="flex gap-2">
                            {selected.is_active !== false && <Button size="sm" variant="outline" className="flex-1" onClick={() => { deactivateFlag(selected.id).then(() => { setSelected(null); load(); }); }}><EyeOff className="h-3.5 w-3.5 mr-1.5" />Deactivate</Button>}
                            <Button size="sm" variant="destructive" className="flex-1" onClick={() => setDeleteTarget(selected.id)}><Trash2 className="h-3.5 w-3.5 mr-1.5" />Delete</Button>
                        </div>
                    </div>)}
                </DialogContent>
            </Dialog>

            {/* Create Dialog */}
            <Dialog open={dialogOpen} onOpenChange={(o) => { if (!o) setDialogOpen(false); }}>
                <DialogContent className="sm:max-w-md"><DialogHeader><DialogTitle className="text-base">Create Flag</DialogTitle></DialogHeader>
                    <div className="space-y-3">
                        <div className="space-y-1.5"><Label className="text-xs">Ride ID *</Label><Input placeholder="Enter ride ID" value={form.ride_id} onChange={(e) => setForm({ ...form, ride_id: e.target.value })} /></div>
                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-1.5"><Label className="text-xs">Target</Label><Select value={form.target_type} onValueChange={(v) => setForm({ ...form, target_type: v })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="driver">Driver</SelectItem><SelectItem value="rider">Rider</SelectItem></SelectContent></Select></div>
                            <div className="space-y-1.5"><Label className="text-xs">Reason</Label><Select value={form.reason} onValueChange={(v) => setForm({ ...form, reason: v })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{REASONS.map((r) => <SelectItem key={r} value={r} className="capitalize">{r.replace(/_/g, " ")}</SelectItem>)}</SelectContent></Select></div>
                        </div>
                        <div className="space-y-1.5"><Label className="text-xs">Description</Label><Textarea placeholder="Optional details..." value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} rows={3} /></div>
                        <ServiceAreaSelect value={form.service_area_id} onChange={(v) => setForm({ ...form, service_area_id: v })} areas={areas} />
                        <Button className="w-full" size="sm" onClick={handleCreate} disabled={saving}>{saving ? "Creating..." : "Create Flag"}</Button>
                    </div>
                </DialogContent>
            </Dialog>

            <AlertDialog open={!!deleteTarget} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Delete flag?</AlertDialogTitle>
                        <AlertDialogDescription>This cannot be undone.</AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={() => { if (deleteTarget) deleteFlag(deleteTarget).then(() => { setSelected(null); load(); }).finally(() => setDeleteTarget(null)); }} className="bg-red-600 hover:bg-red-700">Delete</AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}
