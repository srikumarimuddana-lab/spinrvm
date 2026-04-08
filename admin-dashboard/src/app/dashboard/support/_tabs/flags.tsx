"use client";

import { useEffect, useState } from "react";
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
import { Flag, Search, Plus, Trash2, Eye, RefreshCw, EyeOff, Users, Car, AlertTriangle } from "lucide-react";
import { formatDate } from "@/lib/utils";

const REASONS = ["inappropriate_behavior", "safety_concern", "fraud", "policy_violation", "spam", "other"];

export default function FlagsTab() {
    const [flags, setFlags] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [typeFilter, setTypeFilter] = useState("all");
    const [selected, setSelected] = useState<any>(null);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [saving, setSaving] = useState(false);
    const [form, setForm] = useState({ ride_id: "", target_type: "driver", reason: "other", description: "" });

    const load = () => { setLoading(true); getFlags().then((d) => setFlags(d || [])).catch(() => setFlags([])).finally(() => setLoading(false)); };
    useEffect(() => { load(); }, []);

    const stats = { total: flags.length, riders: flags.filter((f) => f.target_type === "rider").length, drivers: flags.filter((f) => f.target_type === "driver").length, active: flags.filter((f) => f.is_active !== false).length };
    const filtered = flags.filter((f) => {
        const ms = !search || f.reason?.toLowerCase().includes(search.toLowerCase()) || f.description?.toLowerCase().includes(search.toLowerCase()) || f.target_id?.toLowerCase().includes(search.toLowerCase());
        return ms && (typeFilter === "all" || f.target_type === typeFilter);
    });

    const handleCreate = async () => {
        if (!form.ride_id.trim()) { alert("Enter a ride ID."); return; }
        setSaving(true);
        try { await flagRideParticipant(form.ride_id, { target_type: form.target_type, reason: form.reason, description: form.description }); setDialogOpen(false); setForm({ ride_id: "", target_type: "driver", reason: "other", description: "" }); load(); }
        catch (e: any) { alert(e.message); } finally { setSaving(false); }
    };

    return (
        <div className="space-y-4">
            <div className="grid grid-cols-4 gap-3">
                <Card><CardContent className="pt-3 pb-2"><div className="flex items-center gap-2"><Flag className="h-4 w-4 text-violet-500" /><div><p className="text-[10px] text-muted-foreground">Total</p><p className="text-xl font-bold">{stats.total}</p></div></div></CardContent></Card>
                <Card><CardContent className="pt-3 pb-2"><div className="flex items-center gap-2"><AlertTriangle className="h-4 w-4 text-amber-500" /><div><p className="text-[10px] text-muted-foreground">Active</p><p className="text-xl font-bold">{stats.active}</p></div></div></CardContent></Card>
                <Card><CardContent className="pt-3 pb-2"><div className="flex items-center gap-2"><Users className="h-4 w-4 text-blue-500" /><div><p className="text-[10px] text-muted-foreground">Riders</p><p className="text-xl font-bold">{stats.riders}</p></div></div></CardContent></Card>
                <Card><CardContent className="pt-3 pb-2"><div className="flex items-center gap-2"><Car className="h-4 w-4 text-emerald-500" /><div><p className="text-[10px] text-muted-foreground">Drivers</p><p className="text-xl font-bold">{stats.drivers}</p></div></div></CardContent></Card>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2 flex-1">
                    <div className="relative flex-1 max-w-xs"><Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" /><Input placeholder="Search..." value={search} onChange={(e) => setSearch(e.target.value)} className="pl-9 h-9" /></div>
                    <Select value={typeFilter} onValueChange={setTypeFilter}><SelectTrigger className="w-32 h-9"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">All</SelectItem><SelectItem value="rider">Riders</SelectItem><SelectItem value="driver">Drivers</SelectItem></SelectContent></Select>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={load}><RefreshCw className={`mr-1.5 h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />Refresh</Button>
                    <Button size="sm" onClick={() => setDialogOpen(true)}><Plus className="mr-1.5 h-3.5 w-3.5" />Create Flag</Button>
                </div>
            </div>

            <Card><CardContent className="p-0">
                {loading ? <div className="flex justify-center p-12"><div className="h-7 w-7 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
                : filtered.length === 0 ? <div className="text-center py-12 text-muted-foreground text-sm">No flags found.</div>
                : <Table><TableHeader><TableRow><TableHead>Target</TableHead><TableHead>Reason</TableHead><TableHead>Description</TableHead><TableHead>Status</TableHead><TableHead>Date</TableHead><TableHead className="text-right">Actions</TableHead></TableRow></TableHeader>
                    <TableBody>{filtered.map((f) => (
                        <TableRow key={f.id} className="cursor-pointer hover:bg-muted/50" onClick={() => setSelected(f)}>
                            <TableCell><div className="flex items-center gap-1.5">{f.target_type === "rider" ? <Users className="h-3.5 w-3.5 text-blue-500" /> : <Car className="h-3.5 w-3.5 text-emerald-500" />}<span className="text-sm capitalize">{f.target_type}</span></div></TableCell>
                            <TableCell><Badge variant="outline" className="text-[10px]">{f.reason?.replace(/_/g, " ") || "other"}</Badge></TableCell>
                            <TableCell className="max-w-[200px] truncate text-xs text-muted-foreground">{f.description || "—"}</TableCell>
                            <TableCell>{f.is_active === false ? <Badge className="text-[10px] bg-zinc-500/15 text-zinc-600">Inactive</Badge> : <Badge className="text-[10px] bg-amber-500/15 text-amber-600">Active</Badge>}</TableCell>
                            <TableCell className="text-[10px] text-muted-foreground">{formatDate(f.created_at)}</TableCell>
                            <TableCell className="text-right"><div className="flex justify-end gap-0.5">
                                <Button variant="ghost" size="icon" className="h-7 w-7" onClick={(e) => { e.stopPropagation(); setSelected(f); }}><Eye className="h-3.5 w-3.5" /></Button>
                                {f.is_active !== false && <Button variant="ghost" size="icon" className="h-7 w-7" onClick={(e) => { e.stopPropagation(); deactivateFlag(f.id).then(load); }} title="Deactivate"><EyeOff className="h-3.5 w-3.5" /></Button>}
                                <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive" onClick={(e) => { e.stopPropagation(); if (confirm("Delete?")) deleteFlag(f.id).then(load); }}><Trash2 className="h-3.5 w-3.5" /></Button>
                            </div></TableCell>
                        </TableRow>
                    ))}</TableBody></Table>}
            </CardContent></Card>

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
                            <Button size="sm" variant="destructive" className="flex-1" onClick={() => { if (confirm("Delete?")) deleteFlag(selected.id).then(() => { setSelected(null); load(); }); }}><Trash2 className="h-3.5 w-3.5 mr-1.5" />Delete</Button>
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
                        <Button className="w-full" size="sm" onClick={handleCreate} disabled={saving}>{saving ? "Creating..." : "Create Flag"}</Button>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    );
}
