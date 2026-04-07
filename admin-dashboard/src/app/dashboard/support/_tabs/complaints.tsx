"use client";

import { useEffect, useState } from "react";
import { getComplaints, resolveComplaint, deleteComplaint } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { FileWarning, Search, CheckCircle, Clock, XCircle, Trash2 } from "lucide-react";

const STATUS_COLORS: Record<string, string> = {
    open: "bg-amber-100 text-amber-700",
    investigating: "bg-blue-100 text-blue-700",
    resolved: "bg-emerald-100 text-emerald-700",
    dismissed: "bg-gray-100 text-gray-600",
};

const CATEGORY_COLORS: Record<string, string> = {
    safety: "bg-red-100 text-red-700",
    behavior: "bg-amber-100 text-amber-700",
    fraud: "bg-purple-100 text-purple-700",
    damage: "bg-orange-100 text-orange-700",
    other: "bg-blue-100 text-blue-700",
};

export default function ComplaintsTab() {
    const [complaints, setComplaints] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [statusFilter, setStatusFilter] = useState("all");
    const [selected, setSelected] = useState<any>(null);
    const [resolution, setResolution] = useState("");

    const loadComplaints = () => {
        setLoading(true);
        getComplaints().then(setComplaints).catch(() => {}).finally(() => setLoading(false));
    };

    useEffect(() => { loadComplaints(); }, []);

    const filtered = complaints.filter(c => {
        const q = search.toLowerCase();
        const matchSearch = !search ||
            c.description?.toLowerCase().includes(q) ||
            c.category?.toLowerCase().includes(q) ||
            c.ride_id?.toLowerCase().includes(q) ||
            c.against_id?.toLowerCase().includes(q);
        const matchStatus = statusFilter === "all" || c.status === statusFilter;
        return matchSearch && matchStatus;
    });

    const statusCounts: Record<string, number> = {
        all: complaints.length,
        open: complaints.filter(c => c.status === "open").length,
        investigating: complaints.filter(c => c.status === "investigating").length,
        resolved: complaints.filter(c => c.status === "resolved").length,
        dismissed: complaints.filter(c => c.status === "dismissed").length,
    };

    const handleResolve = async (status: "resolved" | "dismissed") => {
        if (!selected || !resolution.trim()) return;
        try {
            await resolveComplaint(selected.id, { status, resolution: resolution.trim() });
            loadComplaints();
            setSelected(null);
            setResolution("");
        } catch (e: any) { alert(e.message || "Failed"); }
    };

    const handleDelete = async (id: string) => {
        if (!confirm("Permanently delete this complaint?")) return;
        try {
            await deleteComplaint(id);
            loadComplaints();
            setSelected(null);
        } catch (e: any) { alert(e.message || "Failed"); }
    };

    return (
        <div>
            {/* Stats */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 sm:gap-3 mb-4">
                {(["open", "investigating", "resolved", "dismissed"] as const).map(s => (
                    <div key={s} className="bg-card border rounded-xl p-2.5 sm:p-3 flex items-center gap-2 sm:gap-3">
                        <div className={`w-8 h-8 sm:w-9 sm:h-9 rounded-lg flex items-center justify-center shrink-0 ${STATUS_COLORS[s]}`}>
                            {s === "open" ? <Clock className="h-3.5 w-3.5 sm:h-4 sm:w-4" /> :
                             s === "resolved" ? <CheckCircle className="h-3.5 w-3.5 sm:h-4 sm:w-4" /> :
                             <XCircle className="h-3.5 w-3.5 sm:h-4 sm:w-4" />}
                        </div>
                        <div>
                            <p className="text-lg sm:text-xl font-bold">{statusCounts[s]}</p>
                            <p className="text-[10px] text-muted-foreground capitalize">{s}</p>
                        </div>
                    </div>
                ))}
            </div>

            {/* Filters */}
            <div className="flex gap-2 mb-3 flex-wrap">
                {["all", "open", "investigating", "resolved", "dismissed"].map(s => (
                    <button key={s} onClick={() => setStatusFilter(s)}
                        className={`px-3 py-1.5 rounded-lg text-xs font-semibold ${statusFilter === s ? "bg-primary text-white" : "bg-muted text-muted-foreground"}`}>
                        {s === "all" ? "All" : s} ({statusCounts[s]})
                    </button>
                ))}
            </div>

            <div className="relative mb-3">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                <Input placeholder="Search description, category, ride ID..." value={search} onChange={e => setSearch(e.target.value)} className="pl-9 h-9 text-sm" />
            </div>

            {/* List */}
            {loading ? (
                <div className="flex items-center justify-center py-16"><div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
            ) : filtered.length === 0 ? (
                <div className="text-center py-16 text-muted-foreground"><FileWarning className="h-10 w-10 mx-auto mb-3 opacity-30" /><p>No complaints found</p></div>
            ) : (
                <div className="space-y-2">
                    {filtered.map(c => (
                        <div key={c.id} onClick={() => { setSelected(c); setResolution(""); }}
                            className="flex items-start gap-3 p-3 bg-card border rounded-xl cursor-pointer hover:shadow-sm transition-shadow">
                            <FileWarning className="h-5 w-5 mt-0.5 shrink-0 text-amber-500" />
                            <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2 flex-wrap">
                                    <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${c.against_type === "rider" ? "bg-blue-100 text-blue-700" : "bg-emerald-100 text-emerald-700"}`}>
                                        vs {c.against_type?.toUpperCase()}
                                    </span>
                                    <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${CATEGORY_COLORS[c.category] || "bg-gray-100 text-gray-600"}`}>
                                        {c.category}
                                    </span>
                                    <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${STATUS_COLORS[c.status] || "bg-gray-100 text-gray-600"}`}>
                                        {c.status}
                                    </span>
                                </div>
                                <p className="text-sm mt-1 line-clamp-2">{c.description}</p>
                                <div className="flex gap-3 text-xs text-muted-foreground mt-1 flex-wrap">
                                    <span>Ride: <span className="font-mono">{c.ride_id?.slice(0, 8)}</span></span>
                                    <span>Against: <span className="font-mono">{c.against_id?.slice(0, 12)}</span></span>
                                    <span>{c.created_at ? new Date(c.created_at).toLocaleString() : ""}</span>
                                </div>
                            </div>
                            <button onClick={e => { e.stopPropagation(); handleDelete(c.id); }}
                                title="Delete" className="p-1.5 rounded-lg hover:bg-red-100 text-red-600 shrink-0">
                                <Trash2 className="h-3.5 w-3.5" />
                            </button>
                        </div>
                    ))}
                </div>
            )}

            {/* Detail/Resolve Dialog */}
            <Dialog open={!!selected} onOpenChange={v => !v && setSelected(null)}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <FileWarning className="h-5 w-5 text-amber-500" /> Complaint Details
                        </DialogTitle>
                    </DialogHeader>
                    {selected && (
                        <div className="space-y-3">
                            <div className="grid grid-cols-2 gap-3">
                                <div>
                                    <p className="text-[10px] text-muted-foreground uppercase font-bold">Against</p>
                                    <p className="text-sm font-medium capitalize">{selected.against_type}</p>
                                </div>
                                <div>
                                    <p className="text-[10px] text-muted-foreground uppercase font-bold">Category</p>
                                    <p className="text-sm font-medium capitalize">{selected.category}</p>
                                </div>
                                <div>
                                    <p className="text-[10px] text-muted-foreground uppercase font-bold">Status</p>
                                    <p className="text-sm font-medium capitalize">{selected.status}</p>
                                </div>
                                <div>
                                    <p className="text-[10px] text-muted-foreground uppercase font-bold">Created</p>
                                    <p className="text-xs">{selected.created_at ? new Date(selected.created_at).toLocaleString() : "—"}</p>
                                </div>
                            </div>
                            <div>
                                <p className="text-[10px] text-muted-foreground uppercase font-bold">Description</p>
                                <p className="text-sm bg-muted/30 rounded-lg p-2 mt-1">{selected.description}</p>
                            </div>
                            <div className="text-xs text-muted-foreground">
                                <p>Ride: <span className="font-mono">{selected.ride_id}</span></p>
                                <p>Against: <span className="font-mono">{selected.against_id}</span></p>
                            </div>
                            {selected.resolution && (
                                <div>
                                    <p className="text-[10px] text-muted-foreground uppercase font-bold">Resolution</p>
                                    <p className="text-sm bg-primary/10 rounded-lg p-2 mt-1">{selected.resolution}</p>
                                </div>
                            )}
                            {(selected.status === "open" || selected.status === "investigating") && (
                                <>
                                    <div>
                                        <label className="text-xs font-medium text-muted-foreground">Resolution Notes</label>
                                        <textarea value={resolution} onChange={e => setResolution(e.target.value)}
                                            placeholder="Enter resolution details..."
                                            className="w-full mt-1 border rounded-lg px-3 py-2 text-sm bg-card text-foreground min-h-[60px] resize-none" />
                                    </div>
                                    <div className="flex gap-2">
                                        <button onClick={() => handleResolve("resolved")} disabled={!resolution.trim()}
                                            className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 text-xs font-semibold rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50">
                                            <CheckCircle className="h-3.5 w-3.5" /> Resolve
                                        </button>
                                        <button onClick={() => handleResolve("dismissed")} disabled={!resolution.trim()}
                                            className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 text-xs font-semibold rounded-lg bg-gray-600 text-white hover:bg-gray-700 disabled:opacity-50">
                                            <XCircle className="h-3.5 w-3.5" /> Dismiss
                                        </button>
                                    </div>
                                </>
                            )}
                            <DialogFooter>
                                <button onClick={() => handleDelete(selected.id)}
                                    className="flex items-center gap-1.5 px-3 py-2 text-xs font-semibold rounded-lg bg-red-100 text-red-700 hover:bg-red-200">
                                    <Trash2 className="h-3.5 w-3.5" /> Delete
                                </button>
                            </DialogFooter>
                        </div>
                    )}
                </DialogContent>
            </Dialog>
        </div>
    );
}
