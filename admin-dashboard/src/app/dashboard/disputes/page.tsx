"use client";

import { useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
    Dialog, DialogContent, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
    Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
    HelpCircle, Search, CheckCircle, XCircle, Clock, Plus, Pencil, Trash2, RefreshCw, Eye,
} from "lucide-react";
import { formatDate } from "@/lib/utils";
import { getDisputes, createDispute, updateDispute, resolveDispute, deleteDispute } from "@/lib/api";

const STATUS_CONFIG: Record<string, { label: string; icon: any; color: string }> = {
    pending: { label: "Pending", icon: Clock, color: "bg-amber-500/15 text-amber-600" },
    open: { label: "Open", icon: Clock, color: "bg-amber-500/15 text-amber-600" },
    resolved: { label: "Resolved", icon: CheckCircle, color: "bg-emerald-500/15 text-emerald-600" },
    rejected: { label: "Rejected", icon: XCircle, color: "bg-red-500/15 text-red-600" },
};

const DISPUTE_TYPES = [
    { value: "fare", label: "Fare Dispute" },
    { value: "behavior", label: "Behavior Issue" },
    { value: "route", label: "Route Issue" },
    { value: "safety", label: "Safety Concern" },
    { value: "damage", label: "Vehicle Damage" },
    { value: "other", label: "Other" },
];

