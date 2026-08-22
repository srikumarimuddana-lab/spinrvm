"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
    Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
    Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import { FileCheck, Plus, Pencil, Trash2, RefreshCw, ArrowLeft } from "lucide-react";
import Link from "next/link";
import {
    getDocumentRequirements,
    createDocumentRequirement,
    updateDocumentRequirement,
    deleteDocumentRequirement,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface DocRequirement {
    id: string;
    name: string;
    description?: string;
    document_type?: string;
    is_required: boolean;
    applicable_to: string;
    created_at?: string;
}

const EMPTY_FORM = {
    name: "",
    description: "",
    document_type: "",
    is_required: true,
    applicable_to: "driver",
};

const APPLICABLE_TO_OPTIONS = [
    { value: "driver", label: "Driver" },
    { value: "vehicle", label: "Vehicle" },
    { value: "both", label: "Driver & Vehicle" },
];

// ---------------------------------------------------------------------------
// Requirement Form Modal
// ---------------------------------------------------------------------------

interface ReqModalProps {
    open: boolean;
    req: Partial<DocRequirement> | null;
    onClose: () => void;
    onSave: (data: typeof EMPTY_FORM) => Promise<void>;
}

function ReqModal({ open, req, onClose, onSave }: ReqModalProps) {
    const isNew = !req?.id;
    const [form, setForm] = useState({ ...EMPTY_FORM });
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState("");

    useEffect(() => {
        if (req) {
            setForm({
                name: req.name ?? "",
                description: req.description ?? "",
                document_type: req.document_type ?? "",
                is_required: req.is_required ?? true,
                applicable_to: req.applicable_to ?? "driver",
            });
        } else {
            setForm({ ...EMPTY_FORM });
        }
        setError("");
    }, [req, open]);

    const handleSubmit = async () => {
        if (!form.name.trim()) { setError("Name is required."); return; }
        setSaving(true);
        try {
            await onSave(form);
            onClose();
        } catch {
            setError("Failed to save. Please try again.");
        } finally {
            setSaving(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>{isNew ? "Add Document Requirement" : "Edit Document Requirement"}</DialogTitle>
                </DialogHeader>
                <div className="space-y-4 py-2">
                    <div className="space-y-1">
                        <Label htmlFor="req-name">Name</Label>
                        <Input
                            id="req-name"
                            value={form.name}
                            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                            placeholder="e.g. Driver's License, Vehicle Insurance"
                        />
                    </div>
                    <div className="space-y-1">
                        <Label htmlFor="req-desc">Description</Label>
                        <Input
                            id="req-desc"
                            value={form.description}
                            onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                            placeholder="What drivers need to know about this document"
                        />
                    </div>
                    <div className="space-y-1">
                        <Label htmlFor="req-type">Document Type Key</Label>
                        <Input
                            id="req-type"
                            value={form.document_type}
                            onChange={(e) => setForm((f) => ({ ...f, document_type: e.target.value }))}
                            placeholder="e.g. drivers_license, vehicle_insurance"
                        />
                        <p className="text-xs text-muted-foreground">Internal key used for legacy field mapping.</p>
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                        <div className="space-y-1">
                            <Label>Applies To</Label>
                            <Select
                                value={form.applicable_to}
                                onValueChange={(v) => setForm((f) => ({ ...f, applicable_to: v }))}
                            >
                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    {APPLICABLE_TO_OPTIONS.map((o) => (
                                        <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="flex items-center gap-2 pt-6">
                            <Switch
                                checked={form.is_required}
                                onCheckedChange={(v) => setForm((f) => ({ ...f, is_required: v }))}
                            />
                            <Label>Required</Label>
                        </div>
                    </div>
                    {error && <p className="text-sm text-destructive">{error}</p>}
                </div>
                <DialogFooter>
                    <Button variant="outline" onClick={onClose} disabled={saving}>Cancel</Button>
                    <Button onClick={handleSubmit} disabled={saving}>
                        {saving ? "Saving…" : isNew ? "Add Requirement" : "Save Changes"}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

// ---------------------------------------------------------------------------
// Delete Confirm Modal
// ---------------------------------------------------------------------------

interface DeleteModalProps {
    open: boolean;
    reqName: string;
    onClose: () => void;
    onConfirm: () => Promise<void>;
}

function DeleteModal({ open, reqName, onClose, onConfirm }: DeleteModalProps) {
    const [deleting, setDeleting] = useState(false);
    const handleConfirm = async () => {
        setDeleting(true);
        try { await onConfirm(); onClose(); }
        finally { setDeleting(false); }
    };
    return (
        <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>Delete Document Requirement</DialogTitle>
                </DialogHeader>
                <p className="text-sm text-muted-foreground py-2">
                    Delete <strong>{reqName}</strong>? Existing driver documents referencing this
                    requirement will keep their historical records, but the requirement will no
                    longer appear in the onboarding checklist.
                </p>
                <DialogFooter>
                    <Button variant="outline" onClick={onClose} disabled={deleting}>Cancel</Button>
                    <Button variant="destructive" onClick={handleConfirm} disabled={deleting}>
                        {deleting ? "Deleting…" : "Delete"}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function DocumentRequirementsPage() {
    const [requirements, setRequirements] = useState<DocRequirement[]>([]);
    const [loading, setLoading] = useState(true);

    const [modalOpen, setModalOpen] = useState(false);
    const [editing, setEditing] = useState<DocRequirement | null>(null);

    const [deleteOpen, setDeleteOpen] = useState(false);
    const [deleting, setDeleting] = useState<DocRequirement | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const data = await getDocumentRequirements();
            setRequirements(Array.isArray(data) ? data : []);
        } catch {
            // non-fatal
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const handleSave = async (form: typeof EMPTY_FORM) => {
        if (editing?.id) {
            await updateDocumentRequirement(editing.id, form);
        } else {
            await createDocumentRequirement(form);
        }
        await load();
    };

    const handleDelete = async () => {
        if (!deleting) return;
        await deleteDocumentRequirement(deleting.id);
        await load();
    };

    const openCreate = () => { setEditing(null); setModalOpen(true); };
    const openEdit = (r: DocRequirement) => { setEditing(r); setModalOpen(true); };
    const openDelete = (r: DocRequirement) => { setDeleting(r); setDeleteOpen(true); };

    const driverReqs = requirements.filter((r) => r.applicable_to !== "vehicle");
    const vehicleReqs = requirements.filter((r) => r.applicable_to === "vehicle");

    return (
        <div className="space-y-6 p-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <Link href="/dashboard/documents" className="text-muted-foreground hover:text-foreground">
                        <ArrowLeft className="h-5 w-5" />
                    </Link>
                    <FileCheck className="h-6 w-6 text-primary" />
                    <div>
                        <h1 className="text-2xl font-bold">Document Requirements</h1>
                        <p className="text-sm text-muted-foreground">Configure which documents drivers must upload to go online</p>
                    </div>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" onClick={load} disabled={loading}>
                        <RefreshCw className={`h-4 w-4 mr-2 ${loading ? "animate-spin" : ""}`} />
                        Refresh
                    </Button>
                    <Button onClick={openCreate}>
                        <Plus className="h-4 w-4 mr-2" />
                        Add Requirement
                    </Button>
                </div>
            </div>

            {/* Stats */}
            <div className="grid grid-cols-3 gap-4">
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <p className="text-xs text-muted-foreground">Total Requirements</p>
                        <p className="text-2xl font-bold">{requirements.length}</p>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <p className="text-xs text-muted-foreground">Required</p>
                        <p className="text-2xl font-bold text-red-600">{requirements.filter((r) => r.is_required).length}</p>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <p className="text-xs text-muted-foreground">Optional</p>
                        <p className="text-2xl font-bold text-muted-foreground">{requirements.filter((r) => !r.is_required).length}</p>
                    </CardContent>
                </Card>
            </div>

            {/* Driver Requirements */}
            <Card>
                <CardHeader className="pb-3">
                    <CardTitle className="text-base">Driver Requirements</CardTitle>
                </CardHeader>
                <CardContent>
                    <ReqTable
                        rows={driverReqs}
                        loading={loading}
                        onEdit={openEdit}
                        onDelete={openDelete}
                    />
                </CardContent>
            </Card>

            <Separator />

            {/* Vehicle Requirements */}
            <Card>
                <CardHeader className="pb-3">
                    <CardTitle className="text-base">Vehicle Requirements</CardTitle>
                </CardHeader>
                <CardContent>
                    <ReqTable
                        rows={vehicleReqs}
                        loading={loading}
                        onEdit={openEdit}
                        onDelete={openDelete}
                    />
                </CardContent>
            </Card>

            <ReqModal
                open={modalOpen}
                req={editing}
                onClose={() => setModalOpen(false)}
                onSave={handleSave}
            />
            <DeleteModal
                open={deleteOpen}
                reqName={deleting?.name ?? ""}
                onClose={() => setDeleteOpen(false)}
                onConfirm={handleDelete}
            />
        </div>
    );
}

function ReqTable({
    rows,
    loading,
    onEdit,
    onDelete,
}: {
    rows: DocRequirement[];
    loading: boolean;
    onEdit: (r: DocRequirement) => void;
    onDelete: (r: DocRequirement) => void;
}) {
    const { sorted, sort, toggle } = useTableSort(rows);
    if (!loading && rows.length === 0) {
        return <p className="text-sm text-muted-foreground py-4 text-center">No requirements configured.</p>;
    }
    return (
        <Table>
            <TableHeader>
                <TableRow>
                    <SortableHead column="name" sort={sort} onSort={toggle}>Name</SortableHead>
                    <SortableHead column="document_type" sort={sort} onSort={toggle}>Type Key</SortableHead>
                    <SortableHead column="applicable_to" sort={sort} onSort={toggle}>Applies To</SortableHead>
                    <SortableHead column="is_required" sort={sort} onSort={toggle}>Required</SortableHead>
                    <TableHead className="text-right">Actions</TableHead>
                </TableRow>
            </TableHeader>
            <TableBody>
                {sorted.map((r) => (
                    <TableRow key={r.id}>
                        <TableCell className="font-medium">
                            <div>{r.name}</div>
                            {r.description && (
                                <div className="text-xs text-muted-foreground">{r.description}</div>
                            )}
                        </TableCell>
                        <TableCell className="font-mono text-xs text-muted-foreground">
                            {r.document_type || "—"}
                        </TableCell>
                        <TableCell>
                            <Badge variant="outline">
                                {APPLICABLE_TO_OPTIONS.find((o) => o.value === r.applicable_to)?.label ?? r.applicable_to}
                            </Badge>
                        </TableCell>
                        <TableCell>
                            <Badge variant={r.is_required ? "destructive" : "secondary"}>
                                {r.is_required ? "Required" : "Optional"}
                            </Badge>
                        </TableCell>
                        <TableCell className="text-right">
                            <div className="flex justify-end gap-2">
                                <Button size="sm" variant="outline" onClick={() => onEdit(r)}>
                                    <Pencil className="h-3 w-3" />
                                </Button>
                                <Button size="sm" variant="outline" onClick={() => onDelete(r)}>
                                    <Trash2 className="h-3 w-3 text-destructive" />
                                </Button>
                            </div>
                        </TableCell>
                    </TableRow>
                ))}
            </TableBody>
        </Table>
    );
}
