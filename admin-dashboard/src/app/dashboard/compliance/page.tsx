"use client";

import { useEffect, useState } from "react";
import { Download, Loader2, FileText } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
    DropdownMenu,
    DropdownMenuCheckboxItem,
    DropdownMenuContent,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/use-toast";
import { InfoHint as Hint } from "@/components/info-hint";
import { useRequireModule } from "@/hooks/useRequireModule";
import { useAuthStore } from "@/store/authStore";
import { monthToDateDefaults } from "@/lib/utils";
import {
    downloadAirportTrips,
    downloadDriverRoster,
    downloadGstPstRemittance,
    downloadInsuranceBillingKnightArcher,
    downloadInsuranceBillingSgi,
    downloadT4aFilerHandoff,
    getServiceAreas,
    type ComplianceReportFormat,
} from "@/lib/api";

/** Service-area scope for a report, as a checkbox multi-select.
 *
 * Why every insurer/authority report needs one: the insurance rates are
 * per-insurer and per-province (SGI is a Saskatchewan Crown insurer and does
 * not operate elsewhere), and each airport authority bills only its own
 * airport. Exporting every area at once invoices one party for another's
 * trips — silently, since nothing about the resulting file looks wrong.
 *
 * Nothing selected means "all areas", which is both the historical behaviour
 * and the safer default: an unscoped invoice is visibly too big, while an
 * accidentally-empty one just looks like a quiet month.
 *
 * Selecting a city also covers its airport sub-area — the backend expands
 * parents to children, because a ride picked up at the airport is recorded
 * against the airport's own service area, not the city's. */
function ServiceAreaScopeField({
    idPrefix,
    areas,
    selected,
    onChange,
}: {
    idPrefix: string;
    areas: { id: string; name: string }[];
    selected: string[];
    onChange: (ids: string[]) => void;
}) {
    const label =
        selected.length === 0
            ? "All service areas"
            : selected.length === 1
              ? (areas.find((a) => a.id === selected[0])?.name ?? "1 selected")
              : `${selected.length} selected`;

    const toggle = (id: string) => {
        onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);
    };

    return (
        <div className="space-y-1.5">
            <Label htmlFor={`${idPrefix}-areas`}>Service areas</Label>
            <DropdownMenu>
                <DropdownMenuTrigger asChild>
                    <Button
                        id={`${idPrefix}-areas`}
                        variant="outline"
                        className="w-56 justify-between font-normal"
                        aria-label="Service areas"
                    >
                        <span className="truncate">{label}</span>
                    </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="w-56 max-h-72 overflow-y-auto">
                    <DropdownMenuCheckboxItem
                        checked={selected.length === 0}
                        onCheckedChange={() => onChange([])}
                        onSelect={(e) => e.preventDefault()}
                    >
                        All service areas
                    </DropdownMenuCheckboxItem>
                    <DropdownMenuSeparator />
                    {areas.map((a) => (
                        <DropdownMenuCheckboxItem
                            key={a.id}
                            checked={selected.includes(a.id)}
                            onCheckedChange={() => toggle(a.id)}
                            onSelect={(e) => e.preventDefault()}
                        >
                            {a.name}
                        </DropdownMenuCheckboxItem>
                    ))}
                </DropdownMenuContent>
            </DropdownMenu>
            <p className="text-[11px] text-muted-foreground w-56">
                Selecting a city includes its airport zone.
            </p>
        </div>
    );
}

/** Shared From/To date-range control for every date-ranged Compliance
 * report — replaces the old rolling-N-days shorthand dropdown. Defaults
 * to the current calendar month, matching the backend's own default when
 * date_from/date_to are omitted.
 *
 * `idPrefix` keeps each call site's From/To ids unique — only one
 * TabsContent is mounted at a time (Radix doesn't forceMount here), so
 * literal collisions can't happen today, but four call sites share this
 * component and ids are cheap to keep unique regardless. */
function DateRangeFields({
    idPrefix,
    from,
    to,
    onFromChange,
    onToChange,
}: {
    idPrefix: string;
    from: string;
    to: string;
    onFromChange: (v: string) => void;
    onToChange: (v: string) => void;
}) {
    return (
        <>
            <div className="space-y-1.5">
                <Label htmlFor={`${idPrefix}-from`}>From</Label>
                <Input id={`${idPrefix}-from`} type="date" className="w-40" value={from} onChange={(e) => onFromChange(e.target.value)} />
            </div>
            <div className="space-y-1.5">
                <Label htmlFor={`${idPrefix}-to`}>To</Label>
                <Input id={`${idPrefix}-to`} type="date" className="w-40" value={to} onChange={(e) => onToChange(e.target.value)} />
            </div>
        </>
    );
}

