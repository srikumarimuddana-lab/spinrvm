"use client";

import { useEffect, useState } from "react";
import { getDrivers, getDriverDocuments, reviewDocument } from "@/lib/api";
import { exportToCsv } from "@/lib/export-csv";
import { formatCurrency } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import {
    Search, Users, Wifi, WifiOff, ShieldCheck, ShieldAlert, Download,
    ChevronRight, X, Star, Car, MapPin, CreditCard, Clock, DollarSign,
    CheckCircle, XCircle, FileText, Phone, Mail,
} from "lucide-react";

const STATUS_TABS = [
    { value: "all", label: "All", icon: Users },
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
    const [selected, setSelected] = useState<any>(null);
    const [verifying, setVerifying] = useState(false);
    const [driverDocs, setDriverDocs] = useState<any[]>([]);
    const [docsLoading, setDocsLoading] = useState(false);
    const [docBusy, setDocBusy] = useState<string | null>(null);

    useEffect(() => {
        loadDrivers();
    }, []);

    // Load the selected driver's uploaded documents whenever the selection
    // changes. This lets admins see (and approve) a newly re-uploaded doc
    // even if the driver is still flagged as verified overall.
    useEffect(() => {
        if (!selected?.id) {
            setDriverDocs([]);
            return;
        }
        setDocsLoading(true);
        getDriverDocuments(selected.id)
            .then((d) => setDriverDocs(Array.isArray(d) ? d : []))
            .catch(() => setDriverDocs([]))
            .finally(() => setDocsLoading(false));
    }, [selected?.id]);

    const reloadDriverDocs = async () => {
        if (!selected?.id) return;
        try {
            const d = await getDriverDocuments(selected.id);
            setDriverDocs(Array.isArray(d) ? d : []);
        } catch {}
    };

    const handleReviewDoc = async (
        docId: string,
        status: "approved" | "rejected",
    ) => {
        setDocBusy(docId);
        try {
            let expiry: string | undefined;
            let reason: string | undefined;
            if (status === "approved") {
                const input = window.prompt(
                    "New expiry date for this document (YYYY-MM-DD). Leave blank if this document does not expire.",
                    "",
                );
                if (input && input.trim()) {
                    const d = new Date(input.trim());
                    if (isNaN(d.getTime())) {
                        alert("Invalid date. Use YYYY-MM-DD.");
                        setDocBusy(null);
                        return;
                    }
                    expiry = d.toISOString();
                }
            } else {
                const r = window.prompt("Reason for rejection (optional):", "");
                reason = r || undefined;
            }
            await reviewDocument(docId, status, reason, expiry);
            await reloadDriverDocs();
            // Refresh the drivers list so is_verified flips reflect in sidebar.
            loadDrivers();
        } catch (e: any) {
            alert("Could not update document: " + (e?.message || "unknown error"));
        } finally {
            setDocBusy(null);
        }
    };

    const loadDrivers = () => {
        setLoading(true);
        getDrivers().then(setDrivers).catch(() => {}).finally(() => setLoading(false));
    };

    const filtered = drivers.filter(d => {
        const matchSearch = !search ||
            (d.first_name + " " + d.last_name).toLowerCase().includes(search.toLowerCase()) ||
            d.email?.toLowerCase().includes(search.toLowerCase()) ||
            d.license_plate?.toLowerCase().includes(search.toLowerCase()) ||
            d.id?.toLowerCase().includes(search.toLowerCase());
        let matchStatus = true;
        if (statusFilter === "online") matchStatus = d.is_online;
        if (statusFilter === "offline") matchStatus = !d.is_online;
        if (statusFilter === "verified") matchStatus = d.is_verified;
        if (statusFilter === "unverified") matchStatus = !d.is_verified;
        return matchSearch && matchStatus;
    });

    const statusCounts = (s: string) => {
        if (s === "all") return drivers.length;
        if (s === "online") return drivers.filter(d => d.is_online).length;
        if (s === "offline") return drivers.filter(d => !d.is_online).length;
        if (s === "verified") return drivers.filter(d => d.is_verified).length;
        if (s === "unverified") return drivers.filter(d => !d.is_verified).length;
        return 0;
    };

    const handleVerify = async (driverId: string, verified: boolean) => {
        setVerifying(true);
        try {
            const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
            const token = (await import("@/store/authStore")).useAuthStore.getState().token;
            await fetch(`${API_BASE}/api/admin/drivers/${driverId}/verify`, {
                method: "POST", headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}` },
                body: JSON.stringify({ verified }),
            });
            loadDrivers();
            if (selected?.id === driverId) setSelected({ ...selected, is_verified: verified });
        } catch {}
        setVerifying(false);
    };

    const fmtDate = (d: string) => {
        if (!d) return "—";
        try { return new Date(d).toLocaleDateString("en-CA", { month: "short", day: "numeric", year: "numeric" }); } catch { return d; }
    };

    return (
        <div className="flex gap-4 h-[calc(100vh-100px)]">
            {/* Left: Driver List */}
            <div className={`flex flex-col ${selected ? "w-1/2 lg:w-3/5" : "w-full"} transition-all`}>
                <div className="flex items-center justify-between mb-4">
                    <div>
                        <h1 className="text-2xl font-bold">Drivers</h1>
                        <p className="text-sm text-muted-foreground">{drivers.length} total drivers</p>
                    </div>
                    <button onClick={() => exportToCsv("drivers", filtered, [
                        {key:"id",label:"ID"},{key:"first_name",label:"First Name"},{key:"last_name",label:"Last Name"},
                        {key:"email",label:"Email"},{key:"phone",label:"Phone"},{key:"is_verified",label:"Verified"},
                        {key:"is_online",label:"Online"},{key:"rating",label:"Rating"},{key:"total_rides",label:"Rides"},
                    ])}
                        className="flex items-center gap-2 text-sm font-medium text-muted-foreground hover:text-foreground transition px-3 py-1.5 rounded-lg hover:bg-muted">
                        <Download className="h-4 w-4" /> Export
                    </button>
                </div>

                {/* Tabs */}
                <div className="flex gap-1.5 mb-3 overflow-x-auto pb-1">
                    {STATUS_TABS.map(tab => (
                        <button key={tab.value} onClick={() => setStatusFilter(tab.value)}
                            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition ${
                                statusFilter === tab.value ? "bg-primary text-white" : "bg-muted text-muted-foreground hover:bg-muted/80"
                            }`}>
                            <tab.icon className="h-3.5 w-3.5" />
                            {tab.label}
                            <span className={`ml-1 px-1.5 rounded text-[10px] ${statusFilter === tab.value ? "bg-white/20" : "bg-background"}`}>
                                {statusCounts(tab.value)}
                            </span>
                        </button>
                    ))}
                </div>

                <div className="relative mb-3">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input placeholder="Search by name, email, plate..." value={search} onChange={e => setSearch(e.target.value)} className="pl-9 h-9 text-sm" />
                </div>

                {/* Driver Cards */}
                <div className="flex-1 overflow-y-auto space-y-2 pr-1">
                    {loading ? (
                        <div className="flex items-center justify-center py-20">
                            <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                        </div>
                    ) : filtered.length === 0 ? (
                        <div className="text-center py-16 text-muted-foreground">
                            <Users className="h-10 w-10 mx-auto mb-3 opacity-30" />
                            <p className="font-medium">No drivers found</p>
                        </div>
                    ) : (
                        filtered.map(driver => (
                            <div key={driver.id} onClick={() => setSelected(driver)}
                                className={`flex items-center gap-3 p-3 rounded-xl border cursor-pointer transition-all hover:shadow-sm ${
                                    selected?.id === driver.id ? "border-primary bg-primary/5" : "border-border hover:border-border/80"
                                }`}>
                                {/* Avatar */}
                                <div className="w-10 h-10 rounded-full bg-muted flex items-center justify-center text-sm font-bold text-muted-foreground shrink-0">
                                    {(driver.first_name?.[0] || "")}{(driver.last_name?.[0] || "")}
                                </div>
                                {/* Info */}
                                <div className="flex-1 min-w-0">
                                    <div className="flex items-center gap-2">
                                        <p className="text-sm font-semibold truncate">{driver.first_name} {driver.last_name}</p>
                                        {driver.is_verified ? (
                                            <ShieldCheck className="h-3.5 w-3.5 text-emerald-500 shrink-0" />
                                        ) : (
                                            <ShieldAlert className="h-3.5 w-3.5 text-amber-500 shrink-0" />
                                        )}
                                    </div>
                                    <p className="text-xs text-muted-foreground truncate">
                                        {driver.vehicle_make} {driver.vehicle_model} · {driver.license_plate || "No plate"}
                                    </p>
                                </div>
                                {/* Status */}
                                <div className="text-right shrink-0">
                                    <div className="flex items-center gap-1">
                                        <span className={`w-2 h-2 rounded-full ${driver.is_online ? "bg-emerald-500" : "bg-gray-300"}`} />
                                        <span className="text-xs text-muted-foreground">{driver.is_online ? "Online" : "Offline"}</span>
                                    </div>
                                    <div className="flex items-center gap-0.5 mt-1">
                                        <Star className="h-3 w-3 text-amber-400" />
                                        <span className="text-xs font-medium">{driver.rating?.toFixed(1) || "New"}</span>
                                    </div>
                                </div>
                                <ChevronRight className="h-4 w-4 text-muted-foreground/40 shrink-0" />
                            </div>
                        ))
                    )}
                </div>
            </div>

            {/* Right: Driver Detail */}
            {selected && (
                <div className="w-1/2 lg:w-2/5 bg-card border rounded-2xl overflow-y-auto">
                    <div className="flex items-center justify-between p-4 border-b sticky top-0 bg-card z-10">
                        <div className="flex items-center gap-3">
                            <div className="w-12 h-12 rounded-full bg-muted flex items-center justify-center text-lg font-bold text-muted-foreground">
                                {(selected.first_name?.[0] || "")}{(selected.last_name?.[0] || "")}
                            </div>
                            <div>
                                <h3 className="font-bold">{selected.first_name} {selected.last_name}</h3>
                                <p className="text-xs text-muted-foreground font-mono">{selected.id?.slice(0, 12)}...</p>
                            </div>
                        </div>
                        <button onClick={() => setSelected(null)} className="p-1.5 hover:bg-muted rounded-lg">
                            <X className="h-4 w-4" />
                        </button>
                    </div>

                    <div className="p-4 space-y-4">
                        {/* Status Badges */}
                        <div className="flex flex-wrap gap-2">
                            <span className={`px-2.5 py-1 rounded-lg text-xs font-bold ${selected.is_verified ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400" : "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"}`}>
                                {selected.is_verified ? "VERIFIED" : "UNVERIFIED"}
                            </span>
                            <span className={`px-2.5 py-1 rounded-lg text-xs font-bold ${selected.is_online ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400" : "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400"}`}>
                                {selected.is_online ? "ONLINE" : "OFFLINE"}
                            </span>
                        </div>

                        {/* Verify Actions — always available so admin can
                            re-verify a driver whose documents were updated
                            after an original approval. */}
                        <div className={`${selected.is_verified ? "bg-emerald-50 dark:bg-emerald-900/20 border-emerald-200 dark:border-emerald-800" : "bg-amber-50 dark:bg-amber-900/20 border-amber-200 dark:border-amber-800"} border rounded-xl p-3`}>
                            <p className={`text-sm mb-2 font-medium ${selected.is_verified ? "text-emerald-800 dark:text-emerald-300" : "text-amber-800 dark:text-amber-300"}`}>
                                {selected.is_verified
                                    ? "Driver is currently verified. You can un-verify to send them back for review."
                                    : "This driver is pending verification"}
                            </p>
                            <div className="flex gap-2">
                                <button onClick={() => handleVerify(selected.id, true)} disabled={verifying || selected.is_verified}
                                    className="flex-1 flex items-center justify-center gap-1 py-2 rounded-lg bg-emerald-500 text-white text-sm font-semibold hover:bg-emerald-600 transition disabled:opacity-50">
                                    <CheckCircle className="h-4 w-4" /> {selected.is_verified ? "Verified" : "Approve"}
                                </button>
                                <button onClick={() => handleVerify(selected.id, false)} disabled={verifying || !selected.is_verified}
                                    className="flex-1 flex items-center justify-center gap-1 py-2 rounded-lg bg-red-500 text-white text-sm font-semibold hover:bg-red-600 transition disabled:opacity-50">
                                    <XCircle className="h-4 w-4" /> Un-verify
                                </button>
                            </div>
                        </div>

                        {/* Contact Info */}
                        <Section title="Contact">
                            <InfoRow icon={Mail} label="Email" value={selected.email || "—"} />
                            <InfoRow icon={Phone} label="Phone" value={selected.phone || "—"} />
                            <InfoRow icon={MapPin} label="City" value={selected.city || "—"} />
                            <InfoRow icon={MapPin} label="Service Area" value={selected.service_area_id?.slice(0, 8) || "Not set"} />
                        </Section>

                        {/* Vehicle */}
                        <Section title="Vehicle">
                            <InfoRow icon={Car} label="Vehicle" value={`${selected.vehicle_color || ""} ${selected.vehicle_make || ""} ${selected.vehicle_model || ""}`.trim() || "—"} />
                            <InfoRow icon={FileText} label="License Plate" value={selected.license_plate || "—"} bold />
                            <InfoRow icon={Car} label="Year" value={selected.vehicle_year || "—"} />
                            <InfoRow icon={FileText} label="VIN" value={selected.vehicle_vin || "—"} />
                        </Section>

                        {/* Stats */}
                        <Section title="Performance">
                            <div className="grid grid-cols-3 gap-2">
                                <StatMini label="Rating" value={selected.rating?.toFixed(1) || "New"} icon={Star} color="text-amber-500" />
                                <StatMini label="Total Rides" value={selected.total_rides || 0} icon={Car} color="text-blue-500" />
                                <StatMini label="Earnings" value={formatCurrency(selected.total_earnings || 0)} icon={DollarSign} color="text-emerald-500" />
                            </div>
                        </Section>

                        {/* Documents */}
                        <Section title="Documents">
                            <DocRow label="Driver's License" expiry={selected.license_expiry_date} />
                            <DocRow label="Insurance" expiry={selected.insurance_expiry_date} />
                            <DocRow label="Vehicle Inspection" expiry={selected.vehicle_inspection_expiry_date} />
                            <DocRow label="Background Check" expiry={selected.background_check_expiry_date} />
                        </Section>

                        {/* Uploaded Documents (dynamic, from driver_documents) */}
                        <Section title="Uploaded Documents">
                            {docsLoading ? (
                                <p className="text-xs text-muted-foreground">Loading…</p>
                            ) : driverDocs.length === 0 ? (
                                <p className="text-xs text-muted-foreground">No documents uploaded.</p>
                            ) : (
                                <div className="space-y-2">
                                    {driverDocs
                                        .filter((d) => d.status !== "superseded")
                                        .map((d) => {
                                            const exp = d.expiry_date || d.expires_at;
                                            const expired = exp && new Date(exp) < new Date();
                                            const statusColor =
                                                d.status === "approved" && !expired
                                                    ? "text-emerald-600"
                                                    : d.status === "rejected"
                                                        ? "text-red-500"
                                                        : expired
                                                            ? "text-red-500"
                                                            : "text-amber-600";
                                            return (
                                                <div key={d.id} className="bg-background rounded-lg p-2.5 border">
                                                    <div className="flex items-start justify-between gap-2">
                                                        <div className="min-w-0 flex-1">
                                                            <p className="text-sm font-semibold truncate">
                                                                {d.document_type || "Document"}{d.side ? ` (${d.side})` : ""}
                                                            </p>
                                                            <p className={`text-[11px] font-medium uppercase tracking-wider ${statusColor}`}>
                                                                {expired && d.status === "approved" ? "EXPIRED" : d.status}
                                                            </p>
                                                            {exp && (
                                                                <p className="text-[11px] text-muted-foreground">
                                                                    Expires: {new Date(exp).toLocaleDateString("en-CA")}
                                                                </p>
                                                            )}
                                                            {d.document_url && (
                                                                <a
                                                                    href={d.document_url}
                                                                    target="_blank"
                                                                    rel="noreferrer"
                                                                    className="text-[11px] text-primary underline"
                                                                >
                                                                    View file
                                                                </a>
                                                            )}
                                                            {d.rejection_reason && (
                                                                <p className="text-[11px] text-red-500 mt-1">
                                                                    Reason: {d.rejection_reason}
                                                                </p>
                                                            )}
                                                        </div>
                                                        <div className="flex flex-col gap-1 shrink-0">
                                                            <button
                                                                onClick={() => handleReviewDoc(d.id, "approved")}
                                                                disabled={docBusy === d.id}
                                                                className="px-2 py-1 rounded bg-emerald-500 text-white text-[11px] font-semibold hover:bg-emerald-600 transition disabled:opacity-50"
                                                            >
                                                                Approve
                                                            </button>
                                                            <button
                                                                onClick={() => handleReviewDoc(d.id, "rejected")}
                                                                disabled={docBusy === d.id}
                                                                className="px-2 py-1 rounded bg-red-500 text-white text-[11px] font-semibold hover:bg-red-600 transition disabled:opacity-50"
                                                            >
                                                                Reject
                                                            </button>
                                                        </div>
                                                    </div>
                                                </div>
                                            );
                                        })}
                                </div>
                            )}
                        </Section>

                        {/* Subscription */}
                        <Section title="Spinr Pass">
                            <div className="bg-muted/30 rounded-xl p-3">
                                <p className="text-sm text-muted-foreground">
                                    {selected.subscription_status === "active"
                                        ? `Active — ${selected.subscription_plan || "Plan"}`
                                        : "No active subscription"}
                                </p>
                            </div>
                        </Section>

                        {/* Meta */}
                        <div className="text-xs text-muted-foreground space-y-1 pt-2 border-t">
                            <p>Joined: {fmtDate(selected.created_at)}</p>
                            <p>Last updated: {fmtDate(selected.updated_at)}</p>
                            <p>Acceptance rate: {selected.acceptance_rate || "—"}</p>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
    return (
        <div>
            <h4 className="text-xs font-bold text-muted-foreground uppercase tracking-wider mb-2">{title}</h4>
            <div className="bg-muted/30 rounded-xl p-3 space-y-2">{children}</div>
        </div>
    );
}

function InfoRow({ icon: Icon, label, value, bold }: { icon: any; label: string; value: string; bold?: boolean }) {
    return (
        <div className="flex items-center gap-2.5">
            <Icon className="h-4 w-4 text-muted-foreground shrink-0" />
            <span className="text-xs text-muted-foreground w-24 shrink-0">{label}</span>
            <span className={`text-sm truncate ${bold ? "font-bold tracking-wider" : "font-medium"}`}>{value}</span>
        </div>
    );
}

function StatMini({ label, value, icon: Icon, color }: { label: string; value: any; icon: any; color: string }) {
    return (
        <div className="bg-background rounded-lg p-2.5 text-center">
            <Icon className={`h-4 w-4 ${color} mx-auto mb-1`} />
            <p className="text-sm font-bold">{typeof value === "number" ? value.toLocaleString() : value}</p>
            <p className="text-[10px] text-muted-foreground">{label}</p>
        </div>
    );
}

function DocRow({ label, expiry }: { label: string; expiry?: string }) {
    const isExpired = expiry && new Date(expiry) < new Date();
    const fmtDate = (d: string) => { try { return new Date(d).toLocaleDateString("en-CA", { month: "short", day: "numeric", year: "numeric" }); } catch { return d; } };
    return (
        <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
                <FileText className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="text-sm">{label}</span>
            </div>
            {expiry ? (
                <span className={`text-xs font-medium ${isExpired ? "text-red-500" : "text-emerald-600"}`}>
                    {isExpired ? "EXPIRED" : fmtDate(expiry)}
                </span>
            ) : (
                <span className="text-xs text-muted-foreground">Not set</span>
            )}
        </div>
    );
}
