"use client";

import { useEffect, useState, useCallback, useMemo, useRef } from "react";
import { getDriverStats, getDrivers, getDriverDocuments, downloadDriverDocument, reviewDocument, updateDriver, reviewDriverPhoto, uploadDriverPhoto, getDriverVehicleHistory, getServiceAreas, getVehicleTypes, getFareConfigs, exportDrivers, getDriverRides, getDriverLiveStats, getDriverPayoutsSummary, getDriverReferrals, getDriverTraining, retryPayout, refreshDriverStripeKyc, refreshDriverStripePayouts, refreshAllDriverStripeKyc, refreshAllDriverStripePayouts, recomputeStatementTotals, revealDriverSin, getAdminSubscriptionPayments, type DriverLiveStats, type DriverPayoutSummary, type DriverReferralSummary, type DriverTraining } from "@/lib/api";
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
import { Table, TableBody, TableCell, TableHeader, TableRow } from "@/components/ui/table";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import { ShieldCheck, ShieldAlert, Shield, X, Star, Car, MapPin, CreditCard, Clock, DollarSign, CheckCircle, XCircle, FileText, Phone, Mail, CalendarRange, ExternalLink, Copy, AlertTriangle, Image, Pencil, Save, Loader2, Ban, Pause, Maximize2, Upload, Trash2 } from "lucide-react";
import { maskEmail, maskPhone, maskPlate, maskVin } from "@/lib/pii";
import { DocumentReviewer } from "./_components/document-reviewer";
import { DocumentUploadDialog } from "./_components/document-upload-dialog";
import DriverActionBar from "./_components/driver-action-bar";
import DriverNotes from "./_components/driver-notes";
import DriverTimeline from "./_components/driver-timeline";
import DriverActivity from "./_components/driver-activity";
import DriverDistance from "./_components/driver-distance";
import { useRequireModule } from "@/hooks/useRequireModule";
import { useFeatureFlag } from "@/hooks/useFeatureFlag";
import { useToast } from "@/components/ui/use-toast";
import { useAuthStore } from "@/store/authStore";
import { isPhotoFileTypeValid } from "@/lib/driverPhotoUploadSchema";
import { driverDisplayName, QuickStat, DetailSection, DetailField, CopyableField, EditField, EditBooleanField, workAuth, workAuthLocal, WORK_AUTH_LABELS, WORK_AUTH_FLAG_LABELS } from "./_components/driver-detail-shared";
import { matchesRequirement, VerificationSummaryCard, DocExpirySummaryCard, DocCard } from "./_components/driver-documents-helpers";
import { DriverPayoutsTab } from "./_components/driver-payouts-tab";
import { DriverReferralsTab } from "./_components/driver-referrals-tab";
import { DriverTrainingTab } from "./_components/driver-training-tab";
import { DriverRidesTab } from "./_components/driver-rides-tab";
import DriverListTable from "./_components/driver-list-table";
import DriverOverviewTab from "./_components/driver-overview-tab";

const PAGE_SIZE = 50;

