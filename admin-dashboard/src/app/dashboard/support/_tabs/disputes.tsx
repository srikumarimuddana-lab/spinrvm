"use client";

import { useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { HelpCircle, Search, CheckCircle, XCircle, Clock, Plus, Pencil, Trash2, RefreshCw, Eye } from "lucide-react";
import { formatDate } from "@/lib/utils";
import { getDisputes, createDispute, updateDispute, resolveDispute, deleteDispute } from "@/lib/api";
import { useServiceAreas, ServiceAreaFilter, ServiceAreaSelect } from "../_components/service-area-select";

const S_CFG: Record<string, { l: string; i: any; c: string }> = {
    pending: { l: "Pending", i: Clock, c: "bg-amber-500/15 text-amber-600" },
    open: { l: "Open", i: Clock, c: "bg-amber-500/15 text-amber-600" },
    resolved: { l: "Resolved", i: CheckCircle, c: "bg-emerald-500/15 text-emerald-600" },
    rejected: { l: "Rejected", i: XCircle, c: "bg-red-500/15 text-red-600" },
};
const TYPES = [{ v: "fare", l: "Fare Dispute" }, { v: "behavior", l: "Behavior Issue" }, { v: "route", l: "Route Issue" }, { v: "safety", l: "Safety Concern" }, { v: "damage", l: "Vehicle Damage" }, { v: "other", l: "Other" }];

export default function DisputesTab() {
    const { areas } = useServiceAreas();
    const [disputes, setDisputes] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [statusFilter, setStatusFilter] = useState("all");
    const [areaFilter, setAreaFilter] = useState("all");
    const [selected, setSelected] = useState<any>(null);
    const [resolution, setResolution] = useState("");
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editing, setEditing] = useState<any>(null);
    const [saving, setSaving] = useState(false);
    const [form, setForm] = useState({ ride_id: "", user_name: "", user_type: "rider", reason: "fare", description: "", refund_amount: "", service_area_id: "" });

    const load = async () => {
        setLoading(true);
        try {
            const data = await getDisputes();
            setDisputes((data || []).map((d: any) => ({ id: d.id, ride_id: d.ride_id, user_name: d.user_name || "Unknown", user_type: d.user_type || "rider", dispute_type: d.reason || "other", description: d.description, status: d.status || "pending", refund_amount: d.refund_amount, service_area_id: d.service_area_id, created_at: d.created_at, resolution: d.admin_note || d.resolution_notes || d.resolution })));
        } catch { setDisputes([]); } finally { setLoading(false); }
    };
    useEffect(() => { load(); }, []);

    const stats = { pending: disputes.filter((d) => d.status === "open" || d.status === "pending").length, resolved: disputes.filter((d) => d.status === "resolved").length, rejected: disputes.filter((d) => d.status === "rejected").length };
    const filtered = disputes.filter((d) => {
        const ms = !search || d.user_name?.toLowerCase().includes(search.toLowerCase()) || d.ride_id?.toLowerCase().includes(search.toLowerCase()) || d.description?.toLowerCase().includes(search.toLowerCase());
        const ma = areaFilter === "all" || d.service_area_id === areaFilter;
        return ms && ma && (statusFilter === "all" || d.status === statusFilter || (statusFilter === "pending" && d.status === "open"));
    });

    const reset = () => { setForm({ ride_id: "", user_name: "", user_type: "rider", reason: "fare", description: "", refund_amount: "", service_area_id: "" }); setEditing(null); };
    const areaName = (id: string) => areas.find((a) => a.id === id)?.name || "";

    const handleSave = async () => {
        if (!form.description.trim()) { alert("Enter a description."); return; }
        setSaving(true);
        try {
            if (editing) await updateDispute(editing.id, { reason: form.reason, description: form.description, refund_amount: form.refund_amount ? parseFloat(form.refund_amount) : 0, user_type: form.user_type, service_area_id: form.service_area_id || null });
            else await createDispute({ ride_id: form.ride_id || null, user_name: form.user_name, user_type: form.user_type, reason: form.reason, description: form.description, refund_amount: form.refund_amount ? parseFloat(form.refund_amount) : 0, service_area_id: form.service_area_id || null });
            setDialogOpen(false); reset(); load();
        } catch (e: any) { alert(e.message); } finally { setSaving(false); }
    };

    const handleResolve = async (status: string) => {
        if (!selected || !resolution.trim()) { alert("Enter resolution notes."); return; }
        try { await resolveDispute(selected.id, { resolution: status, admin_note: resolution.trim() }); setSelected(null); setResolution(""); load(); } catch (e: any) { alert(e.message); }
    };

    return (
        <div className="space-y-4">
            <div className="grid grid-cols-3 gap-3">
                <Card><CardContent className="pt-3 pb-2"><div className="flex items-center gap-2"><Clock className="h-4 w-4 text-amber-500" /><div><p className="text-[10px] text-muted-foreground">Pending</p><p className="text-xl font-bold">{stats.pending}</p></div></div></CardContent></Card>
                <Card><CardContent className="pt-3 pb-2"><div className="flex items-center gap-2"><CheckCircle className="h-4 w-4 text-emerald-500" /><div><p className="text-[10px] text-muted-foreground">Resolved</p><p className="text-xl font-bold">{stats.resolved}</p></div></div></CardContent></Card>
                <Card><CardContent className="pt-3 pb-2"><div className="flex items-center gap-2"><XCircle className="h-4 w-4 text-red-500" /><div><p className="text-[10px] text-muted-foreground">Rejected</p><p className="text-xl font-bold">{stats.rejected}</p></div></div></CardContent></Card>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2 flex-1 flex-wrap">
                    <div className="relative flex-1 max-w-xs"><Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" /><Input placeholder="Search..." value={search} onChange={(e) => setSearch(e.target.value)} className="pl-9 h-9" /></div>
                    <Select value={statusFilter} onValueChange={setStatusFilter}><SelectTrigger className="w-32 h-9"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">All</SelectItem><SelectItem value="pending">Pending</SelectItem><SelectItem value="resolved">Resolved</SelectItem><SelectItem value="rejected">Rejected</SelectItem></SelectContent></Select>
                    <ServiceAreaFilter value={areaFilter} onChange={setAreaFilter} areas={areas} />
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={load}><RefreshCw className={`mr-1.5 h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />Refresh</Button>
                    <Button size="sm" onClick={() => { reset(); setDialogOpen(true); }}><Plus className="mr-1.5 h-3.5 w-3.5" />Create Dispute</Button>
                </div>
            </div>

            <Card><CardContent className="p-0">
                {loading ? <div className="flex justify-center p-12"><div className="h-7 w-7 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
                : filtered.length === 0 ? <div className="text-center py-12 text-muted-foreground text-sm">No disputes found.</div>
                : <Table><TableHeader><TableRow><TableHead>User</TableHead><TableHead>Type</TableHead><TableHead>Area</TableHead><TableHead>Description</TableHead><TableHead>Status</TableHead><TableHead>Date</TableHead><TableHead className="text-right">Actions</TableHead></TableRow></TableHeader>
                    <TableBody>{filtered.map((d) => {
                        const sc = S_CFG[d.status] || S_CFG.pending; const SI = sc.i;
                        return (
                            <TableRow key={d.id} className="cursor-pointer hover:bg-muted/50" onClick={() => { setSelected(d); setResolution(""); }}>
                                <TableCell><p className="font-medium text-sm">{d.user_name}</p><p className="text-[10px] text-muted-foreground capitalize">{d.user_type}</p></TableCell>
                                <TableCell><Badge variant="outline" className="text-[10px]">{TYPES.find((t) => t.v === d.dispute_type)?.l || d.dispute_type}</Badge></TableCell>
                                <TableCell className="text-xs text-muted-foreground">{areaName(d.service_area_id) || "—"}</TableCell>
                                <TableCell className="max-w-[150px] truncate text-xs text-muted-foreground">{d.description}</TableCell>
                                <TableCell><Badge className={`text-[10px] ${sc.c}`}><SI className="h-3 w-3 mr-1" />{sc.l}</Badge></TableCell>
                                <TableCell className="text-[10px] text-muted-foreground">{formatDate(d.created_at)}</TableCell>
                                <TableCell className="text-right"><div className="flex justify-end gap-0.5">
                                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={(e) => { e.stopPropagation(); setSelected(d); setResolution(""); }}><Eye className="h-3.5 w-3.5" /></Button>
                                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={(e) => { e.stopPropagation(); setEditing(d); setForm({ ride_id: d.ride_id || "", user_name: d.user_name || "", user_type: d.user_type || "rider", reason: d.dispute_type || "fare", description: d.description || "", refund_amount: d.refund_amount ? String(d.refund_amount) : "", service_area_id: d.service_area_id || "" }); setDialogOpen(true); }}><Pencil className="h-3.5 w-3.5" /></Button>
                                    <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive" onClick={(e) => { e.stopPropagation(); if (confirm("Delete?")) deleteDispute(d.id).then(load); }}><Trash2 className="h-3.5 w-3.5" /></Button>
                                </div></TableCell>
                            </TableRow>
                        );
                    })}</TableBody></Table>}
            </CardContent></Card>

            {/* Review Dialog */}
            <Dialog open={!!selected && !dialogOpen} onOpenChange={(o) => { if (!o) setSelected(null); }}>
                <DialogContent className="sm:max-w-md"><DialogHeader><DialogTitle className="text-base flex items-center gap-2"><HelpCircle className="h-4 w-4 text-amber-500" />Review Dispute</DialogTitle></DialogHeader>
                    {selected && (<div className="space-y-3">
                        <div className="grid grid-cols-2 gap-3 text-sm">
                            <div><Label className="text-[10px] text-muted-foreground">User</Label><p>{selected.user_name} ({selected.user_type})</p></div>
                            <div><Label className="text-[10px] text-muted-foreground">Type</Label><p className="capitalize">{TYPES.find((t) => t.v === selected.dispute_type)?.l || selected.dispute_type}</p></div>
                            {selected.ride_id && <div><Label className="text-[10px] text-muted-foreground">Ride ID</Label><p className="font-mono text-xs">{selected.ride_id}</p></div>}
                            {areaName(selected.service_area_id) && <div><Label className="text-[10px] text-muted-foreground">Service Area</Label><p className="text-xs">{areaName(selected.service_area_id)}</p></div>}
                            <div><Label className="text-[10px] text-muted-foreground">Status</Label><div className="mt-0.5"><Badge className={`text-[10px] ${(S_CFG[selected.status] || S_CFG.pending).c}`}>{(S_CFG[selected.status] || S_CFG.pending).l}</Badge></div></div>
                        </div>
                        <div><Label className="text-[10px] text-muted-foreground">Description</Label><div className="rounded-lg bg-muted/50 p-2.5 text-xs mt-1">{selected.description}</div></div>
                        {selected.resolution && <div><Label className="text-[10px] text-muted-foreground">Resolution</Label><div className="rounded-lg bg-primary/5 border border-primary/10 p-2.5 text-xs mt-1">{selected.resolution}</div></div>}
                        {(selected.status === "pending" || selected.status === "open") && (<>
                            <div className="space-y-1.5"><Label className="text-xs">Resolution Notes</Label><Textarea placeholder="Enter resolution..." value={resolution} onChange={(e) => setResolution(e.target.value)} rows={2} /></div>
                            <div className="flex gap-2">
                                <Button size="sm" className="flex-1 bg-emerald-600 hover:bg-emerald-700" onClick={() => handleResolve("approved")} disabled={!resolution.trim()}><CheckCircle className="h-3.5 w-3.5 mr-1.5" />Resolve</Button>
                                <Button size="sm" variant="destructive" className="flex-1" onClick={() => handleResolve("rejected")} disabled={!resolution.trim()}><XCircle className="h-3.5 w-3.5 mr-1.5" />Reject</Button>
                            </div>
                        </>)}
                    </div>)}
                </DialogContent>
            </Dialog>

            {/* Create/Edit Dialog */}
            <Dialog open={dialogOpen} onOpenChange={(o) => { if (!o) { setDialogOpen(false); reset(); } }}>
                <DialogContent className="sm:max-w-md"><DialogHeader><DialogTitle className="text-base">{editing ? "Edit Dispute" : "Create Dispute"}</DialogTitle></DialogHeader>
                    <div className="space-y-3">
                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-1.5"><Label className="text-xs">User Name</Label><Input placeholder="John Doe" value={form.user_name} onChange={(e) => setForm({ ...form, user_name: e.target.value })} /></div>
                            <div className="space-y-1.5"><Label className="text-xs">User Type</Label><Select value={form.user_type} onValueChange={(v) => setForm({ ...form, user_type: v })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="rider">Rider</SelectItem><SelectItem value="driver">Driver</SelectItem></SelectContent></Select></div>
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-1.5"><Label className="text-xs">Type</Label><Select value={form.reason} onValueChange={(v) => setForm({ ...form, reason: v })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{TYPES.map((t) => <SelectItem key={t.v} value={t.v}>{t.l}</SelectItem>)}</SelectContent></Select></div>
                            <div className="space-y-1.5"><Label className="text-xs">Ride ID</Label><Input placeholder="Optional" value={form.ride_id} onChange={(e) => setForm({ ...form, ride_id: e.target.value })} /></div>
                        </div>
                        <ServiceAreaSelect value={form.service_area_id} onChange={(v) => setForm({ ...form, service_area_id: v })} areas={areas} />
                        <div className="space-y-1.5"><Label className="text-xs">Description *</Label><Textarea placeholder="Describe..." value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} rows={3} /></div>
                        <div className="space-y-1.5 max-w-[180px]"><Label className="text-xs">Refund Amount ($)</Label><Input type="number" placeholder="0.00" value={form.refund_amount} onChange={(e) => setForm({ ...form, refund_amount: e.target.value })} /></div>
                        <Button className="w-full" size="sm" onClick={handleSave} disabled={saving}>{saving ? "Saving..." : editing ? "Update" : "Create"}</Button>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    );
}