export default function DisputesPage() {
    const [disputes, setDisputes] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [statusFilter, setStatusFilter] = useState("all");
    const [selectedDispute, setSelectedDispute] = useState<any>(null);
    const [resolution, setResolution] = useState("");

    // Create/Edit dialog
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editingDispute, setEditingDispute] = useState<any>(null);
    const [saving, setSaving] = useState(false);
    const [form, setForm] = useState({
        ride_id: "",
        user_name: "",
        user_type: "rider",
        reason: "fare",
        description: "",
        refund_amount: "",
    });

    const fetchDisputes = async () => {
        setLoading(true);
        try {
            const data = await getDisputes();
            const transformed = (data || []).map((d: any) => ({
                id: d.id,
                ride_id: d.ride_id,
                user_name: d.user_name || "Unknown",
                user_type: d.user_type || "rider",
                dispute_type: d.reason || "other",
                description: d.description,
                status: d.status || "pending",
                refund_amount: d.refund_amount,
                created_at: d.created_at,
                resolution: d.admin_note || d.resolution_notes || d.resolution,
            }));
            setDisputes(transformed);
        } catch {
            setDisputes([]);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { fetchDisputes(); }, []);

    const stats = {
        pending: disputes.filter((d) => d.status === "open" || d.status === "pending").length,
        resolved: disputes.filter((d) => d.status === "resolved").length,
        rejected: disputes.filter((d) => d.status === "rejected").length,
    };

    const filtered = disputes.filter((d) => {
        const matchSearch = !search || d.user_name?.toLowerCase().includes(search.toLowerCase()) || d.ride_id?.toLowerCase().includes(search.toLowerCase()) || d.description?.toLowerCase().includes(search.toLowerCase());
        const matchStatus = statusFilter === "all" || d.status === statusFilter || (statusFilter === "pending" && d.status === "open");
        return matchSearch && matchStatus;
    });

    const resetForm = () => {
        setForm({ ride_id: "", user_name: "", user_type: "rider", reason: "fare", description: "", refund_amount: "" });
        setEditingDispute(null);
    };

    const openCreate = () => { resetForm(); setDialogOpen(true); };

    const openEdit = (d: any) => {
        setEditingDispute(d);
        setForm({
            ride_id: d.ride_id || "",
            user_name: d.user_name || "",
            user_type: d.user_type || "rider",
            reason: d.dispute_type || "fare",
            description: d.description || "",
            refund_amount: d.refund_amount ? String(d.refund_amount) : "",
        });
        setDialogOpen(true);
    };

    const handleSave = async () => {
        if (!form.description.trim()) { alert("Please enter a description."); return; }
        setSaving(true);
        try {
            if (editingDispute) {
                await updateDispute(editingDispute.id, {
                    reason: form.reason,
                    description: form.description,
                    refund_amount: form.refund_amount ? parseFloat(form.refund_amount) : 0,
                    user_type: form.user_type,
                });
            } else {
                await createDispute({
                    ride_id: form.ride_id || null,
                    user_name: form.user_name,
                    user_type: form.user_type,
                    reason: form.reason,
                    description: form.description,
                    refund_amount: form.refund_amount ? parseFloat(form.refund_amount) : 0,
                });
            }
            setDialogOpen(false);
            resetForm();
            fetchDisputes();
        } catch (e: any) {
            alert(`Failed: ${e.message}`);
        } finally {
            setSaving(false);
        }
    };

    const handleResolve = async (status: "approved" | "rejected") => {
        if (!selectedDispute || !resolution.trim()) { alert("Please enter resolution notes."); return; }
        try {
            await resolveDispute(selectedDispute.id, { resolution: status, admin_note: resolution.trim(), refund_amount: selectedDispute.refund_amount });
            await fetchDisputes();
            setSelectedDispute(null);
            setResolution("");
        } catch (e: any) {
            alert(`Failed: ${e.message}`);
        }
    };

    const handleDelete = async (id: string) => {
        if (!confirm("Delete this dispute?")) return;
        try {
            await deleteDispute(id);
            if (selectedDispute?.id === id) setSelectedDispute(null);
            fetchDisputes();
        } catch (e: any) {
            alert(`Failed: ${e.message}`);
        }
    };

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
                        <HelpCircle className="h-8 w-8 text-amber-500" />
                        Disputes
                    </h1>
                    <p className="text-muted-foreground mt-1">Review and resolve disputes from riders and drivers.</p>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={fetchDisputes} disabled={loading}><RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} /> Refresh</Button>
                    <Button size="sm" onClick={openCreate}><Plus className="mr-2 h-4 w-4" /> Create Dispute</Button>
                </div>
            </div>

            {/* Stats */}
            <div className="grid grid-cols-3 gap-4">
                <Card><CardContent className="pt-4 pb-3"><div className="flex items-center gap-2"><Clock className="h-5 w-5 text-amber-500" /><div><p className="text-xs text-muted-foreground">Pending</p><p className="text-2xl font-bold">{stats.pending}</p></div></div></CardContent></Card>
                <Card><CardContent className="pt-4 pb-3"><div className="flex items-center gap-2"><CheckCircle className="h-5 w-5 text-emerald-500" /><div><p className="text-xs text-muted-foreground">Resolved</p><p className="text-2xl font-bold">{stats.resolved}</p></div></div></CardContent></Card>
                <Card><CardContent className="pt-4 pb-3"><div className="flex items-center gap-2"><XCircle className="h-5 w-5 text-red-500" /><div><p className="text-xs text-muted-foreground">Rejected</p><p className="text-2xl font-bold">{stats.rejected}</p></div></div></CardContent></Card>
            </div>

            {/* Filters */}
            <div className="flex flex-wrap items-center gap-3">
                <div className="relative flex-1 max-w-sm">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input placeholder="Search disputes..." value={search} onChange={(e) => setSearch(e.target.value)} className="pl-9" />
                </div>
                <Select value={statusFilter} onValueChange={setStatusFilter}>
                    <SelectTrigger className="w-40"><SelectValue placeholder="Status" /></SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All Status</SelectItem>
                        <SelectItem value="pending">Pending</SelectItem>
                        <SelectItem value="resolved">Resolved</SelectItem>
                        <SelectItem value="rejected">Rejected</SelectItem>
                    </SelectContent>
                </Select>
            </div>

            {/* Table */}
            <Card className="border-border/50">
                <CardContent className="p-0">
                    {loading ? (
                        <div className="flex items-center justify-center p-12"><div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
                    ) : filtered.length === 0 ? (
                        <div className="text-center py-12 text-muted-foreground">No disputes found.</div>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>User</TableHead>
                                    <TableHead>Type</TableHead>
                                    <TableHead>Description</TableHead>
                                    <TableHead>Status</TableHead>
                                    <TableHead>Date</TableHead>
                                    <TableHead className="text-right">Actions</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {filtered.map((dispute) => {
                                    const sc = STATUS_CONFIG[dispute.status] || STATUS_CONFIG.pending;
                                    const SI = sc.icon;
                                    return (
                                        <TableRow key={dispute.id} className="cursor-pointer hover:bg-muted/50" onClick={() => { setSelectedDispute(dispute); setResolution(""); }}>
                                            <TableCell>
                                                <p className="font-medium">{dispute.user_name}</p>
                                                <p className="text-xs text-muted-foreground capitalize">{dispute.user_type}</p>
                                            </TableCell>
                                            <TableCell>
                                                <Badge variant="outline" className="text-xs">{DISPUTE_TYPES.find((t) => t.value === dispute.dispute_type)?.label || dispute.dispute_type}</Badge>
                                            </TableCell>
                                            <TableCell className="max-w-[200px] truncate text-muted-foreground">{dispute.description}</TableCell>
                                            <TableCell><Badge className={sc.color}><SI className="h-3 w-3 mr-1" />{sc.label}</Badge></TableCell>
                                            <TableCell className="text-xs text-muted-foreground">{formatDate(dispute.created_at)}</TableCell>
                                            <TableCell className="text-right">
                                                <div className="flex justify-end gap-1">
                                                    <Button variant="ghost" size="icon" className="h-8 w-8" onClick={(e) => { e.stopPropagation(); setSelectedDispute(dispute); setResolution(""); }}><Eye className="h-4 w-4" /></Button>
                                                    <Button variant="ghost" size="icon" className="h-8 w-8" onClick={(e) => { e.stopPropagation(); openEdit(dispute); }}><Pencil className="h-4 w-4" /></Button>
                                                    <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive" onClick={(e) => { e.stopPropagation(); handleDelete(dispute.id); }}><Trash2 className="h-4 w-4" /></Button>
                                                </div>
                                            </TableCell>
                                        </TableRow>
                                    );
                                })}
                            </TableBody>
                        </Table>
                    )}
                </CardContent>
            </Card>

            {/* Review Dialog */}
            <Dialog open={!!selectedDispute && !dialogOpen} onOpenChange={(open) => { if (!open) setSelectedDispute(null); }}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader><DialogTitle className="flex items-center gap-2"><HelpCircle className="h-5 w-5 text-amber-500" /> Review Dispute</DialogTitle></DialogHeader>
                    {selectedDispute && (
                        <div className="space-y-4">
                            <div className="grid grid-cols-2 gap-4">
                                <div><Label className="text-xs text-muted-foreground">Ride ID</Label><p className="font-mono text-sm">{selectedDispute.ride_id || "N/A"}</p></div>
                                <div><Label className="text-xs text-muted-foreground">User</Label><p className="text-sm">{selectedDispute.user_name} ({selectedDispute.user_type})</p></div>
                            </div>
                            <div className="grid grid-cols-2 gap-4">
                                <div><Label className="text-xs text-muted-foreground">Type</Label><p className="text-sm capitalize">{DISPUTE_TYPES.find((t) => t.value === selectedDispute.dispute_type)?.label || selectedDispute.dispute_type}</p></div>
                                <div><Label className="text-xs text-muted-foreground">Status</Label><div className="mt-0.5"><Badge className={STATUS_CONFIG[selectedDispute.status]?.color || "bg-zinc-500/15"}>{STATUS_CONFIG[selectedDispute.status]?.label || selectedDispute.status}</Badge></div></div>
                            </div>
                            <div><Label className="text-xs text-muted-foreground">Description</Label><div className="rounded-lg bg-muted/50 p-3 text-sm mt-1">{selectedDispute.description}</div></div>
                            {selectedDispute.resolution && (
                                <div><Label className="text-xs text-muted-foreground">Resolution</Label><div className="rounded-lg bg-primary/5 border border-primary/10 p-3 text-sm mt-1">{selectedDispute.resolution}</div></div>
                            )}
                            {(selectedDispute.status === "pending" || selectedDispute.status === "open") && (
                                <>
                                    <div className="space-y-2">
                                        <Label>Resolution Notes</Label>
                                        <Textarea placeholder="Enter resolution details..." value={resolution} onChange={(e) => setResolution(e.target.value)} rows={3} />
                                    </div>
                                    <div className="flex gap-2">
                                        <Button className="flex-1 bg-emerald-600 hover:bg-emerald-700" onClick={() => handleResolve("approved")} disabled={!resolution.trim()}><CheckCircle className="h-4 w-4 mr-2" /> Resolve</Button>
                                        <Button variant="destructive" className="flex-1" onClick={() => handleResolve("rejected")} disabled={!resolution.trim()}><XCircle className="h-4 w-4 mr-2" /> Reject</Button>
                                    </div>
                                </>
                            )}
                        </div>
                    )}
                </DialogContent>
            </Dialog>

            {/* Create/Edit Dialog */}
            <Dialog open={dialogOpen} onOpenChange={(open) => { if (!open) { setDialogOpen(false); resetForm(); } }}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader><DialogTitle>{editingDispute ? "Edit Dispute" : "Create Dispute"}</DialogTitle></DialogHeader>
                    <div className="space-y-4">
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <Label>User Name</Label>
                                <Input placeholder="e.g. John Doe" value={form.user_name} onChange={(e) => setForm({ ...form, user_name: e.target.value })} />
                            </div>
                            <div className="space-y-2">
                                <Label>User Type</Label>
                                <Select value={form.user_type} onValueChange={(v) => setForm({ ...form, user_type: v })}>
                                    <SelectTrigger><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="rider">Rider</SelectItem>
                                        <SelectItem value="driver">Driver</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <Label>Dispute Type</Label>
                                <Select value={form.reason} onValueChange={(v) => setForm({ ...form, reason: v })}>
                                    <SelectTrigger><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        {DISPUTE_TYPES.map((t) => <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>)}
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-2">
                                <Label>Ride ID</Label>
                                <Input placeholder="Optional" value={form.ride_id} onChange={(e) => setForm({ ...form, ride_id: e.target.value })} />
                            </div>
                        </div>
                        <div className="space-y-2">
                            <Label>Description <span className="text-destructive">*</span></Label>
                            <Textarea placeholder="Describe the dispute..." value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} rows={4} />
                        </div>
                        <div className="space-y-2 max-w-[200px]">
                            <Label>Refund Amount ($)</Label>
                            <Input type="number" placeholder="0.00" value={form.refund_amount} onChange={(e) => setForm({ ...form, refund_amount: e.target.value })} />
                        </div>
                        <Button className="w-full" onClick={handleSave} disabled={saving}>{saving ? "Saving..." : editingDispute ? "Update Dispute" : "Create Dispute"}</Button>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    );
}
