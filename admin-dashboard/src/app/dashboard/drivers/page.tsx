"use client";

import { useEffect, useState, useCallback, useMemo, useRef } from "react";
import { getDriverStats, getDrivers, getDriverDocuments, downloadDriverDocument, reviewDocument, updateDriver, reviewDriverPhoto, uploadDriverPhoto, getDriverVehicleHistory, getServiceAreas, getVehicleTypes, getFareConfigs, exportDrivers, getDriverRides, getDriverLiveStats, getDriverPayoutsSummary, getDriverReferrals, getDriverTraining, retryPayout, refreshDriverStripeKyc, refreshDriverStripePayouts, refreshAllDriverStripeKyc, refreshAllDriverStripePayouts, recomputeStatementTotals, revealDriverSin, logPiiReveal, getAdminSubscriptionPayments, type DriverLiveStats, type DriverPayoutSummary, type DriverReferralSummary, type DriverTraining } from "@/lib/api";
import { Pagination } from "@/components/ui/pagination";
import { exportToCsv } from "@/lib/export-csv";
import { formatCurrency } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetTitle, SheetDescription } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import { Search, Users, Wifi, ShieldCheck, ShieldAlert, Shield, Download, X, Star, Car, MapPin, CreditCard, Clock, DollarSign, CheckCircle, XCircle, FileText, Phone, Mail, CalendarRange, ExternalLink, Copy, AlertTriangle, ZoomIn, Image, Pencil, Save, Loader2, Eye, EyeOff, ArrowUpDown, ArrowUp, ArrowDown, Ban, Pause, Maximize2, RefreshCw, GraduationCap, Award, Upload, Trash2 } from "lucide-react";
import { maskEmail, maskPhone, maskPlate, maskVin } from "@/lib/pii";
import { DocumentReviewer } from "./_components/document-reviewer";
import { DocumentUploadDialog } from "./_components/document-upload-dialog";
import DriverStatsCards from "./_components/driver-stats-cards";
import DriverCharts from "./_components/driver-charts";
import AreaStatsTable from "./_components/area-stats-table";
import DriverActionBar from "./_components/driver-action-bar";
import DriverNotes from "./_components/driver-notes";
import DriverTimeline from "./_components/driver-timeline";
import { DriverStatementsPanel } from "./_components/driver-statements-panel";
import DriverActivity from "./_components/driver-activity";
import { useRequireModule } from "@/hooks/useRequireModule";
import { useToast } from "@/components/ui/use-toast";
import { useAuthStore } from "@/store/authStore";

const STATUS_TABS = [
    { value: "all", label: "All", icon: Users },
    { value: "active", label: "Active", icon: ShieldCheck },
    { value: "pending", label: "Pending", icon: ShieldAlert },
    { value: "needs_review", label: "Needs Review", icon: AlertTriangle },
    { value: "suspended", label: "Suspended", icon: Pause },
    { value: "banned", label: "Banned", icon: Ban },
    { value: "online", label: "Online", icon: Wifi },
    { value: "photos_pending", label: "Pending photos", icon: Image },
];

const PAGE_SIZE = 50;

// Match a driver_documents row to a service-area requirement using
// one consistent priority everywhere:
//   1. requirement_key — canonical slug stored since migration 28
//   2. requirement_id — UUID, or the slug treated as a legacy id
//   3. document_type exact match (label or de-snaked key)
//   4. fuzzy: slugified document_type contains slugified key
// Previously this logic lived in 4 different places with slight drift,
// which caused expiry summaries and "requires-expiry" detection to
// silently miss docs that only carried a requirement_key.
function matchesRequirement(
    d: any,
    req: { id?: string; key: string; label?: string },
): boolean {
    if (d.requirement_key) return d.requirement_key === req.key;
    if (d.requirement_id) return d.requirement_id === req.id || d.requirement_id === req.key;
    const dt = (d.document_type || "").toLowerCase();
    const label = (req.label || "").toLowerCase();
    const keySpaced = req.key.toLowerCase().replace(/_/g, " ");
    if (dt === label || dt === keySpaced) return true;
    const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "_");
    return slug(dt).includes(slug(req.key));
}

