"use client";

import { useEffect, useState, Suspense, lazy } from "react";
import { getDrivers, getServiceAreas } from "@/lib/api";
import { exportToCsv } from "@/lib/export-csv";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import {
    Star,
    Search,
    Users,
    Wifi,
    WifiOff,
    ShieldCheck,
    ShieldAlert,
    Download,
    Map,
    List,
    FileText,
    CheckCircle,
    XCircle,
    Copy,
    ExternalLink
} from "lucide-react";
import {
    Sheet,
    SheetContent,
    SheetDescription,
    SheetHeader,
    SheetTitle,
} from "@/components/ui/sheet";

import { Separator } from "@/components/ui/separator";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

const DriverMap = lazy(() => import("@/components/driver-map"));

const STATUS_TABS = [
    { value: "all", label: "All Drivers", icon: Users },
    { value: "online", label: "Online", icon: Wifi },
    { value: "offline", label: "Offline", icon: WifiOff },
    { value: "verified", label: "Verified", icon: ShieldCheck },
    { value: "unverified", label: "Unverified", icon: ShieldAlert },
];

export default function DriversPage() {
    const [drivers, setDrivers] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [statusFilter, setStatusFilter] = useState("all");
    const [viewMode, setViewMode] = useState("list");
    const [dateFrom, setDateFrom] = useState("");
    const [dateTo, setDateTo] = useState("");
    const [serviceAreas, setServiceAreas] = useState<any[]>([]);
    const [selectedArea, setSelectedArea] = useState("all");

    // Verification state
    const [selectedDriver, setSelectedDriver] = useState<any>(null);
    const [rejectReason, setRejectReason] = useState("");
    const [rejectDialogOpen, setRejectDialogOpen] = useState(false);
    const [actionLoading, setActionLoading] = useState(false);

    useEffect(() => {
        getDrivers()
            .then(setDrivers)
            .catch(() => { })
            .finally(() => setLoading(false));
        getServiceAreas()
            .then(setServiceAreas)
            .catch(() => { });
    }, []);

    const filtered = drivers.filter((d) => {
        const matchSearch =
            !search ||
            d.name?.toLowerCase().includes(search.toLowerCase()) ||
            d.phone?.toLowerCase().includes(search.toLowerCase()) ||
            d.license_plate?.toLowerCase().includes(search.toLowerCase());

        let matchStatus = true;
        if (statusFilter === "online") matchStatus = d.is_online === true;
        else if (statusFilter === "offline") matchStatus = d.is_online !== true;
        else if (statusFilter === "verified") matchStatus = d.is_verified === true;
        else if (statusFilter === "unverified") matchStatus = d.is_verified !== true;

        let matchDate = true;
        if (dateFrom || dateTo) {
            const reg = d.created_at ? new Date(d.created_at).toISOString().split("T")[0] : "";
            if (dateFrom && reg < dateFrom) matchDate = false;
            if (dateTo && reg > dateTo) matchDate = false;
        }

        const matchArea = selectedArea === "all" || d.service_area_id === selectedArea;

        return matchSearch && matchStatus && matchDate && matchArea;
    });

    const getCount = (status: string) => {
        if (status === "all") return drivers.length;
        if (status === "online") return drivers.filter((d) => d.is_online === true).length;
        if (status === "offline") return drivers.filter((d) => d.is_online !== true).length;
        if (status === "verified") return drivers.filter((d) => d.is_verified === true).length;
        if (status === "unverified") return drivers.filter((d) => d.is_verified !== true).length;
        return 0;
    };

    const onlineCount = filtered.filter((d) => d.is_online === true).length;
    const offlineCount = filtered.filter((d) => d.is_online !== true).length;

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <div>
                        <h1 className="text-3xl font-bold tracking-tight">Drivers</h1>
                        <p className="text-muted-foreground mt-1">
                            View and manage registered drivers.
                        </p>
                    </div>
                    {serviceAreas.length > 0 && (
                        <select
                            value={selectedArea}
                            onChange={(e) => setSelectedArea(e.target.value)}
                            className="ml-4 h-9 rounded-md border border-border bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                        >
                            <option value="all">All Service Areas</option>
                            {serviceAreas.map((area) => (
                                <option key={area.id} value={area.id}>
                                    {area.name}{area.is_airport ? " ✈️" : ""}
                                </option>
                            ))}
                        </select>
                    )}
                </div>
                <div className="flex items-center gap-2">
                    <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="w-36 text-xs" />
                    <span className="text-muted-foreground text-sm">to</span>
                    <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="w-36 text-xs" />
                    <Button
                        variant="outline"
                        onClick={() => exportToCsv("drivers", filtered, [
                            { key: "name", label: "Name" },
                            { key: "phone", label: "Phone" },
                            { key: "email", label: "Email" },
                            { key: "vehicle_make", label: "Vehicle Make" },
                            { key: "vehicle_model", label: "Vehicle Model" },
                            { key: "vehicle_color", label: "Color" },
                            { key: "license_plate", label: "Plate" },
                            { key: "rating", label: "Rating" },
                            { key: "total_rides", label: "Total Rides" },
                            { key: "is_online", label: "Online" },
                            { key: "is_verified", label: "Verified" },
                            { key: "service_area_id", label: "Service Area ID" },
                            { key: "created_at", label: "Registered Date" },
                        ])}
                        disabled={filtered.length === 0}
                    >
                        <Download className="mr-2 h-4 w-4" /> Export CSV
                    </Button>
                </div>
            </div>

            {/* Stats */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                <Card className="border-border/50">
                    <CardContent className="pt-4 pb-3">
                        <p className="text-xs text-muted-foreground">Total</p>
                        <p className="text-2xl font-bold">{drivers.length}</p>
                    </CardContent>
                </Card>
                <Card className="border-border/50">
                    <CardContent className="pt-4 pb-3">
                        <p className="text-xs text-muted-foreground">Online Now</p>
                        <p className="text-2xl font-bold text-emerald-500">{onlineCount}</p>
                    </CardContent>
                </Card>
                <Card className="border-border/50">
                    <CardContent className="pt-4 pb-3">
                        <p className="text-xs text-muted-foreground">Offline</p>
                        <p className="text-2xl font-bold text-zinc-400">{offlineCount}</p>
                    </CardContent>
                </Card>
                <Card className="border-border/50">
                    <CardContent className="pt-4 pb-3">
                        <p className="text-xs text-muted-foreground">Avg Rating</p>
                        <p className="text-2xl font-bold text-amber-500">
                            {drivers.length > 0
                                ? (drivers.reduce((s, d) => s + (d.rating || 5), 0) / drivers.length).toFixed(1)
                                : "—"}
                        </p>
                    </CardContent>
                </Card>
            </div>

            {/* Status tabs */}
            <div className="flex flex-wrap gap-2">
                {STATUS_TABS.map((tab) => {
                    const count = getCount(tab.value);
                    const active = statusFilter === tab.value;
                    return (
                        <button
                            key={tab.value}
                            onClick={() => setStatusFilter(tab.value)}
                            className={`
                                inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium transition-all
                                ${active
                                    ? "border-primary bg-primary/10 text-primary shadow-sm"
                                    : "border-border bg-card text-muted-foreground hover:bg-muted hover:text-foreground"
                                }
                            `}
                        >
                            <tab.icon className="h-4 w-4" />
                            {tab.label}
                            <Badge variant="secondary" className={`text-xs px-1.5 py-0 ${active ? "bg-primary/20" : ""}`}>
                                {count}
                            </Badge>
                        </button>
                    );
                })}
            </div>

            <div className="flex items-center gap-3">
                <div className="relative flex-1 max-w-xs">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input
                        placeholder="Search by name, phone, or plate..."
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="pl-9"
                    />
                </div>
                <Tabs value={viewMode} onValueChange={setViewMode}>
                    <TabsList>
                        <TabsTrigger value="list"><List className="h-4 w-4" /></TabsTrigger>
                        <TabsTrigger value="map"><Map className="h-4 w-4" /></TabsTrigger>
                    </TabsList>
                </Tabs>
            </div>

            {viewMode === "map" ? (
                <Card className="border-border/50">
                    <CardContent className="p-0 overflow-hidden rounded-lg">
                        <Suspense
                            fallback={
                                <div className="flex items-center justify-center h-[500px]">
                                    <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                                </div>
                            }
                        >
                            <DriverMap drivers={filtered} serviceAreas={serviceAreas} selectedArea={selectedArea} />
                        </Suspense>
                    </CardContent>
                </Card>
            ) : (
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
                                        <TableHead>Phone</TableHead>
                                        <TableHead>Vehicle</TableHead>
                                        <TableHead>Plate</TableHead>
                                        <TableHead>Rating</TableHead>
                                        <TableHead>Status</TableHead>
                                        <TableHead>Verification</TableHead>
                                        <TableHead className="text-right">Actions</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {filtered.length === 0 ? (
                                        <TableRow>
                                            <TableCell colSpan={8} className="text-center text-muted-foreground py-12">
                                                No drivers found.
                                            </TableCell>
                                        </TableRow>
                                    ) : (
                                        filtered.map((driver) => (
                                            <TableRow key={driver.id}>
                                                <TableCell className="font-medium">{driver.name}</TableCell>
                                                <TableCell className="text-muted-foreground">{driver.phone}</TableCell>
                                                <TableCell>
                                                    {driver.vehicle_color} {driver.vehicle_make} {driver.vehicle_model}
                                                    <div className="text-xs text-muted-foreground">{driver.vehicle_year}</div>
                                                </TableCell>
                                                <TableCell className="font-mono">{driver.license_plate}</TableCell>
                                                <TableCell>
                                                    <div className="flex items-center gap-1">
                                                        <Star className="h-3.5 w-3.5 fill-amber-400 text-amber-400" />
                                                        <span>{driver.rating?.toFixed(1) || "5.0"}</span>
                                                    </div>
                                                </TableCell>
                                                <TableCell>
                                                    <div className="flex items-center gap-2">
                                                        <span className={`flex h-2 w-2 rounded-full ${driver.is_online ? "bg-emerald-500" : "bg-zinc-300"}`} />
                                                        <span className="text-sm text-muted-foreground">{driver.is_online ? "Online" : "Offline"}</span>
                                                    </div>
                                                </TableCell>
                                                <TableCell>
                                                    <Badge variant={driver.is_verified ? "default" : "destructive"} className={driver.is_verified ? "bg-emerald-500 hover:bg-emerald-600" : ""}>
                                                        {driver.is_verified ? "Verified" : "Pending"}
                                                    </Badge>
                                                </TableCell>
                                                <TableCell className="text-right">
                                                    <Button variant="ghost" size="sm" onClick={() => setSelectedDriver(driver)}>
                                                        Details
                                                    </Button>
                                                </TableCell>
                                            </TableRow>
                                        ))
                                    )}
                                </TableBody>
                            </Table>
                        )}
                    </CardContent>
                </Card>
            )}


            {/* Driver Details Sheet */}
            <Sheet open={!!selectedDriver} onOpenChange={(open) => !open && setSelectedDriver(null)}>
                <SheetContent className="w-[400px] sm:w-[540px]">
                    <SheetHeader>
                        <SheetTitle>Driver Details</SheetTitle>
                        <SheetDescription>Verify driver documents and vehicle information.</SheetDescription>
                    </SheetHeader>
                    {selectedDriver && (
                        <div className="mt-6 flex flex-col h-[calc(100vh-120px)]">
                            <div className="flex-1 pr-4 overflow-y-auto">
                                <div className="space-y-6">
                                    {/* Verification Actions */}
                                    <div className="flex gap-2">
                                        {!selectedDriver.is_verified ? (
                                            <>
                                                <Button
                                                    className="flex-1 bg-emerald-600 hover:bg-emerald-700"
                                                    onClick={() => handleVerify(selectedDriver.id, true)}
                                                    disabled={actionLoading}
                                                >
                                                    <CheckCircle className="mr-2 h-4 w-4" /> Approve Driver
                                                </Button>
                                                <Button
                                                    variant="destructive"
                                                    className="flex-1"
                                                    onClick={() => setRejectDialogOpen(true)}
                                                    disabled={actionLoading}
                                                >
                                                    <XCircle className="mr-2 h-4 w-4" /> Reject
                                                </Button>
                                            </>
                                        ) : (
                                            <div className="w-full p-3 bg-emerald-100 dark:bg-emerald-900/20 text-emerald-700 rounded-md flex items-center justify-center gap-2">
                                                <CheckCircle className="h-5 w-5" /> Verified on {new Date(selectedDriver.updated_at).toLocaleDateString()}
                                            </div>
                                        )}
                                    </div>

                                    <div className="grid grid-cols-2 gap-4">
                                        <div>
                                            <Label className="text-xs text-muted-foreground">Status</Label>
                                            <div className="font-medium">{selectedDriver.is_verified ? "Verified" : "Pending Verification"}</div>
                                        </div>
                                        <div>
                                            <Label className="text-xs text-muted-foreground">Submitted</Label>
                                            <div className="font-medium">{selectedDriver.submitted_at ? new Date(selectedDriver.submitted_at).toLocaleDateString() : "N/A"}</div>
                                        </div>
                                    </div>

                                    <Separator />

                                    <div className="space-y-4">
                                        <h3 className="font-medium">Personal Information</h3>
                                        <div className="grid grid-cols-2 gap-4 text-sm">
                                            <div>
                                                <Label className="text-xs text-muted-foreground">Full Name</Label>
                                                <div>{selectedDriver.name}</div>
                                            </div>
                                            <div>
                                                <Label className="text-xs text-muted-foreground">Phone</Label>
                                                <div>{selectedDriver.phone}</div>
                                            </div>
                                            <div>
                                                <Label className="text-xs text-muted-foreground">Email</Label>
                                                <div>{selectedDriver.email || "N/A"}</div>
                                            </div>
                                            <div>
                                                <Label className="text-xs text-muted-foreground">City</Label>
                                                <div>{selectedDriver.city || "N/A"}</div>
                                            </div>
                                        </div>
                                    </div>

                                    <Separator />

                                    <div className="space-y-4">
                                        <h3 className="font-medium">Vehicle Information</h3>
                                        <div className="grid grid-cols-2 gap-4 text-sm">
                                            <div>
                                                <Label className="text-xs text-muted-foreground">Vehicle</Label>
                                                <div>{selectedDriver.vehicle_year} {selectedDriver.vehicle_make} {selectedDriver.vehicle_model}</div>
                                            </div>
                                            <div>
                                                <Label className="text-xs text-muted-foreground">Color</Label>
                                                <div>{selectedDriver.vehicle_color}</div>
                                            </div>
                                            <div>
                                                <Label className="text-xs text-muted-foreground">License Plate</Label>
                                                <div className="font-mono bg-muted px-2 py-0.5 rounded inline-block">{selectedDriver.license_plate}</div>
                                            </div>
                                            <div>
                                                <Label className="text-xs text-muted-foreground">VIN</Label>
                                                <div className="font-mono text-xs">{selectedDriver.vehicle_vin || "N/A"}</div>
                                            </div>
                                        </div>
                                    </div>

                                    <Separator />

                                    <div className="space-y-4">
                                        <h3 className="font-medium">Documents</h3>
                                        <div className="grid gap-3">
                                            {renderDoc("License Front", selectedDriver.documents?.license_front)}
                                            {renderDoc("License Back", selectedDriver.documents?.license_back)}
                                            {renderDoc("Insurance", selectedDriver.documents?.insurance)}
                                            {renderDoc("Vehicle Registration", selectedDriver.documents?.registration)}
                                            {renderDoc("Vehicle Inspection", selectedDriver.documents?.inspection)}
                                            {renderDoc("Background Check", selectedDriver.documents?.background_check)}
                                        </div>

                                        <div className="grid grid-cols-2 gap-4 text-sm mt-4">
                                            <div>
                                                <Label className="text-xs text-muted-foreground">License Expiry</Label>
                                                <div className={isExpired(selectedDriver.license_expiry_date) ? "text-red-500 font-bold" : ""}>
                                                    {formatDate(selectedDriver.license_expiry_date)}
                                                </div>
                                            </div>
                                            <div>
                                                <Label className="text-xs text-muted-foreground">Inspection Expiry</Label>
                                                <div className={isExpired(selectedDriver.vehicle_inspection_expiry_date) ? "text-red-500 font-bold" : ""}>
                                                    {formatDate(selectedDriver.vehicle_inspection_expiry_date)}
                                                </div>
                                            </div>
                                            <div>
                                                <Label className="text-xs text-muted-foreground">Insurance Expiry</Label>
                                                <div className={isExpired(selectedDriver.insurance_expiry_date) ? "text-red-500 font-bold" : ""}>
                                                    {formatDate(selectedDriver.insurance_expiry_date)}
                                                </div>
                                            </div>
                                            <div>
                                                <Label className="text-xs text-muted-foreground">Background Check Expiry</Label>
                                                <div className={isExpired(selectedDriver.background_check_expiry_date) ? "text-red-500 font-bold" : ""}>
                                                    {formatDate(selectedDriver.background_check_expiry_date)}
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    )}
                </SheetContent>
            </Sheet>

            {/* Reject Dialog */}
            <Dialog open={rejectDialogOpen} onOpenChange={setRejectDialogOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Reject Application</DialogTitle>
                        <DialogDescription>Please provide a reason for rejection. This will be sent to the driver.</DialogDescription>
                    </DialogHeader>
                    <Textarea
                        placeholder="e.g. License is expired, Photos are blurry..."
                        value={rejectReason}
                        onChange={(e) => setRejectReason(e.target.value)}
                    />
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setRejectDialogOpen(false)}>Cancel</Button>
                        <Button variant="destructive" onClick={() => handleVerify(selectedDriver.id, false)} disabled={!rejectReason}>Reject Driver</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div >
    );

    function renderDoc(label: string, url: string) {
        if (!url) return null;
        const processUrl = (u: string) => u.startsWith("http") ? u : `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}${u}`;
        return (
            <div className="flex items-center justify-between p-3 border rounded-md">
                <div className="flex items-center gap-2">
                    <FileText className="h-4 w-4 text-muted-foreground" />
                    <span className="text-sm font-medium">{label}</span>
                </div>
                <div className="flex gap-2">
                    <Button variant="ghost" size="icon" asChild>
                        <a href={processUrl(url)} target="_blank" rel="noopener noreferrer">
                            <ExternalLink className="h-4 w-4" />
                        </a>
                    </Button>
                </div>
            </div>
        );
    }

    function formatDate(dateStr: string) {
        if (!dateStr) return "N/A";
        return new Date(dateStr).toLocaleDateString();
    }

    function isExpired(dateStr: string) {
        if (!dateStr) return false;
        return new Date(dateStr) < new Date();
    }

    async function handleVerify(driverId: string, isVerified: boolean) {
        setActionLoading(true);
        try {
            const token = localStorage.getItem("admin_token");
            const res = await fetch(`/api/admin/drivers/${driverId}/verify`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "Authorization": `Bearer ${token}`
                },
                body: JSON.stringify({
                    is_verified: isVerified,
                    rejection_reason: isVerified ? null : rejectReason
                })
            });

            if (!res.ok) throw new Error("Failed to update status");

            setDrivers(drivers.map(d => d.id === driverId ? { ...d, is_verified: isVerified, rejection_reason: isVerified ? null : rejectReason } : d));
            setSelectedDriver((prev: any) => ({ ...prev, is_verified: isVerified }));
            setRejectDialogOpen(false);
            setRejectReason("");
            // Don't close sheet, let them see it updated
        } catch (err: any) {
            alert(err.message);
        } finally {
            setActionLoading(false);
        }
    }
}
