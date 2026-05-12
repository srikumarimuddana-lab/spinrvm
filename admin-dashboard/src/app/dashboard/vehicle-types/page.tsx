"use client";

import { useEffect, useState } from "react";
import {
    getVehicleTypes,
    createVehicleType,
    updateVehicleType,
    deleteVehicleType,
    adminUploadVehicleIllustration,
} from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Car, Plus, Pencil, Trash2, Users, Image as ImageIcon } from "lucide-react";
import { useRequireModule } from "@/hooks/useRequireModule";
import { useToast } from "@/components/ui/use-toast";
import {
    AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
    AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";

interface VehicleType {
    id: string;
    name: string;
    description: string;
    icon: string;
    capacity: number;
    // Admin-uploaded car image. DB column is `illustration_url`; the rider-
    // facing GET aliases it to `image_url`. Admin GET returns raw rows, so
    // we accept either here for backward compat.
    image_url?: string;
    illustration_url?: string;
    is_active: boolean;
    created_at?: string;
}

const EMPTY_FORM: Omit<VehicleType, "id" | "created_at"> = {
    name: "",
    description: "",
    icon: "car",
    capacity: 4,
    image_url: "",
    is_active: true,
};

export default function VehicleTypesPage() {
    const { allowed } = useRequireModule("pricing");
    const { toast } = useToast();
    const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
    const [types, setTypes] = useState<VehicleType[]>([]);
    const [loading, setLoading] = useState(true);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editingId, setEditingId] = useState<string | null>(null);
    const [form, setForm] = useState(EMPTY_FORM);
    const [saving, setSaving] = useState(false);
    const [uploading, setUploading] = useState(false);

    const handleUploadIllustration = async (file: File) => {
        if (!editingId) {
            toast({
                title: "Save the type first",
                description: "Create the vehicle type before uploading an illustration so we can key the file by id.",
                variant: "destructive",
            });
            return;
        }
        if (file.size > 500 * 1024) {
            toast({ title: "File too large", description: "Max 500 KB.", variant: "destructive" });
            return;
        }
        setUploading(true);
        try {
            const { illustration_url } = await adminUploadVehicleIllustration(editingId, file);
            setForm({ ...form, image_url: illustration_url });
            toast({ title: "Illustration uploaded" });
        } catch (err) {
            console.error(err);
            toast({ title: "Upload failed", description: String((err as Error).message || err), variant: "destructive" });
        } finally {
            setUploading(false);
        }
    };

    const fetchTypes = () => {
        setLoading(true);
        getVehicleTypes()
            .then(setTypes)
            .catch(() => {
                toast({ title: "Failed to load vehicle types", description: "Please refresh the page.", variant: "destructive" });
            })
            .finally(() => setLoading(false));
    };

    useEffect(() => {
        fetchTypes();
    }, []);

    const openCreate = () => {
        setEditingId(null);
        setForm({ ...EMPTY_FORM });
        setDialogOpen(true);
    };

    const openEdit = (vt: VehicleType) => {
        setEditingId(vt.id);
        setForm({
            name: vt.name,
            description: vt.description,
            icon: vt.icon,
            capacity: vt.capacity,
            // Backend admin GET returns raw rows; rider-facing GET aliases
            // illustration_url → image_url, but here we read either.
            image_url: vt.image_url || (vt as { illustration_url?: string }).illustration_url || "",
            is_active: vt.is_active,
        });
        setDialogOpen(true);
    };

    const handleSave = async () => {
        setSaving(true);
        try {
            // Backend persists the URL as `illustration_url`; the form keeps
            // the legacy `image_url` name internally because the rider-app's
            // existing type expects that. The /vehicle-types GET also returns
            // both keys (mapped server-side) so reads stay consistent.
            const { image_url, ...rest } = form;
            const payload = { ...rest, illustration_url: image_url || null };
            if (editingId) {
                await updateVehicleType(editingId, payload);
            } else {
                await createVehicleType(payload);
            }
            toast({ title: "Vehicle type saved" });
            setDialogOpen(false);
            fetchTypes();
        } catch (err) {
            console.error("Error saving vehicle type:", err);
            toast({ title: "Save failed", description: "Could not save vehicle type. Please try again.", variant: "destructive" });
        } finally {
            setSaving(false);
        }
    };

    const handleDelete = async (id: string) => {
        setDeleteTarget(id);
    };

    const confirmDelete = async () => {
        if (!deleteTarget) return;
        try {
            await deleteVehicleType(deleteTarget);
            toast({ title: "Vehicle type deleted" });
            fetchTypes();
        } catch (err: any) {
            // 409 from backend means still referenced by a service area or fare config.
            toast({ title: "Could not delete vehicle type", description: err?.message, variant: "destructive" });
        } finally {
            setDeleteTarget(null);
        }
    };

    const handleToggleActive = async (vt: VehicleType) => {
        try {
            await updateVehicleType(vt.id, { is_active: !vt.is_active });
            setTypes((prev) =>
                prev.map((t) =>
                    t.id === vt.id ? { ...t, is_active: !t.is_active } : t
                )
            );
            toast({ title: `Vehicle type ${!vt.is_active ? 'activated' : 'deactivated'}` });
        } catch (err) {
            console.error("Error toggling vehicle type:", err);
            toast({ title: "Update failed", description: "Could not update vehicle type status. Please try again.", variant: "destructive" });
        }
    };

    if (!allowed) return null;

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Vehicle Types</h1>
                    <p className="text-muted-foreground mt-1">
                        Manage the car types available for rides. Add images, set capacity,
                        and toggle availability.
                    </p>
                </div>
                <Button onClick={openCreate} className="gap-2">
                    <Plus className="h-4 w-4" />
                    Add Vehicle Type
                </Button>
            </div>

            {/* Content */}
            {loading ? (
                <div className="flex items-center justify-center p-12">
                    <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                </div>
            ) : types.length === 0 ? (
                <Card className="border-dashed">
                    <CardContent className="flex flex-col items-center justify-center py-16 text-center">
                        <Car className="h-12 w-12 text-muted-foreground/50 mb-4" />
                        <h3 className="text-lg font-semibold">No vehicle types yet</h3>
                        <p className="text-muted-foreground text-sm mt-1 mb-4">
                            Add your first vehicle type to get started.
                        </p>
                        <Button onClick={openCreate} variant="outline" className="gap-2">
                            <Plus className="h-4 w-4" />
                            Add Vehicle Type
                        </Button>
                    </CardContent>
                </Card>
            ) : (
                <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                    {types.map((vt) => (
                        <Card
                            key={vt.id}
                            className={`relative overflow-hidden transition-all ${!vt.is_active ? "opacity-60" : ""
                                }`}
                        >
                            {/* Image */}
                            <div className="h-32 bg-muted flex items-center justify-center">
                                {(vt.image_url || vt.illustration_url) ? (
                                    <img
                                        src={vt.image_url || vt.illustration_url}
                                        alt={vt.name}
                                        className="h-full w-full object-contain p-4"
                                    />
                                ) : (
                                    <Car className="h-16 w-16 text-muted-foreground/30" />
                                )}
                            </div>

                            <CardContent className="pt-4 space-y-3">
                                <div className="flex items-center justify-between">
                                    <h3 className="font-semibold text-lg">{vt.name}</h3>
                                    <Badge variant={vt.is_active ? "default" : "secondary"}>
                                        {vt.is_active ? "Active" : "Inactive"}
                                    </Badge>
                                </div>

                                <p className="text-sm text-muted-foreground">
                                    {vt.description}
                                </p>

                                <div className="flex items-center gap-4 text-sm text-muted-foreground">
                                    <span className="flex items-center gap-1">
                                        <Users className="h-3.5 w-3.5" />
                                        {vt.capacity} seats
                                    </span>
                                    <span className="flex items-center gap-1">
                                        <Car className="h-3.5 w-3.5" />
                                        {vt.icon}
                                    </span>
                                </div>

                                {/* Actions */}
                                <div className="flex items-center justify-between pt-2 border-t">
                                    <Switch
                                        checked={vt.is_active}
                                        onCheckedChange={() => handleToggleActive(vt)}
                                    />
                                    <div className="flex gap-1">
                                        <Button
                                            variant="ghost"
                                            size="icon"
                                            onClick={() => openEdit(vt)}
                                        >
                                            <Pencil className="h-4 w-4" />
                                        </Button>
                                        <Button
                                            variant="ghost"
                                            size="icon"
                                            className="text-destructive hover:text-destructive"
                                            onClick={() => handleDelete(vt.id)}
                                        >
                                            <Trash2 className="h-4 w-4" />
                                        </Button>
                                    </div>
                                </div>
                            </CardContent>
                        </Card>
                    ))}
                </div>
            )}

            {/* Create/Edit Dialog */}
            <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>
                            {editingId ? "Edit Vehicle Type" : "Add Vehicle Type"}
                        </DialogTitle>
                    </DialogHeader>

                    <div className="space-y-4">
                        <div className="space-y-2">
                            <Label>Name</Label>
                            <Input
                                placeholder="e.g. Spinr Go"
                                value={form.name}
                                onChange={(e) =>
                                    setForm({ ...form, name: e.target.value })
                                }
                            />
                        </div>

                        <div className="space-y-2">
                            <Label>Description</Label>
                            <Input
                                placeholder="e.g. Affordable rides"
                                value={form.description}
                                onChange={(e) =>
                                    setForm({ ...form, description: e.target.value })
                                }
                            />
                        </div>

                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <Label>Icon Name</Label>
                                <Input
                                    placeholder="car"
                                    value={form.icon}
                                    onChange={(e) =>
                                        setForm({ ...form, icon: e.target.value })
                                    }
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>Capacity</Label>
                                <Input
                                    type="number"
                                    min={1}
                                    max={20}
                                    value={form.capacity}
                                    onChange={(e) =>
                                        setForm({
                                            ...form,
                                            capacity: parseInt(e.target.value) || 4,
                                        })
                                    }
                                />
                            </div>
                        </div>

                        <div className="space-y-2">
                            <Label className="flex items-center gap-2">
                                <ImageIcon className="h-4 w-4" />
                                Illustration
                            </Label>
                            <div className="flex items-center gap-2">
                                <Input
                                    type="file"
                                    accept="image/png,image/jpeg,image/webp"
                                    disabled={uploading || !editingId}
                                    onChange={(e) => {
                                        const f = e.target.files?.[0];
                                        if (f) void handleUploadIllustration(f);
                                        e.target.value = ""; // allow re-selecting same file
                                    }}
                                />
                                {uploading && (
                                    <span className="text-xs text-muted-foreground">Uploading…</span>
                                )}
                            </div>
                            {!editingId && (
                                <p className="text-xs text-muted-foreground">
                                    Save the vehicle type first, then re-open to upload the illustration.
                                </p>
                            )}
                            <Input
                                placeholder="…or paste a hosted URL"
                                value={form.image_url}
                                onChange={(e) =>
                                    setForm({ ...form, image_url: e.target.value })
                                }
                            />
                            {form.image_url && (
                                <div className="mt-2 h-24 rounded-lg bg-muted flex items-center justify-center">
                                    <img
                                        src={form.image_url}
                                        alt="Preview"
                                        className="h-full object-contain p-2"
                                        onError={(e) => {
                                            (e.target as HTMLImageElement).style.display = "none";
                                        }}
                                    />
                                </div>
                            )}
                        </div>

                        <div className="flex items-center justify-between">
                            <Label>Active</Label>
                            <Switch
                                checked={form.is_active}
                                onCheckedChange={(checked) =>
                                    setForm({ ...form, is_active: checked })
                                }
                            />
                        </div>

                        <div className="flex justify-end gap-2 pt-2">
                            <Button
                                variant="outline"
                                onClick={() => setDialogOpen(false)}
                            >
                                Cancel
                            </Button>
                            <Button
                                onClick={handleSave}
                                disabled={saving || !form.name}
                            >
                                {saving ? "Saving..." : editingId ? "Update" : "Create"}
                            </Button>
                        </div>
                    </div>
                </DialogContent>
            </Dialog>

            <AlertDialog open={!!deleteTarget} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Delete vehicle type?</AlertDialogTitle>
                        <AlertDialogDescription>This cannot be undone. If this type is referenced by a fare config or service area, deletion will fail.</AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={confirmDelete} className="bg-red-600 hover:bg-red-700">Delete</AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}
