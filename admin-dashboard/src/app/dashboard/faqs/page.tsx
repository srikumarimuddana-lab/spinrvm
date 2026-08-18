"use client";

import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
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
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import { HelpCircle, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { getFaqs, createFaq, updateFaq, deleteFaq } from "@/lib/api";
import { FAQ_CATEGORIES } from "@/lib/faq-categories";

// ── Types ──────────────────────────────────────────────────────────────────────

interface Faq {
    id: string;
    question: string;
    answer: string;
    category: string;
    audience: string;
    is_active: boolean;
    // Lower sorts first within a category on the public FAQ screens. Existing
    // rows default to 0 — most FAQs will share that value until an admin
    // explicitly promotes one, which is expected (see sort field note below).
    sort_order: number;
    created_at: string;
    updated_at?: string;
}

interface FaqFormState {
    question: string;
    answer: string;
    category: string;
    audience: string;
    is_active: boolean;
    sort_order: number;
}

const EMPTY_FORM: FaqFormState = {
    question: "",
    answer: "",
    category: "general",
    audience: "both",
    is_active: true,
    sort_order: 0,
};

const AUDIENCE_LABELS: Record<string, string> = {
    both: "Both",
    rider: "Riders",
    driver: "Drivers",
};

const CATEGORY_OPTIONS: readonly string[] = FAQ_CATEGORIES;

// ── Component ─────────────────────────────────────────────────────────────────

export default function FaqsPage() {
    const [faqs, setFaqs] = useState<Faq[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const [search, setSearch] = useState("");
    const [audienceFilter, setAudienceFilter] = useState<string>("all");

    const [dialogOpen, setDialogOpen] = useState(false);
    const [editTarget, setEditTarget] = useState<Faq | null>(null);
    const [form, setForm] = useState<FaqFormState>(EMPTY_FORM);
    const [saving, setSaving] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);

    const [deleteId, setDeleteId] = useState<string | null>(null);
    const [deleting, setDeleting] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const data = await getFaqs();
            setFaqs(Array.isArray(data) ? data : []);
        } catch {
            setError("Failed to load FAQs. Please try again.");
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { void load(); }, [load]);

    const filtered = faqs.filter((f) => {
        const matchSearch =
            !search ||
            f.question.toLowerCase().includes(search.toLowerCase()) ||
            f.answer.toLowerCase().includes(search.toLowerCase()) ||
            f.category.toLowerCase().includes(search.toLowerCase());
        const matchAudience = audienceFilter === "all" || f.audience === audienceFilter;
        return matchSearch && matchAudience;
    });

    // Default view matches display order on the public FAQ screens (sort_order
    // asc) rather than whatever order the API happened to return, so the table
    // is a true preview of what riders/drivers see.
    const { sorted, sort, toggle } = useTableSort(filtered, { key: "sort_order", dir: "asc" });

    function openCreate() {
        setEditTarget(null);
        setForm(EMPTY_FORM);
        setSaveError(null);
        setDialogOpen(true);
    }

    function openEdit(faq: Faq) {
        setEditTarget(faq);
        setForm({
            question: faq.question,
            answer: faq.answer,
            category: faq.category,
            audience: faq.audience,
            is_active: faq.is_active,
            sort_order: faq.sort_order ?? 0,
        });
        setSaveError(null);
        setDialogOpen(true);
    }

    async function handleSave() {
        if (!form.question.trim() || !form.answer.trim()) {
            setSaveError("Question and answer are required.");
            return;
        }
        setSaving(true);
        setSaveError(null);
        try {
            if (editTarget) {
                await updateFaq(editTarget.id, form);
            } else {
                await createFaq(form);
            }
            setDialogOpen(false);
            await load();
        } catch {
            setSaveError("Save failed. Please try again.");
        } finally {
            setSaving(false);
        }
    }

    async function handleDelete(id: string) {
        setDeleting(true);
        try {
            await deleteFaq(id);
            setDeleteId(null);
            await load();
        } catch {
            // deletion failure — keep dialog open, user can retry
        } finally {
            setDeleting(false);
        }
    }

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                    <HelpCircle className="h-5 w-5 text-muted-foreground" />
                    <h1 className="text-xl font-semibold">FAQs</h1>
                    <Badge variant="secondary">{faqs.length}</Badge>
                </div>
                <Button onClick={openCreate} size="sm">
                    <Plus className="h-4 w-4 mr-1" />
                    New FAQ
                </Button>
            </div>

            {/* Filters */}
            <div className="flex gap-2">
                <div className="relative flex-1">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input
                        className="pl-9"
                        placeholder="Search questions, answers, categories…"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                    />
                </div>
                <Select value={audienceFilter} onValueChange={setAudienceFilter}>
                    <SelectTrigger className="w-36" aria-label="Audience">
                        <SelectValue placeholder="Audience" />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All audiences</SelectItem>
                        <SelectItem value="both">Both</SelectItem>
                        <SelectItem value="rider">Riders</SelectItem>
                        <SelectItem value="driver">Drivers</SelectItem>
                    </SelectContent>
                </Select>
            </div>

            {/* Error */}
            {error && (
                <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                    {error}
                </div>
            )}

            {/* Table */}
            <div className="rounded-xl border border-border overflow-hidden">
                <Table>
                    <TableHeader>
                        <TableRow className="bg-muted/40">
                            <SortableHead column="sort_order" sort={sort} onSort={toggle} align="right" className="w-16">Order</SortableHead>
                            <SortableHead column="question" sort={sort} onSort={toggle} className="w-[40%]">Question</SortableHead>
                            <SortableHead column="category" sort={sort} onSort={toggle}>Category</SortableHead>
                            <SortableHead column="audience" sort={sort} onSort={toggle}>Audience</SortableHead>
                            <SortableHead column="is_active" sort={sort} onSort={toggle}>Status</SortableHead>
                            <TableHead className="text-right">Actions</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {loading ? (
                            <TableRow>
                                <TableCell colSpan={6} className="text-center text-muted-foreground py-8">
                                    Loading…
                                </TableCell>
                            </TableRow>
                        ) : filtered.length === 0 ? (
                            <TableRow>
                                <TableCell colSpan={6} className="text-center text-muted-foreground py-8">
                                    {search || audienceFilter !== "all" ? "No FAQs match your filters." : "No FAQs yet. Create one above."}
                                </TableCell>
                            </TableRow>
                        ) : (
                            sorted.map((faq) => (
                                <TableRow key={faq.id}>
                                    <TableCell className="text-right text-sm text-muted-foreground tabular-nums">
                                        {faq.sort_order ?? 0}
                                    </TableCell>
                                    <TableCell className="font-medium max-w-xs truncate" title={faq.question}>
                                        {faq.question}
                                    </TableCell>
                                    <TableCell>
                                        <Badge variant="outline" className="capitalize">
                                            {faq.category}
                                        </Badge>
                                    </TableCell>
                                    <TableCell className="text-sm text-muted-foreground">
                                        {AUDIENCE_LABELS[faq.audience] ?? faq.audience}
                                    </TableCell>
                                    <TableCell>
                                        {faq.is_active ? (
                                            <Badge variant="default">Active</Badge>
                                        ) : (
                                            <Badge variant="secondary">Inactive</Badge>
                                        )}
                                    </TableCell>
                                    <TableCell className="text-right">
                                        <div className="flex justify-end gap-1">
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                aria-label={`Edit FAQ: ${faq.question}`}
                                                onClick={() => openEdit(faq)}
                                            >
                                                <Pencil className="h-4 w-4" />
                                            </Button>
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                aria-label={`Delete FAQ: ${faq.question}`}
                                                className="text-destructive hover:text-destructive"
                                                onClick={() => setDeleteId(faq.id)}
                                            >
                                                <Trash2 className="h-4 w-4" />
                                            </Button>
                                        </div>
                                    </TableCell>
                                </TableRow>
                            ))
                        )}
                    </TableBody>
                </Table>
            </div>

            {/* Create / Edit Dialog */}
            <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                <DialogContent className="max-w-lg">
                    <DialogHeader>
                        <DialogTitle>{editTarget ? "Edit FAQ" : "New FAQ"}</DialogTitle>
                    </DialogHeader>
                    <div className="space-y-4 pt-2">
                        <div className="space-y-1.5">
                            <Label htmlFor="faq-question">Question</Label>
                            <Input
                                id="faq-question"
                                placeholder="What is…?"
                                value={form.question}
                                onChange={(e) => setForm((f) => ({ ...f, question: e.target.value }))}
                            />
                        </div>
                        <div className="space-y-1.5">
                            <Label htmlFor="faq-answer">Answer</Label>
                            <Textarea
                                id="faq-answer"
                                rows={4}
                                placeholder="The answer…"
                                value={form.answer}
                                onChange={(e) => setForm((f) => ({ ...f, answer: e.target.value }))}
                            />
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-1.5">
                                <Label htmlFor="faq-category">Category</Label>
                                <Select
                                    value={form.category}
                                    onValueChange={(v) => setForm((f) => ({ ...f, category: v }))}
                                >
                                    <SelectTrigger id="faq-category">
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {CATEGORY_OPTIONS.map((c) => (
                                            <SelectItem key={c} value={c} className="capitalize">
                                                {c}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-1.5">
                                <Label htmlFor="faq-audience">Audience</Label>
                                <Select
                                    value={form.audience}
                                    onValueChange={(v) => setForm((f) => ({ ...f, audience: v }))}
                                >
                                    <SelectTrigger id="faq-audience">
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="both">Both</SelectItem>
                                        <SelectItem value="rider">Riders</SelectItem>
                                        <SelectItem value="driver">Drivers</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                        </div>
                        <div className="space-y-1.5">
                            <Label htmlFor="faq-sort-order">Display order</Label>
                            <Input
                                id="faq-sort-order"
                                type="number"
                                inputMode="numeric"
                                className="w-28"
                                value={form.sort_order}
                                onChange={(e) => setForm((f) => ({ ...f, sort_order: Number(e.target.value) || 0 }))}
                            />
                            <p className="text-xs text-muted-foreground">
                                Lower numbers show first within a category. Most FAQs are 0 — set a lower value to pin one to the top.
                            </p>
                        </div>
                        <div className="flex items-center gap-2">
                            <Switch
                                id="faq-active"
                                checked={form.is_active}
                                onCheckedChange={(v) => setForm((f) => ({ ...f, is_active: v }))}
                            />
                            <Label htmlFor="faq-active">Active (visible to users)</Label>
                        </div>
                        {saveError && (
                            <p className="text-sm text-destructive">{saveError}</p>
                        )}
                        <div className="flex justify-end gap-2 pt-1">
                            <Button variant="outline" onClick={() => setDialogOpen(false)} disabled={saving}>
                                Cancel
                            </Button>
                            <Button onClick={handleSave} disabled={saving}>
                                {saving ? "Saving…" : editTarget ? "Save changes" : "Create FAQ"}
                            </Button>
                        </div>
                    </div>
                </DialogContent>
            </Dialog>

            {/* Delete Confirmation Dialog */}
            <Dialog open={!!deleteId} onOpenChange={(open) => { if (!open) setDeleteId(null); }}>
                <DialogContent className="max-w-sm">
                    <DialogHeader>
                        <DialogTitle>Delete FAQ?</DialogTitle>
                    </DialogHeader>
                    <p className="text-sm text-muted-foreground pt-1">
                        This FAQ will be permanently removed. This action cannot be undone.
                    </p>
                    <div className="flex justify-end gap-2 pt-2">
                        <Button variant="outline" onClick={() => setDeleteId(null)} disabled={deleting}>
                            Cancel
                        </Button>
                        <Button
                            variant="destructive"
                            disabled={deleting}
                            onClick={() => deleteId && handleDelete(deleteId)}
                        >
                            {deleting ? "Deleting…" : "Delete"}
                        </Button>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    );
}
