"use client";

import { useEffect, useState, useMemo, lazy, Suspense } from "react";
import {
    getServiceAreas,
    createServiceArea,
    updateServiceArea,
    deleteServiceArea,
} from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from "@/components/ui/dialog";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Plus, Trash2, Pencil, MapPin, Eye, Plane } from "lucide-react";

// Lazy-load Leaflet map to avoid SSR issues
const GeofenceMap = lazy(() => import("@/components/geofence-map"));

/* ── City presets with bounding polygons ── */
const CITY_PRESETS: Record<
    string,
    { city: string; center: { lat: number; lng: number }; polygon: { lat: number; lng: number }[] }
> = {
    saskatoon: {
        city: "Saskatoon",
        center: { lat: 52.13, lng: -106.67 },
        polygon: [
            { lat: 52.19, lng: -106.75 },
            { lat: 52.19, lng: -106.55 },
            { lat: 52.08, lng: -106.55 },
            { lat: 52.08, lng: -106.75 },
        ],
    },
    regina: {
        city: "Regina",
        center: { lat: 50.45, lng: -104.6 },
        polygon: [
            { lat: 50.5, lng: -104.7 },
            { lat: 50.5, lng: -104.5 },
            { lat: 50.4, lng: -104.5 },
            { lat: 50.4, lng: -104.7 },
        ],
    },
    toronto: {
        city: "Toronto",
        center: { lat: 43.7, lng: -79.4 },
        polygon: [
            { lat: 43.85, lng: -79.64 },
            { lat: 43.85, lng: -79.12 },
            { lat: 43.58, lng: -79.12 },
            { lat: 43.58, lng: -79.64 },
        ],
    },
    vancouver: {
        city: "Vancouver",
        center: { lat: 49.26, lng: -123.14 },
        polygon: [
            { lat: 49.32, lng: -123.26 },
            { lat: 49.32, lng: -123.02 },
            { lat: 49.2, lng: -123.02 },
            { lat: 49.2, lng: -123.26 },
        ],
    },
    calgary: {
        city: "Calgary",
        center: { lat: 51.03, lng: -114.08 },
        polygon: [
            { lat: 51.18, lng: -114.27 },
            { lat: 51.18, lng: -113.9 },
            { lat: 50.88, lng: -113.9 },
            { lat: 50.88, lng: -114.27 },
        ],
    },
};

function polygonToText(polygon: { lat: number; lng: number }[]): string {
    return polygon.map((p) => `${p.lat}, ${p.lng}`).join("\n");
}

function textToPolygon(text: string): { lat: number; lng: number }[] {
    return text
        .trim()
        .split("\n")
        .filter((line) => line.trim())
        .map((line) => {
            const [lat, lng] = line.split(",").map((s) => parseFloat(s.trim()));
            return { lat, lng };
        })
        .filter((p) => !isNaN(p.lat) && !isNaN(p.lng));
}

