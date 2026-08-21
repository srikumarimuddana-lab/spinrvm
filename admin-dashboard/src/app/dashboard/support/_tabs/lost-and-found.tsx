"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getLostAndFoundItems, resolveLostItem, updateLostItem, deleteLostItem, reportLostItem } from "@/lib/api";
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
import { Search, CheckCircle, Plus, Pencil, Trash2, RefreshCw } from "lucide-react";
import { formatDate } from "@/lib/utils";
import { useServiceAreas, ServiceAreaFilter, ServiceAreaSelect } from "../_components/service-area-select";
import { useToast } from "@/components/ui/use-toast";
import {
    AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
    AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";

const PAGE_SIZE = 50;

const S_CFG: Record<string, { l: string; c: string }> = {
    reported: { l: "Reported", c: "bg-warning/15 text-warning" },
    // eslint-disable-next-line no-restricted-syntax -- "driver_notified" (in progress, neither good nor bad) has no semantic-token equivalent; must stay distinct from reported/resolved/unresolved (#2816)
    driver_notified: { l: "Driver Notified", c: "bg-blue-500/15 text-blue-600" },
    resolved: { l: "Resolved", c: "bg-success/15 text-success" },
    unresolved: { l: "Unresolved", c: "bg-destructive/15 text-destructive" },
};

export default function LostAndFoundTab() {
    const { toast } = useToast();
    const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
    const { areas } = useServiceAreas();
    const [items, setItems] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [statusFilter, setStatusFilter] = useState("all");
    const [areaFilter, setAreaFilter] = useState("all");
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editDialog, setEditDialog] = useState(false);
    const [editing, setEditing] = useState<any>(null);
    const [saving, setSaving] = useState(false);
    const [form, setForm] = useState({ ride_id: "", item_description: "", service_area_id: "" });
    const [editForm, setEditForm] = useState({ item_description: "", admin_notes: "", status: "reported" });
    const [page, setPage] = useState(0);
    const [hasNextPage, setHasNextPage] = useState(false);
    const reqIdRef = useRef(0);

    const load = useCallback(() => {
        setLoading(true);
        const reqId = ++reqIdRef.current;
        const opts: any = { limit: PAGE_SIZE + 1, offset: page * PAGE_SIZE };
        if (statusFilter !== "all") opts.status = statusFilter;
        if (areaFilter !== "all") opts.service_area_id = areaFilter;
        getLostAndFoundItems(opts)
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
        return i.item_description?.toLowerCase().includes(search.toLowerCase());
    });
    const { sorted, sort, toggle } = useTableSort(filtered);
    const areaName = (id: string) => areas.find((a) => a.id === id)?.name || "";

    const handleCreate = async () => {
        if (!form.ride_id.trim() || !form.item_description.trim()) { toast({ title: "Missing fields", description: "Enter ride ID and item description.", variant: "destructive" }); return; }
        setSaving(true);
        try {
            await reportLostItem(form.ride_id, { item_description: form.item_description, service_area_id: form.service_area_id || null });
            toast({ title: "Lost item reported" });
            setDialogOpen(false); setForm({ ride_id: "", item_description: "", service_area_id: "" }); load();
        }
        catch (e: any) { toast({ title: "Failed to report item", description: e?.message || "Please try again", variant: "destructive" }); } finally { setSaving(false); }
    };

    const handleUpdate = async () => {
        if (!editing) return;
        setSaving(true);
        try {
            await updateLostItem(editing.id, editForm);
            toast({ title: "Lost item updated" });
            setEditDialog(false); setEditing(null); load();
        }
        catch (e: any) { toast({ title: "Failed to update item", description: e?.message || "Please try again", variant: "destructive" }); } finally { setSaving(false); }
    };

    const handleResolve = async (id: string, status: string) => {
        try {
            await resolveLostItem(id, { status });
            toast({ title: `Marked as ${status}` });
            load();
        } catch (e: any) { toast({ title: "Failed to update status", description: e?.message || "Please try again", variant: "destructive" }); }
    };

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2 flex-1">
                    <div className="relative flex-1 max-w-xs"><Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" /><Input placeholder="Search items..." value={search} onChange={(e) => setSearch(e.target.value)} className="pl-9 h-9" /></div>
                    <Select value={statusFilter} onValueChange={setStatusFilter}><SelectTrigger className="w-36 h-9"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">All</SelectItem><SelectItem value="reported">Reported</SelectItem><SelectItem value="driver_notified">Notified</SelectItem><SelectItem value="resolved">Resolved</SelectItem><SelectItem value="unresolved">Unresolved</SelectItem></SelectContent></Select>
                    <ServiceAreaFilter value={areaFilter} onChange={setAreaFilter} areas={areas} />
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={load}><RefreshCw className={`mr-1.5 h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />Refresh</Button>
                    <Button size="sm" onClick={() => setDialogOpen(true)}><Plus className="mr-1.5 h-3.5 w-3.5" />Report Item</Button>
                </div>
            </div>

            <Card><CardContent className="p-0">
                {loading ? <div className="flex justify-center p-12"><div className="h-7 w-7 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
                : filtered.length === 0 ? <div className="text-center py-12 text-muted-foreground text-sm">No items found.</div>
                : <Table><TableHeader><TableRow><SortableHead column="item_description" sort={sort} onSort={toggle}>Item</SortableHead><SortableHead column="ride_id" sort={sort} onSort={toggle}>Ride</SortableHead><SortableHead column="service_area_id" sort={sort} onSort={toggle}>Area</SortableHead><SortableHead column="status" sort={sort} onSort={toggle}>Status</SortableHead><SortableHead column="created_at" sort={sort} onSort={toggle}>Date</SortableHead><TableHead className="text-right">Actions</TableHead></TableRow></TableHeader>
                    <TableBody>{sorted.map((item) => (
                        <TableRow key={item.id}>
                            <TableCell className="font-medium max-w-[220px] truncate text-sm">{item.item_description}</TableCell>
                            <TableCell className="font-mono text-xs text-muted-foreground">{item.ride_id?.slice(0, 8) || "—"}</TableCell>
                            <TableCell className="text-xs text-muted-foreground">{areaName(item.service_area_id) || "—"}</TableCell>
                            <TableCell><Badge className={`text-[10px] ${(S_CFG[item.status] || S_CFG.reported).c}`}>{(S_CFG[item.status] || S_CFG.reported).l}</Badge></TableCell>
                            <TableCell className="text-[10px] text-muted-foreground">{formatDate(item.created_at)}</TableCell>
                            <TableCell className="text-right"><div className="flex justify-end gap-0.5">
                                {item.status !== "resolved" && <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => handleResolve(item.id, "resolved")} title="Resolve"><CheckCircle className="h-3.5 w-3.5 text-success" /></Button>}
                                <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => { setEditing(item); setEditForm({ item_description: item.item_description || "", admin_notes: item.admin_notes || "", status: item.status || "reported" }); setEditDialog(true); }}><Pencil className="h-3.5 w-3.5" /></Button>
                                <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive" onClick={() => setDeleteTarget(item.id)}><Trash2 className="h-3.5 w-3.5" /></Button>
                            </div></TableCell>
                        </TableRow>
                    ))}</TableBody></Table>}
            </CardContent></Card>

            <Pagination page={page} pageSize={PAGE_SIZE} hasNextPage={hasNextPage} onPageChange={setPage} />

            {/* Report Dialog */}
            <Dialog open={dialogOpen} onOpenChange={(o) => { if (!o) setDialogOpen(false); }}>
                <DialogContent className="sm:max-w-md"><DialogHeader><DialogTitle className="text-base">Report Lost Item</DialogTitle></DialogHeader>
                    <div className="space-y-3">
                        <div className="space-y-1.5"><Label className="text-xs">Ride ID *</Label><Input placeholder="Enter ride ID" value={form.ride_id} onChange={(e) => setForm({ ...form, ride_id: e.target.value })} /></div>
                        <div className="space-y-1.5"><Label className="text-xs">Item Description *</Label><Textarea placeholder="Describe the item..." value={form.item_description} onChange={(e) => setForm({ ...form, item_description: e.target.value })} rows={3} /></div>
                        <ServiceAreaSelect value={form.service_area_id} onChange={(v) => setForm({ ...form, service_area_id: v })} areas={areas} />
                        <Button className="w-full" size="sm" onClick={handleCreate} disabled={saving}>{saving ? "Reporting..." : "Report Item"}</Button>
                    </div>
                </DialogContent>
            </Dialog>

            {/* Edit Dialog */}
            <Dialog open={editDialog} onOpenChange={(o) => { if (!o) { setEditDialog(false); setEditing(null); } }}>
                <DialogContent className="sm:max-w-md"><DialogHeader><DialogTitle className="text-base">Edit Lost Item</DialogTitle></DialogHeader>
                    <div className="space-y-3">
                        <div className="space-y-1.5"><Label className="text-xs">Item Description</Label><Textarea value={editForm.item_description} onChange={(e) => setEditForm({ ...editForm, item_description: e.target.value })} rows={2} /></div>
                        <div className="space-y-1.5"><Label className="text-xs">Admin Notes</Label><Textarea value={editForm.admin_notes} onChange={(e) => setEditForm({ ...editForm, admin_notes: e.target.value })} rows={2} /></div>
                        <div className="space-y-1.5"><Label className="text-xs">Status</Label><Select value={editForm.status} onValueChange={(v) => setEditForm({ ...editForm, status: v })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="reported">Reported</SelectItem><SelectItem value="driver_notified">Driver Notified</SelectItem><SelectItem value="resolved">Resolved</SelectItem><SelectItem value="unresolved">Unresolved</SelectItem></SelectContent></Select></div>
                        <Button className="w-full" size="sm" onClick={handleUpdate} disabled={saving}>{saving ? "Saving..." : "Save Changes"}</Button>
                    </div>
                </DialogContent>
            </Dialog>

            <AlertDialog open={!!deleteTarget} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Delete lost item?</AlertDialogTitle>
                        <AlertDialogDescription>This cannot be undone.</AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={() => { if (deleteTarget) deleteLostItem(deleteTarget).then(() => { toast({ title: "Lost item deleted" }); load(); }).catch((e: any) => { toast({ title: "Failed to delete item", description: e?.message || "Please try again", variant: "destructive" }); }).finally(() => setDeleteTarget(null)); }} className="bg-red-600 hover:bg-red-700">Delete</AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}
