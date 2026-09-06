// Drivers list: filter/toolbar header, status tabs, search, table, and
// pagination. Pure code motion out of drivers/page.tsx (design-audit
// follow-up, docs/change-log/2026-09-04-breakup-drivers-page-god-component.md)
// — no logic changes. This is a controlled/presentational component: every
// piece of state it reads or mutates is still owned by DriversPage and
// passed down as props, mirroring the rides/_components/ride-list.tsx
// pattern already established in this codebase.

import { PageHeader } from "@/components/page-header";
import { formatCurrency } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ClickableTableRow } from "@/components/ui/clickable-table-row";
import { Pagination } from "@/components/ui/pagination";
import { Search, Users, Wifi, ShieldCheck, ShieldAlert, Download, X, Star, Car, MapPin, Clock, Phone, CalendarRange, AlertTriangle, Image, Loader2, Eye, EyeOff, Ban, Pause, RefreshCw, Upload, Trash2, Tag, UserX, Globe, ArrowUpDown, ArrowUp, ArrowDown } from "lucide-react";
import { maskEmail, maskPhone, maskPlate } from "@/lib/pii";
import { logPiiReveal } from "@/lib/api";
import { driverDisplayName } from "./driver-detail-shared";
import DriverStatsCards from "./driver-stats-cards";
import AreaStatsTable from "./area-stats-table";
import DriverCharts from "./driver-charts";

const STATUS_TABS = [
    { value: "all", label: "All", icon: Users },
    { value: "active", label: "Active", icon: ShieldCheck },
    { value: "pending", label: "Pending", icon: ShieldAlert },
    { value: "needs_review", label: "Needs Review", icon: AlertTriangle },
    { value: "suspended", label: "Suspended", icon: Pause },
    { value: "banned", label: "Banned", icon: Ban },
    { value: "online", label: "Online", icon: Wifi },
    { value: "photos_pending", label: "Pending photos", icon: Image },
    // Abandoned-onboarding rows carried over by the legacy import: someone
    // OTP-verified a phone in the old app and never completed a profile. They
    // are real `drivers` rows (forced needs_review/unverified/offline, so they
    // can never dispatch) and they outnumber the real fleet ~2:1, which is why
    // every other tab excludes them and this one exists to reach them.
    { value: "legacy_incomplete", label: "Legacy incomplete", icon: UserX },
    // Legacy-imported rows whose phone is not a Canadian number. The old app's
    // database was a shared, multi-tenant SaaS export, so it carried in other
    // tenants' drivers alongside Spinr's. This is a REVIEW queue, not a verdict:
    // the classification is inferred from the area code (the export's own
    // country_code was never stored), so an admin confirms before acting.
    { value: "legacy_review", label: "Non-Canadian number", icon: Globe },
];

