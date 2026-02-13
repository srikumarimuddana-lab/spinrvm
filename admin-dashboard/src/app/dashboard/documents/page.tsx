"use client";

import { useEffect, useState } from "react";
import { Plus, Pencil, Trash2, Check, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogFooter,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
    getRequirements,
    createRequirement,
    updateRequirement,
    deleteRequirement,
} from "@/lib/api";

type Requirement = {
    id: string;
    name: string;
    description: string;
    is_mandatory: boolean;
    requires_back_side: boolean;
    created_at: string;
};

export default function DocumentRequirementsPage() {
    const [requirements, setRequirements] = useState<Requirement[]>([]);
    const [loading, setLoading] = useState(true);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editingId, setEditingId] = useState<string | null>(null);

    // Form State
    const [name, setName] = useState("");
    const [description, setDescription] = useState("");
    const [isMandatory, setIsMandatory] = useState(true);
    const [requiresBackSide, setRequiresBackSide] = useState(false);

    const fetchRequirements = async () => {
        try {
            setLoading(true);
            const data = await getRequirements();
            setRequirements(data);
        } catch (err) {
            console.error("Failed to fetch requirements:", err);
            // alert("Failed to fetch requirements");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchRequirements();
    }, []);

    const handleOpenDialog = (req?: Requirement) => {
        if (req) {
            setEditingId(req.id);
            setName(req.name);
            setDescription(req.description || "");
            setIsMandatory(req.is_mandatory);
            setRequiresBackSide(req.requires_back_side);
        } else {
            setEditingId(null);
            setName("");
            setDescription("");
            setIsMandatory(true);
            setRequiresBackSide(false);
        }
        setDialogOpen(true);
    };

    const handleSave = async () => {
        try {
            const payload = {
                name,
                description,
                is_mandatory: isMandatory,
                requires_back_side: requiresBackSide,
            };

            if (editingId) {
                await updateRequirement(editingId, payload);
            } else {
                await createRequirement(payload);
            }
            setDialogOpen(false);
            fetchRequirements();
        } catch (err) {
            console.error("Failed to save requirement:", err);
            alert("Failed to save. Please try again.");
        }
    };

    const handleDelete = async (id: string) => {
        if (!confirm("Are you sure you want to delete this requirement?")) return;
        try {
            await deleteRequirement(id);
            fetchRequirements();
        } catch (err) {
            console.error("Failed to delete:", err);
            alert("Failed to delete.");
        }
    };

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <h1 className="text-3xl font-bold tracking-tight">Driver Documents</h1>
                <Button onClick={() => handleOpenDialog()}>
                    <Plus className="mr-2 h-4 w-4" />
                    Add Requirement
                </Button>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle>Document Requirements</CardTitle>
                </CardHeader>
                <CardContent>
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Name</TableHead>
                                <TableHead>Description</TableHead>
                                <TableHead className="text-center">Mandatory</TableHead>
                                <TableHead className="text-center">Back Side</TableHead>
                                <TableHead className="text-right">Actions</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {loading ? (
                                <TableRow>
                                    <TableCell colSpan={5} className="text-center py-8">
                                        Loading...
                                    </TableCell>
                                </TableRow>
                            ) : requirements.length === 0 ? (
                                <TableRow>
                                    <TableCell colSpan={5} className="text-center py-8 text-muted-foreground">
                                        No requirements found. Add one to get started.
                                    </TableCell>
                                </TableRow>
                            ) : (
                                requirements.map((req) => (
                                    <TableRow key={req.id}>
                                        <TableCell className="font-medium">{req.name}</TableCell>
                                        <TableCell>{req.description}</TableCell>
                                        <TableCell className="text-center">
                                            {req.is_mandatory ? (
                                                <Check className="mx-auto h-4 w-4 text-green-500" />
                                            ) : (
                                                <X className="mx-auto h-4 w-4 text-muted-foreground" />
                                            )}
                                        </TableCell>
                                        <TableCell className="text-center">
                                            {req.requires_back_side ? (
                                                <Check className="mx-auto h-4 w-4 text-blue-500" />
                                            ) : (
                                                <X className="mx-auto h-4 w-4 text-muted-foreground" />
                                            )}
                                        </TableCell>
                                        <TableCell className="text-right">
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                onClick={() => handleOpenDialog(req)}
                                            >
                                                <Pencil className="h-4 w-4" />
                                            </Button>
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                className="text-destructive hover:text-destructive"
                                                onClick={() => handleDelete(req.id)}
                                            >
                                                <Trash2 className="h-4 w-4" />
                                            </Button>
                                        </TableCell>
                                    </TableRow>
                                ))
                            )}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>

            <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                <DialogContent className="sm:max-w-[425px]">
                    <DialogHeader>
                        <DialogTitle>
                            {editingId ? "Edit Requirement" : "Add Requirement"}
                        </DialogTitle>
                    </DialogHeader>
                    <div className="grid gap-4 py-4">
                        <div className="grid grid-cols-4 items-center gap-4">
                            <Label htmlFor="name" className="text-right">
                                Name
                            </Label>
                            <Input
                                id="name"
                                value={name}
                                onChange={(e) => setName(e.target.value)}
                                className="col-span-3"
                                placeholder="e.g. Driving License"
                            />
                        </div>
                        <div className="grid grid-cols-4 items-center gap-4">
                            <Label htmlFor="description" className="text-right">
                                Description
                            </Label>
                            <Input
                                id="description"
                                value={description}
                                onChange={(e) => setDescription(e.target.value)}
                                className="col-span-3"
                                placeholder="Brief description"
                            />
                        </div>
                        <div className="grid grid-cols-4 items-center gap-4">
                            <Label htmlFor="mandatory" className="text-right">
                                Mandatory
                            </Label>
                            <div className="col-span-3 flex items-center space-x-2">
                                <Switch
                                    id="mandatory"
                                    checked={isMandatory}
                                    onCheckedChange={setIsMandatory}
                                />
                                <Label htmlFor="mandatory" className="text-muted-foreground font-normal">
                                    Driver cannot register without this
                                </Label>
                            </div>
                        </div>
                        <div className="grid grid-cols-4 items-center gap-4">
                            <Label htmlFor="backside" className="text-right">
                                Back Side
                            </Label>
                            <div className="col-span-3 flex items-center space-x-2">
                                <Switch
                                    id="backside"
                                    checked={requiresBackSide}
                                    onCheckedChange={setRequiresBackSide}
                                />
                                <Label htmlFor="backside" className="text-muted-foreground font-normal">
                                    Requires scanning back of document
                                </Label>
                            </div>
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setDialogOpen(false)}>
                            Cancel
                        </Button>
                        <Button onClick={handleSave}>Save changes</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