export default function ServiceAreasPage() {
    const [areas, setAreas] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editing, setEditing] = useState<any>(null);
    const [viewingArea, setViewingArea] = useState<any>(null);
    const [form, setForm] = useState({
        name: "",
        city: "",
        polygon: [] as { lat: number; lng: number }[],
        polygonText: "",
        is_active: true,
        is_airport: false,
        airport_fee: 0,
    });
    // Key to force map remount when polygon comes from preset
    const [mapKey, setMapKey] = useState(0);

    const fetchAreas = () => {
        setLoading(true);
        getServiceAreas()
            .then(setAreas)
            .catch(() => { })
            .finally(() => setLoading(false));
    };

    useEffect(() => {
        fetchAreas();
    }, []);

    const handlePresetSelect = (presetKey: string) => {
        if (presetKey === "custom") {
            setForm({ ...form, city: "", polygon: [], polygonText: "" });
            setMapKey((k) => k + 1);
            return;
        }
        const preset = CITY_PRESETS[presetKey];
        if (preset) {
            setForm({
                ...form,
                name: form.name || preset.city,
                city: preset.city,
                polygon: preset.polygon,
                polygonText: polygonToText(preset.polygon),
            });
            setMapKey((k) => k + 1);
        }
    };

    const handlePolygonFromMap = (pts: { lat: number; lng: number }[]) => {
        setForm((prev) => ({
            ...prev,
            polygon: pts,
            polygonText: polygonToText(pts),
        }));
    };

    const handlePolygonTextChange = (text: string) => {
        const pts = textToPolygon(text);
        setForm((prev) => ({
            ...prev,
            polygonText: text,
            polygon: pts.length >= 3 ? pts : prev.polygon,
        }));
        if (pts.length >= 3) setMapKey((k) => k + 1);
    };

    const handleSave = async () => {
        if (form.polygon.length < 3) {
            alert(
                "Draw at least 3 points on the map or enter coordinates to define the geofence."
            );
            return;
        }

        const payload = {
            name: form.name,
            city: form.city,
            polygon: form.polygon,
            is_active: form.is_active,
            is_airport: form.is_airport,
            airport_fee: form.is_airport ? form.airport_fee : 0,
        };

        try {
            if (editing) {
                await updateServiceArea(editing.id, payload);
            } else {
                await createServiceArea(payload);
            }
            setDialogOpen(false);
            setEditing(null);
            setForm({ name: "", city: "", polygon: [], polygonText: "", is_active: true, is_airport: false, airport_fee: 0 });
            fetchAreas();
        } catch (e: any) {
            alert(e.message || "Failed to save service area");
        }
    };

    const handleDelete = async (id: string) => {
        if (!confirm("Delete this service area?")) return;
        try {
            await deleteServiceArea(id);
            fetchAreas();
        } catch { }
    };

    const openEdit = (area: any) => {
        setEditing(area);
        const poly = area.polygon || [];
        setForm({
            name: area.name || "",
            city: area.city || "",
            polygon: poly,
            polygonText: polygonToText(poly),
            is_active: area.is_active ?? true,
            is_airport: area.is_airport ?? false,
            airport_fee: area.airport_fee ?? 0,
        });
        setMapKey((k) => k + 1);
        setDialogOpen(true);
    };

    const openCreate = () => {
        setEditing(null);
        setForm({ name: "", city: "", polygon: [], polygonText: "", is_active: true, is_airport: false, airport_fee: 0 });
        setMapKey((k) => k + 1);
        setDialogOpen(true);
    };

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Service Areas</h1>
                    <p className="text-muted-foreground mt-1">
                        Draw geofence boundaries on the map to define where Spinr operates.
                    </p>
                </div>
                <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                    <DialogTrigger asChild>
                        <Button onClick={openCreate}>
                            <Plus className="mr-2 h-4 w-4" /> Add Area
                        </Button>
                    </DialogTrigger>
                    <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto">
                        <DialogHeader>
                            <DialogTitle>
                                {editing ? "Edit" : "Create"} Service Area
                            </DialogTitle>
                        </DialogHeader>
                        <div className="space-y-4 pt-2">
                            {/* City Preset */}
                            {!editing && (
                                <div className="space-y-2">
                                    <Label>Quick Start — City Preset</Label>
                                    <Select onValueChange={handlePresetSelect}>
                                        <SelectTrigger>
                                            <SelectValue placeholder="Select a city or draw custom..." />
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="custom">Custom (draw on map)</SelectItem>
                                            {Object.entries(CITY_PRESETS).map(([key, p]) => (
                                                <SelectItem key={key} value={key}>
                                                    {p.city}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                            )}

                            <div className="grid grid-cols-2 gap-4">
                                <div className="space-y-2">
                                    <Label>Area Name</Label>
                                    <Input
                                        value={form.name}
                                        onChange={(e) => setForm({ ...form, name: e.target.value })}
                                        placeholder="e.g. Downtown Saskatoon"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>City</Label>
                                    <Input
                                        value={form.city}
                                        onChange={(e) => setForm({ ...form, city: e.target.value })}
                                        placeholder="e.g. Saskatoon"
                                    />
                                </div>
                            </div>

                            <Separator />

                            {/* Interactive Map */}
                            <div className="space-y-2">
                                <Label>
                                    <MapPin className="mr-1 inline h-4 w-4 text-violet-500" />
                                    Draw Geofence on Map
                                </Label>
                                <p className="text-xs text-muted-foreground mb-2">
                                    Use the polygon/rectangle tools on the map to draw the service
                                    boundary. Click vertices to create a custom shape, or select a
                                    city preset above.
                                </p>
                                <Suspense
                                    fallback={
                                        <div className="flex items-center justify-center h-[400px] bg-muted rounded-lg">
                                            <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                                        </div>
                                    }
                                >
                                    <GeofenceMap
                                        key={mapKey}
                                        polygon={form.polygon.length >= 3 ? form.polygon : undefined}
                                        center={
                                            form.city
                                                ? Object.values(CITY_PRESETS).find(
                                                    (p) => p.city.toLowerCase() === form.city.toLowerCase()
                                                )?.center
                                                : undefined
                                        }
                                        onPolygonChange={handlePolygonFromMap}
                                        height="400px"
                                    />
                                </Suspense>
                            </div>

                            {/* Coordinate text (advanced / read view) */}
                            <details className="group">
                                <summary className="cursor-pointer text-sm text-muted-foreground hover:text-foreground">
                                    Advanced: View/edit raw coordinates (
                                    {form.polygon.length} points)
                                </summary>
                                <Textarea
                                    value={form.polygonText}
                                    onChange={(e) => handlePolygonTextChange(e.target.value)}
                                    placeholder={`52.19, -106.75\n52.19, -106.55\n52.08, -106.55\n52.08, -106.75`}
                                    rows={4}
                                    className="font-mono text-xs mt-2"
                                />
                            </details>

                            <Separator />

                            {/* Airport Zone Toggle */}
                            <div className="rounded-lg border border-border/60 p-4 space-y-3">
                                <div className="flex items-center justify-between">
                                    <div className="flex items-center gap-2">
                                        <Plane className="h-4 w-4 text-sky-500" />
                                        <Label htmlFor="airport-toggle" className="font-medium">
                                            Airport Zone
                                        </Label>
                                    </div>
                                    <Switch
                                        id="airport-toggle"
                                        checked={form.is_airport}
                                        onCheckedChange={(v) =>
                                            setForm({ ...form, is_airport: v })
                                        }
                                    />
                                </div>
                                <p className="text-xs text-muted-foreground">
                                    When enabled, rides starting from or ending in this zone
                                    will have an airport surcharge added to the fare.
                                </p>
                                {form.is_airport && (
                                    <div className="space-y-2 pt-1">
                                        <Label>Airport Fee ($)</Label>
                                        <Input
                                            type="number"
                                            min={0}
                                            step={0.5}
                                            value={form.airport_fee}
                                            onChange={(e) =>
                                                setForm({
                                                    ...form,
                                                    airport_fee: parseFloat(e.target.value) || 0,
                                                })
                                            }
                                            placeholder="e.g. 5.00"
                                        />
                                    </div>
                                )}
                            </div>

                            <Button className="w-full" onClick={handleSave}>
                                {editing ? "Update" : "Create"} Service Area
                                {form.polygon.length > 0 && (
                                    <span className="ml-2 text-xs opacity-70">
                                        ({form.polygon.length} points)
                                    </span>
                                )}
                            </Button>
                        </div>
                    </DialogContent>
                </Dialog>
            </div>

            {/* View polygon on map dialog */}
            <Dialog
                open={!!viewingArea}
                onOpenChange={(v) => !v && setViewingArea(null)}
            >
                <DialogContent className="sm:max-w-xl">
                    <DialogHeader>
                        <DialogTitle>
                            <MapPin className="mr-2 inline h-5 w-5 text-violet-500" />
                            {viewingArea?.name} — Geofence
                        </DialogTitle>
                    </DialogHeader>
                    {viewingArea && (
                        <Suspense
                            fallback={
                                <div className="flex items-center justify-center h-[350px] bg-muted rounded-lg">
                                    <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                                </div>
                            }
                        >
                            <GeofenceMap
                                key={`view-${viewingArea.id}`}
                                polygon={viewingArea.polygon}
                                readonly
                                height="350px"
                            />
                        </Suspense>
                    )}
                </DialogContent>
            </Dialog>

            <Card className="border-border/50">
                <CardContent className="p-0">
                    {loading ? (
                        <div className="flex items-center justify-center p-12">
                            <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                        </div>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Name</TableHead>
                                    <TableHead>City</TableHead>
                                    <TableHead>Geofence</TableHead>
                                    <TableHead>Type</TableHead>
                                    <TableHead>Surge</TableHead>
                                    <TableHead>Status</TableHead>
                                    <TableHead className="text-right">Actions</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {areas.length === 0 ? (
                                    <TableRow>
                                        <TableCell
                                            colSpan={7}
                                            className="text-center text-muted-foreground py-12"
                                        >
                                            No service areas yet. Click &quot;Add Area&quot; to draw
                                            your first geofence.
                                        </TableCell>
                                    </TableRow>
                                ) : (
                                    areas.map((area) => (
                                        <TableRow key={area.id}>
                                            <TableCell className="font-medium">
                                                <div className="flex items-center gap-2">
                                                    <MapPin className="h-4 w-4 text-violet-500" />
                                                    {area.name}
                                                </div>
                                            </TableCell>
                                            <TableCell>{area.city || "—"}</TableCell>
                                            <TableCell>
                                                <Button
                                                    variant="ghost"
                                                    size="sm"
                                                    className="text-xs"
                                                    onClick={() => setViewingArea(area)}
                                                >
                                                    <Eye className="mr-1 h-3 w-3" />
                                                    {area.polygon?.length || 0} pts
                                                </Button>
                                            </TableCell>
                                            <TableCell>
                                                {area.is_airport ? (
                                                    <Badge
                                                        variant="secondary"
                                                        className="bg-sky-500/15 text-sky-600 dark:text-sky-400"
                                                    >
                                                        <Plane className="mr-1 h-3 w-3" />
                                                        Airport ${area.airport_fee || 0}
                                                    </Badge>
                                                ) : (
                                                    <span className="text-xs text-muted-foreground">Standard</span>
                                                )}
                                            </TableCell>
                                            <TableCell>
                                                <Badge
                                                    variant="secondary"
                                                    className={
                                                        area.surge_active
                                                            ? "bg-amber-500/15 text-amber-600 dark:text-amber-400"
                                                            : "bg-zinc-500/15 text-zinc-600"
                                                    }
                                                >
                                                    {area.surge_active
                                                        ? `${area.surge_multiplier || 1.0}x`
                                                        : "Off"}
                                                </Badge>
                                            </TableCell>
                                            <TableCell>
                                                <Badge
                                                    variant="secondary"
                                                    className={
                                                        area.is_active
                                                            ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                                                            : "bg-zinc-500/15 text-zinc-600"
                                                    }
                                                >
                                                    {area.is_active ? "Active" : "Inactive"}
                                                </Badge>
                                            </TableCell>
                                            <TableCell className="text-right">
                                                <div className="flex justify-end gap-1">
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        onClick={() => openEdit(area)}
                                                    >
                                                        <Pencil className="h-4 w-4" />
                                                    </Button>
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        className="text-destructive"
                                                        onClick={() => handleDelete(area.id)}
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
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