export default function DriversPage() {
    const { allowed } = useRequireModule("drivers");
    const { toast } = useToast();
    // SIN reveal and the Stripe payout sync are both gated to super_admin
    // server-side (admin_reveal_driver_sin / admin_refresh_driver_stripe_payouts)
    // — gate the UI the same way so lower roles never see a button that can
    // only 403. Plain `admin` users see only the last-4 from cache columns.
    const currentUserRole = useAuthStore((s) => s.user?.role);
    const isSuperAdmin = (currentUserRole || "").toLowerCase() === "super_admin";
    const canRevealSin = isSuperAdmin;
    const [data, setData] = useState<any>(null);
    const [drivers, setDrivers] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [tableLoading, setTableLoading] = useState(true);
    const [page, setPage] = useState(0);
    const [hasNextPage, setHasNextPage] = useState(false);
    const reqIdRef = useRef(0);
    const [search, setSearch] = useState("");
    // Debounced copy of `search` that actually hits the DB. Searching and
    // sorting are server-side (across the WHOLE drivers table, not just the
    // rows on the current page), so we throttle keystrokes to avoid a request
    // per character.
    const [searchDebounced, setSearchDebounced] = useState("");
    const [showPii, setShowPii] = useState(false);
    const [statusFilter, setStatusFilter] = useState("all");
    const [sortKey, setSortKey] = useState<string>("created_at");
    const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
    const [selected, setSelected] = useState<any>(null);
    const [driverDocs, setDriverDocs] = useState<any[]>([]);
    const [vehicleHistory, setVehicleHistory] = useState<any[]>([]);
    const [docsLoading, setDocsLoading] = useState(false);
    const [docBusy, setDocBusy] = useState<string | null>(null);
    const [reviewingDoc, setReviewingDoc] = useState<{ id: string; action: "approved" | "rejected"; docType?: string; requiresExpiry?: boolean } | null>(null);
    const [reviewExpiry, setReviewExpiry] = useState("");
    const [reviewReason, setReviewReason] = useState("");
    const [previewUrl, setPreviewUrl] = useState<string | null>(null);
    const [openReviewerForDriver, setOpenReviewerForDriver] = useState<{ id: string; name: string } | null>(null);
    const [uploadDialogOpen, setUploadDialogOpen] = useState(false);
    const [editing, setEditing] = useState(false);
    const [editForm, setEditForm] = useState<Record<string, any>>({});
    const [saving, setSaving] = useState(false);
    const [allServiceAreas, setAllServiceAreas] = useState<any[]>([]);
    // Vehicle types catalogue + serviceAreaId → allowed type IDs map,
    // built from active fare_configs — same narrowing the monitoring
    // page uses so picking an area here shows only vehicle types
    // actually configured for it.
    const [vehicleTypes, setVehicleTypes] = useState<{ id: string; name: string }[]>([]);
    const [vehicleTypesByArea, setVehicleTypesByArea] = useState<Record<string, Set<string>>>({});
    const [serviceAreaId, setServiceAreaId] = useState<string>("");
    // Vehicle-type filter on the drivers list (client-side — same
    // shape as serviceAreaId, "" means no filter).
    const [vehicleTypeFilter, setVehicleTypeFilter] = useState<string>("");
    const [startDate, setStartDate] = useState("");
    const [endDate, setEndDate] = useState("");
    const [serviceAreas, setServiceAreas] = useState<{ id: string; name: string }[]>([]);
    const [driverRides, setDriverRides] = useState<any[]>([]);
    const [ridesLoading, setRidesLoading] = useState(false);
    const [ridesLoaded, setRidesLoaded] = useState<string | null>(null);
    const [detailTab, setDetailTab] = useState<string>("overview");
    const [referrals, setReferrals] = useState<DriverReferralSummary | null>(null);
    const [referralsLoading, setReferralsLoading] = useState(false);
    const [referralsLoaded, setReferralsLoaded] = useState<string | null>(null);
    const [driverSubPayments, setDriverSubPayments] = useState<any[]>([]);
    const [subPaymentsLoading, setSubPaymentsLoading] = useState(false);
    const [subPaymentsLoaded, setSubPaymentsLoaded] = useState<string | null>(null);
    const [training, setTraining] = useState<DriverTraining | null>(null);
    const [trainingLoading, setTrainingLoading] = useState(false);
    const [trainingLoaded, setTrainingLoaded] = useState<string | null>(null);
    const [trainingError, setTrainingError] = useState<string | null>(null);
    const [liveStats, setLiveStats] = useState<DriverLiveStats | null>(null);
    const [payoutSummary, setPayoutSummary] = useState<DriverPayoutSummary | null>(null);
    const [payoutLoading, setPayoutLoading] = useState(false);
    const [retryingPayoutId, setRetryingPayoutId] = useState<string | null>(null);
    const [refreshingKyc, setRefreshingKyc] = useState(false);
    const [refreshingPayouts, setRefreshingPayouts] = useState(false);
    // The revealed SIN is held briefly in memory then cleared. Never
    // logged, never persisted, never written to any other state path.
    const [revealedSin, setRevealedSin] = useState<{ sin: string; expiresAt: number } | null>(null);

    // Auto-clear the revealed SIN after 30 seconds so it doesn't linger
    // on screen if the admin walks away from the desk.
    useEffect(() => {
        if (!revealedSin) return;
        const ms = revealedSin.expiresAt - Date.now();
        if (ms <= 0) { setRevealedSin(null); return; }
        const t = setTimeout(() => setRevealedSin(null), ms);
        return () => clearTimeout(t);
    }, [revealedSin]);

    // Clear any revealed SIN when the slideout changes driver — under no
    // circumstance should it persist across selections.
    useEffect(() => { setRevealedSin(null); }, [selected?.id]);


    const loadDriverRides = useCallback(async (driverId: string) => {
        if (ridesLoaded === driverId) return;
        setRidesLoading(true);
        try {
            const res = await getDriverRides(driverId);
            setDriverRides(res?.rides || []);
            setRidesLoaded(driverId);
        } catch {
            setDriverRides([]);
        } finally {
            setRidesLoading(false);
        }
    }, [ridesLoaded]);

    const loadDriverReferrals = useCallback(async (driverId: string) => {
        if (referralsLoaded === driverId) return;
        setReferralsLoading(true);
        try {
            const res = await getDriverReferrals(driverId);
            setReferrals(res);
            setReferralsLoaded(driverId);
        } catch {
            setReferrals(null);
        } finally {
            setReferralsLoading(false);
        }
    }, [referralsLoaded]);

    const loadDriverSubscriptions = useCallback(async (driverId: string) => {
        if (subPaymentsLoaded === driverId) return;
        setSubPaymentsLoading(true);
        try {
            const res = await getAdminSubscriptionPayments({ driver_id: driverId, limit: 100 });
            setDriverSubPayments(res?.payments ?? []);
            setSubPaymentsLoaded(driverId);
        } catch {
            setDriverSubPayments([]);
        } finally {
            setSubPaymentsLoading(false);
        }
    }, [subPaymentsLoaded]);

    // Request-id guard: the LMS lookup can be slow, so a response that
    // arrives after the admin switched drivers (or triggered a newer
    // refresh) must be discarded — otherwise driver A's training data
    // renders in driver B's drawer. The ref also bumps on drawer close /
    // driver change (see the selected?.id effect) to invalidate in-flight
    // requests even when no new one starts.
    const trainingReqRef = useRef(0);
    const loadDriverTraining = useCallback(async (driverId: string, refresh = false) => {
        if (!refresh && trainingLoaded === driverId) return;
        const reqId = ++trainingReqRef.current;
        setTrainingLoading(true);
        setTrainingError(null);
        try {
            const res = await getDriverTraining(driverId, refresh);
            if (reqId !== trainingReqRef.current) return;
            setTraining(res);
            setTrainingLoaded(driverId);
        } catch (e: any) {
            if (reqId !== trainingReqRef.current) return;
            setTraining(null);
            setTrainingError(e?.message || "Failed to load training data from the LMS");
        } finally {
            if (reqId === trainingReqRef.current) setTrainingLoading(false);
        }
    }, [trainingLoaded]);

    const loadData = useCallback(() => {
        setLoading(true);
        const params: any = {};
        if (serviceAreaId) params.service_area_id = serviceAreaId;
        if (startDate) params.start_date = startDate;
        if (endDate) params.end_date = endDate;
        getDriverStats(params).then((res) => { setData(res); setServiceAreas(res.service_areas || []); }).catch(() => {}).finally(() => setLoading(false));
    }, [serviceAreaId, startDate, endDate]);

    const loadDrivers = useCallback(() => {
        setTableLoading(true);
        const reqId = ++reqIdRef.current;
        // Everything the list narrows/orders by is sent to the server so the
        // query runs over the entire table: search, service-area, vehicle-type,
        // status, and sort. The browser only renders the returned page.
        const opts: any = { limit: PAGE_SIZE + 1, offset: page * PAGE_SIZE, sort_by: sortKey, sort_dir: sortDir };
        if (searchDebounced) opts.search = searchDebounced;
        if (serviceAreaId) opts.service_area_id = serviceAreaId;
        if (vehicleTypeFilter) opts.vehicle_type_id = vehicleTypeFilter;
        if (statusFilter === "online") opts.is_online = true;
        else if (statusFilter === "photos_pending") opts.photo_status = "pending_review";
        else if (["active", "pending", "needs_review", "suspended", "banned"].includes(statusFilter)) opts.status = statusFilter;
        getDrivers(opts)
            .then((rows) => {
                if (reqId !== reqIdRef.current) return;
                const arr = Array.isArray(rows) ? rows : [];
                setHasNextPage(arr.length > PAGE_SIZE);
                setDrivers(arr.slice(0, PAGE_SIZE));
            })
            .catch(() => { if (reqId === reqIdRef.current) { setDrivers([]); setHasNextPage(false); } })
            .finally(() => { if (reqId === reqIdRef.current) setTableLoading(false); });
    }, [page, serviceAreaId, statusFilter, searchDebounced, vehicleTypeFilter, sortKey, sortDir]);

    useEffect(() => { loadData(); }, [loadData]);
    useEffect(() => { loadDrivers(); }, [loadDrivers]);
    // Debounce the search box (300ms) into the value that drives the DB query.
    useEffect(() => {
        const t = setTimeout(() => setSearchDebounced(search.trim()), 300);
        return () => clearTimeout(t);
    }, [search]);
    // Reset to first page whenever anything that changes the result set or its
    // ordering changes — otherwise a new search/sort could land you on a page
    // that no longer exists.
    useEffect(() => { setPage(0); }, [statusFilter, serviceAreaId, searchDebounced, vehicleTypeFilter, sortKey, sortDir]);
    // Vehicle-type catalogue + areaId → allowed vt-id set. The map is
    // unioned from BOTH pricing stores because admins can configure
    // vehicles for an area either way:
    //   - fare_configs table (used by fare calc, joins by vehicle_type_id)
    //   - service_areas.vehicle_pricing JSONB (used by the Service Areas
    //     admin editor, joins by vehicle type NAME)
    // Without the JSONB half, the drawer complains "No fare configs"
    // even after the operator set pricing in the Service Areas page.
    useEffect(() => {
        Promise.all([
            getServiceAreas(),
            getVehicleTypes().catch(() => [] as any[]),
            getFareConfigs().catch(() => [] as any[]),
        ])
            .then(([areas, vt, configs]) => {
                setAllServiceAreas(areas || []);
                const types: { id: string; name: string }[] = (vt || []).map((v: any) => ({ id: v.id, name: v.name }));
                setVehicleTypes(types);
                const byName: Record<string, string> = {};
                for (const t of types) byName[t.name] = t.id;

                const map: Record<string, Set<string>> = {};
                // From fare_configs (direct id refs)
                for (const c of configs || []) {
                    if (c?.is_active === false) continue;
                    const aId = c?.service_area_id;
                    const vtId = c?.vehicle_type_id;
                    if (!aId || !vtId) continue;
                    if (!map[aId]) map[aId] = new Set<string>();
                    map[aId].add(vtId);
                }
                // From service_areas.vehicle_pricing (name-based)
                for (const area of areas || []) {
                    const pricing = Array.isArray(area?.vehicle_pricing) ? area.vehicle_pricing : [];
                    for (const row of pricing) {
                        const name = row?.vehicle_type;
                        if (!name) continue;
                        const vtId = byName[name];
                        if (!vtId) continue;
                        if (!map[area.id]) map[area.id] = new Set<string>();
                        map[area.id].add(vtId);
                    }
                }
                setVehicleTypesByArea(map);
            })
            .catch(() => {});
    }, []);

    // Only show vehicle types that are configured for the selected service area
    // (or for any area when no area is selected). Reuses the vehicleTypesByArea
    // map already built from fare_configs + service_areas.vehicle_pricing above.
    const availableVehicleTypes = useMemo(() => {
        if (serviceAreaId) {
            const allowed = vehicleTypesByArea[serviceAreaId];
            return vehicleTypes.filter(v => allowed?.has(v.id));
        }
        const allConfigured = new Set(
            Object.values(vehicleTypesByArea).flatMap(s => [...s])
        );
        return allConfigured.size > 0
            ? vehicleTypes.filter(v => allConfigured.has(v.id))
            : vehicleTypes;
    }, [vehicleTypes, vehicleTypesByArea, serviceAreaId]);

    // Clear the vehicle type filter if the selected type drops out of scope
    // (e.g. admin picks a service area that doesn't offer that type).
    useEffect(() => {
        if (vehicleTypeFilter && !availableVehicleTypes.some(v => v.id === vehicleTypeFilter)) {
            setVehicleTypeFilter("");
        }
    }, [availableVehicleTypes, vehicleTypeFilter]);

    useEffect(() => { if (!selected?.id) { setDriverDocs([]); return; } setDocsLoading(true); getDriverDocuments(selected.id).then((d) => setDriverDocs(Array.isArray(d) ? d : [])).catch(() => setDriverDocs([])).finally(() => setDocsLoading(false)); }, [selected?.id]);
    useEffect(() => { if (!selected?.id) { setVehicleHistory([]); return; } getDriverVehicleHistory(selected.id).then((r) => setVehicleHistory(r?.history || [])).catch(() => setVehicleHistory([])); }, [selected?.id]);
    useEffect(() => { setEditing(false); setEditForm({}); }, [selected?.id]);
    useEffect(() => {
        if (!selected?.id) {
            setDriverRides([]);
            setRidesLoaded(null);
            setReferrals(null);
            setReferralsLoaded(null);
            setLiveStats(null);
            setPayoutSummary(null);
            setDriverSubPayments([]);
            setSubPaymentsLoaded(null);
            trainingReqRef.current++; // invalidate any in-flight LMS request
            setTraining(null);
            setTrainingLoaded(null);
            setTrainingError(null);
            return;
        }
        setDetailTab("overview");
        // Switching directly A → B: drop A's training data and invalidate
        // any in-flight LMS request so it can't render under driver B.
        trainingReqRef.current++;
        setTraining(null);
        setTrainingLoaded(null);
        setTrainingError(null);
        // Live-stats compute Rating / Rides / Earnings / Accept Rate from
        // the rides table on demand because three of the four denormalised
        // columns on the drivers row are unreliable (see backend comment in
        // routes/admin/drivers.py admin_get_driver_live_stats). Cheap query,
        // worth the round-trip to avoid stale headers.
        setLiveStats(null);
        setPayoutSummary(null);
        getDriverLiveStats(selected.id).then(setLiveStats).catch(() => {});
        // Payout summary feeds the "pending payout" subline on the
        // Earnings card AND the dedicated Payouts tab; fetched eagerly so
        // the slideout header tells a complete story on open.
        setPayoutLoading(true);
        getDriverPayoutsSummary(selected.id)
            .then((d) => setPayoutSummary(d))
            .catch(() => {})
            .finally(() => setPayoutLoading(false));
    }, [selected?.id]);
    useEffect(() => {
        if (!previewUrl) return;
        const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setPreviewUrl(null); };
        document.addEventListener("keydown", onKey);
        return () => document.removeEventListener("keydown", onKey);
    }, [previewUrl]);

    const reloadDriverDocs = async () => {
        if (!selected?.id) return;
        try {
            const d = await getDriverDocuments(selected.id);
            setDriverDocs(Array.isArray(d) ? d : []);
        } catch (e: any) {
            toast({ title: "Could not reload documents", description: e?.message || "Unknown error", variant: "destructive" });
        }
    };

    const handleReviewDoc = async (docId: string, status: "approved" | "rejected", reason?: string, expiry?: string) => {
        setDocBusy(docId);
        const prevDocs = [...driverDocs];
        try {
            await reviewDocument(docId, status, reason, expiry ? new Date(expiry).toISOString() : undefined);
            await reloadDriverDocs();
            loadData();
            loadDrivers();
        } catch (e: any) {
            setDriverDocs(prevDocs);
            toast({ title: "Document review failed", description: e?.message || "Unknown error", variant: "destructive" });
        } finally {
            setDocBusy(null);
        }
    };

    const openReviewDialog = (docId: string, action: "approved" | "rejected") => {
        const doc = activeDocs.find(d => d.id === docId);
        const matchedReq = doc ? requiredDocs.find(rd => matchesRequirement(doc, rd)) : undefined;
        const docType = matchedReq?.label || doc?.document_type || doc?.requirement_id || "";
        setReviewingDoc({ id: docId, action, docType, requiresExpiry: matchedReq?.has_expiry || false });
        setReviewExpiry(""); setReviewReason("");
    };
    const confirmReview = async () => { if (!reviewingDoc) return; await handleReviewDoc(reviewingDoc.id, reviewingDoc.action, reviewReason || undefined, reviewExpiry || undefined); setReviewingDoc(null); };

    const dateInputValue = (v: any) => {
        if (!v) return "";
        const d = new Date(v);
        return Number.isNaN(d.getTime()) ? String(v).slice(0, 10) : d.toISOString().slice(0, 10);
    };
    const datetimeLocalValue = (v: any) => {
        if (!v) return "";
        const d = new Date(v);
        if (Number.isNaN(d.getTime())) return String(v).slice(0, 16);
        const local = new Date(d.getTime() - d.getTimezoneOffset() * 60000);
        return local.toISOString().slice(0, 16);
    };
    const boolInputValue = (v: any) => (v === true ? "true" : v === false ? "false" : "");
    const fromBoolInput = (v: any) => (v === "true" ? true : v === "false" ? false : null);
    const startEditing = () => {
        if (!selected) return;
        setEditForm({
            first_name: selected.first_name || "",
            last_name: selected.last_name || "",
            email: selected.email || "",
            phone: selected.phone || "",
            city: selected.city || "",
            service_area_id: selected.service_area_id || "",
            vehicle_type_id: selected.vehicle_type_id || "",
            vehicle_make: selected.vehicle_make || "",
            vehicle_model: selected.vehicle_model || "",
            vehicle_color: selected.vehicle_color || "",
            vehicle_year: selected.vehicle_year || "",
            license_plate: selected.license_plate || "",
            vehicle_vin: selected.vehicle_vin || "",
            date_of_birth: dateInputValue(selected.date_of_birth),
            // Starts blank on purpose: the value on `selected` is the Vault
            // ciphertext, not the licence number, so it can never be prefilled.
            // Blank is treated as "leave unchanged" in saveEdits.
            license_number: "",
            license_class: selected.license_class || "",
            regulatory_authority: selected.regulatory_authority || (selected.sgi_approved != null ? "SGI" : ""),
            regulatory_region: selected.regulatory_region || "",
            regulatory_authority_approved: boolInputValue(selected.regulatory_authority_approved ?? selected.sgi_approved),
            regulatory_authority_approved_at: datetimeLocalValue(selected.regulatory_authority_approved_at || selected.sgi_approved_at),
            // Single source of truth — is_permanent_resident / is_citizen are
            // derived from this by the backend and are no longer edited here.
            work_authorization_status: workAuth(selected).status === "unknown" ? "" : workAuth(selected).status,
            decals_sent: boolInputValue(selected.decals_sent),
            decals_sent_at: datetimeLocalValue(selected.decals_sent_at),
            decal_generated_at: datetimeLocalValue(selected.decal_generated_at),
            decal_number: selected.decal_number || "",
        });
        setEditing(true);
    };

    const saveEdits = async () => {
        if (!selected) return;
        const changes: Record<string, any> = {};
        const boolFields = new Set(["regulatory_authority_approved", "decals_sent"]);
        const dateFields = new Set(["date_of_birth"]);
        const datetimeFields = new Set(["regulatory_authority_approved_at", "decals_sent_at", "decal_generated_at"]);
        // Write-only: the driver row holds the Vault ciphertext, so there is no
        // current value to diff against. Blank means "leave unchanged".
        const writeOnlyFields = new Set(["license_number"]);
        const normalized: Record<string, any> = {};
        for (const [k, v] of Object.entries(editForm)) {
            if (writeOnlyFields.has(k)) continue;
            if (boolFields.has(k)) normalized[k] = fromBoolInput(v);
            else if (dateFields.has(k)) normalized[k] = v || null;
            else normalized[k] = v === "" ? null : v;
        }
        for (const [k, v] of Object.entries(normalized)) {
            const current = boolFields.has(k)
                ? (selected[k] ?? null)
                : dateFields.has(k)
                    ? (dateInputValue(selected[k]) || null)
                    : datetimeFields.has(k)
                        ? (datetimeLocalValue(selected[k]) || null)
                        : (selected[k] ?? null);
            if (v !== current) changes[k] = v;
        }
        for (const k of writeOnlyFields) {
            const v = String(editForm[k] ?? "").trim();
            if (v) changes[k] = v;
        }
        if (Object.keys(changes).length === 0) { setEditing(false); return; }
        setSaving(true);
        try {
            await updateDriver(selected.id, changes);
            // The backend derives is_citizen / is_permanent_resident from the
            // status, so mirror that here rather than leaving the old booleans
            // (and the consolidated projection) stale until the next refetch.
            const derived: Record<string, any> = {};
            if ("work_authorization_status" in changes) {
                const wa = workAuthLocal({ work_authorization_status: changes.work_authorization_status });
                derived.is_citizen = wa.status === "unknown" ? null : wa.status === "citizen";
                derived.is_permanent_resident = wa.status === "unknown" ? null : wa.status === "permanent_resident";
            }
            const patch = { ...changes, ...derived };
            // Never keep the plaintext licence number in client state — the row
            // otherwise stores ciphertext, and the panel only ever shows last-4.
            delete (patch as any).license_number;
            const merge = (d: any) => { const next = { ...d, ...patch }; next.work_authorization = workAuthLocal(next); return next; };
            const updated = merge(selected);
            setSelected(updated);
            setDrivers(prev => prev.map(d => d.id === selected.id ? merge(d) : d));
            if ("license_number" in changes) {
                const last4 = String(changes.license_number).slice(-4);
                setLiveStats(prev => prev ? { ...prev, license_number_last4: last4, license_number_on_file: true } : prev);
            }
            setEditing(false);
        } catch (e: any) { toast({ title: "Failed to save driver", description: e?.message || "Unknown error", variant: "destructive" }); } finally { setSaving(false); }
    };

    const [photoReviewing, setPhotoReviewing] = useState(false);
    const handlePhotoReview = async (action: "approve" | "reject") => {
        if (!selected || photoReviewing) return;
        setPhotoReviewing(true);
        try {
            const res = await reviewDriverPhoto(selected.id, action);
            const next = res.profile_image_status;
            setSelected({ ...selected, profile_image_status: next });
            setDrivers(prev => prev.map(d => d.id === selected.id ? { ...d, profile_image_status: next } : d));
            toast({ title: `Photo ${next}` });
        } catch (e: any) {
            toast({ title: "Photo review failed", description: e?.message || "Unknown error", variant: "destructive" });
        } finally {
            setPhotoReviewing(false);
        }
    };

    const photoInputRef = useRef<HTMLInputElement>(null);
    const [photoUploading, setPhotoUploading] = useState(false);
    const handlePhotoUpload = async (file: File) => {
        if (!selected || photoUploading) return;
        if (!file.type.startsWith("image/")) {
            toast({ title: "Invalid file", description: "Please choose an image (JPEG, PNG, WebP, or GIF).", variant: "destructive" });
            return;
        }
        setPhotoUploading(true);
        try {
            const res = await uploadDriverPhoto(selected.id, file);
            const next = res.profile_image_status;
            setSelected({ ...selected, profile_image_status: next, photo_url: res.profile_image });
            setLiveStats(prev => prev ? { ...prev, photo_url: res.profile_image } : prev);
            setDrivers(prev => prev.map(d => d.id === selected.id ? { ...d, profile_image_status: next } : d));
            toast({ title: "Photo uploaded", description: "The driver's profile photo was updated and approved." });
        } catch (e: any) {
            toast({ title: "Photo upload failed", description: e?.message || "Unknown error", variant: "destructive" });
        } finally {
            setPhotoUploading(false);
            if (photoInputRef.current) photoInputRef.current.value = "";
        }
    };

    const ef = (field: string) => editForm[field] ?? "";
    const setEf = (field: string, value: string) => setEditForm(prev => ({ ...prev, [field]: value }));

    // Search, status/vehicle-type filtering and sorting are all applied by the
    // backend query (see loadDrivers) so `drivers` already holds exactly the
    // rows for the current page, in the requested order. The list renders it
    // directly — no per-page client filtering/sorting, which previously only
    // ever saw the 50 rows already loaded.
    const sorted = drivers;

    const handleSort = (key: string) => { if (sortKey === key) { setSortDir(d => d === "asc" ? "desc" : "asc"); } else { setSortKey(key); setSortDir(key === "created_at" || key === "total_earnings" || key === "total_rides" || key === "rating" ? "desc" : "asc"); } };
    const SortIcon = ({ col }: { col: string }) => { if (sortKey !== col) return <ArrowUpDown className="h-3 w-3 opacity-30 inline ml-1" />; return sortDir === "asc" ? <ArrowUp className="h-3 w-3 inline ml-1" /> : <ArrowDown className="h-3 w-3 inline ml-1" />; };

    const statusCounts = (s: string) => {
        const stats = data?.stats;
        if (!stats) return 0;
        if (s === "all") return stats.total ?? 0;
        if (s === "online") return stats.online ?? 0;
        if (s === "photos_pending") return stats.pending_photos ?? 0;
        return stats[s] ?? 0;
    };
    const fmtDate = (d: string) => { if (!d) return "\u2014"; try { return new Date(d).toLocaleDateString("en-CA", { month: "short", day: "numeric", year: "numeric" }); } catch { return d; } };

    const [bulkKycRunning, setBulkKycRunning] = useState(false);
    const handleBulkKycRefresh = async () => {
        // Report-only by design: account_not_on_key drivers are counted but
        // NOT detached. Retiring in bulk stays a deliberate per-driver action
        // (the slideout button), never one click across the fleet.
        if (!window.confirm(
            "Refresh Stripe verification for ALL drivers with a Stripe account?\n\n" +
            "This reads live state from Stripe and updates each driver's row. " +
            "Nothing is detached or changed on Stripe."
        )) return;
        setBulkKycRunning(true);
        try {
            const res = await refreshAllDriverStripeKyc();
            const parts = [
                `${res.ok ?? 0} synced`,
                res.account_not_on_key ? `${res.account_not_on_key} not on this Stripe key (need re-onboarding)` : null,
                res.no_stripe_account ? `${res.no_stripe_account} without a Stripe account` : null,
                res.stripe_error ? `${res.stripe_error} failed (retry)` : null,
            ].filter(Boolean).join(" · ");
            toast({
                title: `KYC refresh: ${res.total} driver${res.total === 1 ? "" : "s"}`,
                description: parts || "No drivers with a Stripe account.",
                ...(res.stripe_error || res.account_not_on_key ? { variant: "destructive" as const } : {}),
            });
            loadDrivers(); // re-pull the table so mirrored columns show fresh state
        } catch (e: any) {
            toast({ title: "Bulk refresh failed", description: e?.message || "Unknown error", variant: "destructive" });
        } finally {
            setBulkKycRunning(false);
        }
    };

    const [bulkPayoutsRunning, setBulkPayoutsRunning] = useState(false);
    const handleBulkPayoutRefresh = async () => {
        if (!window.confirm(
            "Sync Stripe payout history for ALL drivers?\n\n" +
            "Reads every mapped driver's Stripe Transfers, bank payouts and balance " +
            "transactions and materializes anything missing. Nothing is changed on " +
            "Stripe, and re-running is safe."
        )) return;
        setBulkPayoutsRunning(true);
        try {
            const res = await refreshAllDriverStripePayouts();
            toast({
                title: res.synced ? "Stripe payouts synced" : "Synced with errors",
                description: res.message,
                ...(res.synced ? {} : { variant: "destructive" as const }),
            });
            loadDrivers();
        } catch (e: any) {
            toast({ title: "Payout sync failed", description: e?.message || "Unknown error", variant: "destructive" });
        } finally {
            setBulkPayoutsRunning(false);
        }
    };

    const [bulkTotalsRunning, setBulkTotalsRunning] = useState(false);
    const handleRecomputeStatementTotals = async () => {
        // Preview FIRST, always. This rewrites stored money figures on a
        // driver-facing audit surface, so the operator sees the exact count
        // and net movement before anything is written.
        setBulkTotalsRunning(true);
        try {
            const preview = await recomputeStatementTotals({ apply: false });
            if (preview.corrected === 0) {
                toast({
                    title: "Nothing to correct",
                    description: `${preview.scanned} statement${preview.scanned === 1 ? "" : "s"} checked — all totals already match a live recompute.`,
                });
                return;
            }
            const sample = preview.changes.slice(0, 3).map(c =>
                `${c.period_type} ${String(c.period_start).slice(0, 10)}: paid out ${c.before.payouts_total} → ${c.after.payouts_total}`
            ).join("\n");
            if (!window.confirm(
                `Rewrite stored totals for ${preview.corrected} of ${preview.scanned} statement(s)?\n\n` +
                `Net movement — earnings ${preview.delta_earnings >= 0 ? "+" : ""}${preview.delta_earnings.toFixed(2)}, ` +
                `paid out ${preview.delta_payouts >= 0 ? "+" : ""}${preview.delta_payouts.toFixed(2)}\n\n` +
                `Examples:\n${sample}\n\n` +
                "Previous figures are kept for rollback. Only the totals shown in the " +
                "statements list change — what was emailed to drivers is untouched." +
                (preview.has_more ? "\n\nMore statements remain beyond this batch — run again after this one." : "")
            )) return;

            const applied = await recomputeStatementTotals({ apply: true });
            toast({
                title: "Statement totals updated",
                description:
                    `${applied.corrected} corrected, ${applied.unchanged} already correct` +
                    (applied.has_more ? " · more remain, run again" : "") +
                    (applied.skipped.length ? ` · ${applied.skipped.length} skipped` : ""),
            });
        } catch (e: any) {
            toast({ title: "Recompute failed", description: e?.message || "Unknown error", variant: "destructive" });
        } finally {
            setBulkTotalsRunning(false);
        }
    };

    const handleExport = async () => {
        try {
            const res = await exportDrivers();
            exportToCsv("drivers", res.drivers, [
                { key: "id", label: "ID" }, { key: "driver_code", label: "Driver Code" },
                { key: "name", label: "Name" }, { key: "first_name", label: "First Name" }, { key: "last_name", label: "Last Name" },
                { key: "email", label: "Email" }, { key: "phone", label: "Phone" },
                { key: "status", label: "Status" }, { key: "is_verified", label: "Spinr Approved" },
                { key: "is_online", label: "Online" }, { key: "is_available", label: "Available" },
                { key: "service_area", label: "Service Area" }, { key: "city", label: "City" },
                { key: "regulatory_region", label: "Region" },
                { key: "vehicle_make", label: "Vehicle Make" }, { key: "vehicle_model", label: "Vehicle Model" },
                { key: "vehicle_year", label: "Vehicle Year" }, { key: "vehicle_color", label: "Vehicle Color" },
                { key: "vehicle_type", label: "Vehicle Type" }, { key: "license_plate", label: "License Plate" },
                { key: "vehicle_vin", label: "VIN (last 4)" },
                { key: "license_no", label: "License No (last 4)" }, { key: "license_class", label: "License Class" },
                { key: "rating", label: "Rating" }, { key: "total_rides", label: "Rides" },
                { key: "total_earnings", label: "Total Earnings" }, { key: "acceptance_rate", label: "Acceptance Rate" },
                { key: "license_expiry", label: "License Expiry" }, { key: "insurance_expiry", label: "Insurance Expiry" },
                { key: "vehicle_inspection_expiry", label: "Inspection Expiry" },
                { key: "background_check_expiry", label: "Background Check Expiry" },
                { key: "work_eligibility_expiry", label: "Work Eligibility Expiry" },
                { key: "regulatory_authority", label: "Regulatory Authority" },
                { key: "regulatory_authority_approved", label: "Regulator Approved" },
                { key: "regulatory_authority_approved_at", label: "Regulator Approved At" },
                { key: "sgi_approved", label: "SGI Approved" }, { key: "sgi_approved_at", label: "SGI Approved At" },
                { key: "work_authorization_status", label: "Work Authorization" },
                { key: "is_permanent_resident", label: "Permanent Resident" }, { key: "is_citizen", label: "Citizen" },
                { key: "decal_number", label: "Welcome Letter Ref #" }, { key: "decal_generated_at", label: "Welcome Letter Generated" },
                { key: "decals_sent", label: "Welcome Letter Sent" }, { key: "decals_sent_at", label: "Welcome Letter Sent At" },
                { key: "subscription_status", label: "Subscription Status" },
                { key: "subscription_plan", label: "Subscription Plan" },
                { key: "subscription_expires_at", label: "Subscription Expires" },
                { key: "joined_at", label: "Joined" }, { key: "approved_at", label: "Spinr Approved At" },
                { key: "deleted_at", label: "Account Deleted At" },
                { key: "last_status_changed_at", label: "Last Status Change" }, { key: "updated_at", label: "Updated At" },
            ]);
            toast({ title: "Export complete", description: `${res.count ?? res.drivers?.length ?? 0} drivers exported.` });
        } catch (e: any) {
            toast({ title: "Export failed", description: e?.message, variant: "destructive" });
        }
    };

    // Client-side sort for the subscription-payments table (separate sort
    // state from the main drivers list above, which uses its own sort logic).
    const { sorted: sortedSubPayments, sort: subPaymentsSort, toggle: toggleSubPaymentsSort } = useTableSort(driverSubPayments);
    const [subPage, setSubPage] = useState(0);
    const [subPageSize, setSubPageSize] = useState<number>(25);

    const selectedAreaName = serviceAreaId ? serviceAreas.find(a => a.id === serviceAreaId)?.name || "Selected Area" : "All Areas";
    const activeDocs = driverDocs.filter(d => d.status !== "superseded");
    const pendingDocsCount = activeDocs.filter(d => d.status === "pending").length;
    const selectedDriverArea = selected ? allServiceAreas.find(a => a.id === selected.service_area_id) : null;
    const requiredDocs: { id?: string; key: string; label: string; has_expiry: boolean }[] = selectedDriverArea?.required_documents || [];

    // Map service area document key to driver profile legacy expiry field
    function _docKeyToExpiryField(key: string): string | null {
        const k = key.toLowerCase();
        if (k.includes("license") || k.includes("driving") || k.includes("permit")) return "license_expiry_date";
        if (k.includes("insurance")) return "insurance_expiry_date";
        if (k.includes("inspection")) return "vehicle_inspection_expiry_date";
        if (k.includes("background")) return "background_check_expiry_date";
        if (k.includes("work") || k.includes("eligibility")) return "work_eligibility_expiry_date";
        return null;
    }

    // Summarise a required document's state for the per-doc expiry cards.
    // Resolves the matching driver_documents row(s), picks the highest-
    // priority status (approved > pending > rejected > missing), and falls
    // back to the legacy drivers.*_expiry_date column when the doc row has
    // no expiry of its own (older approvals stored the date only on the
    // drivers row).
    function _getDocSummary(rdId: string | undefined, rdKey: string, rdLabel: string): {
        expiry?: string;
        docStatus: "approved" | "pending" | "rejected" | "missing";
        expiryIsLegacy: boolean;
    } {
        const matches = activeDocs.filter(d => matchesRequirement(d, { id: rdId, key: rdKey, label: rdLabel }));
        const legacyField = _docKeyToExpiryField(rdKey);
        const legacyExpiry: string | undefined = legacyField ? selected?.[legacyField] : undefined;

        const approved = matches.find(d => d.status === "approved");
        if (approved) {
            const docExpiry = approved.expiry_date || approved.expires_at;
            if (docExpiry) return { expiry: docExpiry, docStatus: "approved", expiryIsLegacy: false };
            return { expiry: legacyExpiry, docStatus: "approved", expiryIsLegacy: !!legacyExpiry };
        }
        const pending = matches.find(d => d.status === "pending");
        if (pending) return { expiry: undefined, docStatus: "pending", expiryIsLegacy: false };
        const rejected = matches.find(d => d.status === "rejected");
        if (rejected) return { expiry: undefined, docStatus: "rejected", expiryIsLegacy: false };
        // No driver_documents row at all — but onboarding may have stamped
        // a legacy expiry on the drivers row, in which case treat the
        // requirement as approved from that path.
        if (legacyExpiry) return { expiry: legacyExpiry, docStatus: "approved", expiryIsLegacy: true };
        return { expiry: undefined, docStatus: "missing", expiryIsLegacy: false };
    }

    function _getDocExpiry(rdId: string | undefined, rdKey: string, rdLabel: string): string | undefined {
        return _getDocSummary(rdId, rdKey, rdLabel).expiry;
    }

    // Existing on-file date/status per document type, surfaced in the upload
    // dialog so the admin can see what's already recorded before re-uploading.
    // Plain (non-memoized) map — requiredDocs is small and this must stay above
    // the `if (!allowed) return null` early-return without adding a hook.
    const existingDocInfo: Record<string, { expiry?: string; status: "approved" | "pending" | "rejected" | "missing" }> =
        Object.fromEntries(
            requiredDocs.map((rd) => {
                const s = _getDocSummary(rd.id, rd.key, rd.label);
                return [rd.key, { expiry: s.expiry, status: s.docStatus }];
            }),
        );

    if (!allowed) return null;

    return (
        <div className="space-y-5">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
                <div>
                    <h1 className="text-2xl font-bold">Drivers</h1>
                    <p className="text-sm text-muted-foreground">{data?.stats?.total ?? 0} drivers {serviceAreaId ? `in ${selectedAreaName}` : "overall"}</p>
                </div>
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
                        <CalendarRange className="h-4 w-4 text-muted-foreground" />
                        <Input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} className="h-9 w-[140px] text-xs" aria-label="Filter from date" />
                        <span className="text-xs text-muted-foreground">to</span>
                        <Input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} className="h-9 w-[140px] text-xs" aria-label="Filter to date" />
                    </div>
                    {(serviceAreaId || vehicleTypeFilter || startDate || endDate) && <Button variant="ghost" size="sm" onClick={() => { setServiceAreaId(""); setVehicleTypeFilter(""); setStartDate(""); setEndDate(""); }}><X className="h-3.5 w-3.5" /> Clear</Button>}
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
            </div>

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
                                    <TableCell colSpan={11} className="text-center py-20 text-muted-foreground"><Users className="h-12 w-12 mx-auto mb-3 opacity-20" /><p className="text-base font-medium">No drivers found</p><p className="text-sm mt-1">Try adjusting your search or filters</p></TableCell>
                                </TableRow>
                            ) : sorted.map(driver => {
                                const areaName = serviceAreas.find(a => a.id === driver.service_area_id)?.name;
                                return (
                                    <TableRow key={driver.id} className={`group cursor-pointer transition-colors hover:bg-muted/40 ${selected?.id === driver.id ? "bg-primary/5 hover:bg-primary/5" : ""}`} onClick={() => setSelected(driver)} tabIndex={0} aria-label={`${driver.first_name} ${driver.last_name}, ${driver.status}, ${driver.is_online ? "online" : "offline"}`} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelected(driver); } }}>
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
                                                    <span className={`absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-card ${driver.is_online ? "bg-emerald-500" : "bg-gray-300"}`} />
                                                </div>
                                                <div className="flex-1 min-w-0">
                                                    <p className="text-sm font-semibold truncate">{driver.first_name} {driver.last_name}</p>
                                                    {driver.driver_code && <p className="text-[11px] font-mono text-muted-foreground truncate">{driver.driver_code}</p>}
                                                    {driver.email && <p className="text-[11px] text-muted-foreground truncate">{showPii ? driver.email : maskEmail(driver.email)}</p>}
                                                    {driver.phone && <p className="text-[11px] text-muted-foreground flex items-center gap-1 mt-0.5"><Phone className="h-2.5 w-2.5" /> {showPii ? driver.phone : maskPhone(driver.phone)}</p>}
                                                </div>
                                            </div>
                                        </TableCell>
                                        <TableCell>
                                            <div className="flex flex-col gap-1.5 items-start">
                                                {/* account_deleted wins over status: deletion cannot change
                                                    drivers.status, so a departed driver still carries "active". */}
                                                {driver.account_deleted ? <Badge variant="default" className="bg-zinc-200 text-zinc-700 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 text-[10px] px-1.5 py-0 border-zinc-300 dark:border-zinc-700"><Trash2 className="h-3 w-3 mr-1" />Deleted</Badge>
                                                : driver.status === "active" ? <Badge variant="default" className="bg-emerald-100 text-emerald-700 hover:bg-emerald-100 dark:bg-emerald-900/30 dark:text-emerald-400 text-[10px] px-1.5 py-0 border-emerald-200 dark:border-emerald-800"><ShieldCheck className="h-3 w-3 mr-1" />Active</Badge>
                                                : driver.status === "needs_review" ? <Badge variant="default" className="bg-amber-100 text-amber-700 hover:bg-amber-100 dark:bg-amber-900/30 dark:text-amber-400 text-[10px] px-1.5 py-0 border-amber-200 dark:border-amber-800"><AlertTriangle className="h-3 w-3 mr-1" />Needs Review</Badge>
                                                : driver.status === "suspended" ? <Badge variant="default" className="bg-orange-100 text-orange-700 hover:bg-orange-100 dark:bg-orange-900/30 dark:text-orange-400 text-[10px] px-1.5 py-0 border-orange-200 dark:border-orange-800"><Pause className="h-3 w-3 mr-1" />Suspended</Badge>
                                                : driver.status === "banned" ? <Badge variant="default" className="bg-red-200 text-red-800 hover:bg-red-200 dark:bg-red-900/40 dark:text-red-400 text-[10px] px-1.5 py-0 border-red-300 dark:border-red-800"><Ban className="h-3 w-3 mr-1" />Banned</Badge>
                                                : <Badge variant="default" className="bg-blue-100 text-blue-700 hover:bg-blue-100 dark:bg-blue-900/30 dark:text-blue-400 text-[10px] px-1.5 py-0 border-blue-200 dark:border-blue-800"><ShieldAlert className="h-3 w-3 mr-1" />Pending</Badge>}
                                                <Badge variant="outline" className={`text-[10px] px-1.5 py-0 ${driver.is_online && !driver.account_deleted ? "border-emerald-300 text-emerald-600 bg-emerald-50 dark:bg-emerald-500/10" : ""}`}>{driver.is_online && !driver.account_deleted ? "Online" : "Offline"}</Badge>
                                            </div>
                                        </TableCell>
                                        <TableCell>
                                            <div className="flex flex-col gap-0.5 items-start">
                                                <Badge variant={driver.is_online ? "default" : "outline"} className={driver.is_online ? "bg-emerald-100 text-emerald-700 hover:bg-emerald-100 dark:bg-emerald-900/30 dark:text-emerald-400 text-[10px] px-1.5 py-0 border-emerald-200 dark:border-emerald-800" : "text-[10px] px-1.5 py-0 text-muted-foreground"}>
                                                    <span className={`h-1.5 w-1.5 rounded-full mr-1 ${driver.is_online ? "bg-emerald-500" : "bg-gray-400"}`} />
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
                                                {vehicleTypes.find(v => v.id === driver.vehicle_type_id)?.name || <span className="text-muted-foreground/60 italic">—</span>}
                                            </span>
                                        </TableCell>
                                        <TableCell>
                                            <div className="flex flex-col gap-1 text-xs">
                                                <div className="flex items-center gap-1.5 text-muted-foreground font-medium">
                                                    <Car className="h-3.5 w-3.5" />
                                                    <span className="truncate max-w-[120px]">{[driver.vehicle_color, driver.vehicle_make, driver.vehicle_model].filter(Boolean).join(" ") || "No vehicle"}</span>
                                                </div>
                                                {driver.license_plate ? <span className="font-mono font-bold text-foreground/80 tracking-wider bg-muted px-1.5 py-0.5 rounded text-[10px] border shadow-sm self-start">{showPii ? driver.license_plate : maskPlate(driver.license_plate)}</span> : <span className="text-[10px] text-muted-foreground/60 italic">No plate</span>}
                                            </div>
                                        </TableCell>
                                        <TableCell className="text-center">
                                            <span className="text-xs font-bold flex items-center justify-center gap-1"><Star className="h-3 w-3 text-amber-500 fill-amber-500" />{driver.rating?.toFixed(1) || "\u2014"}</span>
                                        </TableCell>
                                        <TableCell className="text-center">
                                            <span className="text-xs font-bold">{(driver.total_rides || 0).toLocaleString()}</span>
                                        </TableCell>
                                        <TableCell className="text-right">
                                            <span className="text-xs font-bold text-emerald-600 dark:text-emerald-400">{formatCurrency(driver.total_earnings || 0)}</span>
                                        </TableCell>
                                        <TableCell>
                                            <div className="flex items-center gap-1.5 text-xs text-foreground font-medium truncate max-w-[120px]"><MapPin className="h-3.5 w-3.5 text-blue-500 shrink-0" />{areaName || "Unassigned"}</div>
                                        </TableCell>
                                        <TableCell className="pr-5">
                                            <div className="flex items-center gap-1.5 text-xs text-muted-foreground"><Clock className="h-3 w-3 shrink-0" />{fmtDate(driver.created_at)}</div>
                                        </TableCell>
                                    </TableRow>
                                );
                            })}
                        </TableBody>
                    </Table>
                </div>

                <Pagination
                    page={page}
                    pageSize={PAGE_SIZE}
                    hasNextPage={hasNextPage}
                    onPageChange={setPage}
                />
            </div>

            {!serviceAreaId && <AreaStatsTable areaStats={data?.area_stats || []} loading={loading} onAreaClick={(areaId) => setServiceAreaId(areaId)} />}

            <DriverCharts charts={data?.charts || null} loading={loading} />

            {/* Driver Detail Slideout */}
            <Sheet open={!!selected} onOpenChange={(open) => { if (!open) { setSelected(null); setEditing(false); } }}>
                <SheetContent side="right" showCloseButton={false} className="w-full sm:max-w-none sm:w-[90vw] lg:w-[80vw] xl:w-[70vw] p-0 overflow-hidden flex flex-col" aria-describedby={undefined}>
                    <SheetTitle className="sr-only">Driver Details</SheetTitle>
                    <SheetDescription className="sr-only">View and edit driver information</SheetDescription>
                    {selected && (<>
                        <div className="border-b bg-gradient-to-r from-primary/5 to-transparent">
                            <div className="p-6">
                                <div className="flex items-start justify-between">
                                    <div className="flex items-center gap-4">
                                        <div className="relative">
                                            {/* Photo comes from live-stats (loaded on open) — the drivers
                                                list no longer ships profile_image. Falls back to selected
                                                in case an older list payload still carries it. */}
                                            {(liveStats?.photo_url || selected.photo_url) ? (
                                                // eslint-disable-next-line @next/next/no-img-element
                                                <img src={liveStats?.photo_url || selected.photo_url} alt="" className="w-16 h-16 rounded-2xl object-cover" />
                                            ) : (
                                                <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-primary/20 to-primary/5 flex items-center justify-center text-xl font-bold text-primary">{(selected.first_name?.[0] || "")}{(selected.last_name?.[0] || "")}</div>
                                            )}
                                            <span className={`absolute -bottom-1 -right-1 w-4 h-4 rounded-full border-2 border-background ${selected.is_online ? "bg-emerald-500" : "bg-gray-300"}`} />
                                            <button
                                                type="button"
                                                title="Upload / change profile photo"
                                                disabled={photoUploading}
                                                onClick={() => photoInputRef.current?.click()}
                                                className="absolute -top-1 -left-1 w-6 h-6 rounded-full bg-primary text-primary-foreground flex items-center justify-center shadow border-2 border-background disabled:opacity-50"
                                            >
                                                {photoUploading ? <Loader2 className="h-3 w-3 animate-spin" /> : <Upload className="h-3 w-3" />}
                                            </button>
                                            <input
                                                ref={photoInputRef}
                                                type="file"
                                                accept="image/jpeg,image/png,image/webp,image/gif"
                                                className="hidden"
                                                onChange={(e) => { const f = e.target.files?.[0]; if (f) void handlePhotoUpload(f); }}
                                            />
                                        </div>
                                        <div>
                                            <h2 className="text-xl font-bold">{selected.first_name} {selected.last_name}</h2>
                                            <div className="flex items-center gap-2 mt-1 flex-wrap">
                                                {selected.driver_code && (
                                                    <button onClick={() => navigator.clipboard.writeText(selected.driver_code)} className="flex items-center gap-1 text-xs font-mono font-semibold text-primary bg-primary/10 px-2 py-0.5 rounded" title="Copy driver code">{selected.driver_code}<Copy className="h-3 w-3" /></button>
                                                )}
                                                <button onClick={() => navigator.clipboard.writeText(selected.id)} className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition font-mono bg-muted/50 px-2 py-0.5 rounded" title="Copy driver UUID">{selected.id?.slice(0, 12)}…<Copy className="h-3 w-3" /></button>
                                                {selected.email && (
                                                    <button onClick={() => navigator.clipboard.writeText(selected.email)} className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition px-2 py-0.5 rounded hover:bg-muted/50" title={showPii ? `Copy email: ${selected.email}` : "Reveal PII to copy"}>
                                                        <Mail className="h-3 w-3" />
                                                        <span className="truncate max-w-[220px]">{showPii ? selected.email : maskEmail(selected.email)}</span>
                                                    </button>
                                                )}
                                            </div>
                                            {selected.profile_image_status === "pending_review" && (
                                                <div className="flex items-center gap-2 mt-2 p-2 rounded-lg bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800">
                                                    {(liveStats?.photo_url || selected.photo_url) && (
                                                        // eslint-disable-next-line @next/next/no-img-element
                                                        <img src={liveStats?.photo_url || selected.photo_url} alt="" className="w-9 h-9 rounded-full object-cover" />
                                                    )}
                                                    <span className="text-xs text-amber-700 dark:text-amber-400 flex-1">Profile photo pending review</span>
                                                    <button disabled={photoReviewing} onClick={() => handlePhotoReview("approve")} className="text-xs font-semibold px-2 py-1 rounded bg-emerald-600 text-white disabled:opacity-50">Approve</button>
                                                    <button disabled={photoReviewing} onClick={() => handlePhotoReview("reject")} className="text-xs font-semibold px-2 py-1 rounded bg-red-600 text-white disabled:opacity-50">Reject</button>
                                                </div>
                                            )}
                                            {selected.profile_image_status === "rejected" && (
                                                <div className="mt-2 text-xs text-red-600 dark:text-red-400">Profile photo rejected — driver must re-upload.</div>
                                            )}
                                            <div className="flex items-center gap-2 mt-2">
                                                {selected.account_deleted ? <Badge className="bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"><Trash2 className="h-3 w-3" /> Deleted</Badge>
                                                : selected.status === "active" ? <Badge className="bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"><ShieldCheck className="h-3 w-3" /> Active</Badge>
                                                : selected.status === "needs_review" ? <Badge className="bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"><AlertTriangle className="h-3 w-3" /> Needs Review</Badge>
                                                : selected.status === "suspended" ? <Badge className="bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400"><Pause className="h-3 w-3" /> Suspended</Badge>
                                                : selected.status === "banned" ? <Badge className="bg-red-200 text-red-800 dark:bg-red-900/40 dark:text-red-400"><Ban className="h-3 w-3" /> Banned</Badge>
                                                : <Badge className="bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400"><ShieldAlert className="h-3 w-3" /> Pending</Badge>}
                                                <Badge variant="outline" className={selected.is_online && !selected.account_deleted ? "border-emerald-300 text-emerald-600" : ""}>
                                                    {selected.is_online && !selected.account_deleted ? "Online" : "Offline"}
                                                    {selected.last_status_changed_at && (
                                                        <span className="ml-1.5 text-[10px] opacity-70">
                                                            since {new Date(selected.last_status_changed_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}
                                                        </span>
                                                    )}
                                                </Badge>
                                                {selected.subscription_status === "active" && <Badge className="bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-400"><CreditCard className="h-3 w-3" /> Spinr Pass</Badge>}
                                                {selected.subscription_status === "expired" && <Badge className="bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"><CreditCard className="h-3 w-3" /> Pass Expired</Badge>}
                                            </div>
                                        </div>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        {!editing ? <Button variant="outline" size="sm" onClick={startEditing}><Pencil className="h-3.5 w-3.5" /> Edit</Button> : (<>
                                            <Button variant="ghost" size="sm" onClick={() => setEditing(false)} disabled={saving}>Cancel</Button>
                                            <Button size="sm" onClick={saveEdits} disabled={saving} className="bg-emerald-600 hover:bg-emerald-700 text-white">{saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />} Save</Button>
                                        </>)}
                                        <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={() => { setSelected(null); setEditing(false); }}><X className="h-4 w-4" /></Button>
                                    </div>
                                </div>
                                <div className="grid grid-cols-4 gap-3 mt-5">
                                    {/* QuickStats prefer live-stats computed on the backend over
                                        the denormalised drivers.* columns, which were stale or
                                        unset for three of the four metrics. While live-stats are
                                        in flight we show a "\u2026" placeholder so the user sees that
                                        the value is loading instead of a stale 0. */}
                                    <QuickStat
                                        icon={Star} color="text-amber-500" bg="bg-amber-50 dark:bg-amber-900/20"
                                        label="Rating"
                                        value={
                                            liveStats === null
                                                ? "\u2026"
                                                : liveStats.avg_rating != null
                                                    ? liveStats.avg_rating.toFixed(1)
                                                    : selected.rating != null && selected.rating > 0
                                                        ? Number(selected.rating).toFixed(1)
                                                        : "New"
                                        }
                                    />
                                    <QuickStat
                                        icon={Car} color="text-blue-500" bg="bg-blue-50 dark:bg-blue-900/20"
                                        label="Rides"
                                        value={
                                            liveStats === null
                                                ? "\u2026"
                                                : (liveStats.total_rides || 0).toLocaleString()
                                        }
                                    />
                                    <QuickStat
                                        icon={DollarSign} color="text-emerald-500" bg="bg-emerald-50 dark:bg-emerald-900/20"
                                        label="Earnings"
                                        value={
                                            liveStats === null
                                                ? "\u2026"
                                                : formatCurrency(liveStats.total_earnings || 0)
                                        }
                                        sub={
                                            payoutSummary
                                                ? payoutSummary.summary.pending_balance > 0
                                                    ? `${formatCurrency(payoutSummary.summary.pending_balance)} pending`
                                                    : payoutSummary.summary.pending_in_flight > 0
                                                        ? `${formatCurrency(payoutSummary.summary.pending_in_flight)} in flight`
                                                        : "All paid out"
                                                : undefined
                                        }
                                        subTone={
                                            payoutSummary && payoutSummary.summary.pending_balance > 0 ? "amber" : "muted"
                                        }
                                    />
                                    <QuickStat
                                        icon={CheckCircle} color="text-violet-500" bg="bg-violet-50 dark:bg-violet-900/20"
                                        label="Accept Rate"
                                        value={
                                            liveStats === null
                                                ? "\u2026"
                                                : liveStats.acceptance_rate != null
                                                    ? `${liveStats.acceptance_rate}%`
                                                    : "\u2014"
                                        }
                                    />
                                </div>
                            </div>
                        </div>

                        <Tabs value={detailTab} onValueChange={(v) => { setDetailTab(v); if (v === "rides") loadDriverRides(selected.id); if (v === "referrals") loadDriverReferrals(selected.id); if (v === "subscriptions") loadDriverSubscriptions(selected.id); if (v === "training") loadDriverTraining(selected.id); }} className="flex-1 overflow-hidden flex flex-col">
                            <TabsList className="mx-6 mt-4 w-fit">
                                <TabsTrigger value="overview">Overview</TabsTrigger>
                                <TabsTrigger value="documents">Documents{pendingDocsCount > 0 && <span className="ml-1.5 bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 text-[10px] font-bold px-1.5 py-0.5 rounded-full" title={`${pendingDocsCount} document${pendingDocsCount === 1 ? "" : "s"} awaiting review`}>{pendingDocsCount}</span>}</TabsTrigger>
                                <TabsTrigger value="rides">Rides{selected.total_rides > 0 && <span className="ml-1.5 bg-primary/10 text-primary text-[10px] font-bold px-1.5 py-0.5 rounded-full">{(selected.total_rides || 0).toLocaleString()}</span>}</TabsTrigger>
                                <TabsTrigger value="payouts">Payouts{payoutSummary && payoutSummary.summary.pending_balance > 0 && <span className="ml-1.5 bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 text-[10px] font-bold px-1.5 py-0.5 rounded-full" title={`${formatCurrency(payoutSummary.summary.pending_balance)} pending payout`}>!</span>}</TabsTrigger>
                                <TabsTrigger value="referrals">Referrals</TabsTrigger>
                                <TabsTrigger value="training">Training</TabsTrigger>
                                <TabsTrigger value="subscriptions">Subscriptions</TabsTrigger>
                                <TabsTrigger value="verification">Actions</TabsTrigger>
                                <TabsTrigger value="notes">Notes</TabsTrigger>
                                <TabsTrigger value="history">History</TabsTrigger>
                            </TabsList>
                            <div className="flex-1 overflow-y-auto px-6 pb-6">
                                {/* Overview */}
                                <TabsContent value="overview" className="mt-4 space-y-5">
                                    <DetailSection title="Performance" icon={Star}>
                                        {liveStats ? (
                                            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5">
                                                <DetailField icon={Car} label="Total Assigned" value={(liveStats.total_assigned ?? 0).toLocaleString()} />
                                                <DetailField icon={CheckCircle} label="Completed" value={(liveStats.total_rides ?? 0).toLocaleString()} />
                                                <DetailField icon={ShieldCheck} label="Acceptance Rate" value={liveStats.acceptance_rate != null ? `${liveStats.acceptance_rate}%` : "—"} />
                                                <DetailField icon={XCircle} label="Cancelled (driver)" value={(liveStats.cancelled_by_driver ?? 0).toLocaleString()} />
                                                <DetailField icon={Star} label="Avg Rating" value={liveStats.avg_rating != null ? liveStats.avg_rating.toFixed(2) : "—"} />
                                                <DetailField icon={DollarSign} label="Avg / Ride" value={liveStats.total_rides > 0 ? formatCurrency(liveStats.total_earnings / liveStats.total_rides) : "—"} />
                                            </div>
                                        ) : (
                                            <p className="text-xs text-muted-foreground">Loading stats…</p>
                                        )}
                                    </DetailSection>
                                    <DetailSection title="Contact Information" icon={Mail}>
                                        {editing ? (
                                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                                                <EditField label="First Name" value={ef("first_name")} onChange={v => setEf("first_name", v)} />
                                                <EditField label="Last Name" value={ef("last_name")} onChange={v => setEf("last_name", v)} />
                                                <EditField label="Email" value={ef("email")} onChange={v => setEf("email", v)} type="email" />
                                                <EditField label="Phone" value={ef("phone")} onChange={v => setEf("phone", v)} type="tel" />
                                                <EditField label="City" value={ef("city")} onChange={v => setEf("city", v)} />
                                                <div><label className="text-[11px] text-muted-foreground mb-1 block">Service Area</label><Select value={ef("service_area_id") || "none"} onValueChange={v => setEf("service_area_id", v === "none" ? "" : v)}><SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="none">Not assigned</SelectItem>{allServiceAreas.map(a => <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>)}</SelectContent></Select></div>
                                            </div>
                                        ) : (
                                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                                                <CopyableField icon={Mail} label="Email" value={showPii ? selected.email : maskEmail(selected.email)} />
                                                <CopyableField icon={Phone} label="Phone" value={showPii ? selected.phone : maskPhone(selected.phone)} />
                                                <DetailField icon={MapPin} label="City" value={selected.city || "\u2014"} />
                                                <DetailField icon={MapPin} label="Service Area" value={serviceAreas.find(a => a.id === selected.service_area_id)?.name || selected.service_area_id?.slice(0, 8) || "Not assigned"} />
                                            </div>
                                        )}
                                    </DetailSection>
                                    <DetailSection title="Vehicle Information" icon={Car}>
                                        {editing ? (() => {
                                            // Narrow vehicle-type options to the types with
                                            // active fare_configs for the currently-selected
                                            // service area — same convention the monitoring
                                            // filter uses. No area selected → show every
                                            // active type.
                                            const areaId = ef("service_area_id");
                                            const allowed = areaId ? vehicleTypesByArea[areaId] : null;
                                            // The dropdown is NEVER disabled — admins must
                                            // be able to recover any driver from a bad
                                            // state (vehicle type deleted, area
                                            // mis-configured, etc.). When the area has no
                                            // active fare_configs we still show every
                                            // catalogue type and warn inline so the
                                            // operator knows configs are missing.
                                            const areaHasConfigs = !!allowed && allowed.size > 0;
                                            const availableTypes = areaHasConfigs
                                                ? vehicleTypes.filter(v => allowed!.has(v.id))
                                                : vehicleTypes;
                                            const currentTypeId = ef("vehicle_type_id");
                                            const currentInList = availableTypes.some(v => v.id === currentTypeId);
                                            return (
                                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                                                    <div>
                                                        <label className="text-[11px] text-muted-foreground mb-1 block">Vehicle Type</label>
                                                        <Select
                                                            value={currentTypeId || "none"}
                                                            onValueChange={v => setEf("vehicle_type_id", v === "none" ? "" : v)}
                                                        >
                                                            <SelectTrigger className="h-9 text-sm">
                                                                <SelectValue placeholder="Select vehicle type" />
                                                            </SelectTrigger>
                                                            <SelectContent>
                                                                <SelectItem value="none">Not assigned</SelectItem>
                                                                {availableTypes.map(v => (
                                                                    <SelectItem key={v.id} value={v.id}>{v.name}</SelectItem>
                                                                ))}
                                                                {currentTypeId && !currentInList && (
                                                                    <SelectItem value={currentTypeId}>
                                                                        {vehicleTypes.find(v => v.id === currentTypeId)?.name || currentTypeId.slice(0, 8)} (deleted)
                                                                    </SelectItem>
                                                                )}
                                                            </SelectContent>
                                                        </Select>
                                                        {areaId && !areaHasConfigs && (
                                                            <p className="text-[10px] text-amber-600 mt-1">
                                                                No fare configs for this area — set them up in Service Areas → Vehicle Pricing.
                                                            </p>
                                                        )}
                                                    </div>
                                                    <EditField label="Make" value={ef("vehicle_make")} onChange={v => setEf("vehicle_make", v)} />
                                                    <EditField label="Model" value={ef("vehicle_model")} onChange={v => setEf("vehicle_model", v)} />
                                                    <EditField label="Color" value={ef("vehicle_color")} onChange={v => setEf("vehicle_color", v)} />
                                                    <EditField label="Year" value={ef("vehicle_year")} onChange={v => setEf("vehicle_year", v)} />
                                                    <EditField label="License Plate" value={ef("license_plate")} onChange={v => setEf("license_plate", v)} />
                                                    <EditField label="VIN" value={ef("vehicle_vin")} onChange={v => setEf("vehicle_vin", v)} />
                                                </div>
                                            );
                                        })() : (
                                            <>
                                                <div className="grid grid-cols-2 gap-2.5">
                                                    <DetailField icon={Car} label="Vehicle Type" value={vehicleTypes.find(v => v.id === selected.vehicle_type_id)?.name || (selected.vehicle_type_id ? selected.vehicle_type_id.slice(0, 8) : "Not assigned")} />
                                                    <DetailField icon={CalendarRange} label="Year" value={selected.vehicle_year ? String(selected.vehicle_year) : "\u2014"} />
                                                    <DetailField icon={Car} label="Make" value={selected.vehicle_make || "\u2014"} />
                                                    <DetailField icon={Car} label="Model" value={selected.vehicle_model || "\u2014"} />
                                                    <DetailField icon={Car} label="Color" value={selected.vehicle_color || "\u2014"} />
                                                    <DetailField icon={FileText} label="License Plate" value={showPii ? (selected.license_plate || "\u2014") : maskPlate(selected.license_plate)} mono />
                                                </div>
                                                <div className="mt-2.5">
                                                    <DetailField icon={FileText} label="VIN" value={showPii ? (selected.vehicle_vin || "\u2014") : maskVin(selected.vehicle_vin)} mono />
                                                </div>
                                            </>
                                        )}
                                    </DetailSection>
                                    <DetailSection title="Compliance & Import Data" icon={ShieldCheck}>
                                        {editing ? (
                                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                                                <EditField label="Date of Birth" value={ef("date_of_birth")} onChange={v => setEf("date_of_birth", v)} type="date" />
                                                <EditField
                                                    label="License Number"
                                                    value={ef("license_number")}
                                                    onChange={v => setEf("license_number", v)}
                                                    placeholder={liveStats?.license_number_last4 ? `•••• ${liveStats.license_number_last4}` : "Not on file"}
                                                    hint="Leave blank to keep the number on file. Stored encrypted; only the last 4 are ever displayed."
                                                />
                                                <EditField label="License Class" value={ef("license_class")} onChange={v => setEf("license_class", v)} />
                                                <EditField label="Regulatory Authority" value={ef("regulatory_authority")} onChange={v => setEf("regulatory_authority", v)} />
                                                <EditField label="Regulatory Region" value={ef("regulatory_region")} onChange={v => setEf("regulatory_region", v)} />
                                                <EditBooleanField label="Authority Approved" value={ef("regulatory_authority_approved")} onChange={v => setEf("regulatory_authority_approved", v)} />
                                                <EditField label="Authority Approved At" value={ef("regulatory_authority_approved_at")} onChange={v => setEf("regulatory_authority_approved_at", v)} type="datetime-local" />
                                                <div className="sm:col-span-2">
                                                    <label className="text-[11px] text-muted-foreground mb-1 block">Work Authorization</label>
                                                    <Select value={ef("work_authorization_status") || "unknown"} onValueChange={v => setEf("work_authorization_status", v === "unknown" ? "" : v)}>
                                                        <SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger>
                                                        <SelectContent>
                                                            {Object.entries(WORK_AUTH_LABELS).map(([value, label]) => (
                                                                <SelectItem key={value} value={value}>{label}</SelectItem>
                                                            ))}
                                                        </SelectContent>
                                                    </Select>
                                                    <p className="text-[10px] text-muted-foreground mt-1">
                                                        Citizen / permanent resident / work permit are mutually exclusive — picking one marks the others not applicable.
                                                    </p>
                                                </div>
                                                <EditField label="Welcome Letter Ref #" value={ef("decal_number")} onChange={v => setEf("decal_number", v)} />
                                                <EditField label="Welcome Letter Generated" value={ef("decal_generated_at")} onChange={v => setEf("decal_generated_at", v)} type="datetime-local" />
                                                <EditBooleanField label="Welcome Letter Sent" value={ef("decals_sent")} onChange={v => setEf("decals_sent", v)} />
                                                <EditField label="Welcome Letter Sent At" value={ef("decals_sent_at")} onChange={v => setEf("decals_sent_at", v)} type="datetime-local" />
                                            </div>
                                        ) : (
                                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                                                <DetailField icon={CalendarRange} label="Date of Birth" value={selected.date_of_birth ? fmtDate(selected.date_of_birth) : "—"} />
                                                <DetailField
                                                    icon={FileText}
                                                    label="License Number"
                                                    value={liveStats?.license_number_last4 ? `•••• ${liveStats.license_number_last4}` : liveStats?.license_number_on_file ? "On file (unreadable)" : "—"}
                                                    mono
                                                />
                                                <DetailField
                                                    icon={ShieldCheck}
                                                    label="SIN (T4A)"
                                                    // Masked always. The full number comes only from the
                                                    // audited super_admin reveal, never from this panel.
                                                    // "Missing" is called out because a driver without one
                                                    // cannot be filed for at year end.
                                                    value={liveStats?.sin_last4 ? `•••• ${liveStats.sin_last4}` : liveStats?.sin_on_file ? "On file" : "Missing — cannot file T4A"}
                                                    mono
                                                />
                                                <DetailField icon={FileText} label="License Class" value={selected.license_class || "—"} />
                                                <DetailField icon={ShieldCheck} label="Regulatory Authority" value={selected.regulatory_authority || (selected.sgi_approved != null ? "SGI" : "—")} />
                                                <DetailField icon={MapPin} label="Regulatory Region" value={selected.regulatory_region || "—"} />
                                                <DetailField icon={ShieldCheck} label="Authority Approved" value={(selected.regulatory_authority_approved ?? selected.sgi_approved) === true ? "Yes" : (selected.regulatory_authority_approved ?? selected.sgi_approved) === false ? "No" : "Unknown"} />
                                                <DetailField icon={Clock} label="Authority Approved At" value={(selected.regulatory_authority_approved_at || selected.sgi_approved_at) ? new Date(selected.regulatory_authority_approved_at || selected.sgi_approved_at).toLocaleString("en-CA") : "—"} />
                                                <DetailField icon={ShieldCheck} label="Spinr Approved" value={selected.is_verified === true ? "Yes" : selected.is_verified === false ? "No" : "Unknown"} />
                                                <DetailField icon={Clock} label="Spinr Approved At" value={selected.verified_at ? new Date(selected.verified_at).toLocaleString("en-CA") : "—"} />
                                                <DetailField
                                                    icon={FileText}
                                                    label="Work Authorization"
                                                    value={workAuth(selected).expires_at
                                                        ? `${workAuth(selected).label} ${fmtDate(workAuth(selected).expires_at!)}`
                                                        : workAuth(selected).label}
                                                />
                                                <DetailField icon={Shield} label="Permanent Resident" value={WORK_AUTH_FLAG_LABELS[workAuth(selected).permanent_resident] || "Unknown"} />
                                                <DetailField icon={Shield} label="Citizen" value={WORK_AUTH_FLAG_LABELS[workAuth(selected).citizen] || "Unknown"} />
                                                <DetailField icon={FileText} label="Welcome Letter Ref #" value={selected.decal_number || "—"} mono />
                                                <DetailField icon={Clock} label="Welcome Letter Generated" value={selected.decal_generated_at ? new Date(selected.decal_generated_at).toLocaleString("en-CA") : "—"} />
                                                <DetailField icon={CheckCircle} label="Welcome Letter Sent" value={selected.decals_sent === true ? "Yes" : selected.decals_sent === false ? "No" : "Unknown"} />
                                                <DetailField icon={Clock} label="Welcome Letter Sent At" value={selected.decals_sent_at ? new Date(selected.decals_sent_at).toLocaleString("en-CA") : "—"} />
                                            </div>
                                        )}
                                    </DetailSection>
                                    <DetailSection title="Vehicle Change History" icon={FileText}>
                                        {vehicleHistory.length === 0 ? (
                                            <p className="text-xs text-muted-foreground">No vehicle changes recorded.</p>
                                        ) : (
                                            <div className="space-y-2">
                                                {vehicleHistory.slice(0, 20).map((h) => (
                                                    <div key={h.id} className="text-xs flex items-start gap-2">
                                                        <span className="text-muted-foreground whitespace-nowrap">{new Date(h.created_at).toLocaleDateString()}</span>
                                                        <span className="flex-1">
                                                            <span className="font-medium">{h.field.replace(/_/g, " ")}</span>:{" "}
                                                            <span className="line-through text-muted-foreground">{h.old_value || "—"}</span>{" → "}
                                                            <span className="font-semibold">{h.new_value || "—"}</span>
                                                            <span className="ml-1 text-[10px] text-muted-foreground">({h.changed_by_role})</span>
                                                        </span>
                                                    </div>
                                                ))}
                                            </div>
                                        )}
                                    </DetailSection>
                                    <DetailSection title="Spinr Pass" icon={CreditCard}>
                                        {(() => {
                                            const ss = selected.subscription_status;
                                            const plan = selected.subscription_plan;
                                            const exp = selected.subscription_expires_at;
                                            const expLabel = exp ? new Date(exp).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : null;
                                            if (ss === "active") {
                                                return (
                                                    <div className="flex items-center gap-3">
                                                        <div className="w-9 h-9 rounded-xl bg-violet-100 dark:bg-violet-900/30 flex items-center justify-center shrink-0">
                                                            <CreditCard className="h-4 w-4 text-violet-600 dark:text-violet-400" />
                                                        </div>
                                                        <div className="flex-1 min-w-0">
                                                            <p className="text-sm font-semibold text-violet-700 dark:text-violet-300">{plan || "Active Plan"}</p>
                                                            <p className="text-xs text-violet-600/70 dark:text-violet-400/70 mt-0.5">{expLabel ? `Renews / expires ${expLabel}` : "Subscription active"}</p>
                                                        </div>
                                                        <span className="inline-flex items-center px-2.5 py-1 rounded-full bg-violet-100 dark:bg-violet-900/30 text-violet-700 dark:text-violet-300 text-[10px] font-bold uppercase tracking-wide shrink-0">
                                                            Active
                                                        </span>
                                                    </div>
                                                );
                                            }
                                            if (ss === "expired") {
                                                return (
                                                    <div className="flex items-center gap-3">
                                                        <div className="w-9 h-9 rounded-xl bg-red-100 dark:bg-red-900/30 flex items-center justify-center shrink-0">
                                                            <CreditCard className="h-4 w-4 text-red-600 dark:text-red-400" />
                                                        </div>
                                                        <div className="flex-1 min-w-0">
                                                            <p className="text-sm font-semibold text-red-700 dark:text-red-400">{plan || "Spinr Pass"}</p>
                                                            <p className="text-xs text-red-600/80 dark:text-red-400/80 mt-0.5">{expLabel ? `Expired ${expLabel}` : "Subscription expired"}</p>
                                                        </div>
                                                        <span className="inline-flex items-center px-2.5 py-1 rounded-full bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400 text-[10px] font-bold uppercase tracking-wide shrink-0">
                                                            Expired
                                                        </span>
                                                    </div>
                                                );
                                            }
                                            return (
                                                <div className="flex items-center gap-3">
                                                    <div className="w-9 h-9 rounded-xl bg-muted flex items-center justify-center shrink-0">
                                                        <CreditCard className="h-4 w-4 text-muted-foreground" />
                                                    </div>
                                                    <p className="text-sm text-muted-foreground">No subscription</p>
                                                </div>
                                            );
                                        })()}
                                    </DetailSection>
                                    <div className="grid grid-cols-2 gap-2.5">
                                        <DetailField icon={CalendarRange} label="Joined" value={fmtDate(selected.created_at)} />
                                        <DetailField icon={Clock} label="Last Updated" value={fmtDate(selected.updated_at)} />
                                        {selected.account_deleted && (
                                            <DetailField icon={Trash2} label="Account Deleted" value={selected.deleted_at ? new Date(selected.deleted_at).toLocaleString("en-CA") : "Yes"} />
                                        )}
                                    </div>
                                </TabsContent>

                                {/* Rides */}
                                <TabsContent value="rides" className="mt-4">
                                    <DriverRidesTab
                                        rides={driverRides}
                                        loading={ridesLoading}
                                        driverName={`${selected.first_name || ""} ${selected.last_name || ""}`.trim()}
                                        fmtDate={fmtDate}
                                    />
                                </TabsContent>

                                {/* Referrals */}
                                <TabsContent value="referrals" className="mt-4">
                                    <DriverReferralsTab data={referrals} loading={referralsLoading} fmtDate={fmtDate} />
                                </TabsContent>

                                {/* Training (LMS) */}
                                <TabsContent value="training" className="mt-4">
                                    <DriverTrainingTab
                                        data={training}
                                        loading={trainingLoading}
                                        error={trainingError}
                                        onRefresh={() => loadDriverTraining(selected.id, true)}
                                        fmtDate={fmtDate}
                                    />
                                </TabsContent>

                                {/* Payouts */}
                                <TabsContent value="payouts" className="mt-4">
                                    <DriverPayoutsTab
                                        data={payoutSummary}
                                        loading={payoutLoading}
                                        driverId={selected.id}
                                        driverName={`${selected.first_name || ""} ${selected.last_name || ""}`.trim() || selected.email || "this driver"}
                                        notify={toast}
                                        retryingPayoutId={retryingPayoutId}
                                        refreshingKyc={refreshingKyc}
                                        refreshingPayouts={refreshingPayouts}
                                        revealedSin={revealedSin}
                                        canRevealSin={canRevealSin}
                                        canRefreshPayouts={isSuperAdmin}
                                        onRetry={async (payoutId) => {
                                            setRetryingPayoutId(payoutId);
                                            try {
                                                await retryPayout(payoutId);
                                                toast({ title: "Retry queued", description: "Payout sent back to Stripe for processing." });
                                                const fresh = await getDriverPayoutsSummary(selected.id);
                                                setPayoutSummary(fresh);
                                            } catch (e: any) {
                                                toast({ title: "Retry failed", description: e?.message || "Unknown error", variant: "destructive" });
                                            } finally {
                                                setRetryingPayoutId(null);
                                            }
                                        }}
                                        onRefreshKyc={async () => {
                                            setRefreshingKyc(true);
                                            try {
                                                const res = await refreshDriverStripeKyc(selected.id);
                                                const fresh = await getDriverPayoutsSummary(selected.id);
                                                setPayoutSummary(fresh);
                                                toast({
                                                    title: res.synced ? "Synced from Stripe" : "Not synced",
                                                    description: res.message,
                                                    ...(res.synced ? {} : { variant: "destructive" as const }),
                                                });
                                            } catch (e: any) {
                                                toast({ title: "Refresh failed", description: e?.message || "Unknown error", variant: "destructive" });
                                            } finally {
                                                setRefreshingKyc(false);
                                            }
                                        }}
                                        onRefreshPayouts={async () => {
                                            setRefreshingPayouts(true);
                                            try {
                                                // Failures raise non-2xx (handled in catch); still
                                                // branch on `synced` like onRefreshKyc so a future
                                                // partial-success response can't toast as success.
                                                const res = await refreshDriverStripePayouts(selected.id);
                                                const fresh = await getDriverPayoutsSummary(selected.id);
                                                setPayoutSummary(fresh);
                                                toast({
                                                    title: res.synced ? "Payouts synced from Stripe" : "Not synced",
                                                    description: res.message,
                                                    ...(res.synced ? {} : { variant: "destructive" as const }),
                                                });
                                            } catch (e: any) {
                                                toast({ title: "Payout sync failed", description: e?.message || "Unknown error", variant: "destructive" });
                                            } finally {
                                                setRefreshingPayouts(false);
                                            }
                                        }}
                                        onRevealSin={async () => {
                                            // Confirm before triggering — every reveal writes an
                                            // audit_log row and admins should not click it idly.
                                            if (!window.confirm("Reveal this driver's SIN?\n\nThis decrypts Spinr's encrypted copy. The call is recorded in the audit log with your admin ID and a timestamp. The value will be shown for 30 seconds then hidden.")) return;
                                            try {
                                                const res = await revealDriverSin(selected.id);
                                                setRevealedSin({ sin: res.sin, expiresAt: Date.now() + 30_000 });
                                                toast({ title: "SIN revealed", description: "Auto-hides in 30 seconds. Reveal logged." });
                                            } catch (e: any) {
                                                toast({ title: "Reveal failed", description: e?.message || "Unknown error", variant: "destructive" });
                                            }
                                        }}
                                    />
                                </TabsContent>

                                {/* Documents */}
                                <TabsContent value="documents" className="mt-4 space-y-6">
                                    <div className="flex items-center justify-between gap-2 -mt-1">
                                        <p className="text-xs text-muted-foreground">
                                            Review docs inline below, or open the full-screen reviewer for keyboard-driven triage.
                                        </p>
                                        <div className="flex items-center gap-2">
                                            <Button
                                                size="sm"
                                                variant="outline"
                                                className="h-8"
                                                onClick={() => setUploadDialogOpen(true)}
                                            >
                                                <Upload className="h-3.5 w-3.5 mr-1.5" />
                                                Upload Document
                                            </Button>
                                            <Button
                                                size="sm"
                                                variant="outline"
                                                className="h-8"
                                                onClick={() => setOpenReviewerForDriver({ id: selected.id, name: selected.name || selected.email || selected.id })}
                                            >
                                                <Maximize2 className="h-3.5 w-3.5 mr-1.5" />
                                                Open in Reviewer
                                            </Button>
                                        </div>
                                    </div>
                                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                                        {docsLoading ? (
                                            <>{[1,2,3,4,5].map(i => <div key={i} className="h-24 bg-muted rounded-xl animate-pulse" />)}</>
                                        ) : requiredDocs.length > 0 ? requiredDocs.filter(rd => rd.has_expiry).map(rd => (
                                            <DocExpirySummaryCard
                                                key={rd.key}
                                                label={rd.label}
                                                summary={_getDocSummary(rd.id, rd.key, rd.label)}
                                            />
                                        )) : (<>
                                            <DocExpirySummaryCard label="Driver's License"     summary={_getDocSummary(undefined, "drivers_license",      "Driver's License")} />
                                            <DocExpirySummaryCard label="Vehicle Insurance"    summary={_getDocSummary(undefined, "vehicle_insurance",    "Vehicle Insurance")} />
                                            <DocExpirySummaryCard label="Vehicle Registration" summary={_getDocSummary(undefined, "vehicle_registration", "Vehicle Registration")} />
                                            <DocExpirySummaryCard label="Vehicle Inspection"   summary={_getDocSummary(undefined, "vehicle_inspection",  "Vehicle Inspection")} />
                                            <DocExpirySummaryCard label="Background Check"     summary={_getDocSummary(undefined, "background_check",    "Background Check")} />
                                        </>)}
                                    </div>
                                    {docsLoading ? <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">{[1,2,3,4].map(i=><div key={i} className="h-48 bg-muted rounded-xl animate-pulse" />)}</div>
                                    : requiredDocs.length > 0 ? (
                                        <div className="space-y-6">
                                            {requiredDocs.map(reqDoc => {
                                                const matchingDocs = activeDocs.filter(d => matchesRequirement(d, reqDoc));
                                                const counts = {
                                                    pending: matchingDocs.filter(d => d.status === "pending").length,
                                                    approved: matchingDocs.filter(d => d.status === "approved").length,
                                                    rejected: matchingDocs.filter(d => d.status === "rejected").length,
                                                };
                                                // Surface the expiry gap directly in the section header
                                                // when the requirement needs an expiry but the approved
                                                // doc has none on file — the previous "Requires Expiry"
                                                // pill was static and read as a warning even after a
                                                // valid approval, confusing reviewers.
                                                const summary = _getDocSummary(reqDoc.id, reqDoc.key, reqDoc.label);
                                                const expiryMissing = reqDoc.has_expiry && summary.docStatus === "approved" && !summary.expiry;
                                                return (
                                                    <div key={reqDoc.key}>
                                                        <div className="flex items-center gap-2 mb-3 flex-wrap">
                                                            <FileText className="h-4 w-4 text-muted-foreground" /><h4 className="text-sm font-semibold">{reqDoc.label}</h4>
                                                            {matchingDocs.length === 0 && <Badge className="bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 text-[10px]">Missing</Badge>}
                                                            {counts.pending > 0 && <Badge className="bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 text-[10px]">{counts.pending} pending</Badge>}
                                                            {counts.approved > 0 && counts.pending === 0 && !expiryMissing && <Badge className="bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400 text-[10px]">Approved</Badge>}
                                                            {expiryMissing && counts.pending === 0 && <Badge className="bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 text-[10px]">Approved · expiry not recorded</Badge>}
                                                            {counts.rejected > 0 && counts.pending === 0 && counts.approved === 0 && <Badge className="bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 text-[10px]">Re-upload needed</Badge>}
                                                        </div>
                                                        {matchingDocs.length > 0 ? <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">{matchingDocs.map(d=><DocCard key={d.id} d={d} docBusy={docBusy} driverName={selected?.name || selected?.full_name || ''} onPreview={setPreviewUrl} onReview={openReviewDialog} />)}</div>
                                                        : <div className="bg-muted/20 border border-dashed rounded-xl p-6 text-center text-muted-foreground"><Image className="h-8 w-8 mx-auto mb-2 opacity-20" /><p className="text-sm">No {reqDoc.label} uploaded yet</p></div>}
                                                    </div>
                                                );
                                            })}
                                            {/* Other Documents: any active docs not matched by any required doc */}
                                            {(() => {
                                                const matchedIds = new Set(requiredDocs.flatMap(reqDoc =>
                                                    activeDocs.filter(d => matchesRequirement(d, reqDoc)).map(d => d.id)
                                                ));
                                                const unmatched = activeDocs.filter(d => !matchedIds.has(d.id));
                                                if (unmatched.length === 0) return null;
                                                return (
                                                    <div>
                                                        <div className="flex items-center gap-2 mb-3">
                                                            <FileText className="h-4 w-4 text-muted-foreground" /><h4 className="text-sm font-semibold">Other Documents</h4>
                                                            <Badge variant="outline" className="text-[10px]">{unmatched.length} uploaded</Badge>
                                                        </div>
                                                        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">{unmatched.map(d=><DocCard key={d.id} d={d} docBusy={docBusy} driverName={selected?.name || selected?.full_name || ''} onPreview={setPreviewUrl} onReview={openReviewDialog} />)}</div>
                                                    </div>
                                                );
                                            })()}
                                        </div>
                                    ) : activeDocs.length > 0 ? (
                                        <div className="space-y-3">
                                            <p className="text-xs text-muted-foreground">No service area configured — showing all uploaded documents</p>
                                            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                                                {activeDocs.map(d => <DocCard key={d.id} d={d} docBusy={docBusy} driverName={selected?.name || selected?.full_name || ''} onPreview={setPreviewUrl} onReview={openReviewDialog} />)}
                                            </div>
                                        </div>
                                    ) : (
                                        <div className="text-center py-12 text-muted-foreground bg-muted/20 rounded-xl border border-dashed"><Image className="h-10 w-10 mx-auto mb-3 opacity-30" /><p className="text-sm font-medium">No document requirements configured for this service area</p></div>
                                    )}
                                </TabsContent>

                                {/* Actions & Verification */}
                                <TabsContent value="verification" className="mt-4 space-y-5">
                                    <DriverActionBar
                                        driver={selected}
                                        onActionComplete={(updates) => {
                                            if (updates && Object.keys(updates).length > 0) {
                                                setSelected((prev: any) => prev ? { ...prev, ...updates } : prev);
                                                setDrivers(prevList => prevList.map(d => d.id === selected.id ? { ...d, ...updates } : d));
                                            }
                                            loadData();
                                            loadDrivers();
                                        }}
                                    />
                                    <VerificationSummaryCard
                                        requiredDocs={requiredDocs.length > 0 ? requiredDocs : [
                                            { key: "drivers_license",      label: "Driver's License",    has_expiry: true },
                                            { key: "vehicle_insurance",    label: "Vehicle Insurance",   has_expiry: true },
                                            { key: "vehicle_registration", label: "Vehicle Registration",has_expiry: true },
                                            { key: "vehicle_inspection",   label: "Vehicle Inspection",  has_expiry: true },
                                            { key: "background_check",     label: "Background Check",   has_expiry: true },
                                        ]}
                                        activeDocs={activeDocs}
                                        driver={selected}
                                        docKeyToExpiryField={_docKeyToExpiryField}
                                        onOpenDocumentsTab={() => setDetailTab("documents")}
                                    />
                                </TabsContent>

                                {/* Notes */}
                                <TabsContent value="notes" className="mt-4">
                                    <DriverNotes driverId={selected.id} />
                                </TabsContent>

                                {/* History: daily activity (per-phase km + empty/riding time) + audit timeline */}
                                <TabsContent value="history" className="mt-4 space-y-6">
                                    <DriverActivity driverId={selected.id} />
                                    <DriverTimeline driverId={selected.id} driver={selected} />
                                </TabsContent>

                                {/* Subscription payment history */}
                                <TabsContent value="subscriptions" className="mt-4">
                                    {subPaymentsLoading ? (
                                        <div className="py-12 flex justify-center"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>
                                    ) : driverSubPayments.length === 0 ? (
                                        <div className="py-12 text-center text-sm text-muted-foreground">No subscription payments found for this driver.</div>
                                    ) : (
                                        <>
                                        <Table>
                                            <TableHeader>
                                                <TableRow>
                                                    <SortableHead column="created_at" sort={subPaymentsSort} onSort={toggleSubPaymentsSort}>Date</SortableHead>
                                                    <SortableHead column="plan_name" sort={subPaymentsSort} onSort={toggleSubPaymentsSort}>Plan</SortableHead>
                                                    <SortableHead column="billing_reason" sort={subPaymentsSort} onSort={toggleSubPaymentsSort}>Type</SortableHead>
                                                    <SortableHead column="subtotal" sort={subPaymentsSort} onSort={toggleSubPaymentsSort} align="right">Subtotal</SortableHead>
                                                    <SortableHead column="gst_amount" sort={subPaymentsSort} onSort={toggleSubPaymentsSort} align="right">GST</SortableHead>
                                                    <SortableHead column="pst_amount" sort={subPaymentsSort} onSort={toggleSubPaymentsSort} align="right">PST</SortableHead>
                                                    <SortableHead column="hst_amount" sort={subPaymentsSort} onSort={toggleSubPaymentsSort} align="right">HST</SortableHead>
                                                    <SortableHead column="amount" sort={subPaymentsSort} onSort={toggleSubPaymentsSort} align="right">Total</SortableHead>
                                                </TableRow>
                                            </TableHeader>
                                            <TableBody>
                                                {sortedSubPayments.slice(subPage * subPageSize, (subPage + 1) * subPageSize).map((p) => (
                                                    <TableRow key={p.id}>
                                                        <TableCell className="text-xs whitespace-nowrap">
                                                            {p.created_at ? new Date(p.created_at).toLocaleDateString("en-CA", { year: "numeric", month: "short", day: "numeric" }) : "—"}
                                                        </TableCell>
                                                        <TableCell className="text-xs">{p.plan_name ?? "—"}</TableCell>
                                                        <TableCell>
                                                            <Badge variant="secondary" className="text-xs">
                                                                {p.billing_reason === "subscription_cycle" ? "Renewal" : p.billing_reason === "one_off" ? "One-off" : p.billing_reason ?? "—"}
                                                            </Badge>
                                                        </TableCell>
                                                        <TableCell className="text-right text-xs tabular-nums">{formatCurrency(p.subtotal)}</TableCell>
                                                        <TableCell className="text-right text-xs tabular-nums text-muted-foreground">{p.gst_amount > 0 ? formatCurrency(p.gst_amount) : "—"}</TableCell>
                                                        <TableCell className="text-right text-xs tabular-nums text-muted-foreground">{p.pst_amount > 0 ? formatCurrency(p.pst_amount) : "—"}</TableCell>
                                                        <TableCell className="text-right text-xs tabular-nums text-muted-foreground">{p.hst_amount > 0 ? formatCurrency(p.hst_amount) : "—"}</TableCell>
                                                        <TableCell className="text-right text-xs font-semibold tabular-nums">{formatCurrency(p.amount)}</TableCell>
                                                    </TableRow>
                                                ))}
                                            </TableBody>
                                        </Table>
                                        <div className="flex items-center justify-between gap-3 flex-wrap mt-3">
                                            <div className="flex items-center gap-2">
                                                <span className="text-xs text-muted-foreground">Show</span>
                                                <Select value={String(subPageSize)} onValueChange={(v) => { setSubPageSize(Number(v)); setSubPage(0); }}>
                                                    <SelectTrigger className="h-8 text-xs w-[70px]">
                                                        <SelectValue />
                                                    </SelectTrigger>
                                                    <SelectContent>
                                                        {[25, 50, 100].map(n => (
                                                            <SelectItem key={n} value={String(n)} className="text-xs">{n}</SelectItem>
                                                        ))}
                                                    </SelectContent>
                                                </Select>
                                                <span className="text-xs text-muted-foreground">per page</span>
                                            </div>
                                            {sortedSubPayments.length > subPageSize && (
                                                <Pagination
                                                    page={subPage}
                                                    pageSize={subPageSize}
                                                    hasNextPage={sortedSubPayments.length > (subPage + 1) * subPageSize}
                                                    totalCount={sortedSubPayments.length}
                                                    onPageChange={setSubPage}
                                                />
                                            )}
                                        </div>
                                        </>
                                    )}
                                </TabsContent>
                            </div>
                        </Tabs>
                    </>)}
                </SheetContent>
            </Sheet>

            <DocumentReviewer
                open={!!openReviewerForDriver}
                driverId={openReviewerForDriver?.id || null}
                driverName={openReviewerForDriver?.name}
                onClose={() => setOpenReviewerForDriver(null)}
                onAfterAction={() => { reloadDriverDocs(); loadData(); loadDrivers(); }}
            />

            <DocumentUploadDialog
                open={uploadDialogOpen}
                onClose={() => setUploadDialogOpen(false)}
                driverId={selected?.id || null}
                driverName={selected?.name || selected?.email || null}
                requirements={requiredDocs}
                existing={existingDocInfo}
                onUploaded={() => { reloadDriverDocs(); loadData(); loadDrivers(); }}
            />

            {/* Document preview — uses shadcn Dialog (Radix Portal) so it
                stacks above the parent Sheet's modal context. Rendering this
                as a bare fixed div outside the Sheet looked correct but its
                clicks bubbled into the Sheet's pointer-events scope, so
                neither the close X nor "Open original" reached their
                handlers. */}
            <Dialog open={!!previewUrl} onOpenChange={(open) => { if (!open) setPreviewUrl(null); }}>
                <DialogContent
                    className="!max-w-[95vw] sm:!max-w-5xl !p-0 bg-transparent border-none shadow-none"
                    showCloseButton={false}
                >
                    <DialogTitle className="sr-only">Document preview</DialogTitle>
                    {previewUrl && (
                        <div className="relative flex items-center justify-center">
                            <img
                                src={previewUrl}
                                alt="Document preview"
                                className="max-w-full max-h-[85vh] object-contain rounded-lg shadow-2xl bg-black/40"
                            />
                            <button
                                type="button"
                                onClick={() => setPreviewUrl(null)}
                                className="absolute top-2 right-2 bg-black/70 hover:bg-black text-white rounded-full p-2 transition"
                                aria-label="Close preview"
                            >
                                <X className="h-5 w-5" />
                            </button>
                            <a
                                href={previewUrl}
                                target="_blank"
                                rel="noreferrer"
                                className="absolute bottom-4 right-4 bg-white/95 hover:bg-white text-gray-900 rounded-lg px-3 py-1.5 text-sm font-medium flex items-center gap-1.5 transition shadow"
                            >
                                <ExternalLink className="h-4 w-4" /> Open original
                            </a>
                        </div>
                    )}
                </DialogContent>
            </Dialog>

            <Dialog open={!!reviewingDoc} onOpenChange={open => { if (!open) setReviewingDoc(null); }}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>{reviewingDoc?.action === "approved" ? "Approve Document" : "Reject Document"}</DialogTitle>
                        <DialogDescription>
                            {reviewingDoc?.docType && <span className="font-semibold text-foreground">{reviewingDoc.docType}</span>}
                            {reviewingDoc?.action === "approved"
                                ? reviewingDoc?.requiresExpiry
                                    ? " — This document requires an expiry date. Set the date from the document."
                                    : " — Optionally set an expiry date."
                                : " — Provide a reason for rejection."}
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4 py-2">
                        {reviewingDoc?.action === "approved" ? (
                            <div>
                                <label className="text-sm font-medium mb-1.5 block">
                                    Expiry Date {reviewingDoc?.requiresExpiry ? <span className="text-red-500">*</span> : "(optional)"}
                                </label>
                                <Input type="date" value={reviewExpiry} onChange={e => setReviewExpiry(e.target.value)} className="w-full" />
                                {reviewingDoc?.requiresExpiry && !reviewExpiry && (
                                    <p className="text-xs text-red-500 mt-1 flex items-center gap-1"><AlertTriangle className="h-3 w-3" /> Expiry date is required for this document type. This will update the driver&apos;s profile.</p>
                                )}
                                {!reviewingDoc?.requiresExpiry && <p className="text-xs text-muted-foreground mt-1">Leave empty if no expiry.</p>}
                            </div>
                        ) : (
                            <div><label className="text-sm font-medium mb-1.5 block">Reason (optional)</label><Input value={reviewReason} onChange={e => setReviewReason(e.target.value)} placeholder="e.g., Document is blurry" className="w-full" /></div>
                        )}
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setReviewingDoc(null)}>Cancel</Button>
                        <Button
                            onClick={confirmReview}
                            disabled={reviewingDoc?.action === "approved" && reviewingDoc?.requiresExpiry && !reviewExpiry}
                            className={reviewingDoc?.action === "approved" ? "bg-emerald-600 hover:bg-emerald-700 text-white" : "bg-red-600 hover:bg-red-700 text-white"}
                        >
                            {reviewingDoc?.action === "approved" ? "Approve" : "Reject"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}

