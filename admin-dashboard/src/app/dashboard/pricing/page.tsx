"use client";

import { useEffect, useState, useCallback } from "react";
import {
    getServiceAreas,
    getAreaFees,
    createAreaFee,
    updateAreaFee,
    deleteAreaFee,
    getAreaTax,
    updateAreaTax,
    getVehiclePricing,
    getFareConfigs,
    createFareConfig,
    updateFareConfig,
    deleteFareConfig,
} from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
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
import {
    Banknote,
    Plus,
    Trash2,
    Pencil,
    MapPin,
    Car,
    Receipt,
    Percent,
    DollarSign,
    Save,
} from "lucide-react";

/* ── Fee type labels ── */
const FEE_TYPES = [
    { value: "airport", label: "Airport" },
    { value: "night", label: "Night Surcharge" },
    { value: "toll", label: "Toll Fee" },
    { value: "event", label: "Event Fee" },
    { value: "holiday", label: "Holiday Fee" },
    { value: "custom", label: "Custom" },
];

const CALC_MODES = [
    { value: "flat", label: "Flat ($)", icon: DollarSign },
    { value: "per_km", label: "Per KM ($/km)", icon: MapPin },
    { value: "percentage", label: "Percentage (%)", icon: Percent },
];

/* ── Canadian provinces with tax defaults ── */
const PROVINCE_TAX: Record<string, { gst: number; pst: number; hst: number; mode: string }> = {
    AB: { gst: 5, pst: 0, hst: 0, mode: "gst" },
    BC: { gst: 5, pst: 7, hst: 0, mode: "gst+pst" },
    MB: { gst: 5, pst: 7, hst: 0, mode: "gst+pst" },
    NB: { gst: 0, pst: 0, hst: 15, mode: "hst" },
    NL: { gst: 0, pst: 0, hst: 15, mode: "hst" },
    NS: { gst: 0, pst: 0, hst: 15, mode: "hst" },
    ON: { gst: 0, pst: 0, hst: 13, mode: "hst" },
    PE: { gst: 0, pst: 0, hst: 15, mode: "hst" },
    QC: { gst: 5, pst: 9.975, hst: 0, mode: "gst+pst" },
    SK: { gst: 5, pst: 6, hst: 0, mode: "gst+pst" },
    NT: { gst: 5, pst: 0, hst: 0, mode: "gst" },
    NU: { gst: 5, pst: 0, hst: 0, mode: "gst" },
    YT: { gst: 5, pst: 0, hst: 0, mode: "gst" },
};