export default function DriversPage() {
    const { allowed } = useRequireModule("drivers");
    const { toast } = useToast();
    // Quiet Console Stage 3: gates the flag-on Badge alternates for the
    // driver-lifecycle status pill and the Spinr Pass pill below — the
    // ad-hoc-color originals stay fully intact when the flag is off.
    const themeV2Enabled = useFeatureFlag("admin_theme_v2_enabled");
    // SIN reveal and the Stripe payout sync are both gated to super_admin
    // server-side (admin_reveal_driver_sin / admin_refresh_driver_stripe_payouts)
    // — gate the UI the same way so lower roles never see a button that can
    // only 403. Plain `admin` users see only the last-4 from cache columns.
    const currentUserRole = useAuthStore((s) => s.user?.role);
    const isSuperAdmin = (currentUserRole || "").toLowerCase() === "super_admin";
    const canRevealSin = isSuperAdmin;
    // The Documents tab / full-screen reviewer call getDriverDocuments,
    // reviewDocument, downloadDriverDocument — all mounted behind the
    // backend's require_module("documents") (routes/admin/__init__.py),
    // a DIFFERENT grant from the "drivers" module this page itself is
    // gated on above. A staff member can hold one without the other, so
    // this is checked separately (same pattern as earnings/page.tsx's
    // canSeeReferrals) rather than assuming "drivers" implies "documents".
    const currentUserModules = useAuthStore((s) => s.user?.modules) ?? [];
    const canReviewDocuments = isSuperAdmin || currentUserModules.includes("documents");
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
    // Legacy-import filter — "imported"/"not_imported" map to the
    // legacy_import=true/false query param; "all" sends no filter.
    const [legacyFilter, setLegacyFilter] = useState<"all" | "imported" | "not_imported">("all");
    // Pre-launch-flag filter — "hide"/"only" map to the pre_launch=false/true
    // query param (services/pre_launch_flag_service.py); "all" sends no
    // filter (default — matches every prior page load, no silent change).
    const [preLaunchFilter, setPreLaunchFilter] = useState<"all" | "hide" | "only">("all");
    const [startDate, setStartDate] = useState("");
    const [endDate, setEndDate] = useState("");
    const [serviceAreas, setServiceAreas] = useState<{ id: string; name: string }[]>([]);
    const [driverRides, setDriverRides] = useState<any[]>([]);
    // Real count via count_documents(), independent of the fetch cap --
    // lets the Rides tab tell "fetched everything" apart from "there's more"
    // (A30 Finding 2, docs/audit/2026-08-13-migrated-data-visibility-audit.md).
    const [driverRidesTotalCount, setDriverRidesTotalCount] = useState<number | null>(null);
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
            setDriverRidesTotalCount(typeof res?.total_count === "number" ? res.total_count : null);
            setRidesLoaded(driverId);
        } catch {
            setDriverRides([]);
            setDriverRidesTotalCount(null);
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
        // Legacy shells are hidden from every tab but their own. They are not
        // deleted or hidden from the database — the dedicated tab reaches them.
        // The review tab is also exempt: some flagged rows are themselves
        // shells, and sending onboarding_complete=true would hide them from the
        // very queue that exists to surface them.
        if (statusFilter === "legacy_incomplete") opts.onboarding_complete = false;
        else if (statusFilter !== "legacy_review") opts.onboarding_complete = true;
        if (statusFilter === "legacy_review") opts.legacy_review = true;
        if (legacyFilter === "imported") opts.legacy_import = true;
        else if (legacyFilter === "not_imported") opts.legacy_import = false;
        if (preLaunchFilter === "only") opts.pre_launch = true;
        else if (preLaunchFilter === "hide") opts.pre_launch = false;
        // Returns the rendered page so a caller that just mutated a driver can
        // re-sync the open detail sheet from the refreshed server rows.
        return getDrivers(opts)
            .then((rows) => {
                if (reqId !== reqIdRef.current) return [] as any[];
                const arr = Array.isArray(rows) ? rows : [];
                setHasNextPage(arr.length > PAGE_SIZE);
                const pageRows = arr.slice(0, PAGE_SIZE);
                setDrivers(pageRows);
                return pageRows;
            })
            .catch(() => { if (reqId === reqIdRef.current) { setDrivers([]); setHasNextPage(false); } return [] as any[]; })
            .finally(() => { if (reqId === reqIdRef.current) setTableLoading(false); });
    }, [page, serviceAreaId, statusFilter, searchDebounced, vehicleTypeFilter, legacyFilter, preLaunchFilter, sortKey, sortDir]);

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
    useEffect(() => { setPage(0); }, [statusFilter, serviceAreaId, searchDebounced, vehicleTypeFilter, legacyFilter, preLaunchFilter, sortKey, sortDir]);
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
            setDriverRidesTotalCount(null);
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
            // The completeness score/missing-fields are derived server-side from
            // the row, so the local merge above cannot refresh them: an admin who
            // just filled in a field this very panel flagged as missing would keep
            // seeing it flagged. Refetch and re-sync the open sheet from the
            // server row, which is authoritative once the save has landed.
            const savedId = selected.id;
            loadDrivers().then((rows) => {
                const fresh = (rows || []).find((r: any) => r.id === savedId);
                if (fresh) setSelected((cur: any) => (cur?.id === savedId ? fresh : cur));
            });
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
        if (!isPhotoFileTypeValid(file)) {
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

    const statusCounts = (s: string) => {
        const stats = data?.stats;
        if (!stats) return 0;
        // `all` is every REAL driver — `stats.total` still counts the legacy
        // shells too, and showing that made the fleet read ~3x its true size.
        if (s === "all") return stats.onboarded_total ?? stats.total ?? 0;
        if (s === "legacy_incomplete") return stats.legacy_incomplete ?? 0;
        if (s === "legacy_review") return stats.legacy_review ?? 0;
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
            <DriverListTable
                data={data}
                loading={loading}
                serviceAreaId={serviceAreaId}
                setServiceAreaId={setServiceAreaId}
                selectedAreaName={selectedAreaName}
                serviceAreas={serviceAreas}
                vehicleTypeFilter={vehicleTypeFilter}
                setVehicleTypeFilter={setVehicleTypeFilter}
                availableVehicleTypes={availableVehicleTypes}
                legacyFilter={legacyFilter}
                setLegacyFilter={setLegacyFilter}
                preLaunchFilter={preLaunchFilter}
                setPreLaunchFilter={setPreLaunchFilter}
                startDate={startDate}
                setStartDate={setStartDate}
                endDate={endDate}
                setEndDate={setEndDate}
                showPii={showPii}
                setShowPii={setShowPii}
                isSuperAdmin={isSuperAdmin}
                bulkKycRunning={bulkKycRunning}
                handleBulkKycRefresh={handleBulkKycRefresh}
                bulkPayoutsRunning={bulkPayoutsRunning}
                handleBulkPayoutRefresh={handleBulkPayoutRefresh}
                bulkTotalsRunning={bulkTotalsRunning}
                handleRecomputeStatementTotals={handleRecomputeStatementTotals}
                handleExport={handleExport}
                sorted={sorted}
                statusFilter={statusFilter}
                setStatusFilter={setStatusFilter}
                statusCounts={statusCounts}
                search={search}
                setSearch={setSearch}
                tableLoading={tableLoading}
                sortKey={sortKey}
                sortDir={sortDir}
                handleSort={handleSort}
                themeV2Enabled={themeV2Enabled}
                vehicleTypes={vehicleTypes}
                fmtDate={fmtDate}
                selected={selected}
                setSelected={setSelected}
                page={page}
                setPage={setPage}
                hasNextPage={hasNextPage}
                pageSize={PAGE_SIZE}
            />

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
                                            <span className={`absolute -bottom-1 -right-1 w-4 h-4 rounded-full border-2 border-background ${selected.is_online ? "bg-success" : "bg-muted-foreground/40"}`} />
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
                                            <h2 className="text-xl font-bold flex items-center gap-2">
                                                {driverDisplayName(selected) || <span className="text-muted-foreground italic">Unnamed driver</span>}
                                                {selected.legacy_import_metadata && Object.keys(selected.legacy_import_metadata).length > 0 && (
                                                    <span className="inline-block text-[10px] font-medium text-muted-foreground bg-muted rounded px-1.5 py-0.5 align-middle">
                                                        Imported
                                                    </span>
                                                )}
                                            </h2>
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
                                                <div className="flex items-center gap-2 mt-2 p-2 rounded-lg bg-warning/10 border border-warning/30">
                                                    {(liveStats?.photo_url || selected.photo_url) && (
                                                        // eslint-disable-next-line @next/next/no-img-element
                                                        <img src={liveStats?.photo_url || selected.photo_url} alt="" className="w-9 h-9 rounded-full object-cover" />
                                                    )}
                                                    <span className="text-xs text-warning flex-1">Profile photo pending review</span>
                                                    {/* eslint-disable-next-line no-restricted-syntax -- solid-fill white-text success button; --success fails WCAG AA against white text in dark mode (#2816) */}
                                                    <button disabled={photoReviewing} onClick={() => handlePhotoReview("approve")} className="text-xs font-semibold px-2 py-1 rounded bg-emerald-600 text-white disabled:opacity-50">Approve</button>
                                                    <button disabled={photoReviewing} onClick={() => handlePhotoReview("reject")} className="text-xs font-semibold px-2 py-1 rounded bg-destructive text-destructive-foreground disabled:opacity-50">Reject</button>
                                                </div>
                                            )}
                                            {selected.profile_image_status === "rejected" && (
                                                <div className="mt-2 text-xs text-destructive">Profile photo rejected — driver must re-upload.</div>
                                            )}
                                            {/* Same categorical driver-lifecycle-status map as the list row above
                                                (6 states, 5 hues) -- not a #2816 migration target. */}
                                            <div className="flex items-center gap-2 mt-2">
                                                {themeV2Enabled ? (
                                                    selected.account_deleted ? <Badge variant="outline"><Trash2 className="h-3 w-3" /> Deleted</Badge>
                                                    : selected.status === "active" ? <Badge variant="outline-success"><ShieldCheck className="h-3 w-3" /> Active</Badge>
                                                    : selected.status === "needs_review" ? <Badge variant="outline-warning"><AlertTriangle className="h-3 w-3" /> Needs Review</Badge>
                                                    : selected.status === "suspended" ? <Badge variant="outline-destructive"><Pause className="h-3 w-3" /> Suspended</Badge>
                                                    : selected.status === "banned" ? <Badge variant="outline-destructive"><Ban className="h-3 w-3" /> Banned</Badge>
                                                    : <Badge variant="outline"><ShieldAlert className="h-3 w-3" /> Pending</Badge>
                                                ) : (
                                                    // eslint-disable-next-line no-restricted-syntax -- categorical driver-lifecycle-status map (#2816)
                                                    selected.account_deleted ? <Badge className="bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"><Trash2 className="h-3 w-3" /> Deleted</Badge>
                                                    // eslint-disable-next-line no-restricted-syntax -- categorical driver-lifecycle-status map (#2816)
                                                    : selected.status === "active" ? <Badge className="bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"><ShieldCheck className="h-3 w-3" /> Active</Badge>
                                                    // eslint-disable-next-line no-restricted-syntax -- categorical driver-lifecycle-status map (#2816)
                                                    : selected.status === "needs_review" ? <Badge className="bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"><AlertTriangle className="h-3 w-3" /> Needs Review</Badge>
                                                    // eslint-disable-next-line no-restricted-syntax -- categorical driver-lifecycle-status map (#2816)
                                                    : selected.status === "suspended" ? <Badge className="bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400"><Pause className="h-3 w-3" /> Suspended</Badge>
                                                    // eslint-disable-next-line no-restricted-syntax -- categorical driver-lifecycle-status map (#2816)
                                                    : selected.status === "banned" ? <Badge className="bg-red-200 text-red-800 dark:bg-red-900/40 dark:text-red-400"><Ban className="h-3 w-3" /> Banned</Badge>
                                                    // eslint-disable-next-line no-restricted-syntax -- categorical driver-lifecycle-status map (#2816)
                                                    : <Badge className="bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400"><ShieldAlert className="h-3 w-3" /> Pending</Badge>
                                                )}
                                                <Badge variant="outline" className={selected.is_online && !selected.account_deleted ? "border-success/40 text-success" : ""}>
                                                    {selected.is_online && !selected.account_deleted ? "Online" : "Offline"}
                                                    {selected.last_status_changed_at && (
                                                        <span className="ml-1.5 text-[10px] opacity-70">
                                                            since {new Date(selected.last_status_changed_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}
                                                        </span>
                                                    )}
                                                </Badge>
                                                {selected.subscription_status === "active" && (themeV2Enabled ? (
                                                    <Badge variant="outline-accent"><CreditCard className="h-3 w-3" /> Spinr Pass</Badge>
                                                ) : (
                                                    // eslint-disable-next-line no-restricted-syntax -- Spinr Pass brand violet, not a success/warning/destructive signal (#2816)
                                                    <Badge className="bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-400"><CreditCard className="h-3 w-3" /> Spinr Pass</Badge>
                                                ))}
                                                {selected.subscription_status === "expired" && <Badge className="bg-destructive/15 text-destructive"><CreditCard className="h-3 w-3" /> Pass Expired</Badge>}
                                            </div>
                                            {/* Profile Completeness Summary */}
                                            {(() => {
                                                const score = selected.profile_completeness_score;
                                                const missingCount = selected.profile_missing_count || 0;
                                                if (score === undefined || score === null) return null;
                                                return (
                                                    <div className="mt-3 p-2.5 rounded-lg border bg-muted/30">
                                                        <div className="flex items-center justify-between mb-1.5">
                                                            <span className="text-xs font-medium text-foreground/80">
                                                                Profile: {score}% complete{missingCount > 0 ? ` (${missingCount} field${missingCount === 1 ? '' : 's'} missing)` : ''}
                                                            </span>
                                                        </div>
                                                        <div className="h-1.5 w-full bg-muted rounded-full overflow-hidden">
                                                            <div
                                                                className={`h-full rounded-full transition-all ${score === 100 ? 'bg-success' : score >= 70 ? 'bg-warning' : 'bg-destructive'}`}
                                                                style={{ width: `${Math.min(score, 100)}%` }}
                                                            />
                                                        </div>
                                                        {score === 100 ? (
                                                            <p className="text-[11px] text-success mt-1.5 flex items-center gap-1"><CheckCircle className="h-3 w-3" />All required fields complete</p>
                                                        ) : selected.profile_missing_fields && selected.profile_missing_fields.length > 0 ? (
                                                            <div className="mt-1.5 flex flex-wrap gap-1">
                                                                {/* Already display labels ("License Plate"), not field
                                                                    names — the backend sends m["label"]. No un-snaking. */}
                                                                {selected.profile_missing_fields.map((label: string) => (
                                                                    <span key={label} className="text-[10px] text-destructive bg-destructive/10 px-1.5 py-0.5 rounded">{label}</span>
                                                                ))}
                                                            </div>
                                                        ) : null}
                                                    </div>
                                                );
                                            })()}
                                        </div>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        {!editing ? <Button variant="outline" size="sm" onClick={startEditing}><Pencil className="h-3.5 w-3.5" /> Edit</Button> : (<>
                                            <Button variant="ghost" size="sm" onClick={() => setEditing(false)} disabled={saving}>Cancel</Button>
                                            {/* eslint-disable-next-line no-restricted-syntax -- solid-fill white-text success button; --success fails WCAG AA against white text in dark mode (#2816) */}
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
                                        // eslint-disable-next-line no-restricted-syntax -- decorative KPI-card accent color, not a status signal (#2816)
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
                                        // eslint-disable-next-line no-restricted-syntax -- decorative KPI-card accent color, not a status signal (#2816)
                                        icon={Car} color="text-blue-500" bg="bg-blue-50 dark:bg-blue-900/20"
                                        label="Rides"
                                        value={
                                            liveStats === null
                                                ? "\u2026"
                                                : (liveStats.total_rides || 0).toLocaleString()
                                        }
                                    />
                                    <QuickStat
                                        // eslint-disable-next-line no-restricted-syntax -- decorative KPI-card accent color, not a status signal (#2816)
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
                                        // eslint-disable-next-line no-restricted-syntax -- decorative KPI-card accent color, not a status signal (#2816)
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
                                <TabsTrigger value="documents">Documents{pendingDocsCount > 0 && <span className="ml-1.5 bg-warning/15 text-warning text-[10px] font-bold px-1.5 py-0.5 rounded-full" title={`${pendingDocsCount} document${pendingDocsCount === 1 ? "" : "s"} awaiting review`}>{pendingDocsCount}</span>}</TabsTrigger>
                                <TabsTrigger value="rides">Rides{selected.total_rides > 0 && <span className="ml-1.5 bg-primary/10 text-primary text-[10px] font-bold px-1.5 py-0.5 rounded-full">{(selected.total_rides || 0).toLocaleString()}</span>}</TabsTrigger>
                                <TabsTrigger value="distance">Distance</TabsTrigger>
                                <TabsTrigger value="payouts">Payouts{payoutSummary && payoutSummary.summary.pending_balance > 0 && <span className="ml-1.5 bg-warning/15 text-warning text-[10px] font-bold px-1.5 py-0.5 rounded-full" title={`${formatCurrency(payoutSummary.summary.pending_balance)} pending payout`}>!</span>}</TabsTrigger>
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
                                    <DriverOverviewTab
                                        selected={selected}
                                        editing={editing}
                                        ef={ef}
                                        setEf={setEf}
                                        allServiceAreas={allServiceAreas}
                                        vehicleTypes={vehicleTypes}
                                        vehicleTypesByArea={vehicleTypesByArea}
                                        serviceAreas={serviceAreas}
                                        showPii={showPii}
                                        liveStats={liveStats}
                                        vehicleHistory={vehicleHistory}
                                        themeV2Enabled={themeV2Enabled}
                                        fmtDate={fmtDate}
                                    />
                                </TabsContent>

                                {/* Rides */}
                                <TabsContent value="rides" className="mt-4">
                                    <DriverRidesTab
                                        rides={driverRides}
                                        totalCount={driverRidesTotalCount}
                                        loading={ridesLoading}
                                        driverName={driverDisplayName(selected) || "this driver"}
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
                                        driverName={driverDisplayName(selected) || "this driver"}
                                        isLegacyImported={!!selected.legacy_import_metadata && Object.keys(selected.legacy_import_metadata).length > 0}
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
                                                disabled={!canReviewDocuments}
                                                title={canReviewDocuments ? undefined : "Requires the \"Documents\" module — ask an admin to grant it"}
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
                                                            <FileText className="h-4 w-4 text-muted-foreground" /><h3 className="text-sm font-semibold">{reqDoc.label}</h3>
                                                            {matchingDocs.length === 0 && <Badge className="bg-destructive/15 text-destructive text-[10px]">Missing</Badge>}
                                                            {counts.pending > 0 && <Badge className="bg-warning/15 text-warning text-[10px]">{counts.pending} pending</Badge>}
                                                            {counts.approved > 0 && counts.pending === 0 && !expiryMissing && <Badge className="bg-success/15 text-success text-[10px]">Approved</Badge>}
                                                            {expiryMissing && counts.pending === 0 && <Badge className="bg-warning/15 text-warning text-[10px]">Approved · expiry not recorded</Badge>}
                                                            {counts.rejected > 0 && counts.pending === 0 && counts.approved === 0 && <Badge className="bg-destructive/15 text-destructive text-[10px]">Re-upload needed</Badge>}
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
                                                            <FileText className="h-4 w-4 text-muted-foreground" /><h3 className="text-sm font-semibold">Other Documents</h3>
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

                                {/* Distance Travelled: per-Regina-day phase km + durations,
                                    with per-day Distance Logs drill-down (insurance/ops view) */}
                                <TabsContent value="distance" className="mt-4">
                                    <DriverDistance driverId={selected.id} />
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
                canReview={canReviewDocuments}
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
                                // eslint-disable-next-line no-restricted-syntax -- decorative light overlay control on a dark image-preview backdrop, not a status signal (#2816)
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
                                    Expiry Date {reviewingDoc?.requiresExpiry ? <span className="text-destructive">*</span> : "(optional)"}
                                </label>
                                <Input type="date" value={reviewExpiry} onChange={e => setReviewExpiry(e.target.value)} className="w-full" />
                                {reviewingDoc?.requiresExpiry && !reviewExpiry && (
                                    <p className="text-xs text-destructive mt-1 flex items-center gap-1"><AlertTriangle className="h-3 w-3" /> Expiry date is required for this document type. This will update the driver&apos;s profile.</p>
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
                            // eslint-disable-next-line no-restricted-syntax -- solid-fill white-text success button; --success fails WCAG AA against white text in dark mode (#2816)
                            className={reviewingDoc?.action === "approved" ? "bg-emerald-600 hover:bg-emerald-700 text-white" : "bg-destructive hover:bg-destructive/90 text-destructive-foreground"}
                        >
                            {reviewingDoc?.action === "approved" ? "Approve" : "Reject"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
