"use client";

import { useEffect, useState } from "react";
import {
    getVehicleTypes,
    createVehicleType,
    updateVehicleType,
    deleteVehicleType,
} from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
    DialogTrigger,
} from "@/components/ui/dialog";
import { Car, Plus, Pencil, Trash2, Users, Image as ImageIcon } from "lucide-react";

interface VehicleType {
    id: string;
    name: string;
    description: string;
    icon: string;
    capacity: number;
    image_url?: string;
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
    const [types, setTypes] = useState<VehicleType[]>([]);
    const [loading, setLoading] = useState(true);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editingId, setEditingId] = useState<string | null>(null);
    const [form, setForm] = useState(EMPTY_FORM);
    const [saving, setSaving] = useState(false);

    const fetchTypes = () => {
        setLoading(true);
        getVehicleTypes()
            .then(setTypes)
            .catch(() => { })
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
            image_url: vt.image_url || "",
            is_active: vt.is_active,
        });
        setDialogOpen(true);
    };

    const handleSave = async () => {
        setSaving(true);
        try {
            if (editingId) {
                await updateVehicleType(editingId, form);
            } else {
                await createVehicleType(form);
            }
            setDialogOpen(false);
            fetchTypes();
        } catch (err) {
            console.error("Error saving vehicle type:", err);
        } finally {
            setSaving(false);
        }
    };

    const handleDelete = async (id: string) => {
        if (!confirm("Are you sure you want to delete this vehicle type?")) return;
        try {
            await deleteVehicleType(id);
            fetchTypes();
        } catch (err) {
            console.error("Error deleting vehicle type:", err);
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
        } catch (err) {
            console.error("Error toggling vehicle type:", err);
        }
    };

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
                                {vt.image_url ? (
                                    <img
                                        src={vt.image_url}
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
                                Image URL
                            </Label>
                            <Input
                                placeholder="https://example.com/car.png"
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
        </div>
    );
}
