"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getFaqs, createFaq, updateFaq, deleteFaq } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Search, Plus, Pencil, Trash2, RefreshCw } from "lucide-react";
import { formatDate } from "@/lib/utils";

const A_CFG: Record<string, { l: string; c: string }> = {
    rider: { l: "Rider", c: "bg-sky-500/15 text-sky-600" },
    driver: { l: "Driver", c: "bg-emerald-500/15 text-emerald-600" },
    both: { l: "Both", c: "bg-violet-500/15 text-violet-600" },
};

type FaqForm = { question: string; answer: string; category: string; audience: string; is_active: boolean };
const EMPTY: FaqForm = { question: "", answer: "", category: "general", audience: "both", is_active: true };

export default function FaqsTab() {
    const [items, setItems] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [audienceFilter, setAudienceFilter] = useState("all");
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editing, setEditing] = useState<any>(null);
    const [saving, setSaving] = useState(false);
    const [form, setForm] = useState<FaqForm>(EMPTY);
    const reqIdRef = useRef(0);

    const load = useCallback(() => {
        setLoading(true);
        const reqId = ++reqIdRef.current;
        getFaqs()
            .then((rows) => { if (reqId === reqIdRef.current) setItems(Array.isArray(rows) ? rows : []); })
            .catch(() => { if (reqId === reqIdRef.current) setItems([]); })
            .finally(() => { if (reqId === reqIdRef.current) setLoading(false); });
    }, []);
    useEffect(() => { load(); }, [load]);

    const filtered = items.filter((i) => {
        if (audienceFilter !== "all" && (i.audience || "both") !== audienceFilter) return false;
        if (!search) return true;
        const q = search.toLowerCase();
        return i.question?.toLowerCase().includes(q) || i.answer?.toLowerCase().includes(q);
    });

    const openCreate = () => { setEditing(null); setForm(EMPTY); setDialogOpen(true); };
    const openEdit = (item: any) => {
        setEditing(item);
        setForm({
            question: item.question || "",
            answer: item.answer || "",
            category: item.category || "general",
            audience: item.audience || "both",
            is_active: item.is_active !== false,
        });
        setDialogOpen(true);
    };

    const handleSave = async () => {
        if (!form.question.trim() || !form.answer.trim()) { alert("Question and answer are required."); return; }
        setSaving(true);
        try {
            if (editing) await updateFaq(editing.id, form);
            else await createFaq(form);
            setDialogOpen(false); setEditing(null); setForm(EMPTY); load();
        } catch (e: any) { alert(e.message); } finally { setSaving(false); }
    };

    const handleDelete = async (id: string) => {
        if (!confirm("Delete this FAQ?")) return;
        try { await deleteFaq(id); load(); } catch (e: any) { alert(e.message); }
    };

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2 flex-1">
                    <div className="relative flex-1 max-w-xs"><Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" /><Input placeholder="Search FAQs..." value={search} onChange={(e) => setSearch(e.target.value)} className="pl-9 h-9" /></div>
                    <Select value={audienceFilter} onValueChange={setAudienceFilter}><SelectTrigger className="w-36 h-9"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">All audiences</SelectItem><SelectItem value="rider">Rider</SelectItem><SelectItem value="driver">Driver</SelectItem><SelectItem value="both">Both</SelectItem></SelectContent></Select>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={load}><RefreshCw className={`mr-1.5 h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />Refresh</Button>
                    <Button size="sm" onClick={openCreate}><Plus className="mr-1.5 h-3.5 w-3.5" />Add FAQ</Button>
                </div>
            </div>

            <Card><CardContent className="p-0">
                {loading ? <div className="flex justify-center p-12"><div className="h-7 w-7 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
                : filtered.length === 0 ? <div className="text-center py-12 text-muted-foreground text-sm">No FAQs found.</div>
                : <Table><TableHeader><TableRow><TableHead>Question</TableHead><TableHead>Category</TableHead><TableHead>Audience</TableHead><TableHead>Status</TableHead><TableHead>Updated</TableHead><TableHead className="text-right">Actions</TableHead></TableRow></TableHeader>
                    <TableBody>{filtered.map((item) => {
                        const aud = item.audience || "both";
                        return (
                            <TableRow key={item.id}>
                                <TableCell className="font-medium max-w-[420px] truncate text-sm">{item.question}</TableCell>
                                <TableCell className="text-xs text-muted-foreground">{item.category || "—"}</TableCell>
                                <TableCell><Badge className={`text-[10px] ${(A_CFG[aud] || A_CFG.both).c}`}>{(A_CFG[aud] || A_CFG.both).l}</Badge></TableCell>
                                <TableCell><Badge className={`text-[10px] ${item.is_active === false ? "bg-slate-500/15 text-slate-600" : "bg-emerald-500/15 text-emerald-600"}`}>{item.is_active === false ? "Hidden" : "Active"}</Badge></TableCell>
                                <TableCell className="text-[10px] text-muted-foreground">{formatDate(item.updated_at || item.created_at)}</TableCell>
                                <TableCell className="text-right"><div className="flex justify-end gap-0.5">
                                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openEdit(item)}><Pencil className="h-3.5 w-3.5" /></Button>
                                    <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive" onClick={() => handleDelete(item.id)}><Trash2 className="h-3.5 w-3.5" /></Button>
                                </div></TableCell>
                            </TableRow>
                        );
                    })}</TableBody></Table>}
            </CardContent></Card>

            <Dialog open={dialogOpen} onOpenChange={(o) => { if (!o) { setDialogOpen(false); setEditing(null); } }}>
                <DialogContent className="sm:max-w-lg"><DialogHeader><DialogTitle className="text-base">{editing ? "Edit FAQ" : "Add FAQ"}</DialogTitle></DialogHeader>
                    <div className="space-y-3">
                        <div className="space-y-1.5"><Label className="text-xs">Question *</Label><Input placeholder="e.g. How do I cancel a ride?" value={form.question} onChange={(e) => setForm({ ...form, question: e.target.value })} /></div>
                        <div className="space-y-1.5"><Label className="text-xs">Answer *</Label><Textarea placeholder="Plain-text answer shown in the app." value={form.answer} onChange={(e) => setForm({ ...form, answer: e.target.value })} rows={5} /></div>
                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-1.5"><Label className="text-xs">Category</Label><Input placeholder="general" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} /></div>
                            <div className="space-y-1.5"><Label className="text-xs">Audience</Label><Select value={form.audience} onValueChange={(v) => setForm({ ...form, audience: v })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="both">Both apps</SelectItem><SelectItem value="rider">Rider only</SelectItem><SelectItem value="driver">Driver only</SelectItem></SelectContent></Select></div>
                        </div>
                        <div className="flex items-center gap-2"><Switch checked={form.is_active} onCheckedChange={(v) => setForm({ ...form, is_active: v })} /><Label className="text-xs">Active (visible in app)</Label></div>
                        <Button className="w-full" size="sm" onClick={handleSave} disabled={saving}>{saving ? "Saving..." : editing ? "Save Changes" : "Create FAQ"}</Button>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    );
}