export default function PricingPage() {
    const [areas, setAreas] = useState<any[]>([]);
    const [selectedAreaId, setSelectedAreaId] = useState<string>("");
    const [loading, setLoading] = useState(true);
    const [activeTab, setActiveTab] = useState("fees");

    // Fee state
    const [fees, setFees] = useState<any[]>([]);
    const [feesLoading, setFeesLoading] = useState(false);
    const [feeDialogOpen, setFeeDialogOpen] = useState(false);
    const [editingFee, setEditingFee] = useState<any>(null);
    const [feeForm, setFeeForm] = useState({
        fee_name: "",
        fee_type: "custom",
        calc_mode: "flat",
        amount: 0,
        description: "",
        conditions: {} as any,
        is_active: true,
    });

    // Tax state
    const [tax, setTax] = useState({
        gst_enabled: true, gst_rate: 5,
        pst_enabled: false, pst_rate: 0,
        hst_enabled: false, hst_rate: 0,
    });
    const [taxSaving, setTaxSaving] = useState(false);

    // Vehicle pricing state
    const [vehiclePricing, setVehiclePricing] = useState<{ fare_configs: any[]; vehicle_types: any[] }>({
        fare_configs: [],
        vehicle_types: [],
    });
    const [vehicleLoading, setVehicleLoading] = useState(false);

    useEffect(() => {
        getServiceAreas().then((a) => {
            setAreas(a);
            setLoading(false);
        }).catch(() => setLoading(false));
    }, []);

    const selectedArea = areas.find((a) => a.id === selectedAreaId);

    // Load data when area changes
    const loadAreaData = useCallback(async (areaId: string) => {
        setFeesLoading(true);
        setVehicleLoading(true);
        try {
            const [feesData, taxData, vpData] = await Promise.all([
                getAreaFees(areaId),
                getAreaTax(areaId),
                getVehiclePricing(areaId),
            ]);
            setFees(feesData);
            setTax(taxData);
            setVehiclePricing(vpData);
        } catch (e) {
            console.error(e);
        } finally {
            setFeesLoading(false);
            setVehicleLoading(false);
        }
    }, []);

    useEffect(() => {
        if (selectedAreaId) loadAreaData(selectedAreaId);
    }, [selectedAreaId, loadAreaData]);

    // ── Fee CRUD ──
    const openCreateFee = () => {
        setEditingFee(null);
        setFeeForm({
            fee_name: "", fee_type: "custom", calc_mode: "flat",
            amount: 0, description: "", conditions: {}, is_active: true,
        });
        setFeeDialogOpen(true);
    };

    const openEditFee = (fee: any) => {
        setEditingFee(fee);
        setFeeForm({
            fee_name: fee.fee_name || "",
            fee_type: fee.fee_type || "custom",
            calc_mode: fee.calc_mode || "flat",
            amount: fee.amount || 0,
            description: fee.description || "",
            conditions: fee.conditions || {},
            is_active: fee.is_active ?? true,
        });
        setFeeDialogOpen(true);
    };

    const handleSaveFee = async () => {
        try {
            if (editingFee) {
                await updateAreaFee(selectedAreaId, editingFee.id, feeForm);
            } else {
                await createAreaFee(selectedAreaId, feeForm);
            }
            setFeeDialogOpen(false);
            loadAreaData(selectedAreaId);
        } catch (e: any) {
            alert(e.message || "Failed to save fee");
        }
    };

    const handleDeleteFee = async (feeId: string) => {
        if (!confirm("Delete this fee?")) return;
        try {
            await deleteAreaFee(selectedAreaId, feeId);
            loadAreaData(selectedAreaId);
        } catch { }
    };

    // ── Tax ──
    const handleSaveTax = async () => {
        setTaxSaving(true);
        try {
            const result = await updateAreaTax(selectedAreaId, tax);
            setTax(result);
        } catch (e: any) {
            alert(e.message || "Failed to save tax config");
        } finally {
            setTaxSaving(false);
        }
    };

    const applyProvinceTax = (code: string) => {
        const pt = PROVINCE_TAX[code];
        if (!pt) return;
        setTax({
            gst_enabled: pt.mode.includes("gst"),
            gst_rate: pt.gst,
            pst_enabled: pt.mode.includes("pst"),
            pst_rate: pt.pst,
            hst_enabled: pt.mode === "hst",
            hst_rate: pt.hst,
        });
    };

    // ── Vehicle pricing save ──
    const handleSaveVehicleConfig = async (config: any) => {
        try {
            if (config.id) {
                await updateFareConfig(config.id, config);
            } else {
                await createFareConfig({ ...config, service_area_id: selectedAreaId });
            }
            loadAreaData(selectedAreaId);
        } catch (e: any) {
            alert(e.message || "Failed to save vehicle pricing");
        }
    };

    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
                    <Banknote className="h-8 w-8 text-emerald-500" />
                    Pricing
                </h1>
                <p className="text-muted-foreground mt-1">
                    Configure fees, vehicle rates, and tax settings per service area.
                </p>
            </div>

            {/* Area Selector */}
            <Card>
                <CardContent className="pt-6">
                    <div className="flex items-center gap-4">
                        <div className="flex-1">
                            <Label className="text-sm font-medium mb-2 block">
                                <MapPin className="inline h-4 w-4 mr-1 text-violet-500" />
                                Select Service Area
                            </Label>
                            <Select value={selectedAreaId} onValueChange={setSelectedAreaId}>
                                <SelectTrigger className="w-full max-w-md">
                                    <SelectValue placeholder={loading ? "Loading areas..." : "Choose a service area..."} />
                                </SelectTrigger>
                                <SelectContent>
                                    {areas.map((area) => (
                                        <SelectItem key={area.id} value={area.id}>
                                            <div className="flex items-center gap-2">
                                                <MapPin className="h-3 w-3" />
                                                {area.name}
                                                <span className="text-xs text-muted-foreground">
                                                    ({area.city})
                                                </span>
                                                {area.is_airport && (
                                                    <Badge variant="secondary" className="text-xs bg-sky-500/15 text-sky-600">
                                                        ✈️
                                                    </Badge>
                                                )}
                                            </div>
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        {selectedArea && (
                            <div className="text-right">
                                <p className="text-sm font-medium">{selectedArea.name}</p>
                                <p className="text-xs text-muted-foreground">{selectedArea.city}</p>
                                <Badge variant="secondary" className={selectedArea.is_active
                                    ? "bg-emerald-500/15 text-emerald-600 mt-1"
                                    : "bg-zinc-500/15 text-zinc-600 mt-1"
                                }>
                                    {selectedArea.is_active ? "Active" : "Inactive"}
                                </Badge>
                            </div>
                        )}
                    </div>
                </CardContent>
            </Card>

            {!selectedAreaId ? (
                <Card>
                    <CardContent className="py-16 text-center text-muted-foreground">
                        <Banknote className="h-12 w-12 mx-auto mb-4 opacity-30" />
                        <p className="text-lg font-medium">Select a service area above</p>
                        <p className="text-sm">to configure its pricing, fees, and taxes</p>
                    </CardContent>
                </Card>
            ) : (
                <Tabs value={activeTab} onValueChange={setActiveTab}>
                    <TabsList className="grid w-full grid-cols-3">
                        <TabsTrigger value="fees" className="flex items-center gap-2">
                            <Receipt className="h-4 w-4" /> Area Fees
                        </TabsTrigger>
                        <TabsTrigger value="vehicles" className="flex items-center gap-2">
                            <Car className="h-4 w-4" /> Vehicle Pricing
                        </TabsTrigger>
                        <TabsTrigger value="tax" className="flex items-center gap-2">
                            <Percent className="h-4 w-4" /> Tax (GST/PST)
                        </TabsTrigger>
                    </TabsList>

                    {/* ═══ AREA FEES TAB ═══ */}
                    <TabsContent value="fees" className="space-y-4">
                        <div className="flex items-center justify-between">
                            <div>
                                <h2 className="text-lg font-semibold">Area Fees</h2>
                                <p className="text-sm text-muted-foreground">
                                    Add fees that apply to rides in this area. Each fee can be a flat rate, per-km charge, or percentage.
                                </p>
                            </div>
                            <Button onClick={openCreateFee}>
                                <Plus className="mr-2 h-4 w-4" /> Add Fee
                            </Button>
                        </div>

                        <Card>
                            <CardContent className="p-0">
                                {feesLoading ? (
                                    <div className="flex items-center justify-center p-12">
                                        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                                    </div>
                                ) : (
                                    <Table>
                                        <TableHeader>
                                            <TableRow>
                                                <TableHead>Fee Name</TableHead>
                                                <TableHead>Type</TableHead>
                                                <TableHead>Calculation</TableHead>
                                                <TableHead>Amount</TableHead>
                                                <TableHead>Status</TableHead>
                                                <TableHead className="text-right">Actions</TableHead>
                                            </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                            {fees.length === 0 ? (
                                                <TableRow>
                                                    <TableCell colSpan={6} className="text-center text-muted-foreground py-8">
                                                        No fees configured. Click &quot;Add Fee&quot; to add airport, night, toll, or custom fees.
                                                    </TableCell>
                                                </TableRow>
                                            ) : (
                                                fees.map((fee) => (
                                                    <TableRow key={fee.id}>
                                                        <TableCell className="font-medium">{fee.fee_name}</TableCell>
                                                        <TableCell>
                                                            <Badge variant="outline" className="text-xs">
                                                                {FEE_TYPES.find((t) => t.value === fee.fee_type)?.label || fee.fee_type}
                                                            </Badge>
                                                        </TableCell>
                                                        <TableCell>
                                                            <Badge variant="secondary" className="text-xs">
                                                                {fee.calc_mode === "flat" && "Flat $"}
                                                                {fee.calc_mode === "per_km" && "$/km"}
                                                                {fee.calc_mode === "percentage" && "%"}
                                                            </Badge>
                                                        </TableCell>
                                                        <TableCell className="font-mono">
                                                            {fee.calc_mode === "percentage"
                                                                ? `${fee.amount}%`
                                                                : `$${Number(fee.amount).toFixed(2)}`}
                                                            {fee.calc_mode === "per_km" && "/km"}
                                                        </TableCell>
                                                        <TableCell>
                                                            <Badge
                                                                variant="secondary"
                                                                className={fee.is_active
                                                                    ? "bg-emerald-500/15 text-emerald-600"
                                                                    : "bg-zinc-500/15 text-zinc-600"
                                                                }
                                                            >
                                                                {fee.is_active ? "Active" : "Inactive"}
                                                            </Badge>
                                                        </TableCell>
                                                        <TableCell className="text-right">
                                                            <div className="flex justify-end gap-1">
                                                                <Button variant="ghost" size="icon" onClick={() => openEditFee(fee)}>
                                                                    <Pencil className="h-4 w-4" />
                                                                </Button>
                                                                <Button variant="ghost" size="icon" className="text-destructive" onClick={() => handleDeleteFee(fee.id)}>
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

                        {/* Fee Dialog */}
                        <Dialog open={feeDialogOpen} onOpenChange={setFeeDialogOpen}>
                            <DialogContent className="sm:max-w-md">
                                <DialogHeader>
                                    <DialogTitle>{editingFee ? "Edit" : "Add"} Fee</DialogTitle>
                                </DialogHeader>
                                <div className="space-y-4 pt-2">
                                    <div className="space-y-2">
                                        <Label>Fee Name</Label>
                                        <Input
                                            value={feeForm.fee_name}
                                            onChange={(e) => setFeeForm({ ...feeForm, fee_name: e.target.value })}
                                            placeholder="e.g. Airport Surcharge"
                                        />
                                    </div>
                                    <div className="grid grid-cols-2 gap-4">
                                        <div className="space-y-2">
                                            <Label>Fee Type</Label>
                                            <Select value={feeForm.fee_type} onValueChange={(v) => setFeeForm({ ...feeForm, fee_type: v })}>
                                                <SelectTrigger><SelectValue /></SelectTrigger>
                                                <SelectContent>
                                                    {FEE_TYPES.map((t) => (
                                                        <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
                                                    ))}
                                                </SelectContent>
                                            </Select>
                                        </div>
                                        <div className="space-y-2">
                                            <Label>Calculation Mode</Label>
                                            <Select value={feeForm.calc_mode} onValueChange={(v) => setFeeForm({ ...feeForm, calc_mode: v })}>
                                                <SelectTrigger><SelectValue /></SelectTrigger>
                                                <SelectContent>
                                                    {CALC_MODES.map((m) => (
                                                        <SelectItem key={m.value} value={m.value}>{m.label}</SelectItem>
                                                    ))}
                                                </SelectContent>
                                            </Select>
                                        </div>
                                    </div>
                                    <div className="space-y-2">
                                        <Label>
                                            Amount
                                            {feeForm.calc_mode === "flat" && " ($)"}
                                            {feeForm.calc_mode === "per_km" && " ($/km)"}
                                            {feeForm.calc_mode === "percentage" && " (%)"}
                                        </Label>
                                        <Input
                                            type="number"
                                            min={0}
                                            step={0.01}
                                            value={feeForm.amount}
                                            onChange={(e) => setFeeForm({ ...feeForm, amount: parseFloat(e.target.value) || 0 })}
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <Label>Description (optional)</Label>
                                        <Input
                                            value={feeForm.description}
                                            onChange={(e) => setFeeForm({ ...feeForm, description: e.target.value })}
                                            placeholder="e.g. Applied for rides to/from airport"
                                        />
                                    </div>

                                    {/* Night fee conditions */}
                                    {feeForm.fee_type === "night" && (
                                        <div className="grid grid-cols-2 gap-4 rounded-lg border p-3">
                                            <div className="space-y-1">
                                                <Label className="text-xs">Start Hour (24h)</Label>
                                                <Input
                                                    type="number" min={0} max={23}
                                                    value={feeForm.conditions.start_hour ?? 23}
                                                    onChange={(e) => setFeeForm({
                                                        ...feeForm,
                                                        conditions: { ...feeForm.conditions, start_hour: parseInt(e.target.value) }
                                                    })}
                                                />
                                            </div>
                                            <div className="space-y-1">
                                                <Label className="text-xs">End Hour (24h)</Label>
                                                <Input
                                                    type="number" min={0} max={23}
                                                    value={feeForm.conditions.end_hour ?? 5}
                                                    onChange={(e) => setFeeForm({
                                                        ...feeForm,
                                                        conditions: { ...feeForm.conditions, end_hour: parseInt(e.target.value) }
                                                    })}
                                                />
                                            </div>
                                        </div>
                                    )}

                                    <div className="flex items-center justify-between rounded-lg border p-3">
                                        <Label>Active</Label>
                                        <Switch
                                            checked={feeForm.is_active}
                                            onCheckedChange={(v) => setFeeForm({ ...feeForm, is_active: v })}
                                        />
                                    </div>

                                    <Button className="w-full" onClick={handleSaveFee}>
                                        {editingFee ? "Update" : "Add"} Fee
                                    </Button>
                                </div>
                            </DialogContent>
                        </Dialog>
                    </TabsContent>

                    {/* ═══ VEHICLE PRICING TAB ═══ */}
                    <TabsContent value="vehicles" className="space-y-4">
                        <div>
                            <h2 className="text-lg font-semibold">Vehicle Pricing</h2>
                            <p className="text-sm text-muted-foreground">
                                Set base fare, per-km, per-minute rates, and minimum fare for each vehicle type in this area.
                            </p>
                        </div>

                        <Card>
                            <CardContent className="p-0">
                                {vehicleLoading ? (
                                    <div className="flex items-center justify-center p-12">
                                        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                                    </div>
                                ) : (
                                    <Table>
                                        <TableHeader>
                                            <TableRow>
                                                <TableHead>Vehicle</TableHead>
                                                <TableHead>Base Fare</TableHead>
                                                <TableHead>Per KM</TableHead>
                                                <TableHead>Per Min</TableHead>
                                                <TableHead>Min Fare</TableHead>
                                                <TableHead>Booking Fee</TableHead>
                                                <TableHead className="text-right">Action</TableHead>
                                            </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                            {vehiclePricing.vehicle_types.length === 0 ? (
                                                <TableRow>
                                                    <TableCell colSpan={7} className="text-center text-muted-foreground py-8">
                                                        No vehicle types found. Create vehicle types first in the Settings.
                                                    </TableCell>
                                                </TableRow>
                                            ) : (
                                                vehiclePricing.vehicle_types.map((vt) => {
                                                    const config = vehiclePricing.fare_configs.find(
                                                        (c: any) => c.vehicle_type_id === vt.id
                                                    ) || {
                                                        vehicle_type_id: vt.id,
                                                        base_fare: 3.5,
                                                        per_km_rate: 1.5,
                                                        per_minute_rate: 0.25,
                                                        minimum_fare: 8,
                                                        booking_fee: 2,
                                                    };
                                                    return (
                                                        <VehicleRow
                                                            key={vt.id}
                                                            vehicle={vt}
                                                            config={config}
                                                            onSave={handleSaveVehicleConfig}
                                                        />
                                                    );
                                                })
                                            )}
                                        </TableBody>
                                    </Table>
                                )}
                            </CardContent>
                        </Card>
                    </TabsContent>

                    {/* ═══ TAX TAB ═══ */}
                    <TabsContent value="tax" className="space-y-4">
                        <div>
                            <h2 className="text-lg font-semibold">Tax Configuration</h2>
                            <p className="text-sm text-muted-foreground">
                                Configure GST, PST, or HST for this area. Use a province preset or set rates manually.
                            </p>
                        </div>

                        {/* Province preset selector */}
                        <Card>
                            <CardHeader className="pb-3">
                                <CardTitle className="text-sm">Province Preset</CardTitle>
                                <CardDescription>
                                    Quickly apply the correct tax rates for a Canadian province.
                                </CardDescription>
                            </CardHeader>
                            <CardContent>
                                <div className="flex flex-wrap gap-2">
                                    {Object.entries(PROVINCE_TAX).map(([code, pt]) => (
                                        <Button
                                            key={code}
                                            variant="outline"
                                            size="sm"
                                            className="text-xs"
                                            onClick={() => applyProvinceTax(code)}
                                        >
                                            {code}
                                            <span className="ml-1 text-muted-foreground">
                                                {pt.mode === "hst" ? `HST ${pt.hst}%` : pt.pst > 0 ? `${pt.gst}+${pt.pst}%` : `GST ${pt.gst}%`}
                                            </span>
                                        </Button>
                                    ))}
                                </div>
                            </CardContent>
                        </Card>

                        {/* Manual tax config */}
                        <Card>
                            <CardContent className="pt-6 space-y-6">
                                {/* HST */}
                                <div className="flex items-center justify-between rounded-lg border p-4">
                                    <div>
                                        <Label className="font-medium">HST (Harmonized Sales Tax)</Label>
                                        <p className="text-xs text-muted-foreground">ON, NB, NL, NS, PE — replaces GST+PST</p>
                                    </div>
                                    <div className="flex items-center gap-3">
                                        {tax.hst_enabled && (
                                            <Input
                                                type="number" min={0} max={30} step={0.1}
                                                value={tax.hst_rate}
                                                onChange={(e) => setTax({ ...tax, hst_rate: parseFloat(e.target.value) || 0 })}
                                                className="w-20 text-right"
                                            />
                                        )}
                                        <Switch
                                            checked={tax.hst_enabled}
                                            onCheckedChange={(v) => setTax({ ...tax, hst_enabled: v, gst_enabled: !v, pst_enabled: false })}
                                        />
                                    </div>
                                </div>

                                {!tax.hst_enabled && (
                                    <>
                                        {/* GST */}
                                        <div className="flex items-center justify-between rounded-lg border p-4">
                                            <div>
                                                <Label className="font-medium">GST (Federal)</Label>
                                                <p className="text-xs text-muted-foreground">5% across all provinces without HST</p>
                                            </div>
                                            <div className="flex items-center gap-3">
                                                {tax.gst_enabled && (
                                                    <Input
                                                        type="number" min={0} max={20} step={0.1}
                                                        value={tax.gst_rate}
                                                        onChange={(e) => setTax({ ...tax, gst_rate: parseFloat(e.target.value) || 0 })}
                                                        className="w-20 text-right"
                                                    />
                                                )}
                                                <Switch
                                                    checked={tax.gst_enabled}
                                                    onCheckedChange={(v) => setTax({ ...tax, gst_enabled: v })}
                                                />
                                            </div>
                                        </div>

                                        {/* PST */}
                                        <div className="flex items-center justify-between rounded-lg border p-4">
                                            <div>
                                                <Label className="font-medium">PST (Provincial)</Label>
                                                <p className="text-xs text-muted-foreground">SK=6%, BC=7%, MB=7%, QC=9.975%</p>
                                            </div>
                                            <div className="flex items-center gap-3">
                                                {tax.pst_enabled && (
                                                    <Input
                                                        type="number" min={0} max={20} step={0.001}
                                                        value={tax.pst_rate}
                                                        onChange={(e) => setTax({ ...tax, pst_rate: parseFloat(e.target.value) || 0 })}
                                                        className="w-20 text-right"
                                                    />
                                                )}
                                                <Switch
                                                    checked={tax.pst_enabled}
                                                    onCheckedChange={(v) => setTax({ ...tax, pst_enabled: v })}
                                                />
                                            </div>
                                        </div>
                                    </>
                                )}

                                <Separator />

                                {/* Summary */}
                                <div className="rounded-lg bg-muted/50 p-4">
                                    <p className="text-sm font-medium mb-2">Effective Tax Rate</p>
                                    {tax.hst_enabled ? (
                                        <p className="text-2xl font-bold text-emerald-600">{tax.hst_rate}% HST</p>
                                    ) : (
                                        <p className="text-2xl font-bold text-emerald-600">
                                            {((tax.gst_enabled ? tax.gst_rate : 0) + (tax.pst_enabled ? tax.pst_rate : 0)).toFixed(3)}%
                                            <span className="text-sm font-normal text-muted-foreground ml-2">
                                                {tax.gst_enabled && `GST ${tax.gst_rate}%`}
                                                {tax.gst_enabled && tax.pst_enabled && " + "}
                                                {tax.pst_enabled && `PST ${tax.pst_rate}%`}
                                            </span>
                                        </p>
                                    )}
                                </div>

                                <Button onClick={handleSaveTax} disabled={taxSaving} className="w-full">
                                    <Save className="mr-2 h-4 w-4" />
                                    {taxSaving ? "Saving..." : "Save Tax Settings"}
                                </Button>
                            </CardContent>
                        </Card>
                    </TabsContent>
                </Tabs>
            )}
        </div>
    );
}

/* Inline editable vehicle row */
function VehicleRow({ vehicle, config, onSave }: { vehicle: any; config: any; onSave: (c: any) => void }) {
    const [editing, setEditing] = useState(false);
    const [form, setForm] = useState({
        base_fare: config.base_fare ?? 3.5,
        per_km_rate: config.per_km_rate ?? 1.5,
        per_minute_rate: config.per_minute_rate ?? 0.25,
        minimum_fare: config.minimum_fare ?? 8,
        booking_fee: config.booking_fee ?? 2,
    });

    const save = () => {
        onSave({ ...config, ...form });
        setEditing(false);
    };

    return (
        <TableRow>
            <TableCell className="font-medium">
                <div className="flex items-center gap-2">
                    <Car className="h-4 w-4 text-violet-500" />
                    {vehicle.name}
                    <Badge variant="outline" className="text-xs">{vehicle.capacity} seats</Badge>
                </div>
            </TableCell>
            {editing ? (
                <>
                    <TableCell><Input type="number" step={0.5} value={form.base_fare} onChange={(e) => setForm({ ...form, base_fare: parseFloat(e.target.value) || 0 })} className="w-20" /></TableCell>
                    <TableCell><Input type="number" step={0.1} value={form.per_km_rate} onChange={(e) => setForm({ ...form, per_km_rate: parseFloat(e.target.value) || 0 })} className="w-20" /></TableCell>
                    <TableCell><Input type="number" step={0.05} value={form.per_minute_rate} onChange={(e) => setForm({ ...form, per_minute_rate: parseFloat(e.target.value) || 0 })} className="w-20" /></TableCell>
                    <TableCell><Input type="number" step={0.5} value={form.minimum_fare} onChange={(e) => setForm({ ...form, minimum_fare: parseFloat(e.target.value) || 0 })} className="w-20" /></TableCell>
                    <TableCell><Input type="number" step={0.5} value={form.booking_fee} onChange={(e) => setForm({ ...form, booking_fee: parseFloat(e.target.value) || 0 })} className="w-20" /></TableCell>
                    <TableCell className="text-right">
                        <Button size="sm" onClick={save}><Save className="h-3 w-3 mr-1" /> Save</Button>
                    </TableCell>
                </>
            ) : (
                <>
                    <TableCell className="font-mono">${Number(config.base_fare ?? 0).toFixed(2)}</TableCell>
                    <TableCell className="font-mono">${Number(config.per_km_rate ?? 0).toFixed(2)}</TableCell>
                    <TableCell className="font-mono">${Number(config.per_minute_rate ?? 0).toFixed(2)}</TableCell>
                    <TableCell className="font-mono">${Number(config.minimum_fare ?? 0).toFixed(2)}</TableCell>
                    <TableCell className="font-mono">${Number(config.booking_fee ?? 0).toFixed(2)}</TableCell>
                    <TableCell className="text-right">
                        <Button variant="ghost" size="icon" onClick={() => setEditing(true)}>
                            <Pencil className="h-4 w-4" />
                        </Button>
                    </TableCell>
                </>
            )}
        </TableRow>
    );
}
