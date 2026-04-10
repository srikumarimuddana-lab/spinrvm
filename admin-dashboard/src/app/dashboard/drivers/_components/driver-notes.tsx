"use client";

import { useEffect, useState } from "react";
import { getDriverNotes, addDriverNote, deleteDriverNote } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
    MessageSquare, Plus, Trash2, AlertTriangle, FileText, ShieldCheck,
    Flag, Clock, Loader2, StickyNote,
} from "lucide-react";

const CATEGORIES = [
    { value: "general", label: "General", icon: MessageSquare, color: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400" },
    { value: "warning", label: "Warning", icon: AlertTriangle, color: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400" },
    { value: "document", label: "Document", icon: FileText, color: "bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-400" },
    { value: "status_change", label: "Status Change", icon: ShieldCheck, color: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400" },
    { value: "complaint", label: "Complaint", icon: Flag, color: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400" },
];

function fmtDateTime(d: string) {
    if (!d) return "";
    try {
        return new Date(d).toLocaleString("en-CA", {
            month: "short", day: "numeric", year: "numeric",
            hour: "2-digit", minute: "2-digit",
        });
    } catch { return d; }
}

export default function DriverNotes({ driverId }: { driverId: string }) {
    const [notes, setNotes] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [adding, setAdding] = useState(false);
    const [newNote, setNewNote] = useState("");
    const [newCategory, setNewCategory] = useState("general");
    const [saving, setSaving] = useState(false);
    const [deleting, setDeleting] = useState<string | null>(null);

    const loadNotes = async () => {
        setLoading(true);
        try {
            const data = await getDriverNotes(driverId);
            setNotes(Array.isArray(data) ? data : []);
        } catch { setNotes([]); }
        setLoading(false);
    };

    useEffect(() => { loadNotes(); }, [driverId]);

    const handleAdd = async () => {
        if (!newNote.trim()) return;
        setSaving(true);
        try {
            await addDriverNote(driverId, newNote.trim(), newCategory);
            setNewNote("");
            setNewCategory("general");
            setAdding(false);
            loadNotes();
        } catch (e: any) {
            alert(e?.message || "Failed to add note");
        }
        setSaving(false);
    };

    const handleDelete = async (noteId: string) => {
        if (!confirm("Delete this note?")) return;
        setDeleting(noteId);
        try {
            await deleteDriverNote(noteId);
            loadNotes();
        } catch {}
        setDeleting(null);
    };

    const getCategoryConfig = (cat: string) => CATEGORIES.find(c => c.value === cat) || CATEGORIES[0];

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <div>
                    <h4 className="text-sm font-semibold flex items-center gap-2"><StickyNote className="h-4 w-4 text-muted-foreground" /> Staff Notes</h4>
                    <p className="text-xs text-muted-foreground mt-0.5">Internal notes visible to all staff. Not shared with the driver.</p>
                </div>
                {!adding && (
                    <Button size="sm" onClick={() => setAdding(true)} className="h-8">
                        <Plus className="h-3.5 w-3.5 mr-1" /> Add Note
                    </Button>
                )}
            </div>

            {/* Add Note Form */}
            {adding && (
                <div className="bg-muted/30 border rounded-xl p-4 space-y-3">
                    <div className="flex gap-1.5 flex-wrap">
                        {CATEGORIES.map(cat => (
                            <button key={cat.value} onClick={() => setNewCategory(cat.value)}
                                className={`flex items-center gap-1 px-2.5 py-1 rounded-lg text-[10px] font-semibold transition ${
                                    newCategory === cat.value ? cat.color + " ring-1 ring-current/20" : "bg-muted text-muted-foreground hover:bg-muted/80"
                                }`}>
                                <cat.icon className="h-3 w-3" /> {cat.label}
                            </button>
                        ))}
                    </div>
                    <textarea
                        value={newNote}
                        onChange={e => setNewNote(e.target.value)}
                        placeholder="Write a note... (e.g., 'Called driver about expired license — will upload new one by Friday')"
                        className="w-full border rounded-lg px-3 py-2 text-sm min-h-[80px] resize-none bg-background focus:outline-none focus:ring-2 focus:ring-primary/20"
                    />
                    <div className="flex gap-2">
                        <Button size="sm" onClick={handleAdd} disabled={!newNote.trim() || saving} className="h-8">
                            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" /> : <Plus className="h-3.5 w-3.5 mr-1" />}
                            Save Note
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => { setAdding(false); setNewNote(""); }} className="h-8">Cancel</Button>
                    </div>
                </div>
            )}

            {/* Notes List */}
            {loading ? (
                <div className="flex items-center justify-center py-8">
                    <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                </div>
            ) : notes.length === 0 ? (
                <div className="text-center py-10 bg-muted/20 rounded-xl border border-dashed">
                    <StickyNote className="h-8 w-8 mx-auto mb-2 text-muted-foreground/30" />
                    <p className="text-sm text-muted-foreground">No notes yet</p>
                    <p className="text-xs text-muted-foreground/70 mt-0.5">Add a note to track interactions, decisions, or reminders</p>
                </div>
            ) : (
                <div className="space-y-2">
                    {notes.map(note => {
                        const cat = getCategoryConfig(note.category);
                        const CatIcon = cat.icon;
                        return (
                            <div key={note.id} className="bg-card border rounded-xl p-3.5 group hover:shadow-sm transition">
                                <div className="flex items-start justify-between gap-3">
                                    <div className="flex-1 min-w-0">
                                        <div className="flex items-center gap-2 mb-1.5">
                                            <span className={`flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-semibold ${cat.color}`}>
                                                <CatIcon className="h-3 w-3" /> {cat.label}
                                            </span>
                                            <span className="text-[10px] text-muted-foreground flex items-center gap-1">
                                                <Clock className="h-3 w-3" /> {fmtDateTime(note.created_at)}
                                            </span>
                                            {note.staff_name && (
                                                <span className="text-[10px] text-muted-foreground">by {note.staff_name}</span>
                                            )}
                                        </div>
                                        <p className="text-sm text-foreground whitespace-pre-wrap">{note.note}</p>
                                    </div>
                                    <button onClick={() => handleDelete(note.id)} disabled={deleting === note.id}
                                        className="opacity-0 group-hover:opacity-100 transition p-1 text-muted-foreground hover:text-red-500 shrink-0">
                                        {deleting === note.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                                    </button>
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}