const RIDE_STATUS_STYLE: Record<string, { bg: string; text: string; label: string }> = {
    completed:        { bg: "bg-emerald-100 dark:bg-emerald-900/30", text: "text-emerald-700 dark:text-emerald-300", label: "Completed" },
    in_progress:      { bg: "bg-blue-100 dark:bg-blue-900/30",   text: "text-blue-700 dark:text-blue-300",   label: "In Progress" },
    cancelled:        { bg: "bg-red-100 dark:bg-red-900/30",     text: "text-red-700 dark:text-red-300",     label: "Cancelled" },
    driver_assigned:  { bg: "bg-violet-100 dark:bg-violet-900/30", text: "text-violet-700 dark:text-violet-300", label: "Assigned" },
    driver_accepted:  { bg: "bg-violet-100 dark:bg-violet-900/30", text: "text-violet-700 dark:text-violet-300", label: "Accepted" },
    driver_arrived:   { bg: "bg-indigo-100 dark:bg-indigo-900/30", text: "text-indigo-700 dark:text-indigo-300", label: "Arrived" },
    searching:        { bg: "bg-amber-100 dark:bg-amber-900/30",  text: "text-amber-700 dark:text-amber-300",  label: "Searching" },
};

function VerificationSummaryCard({
    requiredDocs,
    activeDocs,
    driver,
    docKeyToExpiryField,
    onOpenDocumentsTab,
}: {
    requiredDocs: { id?: string; key: string; label: string; has_expiry: boolean }[];
    activeDocs: any[];
    driver: any;
    docKeyToExpiryField: (key: string) => string | null;
    onOpenDocumentsTab: () => void;
}) {
    const rows = requiredDocs.map(rd => {
        const matchingDocs = activeDocs.filter(d => matchesRequirement(d, rd));
        const hasApproved = matchingDocs.some(d => d.status === "approved");
        const hasPending = matchingDocs.some(d => d.status === "pending");
        const expiryField = docKeyToExpiryField(rd.key);
        const expiryVal = expiryField ? driver[expiryField] : undefined;
        const isExpired = expiryVal && new Date(expiryVal) < new Date();
        let s: "approved" | "pending" | "missing" | "expired" = "missing";
        if (isExpired) s = "expired";
        else if (hasApproved) s = "approved";
        else if (hasPending || matchingDocs.length > 0) s = "pending";
        return { rd, status: s };
    });

    const approved = rows.filter(r => r.status === "approved").length;
    const total = rows.length;
    const pending = rows.filter(r => r.status === "pending").length;
    const missing = rows.filter(r => r.status === "missing").length;
    const expired = rows.filter(r => r.status === "expired").length;
    const pct = total > 0 ? Math.round((approved / total) * 100) : 0;
    const allClear = pending === 0 && missing === 0 && expired === 0 && total > 0;

    return (
        <div className="rounded-xl border border-border bg-card overflow-hidden">
            <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                    <CheckCircle className={`h-4 w-4 ${allClear ? "text-emerald-500" : "text-muted-foreground"}`} />
                    <h4 className="text-sm font-semibold tracking-tight">Verification</h4>
                    <span className="text-xs text-muted-foreground">{approved} / {total} approved</span>
                </div>
                <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={onOpenDocumentsTab}>
                    Open Documents
                    <ExternalLink className="h-3 w-3 ml-1" />
                </Button>
            </div>
            <div className="px-4 py-3 space-y-3">
                <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                    <div
                        className={`h-full transition-all ${allClear ? "bg-emerald-500" : pct >= 75 ? "bg-amber-500" : "bg-muted-foreground/40"}`}
                        style={{ width: `${pct}%` }}
                    />
                </div>
                <div className="flex items-center gap-3 flex-wrap text-xs">
                    {pending > 0 && <span className="inline-flex items-center gap-1 text-amber-600 dark:text-amber-400"><Clock className="h-3 w-3" />{pending} pending</span>}
                    {missing > 0 && <span className="inline-flex items-center gap-1 text-red-600 dark:text-red-400"><AlertTriangle className="h-3 w-3" />{missing} missing</span>}
                    {expired > 0 && <span className="inline-flex items-center gap-1 text-red-600 dark:text-red-400"><AlertTriangle className="h-3 w-3" />{expired} expired</span>}
                    {allClear && <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400"><CheckCircle className="h-3 w-3" />All required documents are approved.</span>}
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5 pt-1">
                    {rows.map(({ rd, status }) => {
                        const cfg = status === "approved" ? { icon: <CheckCircle className="h-3.5 w-3.5 text-emerald-500" />, text: "text-emerald-600 dark:text-emerald-400" }
                            : status === "pending" ? { icon: <Clock className="h-3.5 w-3.5 text-amber-500" />, text: "text-amber-600 dark:text-amber-400" }
                            : status === "expired" ? { icon: <AlertTriangle className="h-3.5 w-3.5 text-red-500" />, text: "text-red-600 dark:text-red-400" }
                            : { icon: <div className="w-3.5 h-3.5 rounded-full border-2 border-muted-foreground/30" />, text: "text-muted-foreground" };
                        return (
                            <div key={rd.key} className="flex items-center gap-2 text-xs">
                                {cfg.icon}
                                <span className="truncate flex-1">{rd.label}</span>
                                <span className={`text-[10px] uppercase tracking-wide font-semibold ${cfg.text}`}>{status}</span>
                            </div>
                        );
                    })}
                </div>
                <div className="flex items-center justify-between pt-1 text-xs border-t border-border">
                    <div className="flex items-center gap-2">
                        <div className={`w-2 h-2 rounded-full ${driver.profile_image_status && driver.profile_image_status !== "rejected" ? "bg-emerald-500" : "bg-muted-foreground/30"}`} />
                        <span className="text-muted-foreground">Profile photo</span>
                    </div>
                    <div className="flex items-center gap-2">
                        <div className={`w-2 h-2 rounded-full ${driver.vehicle_photo_url ? "bg-emerald-500" : "bg-muted-foreground/30"}`} />
                        <span className="text-muted-foreground">Vehicle photo</span>
                    </div>
                </div>
            </div>
        </div>
    );
}

const DRIVER_RIDES_PAGE_SIZE_OPTIONS = [25, 50, 100] as const;

type RidesSortKey = "created_at" | "rider_name" | "status" | "distance_km" | "duration_seconds" | "total_fare" | "tip_amount";

const PAYOUT_STATUS_STYLE: Record<string, { bg: string; text: string; label: string }> = {
    completed:  { bg: "bg-emerald-100 dark:bg-emerald-900/30", text: "text-emerald-700 dark:text-emerald-300", label: "Paid" },
    pending:    { bg: "bg-amber-100 dark:bg-amber-900/30",     text: "text-amber-700 dark:text-amber-300",     label: "Pending" },
    processing: { bg: "bg-blue-100 dark:bg-blue-900/30",       text: "text-blue-700 dark:text-blue-300",       label: "Processing" },
    failed:     { bg: "bg-red-100 dark:bg-red-900/30",         text: "text-red-700 dark:text-red-300",         label: "Failed" },
};

function PayoutMetric({ label, value, tone, sub }: { label: string; value: string; tone?: "emerald" | "amber" | "red" | "neutral"; sub?: string }) {
    const styles = {
        emerald: { bg: "bg-emerald-50 dark:bg-emerald-900/10 border-emerald-200 dark:border-emerald-800", value: "text-emerald-700 dark:text-emerald-300" },
        amber:   { bg: "bg-amber-50 dark:bg-amber-900/10 border-amber-200 dark:border-amber-800",         value: "text-amber-700 dark:text-amber-300" },
        red:     { bg: "bg-red-50 dark:bg-red-900/10 border-red-200 dark:border-red-800",                 value: "text-red-700 dark:text-red-300" },
        neutral: { bg: "bg-card border-border",                                                            value: "text-foreground" },
    }[tone ?? "neutral"];
    return (
        <div className={`rounded-xl p-3.5 border ${styles.bg}`}>
            <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">{label}</p>
            <p className={`text-xl font-bold tabular-nums mt-1 ${styles.value}`}>{value}</p>
            {sub && <p className="text-[10px] text-muted-foreground mt-0.5">{sub}</p>}
        </div>
    );
}

function DriverPayoutsTab({ data, loading, driverId, driverName, retryingPayoutId, onRetry, onRefreshKyc, onRefreshPayouts, onRevealSin, refreshingKyc, refreshingPayouts, revealedSin, canRevealSin, canRefreshPayouts, notify }: {
    data: DriverPayoutSummary | null;
    loading: boolean;
    driverId: string;
    driverName: string;
    retryingPayoutId: string | null;
    onRetry: (payoutId: string) => Promise<void>;
    onRefreshKyc: () => Promise<void>;
    onRefreshPayouts: () => Promise<void>;
    onRevealSin: () => Promise<void>;
    refreshingKyc: boolean;
    refreshingPayouts: boolean;
    revealedSin: { sin: string; expiresAt: number } | null;
    canRevealSin: boolean;
    canRefreshPayouts: boolean;
    notify: (opts: { title: string; description?: string; variant?: "destructive" }) => void;
}) {
    const fmtDateTime = (iso?: string | null) => {
        if (!iso) return "—";
        try {
            const d = new Date(iso);
            return `${d.toLocaleDateString("en-CA", { month: "short", day: "numeric", year: "numeric" })} · ${d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}`;
        } catch { return iso; }
    };
    const fmtMoney = (n: number) => formatCurrency(n);

    // Client-side sort for the payout-history table. Called before the early
    // returns so the hook order stays stable; reads payouts off the nullable
    // data and falls back to an empty list when not yet loaded.
    const { sorted: sortedPayouts, sort: payoutsSort, toggle: togglePayoutsSort } = useTableSort(data?.payouts ?? []);
    const [payoutPage, setPayoutPage] = useState(0);
    const [payoutPageSize, setPayoutPageSize] = useState<number>(25);
    useEffect(() => { setPayoutPage(0); }, [payoutsSort]);

    if (loading && !data) return (
        <div className="space-y-4 animate-pulse">
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                {Array.from({ length: 4 }).map((_, i) => <div key={i} className="h-20 rounded-xl bg-muted" />)}
            </div>
            <div className="h-32 rounded-xl bg-muted" />
            {Array.from({ length: 4 }).map((_, i) => <div key={i} className="h-12 rounded-xl bg-muted" />)}
        </div>
    );

    if (!data) return (
        <div className="py-16 text-center text-muted-foreground">
            <DollarSign className="h-10 w-10 mx-auto mb-3 opacity-30" />
            <p className="text-sm font-medium">No payout data available</p>
        </div>
    );

    const { summary, payment_method: pm, payouts } = data;
    const bonuses = data.bonuses ?? [];
    const hasBonuses = (summary.lifetime_bonuses ?? 0) > 0;

    return (
        <div className="space-y-5">
            {/* Refresh Payouts from Stripe — super_admin only, matching the
                backend gate; lower roles never see a button that can only 403. */}
            {canRefreshPayouts && (
                <div className="flex items-center justify-end">
                    <Button
                        variant="outline"
                        size="sm"
                        className="h-8 text-xs"
                        disabled={refreshingPayouts}
                        onClick={onRefreshPayouts}
                        title="Pull all Transfers, bank payouts, and balance transactions from Stripe for this driver"
                    >
                        {refreshingPayouts ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" /> : <RefreshCw className="h-3.5 w-3.5 mr-1.5" />}
                        Refresh Payouts from Stripe
                    </Button>
                </div>
            )}

            {/* Migrated-driver context. One sentence up front beats four
                cards that each look wrong for a different reason: without
                this, "$0.00 lifetime" sits beside a Rides tab showing
                previous-app trips and a paid-out card full of previous-app
                transfers, and every number invites a support escalation. */}
            {((summary.imported_rides_excluded ?? 0) > 0 || (summary.legacy_stripe_transfers ?? 0) > 0) && (
                <div className="rounded-xl border border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-900/10 p-3 flex items-start gap-3">
                    <Clock className="h-4 w-4 text-blue-600 dark:text-blue-400 shrink-0 mt-0.5" />
                    <p className="text-xs text-blue-700 dark:text-blue-300">
                        <span className="font-semibold">Migrated from the previous app.</span>{" "}
                        {(summary.imported_rides_excluded ?? 0) > 0 && `${summary.imported_rides_excluded} imported ride${(summary.imported_rides_excluded ?? 0) === 1 ? "" : "s"}`}
                        {(summary.imported_rides_excluded ?? 0) > 0 && (summary.legacy_stripe_transfers ?? 0) > 0 && " and "}
                        {(summary.legacy_stripe_transfers ?? 0) > 0 && `${fmtMoney(summary.legacy_stripe_transfers ?? 0)} in previous-app payouts`}
                        {" "}are kept for history and tax, but are not counted in Spinr earnings or the owed balance below.
                    </p>
                </div>
            )}

            {/* Top 4 metric cards: Pending / Paid out / Lifetime / YTD */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                <PayoutMetric
                    label="Pending payout"
                    value={fmtMoney(summary.pending_balance)}
                    tone={summary.pending_balance > 0 ? "amber" : "neutral"}
                    sub={summary.pending_in_flight > 0 ? `${fmtMoney(summary.pending_in_flight)} in flight` : "Owed to driver, not yet queued"}
                />
                <PayoutMetric
                    label="Total paid out"
                    value={fmtMoney(summary.total_paid_out)}
                    tone="emerald"
                    // legacy_stripe_transfers is a slice OF this total, not an
                    // addition to it — say "incl.", never "+". When the whole
                    // total is previous-app money, say THAT: "incl. $327.02"
                    // under a $327.02 headline reads like double-speak.
                    sub={(summary.legacy_stripe_transfers ?? 0) > 0
                        ? ((summary.legacy_stripe_transfers ?? 0) >= summary.total_paid_out
                            ? `${payouts.filter(p => p.status === "completed").length} payouts · all paid by the previous app`
                            : `${payouts.filter(p => p.status === "completed").length} payouts · incl. ${fmtMoney(summary.legacy_stripe_transfers ?? 0)} from the previous app`)
                        : `${payouts.filter(p => p.status === "completed").length} completed payouts`}
                />
                <PayoutMetric
                    label="Lifetime earnings"
                    value={fmtMoney(summary.lifetime_earnings)}
                    // Spell out imported rides. Otherwise this reads "0
                    // completed rides · $0.00" next to a Rides tab showing 15,
                    // and nothing explains why — which looks like data loss.
                    sub={(summary.imported_rides_excluded ?? 0) > 0
                        ? `${summary.rides_count.toLocaleString()} Spinr rides · ${summary.imported_rides_excluded} imported from previous app (not counted)`
                        : hasBonuses
                            ? `${fmtMoney(summary.lifetime_ride_earnings ?? 0)} rides + ${fmtMoney(summary.lifetime_bonuses ?? 0)} bonuses · ${fmtMoney(summary.lifetime_tips)} tips`
                            : `${summary.rides_count.toLocaleString()} completed rides · ${fmtMoney(summary.lifetime_tips)} tips`
                    }
                />
                <PayoutMetric
                    label="Year to date"
                    value={fmtMoney(summary.ytd_earnings)}
                    sub={`${summary.active_days_30d} active days in last 30d`}
                />
            </div>

            {/* On-hold warning if any failed payouts */}
            {summary.on_hold > 0 && summary.last_failed_payout && (
                <div className="rounded-xl border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/10 p-3 flex items-start gap-3">
                    <AlertTriangle className="h-4 w-4 text-red-600 dark:text-red-400 shrink-0 mt-0.5" />
                    <div className="min-w-0 flex-1">
                        <p className="text-sm font-semibold text-red-700 dark:text-red-300">{fmtMoney(summary.on_hold)} on hold from failed payouts</p>
                        <p className="text-xs text-red-600/80 dark:text-red-400/80 mt-0.5">
                            Most recent failure: {summary.last_failed_payout.error_message || "Unknown error"} · {fmtDateTime(summary.last_failed_payout.created_at)}
                        </p>
                    </div>
                </div>
            )}

            {/* Payment method on file */}
            <div className="rounded-xl border border-border bg-card overflow-hidden">
                <div className="px-4 py-3 border-b border-border flex items-center justify-between">
                    <div className="flex items-center gap-2">
                        <CreditCard className="h-4 w-4 text-muted-foreground" />
                        <h4 className="text-sm font-semibold">Payout method</h4>
                    </div>
                    {pm.has_bank_account ? (
                        <Badge className="bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300 text-[10px]">Linked</Badge>
                    ) : (
                        <Badge className="bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300 text-[10px]">No method linked</Badge>
                    )}
                </div>
                <div className="px-4 py-3 grid grid-cols-1 sm:grid-cols-2 gap-3">
                    {pm.has_bank_account ? (
                        <>
                            <div>
                                <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">Bank</p>
                                <p className="text-sm font-medium mt-0.5">{pm.bank_name || "Stripe Connect"}</p>
                            </div>
                            <div>
                                <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">Account</p>
                                <p className="text-sm font-medium mt-0.5 font-mono">
                                    •••• {pm.account_last4 || "****"}
                                    {pm.account_type && <span className="text-xs text-muted-foreground ml-2 font-sans">({pm.account_type})</span>}
                                </p>
                            </div>
                            {pm.account_holder_name && (
                                <div>
                                    <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">Holder</p>
                                    <p className="text-sm font-medium mt-0.5">{pm.account_holder_name}</p>
                                </div>
                            )}
                            <div>
                                <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">Stripe Connect</p>
                                {pm.stripe_connected ? (
                                    <p className="text-sm font-medium mt-0.5 inline-flex items-center gap-1.5">
                                        <span className="w-2 h-2 rounded-full bg-emerald-500" />
                                        Connected
                                        {pm.stripe_account_hint && <span className="text-xs text-muted-foreground font-mono">acct…{pm.stripe_account_hint}</span>}
                                    </p>
                                ) : (
                                    <p className="text-sm font-medium mt-0.5 inline-flex items-center gap-1.5 text-muted-foreground">
                                        <span className="w-2 h-2 rounded-full bg-muted-foreground/40" />
                                        Not connected
                                    </p>
                                )}
                            </div>
                            {pm.is_verified !== null && (
                                <div>
                                    <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">Verification</p>
                                    <p className={`text-sm font-medium mt-0.5 ${pm.is_verified ? "text-emerald-600 dark:text-emerald-400" : "text-amber-600 dark:text-amber-400"}`}>
                                        {pm.is_verified ? "Verified" : "Unverified"}
                                    </p>
                                </div>
                            )}
                        </>
                    ) : (
                        <div className="col-span-full text-sm text-muted-foreground py-2">
                            {driverName} has not added a payout method yet. Payouts cannot be processed until a bank account or Stripe Connect account is linked.
                        </div>
                    )}
                </div>
            </div>

            {/* Tax & Identity (Stripe Connect KYC mirror) */}
            <div className="rounded-xl border border-border bg-card overflow-hidden">
                <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                        <Shield className="h-4 w-4 text-muted-foreground" />
                        <h4 className="text-sm font-semibold">Tax &amp; Identity</h4>
                        {data.kyc.payouts_enabled ? (
                            <Badge className="bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300 text-[10px]">Verified</Badge>
                        ) : data.kyc.details_submitted ? (
                            <Badge className="bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300 text-[10px]">Pending Stripe review</Badge>
                        ) : data.kyc.requirements_due.length > 0 || data.kyc.requirements_past_due.length > 0 ? (
                            <Badge className="bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300 text-[10px]">Action required</Badge>
                        ) : (
                            <Badge variant="outline" className="text-[10px]">Not started</Badge>
                        )}
                    </div>
                    <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 text-xs"
                        disabled={refreshingKyc}
                        onClick={onRefreshKyc}
                        title="Pull latest from Stripe"
                    >
                        {refreshingKyc ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : <RefreshCw className="h-3 w-3 mr-1" />}
                        Refresh
                    </Button>
                </div>
                <div className="px-4 py-3 space-y-3">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div>
                            <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">SIN</p>
                            {/* Gate on OUR Vault-encrypted copy (sin_on_file), not Stripe's
                                id_number_provided mirror: the reveal endpoint decrypts our
                                column (Stripe's is write-only), and the SIN-before-Stripe
                                flow means drivers now have a SIN on file long before Stripe
                                does. Gating on Stripe's flag hid the Reveal button for
                                exactly those drivers — and showed it for legacy drivers
                                whose reveal call can only 400. */}
                            {data.kyc.sin_on_file ? (
                                <div className="flex items-center gap-2 mt-0.5">
                                    {revealedSin ? (
                                        <p className="text-sm font-medium font-mono">{revealedSin.sin}</p>
                                    ) : (
                                        <p className="text-sm font-medium font-mono">•••-•••-{data.kyc.sin_last4 || "•••"}</p>
                                    )}
                                    {canRevealSin ? (
                                        <Button
                                            size="xs"
                                            variant="outline"
                                            className="h-6 text-[10px] px-2"
                                            onClick={onRevealSin}
                                            disabled={!!revealedSin}
                                            title="One-shot decrypt of Spinr's encrypted copy. Every reveal writes an audit log entry."
                                        >
                                            {revealedSin ? <CheckCircle className="h-3 w-3 mr-1" /> : <Eye className="h-3 w-3 mr-1" />}
                                            {revealedSin ? "Shown" : "Reveal"}
                                        </Button>
                                    ) : (
                                        <span className="text-[10px] text-muted-foreground italic" title="Only super admins can retrieve the full SIN. Contact a super admin if you need this for tax filing.">
                                            super_admin only
                                        </span>
                                    )}
                                </div>
                            ) : data.kyc.id_number_provided ? (
                                <p
                                    className="text-sm text-muted-foreground mt-0.5"
                                    title="This driver entered their SIN into Stripe's own form before Spinr collected SINs in-app. Stripe never returns it (write-only), so it cannot be revealed here — use the tax-ID import or ask the driver via support to get a copy on file for the T4A."
                                >
                                    Held by Stripe only — not revealable
                                </p>
                            ) : (
                                <p className="text-sm text-muted-foreground mt-0.5">Not provided yet</p>
                            )}
                        </div>
                        <div>
                            <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">GST/HST number</p>
                            {data.kyc.gst_hst_number ? (
                                <div className="flex items-center gap-2 mt-0.5">
                                    <p className="text-sm font-medium font-mono">{data.kyc.gst_hst_number}</p>
                                    <button
                                        type="button"
                                        onClick={() => navigator.clipboard.writeText(data.kyc.gst_hst_number!)}
                                        className="text-muted-foreground hover:text-foreground"
                                        title="Copy GST/HST number"
                                    >
                                        <Copy className="h-3 w-3" />
                                    </button>
                                </div>
                            ) : (
                                <p className="text-sm text-muted-foreground mt-0.5">Not registered</p>
                            )}
                        </div>
                        <div>
                            <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">Onboarding</p>
                            <p className="text-sm font-medium mt-0.5">
                                {data.kyc.details_submitted ? "Submitted to Stripe" : "Incomplete"}
                            </p>
                        </div>
                        <div>
                            <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">Stripe ToS</p>
                            <p className="text-sm font-medium mt-0.5">
                                {data.kyc.tos_accepted_at ? fmtDateTime(data.kyc.tos_accepted_at) : "Not accepted"}
                            </p>
                        </div>
                    </div>

                    {(data.kyc.requirements_due.length > 0 || data.kyc.requirements_past_due.length > 0) && (
                        <div className="rounded-md border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/10 p-2.5">
                            <p className="text-[11px] font-semibold text-amber-700 dark:text-amber-300 mb-1">
                                {data.kyc.requirements_past_due.length > 0 && `${data.kyc.requirements_past_due.length} past due · `}
                                {data.kyc.requirements_due.length} item{data.kyc.requirements_due.length === 1 ? "" : "s"} needed from driver
                            </p>
                            <ul className="text-[11px] text-amber-700/80 dark:text-amber-300/80 space-y-0.5">
                                {[...data.kyc.requirements_past_due, ...data.kyc.requirements_due].slice(0, 6).map((req) => (
                                    <li key={req} className="font-mono">{req}</li>
                                ))}
                                {data.kyc.requirements_due.length + data.kyc.requirements_past_due.length > 6 && (
                                    <li className="text-amber-700/60 dark:text-amber-300/60 italic">…and {data.kyc.requirements_due.length + data.kyc.requirements_past_due.length - 6} more</li>
                                )}
                            </ul>
                        </div>
                    )}

                    {data.kyc.disabled_reason && (
                        <div className="rounded-md border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/10 p-2.5">
                            <p className="text-[11px] font-semibold text-red-700 dark:text-red-300">
                                Payouts disabled: <span className="font-mono">{data.kyc.disabled_reason}</span>
                            </p>
                        </div>
                    )}

                    {revealedSin && (
                        <div className="rounded-md border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/10 p-2.5">
                            <p className="text-[11px] text-amber-700 dark:text-amber-300">
                                <AlertTriangle className="h-3 w-3 inline mr-1" />
                                SIN revealed at {new Date(revealedSin.expiresAt - 30000).toLocaleTimeString()}.
                                Auto-hides in {Math.max(0, Math.ceil((revealedSin.expiresAt - Date.now()) / 1000))}s.
                                Never paste this anywhere — audit log captured the reveal.
                            </p>
                        </div>
                    )}

                    {data.kyc.last_synced_at && (
                        <p className="text-[10px] text-muted-foreground">Last synced: {fmtDateTime(data.kyc.last_synced_at)}</p>
                    )}
                </div>
            </div>

            {/* Last payout highlight */}
            {summary.last_payout && (
                <div className="rounded-xl border border-emerald-200 dark:border-emerald-800 bg-emerald-50/50 dark:bg-emerald-900/10 p-3 flex items-center gap-3">
                    <CheckCircle className="h-4 w-4 text-emerald-600 dark:text-emerald-400 shrink-0" />
                    <div className="flex-1 min-w-0">
                        <p className="text-xs text-emerald-700 dark:text-emerald-300">
                            <span className="font-semibold">Last payout:</span> {fmtMoney(summary.last_payout.amount)}
                            {summary.last_payout.bank_name && ` to ${summary.last_payout.bank_name} ••••${summary.last_payout.account_last4 || ""}`}
                        </p>
                        <p className="text-[10px] text-emerald-600/70 dark:text-emerald-400/70">{fmtDateTime(summary.last_payout.processed_at)}</p>
                    </div>
                </div>
            )}

            {/* Bonuses (quest/referral/adjustment) */}
            {bonuses.length > 0 && (
                <div className="rounded-xl border border-border overflow-x-auto">
                    <div className="px-4 py-3 border-b border-border flex items-center gap-2">
                        <DollarSign className="h-4 w-4 text-muted-foreground" />
                        <h4 className="text-sm font-semibold">Bonuses &amp; Adjustments</h4>
                        <Badge variant="outline" className="text-[10px] ml-auto">{bonuses.length} entries</Badge>
                    </div>
                    <Table>
                        <TableHeader>
                            <TableRow className="bg-muted/30 hover:bg-muted/30">
                                <TableHead className="h-9 text-[11px] uppercase tracking-wider">Date</TableHead>
                                <TableHead className="h-9 text-[11px] uppercase tracking-wider text-right">Amount</TableHead>
                                <TableHead className="h-9 text-[11px] uppercase tracking-wider">Type</TableHead>
                                <TableHead className="h-9 text-[11px] uppercase tracking-wider">Description</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {bonuses.map((b) => (
                                <TableRow key={b.id} className="hover:bg-muted/20">
                                    <TableCell className="text-xs whitespace-nowrap">{fmtDateTime(b.created_at)}</TableCell>
                                    <TableCell className="text-sm font-semibold text-right tabular-nums">{fmtMoney(b.amount)}</TableCell>
                                    <TableCell>
                                        <Badge variant="outline" className="text-[10px]">{b.kind}</Badge>
                                    </TableCell>
                                    <TableCell className="text-xs text-muted-foreground">{b.description || "—"}</TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            )}

            {/* Earnings statements — date-filtered download / email to driver */}
            <DriverStatementsPanel driverId={driverId} driverName={driverName} notify={notify} />

            {/* Payout history table */}
            <div className="rounded-xl border border-border overflow-x-auto">
                <Table>
                    <TableHeader>
                        <TableRow className="bg-muted/30 hover:bg-muted/30">
                            <SortableHead column="processed_at" sort={payoutsSort} onSort={togglePayoutsSort} className="h-9 text-[11px] uppercase tracking-wider">Date</SortableHead>
                            <SortableHead column="amount" sort={payoutsSort} onSort={togglePayoutsSort} align="right" className="h-9 text-[11px] uppercase tracking-wider">Amount</SortableHead>
                            <SortableHead column="status" sort={payoutsSort} onSort={togglePayoutsSort} className="h-9 text-[11px] uppercase tracking-wider">Status</SortableHead>
                            <TableHead className="h-9 text-[11px] uppercase tracking-wider">Type</TableHead>
                            <SortableHead column="bank_name" sort={payoutsSort} onSort={togglePayoutsSort} className="h-9 text-[11px] uppercase tracking-wider">Destination</SortableHead>
                            <SortableHead column="stripe_payout_id" sort={payoutsSort} onSort={togglePayoutsSort} className="h-9 text-[11px] uppercase tracking-wider">Stripe Ref</SortableHead>
                            <TableHead className="h-9 text-[11px] uppercase tracking-wider text-right">Action</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {payouts.length === 0 ? (
                            <TableRow>
                                <TableCell colSpan={7} className="text-center text-muted-foreground py-12">
                                    <p className="text-sm">No payouts yet.</p>
                                    <p className="text-xs mt-1">When {driverName} requests a withdrawal it will appear here.</p>
                                </TableCell>
                            </TableRow>
                        ) : sortedPayouts.slice(payoutPage * payoutPageSize, (payoutPage + 1) * payoutPageSize).map((p) => {
                            const style = PAYOUT_STATUS_STYLE[p.status] ?? { bg: "bg-muted/30", text: "text-muted-foreground", label: p.status };
                            const isRetrying = retryingPayoutId === p.id;
                            const typeLabel =
                                p.payout_type === "stripe_sync" ? "Stripe Transfer"
                                : p.payout_type === "instant" ? "Instant"
                                : p.payout_type === "legacy_import" ? "Legacy import"
                                : !p.payout_type || p.payout_type === "standard" ? "Standard"
                                : p.payout_type;
                            return (
                                <TableRow key={p.id} className="hover:bg-muted/20">
                                    <TableCell className="text-xs whitespace-nowrap">
                                        {fmtDateTime(p.processed_at || p.created_at)}
                                    </TableCell>
                                    <TableCell className="text-sm font-semibold text-right tabular-nums">
                                        {fmtMoney(p.amount)}
                                    </TableCell>
                                    <TableCell>
                                        <div className="flex flex-col gap-0.5">
                                            <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-semibold w-fit ${style.bg} ${style.text}`}>
                                                {style.label}
                                            </span>
                                            {p.status === "failed" && p.error_message && (
                                                <span className="text-[10px] text-red-600 dark:text-red-400 truncate max-w-[200px]" title={p.error_message}>{p.error_message}</span>
                                            )}
                                        </div>
                                    </TableCell>
                                    <TableCell>
                                        <Badge variant="outline" className="text-[10px]">{typeLabel}</Badge>
                                    </TableCell>
                                    <TableCell className="text-xs">
                                        {p.bank_name ? (
                                            <>
                                                <p>{p.bank_name}</p>
                                                {p.account_last4 && <p className="text-[10px] text-muted-foreground font-mono">••••{p.account_last4}</p>}
                                            </>
                                        ) : "—"}
                                    </TableCell>
                                    <TableCell>
                                        {(p.stripe_transfer_id || p.stripe_payout_id) ? (
                                            <button
                                                type="button"
                                                onClick={() => navigator.clipboard.writeText(p.stripe_transfer_id || p.stripe_payout_id || "")}
                                                title={`Copy ${p.stripe_transfer_id || p.stripe_payout_id}`}
                                                className="inline-flex items-center gap-1 text-[10px] font-mono text-muted-foreground hover:text-foreground transition-colors"
                                            >
                                                {(p.stripe_transfer_id || p.stripe_payout_id || "").slice(0, 12)}...
                                                <Copy className="h-2.5 w-2.5" />
                                            </button>
                                        ) : (
                                            <span className="text-[10px] text-muted-foreground">—</span>
                                        )}
                                    </TableCell>
                                    <TableCell className="text-right">
                                        {p.status === "failed" ? (
                                            <Button
                                                size="xs"
                                                variant="outline"
                                                className="h-7 text-[11px]"
                                                disabled={isRetrying}
                                                onClick={() => onRetry(p.id)}
                                            >
                                                {isRetrying ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
                                                Retry
                                            </Button>
                                        ) : (
                                            <span className="text-[10px] text-muted-foreground">—</span>
                                        )}
                                    </TableCell>
                                </TableRow>
                            );
                        })}
                    </TableBody>
                </Table>
            </div>
            {payouts.length > 0 && (
                <div className="flex items-center justify-between gap-3 flex-wrap">
                    <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground">Show</span>
                        <Select value={String(payoutPageSize)} onValueChange={(v) => { setPayoutPageSize(Number(v)); setPayoutPage(0); }}>
                            <SelectTrigger className="h-8 text-xs w-[70px]">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                {[25, 50, 100].map(n => (
                                    <SelectItem key={n} value={String(n)} className="text-xs">{n}</SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <span className="text-xs text-muted-foreground">per page</span>
                    </div>
                    {sortedPayouts.length > payoutPageSize && (
                        <Pagination
                            page={payoutPage}
                            pageSize={payoutPageSize}
                            hasNextPage={sortedPayouts.length > (payoutPage + 1) * payoutPageSize}
                            totalCount={sortedPayouts.length}
                            onPageChange={setPayoutPage}
                        />
                    )}
                </div>
            )}
        </div>
    );
}

function DriverReferralsTab({ data, loading, fmtDate }: {
    data: DriverReferralSummary | null;
    loading: boolean;
    fmtDate: (d: string) => string;
}) {
    const [refPage, setRefPage] = useState(0);
    const [refPageSize, setRefPageSize] = useState<number>(25);

    if (loading) {
        return <div className="text-sm text-muted-foreground py-10 text-center">Loading referrals…</div>;
    }
    if (!data) {
        return <div className="text-sm text-muted-foreground py-10 text-center">No referral data.</div>;
    }
    const referees = data.referees || [];
    const pagedReferees = referees.slice(refPage * refPageSize, (refPage + 1) * refPageSize);
    return (
        <div className="space-y-5">
            {/* Summary */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div className="rounded-xl border border-border/50 p-3">
                    <p className="text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Referrals</p>
                    <p className="text-xl font-bold mt-0.5">{data.total_referrals}</p>
                </div>
                <div className="rounded-xl border border-border/50 p-3">
                    <p className="text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Rewarded</p>
                    <p className="text-xl font-bold mt-0.5 text-emerald-600 dark:text-emerald-400">{data.qualified_referrals}</p>
                </div>
                <div className="rounded-xl border border-border/50 p-3">
                    <p className="text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Pending</p>
                    <p className="text-xl font-bold mt-0.5 text-amber-600 dark:text-amber-400">{data.pending_referrals}</p>
                </div>
                <div className="rounded-xl border border-border/50 p-3">
                    <p className="text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Earned</p>
                    <p className="text-xl font-bold mt-0.5">{formatCurrency(data.referral_earnings)}</p>
                </div>
            </div>
            <p className="text-xs text-muted-foreground">
                Code <span className="font-mono font-semibold">{data.referral_code}</span> · reward {formatCurrency(data.reward_amount)} once a referee completes {data.rides_required} rides.
            </p>
            {data.referred_by ? (
                <p className="text-xs text-muted-foreground">
                    Referred by <span className="font-semibold text-foreground">{data.referred_by.name}</span> ({data.referred_by.code})
                </p>
            ) : null}

            {/* Referee list */}
            {referees.length === 0 ? (
                <p className="text-sm text-muted-foreground py-6 text-center">No one has signed up with this driver&apos;s code yet.</p>
            ) : (
                <>
                <div className="space-y-2">
                    {pagedReferees.map((r, i) => {
                        const pct = Math.min(100, Math.round(((r.completed_rides || 0) / (r.rides_required || 1)) * 100));
                        return (
                            <div key={i} className="rounded-xl border border-border/50 p-3 flex items-center gap-3">
                                <div className="flex-1 min-w-0">
                                    <div className="flex items-center gap-2">
                                        <p className="text-sm font-medium truncate">{r.name}</p>
                                        <span className="text-[11px] text-muted-foreground">{fmtDate(r.referred_at)}</span>
                                    </div>
                                    {r.qualified ? (
                                        <p className="text-xs text-emerald-600 dark:text-emerald-400 font-medium mt-0.5">Reward earned</p>
                                    ) : (
                                        <>
                                            <p className="text-xs text-muted-foreground mt-0.5">
                                                {r.completed_rides}/{r.rides_required} rides
                                                {r.rides_remaining > 0 ? ` · ${r.rides_remaining} to go` : ""}
                                                {!r.is_driver ? " · not a driver yet" : ""}
                                            </p>
                                            <div className="h-1.5 rounded-full bg-muted overflow-hidden mt-1.5 max-w-[200px]">
                                                <div className="h-full bg-primary rounded-full" style={{ width: `${pct}%` }} />
                                            </div>
                                        </>
                                    )}
                                </div>
                                <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wide shrink-0 ${r.qualified ? "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300" : "bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300"}`}>
                                    {r.qualified ? "Earned" : "In progress"}
                                </span>
                            </div>
                        );
                    })}
                </div>
                <div className="flex items-center justify-between gap-3 flex-wrap">
                    <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground">Show</span>
                        <Select value={String(refPageSize)} onValueChange={(v) => { setRefPageSize(Number(v)); setRefPage(0); }}>
                            <SelectTrigger className="h-8 text-xs w-[70px]">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                {[25, 50, 100].map(n => (
                                    <SelectItem key={n} value={String(n)} className="text-xs">{n}</SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <span className="text-xs text-muted-foreground">per page</span>
                    </div>
                    {referees.length > refPageSize && (
                        <Pagination
                            page={refPage}
                            pageSize={refPageSize}
                            hasNextPage={referees.length > (refPage + 1) * refPageSize}
                            totalCount={referees.length}
                            onPageChange={setRefPage}
                        />
                    )}
                </div>
                </>
            )}
        </div>
    );
}

const TRAINING_STATUS_STYLES: Record<string, string> = {
    completed: "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300",
    in_progress: "bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300",
    registered: "bg-violet-100 dark:bg-violet-900/30 text-violet-700 dark:text-violet-300",
    invited: "bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300",
    not_invited: "bg-muted text-muted-foreground",
};

function DriverTrainingTab({ data, loading, error, onRefresh, fmtDate }: {
    data: DriverTraining | null;
    loading: boolean;
    error: string | null;
    onRefresh: () => void;
    fmtDate: (d: string) => string;
}) {
    if (loading) {
        return <div className="text-sm text-muted-foreground py-10 text-center">Loading training data from the LMS…</div>;
    }
    if (error) {
        return (
            <div className="py-10 text-center space-y-3">
                <p className="text-sm text-muted-foreground">{error}</p>
                <Button variant="outline" size="sm" onClick={onRefresh}><RefreshCw className="w-3.5 h-3.5 mr-1.5" />Retry</Button>
            </div>
        );
    }
    if (!data) {
        return <div className="text-sm text-muted-foreground py-10 text-center">No training data.</div>;
    }
    if (!data.matched) {
        return (
            <div className="py-10 text-center space-y-3">
                <GraduationCap className="w-8 h-8 mx-auto text-muted-foreground/50" />
                <p className="text-sm text-muted-foreground">
                    {data.reason === "no_phone"
                        ? "This driver has no phone number on file, so they can't be matched against the LMS."
                        : `No LMS driver record matches this phone number${data.phone_last4 ? ` (ending in ${data.phone_last4})` : ""}.`}
                </p>
                <Button variant="outline" size="sm" onClick={onRefresh}><RefreshCw className="w-3.5 h-3.5 mr-1.5" />Refresh</Button>
            </div>
        );
    }

    const lms = data.lms!;
    const t = lms.training;
    const statusLabel = (t.status || "unknown").replace(/_/g, " ");
    const pct = Math.max(0, Math.min(100, Math.round(t.completion_percentage ?? 0)));
    const quizAttempts = lms.history?.quiz_attempts || [];
    const communications = lms.history?.communications || [];

    return (
        <div className="space-y-5">
            {/* Status summary */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div className="rounded-xl border border-border/50 p-3">
                    <p className="text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Training Status</p>
                    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wide mt-1.5 ${TRAINING_STATUS_STYLES[t.status] || "bg-muted text-muted-foreground"}`}>
                        {statusLabel}
                    </span>
                </div>
                <div className="rounded-xl border border-border/50 p-3">
                    <p className="text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Registered</p>
                    <p className="text-sm font-semibold mt-1">
                        {t.registered ? `Yes${t.registered_at ? ` · ${fmtDate(t.registered_at)}` : ""}` : "No"}
                    </p>
                </div>
                <div className="rounded-xl border border-border/50 p-3">
                    <p className="text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Completion</p>
                    <p className="text-xl font-bold mt-0.5">{pct}%</p>
                    <div className="h-1.5 rounded-full bg-muted overflow-hidden mt-1.5">
                        <div className={`h-full rounded-full ${pct >= 100 ? "bg-emerald-500" : "bg-primary"}`} style={{ width: `${pct}%` }} />
                    </div>
                </div>
                <div className="rounded-xl border border-border/50 p-3">
                    <p className="text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Completed</p>
                    <p className="text-sm font-semibold mt-1">{t.completed_at ? fmtDate(t.completed_at) : "—"}</p>
                </div>
            </div>
            <p className="text-xs text-muted-foreground flex items-center gap-2">
                Matched by phone{data.phone_last4 ? ` ending in ${data.phone_last4}` : ""} · LMS record for <span className="font-semibold text-foreground">{lms.driver.full_name}</span>
                <button type="button" onClick={onRefresh} className="inline-flex items-center gap-1 text-primary hover:underline" title="Bypass the cache and re-fetch from the LMS">
                    <RefreshCw className="w-3 h-3" />Refresh
                </button>
            </p>

            {/* Certificates */}
            <div>
                <h4 className="text-sm font-semibold flex items-center gap-1.5 mb-2"><Award className="w-4 h-4 text-muted-foreground" />Certificates</h4>
                {lms.certificates.length === 0 ? (
                    <p className="text-sm text-muted-foreground py-4 text-center">No certificates issued yet.</p>
                ) : (
                    <div className="space-y-2">
                        {lms.certificates.map((c, i) => (
                            <div key={i} className="rounded-xl border border-border/50 p-3 flex items-center gap-3">
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-mono font-semibold truncate">{c.certificate_number}</p>
                                    <p className="text-xs text-muted-foreground mt-0.5">
                                        {c.course_title}
                                        {c.final_quiz_score != null ? ` · final score ${Math.round(Number(c.final_quiz_score))}%` : ""}
                                        {` · issued ${fmtDate(c.issued_at)}`}
                                        {c.expires_at ? ` · expires ${fmtDate(c.expires_at)}` : ""}
                                    </p>
                                </div>
                                <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wide shrink-0 ${c.status === "active" ? "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300" : "bg-rose-100 dark:bg-rose-900/30 text-rose-700 dark:text-rose-300"}`}>
                                    {c.status}
                                </span>
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {/* Courses */}
            {t.courses.length > 0 && (
                <div>
                    <h4 className="text-sm font-semibold flex items-center gap-1.5 mb-2"><GraduationCap className="w-4 h-4 text-muted-foreground" />Courses</h4>
                    <div className="space-y-2">
                        {t.courses.map((c, i) => {
                            const coursePct = Math.max(0, Math.min(100, Math.round(c.progress ?? 0)));
                            return (
                                <div key={i} className="rounded-xl border border-border/50 p-3">
                                    <div className="flex items-center justify-between gap-2">
                                        <p className="text-sm font-medium truncate">{c.course_title || "Course"}</p>
                                        <span className="text-[11px] text-muted-foreground shrink-0">{coursePct}% · {(c.status || "").replace(/_/g, " ")}</span>
                                    </div>
                                    <div className="h-1.5 rounded-full bg-muted overflow-hidden mt-1.5">
                                        <div className={`h-full rounded-full ${coursePct >= 100 ? "bg-emerald-500" : "bg-primary"}`} style={{ width: `${coursePct}%` }} />
                                    </div>
                                    <p className="text-[11px] text-muted-foreground mt-1">
                                        Enrolled {c.enrolled_at ? fmtDate(c.enrolled_at) : "—"}
                                        {c.completed_at ? ` · completed ${fmtDate(c.completed_at)}` : ""}
                                    </p>
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}

            {/* History */}
            <div>
                <h4 className="text-sm font-semibold flex items-center gap-1.5 mb-2"><Clock className="w-4 h-4 text-muted-foreground" />History</h4>
                {quizAttempts.length === 0 && communications.length === 0 ? (
                    <p className="text-sm text-muted-foreground py-4 text-center">No training history yet.</p>
                ) : (
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                        <div>
                            <p className="text-[11px] uppercase tracking-wide text-muted-foreground font-medium mb-1.5">Quiz Attempts</p>
                            {quizAttempts.length === 0 ? (
                                <p className="text-xs text-muted-foreground">None.</p>
                            ) : (
                                <div className="space-y-1.5">
                                    {quizAttempts.map((q, i) => (
                                        <div key={i} className="flex items-center justify-between gap-2 rounded-lg border border-border/50 px-2.5 py-1.5">
                                            <span className="text-xs truncate">{q.quiz_title || "Quiz"} · {Math.round(Number(q.score))}%</span>
                                            <span className="flex items-center gap-1.5 shrink-0">
                                                <span className={`text-[10px] font-bold uppercase ${q.passed ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}`}>{q.passed ? "Pass" : "Fail"}</span>
                                                <span className="text-[10px] text-muted-foreground">{fmtDate(q.attempted_at)}</span>
                                            </span>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                        <div>
                            <p className="text-[11px] uppercase tracking-wide text-muted-foreground font-medium mb-1.5">Communications</p>
                            {communications.length === 0 ? (
                                <p className="text-xs text-muted-foreground">None.</p>
                            ) : (
                                <div className="space-y-1.5">
                                    {communications.map((m, i) => (
                                        <div key={i} className="flex items-center justify-between gap-2 rounded-lg border border-border/50 px-2.5 py-1.5">
                                            <span className="text-xs truncate">{(m.message_type || "message").replace(/_/g, " ")} ({m.communication_type})</span>
                                            <span className="flex items-center gap-1.5 shrink-0">
                                                <span className="text-[10px] text-muted-foreground uppercase">{m.status}</span>
                                                <span className="text-[10px] text-muted-foreground">{fmtDate(m.sent_at)}</span>
                                            </span>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}

function DriverRidesTab({ rides, loading, driverName, fmtDate }: {
    rides: any[];
    loading: boolean;
    driverName: string;
    fmtDate: (d: string) => string;
}) {
    const [statusFilter, setStatusFilter] = useState<string>("all");
    const [search, setSearch] = useState("");
    const [sortKey, setSortKey] = useState<RidesSortKey>("created_at");
    const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
    const [page, setPage] = useState(0);
    const [pageSize, setPageSize] = useState<number>(25);
    const [dateFrom, setDateFrom] = useState("");
    const [dateTo, setDateTo] = useState("");

    useEffect(() => { setPage(0); }, [statusFilter, search, sortKey, sortDir, dateFrom, dateTo, pageSize]);

    const fmtDuration = (s?: number) => {
        if (!s) return "—";
        const m = Math.round(s / 60);
        return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${m % 60}m`;
    };

    const riderDisplay = (r: any) => {
        const name = (r.rider_name || "").trim();
        if (name) return name;
        if (r.rider_id) return `Rider ${String(r.rider_id).slice(0, 6)}`;
        return "Unknown rider";
    };

    const statusCounts = useMemo(() => {
        const c: Record<string, number> = { all: rides.length };
        for (const r of rides) {
            const s = r.status || "unknown";
            c[s] = (c[s] || 0) + 1;
        }
        return c;
    }, [rides]);

    const statusOptions = useMemo(() => {
        const seen = new Set<string>();
        for (const r of rides) seen.add(r.status || "unknown");
        return [
            { value: "all", label: "All statuses" },
            ...Array.from(seen).sort().map(s => ({
                value: s,
                label: RIDE_STATUS_STYLE[s]?.label || s,
            })),
        ];
    }, [rides]);

    const processed = useMemo(() => {
        const q = search.trim().toLowerCase();
        let out = rides;
        if (statusFilter !== "all") out = out.filter(r => r.status === statusFilter);
        if (dateFrom) {
            const from = new Date(dateFrom);
            from.setHours(0, 0, 0, 0);
            out = out.filter(r => r.created_at && new Date(r.created_at) >= from);
        }
        if (dateTo) {
            const to = new Date(dateTo);
            to.setHours(23, 59, 59, 999);
            out = out.filter(r => r.created_at && new Date(r.created_at) <= to);
        }
        if (q) out = out.filter(r => {
            const haystack = `${riderDisplay(r)} ${r.id || ""} ${r.pickup_address || ""} ${r.dropoff_address || ""}`.toLowerCase();
            return haystack.includes(q);
        });
        const sorted = [...out].sort((a, b) => {
            let av: any, bv: any;
            if (sortKey === "rider_name") { av = riderDisplay(a).toLowerCase(); bv = riderDisplay(b).toLowerCase(); }
            else if (sortKey === "status") { av = a.status || ""; bv = b.status || ""; }
            else if (sortKey === "total_fare") { av = Number(a.total_fare ?? a.fare_amount ?? a.base_fare ?? 0); bv = Number(b.total_fare ?? b.fare_amount ?? b.base_fare ?? 0); }
            else if (sortKey === "tip_amount") { av = Number(a.tip_amount ?? 0); bv = Number(b.tip_amount ?? 0); }
            else if (sortKey === "distance_km") { av = Number(a.distance_km ?? 0); bv = Number(b.distance_km ?? 0); }
            else if (sortKey === "duration_seconds") { av = Number(a.duration_seconds ?? 0); bv = Number(b.duration_seconds ?? 0); }
            else { av = a.created_at || ""; bv = b.created_at || ""; }
            if (av < bv) return sortDir === "asc" ? -1 : 1;
            if (av > bv) return sortDir === "asc" ? 1 : -1;
            return 0;
        });
        return sorted;
    }, [rides, statusFilter, search, sortKey, sortDir, dateFrom, dateTo]);

    const paged = processed.slice(page * pageSize, (page + 1) * pageSize);
    const hasNextPage = processed.length > (page + 1) * pageSize;

    const handleSort = (k: RidesSortKey) => {
        if (sortKey === k) setSortDir(d => d === "asc" ? "desc" : "asc");
        else {
            setSortKey(k);
            setSortDir(k === "rider_name" ? "asc" : "desc");
        }
    };

    const handleExportRides = () => {
        if (processed.length === 0) return;
        const cols = [
            { key: "ride_code", label: "Ride Code" },
            { label: "Date", value: (r: any) => r.created_at ? new Date(r.created_at).toLocaleString() : "" },
            { label: "Rider", value: (r: any) => riderDisplay(r) },
            { label: "Driver", value: () => driverName },
            { key: "pickup_address", label: "Pickup" },
            { key: "dropoff_address", label: "Dropoff" },
            { key: "status", label: "Status" },
            { label: "Distance (km)", value: (r: any) => r.distance_km != null ? Number(r.distance_km).toFixed(1) : "" },
            { label: "Duration (min)", value: (r: any) => r.duration_seconds ? Math.round(r.duration_seconds / 60) : "" },
            { label: "Tip", value: (r: any) => r.tip_amount != null && Number(r.tip_amount) > 0 ? Number(r.tip_amount).toFixed(2) : "" },
            { label: "Fare", value: (r: any) => { const f = r.total_fare ?? r.fare_amount ?? r.base_fare; return f != null ? Number(f).toFixed(2) : ""; } },
        ];
        const safeName = driverName.replace(/[^a-zA-Z0-9]/g, "_").toLowerCase();
        exportToCsv(`driver_rides_${safeName}`, processed, cols);
    };

    const SortIcon = ({ col }: { col: RidesSortKey }) => {
        if (sortKey !== col) return <ArrowUpDown className="h-3 w-3 opacity-30 inline ml-1" />;
        return sortDir === "asc" ? <ArrowUp className="h-3 w-3 inline ml-1" /> : <ArrowDown className="h-3 w-3 inline ml-1" />;
    };

    if (loading) return (
        <div className="space-y-2.5 animate-pulse">
            <div className="h-9 w-full rounded-lg bg-muted" />
            {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="h-12 rounded-lg bg-muted" />
            ))}
        </div>
    );

    if (rides.length === 0) return (
        <div className="py-16 text-center text-muted-foreground">
            <Car className="h-10 w-10 mx-auto mb-3 opacity-30" />
            <p className="text-sm font-medium">No rides yet</p>
            <p className="text-xs mt-1">{driverName} has not completed any trips.</p>
        </div>
    );

    return (
        <div className="space-y-3">
            <div className="flex items-center gap-2 flex-wrap">
                <div className="flex items-center gap-1.5">
                    <Search className="h-4 w-4 text-muted-foreground" />
                    <Input
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        placeholder="Search rider, ride id, address"
                        className="h-8 text-xs w-[260px]"
                    />
                </div>
                <Select value={statusFilter} onValueChange={setStatusFilter}>
                    <SelectTrigger className="h-8 text-xs w-[170px]">
                        <SelectValue placeholder="Status" />
                    </SelectTrigger>
                    <SelectContent>
                        {statusOptions.map((opt: { value: string; label: string }) => (
                            <SelectItem key={opt.value} value={opt.value} className="text-xs">
                                {opt.label}{opt.value !== "all" && statusCounts[opt.value] != null ? ` · ${statusCounts[opt.value]}` : opt.value === "all" ? ` · ${statusCounts.all}` : ""}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
                <div className="flex items-center gap-1.5">
                    <CalendarRange className="h-4 w-4 text-muted-foreground" />
                    <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="h-8 text-xs w-[130px]" />
                    <span className="text-xs text-muted-foreground">to</span>
                    <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="h-8 text-xs w-[130px]" />
                </div>
                <span className="ml-auto text-xs text-muted-foreground tabular-nums">
                    {processed.length} of {rides.length}
                </span>
                <Button variant="outline" size="sm" className="h-8 text-xs gap-1.5" onClick={handleExportRides} disabled={processed.length === 0}>
                    <Download className="h-3.5 w-3.5" />
                    Export CSV
                </Button>
            </div>

            <div className="rounded-xl border border-border overflow-x-auto">
                <Table>
                    <TableHeader>
                        <TableRow className="bg-muted/30 hover:bg-muted/30">
                            <TableHead className="h-9 text-[11px] uppercase tracking-wider cursor-pointer select-none" onClick={() => handleSort("created_at")}>
                                Date<SortIcon col="created_at" />
                            </TableHead>
                            <TableHead className="h-9 text-[11px] uppercase tracking-wider cursor-pointer select-none" onClick={() => handleSort("rider_name")}>
                                Rider<SortIcon col="rider_name" />
                            </TableHead>
                            <TableHead className="h-9 text-[11px] uppercase tracking-wider">Driver</TableHead>
                            <TableHead className="h-9 text-[11px] uppercase tracking-wider">Route</TableHead>
                            <TableHead className="h-9 text-[11px] uppercase tracking-wider cursor-pointer select-none" onClick={() => handleSort("status")}>
                                Status<SortIcon col="status" />
                            </TableHead>
                            <TableHead className="h-9 text-[11px] uppercase tracking-wider text-right cursor-pointer select-none" onClick={() => handleSort("distance_km")}>
                                Distance<SortIcon col="distance_km" />
                            </TableHead>
                            <TableHead className="h-9 text-[11px] uppercase tracking-wider text-right cursor-pointer select-none" onClick={() => handleSort("duration_seconds")}>
                                Duration<SortIcon col="duration_seconds" />
                            </TableHead>
                            <TableHead className="h-9 text-[11px] uppercase tracking-wider text-right cursor-pointer select-none" onClick={() => handleSort("tip_amount")}>
                                Tip<SortIcon col="tip_amount" />
                            </TableHead>
                            <TableHead className="h-9 text-[11px] uppercase tracking-wider text-right cursor-pointer select-none" onClick={() => handleSort("total_fare")}>
                                Fare<SortIcon col="total_fare" />
                            </TableHead>
                            <TableHead className="h-9 text-[11px] uppercase tracking-wider">Ride</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {paged.length === 0 ? (
                            <TableRow>
                                <TableCell colSpan={10} className="text-center text-muted-foreground py-12">
                                    <p className="text-sm">No rides match this filter.</p>
                                </TableCell>
                            </TableRow>
                        ) : paged.map((r: any) => {
                            const style = RIDE_STATUS_STYLE[r.status] ?? { bg: "bg-muted/30", text: "text-muted-foreground", label: r.status };
                            const totalFare = r.total_fare ?? r.fare_amount ?? r.base_fare;
                            const tip = r.tip_amount;
                            const hasTip = tip != null && Number(tip) > 0;
                            const dt = r.created_at ? new Date(r.created_at) : null;
                            const rider = riderDisplay(r);
                            return (
                                <TableRow key={r.id} className="hover:bg-muted/20">
                                    <TableCell className="text-xs tabular-nums whitespace-nowrap">
                                        {dt ? (
                                            <>
                                                <div>{fmtDate(r.created_at)}</div>
                                                <div className="text-[10px] text-muted-foreground">{dt.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}</div>
                                            </>
                                        ) : "—"}
                                    </TableCell>
                                    <TableCell className="text-xs">
                                        <span className="font-medium truncate block max-w-[150px]" title={rider}>{rider}</span>
                                    </TableCell>
                                    <TableCell className="text-xs text-muted-foreground truncate max-w-[150px]" title={driverName}>{driverName}</TableCell>
                                    <TableCell className="text-xs">
                                        <div className="max-w-[220px]">
                                            <p className="truncate text-foreground" title={r.pickup_address}>{r.pickup_address || "—"}</p>
                                            <p className="truncate text-muted-foreground text-[10px]" title={r.dropoff_address}>{r.dropoff_address ? `→ ${r.dropoff_address}` : ""}</p>
                                        </div>
                                    </TableCell>
                                    <TableCell>
                                        <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-semibold ${style.bg} ${style.text}`}>
                                            {style.label}
                                        </span>
                                    </TableCell>
                                    <TableCell className="text-xs text-right tabular-nums">
                                        {r.distance_km != null ? `${Number(r.distance_km).toFixed(1)} km` : "—"}
                                    </TableCell>
                                    <TableCell className="text-xs text-right tabular-nums">
                                        {fmtDuration(r.duration_seconds)}
                                    </TableCell>
                                    <TableCell className="text-xs text-right tabular-nums">
                                        {hasTip ? <span className="text-emerald-600 dark:text-emerald-400 font-medium">${Number(tip).toFixed(2)}</span> : <span className="text-muted-foreground">—</span>}
                                    </TableCell>
                                    <TableCell className="text-sm text-right font-semibold tabular-nums">
                                        {totalFare != null ? `$${Number(totalFare).toFixed(2)}` : "—"}
                                    </TableCell>
                                    <TableCell>
                                        {(() => {
                                            // Prefer the human-readable ride_code (SPR-XXXXXX,
                                            // canonical short identifier — see migration 40).
                                            // Fall back to a UUID prefix only for rides predating
                                            // the backfill, which shouldn't happen in practice.
                                            const code = r.ride_code ? String(r.ride_code).toLowerCase() : `#${String(r.id).slice(0, 8)}`;
                                            const copyTarget = r.ride_code || r.id;
                                            return (
                                                <button
                                                    type="button"
                                                    onClick={() => navigator.clipboard.writeText(copyTarget)}
                                                    title={`Click to copy ${copyTarget}\nFull ID: ${r.id}`}
                                                    className="inline-flex items-center gap-1 text-[10px] font-mono text-muted-foreground hover:text-foreground transition-colors"
                                                >
                                                    {code}
                                                    <Copy className="h-2.5 w-2.5" />
                                                </button>
                                            );
                                        })()}
                                    </TableCell>
                                </TableRow>
                            );
                        })}
                    </TableBody>
                </Table>
            </div>

            <div className="flex items-center justify-between gap-3 flex-wrap">
                <div className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground">Show</span>
                    <Select value={String(pageSize)} onValueChange={(v) => setPageSize(Number(v))}>
                        <SelectTrigger className="h-8 text-xs w-[70px]">
                            <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                            {DRIVER_RIDES_PAGE_SIZE_OPTIONS.map(n => (
                                <SelectItem key={n} value={String(n)} className="text-xs">{n}</SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                    <span className="text-xs text-muted-foreground">per page</span>
                </div>
                {processed.length > pageSize && (
                    <Pagination
                        page={page}
                        pageSize={pageSize}
                        hasNextPage={hasNextPage}
                        totalCount={processed.length}
                        onPageChange={setPage}
                    />
                )}
            </div>
        </div>
    );
}

function QuickStat({ icon: Icon, color, bg, label, value, sub, subTone }: { icon: any; color: string; bg: string; label: string; value: string; sub?: string; subTone?: "amber" | "muted" }) {
    const subClass = subTone === "amber" ? "text-amber-600 dark:text-amber-400" : "text-muted-foreground";
    return (
        <div className={`${bg} rounded-xl p-3 text-center`}>
            <Icon className={`h-4 w-4 ${color} mx-auto mb-1`} />
            <p className="text-sm font-bold">{value}</p>
            <p className="text-[10px] text-muted-foreground">{label}</p>
            {sub && <p className={`text-[10px] mt-0.5 font-medium ${subClass}`}>{sub}</p>}
        </div>
    );
}

function DetailSection({ title, icon: Icon, children }: { title: string; icon: any; children: React.ReactNode }) {
    return (
        <div>
            <div className="flex items-center gap-2 mb-2.5">
                <div className="w-6 h-6 rounded-md bg-muted/60 flex items-center justify-center shrink-0">
                    <Icon className="h-3.5 w-3.5 text-muted-foreground" />
                </div>
                <h4 className="text-sm font-semibold tracking-tight">{title}</h4>
            </div>
            <div className="bg-muted/10 rounded-xl p-3.5 border border-border/60">
                {children}
            </div>
        </div>
    );
}

/* ── Work authorization ───────────────────────────────────────────────
 * One canonical status. `is_permanent_resident` / `is_citizen` are derived
 * columns, not independently editable fields — they used to render as three
 * separate "Unknown" rows that could disagree with each other.
 * Mirrors routes/admin/drivers.py::work_authorization_view; the backend ships
 * the same projection as `work_authorization` on every driver row and this
 * local copy only recomputes it after an optimistic post-save merge. */
const WORK_AUTH_LABELS: Record<string, string> = {
    citizen: "Canadian citizen",
    permanent_resident: "Permanent resident",
    indefinite: "Work permit — no expiry",
    expiring: "Work permit — expires",
    unknown: "Unknown",
};
type WorkAuthView = { status: string; label: string; citizen: string; permanent_resident: string; expires_at: string | null };

function workAuthLocal(driver: any): WorkAuthView {
    const raw = String(driver?.work_authorization_status || "").toLowerCase();
    let status = WORK_AUTH_LABELS[raw] ? raw : "unknown";
    // Legacy rows imported before the status column existed carry only the
    // booleans — promote them so those drivers do not read as "Unknown".
    if (status === "unknown") {
        if (driver?.is_citizen === true) status = "citizen";
        else if (driver?.is_permanent_resident === true) status = "permanent_resident";
    }
    const flag = (matches: boolean) => (status === "unknown" ? "unknown" : matches ? "yes" : "not_applicable");
    return {
        status,
        label: WORK_AUTH_LABELS[status],
        citizen: flag(status === "citizen"),
        permanent_resident: flag(status === "permanent_resident"),
        expires_at: status === "expiring" ? (driver?.work_eligibility_expiry_date ?? null) : null,
    };
}

/** Backend projection when present, local mirror otherwise. */
const workAuth = (driver: any): WorkAuthView => (driver?.work_authorization as WorkAuthView) || workAuthLocal(driver);

const WORK_AUTH_FLAG_LABELS: Record<string, string> = { yes: "Yes", not_applicable: "Not applicable", unknown: "Unknown" };

function DetailField({ icon: Icon, label, value, mono }: { icon: any; label: string; value: string; mono?: boolean }) {
    return (
        <div className="bg-background border border-border/60 rounded-lg px-3 py-2.5 flex items-center gap-3 min-w-0">
            <div className="shrink-0 w-7 h-7 rounded-md bg-muted/50 flex items-center justify-center">
                <Icon className="h-3.5 w-3.5 text-muted-foreground" />
            </div>
            <div className="min-w-0">
                <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide leading-none">{label}</p>
                <p className={`text-sm font-medium truncate leading-tight mt-1 ${mono ? "font-mono tracking-wide text-xs" : ""}`}>{value}</p>
            </div>
        </div>
    );
}

function CopyableField({ icon: Icon, label, value }: { icon: any; label: string; value?: string }) {
    const { toast } = useToast();
    const display = value || "—";
    const canCopy = !!value;
    const copy = () => {
        if (!canCopy) return;
        navigator.clipboard.writeText(value!);
        toast({ description: `${label} copied`, duration: 1500 });
    };
    return (
        <button
            type="button"
            onClick={copy}
            disabled={!canCopy}
            className="text-left bg-background border border-border/60 rounded-lg px-3 py-2.5 flex items-center gap-3 min-w-0 w-full hover:bg-muted/30 transition-colors group disabled:cursor-default disabled:opacity-70 disabled:hover:bg-transparent"
        >
            <div className="shrink-0 w-7 h-7 rounded-md bg-muted/50 flex items-center justify-center">
                <Icon className="h-3.5 w-3.5 text-muted-foreground" />
            </div>
            <div className="min-w-0 flex-1">
                <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide leading-none">{label}</p>
                <p className="text-sm font-medium truncate leading-tight mt-1">{display}</p>
            </div>
            {canCopy && <Copy className="h-3.5 w-3.5 text-muted-foreground/30 group-hover:text-muted-foreground shrink-0 transition-colors" />}
        </button>
    );
}

function EditField({ label, value, onChange, type = "text", placeholder, hint }: { label: string; value: string; onChange: (v: string) => void; type?: string; placeholder?: string; hint?: string }) {
    return (
        <div>
            <label className="text-[11px] text-muted-foreground mb-1 block">{label}</label>
            <Input type={type} value={value} placeholder={placeholder} onChange={e => onChange(e.target.value)} className="h-9 text-sm" />
            {hint && <p className="text-[10px] text-muted-foreground mt-1">{hint}</p>}
        </div>
    );
}

function EditBooleanField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
    return (
        <div>
            <label className="text-[11px] text-muted-foreground mb-1 block">{label}</label>
            <Select value={value || "unknown"} onValueChange={v => onChange(v === "unknown" ? "" : v)}>
                <SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger>
                <SelectContent>
                    <SelectItem value="unknown">Unknown</SelectItem>
                    <SelectItem value="true">Yes</SelectItem>
                    <SelectItem value="false">No</SelectItem>
                </SelectContent>
            </Select>
        </div>
    );
}

type DocSummary = {
    expiry?: string;
    docStatus: "approved" | "pending" | "rejected" | "missing";
    expiryIsLegacy: boolean;
};

function DocExpirySummaryCard({ label, summary }: { label: string; summary: DocSummary }) {
    const { expiry, docStatus } = summary;
    const fmt = (d: string) => { try { return new Date(d).toLocaleDateString("en-CA", { month: "short", day: "numeric", year: "numeric" }); } catch { return d; } };
    const isExpired = expiry ? new Date(expiry) < new Date() : false;
    const daysUntil = expiry ? Math.ceil((new Date(expiry).getTime() - Date.now()) / 86400000) : null;
    const isExpiringSoon = daysUntil !== null && daysUntil > 0 && daysUntil <= 30;

    // Pick palette + copy from the highest-priority signal: status first,
    // then expiry health. Crucially, an approved doc that's missing an
    // expiry is treated as amber ("Expiry not recorded") instead of being
    // greyed-out like an upload that was never made — those are very
    // different operational states.
    let palette: "neutral" | "emerald" | "amber" | "red";
    let primary: string;
    let secondary: string | null = null;

    if (docStatus === "missing") {
        palette = "neutral";
        primary = "Not uploaded";
        secondary = "Driver has not provided this document yet";
    } else if (docStatus === "rejected") {
        palette = "red";
        primary = "Re-upload needed";
        secondary = "Previous upload was rejected";
    } else if (docStatus === "pending") {
        palette = "amber";
        primary = "Pending review";
        secondary = "Waiting for admin approval";
    } else if (docStatus === "approved" && !expiry) {
        // Approved but no expiry on file. Often a legacy approval predating
        // the per-doc expiry column; re-approve to record one.
        palette = "amber";
        primary = "Approved";
        secondary = "Expiry not recorded — re-approve to set";
    } else if (isExpired) {
        palette = "red";
        primary = "Expired";
        secondary = `Expired ${fmt(expiry!)}`;
    } else if (isExpiringSoon) {
        palette = "amber";
        primary = fmt(expiry!);
        secondary = `${daysUntil} day${daysUntil !== 1 ? "s" : ""} remaining`;
    } else {
        palette = "emerald";
        primary = fmt(expiry!);
        secondary = daysUntil !== null ? `${daysUntil} days remaining` : null;
    }

    const styles = {
        neutral: { bg: "bg-muted/30 border-border", dot: "bg-gray-300", primary: "text-muted-foreground", secondary: "text-muted-foreground" },
        emerald: { bg: "bg-emerald-50 dark:bg-emerald-900/10 border-emerald-200 dark:border-emerald-800", dot: "bg-emerald-500", primary: "text-emerald-700 dark:text-emerald-300", secondary: "text-emerald-600/70 dark:text-emerald-400/70" },
        amber:   { bg: "bg-amber-50 dark:bg-amber-900/10 border-amber-200 dark:border-amber-800",       dot: "bg-amber-500",   primary: "text-amber-700 dark:text-amber-300",   secondary: "text-amber-600/80 dark:text-amber-400/80" },
        red:     { bg: "bg-red-50 dark:bg-red-900/10 border-red-200 dark:border-red-800",               dot: "bg-red-500",     primary: "text-red-700 dark:text-red-300",       secondary: "text-red-600/80 dark:text-red-400/80" },
    }[palette];

    return (
        <div className={`rounded-xl p-3 border ${styles.bg}`}>
            <div className="flex items-center gap-2 mb-1">
                <div className={`w-2 h-2 rounded-full ${styles.dot}`} />
                <p className="text-xs font-medium text-muted-foreground">{label}</p>
            </div>
            <p className={`text-sm font-bold ${styles.primary}`}>{primary}</p>
            {secondary && <p className={`text-[10px] mt-0.5 ${styles.secondary}`}>{secondary}</p>}
        </div>
    );
}

function DocCard({ d, docBusy, driverName, onPreview, onReview }: { d: any; docBusy: string | null; driverName: string; onPreview: (url: string) => void; onReview: (id: string, action: "approved" | "rejected") => void }) {
    const { toast } = useToast();
    const [downloading, setDownloading] = useState(false);

    const onDownload = async () => {
        setDownloading(true);
        try {
            await downloadDriverDocument(d.id, driverName, d.document_type || "document");
        } catch (e: any) {
            // Surfaced, not swallowed: a document that won't download is the
            // difference between filing a regulator submission and not.
            toast({ title: "Download failed", description: e?.message ?? "Unknown error", variant: "destructive" });
        } finally {
            setDownloading(false);
        }
    };

    const exp = d.expiry_date || d.expires_at;
    const expired = exp && new Date(exp) < new Date();
    const isImage = d.document_url && /\.(jpg|jpeg|png|gif|webp|bmp|svg)(\?|$)/i.test(d.document_url);
    const sc = d.status === "approved" && !expired ? "emerald" : d.status === "rejected" ? "red" : expired ? "red" : "amber";
    return (
        <div className="bg-card rounded-xl border overflow-hidden transition hover:shadow-md group">
            <div className="relative h-44 bg-muted/50 flex items-center justify-center overflow-hidden">
                {isImage ? (<><img src={d.document_url} alt={d.document_type||"Document"} loading="lazy" decoding="async" className="w-full h-full object-cover" onError={e=>{(e.target as HTMLImageElement).style.display='none';}} /><button onClick={()=>onPreview(d.document_url)} className="absolute inset-0 bg-black/0 group-hover:bg-black/30 transition flex items-center justify-center opacity-0 group-hover:opacity-100"><div className="bg-white/90 rounded-full p-2"><ZoomIn className="h-5 w-5 text-gray-800" /></div></button></>)
                : d.document_url ? <a href={d.document_url} target="_blank" rel="noreferrer" className="flex flex-col items-center gap-2 text-muted-foreground hover:text-foreground transition"><FileText className="h-12 w-12 opacity-40" /><span className="text-xs font-medium">Click to view</span></a>
                : <div className="flex flex-col items-center gap-2 text-muted-foreground"><Image className="h-12 w-12 opacity-20" /><span className="text-xs">No file</span></div>}
                <div className="absolute top-2 right-2"><Badge className={`text-[10px] shadow-sm ${sc==="emerald"?"bg-emerald-500 text-white":sc==="red"?"bg-red-500 text-white":"bg-amber-500 text-white"}`}>{expired&&d.status==="approved"?"EXPIRED":d.status?.toUpperCase()}</Badge></div>
                {d.side && <div className="absolute top-2 left-2"><Badge variant="secondary" className="text-[10px] shadow-sm bg-black/60 text-white border-none">{d.side}</Badge></div>}
            </div>
            <div className="p-3 space-y-2">
                <p className="text-sm font-semibold truncate">{d.document_type||"Document"}{d.side?` (${d.side})`:""}</p>
                <div className="space-y-1">
                    {d.created_at && <p className="text-[11px] text-muted-foreground flex items-center gap-1"><CalendarRange className="h-3 w-3" />Uploaded: {new Date(d.created_at).toLocaleDateString("en-CA",{month:"short",day:"numeric",year:"numeric"})}</p>}
                    {exp && <p className={`text-[11px] flex items-center gap-1 ${expired?"text-red-500 font-medium":"text-muted-foreground"}`}><Clock className="h-3 w-3" />Expires: {new Date(exp).toLocaleDateString("en-CA",{month:"short",day:"numeric",year:"numeric"})}{expired&&" (EXPIRED)"}</p>}
                </div>
                {d.rejection_reason && <p className="text-[11px] text-red-500 bg-red-50 dark:bg-red-900/20 rounded-lg px-2 py-1"><AlertTriangle className="h-3 w-3 inline mr-1" />{d.rejection_reason}</p>}
                <div className="flex items-center gap-1.5 pt-1">
                    <Button variant="outline" size="xs" className="flex-1 text-emerald-600 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800 hover:bg-emerald-50 dark:hover:bg-emerald-900/20" disabled={docBusy===d.id} onClick={()=>onReview(d.id,"approved")}><CheckCircle className="h-3 w-3" /> Approve</Button>
                    <Button variant="outline" size="xs" className="flex-1 text-red-600 dark:text-red-400 border-red-200 dark:border-red-800 hover:bg-red-50 dark:hover:bg-red-900/20" disabled={docBusy===d.id} onClick={()=>onReview(d.id,"rejected")}><XCircle className="h-3 w-3" /> Reject</Button>
                </div>
                {/* Saving the file to disk had no affordance at all — the card
                    could only preview, approve, or reject. Admins need the
                    actual file to attach to a regulator email (e.g. sending a
                    criminal record check to SGI). */}
                <Button variant="outline" size="xs" className="w-full" disabled={!d.document_url || downloading} onClick={onDownload}>
                    {downloading ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />} Download file
                </Button>
            </div>
        </div>
    );
}