export default function DriverListTable({
    data,
    loading,
    serviceAreaId,
    setServiceAreaId,
    selectedAreaName,
    serviceAreas,
    vehicleTypeFilter,
    setVehicleTypeFilter,
    availableVehicleTypes,
    legacyFilter,
    setLegacyFilter,
    preLaunchFilter,
    setPreLaunchFilter,
    startDate,
    setStartDate,
    endDate,
    setEndDate,
    showPii,
    setShowPii,
    isSuperAdmin,
    bulkKycRunning,
    handleBulkKycRefresh,
    bulkPayoutsRunning,
    handleBulkPayoutRefresh,
    bulkTotalsRunning,
    handleRecomputeStatementTotals,
    handleExport,
    sorted,
    statusFilter,
    setStatusFilter,
    statusCounts,
    search,
    setSearch,
    tableLoading,
    sortKey,
    sortDir,
    handleSort,
    themeV2Enabled,
    vehicleTypes,
    fmtDate,
    selected,
    setSelected,
    page,
    setPage,
    hasNextPage,
    pageSize,
}: {
    data: any;
    loading: boolean;
    serviceAreaId: string;
    setServiceAreaId: (v: string) => void;
    selectedAreaName: string;
    serviceAreas: { id: string; name: string }[];
    vehicleTypeFilter: string;
    setVehicleTypeFilter: (v: string) => void;
    availableVehicleTypes: { id: string; name: string }[];
    legacyFilter: "all" | "imported" | "not_imported";
    setLegacyFilter: (v: "all" | "imported" | "not_imported") => void;
    preLaunchFilter: "all" | "hide" | "only";
    setPreLaunchFilter: (v: "all" | "hide" | "only") => void;
    startDate: string;
    setStartDate: (v: string) => void;
    endDate: string;
    setEndDate: (v: string) => void;
    showPii: boolean;
    setShowPii: (v: boolean) => void;
    isSuperAdmin: boolean;
    bulkKycRunning: boolean;
    handleBulkKycRefresh: () => void;
    bulkPayoutsRunning: boolean;
    handleBulkPayoutRefresh: () => void;
    bulkTotalsRunning: boolean;
    handleRecomputeStatementTotals: () => void;
    handleExport: () => void;
    sorted: any[];
    statusFilter: string;
    setStatusFilter: (v: string) => void;
    statusCounts: (s: string) => number;
    search: string;
    setSearch: (v: string) => void;
    tableLoading: boolean;
    sortKey: string;
    sortDir: "asc" | "desc";
    handleSort: (key: string) => void;
    themeV2Enabled: boolean;
    vehicleTypes: { id: string; name: string }[];
    fmtDate: (d: string) => string;
    selected: any;
    setSelected: (d: any) => void;
    page: number;
    setPage: (p: number) => void;
    hasNextPage: boolean;
    pageSize: number;
}) {
    const SortIcon = ({ col }: { col: string }) => { if (sortKey !== col) return <ArrowUpDown className="h-3 w-3 opacity-30 inline ml-1" />; return sortDir === "asc" ? <ArrowUp className="h-3 w-3 inline ml-1" /> : <ArrowDown className="h-3 w-3 inline ml-1" />; };

    return (
        <>
            <PageHeader
                className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between"
                title="Drivers"
                description={
                    // Real drivers, not the raw row count: the legacy import left
                    // abandoned-onboarding shells in the table. They stay visible
                    // as a separate figure so the two reconcile to `stats.total`.
                    `${data?.stats?.onboarded_total ?? data?.stats?.total ?? 0} drivers ${serviceAreaId ? `in ${selectedAreaName}` : "overall"}`
                    + (data?.stats?.legacy_incomplete ? ` \u00b7 ${data.stats.legacy_incomplete} legacy incomplete` : "")
                }
                actions={
                <div className="flex flex-wrap items-center gap-2">
                    <div className="flex items-center gap-1.5">
                        <MapPin className="h-4 w-4 text-muted-foreground" />
                        <Select value={serviceAreaId || "all"} onValueChange={(v) => setServiceAreaId(v === "all" ? "" : v)}>
                            <SelectTrigger className="h-9 text-xs w-[180px]" aria-label="Filter by service area"><SelectValue placeholder="All Service Areas" /></SelectTrigger>
                            <SelectContent><SelectItem value="all">All Service Areas</SelectItem>{serviceAreas.map(a => <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>)}</SelectContent>
                        </Select>
                    </div>
                    <div className="flex items-center gap-1.5">
                        <Car className="h-4 w-4 text-muted-foreground" />
                        <Select value={vehicleTypeFilter || "all"} onValueChange={(v) => setVehicleTypeFilter(v === "all" ? "" : v)}>
                            <SelectTrigger className="h-9 text-xs w-[160px]" aria-label="Filter by vehicle type"><SelectValue placeholder="All Vehicle Types" /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">All Vehicle Types</SelectItem>
                                {availableVehicleTypes.map(v => <SelectItem key={v.id} value={v.id}>{v.name}</SelectItem>)}
                            </SelectContent>
                        </Select>
                    </div>
                    <div className="flex items-center gap-1.5">
                        <Upload className="h-4 w-4 text-muted-foreground" />
                        <Select value={legacyFilter} onValueChange={(v) => setLegacyFilter(v as "all" | "imported" | "not_imported")}>
                            <SelectTrigger className="h-9 text-xs w-[150px]" aria-label="Filter by legacy import status"><SelectValue placeholder="All Drivers" /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">All Drivers</SelectItem>
                                <SelectItem value="imported">Imported only</SelectItem>
                                <SelectItem value="not_imported">Not imported</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>
                    <div className="flex items-center gap-1.5">
                        <Tag className="h-4 w-4 text-muted-foreground" />
                        <Select value={preLaunchFilter} onValueChange={(v) => setPreLaunchFilter(v as "all" | "hide" | "only")}>
                            <SelectTrigger className="h-9 text-xs w-[170px]" aria-label="Filter by pre-launch flag"><SelectValue placeholder="All Drivers" /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">All Drivers</SelectItem>
                                <SelectItem value="hide">Hide pre-launch test</SelectItem>
                                <SelectItem value="only">Pre-launch test only</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>
                    <div className="flex items-center gap-1.5">
                        <CalendarRange className="h-4 w-4 text-muted-foreground" />
                        <Input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} className="h-9 w-[140px] text-xs" aria-label="Filter from date" />
                        <span className="text-xs text-muted-foreground">to</span>
                        <Input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} className="h-9 w-[140px] text-xs" aria-label="Filter to date" />
                    </div>
                    {(serviceAreaId || vehicleTypeFilter || legacyFilter !== "all" || preLaunchFilter !== "all" || startDate || endDate) && <Button variant="ghost" size="sm" onClick={() => { setServiceAreaId(""); setVehicleTypeFilter(""); setLegacyFilter("all"); setPreLaunchFilter("all"); setStartDate(""); setEndDate(""); }}><X className="h-3.5 w-3.5" /> Clear</Button>}
                    <Button variant="outline" size="sm" onClick={() => { const next = !showPii; setShowPii(next); if (next) logPiiReveal("drivers", "page_toggle").catch(() => {}); }}>{showPii ? <EyeOff className="h-4 w-4 mr-1" /> : <Eye className="h-4 w-4 mr-1" />}{showPii ? "Hide PII" : "Show PII"}</Button>
                    {/* Fleet-wide money tools are super_admin server-side —
                        hide them for lower roles instead of surfacing buttons
                        that can only 403. */}
                    {isSuperAdmin && (
                        <>
                            <Button variant="outline" size="sm" onClick={handleBulkKycRefresh} disabled={bulkKycRunning} title="Pull live Stripe verification state for every driver with a Stripe account (super admin)">
                                {bulkKycRunning ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />} Refresh Stripe KYC
                            </Button>
                            <Button variant="outline" size="sm" onClick={handleBulkPayoutRefresh} disabled={bulkPayoutsRunning} title="Sync Stripe Transfers, bank payouts and balance transactions for every mapped driver (super admin). Safe to re-run.">
                                {bulkPayoutsRunning ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />} Sync All Payouts
                            </Button>
                            <Button variant="outline" size="sm" onClick={handleRecomputeStatementTotals} disabled={bulkTotalsRunning} title="Recompute the stored totals shown in every driver's statements list. Previews the diff before writing (super admin).">
                                {bulkTotalsRunning ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />} Fix Statement Totals
                            </Button>
                        </>
                    )}
                    <Button variant="outline" size="sm" onClick={handleExport} disabled={sorted.length === 0}><Download className="h-4 w-4" /> Export</Button>
                </div>
                }
            />

            <DriverStatsCards stats={data?.stats || null} loading={loading} />

            <div className="space-y-3">
                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                    <div className="flex gap-1.5 overflow-x-auto pb-1">
                        {STATUS_TABS.map(tab => (
                            <button key={tab.value} onClick={() => setStatusFilter(tab.value)} className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition ${statusFilter === tab.value ? "bg-primary text-white" : "bg-muted text-muted-foreground hover:bg-muted/80"}`}>
                                <tab.icon className="h-3.5 w-3.5" />{tab.label}<span className={`ml-1 px-1.5 rounded text-[10px] ${statusFilter === tab.value ? "bg-white/20" : "bg-background"}`}>{statusCounts(tab.value)}</span>
                            </button>
                        ))}
                    </div>
                    <div className="relative w-full sm:w-72"><Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" /><Input placeholder="Search by name, email, plate..." aria-label="Search drivers" value={search} onChange={e => setSearch(e.target.value)} className="pl-9 h-9 text-sm" /></div>
                </div>

                <div className="bg-card border rounded-2xl overflow-hidden shadow-sm">
                    <Table>
                        <TableHeader>
                            <TableRow className="bg-muted/50 hover:bg-muted/50 border-b-0">
                                <TableHead className="h-11 pl-5 w-20"><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Actions</span></TableHead>
                                <TableHead className="h-11 cursor-pointer select-none" onClick={() => handleSort("name")} tabIndex={0} role="columnheader" aria-sort={sortKey === "name" ? (sortDir === "asc" ? "ascending" : "descending") : "none"} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handleSort("name"); } }}><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Driver<SortIcon col="name" /></span></TableHead>
                                <TableHead className="h-11 cursor-pointer select-none" onClick={() => handleSort("status")} tabIndex={0} role="columnheader" aria-sort={sortKey === "status" ? (sortDir === "asc" ? "ascending" : "descending") : "none"} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handleSort("status"); } }}><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Status<SortIcon col="status" /></span></TableHead>
                                {/* Deliberately NOT sortable. Every other header here sorts at the
                                    DB over the whole table (sort_by -> _DRIVER_SORT_COLUMNS), but the
                                    completeness score is computed per-request from row data and is not
                                    a column — the backend would silently ignore the key and reorder by
                                    created_at while aria-sort announced a state the table was not in.
                                    Sorting this needs the score persisted first. */}
                                <TableHead className="h-11"><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Profile</span></TableHead>
                                <TableHead className="h-11 cursor-pointer select-none" onClick={() => handleSort("is_online")} tabIndex={0} role="columnheader" aria-sort={sortKey === "is_online" ? (sortDir === "asc" ? "ascending" : "descending") : "none"} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handleSort("is_online"); } }}><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Online<SortIcon col="is_online" /></span></TableHead>
                                <TableHead className="h-11 cursor-pointer select-none" onClick={() => handleSort("vehicle_type")} tabIndex={0} role="columnheader" aria-sort={sortKey === "vehicle_type" ? (sortDir === "asc" ? "ascending" : "descending") : "none"} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handleSort("vehicle_type"); } }}><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Vehicle Type<SortIcon col="vehicle_type" /></span></TableHead>
                                <TableHead className="h-11 cursor-pointer select-none" onClick={() => handleSort("vehicle_make")} tabIndex={0} role="columnheader" aria-sort={sortKey === "vehicle_make" ? (sortDir === "asc" ? "ascending" : "descending") : "none"} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handleSort("vehicle_make"); } }}><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Vehicle<SortIcon col="vehicle_make" /></span></TableHead>
                                <TableHead className="h-11 cursor-pointer select-none text-center" onClick={() => handleSort("rating")} tabIndex={0} role="columnheader" aria-sort={sortKey === "rating" ? (sortDir === "asc" ? "ascending" : "descending") : "none"} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handleSort("rating"); } }}><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Rating<SortIcon col="rating" /></span></TableHead>
                                <TableHead className="h-11 cursor-pointer select-none text-center" onClick={() => handleSort("total_rides")} tabIndex={0} role="columnheader" aria-sort={sortKey === "total_rides" ? (sortDir === "asc" ? "ascending" : "descending") : "none"} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handleSort("total_rides"); } }}><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Rides<SortIcon col="total_rides" /></span></TableHead>
                                <TableHead className="h-11 cursor-pointer select-none text-right" onClick={() => handleSort("total_earnings")} tabIndex={0} role="columnheader" aria-sort={sortKey === "total_earnings" ? (sortDir === "asc" ? "ascending" : "descending") : "none"} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handleSort("total_earnings"); } }}><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Earnings<SortIcon col="total_earnings" /></span></TableHead>
                                <TableHead className="h-11 cursor-pointer select-none" onClick={() => handleSort("region")} tabIndex={0} role="columnheader" aria-sort={sortKey === "region" ? (sortDir === "asc" ? "ascending" : "descending") : "none"} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handleSort("region"); } }}><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Region<SortIcon col="region" /></span></TableHead>
                                <TableHead className="h-11 cursor-pointer select-none pr-5" onClick={() => handleSort("created_at")} tabIndex={0} role="columnheader" aria-sort={sortKey === "created_at" ? (sortDir === "asc" ? "ascending" : "descending") : "none"} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handleSort("created_at"); } }}><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Joined<SortIcon col="created_at" /></span></TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {tableLoading ? Array.from({ length: 5 }).map((_, i) => (
                                <TableRow key={i} className="animate-pulse">
                                    <TableCell><div className="h-8 w-16 bg-muted rounded" /></TableCell>
                                    <TableCell className="py-4"><div className="flex items-center gap-3"><div className="w-10 h-10 rounded-full bg-muted" /><div className="space-y-2"><div className="h-3 w-24 bg-muted rounded" /><div className="h-2 w-16 bg-muted rounded" /></div></div></TableCell>
                                    <TableCell><div className="h-4 w-16 bg-muted rounded" /></TableCell>
                                    <TableCell><div className="h-4 w-14 bg-muted rounded" /></TableCell>
                                    <TableCell><div className="h-4 w-12 bg-muted rounded" /></TableCell>
                                    <TableCell><div className="h-3 w-16 bg-muted rounded" /></TableCell>
                                    <TableCell><div className="h-3 w-20 bg-muted rounded" /></TableCell>
                                    <TableCell><div className="h-4 w-8 bg-muted rounded mx-auto" /></TableCell>
                                    <TableCell><div className="h-4 w-8 bg-muted rounded mx-auto" /></TableCell>
                                    <TableCell><div className="h-4 w-12 bg-muted rounded ml-auto" /></TableCell>
                                    <TableCell><div className="h-3 w-16 bg-muted rounded" /></TableCell>
                                    <TableCell><div className="h-3 w-16 bg-muted rounded" /></TableCell>
                                </TableRow>
                            )) : sorted.length === 0 ? (
                                <TableRow>
                                    <TableCell colSpan={12} className="text-center py-20 text-muted-foreground"><Users className="h-12 w-12 mx-auto mb-3 opacity-20" /><p className="text-base font-medium">No drivers found</p><p className="text-sm mt-1">Try adjusting your search or filters</p></TableCell>
                                </TableRow>
                            ) : sorted.map(driver => {
                                const areaName = serviceAreas.find(a => a.id === driver.service_area_id)?.name;
                                return (
                                    <ClickableTableRow key={driver.id} className={`group transition-colors hover:bg-muted/40 ${selected?.id === driver.id ? "bg-primary/5 hover:bg-primary/5" : ""}`} onActivate={() => setSelected(driver)} ariaLabel={`${driver.first_name} ${driver.last_name}, ${driver.status}, ${driver.is_online ? "online" : "offline"}`}>
                                        <TableCell className="pl-4 align-middle">
                                            <Button size="sm" variant="secondary" className="h-7 text-[10px] font-medium px-2" onClick={(e) => { e.stopPropagation(); setSelected(driver); }}><Eye className="h-3 w-3 mr-1" />View</Button>
                                        </TableCell>
                                        <TableCell className="py-3">
                                            <div className="flex items-center gap-3">
                                                <div className="relative">
                                                    {/* Profile photo intentionally omitted from the list — loading
                                                        one image per row slowed the page down. Initials stand in here;
                                                        the real photo still renders in the detail slideout. */}
                                                    <div className="w-10 h-10 rounded-full bg-gradient-to-br from-primary/20 to-primary/5 flex items-center justify-center text-sm font-bold text-primary ring-1 ring-border shadow-sm">{(driver.first_name?.[0] || "")}{(driver.last_name?.[0] || "")}</div>
                                                    <span className={`absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-card ${driver.is_online ? "bg-success" : "bg-muted-foreground/40"}`} />
                                                </div>
                                                <div className="flex-1 min-w-0">
                                                    <p className="text-sm font-semibold truncate flex items-center gap-1.5">
                                                        {/* opacity-60 dropped: text-muted-foreground alone already reads
                                                            as de-emphasized; stacked with /60 it was 3.41:1, below AA's
                                                            4.5:1 (#2826). */}
                                                        {driverDisplayName(driver) || <span className="text-muted-foreground italic">Unnamed driver</span>}
                                                        {driver.legacy_import_metadata && Object.keys(driver.legacy_import_metadata).length > 0 && (
                                                            <span className="inline-block text-[10px] font-medium text-muted-foreground bg-muted rounded px-1.5 py-0.5 shrink-0">
                                                                Imported
                                                            </span>
                                                        )}
                                                    </p>
                                                    {driver.driver_code && <p className="text-[11px] font-mono text-muted-foreground truncate">{driver.driver_code}</p>}
                                                    {driver.email && <p className="text-[11px] text-muted-foreground truncate">{showPii ? driver.email : maskEmail(driver.email)}</p>}
                                                    {driver.phone && <p className="text-[11px] text-muted-foreground flex items-center gap-1 mt-0.5"><Phone className="h-2.5 w-2.5" /> {showPii ? driver.phone : maskPhone(driver.phone)}</p>}
                                                </div>
                                            </div>
                                        </TableCell>
                                        <TableCell>
                                            <div className="flex flex-col gap-1.5 items-start">
                                                {/* account_deleted wins over status: deletion cannot change
                                                    drivers.status, so a departed driver still carries "active".
                                                    Categorical driver-lifecycle-status map (6 states, 5 hues) --
                                                    same class as driver-action-bar.tsx's STATUS_CONFIG; not a
                                                    #2816 migration target (a 3-token system can't express 6
                                                    distinct states). */}
                                                {themeV2Enabled ? (
                                                    driver.account_deleted ? <Badge variant="outline" className="text-[10px] px-1.5 py-0"><Trash2 className="h-3 w-3 mr-1" />Deleted</Badge>
                                                    : driver.status === "active" ? <Badge variant="outline-success" className="text-[10px] px-1.5 py-0"><ShieldCheck className="h-3 w-3 mr-1" />Active</Badge>
                                                    : driver.status === "needs_review" ? <Badge variant="outline-warning" className="text-[10px] px-1.5 py-0"><AlertTriangle className="h-3 w-3 mr-1" />Needs Review</Badge>
                                                    : driver.status === "suspended" ? <Badge variant="outline-destructive" className="text-[10px] px-1.5 py-0"><Pause className="h-3 w-3 mr-1" />Suspended</Badge>
                                                    : driver.status === "banned" ? <Badge variant="outline-destructive" className="text-[10px] px-1.5 py-0"><Ban className="h-3 w-3 mr-1" />Banned</Badge>
                                                    : <Badge variant="outline" className="text-[10px] px-1.5 py-0"><ShieldAlert className="h-3 w-3 mr-1" />Pending</Badge>
                                                ) : (
                                                    // eslint-disable-next-line no-restricted-syntax -- categorical driver-lifecycle-status map (#2816)
                                                    driver.account_deleted ? <Badge variant="default" className="bg-zinc-200 text-zinc-700 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 text-[10px] px-1.5 py-0 border-zinc-300 dark:border-zinc-700"><Trash2 className="h-3 w-3 mr-1" />Deleted</Badge>
                                                    // eslint-disable-next-line no-restricted-syntax -- categorical driver-lifecycle-status map (#2816)
                                                    : driver.status === "active" ? <Badge variant="default" className="bg-emerald-100 text-emerald-700 hover:bg-emerald-100 dark:bg-emerald-900/30 dark:text-emerald-400 text-[10px] px-1.5 py-0 border-emerald-200 dark:border-emerald-800"><ShieldCheck className="h-3 w-3 mr-1" />Active</Badge>
                                                    // eslint-disable-next-line no-restricted-syntax -- categorical driver-lifecycle-status map (#2816)
                                                    : driver.status === "needs_review" ? <Badge variant="default" className="bg-amber-100 text-amber-700 hover:bg-amber-100 dark:bg-amber-900/30 dark:text-amber-400 text-[10px] px-1.5 py-0 border-amber-200 dark:border-amber-800"><AlertTriangle className="h-3 w-3 mr-1" />Needs Review</Badge>
                                                    // eslint-disable-next-line no-restricted-syntax -- categorical driver-lifecycle-status map (#2816)
                                                    : driver.status === "suspended" ? <Badge variant="default" className="bg-orange-100 text-orange-700 hover:bg-orange-100 dark:bg-orange-900/30 dark:text-orange-400 text-[10px] px-1.5 py-0 border-orange-200 dark:border-orange-800"><Pause className="h-3 w-3 mr-1" />Suspended</Badge>
                                                    // eslint-disable-next-line no-restricted-syntax -- categorical driver-lifecycle-status map (#2816)
                                                    : driver.status === "banned" ? <Badge variant="default" className="bg-red-200 text-red-800 hover:bg-red-200 dark:bg-red-900/40 dark:text-red-400 text-[10px] px-1.5 py-0 border-red-300 dark:border-red-800"><Ban className="h-3 w-3 mr-1" />Banned</Badge>
                                                    // eslint-disable-next-line no-restricted-syntax -- categorical driver-lifecycle-status map (#2816)
                                                    : <Badge variant="default" className="bg-blue-100 text-blue-700 hover:bg-blue-100 dark:bg-blue-900/30 dark:text-blue-400 text-[10px] px-1.5 py-0 border-blue-200 dark:border-blue-800"><ShieldAlert className="h-3 w-3 mr-1" />Pending</Badge>
                                                )}
                                                <Badge variant="outline" className={`text-[10px] px-1.5 py-0 ${driver.is_online && !driver.account_deleted ? "border-success/40 text-success bg-success/10" : ""}`}>{driver.is_online && !driver.account_deleted ? "Online" : "Offline"}</Badge>
                                            </div>
                                        </TableCell>
                                        <TableCell>
                                            {driver.profile_completeness_score === undefined || driver.profile_completeness_score === null ? (
                                                <Badge variant="outline" className="text-[10px] px-1.5 py-0 text-muted-foreground">—</Badge>
                                            ) : driver.profile_completeness_score === 100 ? (
                                                <Badge variant="default" className="bg-success/15 text-success hover:bg-success/15 text-[10px] px-1.5 py-0 border-success/30">Complete</Badge>
                                            ) : driver.profile_completeness_score >= 70 ? (
                                                <Badge variant="default" className="bg-warning/15 text-warning hover:bg-warning/15 text-[10px] px-1.5 py-0 border-warning/30">Incomplete ({driver.profile_completeness_score}%)</Badge>
                                            ) : (
                                                // dark:text-[#ff453a] — text-destructive alone is only 3.7:1 on this
                                                // bg-destructive/15 tint, below AA's 4.5:1 (#2826).
                                                <Badge variant="default" className="bg-destructive/15 text-destructive dark:text-[#ff453a] text-[10px] px-1.5 py-0">Missing ({driver.profile_completeness_score}%)</Badge>
                                            )}
                                        </TableCell>
                                        <TableCell>
                                            <div className="flex flex-col gap-0.5 items-start">
                                                <Badge variant={driver.is_online ? "default" : "outline"} className={driver.is_online ? "bg-success/15 text-success text-[10px] px-1.5 py-0 border-success/30" : "text-[10px] px-1.5 py-0 text-muted-foreground"}>
                                                    <span className={`h-1.5 w-1.5 rounded-full mr-1 ${driver.is_online ? "bg-success" : "bg-muted-foreground/40"}`} />
                                                    {driver.is_online ? "Online" : "Offline"}
                                                </Badge>
                                                {driver.last_status_changed_at && (
                                                    <span className="text-[10px] text-muted-foreground whitespace-nowrap" title={new Date(driver.last_status_changed_at).toLocaleString()}>
                                                        {driver.is_online ? "since " : "since "}
                                                        {new Date(driver.last_status_changed_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}
                                                    </span>
                                                )}
                                            </div>
                                        </TableCell>
                                        <TableCell>
                                            <span className="text-xs text-foreground/80">
                                                {/* Same #2826 opacity fix as "Unnamed driver" above. */}
                                                {vehicleTypes.find(v => v.id === driver.vehicle_type_id)?.name || <span className="text-muted-foreground italic">—</span>}
                                            </span>
                                        </TableCell>
                                        <TableCell>
                                            <div className="flex flex-col gap-1 text-xs">
                                                <div className="flex items-center gap-1.5 text-muted-foreground font-medium">
                                                    <Car className="h-3.5 w-3.5" />
                                                    <span className="truncate max-w-[120px]">{[driver.vehicle_color, driver.vehicle_make, driver.vehicle_model].filter(Boolean).join(" ") || "No vehicle"}</span>
                                                </div>
                                                {/* opacity-60 dropped from the "No plate" fallback — same #2826 fix
                                                    as above; the license_plate span's text-foreground/80 already
                                                    clears AA by a wide margin, so it's untouched. */}
                                                {driver.license_plate ? <span className="font-mono font-bold text-foreground/80 tracking-wider bg-muted px-1.5 py-0.5 rounded text-[10px] border shadow-sm self-start">{showPii ? driver.license_plate : maskPlate(driver.license_plate)}</span> : <span className="text-[10px] text-muted-foreground italic">No plate</span>}
                                            </div>
                                        </TableCell>
                                        <TableCell className="text-center">
                                            {/* eslint-disable-next-line no-restricted-syntax -- star-rating amber is a decorative convention, not a status signal (#2816) */}
                                            <span className="text-xs font-bold flex items-center justify-center gap-1"><Star className="h-3 w-3 text-amber-500 fill-amber-500" />{driver.rating?.toFixed(1) || "\u2014"}</span>
                                        </TableCell>
                                        <TableCell className="text-center">
                                            <span className="text-xs font-bold">{(driver.total_rides || 0).toLocaleString()}</span>
                                        </TableCell>
                                        <TableCell className="text-right">
                                            <span className="text-xs font-bold text-success">{formatCurrency(driver.total_earnings || 0)}</span>
                                        </TableCell>
                                        <TableCell>
                                            {/* eslint-disable-next-line no-restricted-syntax -- decorative map-pin marker tint, not a status signal (#2816) */}
                                            <div className="flex items-center gap-1.5 text-xs text-foreground font-medium truncate max-w-[120px]"><MapPin className="h-3.5 w-3.5 text-blue-500 shrink-0" />{areaName || "Unassigned"}</div>
                                        </TableCell>
                                        <TableCell className="pr-5">
                                            <div className="flex items-center gap-1.5 text-xs text-muted-foreground"><Clock className="h-3 w-3 shrink-0" />{fmtDate(driver.created_at)}</div>
                                        </TableCell>
                                    </ClickableTableRow>
                                );
                            })}
                        </TableBody>
                    </Table>
                </div>

                <Pagination
                    page={page}
                    pageSize={pageSize}
                    hasNextPage={hasNextPage}
                    onPageChange={setPage}
                />
            </div>

            {!serviceAreaId && <AreaStatsTable areaStats={data?.area_stats || []} loading={loading} onAreaClick={(areaId) => setServiceAreaId(areaId)} />}

            <DriverCharts charts={data?.charts || null} loading={loading} />
        </>
    );
}