const FORMATS: { value: ComplianceReportFormat; label: string }[] = [
    { value: "pdf", label: "PDF" },
    { value: "csv", label: "CSV" },
    { value: "xlsx", label: "Excel" },
    { value: "docx", label: "Word" },
];

const DRIVER_STATUSES = ["active", "pending", "needs_review", "suspended", "banned"];

function triggerBrowserDownload(blob: Blob, filename: string) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
}

/**
 * Compliance & Tax Reporting — regulatory/tax exports that aren't covered
 * by operational analytics (/dashboard/analytics) or the fixed-format SGI
 * forms (Data Transfer tab). Every report here is Spinr-branded and logged
 * to compliance_export_events server-side.
 */
export default function CompliancePage() {
    const { allowed } = useRequireModule("compliance");
    const { toast } = useToast();

    const monthDefaults = monthToDateDefaults();

    const [gstPstFrom, setGstPstFrom] = useState(monthDefaults.from);
    const [gstPstTo, setGstPstTo] = useState(monthDefaults.to);
    const [gstPstFormat, setGstPstFormat] = useState<ComplianceReportFormat>("pdf");
    const [gstPstLoading, setGstPstLoading] = useState(false);

    const [rosterFormat, setRosterFormat] = useState<ComplianceReportFormat>("pdf");
    const [rosterStatus, setRosterStatus] = useState("all");
    const [rosterLoading, setRosterLoading] = useState(false);

    const [t4aYear, setT4aYear] = useState(new Date().getFullYear() - 1);
    const [t4aFormat, setT4aFormat] = useState<ComplianceReportFormat>("xlsx");
    const [t4aLoading, setT4aLoading] = useState(false);
    const isSuperAdmin = useAuthStore((s) => s.user?.role === "super_admin");

    // Service areas for the scope pickers. A failed load leaves the list
    // empty, which degrades to "all areas" — the historical behaviour —
    // rather than blocking the download entirely.
    const [serviceAreas, setServiceAreas] = useState<{ id: string; name: string }[]>([]);
    useEffect(() => {
        let cancelled = false;
        getServiceAreas()
            .then((rows: any[]) => {
                if (cancelled) return;
                setServiceAreas(
                    (rows || [])
                        .map((r) => ({ id: String(r.id), name: String(r.name ?? r.city ?? r.id) }))
                        .sort((a, b) => a.name.localeCompare(b.name)),
                );
            })
            .catch(() => {
                /* non-fatal — see comment above */
            });
        return () => {
            cancelled = true;
        };
    }, []);

    const [sgiAreas, setSgiAreas] = useState<string[]>([]);
    const [kaAreas, setKaAreas] = useState<string[]>([]);
    const [airportAreas, setAirportAreas] = useState<string[]>([]);

    const [sgiFrom, setSgiFrom] = useState(monthDefaults.from);
    const [sgiTo, setSgiTo] = useState(monthDefaults.to);
    const [sgiFormat, setSgiFormat] = useState<ComplianceReportFormat>("pdf");
    const [sgiLoading, setSgiLoading] = useState(false);

    const [kaFrom, setKaFrom] = useState(monthDefaults.from);
    const [kaTo, setKaTo] = useState(monthDefaults.to);
    const [kaFormat, setKaFormat] = useState<ComplianceReportFormat>("pdf");
    const [kaLoading, setKaLoading] = useState(false);

    const [airportFrom, setAirportFrom] = useState(monthDefaults.from);
    const [airportTo, setAirportTo] = useState(monthDefaults.to);
    const [airportFormat, setAirportFormat] = useState<ComplianceReportFormat>("pdf");
    const [airportLoading, setAirportLoading] = useState(false);

    if (!allowed) return null;

    const onError = (e: any) =>
        toast({ title: "Could not generate report", description: e?.message || "Unknown error", variant: "destructive" });

    const onDownloadGstPst = async () => {
        setGstPstLoading(true);
        try {
            const { blob, filename } = await downloadGstPstRemittance(gstPstFormat, gstPstFrom, gstPstTo);
            triggerBrowserDownload(blob, filename);
        } catch (e: any) {
            onError(e);
        } finally {
            setGstPstLoading(false);
        }
    };

    const onDownloadRoster = async () => {
        setRosterLoading(true);
        try {
            const { blob, filename } = await downloadDriverRoster(
                rosterFormat,
                rosterStatus === "all" ? undefined : rosterStatus,
            );
            triggerBrowserDownload(blob, filename);
        } catch (e: any) {
            onError(e);
        } finally {
            setRosterLoading(false);
        }
    };

    const onDownloadT4a = async () => {
        setT4aLoading(true);
        try {
            const { blob, filename } = await downloadT4aFilerHandoff(t4aYear, t4aFormat);
            triggerBrowserDownload(blob, filename);
        } catch (e: any) {
            onError(e);
        } finally {
            setT4aLoading(false);
        }
    };

    const onDownloadSgi = async () => {
        setSgiLoading(true);
        try {
            const { blob, filename } = await downloadInsuranceBillingSgi(sgiFormat, sgiFrom, sgiTo, sgiAreas);
            triggerBrowserDownload(blob, filename);
        } catch (e: any) {
            onError(e);
        } finally {
            setSgiLoading(false);
        }
    };

    const onDownloadKa = async () => {
        setKaLoading(true);
        try {
            const { blob, filename } = await downloadInsuranceBillingKnightArcher(kaFormat, kaFrom, kaTo, kaAreas);
            triggerBrowserDownload(blob, filename);
        } catch (e: any) {
            onError(e);
        } finally {
            setKaLoading(false);
        }
    };

    const onDownloadAirport = async () => {
        setAirportLoading(true);
        try {
            const { blob, filename } = await downloadAirportTrips(airportFormat, airportFrom, airportTo, airportAreas);
            triggerBrowserDownload(blob, filename);
        } catch (e: any) {
            onError(e);
        } finally {
            setAirportLoading(false);
        }
    };

    return (
        <div className="p-6 space-y-6">
            <div>
                <h1 className="text-2xl font-semibold">Compliance & Tax Reporting</h1>
                <p className="text-muted-foreground">
                    Spinr-branded regulatory and tax exports — GST/PST remittance, per-insurer usage-based
                    billing, and driver roster. Every export here is logged with the requesting admin, date
                    range, and row count for a future privacy/regulatory audit. Fixed-format regulator
                    documents (SGI D00032/D00033) live under Data Transfer instead — this module never
                    re-styles those into Spinr branding.
                </p>
            </div>

            <Tabs defaultValue="gst-pst">
                <TabsList>
                    <TabsTrigger value="gst-pst">GST/PST Remittance</TabsTrigger>
                    <TabsTrigger value="insurance-billing-sgi">SGI Insurance Billing</TabsTrigger>
                    <TabsTrigger value="insurance-billing-ka">Knight Archer Insurance Billing</TabsTrigger>
                    <TabsTrigger value="driver-roster">Driver Roster</TabsTrigger>
                    {isSuperAdmin && <TabsTrigger value="t4a-filer">T4A Filer Handoff</TabsTrigger>}
                    <TabsTrigger value="airport-trips">Airport Trips</TabsTrigger>
                </TabsList>

                <TabsContent value="gst-pst">
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                                <FileText className="h-5 w-5" />
                                GST/PST Remittance Summary
                            </CardTitle>
                            <CardDescription>
                                GST/PST/HST collected on completed rides, grouped by month, summed from what
                                was actually charged on each ride&apos;s receipt — never recomputed.
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="flex flex-wrap items-end gap-4">
                                <DateRangeFields
                                    idPrefix="gst-pst"
                                    from={gstPstFrom}
                                    to={gstPstTo}
                                    onFromChange={setGstPstFrom}
                                    onToChange={setGstPstTo}
                                />
                                <div className="space-y-1.5">
                                    <Label>Format</Label>
                                    <Select
                                        value={gstPstFormat}
                                        onValueChange={(v) => setGstPstFormat(v as ComplianceReportFormat)}
                                    >
                                        <SelectTrigger className="w-32" aria-label="Format">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {FORMATS.map((f) => (
                                                <SelectItem key={f.value} value={f.value}>{f.label}</SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <Button onClick={onDownloadGstPst} disabled={gstPstLoading}>
                                    {gstPstLoading ? (
                                        <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                    ) : (
                                        <Download className="h-4 w-4 mr-2" />
                                    )}
                                    Download
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="insurance-billing-sgi">
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                                <FileText className="h-5 w-5" />
                                SGI Insurance Billing
                            </CardTitle>
                            <CardDescription className="flex items-start gap-1.5">
                                <span>
                                    Per-trip, per-phase insured kilometres for every driver in Period 2 (en
                                    route to pickup) or Period 3 (passenger aboard), Spinr&apos;s primary-
                                    commercial insurance coverage — billed at SGI&apos;s contracted rate,
                                    $0.11/km. Monthly cadence by default.
                                </span>
                                <Hint text="Rate is fixed at $0.11/km, not entered per-report — SGI's contracted rate doesn't change report to report, and a fixed rate removes the chance of a typo misstating the invoice. Each row is one trip's one phase, showing that phase's own GPS-measured distance, not a per-driver total, so SGI can audit down to the trip." />
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="flex flex-wrap items-end gap-4">
                                <DateRangeFields
                                    idPrefix="sgi"
                                    from={sgiFrom}
                                    to={sgiTo}
                                    onFromChange={setSgiFrom}
                                    onToChange={setSgiTo}
                                />
                                <ServiceAreaScopeField
                                    idPrefix="sgi"
                                    areas={serviceAreas}
                                    selected={sgiAreas}
                                    onChange={setSgiAreas}
                                />
                                <div className="space-y-1.5">
                                    <Label>Format</Label>
                                    <Select value={sgiFormat} onValueChange={(v) => setSgiFormat(v as ComplianceReportFormat)}>
                                        <SelectTrigger className="w-32" aria-label="Format">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {FORMATS.map((f) => (
                                                <SelectItem key={f.value} value={f.value}>{f.label}</SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <Button onClick={onDownloadSgi} disabled={sgiLoading}>
                                    {sgiLoading ? (
                                        <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                    ) : (
                                        <Download className="h-4 w-4 mr-2" />
                                    )}
                                    Download
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="insurance-billing-ka">
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                                <FileText className="h-5 w-5" />
                                Knight Archer Insurance Billing
                            </CardTitle>
                            <CardDescription className="flex items-start gap-1.5">
                                <span>
                                    Same per-trip, per-phase insured kilometres as the SGI report, billed at
                                    Knight Archer&apos;s contracted rate, $0.011/km. Monthly cadence by
                                    default.
                                </span>
                                <Hint text="Rate is fixed at $0.011/km, not entered per-report. Only Periods 2 and 3 count — Period 1 (available, no assigned ride) is contingent-liability coverage, billed differently." />
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="flex flex-wrap items-end gap-4">
                                <DateRangeFields idPrefix="ka" from={kaFrom} to={kaTo} onFromChange={setKaFrom} onToChange={setKaTo} />
                                <ServiceAreaScopeField
                                    idPrefix="ka"
                                    areas={serviceAreas}
                                    selected={kaAreas}
                                    onChange={setKaAreas}
                                />
                                <div className="space-y-1.5">
                                    <Label>Format</Label>
                                    <Select value={kaFormat} onValueChange={(v) => setKaFormat(v as ComplianceReportFormat)}>
                                        <SelectTrigger className="w-32" aria-label="Format">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {FORMATS.map((f) => (
                                                <SelectItem key={f.value} value={f.value}>{f.label}</SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <Button onClick={onDownloadKa} disabled={kaLoading}>
                                    {kaLoading ? (
                                        <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                    ) : (
                                        <Download className="h-4 w-4 mr-2" />
                                    )}
                                    Download
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="driver-roster">
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                                <FileText className="h-5 w-5" />
                                Driver Roster
                            </CardTitle>
                            <CardDescription className="flex items-start gap-1.5">
                                <span>
                                    Driver name, license number, license class, and current status for every
                                    onboarded driver. Originally built to keep Knight Archer Insurance current
                                    on active drivers monthly — includes every status by default (not just
                                    active) unless you filter below.
                                </span>
                                <Hint text="Knight Archer needs the full roster including pending/needs_review/suspended/banned drivers, not just active ones — that's why 'All statuses' is the default here." />
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="flex flex-wrap items-end gap-4">
                                <div className="space-y-1.5">
                                    <Label>Status</Label>
                                    <Select value={rosterStatus} onValueChange={setRosterStatus}>
                                        <SelectTrigger className="w-44" aria-label="Status">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="all">All statuses</SelectItem>
                                            {DRIVER_STATUSES.map((s) => (
                                                <SelectItem key={s} value={s}>{s.replace(/_/g, " ")}</SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className="space-y-1.5">
                                    <Label>Format</Label>
                                    <Select
                                        value={rosterFormat}
                                        onValueChange={(v) => setRosterFormat(v as ComplianceReportFormat)}
                                    >
                                        <SelectTrigger className="w-32" aria-label="Format">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {FORMATS.map((f) => (
                                                <SelectItem key={f.value} value={f.value}>{f.label}</SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <Button onClick={onDownloadRoster} disabled={rosterLoading}>
                                    {rosterLoading ? (
                                        <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                    ) : (
                                        <Download className="h-4 w-4 mr-2" />
                                    )}
                                    Download
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>

                {isSuperAdmin && (
                    <TabsContent value="t4a-filer">
                        <Card>
                            <CardHeader>
                                <CardTitle className="flex items-center gap-2">
                                    <FileText className="h-5 w-5" />
                                    T4A Filer Handoff
                                </CardTitle>
                                <CardDescription className="flex items-start gap-1.5">
                                    <span>
                                        Per-driver annual earnings plus Stripe-verified legal name and mailing
                                        address, for every driver at or above the $500 CRA reporting threshold —
                                        for handoff to a third-party tax filer (accountant or platform-reporting
                                        vendor). Never includes the SIN itself: SIN stays on Stripe&apos;s side and
                                        is retrieved per-driver only via the audited &quot;Reveal SIN&quot; action on a
                                        driver&apos;s Payouts tab when actually filing.
                                    </span>
                                    <Hint text="Two CRA obligations can apply to Spinr: the traditional T4A (Box 048, $500 threshold) and the newer Reportable Platform Operator rules (Part XX.1, since Jan 2024). Which apply, and whether they overlap, is a determination for your tax filer — this export gives them everything except the SIN." />
                                </CardDescription>
                            </CardHeader>
                            <CardContent className="space-y-4">
                                <div className="flex flex-wrap items-end gap-4">
                                    <div className="space-y-1.5">
                                        <Label htmlFor="t4a-tax-year">Tax year</Label>
                                        <Input
                                            id="t4a-tax-year"
                                            type="number"
                                            className="w-28"
                                            value={t4aYear}
                                            onChange={(e) => setT4aYear(Number(e.target.value) || t4aYear)}
                                            min={2020}
                                            max={2100}
                                        />
                                    </div>
                                    <div className="space-y-1.5">
                                        <Label>Format</Label>
                                        <Select value={t4aFormat} onValueChange={(v) => setT4aFormat(v as ComplianceReportFormat)}>
                                            <SelectTrigger className="w-32" aria-label="Format">
                                                <SelectValue />
                                            </SelectTrigger>
                                            <SelectContent>
                                                {FORMATS.map((f) => (
                                                    <SelectItem key={f.value} value={f.value}>{f.label}</SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                    </div>
                                    <Button onClick={onDownloadT4a} disabled={t4aLoading}>
                                        {t4aLoading ? (
                                            <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                        ) : (
                                            <Download className="h-4 w-4 mr-2" />
                                        )}
                                        Download
                                    </Button>
                                </div>
                                <p className="text-xs text-muted-foreground">
                                    Super admin only — this export surfaces verified legal name and mailing
                                    address in bulk, more sensitive than the aggregate totals in the other
                                    reports on this page. Download and hand off through your own secure
                                    channel to the filer.
                                </p>
                            </CardContent>
                        </Card>
                    </TabsContent>
                )}

                <TabsContent value="airport-trips">
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                                <FileText className="h-5 w-5" />
                                Airport Trips
                            </CardTitle>
                            <CardDescription className="flex items-start gap-1.5">
                                <span>
                                    Completed rides with &quot;airport&quot; in the pickup or dropoff address —
                                    date/time, trip type, rider, driver, vehicle registration, pickup/dropoff
                                    points, distance, and service area — for the airport authority to invoice
                                    Spinr per trip. Pickup and dropoff counts are broken out in the report so
                                    they can be reconciled against the authority&apos;s per-trip fee separately.
                                </span>
                                <Hint text="Matched by a text search on the pickup/dropoff address, not a dedicated airport flag — Spinr doesn't yet link rides to a specific pickup venue. Column set follows the general convention most North American airport TNC programs use (date/time, rider, driver, vehicle, pickup-vs-dropoff, distance) — confirm against your specific airport authority's actual reporting requirements before submitting." />
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="flex flex-wrap items-end gap-4">
                                <DateRangeFields
                                    idPrefix="airport"
                                    from={airportFrom}
                                    to={airportTo}
                                    onFromChange={setAirportFrom}
                                    onToChange={setAirportTo}
                                />
                                <ServiceAreaScopeField
                                    idPrefix="airport"
                                    areas={serviceAreas}
                                    selected={airportAreas}
                                    onChange={setAirportAreas}
                                />
                                <div className="space-y-1.5">
                                    <Label>Format</Label>
                                    <Select value={airportFormat} onValueChange={(v) => setAirportFormat(v as ComplianceReportFormat)}>
                                        <SelectTrigger className="w-32" aria-label="Format">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {FORMATS.map((f) => (
                                                <SelectItem key={f.value} value={f.value}>{f.label}</SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <Button onClick={onDownloadAirport} disabled={airportLoading}>
                                    {airportLoading ? (
                                        <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                    ) : (
                                        <Download className="h-4 w-4 mr-2" />
                                    )}
                                    Download
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>
            </Tabs>
        </div>
    );
}
