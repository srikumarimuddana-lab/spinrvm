// This file is being incrementally split into per-domain modules under
// src/lib/api/ (see src/lib/api/client.ts for the shared fetch client, and
// the CLAUDE.md task log for migration status). Domain functions not yet
// migrated stay below unchanged; migrated ones are re-exported so existing
// `import { ... } from "@/lib/api"` call sites are unaffected.
export { API_BASE, RateLimitError, request } from "./api/client";
export {
    getDriverDocuments,
    reviewDocument,
    adminUploadDriverDocument,
    getDriverNotes,
    addDriverNote,
    deleteDriverNote,
} from "./api/driver-documents";
export type {
    RejectTemplate,
    ReviewDocumentOptions,
    AdminUploadDocumentInput,
} from "./api/driver-documents";
export {
    getRides,
    exportRides,
    getRideDetails,
    getRideTrend,
    getRideStats,
    getRideFinancials,
    getRideLocationTrail,
    getLiveRideData,
    getRideInvoice,
    getRideRouteMapDataUrl,
    flagRideParticipant,
    createRideComplaint,
    resolveComplaint,
    reportLostItem,
    resolveLostItem,
    sendRideInvoice,
    sendPayableRideInvoice,
    getFlags,
    deactivateFlag,
    deleteFlag,
    getLostAndFoundItems,
    updateLostItem,
    deleteLostItem,
    deleteDispute,
    getComplaints,
    deleteComplaint,
} from "./api/rides";
export type { RideListOpts, RideFinancialsPeriod } from "./api/rides";
export {
    getDrivers,
    adminSearchDrivers,
    adminSearchUsers,
    getDriverRides,
    getDriverDailyActivity,
    getDriverLiveStats,
    getDriverReferrals,
    getDriverTraining,
    getReferralLeaderboard,
    getRiderReferralLeaderboard,
    getReferralAnalytics,
    getFailedReferralClaims,
    requeueFailedReferral,
    getReferralPairs,
    getDriverPayoutsSummary,
    refreshDriverStripeKyc,
    revealDriverSin,
    getDriverStats,
    updateDriver,
} from "./api/drivers";
export type {
    DriverLiveStats,
    DriverReferee,
    DriverReferralSummary,
    DriverTrainingCourse,
    DriverTrainingCertificate,
    DriverTrainingQuizAttempt,
    DriverTrainingCommunication,
    DriverTraining,
    ReferralLeader,
    ReferralLeaderboard,
    ReferralAnalytics,
    FailedReferralClaim,
    ReferralPair,
    DriverPayoutSummary,
    RevealSinResponse,
} from "./api/drivers";
import { request } from "./api/client";
import { useAuthStore } from "@/store/authStore";

/* ── Auth ─────────────────────────────────── */
export interface AuthResponse {
    token: string;
    user: {
        id: string;
        phone: string;
        first_name?: string;
        last_name?: string;
        email?: string;
        role: string;
        profile_complete: boolean;
    };
    is_new_user: boolean;
}

export interface AdminLoginResponse {
    token: string;
    access_expires_at: string;
    csrf_token?: string;
    user: {
        id: string;
        email: string;
        role: string;
        first_name?: string;
        last_name?: string;
        modules?: string[];
    };
}

export interface AdminMfaRequired {
    mfa_required: true;
    mfa_token: string;
}

// ADMIN_MFA_ENFORCED: password was correct but the account has no MFA yet.
// mfa_token is enrollment-scoped — only /mfa/enroll and /mfa/confirm accept it.
export interface AdminMfaEnrollmentRequired {
    mfa_enrollment_required: true;
    mfa_token: string;
}

export type AdminLoginResult = AdminLoginResponse | AdminMfaRequired | AdminMfaEnrollmentRequired;

export const loginAdmin = (phone: string, code: string) =>
    request<AuthResponse>("/api/auth/verify-otp", {
        method: "POST",
        body: JSON.stringify({ phone, code }),
    });

export const loginAdminSession = (email: string, password: string) =>
    request<AdminLoginResult>("/api/admin/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
    });

export const mfaChallenge = (mfa_token: string, totp_code: string) =>
    request<AdminLoginResponse>("/api/admin/auth/mfa/challenge", {
        method: "POST",
        body: JSON.stringify({ mfa_token, totp_code }),
    });

export const mfaStatus = () =>
    request<{ mfa_enabled: boolean; available: boolean; enforced?: boolean }>("/api/admin/auth/mfa/status");

// Confirm also returns full session tokens so first-login enrollment
// (no session yet, only the enrollment-scoped token) lands in the dashboard.
// Settings-flow callers already have a session and can ignore them.
export interface MfaConfirmResponse extends AdminLoginResponse {
    backup_codes: string[];
    refresh_expires_at?: string;
}

// `authToken` carries the enrollment-scoped token during forced first-login
// enrollment; omitted, the session token from the store is used (Settings flow).
// The store token is null in the forced flow, so this header is not overwritten.
export const mfaEnroll = (authToken?: string) =>
    request<{ secret: string; otpauth_uri: string }>("/api/admin/auth/mfa/enroll", {
        method: "POST",
        ...(authToken ? { headers: { Authorization: `Bearer ${authToken}` } } : {}),
    });

export const mfaConfirm = (totp_code: string, authToken?: string) =>
    request<MfaConfirmResponse>("/api/admin/auth/mfa/confirm", {
        method: "POST",
        body: JSON.stringify({ totp_code }),
        ...(authToken ? { headers: { Authorization: `Bearer ${authToken}` } } : {}),
    });

export const mfaDisable = (totp_code: string, password: string) =>
    request<{ success: boolean }>("/api/admin/auth/mfa/disable", {
        method: "POST",
        body: JSON.stringify({ totp_code, password }),
    });

export const sendOtp = (phone: string) =>
    request<{ success: boolean }>("/api/auth/send-otp", {
        method: "POST",
        body: JSON.stringify({ phone }),
    });

// Admin "sign out everywhere" — closes B-P1-13. Bumps
// admin_staff.token_version (kills all in-flight admin access tokens
// on next request) and revokes every refresh token for this staff row.
// Refused server-side for admin-001 (env-var super admin); rotate
// ADMIN_PASSWORD to globally kill that account.
export const logoutAllAdmin = () =>
    request<{ success: boolean; revoked_refresh_tokens: number }>("/api/admin/auth/logout-all", {
        method: "POST",
    });

/* ── Dashboard ────────────────────────────── */
export const getStats = () =>
    request<{
        total_rides: number;
        completed_rides: number;
        cancelled_rides: number;
        active_rides: number;
        total_drivers: number;
        online_drivers: number;
        total_users: number;
        total_driver_earnings: number;
        total_admin_earnings: number;
        total_tips: number;
    }>("/api/admin/stats");

/* ── Rides, flags, complaints, lost-and-found ── moved to lib/api/rides.ts, re-exported above. */
/* ── Drivers (listings, live stats, referrals, training, payouts) ── moved to lib/api/drivers.ts, re-exported above. */
/* ── Earnings ─────────────────────────────── */
export const getEarnings = () => request<any[]>("/api/admin/earnings");

export interface EarningsRide {
    ride_id: string;
    ride_code: string | null;
    status: string;
    total_fare: number;
    driver_earnings: number;
    admin_earnings: number;
    tip_amount: number;
    tax_amount: number;
    discount_amount: number;
    surge_multiplier: number;
    stripe_charge_id: string | null;
    driver_id: string | null;
    driver_name: string | null;
    rider_id: string | null;
    rider_name: string | null;
    service_area_id: string | null;
    completed_at: string | null;
    created_at: string | null;
}

export interface EarningsRidesResponse {
    rides: EarningsRide[];
    total: number;
    offset: number;
    limit: number;
}

export const getEarningsRides = (params?: {
    start_date?: string;
    end_date?: string;
    service_area_id?: string;
    limit?: number;
    offset?: number;
}) => {
    const sp = new URLSearchParams();
    if (params?.start_date) sp.set("start_date", params.start_date);
    if (params?.end_date) sp.set("end_date", params.end_date);
    if (params?.service_area_id) sp.set("service_area_id", params.service_area_id);
    if (params?.limit != null) sp.set("limit", String(params.limit));
    if (params?.offset != null) sp.set("offset", String(params.offset));
    const qs = sp.toString();
    return request<EarningsRidesResponse>(`/api/admin/earnings/rides${qs ? `?${qs}` : ""}`);
};

export type EarningsPeriod = "7d" | "30d" | "mtd" | "ytd";

export interface MetricWithDelta {
    current: number;
    previous: number;
    /** Null when the previous-window value was 0 — UI shows "—" instead of "+Inf%". */
    delta_pct: number | null;
}

export interface EarningsOverview {
    period: {
        key: EarningsPeriod;
        label: string;
        days: number;
        start: string;
        end: string;
        prev_start: string;
        prev_end: string;
    };
    metrics: {
        // Pass 1 — CEO row
        gbv: MetricWithDelta;
        net_revenue: MetricWithDelta;
        take_rate_pct: MetricWithDelta;
        completed_trips: MetricWithDelta;
        active_riders: MetricWithDelta;
        active_drivers: MetricWithDelta;
        avg_fare: MetricWithDelta;
        spinr_pass_mrr: MetricWithDelta;
        // Pass 2 — operational health
        cancellation_rate_pct: MetricWithDelta;
        cancellation_revenue: MetricWithDelta;
        cancelled_trips: MetricWithDelta;
        refund_amount: MetricWithDelta;
        refund_count: MetricWithDelta;
        promo_spend: MetricWithDelta;
        promo_count: MetricWithDelta;
        surge_revenue: MetricWithDelta;
        gst_collected: MetricWithDelta;
        pst_collected: MetricWithDelta;
    };
    cancellation_breakdown: {
        current: { rider: number; driver: number; system: number };
        previous: { rider: number; driver: number; system: number };
    };
    /** Ops funnel — created_at cohort of rides requested in the window,
     *  each as a current/previous MetricWithDelta. Semantics (cohort basis,
     *  cancelled_by attribution, reached-searching definition) live in
     *  migration 227_earnings_overview_funnel_agg.sql. The cancel splits
     *  share attribution with cancellation_breakdown — the two widgets
     *  always reconcile. */
    ride_funnel: {
        price_searches: MetricWithDelta;
        requested: MetricWithDelta;
        reached_searching: MetricWithDelta;
        completed: MetricWithDelta;
        rider_cancelled: MetricWithDelta;
        driver_cancelled: MetricWithDelta;
        /** Admin/system/no-driver + unattributed — count − rider − driver. */
        system_cancelled: MetricWithDelta;
        cancelled_after_start: MetricWithDelta;
    };
    daily_series: Array<{
        date: string;
        gbv: number;
        trips: number;
        net_revenue: number;
    }>;
}

export const getEarningsOverview = (params: { period: EarningsPeriod; service_area_id?: string }) => {
    const sp = new URLSearchParams({ period: params.period });
    if (params.service_area_id) sp.set("service_area_id", params.service_area_id);
    return request<EarningsOverview>(`/api/admin/earnings/overview?${sp.toString()}`);
};

export const getSubscriptionStats = (params?: { start_date?: string; end_date?: string; service_area_ids?: string }) => {
    const sp = new URLSearchParams();
    if (params?.start_date) sp.set("start_date", params.start_date);
    if (params?.end_date) sp.set("end_date", params.end_date);
    if (params?.service_area_ids) sp.set("service_area_ids", params.service_area_ids);
    return request<any>(`/api/admin/subscription-stats?${sp.toString()}`);
};

/* ── Settings ─────────────────────────────── */
export const getEmailDeliverability = (days = 7) =>
    request<{
        window_days: number;
        total: number;
        by_status: Record<string, number>;
        by_provider: Record<string, number>;
        by_type: Record<string, number>;
        failure_rate: number;
        suppressed_in_window: number;
        suppression_list_size: number;
        recent_failures: Array<{ email_type: string; provider: string; status: string; recipient_user_id: string | null; created_at: string }>;
        recent_suppressions: Array<{ reason: string; detail: string | null; source: string; message_id: string | null; created_at: string }>;
    }>(`/api/admin/monitoring/email-deliverability?days=${days}`);

export const getSettings = () => request<any>("/api/admin/settings");
export const updateSettings = (data: any) =>
    request<{ message: string; audit_log_id?: string }>("/api/admin/settings", {
        method: "PUT",
        body: JSON.stringify(data),
    });

/* ── AI Assistant ─────────────────────────── */
export interface AiCatalogModel { id: string; label: string; }
export interface AiCatalogProvider {
    provider: string;
    label: string;
    key_field: string;
    models: AiCatalogModel[];
}
export const getAiCatalog = () =>
    request<{ providers: AiCatalogProvider[] }>("/api/admin/ai/catalog");

/* Super-admin AI console — chat as a user + view their threads. */
export interface AdminAiChatResponse {
    conversation_id: string;
    message_id: string;
    reply: string;
    actions: any[];
    audience: string;
}
export const adminAiChat = (data: {
    user_id: string;
    message: string;
    conversation_id?: string | null;
    audience?: "rider" | "driver";
}) =>
    request<AdminAiChatResponse>("/api/admin/ai/chat", {
        method: "POST",
        body: JSON.stringify(data),
    });
export const getAdminAiConversations = (userId: string) =>
    request<{ conversations: { id: string; title: string; updated_at: string }[] }>(
        `/api/admin/ai/users/${userId}/conversations`,
    );
export const getAdminAiMessages = (userId: string, conversationId: string) =>
    request<{ messages: { id: string; role: "user" | "assistant"; content: string; created_at: string }[] }>(
        `/api/admin/ai/users/${userId}/conversations/${conversationId}/messages`,
    );

/* ── Service Areas ────────────────────────── */
export const getServiceAreas = () =>
    request<any[]>("/api/admin/service-areas");

/* ── Pickup Venues ───────────────────────────── */
export interface VenuePickupPoint { name: string; lat: number; lng: number; }
export interface Venue {
    id: string;
    name: string;
    center_lat: number;
    center_lng: number;
    radius_m: number;
    pickup_points: VenuePickupPoint[];
    service_area_id?: string | null;
    is_active: boolean;
}
export type VenueUpsert = Omit<Venue, "id">;
export const getVenues = (params?: { service_area_id?: string }) => {
    const qs = params?.service_area_id ? `?service_area_id=${encodeURIComponent(params.service_area_id)}` : "";
    return request<{ venues: Venue[] }>(`/api/admin/venues${qs}`);
};
export const createVenue = (body: VenueUpsert) =>
    request<Venue>("/api/admin/venues", { method: "POST", body: JSON.stringify(body) });
export const updateVenue = (id: string, body: VenueUpsert) =>
    request<Venue>(`/api/admin/venues/${id}`, { method: "PUT", body: JSON.stringify(body) });
export const deleteVenue = (id: string) =>
    request<{ success: boolean }>(`/api/admin/venues/${id}`, { method: "DELETE" });
export const createServiceArea = (data: any) =>
    request<any>("/api/admin/service-areas", {
        method: "POST",
        body: JSON.stringify(data),
    });
export const updateServiceArea = (id: string, data: any) =>
    request<any>(`/api/admin/service-areas/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });
export const deleteServiceArea = (id: string) =>
    request<any>(`/api/admin/service-areas/${id}`, { method: "DELETE" });

/* ── Ride Incentives ──────────────────────── */
export const getIncentives = (areaId?: string) =>
    request<any[]>(`/api/admin/incentives${areaId ? `?service_area_id=${areaId}` : ''}`);
export const createIncentive = (data: any) =>
    request<any>("/api/admin/incentives", {
        method: "POST",
        body: JSON.stringify(data),
    });
export const updateIncentive = (id: string, data: any) =>
    request<any>(`/api/admin/incentives/${id}`, {
        method: "PATCH",
        body: JSON.stringify(data),
    });
export const toggleIncentive = (id: string) =>
    request<any>(`/api/admin/incentives/${id}/toggle`, { method: "PATCH" });
export const deleteIncentive = (id: string) =>
    request<any>(`/api/admin/incentives/${id}`, { method: "DELETE" });

/* ── Surge Pricing ────────────────────────── */
export const getSurgeStatus = () =>
    request<any[]>("/api/admin/surge/status");
export const resetSurgeToAuto = (id: string) =>
    request<any>(`/api/v1/service-areas/${id}/surge/auto`, { method: "PUT" });

/* ── Vehicle Types ────────────────────────── */
export const getVehicleTypes = () =>
    request<any[]>("/api/admin/vehicle-types");
export const createVehicleType = (data: any) =>
    request<any>("/api/admin/vehicle-types", {
        method: "POST",
        body: JSON.stringify(data),
    });
export const updateVehicleType = (id: string, data: any) =>
    request<any>(`/api/admin/vehicle-types/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });
export const deleteVehicleType = (id: string) =>
    request<any>(`/api/admin/vehicle-types/${id}`, { method: "DELETE" });

/**
 * Upload a PNG/JPEG/WebP illustration for a vehicle type. ≤500 KB.
 * Returns the public URL stored on `vehicle_types.illustration_url`.
 */
export const adminUploadVehicleIllustration = (id: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<{ illustration_url: string }>(
        `/api/admin/vehicle-types/${id}/upload-illustration`,
        { method: "POST", body: fd },
    );
};

/**
 * Upload a custom map marker image for a vehicle type. Transparent
 * PNG/WebP only (car facing north), ≤500 KB. Returns the public URL
 * stored on `vehicle_types.marker_image_url`.
 */
export const adminUploadVehicleMarker = (id: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<{ marker_image_url: string }>(
        `/api/admin/vehicle-types/${id}/upload-marker`,
        { method: "POST", body: fd },
    );
};

/**
 * Upload the driver-app ride-offer alert tone. mp3/wav, ≤500 KB.
 * Returns the public URL stored on `settings.ride_offer_sound_url`.
 */
export const adminUploadRideOfferSound = (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<{ ride_offer_sound_url: string }>(
        "/api/admin/settings/ride-offer-sound",
        { method: "POST", body: fd },
    );
};

/* ── Fare Configs ─────────────────────────── */
export const getFareConfigs = () =>
    request<any[]>("/api/admin/fare-configs");
export const createFareConfig = (data: any) =>
    request<any>("/api/admin/fare-configs", {
        method: "POST",
        body: JSON.stringify(data),
    });
export const updateFareConfig = (id: string, data: any) =>
    request<any>(`/api/admin/fare-configs/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });
export const deleteFareConfig = (id: string) =>
    request<any>(`/api/admin/fare-configs/${id}`, { method: "DELETE" });

/* ── Surge Pricing ────────────────────────── */
export const updateSurge = (areaId: string, data: any) =>
    request<any>(`/api/admin/service-areas/${areaId}/surge`, {
        method: "PUT",
        body: JSON.stringify(data),
    });

/* ── Export Approvals (ACTION_ITEMS.md B10 dual-approval gate) ────────── */
export interface ExportApprovalRequest {
    id: string;
    requested_by: string;
    route_key: string;
    params: Record<string, any>;
    row_count: number | null;
    reason: string | null;
    status: "pending" | "approved" | "denied" | "expired" | "consumed";
    created_at: string;
}

export const getPendingExportApprovals = () =>
    request<ExportApprovalRequest[]>("/api/admin/export-approvals/pending");

export const approveExportRequest = (requestId: string, decisionNote = "") =>
    request<ExportApprovalRequest>(`/api/admin/export-approvals/${requestId}/approve`, {
        method: "POST",
        body: JSON.stringify({ decision_note: decisionNote }),
    });

export const denyExportRequest = (requestId: string, decisionNote = "") =>
    request<ExportApprovalRequest>(`/api/admin/export-approvals/${requestId}/deny`, {
        method: "POST",
        body: JSON.stringify({ decision_note: decisionNote }),
    });

/* ── Driver Document Verification ─────────
   Moved to lib/api/driver-documents.ts, re-exported above. */

/* ── Bulk Driver Import (CSV) ─────────────── */
export interface DriverImportReportItem {
    old_driver_id: string;
    field: string;
    message: string;
}
export interface DriverImportReport {
    batch: string;
    can_commit: boolean;
    counts: { rows: number; users: number; drivers: number; updated: number; skipped_resume: number };
    warnings: DriverImportReportItem[];
    errors: DriverImportReportItem[];
}
export interface DriverImportCommitResult {
    batch: string;
    committed: boolean;
    imported_users?: number;
    imported_drivers?: number;
    updated_drivers?: number;
    warnings?: DriverImportReportItem[];
    // Present (with can_commit=false) when the commit was refused on errors.
    can_commit?: boolean;
    counts?: DriverImportReport["counts"];
    errors?: DriverImportReportItem[];
}
export interface DriverImportOptions {
    serviceAreaId?: string;
    serviceAreaName?: string;
    batch?: string;
}

function driverImportFormData(file: File, opts?: DriverImportOptions): FormData {
    const fd = new FormData();
    fd.append("drivers_csv", file);
    if (opts?.serviceAreaId) fd.append("service_area_id", opts.serviceAreaId);
    if (opts?.serviceAreaName) fd.append("service_area_name", opts.serviceAreaName);
    if (opts?.batch) fd.append("batch", opts.batch);
    return fd;
}

/** Dry-run: parse + validate the drivers CSV and return the report (no writes). */
export const adminValidateDriverImport = (file: File, opts?: DriverImportOptions) =>
    request<DriverImportReport>("/api/admin/drivers/import/validate", {
        method: "POST",
        body: driverImportFormData(file, opts),
    });

/** Commit the import. Returns committed=false + errors if the CSV no longer validates. */
export const adminCommitDriverImport = (file: File, opts?: DriverImportOptions) =>
    request<DriverImportCommitResult>("/api/admin/drivers/import/commit", {
        method: "POST",
        body: driverImportFormData(file, opts),
    });

/* ── Legacy Booking Import (4 CSVs) ───────── */
// Super-admin-only (backend/routes/admin/booking_import.py). Imports completed
// rides from the previous app into `rides`, plus one offsetting `payouts` row
// per driver so already-settled earnings never become withdrawable again.
// Takes four files in one request — customers/drivers supply the phone numbers
// used to match Spinr accounts, and driverearnings supplies each booking's
// actual payout, which is what the offset must cancel.
export interface BookingImportReportItem {
    row_num: number;
    booking_code: string;
    field: string;
    message: string;
}
/** Mirrors BookingImportPlan.stats — all ints except the sum_* money fields. */
export interface BookingImportCounts {
    bookings_read: number;
    skipped_not_completed: number;
    skipped_test_account: number;
    target_rows: number;
    skipped_already_imported: number;
    skipped_unmatched_both: number;
    rides_planned: number;
    unmatched_riders: number;
    unmatched_drivers: number;
    earnings_fallback_rows: number;
    missing_start_rows: number;
    payouts_planned: number;
    payouts_skipped_existing: number;
    drivers_to_recount: number;
    sum_rider_paid: number;
    sum_driver_total: number;
    sum_offset_payouts: number;
    sum_tips: number;
    sum_tax: number;
}
export interface BookingImportReport {
    batch: string;
    can_commit: boolean;
    counts: BookingImportCounts;
    warnings: BookingImportReportItem[];
    errors: BookingImportReportItem[];
}
export interface BookingImportCommitResult {
    batch: string;
    committed: boolean;
    imported_rides?: number;
    offset_payouts?: number;
    drivers_recounted?: number;
    counts?: BookingImportCounts;
    warnings?: BookingImportReportItem[];
    // Present (with can_commit=false) when the commit was refused.
    can_commit?: boolean;
    errors?: BookingImportReportItem[];
}
export interface BookingImportFiles {
    bookings: File;
    customers: File;
    drivers: File;
    earnings: File;
}
export interface BookingImportOptions {
    serviceAreaId?: string;
    serviceAreaName?: string;
    vehicleTypeId?: string;
    vehicleTypeName?: string;
    batch?: string;
}

function bookingImportFormData(files: BookingImportFiles, opts?: BookingImportOptions): FormData {
    const fd = new FormData();
    fd.append("bookings_csv", files.bookings);
    fd.append("customers_csv", files.customers);
    fd.append("drivers_csv", files.drivers);
    fd.append("earnings_csv", files.earnings);
    if (opts?.serviceAreaId) fd.append("service_area_id", opts.serviceAreaId);
    if (opts?.serviceAreaName) fd.append("service_area_name", opts.serviceAreaName);
    if (opts?.vehicleTypeId) fd.append("vehicle_type_id", opts.vehicleTypeId);
    if (opts?.vehicleTypeName) fd.append("vehicle_type_name", opts.vehicleTypeName);
    if (opts?.batch) fd.append("batch", opts.batch);
    return fd;
}

/** Dry-run: parse + validate the four CSVs and return the report (no writes). */
export const adminValidateBookingImport = (files: BookingImportFiles, opts?: BookingImportOptions) =>
    request<BookingImportReport>("/api/admin/bookings/import/validate", {
        method: "POST",
        body: bookingImportFormData(files, opts),
    });

/**
 * Commit the import. Returns committed=false + errors if the CSVs no longer
 * validate, or if there is nothing left to import (a completed re-run).
 * Pass the batch from the validate response so payout IDs stay deterministic.
 */
export const adminCommitBookingImport = (files: BookingImportFiles, opts?: BookingImportOptions) =>
    request<BookingImportCommitResult>("/api/admin/bookings/import/commit", {
        method: "POST",
        body: bookingImportFormData(files, opts),
    });

/* ── Legacy Stripe Mapping Import (CSV) ───── */
// Super-admin-only endpoints (backend/routes/admin/stripe_import.py). Maps
// old-app Stripe IDs onto imported rows: drivers.stripe_account_id (payout
// destination) / users.stripe_customer_id (saved cards).
export type StripeImportKind = "drivers" | "riders";
export interface StripeImportReportItem {
    row_ref: string;
    field: string;
    message: string;
}
// Drivers already carrying a DIFFERENT account than the CSV. Non-blocking:
// surfaced for an explicit per-driver update. PII-free (name is resolved in the
// UI by driver_id, never carried in the report).
export interface StripeImportNeedsUpdateItem {
    row_ref: string;
    driver_id: string;
    current_stripe_account_id: string;
    new_stripe_account_id: string;
}
export interface StripeImportReport {
    batch: string;
    kind: StripeImportKind;
    can_commit: boolean;
    counts: { rows: number; to_map: number; skipped_already_mapped: number; needs_update: number };
    warnings: StripeImportReportItem[];
    errors: StripeImportReportItem[];
    needs_update: StripeImportNeedsUpdateItem[];
}
export interface StripeImportCommitResult {
    batch: string;
    kind: StripeImportKind;
    committed: boolean;
    updated_drivers?: number;
    updated_users?: number;
    conflicts?: string[];
    kyc_sync?: "started" | "not_applicable";
    warnings?: StripeImportReportItem[];
    // Present (with can_commit=false) when the commit was refused on errors.
    can_commit?: boolean;
    counts?: StripeImportReport["counts"];
    errors?: StripeImportReportItem[];
    needs_update?: StripeImportNeedsUpdateItem[];
}
export interface StripeImportStatus {
    batch: string;
    drivers: number;
    kyc_sync: Record<string, number>;
    payouts_enabled: number;
    details_submitted: number;
}

function stripeImportFormData(file: File, kind: StripeImportKind, batch?: string): FormData {
    const fd = new FormData();
    fd.append("mapping_csv", file);
    fd.append("kind", kind);
    if (batch) fd.append("batch", batch);
    return fd;
}

/** Dry-run: parse, match, and live-validate the mapping CSV (no writes). */
export const adminValidateStripeImport = (file: File, kind: StripeImportKind, batch?: string) =>
    request<StripeImportReport>("/api/admin/stripe/import/validate", {
        method: "POST",
        body: stripeImportFormData(file, kind, batch),
    });

/** Commit the mapping. Returns committed=false + errors if the CSV no longer validates. */
export const adminCommitStripeImport = (file: File, kind: StripeImportKind, batch?: string) =>
    request<StripeImportCommitResult>("/api/admin/stripe/import/commit", {
        method: "POST",
        body: stripeImportFormData(file, kind, batch),
    });

/** Per-batch KYC-sync convergence (drivers kind only). DB-only, cheap to poll. */
export const adminStripeImportStatus = (batch: string) =>
    request<StripeImportStatus>(
        `/api/admin/stripe/import/status?batch=${encodeURIComponent(batch)}`,
    );

export interface StripeDriverAccountUpdateResult {
    ok: boolean;
    status: "updated";
    driver_id: string;
    batch: string;
    warnings: StripeImportReportItem[];
}

/**
 * Overwrite ONE driver's payout account (resolves a `needs_update` row). This
 * redirects where the driver is paid, so it is super-admin-only, live-validated,
 * and confirmed in the UI. On failure `request` throws with the backend's
 * human-readable message (409 = stale/id-taken, 422 = bad account/validation).
 */
export const adminUpdateDriverStripeAccount = (body: {
    driver_id: string;
    new_stripe_account_id: string;
    current_stripe_account_id: string;
    batch?: string;
}) =>
    request<StripeDriverAccountUpdateResult>("/api/admin/stripe/import/update-driver", {
        method: "POST",
        body: JSON.stringify(body),
    });

/* ── Bulk Rider Import (CSV) ──────────────── */
export interface RiderImportReportItem {
    row_num: number;
    field: string;
    message: string;
}
export interface RiderImportDuplicate {
    row: number;
    phone: string;
    match_type: "rider" | "driver";
    is_driver: boolean;
    existing_user_id: string;
}
export interface RiderImportReport {
    batch: string;
    can_commit: boolean;
    counts: {
        rows: number;
        to_create: number;
        to_update: number;
        duplicates: number;
        duplicate_drivers: number;
    };
    duplicates: RiderImportDuplicate[];
    warnings: RiderImportReportItem[];
    errors: RiderImportReportItem[];
}
export interface RiderImportCommitResult {
    batch: string;
    committed: boolean;
    created_users?: number;
    updated_users?: number;
    duplicates?: RiderImportDuplicate[];
    warnings?: RiderImportReportItem[];
    can_commit?: boolean;
    counts?: RiderImportReport["counts"];
    errors?: RiderImportReportItem[];
}

function riderImportFormData(file: File, batch?: string): FormData {
    const fd = new FormData();
    fd.append("riders_csv", file);
    if (batch) fd.append("batch", batch);
    return fd;
}

/** Dry-run: parse + validate the riders CSV and return the report (no writes). */
export const adminValidateRiderImport = (file: File, batch?: string) =>
    request<RiderImportReport>("/api/admin/riders/import/validate", {
        method: "POST",
        body: riderImportFormData(file, batch),
    });

/** Commit the rider import. Returns committed=false + errors if the CSV no longer validates. */
export const adminCommitRiderImport = (file: File, batch?: string) =>
    request<RiderImportCommitResult>("/api/admin/riders/import/commit", {
        method: "POST",
        body: riderImportFormData(file, batch),
    });

/* ── Manual Admin Document Upload ─────────
   Moved to lib/api/driver-documents.ts, re-exported above. */

export interface ApprovalQueueItem {
    driver_id: string;
    user_id: string | null;
    first_name: string;
    last_name: string;
    name: string;
    email: string | null;
    phone: string | null;
    profile_photo_url: string | null;
    status: string;
    created_at: string | null;
    queue_started_at: string | null;
    time_in_queue_seconds: number;
    pending_docs_count: number;
    missing_docs_count: number;
    service_area_id: string | null;
    service_area_name: string | null;
    vehicle_type_id: string | null;
    vehicle_type_name: string | null;
    profile_image_status: string | null;
    is_new_applicant: boolean;
    is_resubmission: boolean;
    has_pending_photo: boolean;
}

export interface ApprovalQueueResponse {
    stats: {
        total_pending: number;
        oldest_in_queue_hours: number;
        median_wait_hours: number;
        over_24h_count: number;
        new_applicants: number;
        resubmissions: number;
        photo_review: number;
    };
    items: ApprovalQueueItem[];
}

export const getApprovalQueue = (params?: {
    limit?: number;
    service_area_id?: string;
}) => {
    const q = new URLSearchParams();
    if (params?.limit) q.set("limit", String(params.limit));
    if (params?.service_area_id) q.set("service_area_id", params.service_area_id);
    const qs = q.toString();
    return request<ApprovalQueueResponse>(
        `/api/admin/drivers/approval-queue${qs ? `?${qs}` : ""}`,
    );
};

export interface ExpiringDocItem {
    driver_id: string;
    user_id: string | null;
    name: string;
    first_name: string;
    last_name: string;
    email: string | null;
    phone: string | null;
    profile_photo_url: string | null;
    status: string | null;
    service_area_id: string | null;
    service_area_name: string | null;
    doc_type: string;
    doc_label: string;
    doc_field: string;
    expiry_date: string;
    days_remaining: number;
    rides_last_30d: number;
    last_nudged_at: string | null;
}

export const getExpiringDocs = (params?: {
    window_days?: 7 | 14 | 30 | number;
    service_area_id?: string;
}) => {
    const q = new URLSearchParams();
    if (params?.window_days) q.set("window_days", String(params.window_days));
    if (params?.service_area_id) q.set("service_area_id", params.service_area_id);
    const qs = q.toString();
    return request<{ items: ExpiringDocItem[] }>(
        `/api/admin/drivers/expiring${qs ? `?${qs}` : ""}`,
    );
};

export const nudgeDriverExpiry = (
    driverId: string,
    body: { doc_type: string; doc_label?: string; custom_message?: string },
) =>
    request<{ ok: boolean }>(`/api/admin/drivers/${driverId}/nudge-expiry`, {
        method: "POST",
        body: JSON.stringify(body),
    });

/* ── Corporate Accounts ─────────────────────── */
export type CompanyStatus =
    | "pending_verification"
    | "active"
    | "suspended"
    | "closed";

export type SizeTier = "smb" | "mid_market" | "enterprise";

export interface CorporateAccount {
    id: string;
    name: string;
    legal_name?: string | null;
    business_number?: string | null;
    tax_region?: string | null;
    billing_email?: string | null;
    contact_name?: string | null;
    contact_email?: string | null;
    contact_phone?: string | null;
    status: CompanyStatus;
    size_tier: SizeTier;
    kyb_document_url?: string | null;
    kyb_reviewed_at?: string | null;
    kyb_reviewed_by?: string | null;
    kyb_submitted_at?: string | null;
    kyb_review_note?: string | null;
    kyb_last_decision?: "approved" | "rejected" | null;
    credit_limit?: number;
    is_active: boolean;
    created_at: string;
    updated_at: string;
}

export const getCorporateAccounts = () =>
    request<CorporateAccount[]>("/api/admin/corporate-accounts");

export const listCorporateAccounts = (opts: {
    status?: CompanyStatus;
    size_tier?: SizeTier;
    search?: string;
    skip?: number;
    limit?: number;
} = {}) => {
    const p = new URLSearchParams();
    if (opts.status) p.set("status", opts.status);
    if (opts.size_tier) p.set("size_tier", opts.size_tier);
    if (opts.search) p.set("search", opts.search);
    if (opts.skip != null) p.set("skip", String(opts.skip));
    if (opts.limit != null) p.set("limit", String(opts.limit));
    const qs = p.toString();
    return request<CorporateAccount[]>(
        `/api/admin/corporate-accounts${qs ? `?${qs}` : ""}`
    );
};

export const reviewKyb = (id: string, decision: { approve: boolean; note?: string }) =>
    request<CorporateAccount>(`/api/admin/corporate-accounts/${id}/kyb-review`, {
        method: "POST",
        body: JSON.stringify(decision),
    });

export const getCorporateAccount = (id: string) =>
    request<CorporateAccount>(`/api/admin/corporate-accounts/${id}`);

export const changeCompanyStatus = (
    id: string,
    transition: { status: CompanyStatus; reason?: string }
) =>
    request<CorporateAccount>(`/api/admin/corporate-accounts/${id}/status`, {
        method: "POST",
        body: JSON.stringify(transition),
    });

// Blob-fetch the KYB document through the backend streaming endpoint
// (kyb_document_url is a raw PRIVATE-bucket key, not a browser-usable URL).
export async function fetchKybDocumentBlob(id: string): Promise<Blob> {
    const token = useAuthStore.getState().token;
    const res = await fetch(`/api/admin/corporate-accounts/${id}/kyb/view`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) throw new Error(`Could not load document (${res.status})`);
    return res.blob();
}

export const createCorporateAccount = (data: any) =>
    request<CorporateAccount>("/api/admin/corporate-accounts", {
        method: "POST",
        body: JSON.stringify(data),
    });

export const updateCorporateAccount = (id: string, data: any) =>
    request<CorporateAccount>(`/api/admin/corporate-accounts/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });

export const deleteCorporateAccount = (id: string) =>
    request<any>(`/api/admin/corporate-accounts/${id}`, { method: "DELETE" });

/* ── Corporate Wallet ─────────────────────── */
export interface WalletTxn {
    id: string;
    type: string;
    scope: string;
    amount: string;
    balance_after: string;
    created_at: string;
    notes?: string | null;
    ride_id?: string | null;
    member_id?: string | null;
}

export interface CorporateWallet {
    id: string;
    company_id: string;
    balance: string;
    currency: string;
    auto_topup_enabled: boolean;
    auto_topup_threshold: string | null;
    auto_topup_amount: string | null;
    auto_topup_daily_cap: string;
    soft_negative_floor: string;
    transactions: WalletTxn[];
}

export type WalletConfigPatch = Partial<
    Pick<
        CorporateWallet,
        | "auto_topup_enabled"
        | "auto_topup_threshold"
        | "auto_topup_amount"
        | "auto_topup_daily_cap"
    >
>;

export const getCorporateWallet = (companyId: string) =>
    request<CorporateWallet>(`/api/admin/corporate-accounts/${companyId}/wallet`);

export const updateWalletConfig = (companyId: string, patch: WalletConfigPatch) =>
    request<CorporateWallet>(
        `/api/admin/corporate-accounts/${companyId}/wallet/config`,
        { method: "PUT", body: JSON.stringify(patch) }
    );

export const walletTopupIntent = (
    companyId: string,
    body: { amount: number; payment_method_id?: string }
) =>
    request<{ payment_intent_id: string; client_secret: string }>(
        `/api/admin/corporate-accounts/${companyId}/wallet/topup`,
        { method: "POST", body: JSON.stringify(body) }
    );

export const walletAdjust = (
    companyId: string,
    body: { amount: number; notes: string }
) =>
    request<{ transaction_id: string; balance_after: string }>(
        `/api/admin/corporate-accounts/${companyId}/wallet/adjust`,
        { method: "POST", body: JSON.stringify(body) }
    );

/* ── Corporate members / allowances (Plan 3) ── */
export type CorporateMemberRole = "owner" | "admin" | "member";
export type CorporateMemberStatus = "invited" | "active" | "suspended" | "removed";
export type AllowanceTypeValue = "fixed_recurring" | "one_time" | "unlimited";

export interface CorporateMember {
    id: string;
    company_id: string;
    user_id?: string | null;
    role: CorporateMemberRole;
    status: CorporateMemberStatus;
    invited_email?: string | null;
    created_at?: string;
    updated_at?: string;
}

export interface CorporateAllowance {
    id: string;
    member_id: string;
    type: AllowanceTypeValue;
    amount?: number | null;
    used: number;
    period_start?: string | null;
    period_end?: string | null;
    rollover?: boolean;
    auto_approve_topup_amount?: number | null;
    auto_approve_monthly_count?: number | null;
    status: "active" | "paused" | "expired";
}

export interface AllowanceRequestRow {
    id: string;
    member_id: string;
    amount: number;
    reason: string;
    status: "pending" | "approved" | "denied" | "auto_approved";
    reviewed_by?: string | null;
    decision_notes?: string | null;
    created_at?: string;
}

export const listCompanyMembers = (companyId: string, status?: string) =>
    request<CorporateMember[]>(
        `/api/company/${companyId}/members${status ? `?status=${encodeURIComponent(status)}` : ""}`
    );

export const inviteCompanyMember = (
    companyId: string,
    body: { email: string; role: CorporateMemberRole; policy_override?: boolean }
) =>
    request<{ member: CorporateMember; invite_url: string }>(
        `/api/company/${companyId}/members/invite`,
        { method: "POST", body: JSON.stringify(body) }
    );

export const removeCompanyMember = (companyId: string, memberId: string) =>
    request<CorporateMember>(`/api/company/${companyId}/members/${memberId}`, {
        method: "DELETE",
    });

export const getMemberAllowance = (companyId: string, memberId: string) =>
    request<CorporateAllowance | Record<string, never>>(
        `/api/company/${companyId}/members/${memberId}/allowance`
    );

export const putMemberAllowance = (
    companyId: string,
    memberId: string,
    body: {
        type: AllowanceTypeValue;
        amount?: number | null;
        period_start?: string | null;
        period_end?: string | null;
        rollover?: boolean;
        auto_approve_topup_amount?: number | null;
        auto_approve_monthly_count?: number | null;
    }
) =>
    request<CorporateAllowance>(
        `/api/company/${companyId}/members/${memberId}/allowance`,
        { method: "PUT", body: JSON.stringify(body) }
    );

export const listCompanyAllowanceRequests = (companyId: string, status = "pending") =>
    request<AllowanceRequestRow[]>(
        `/api/company/${companyId}/allowance-requests?status=${encodeURIComponent(status)}`
    );

export const decideAllowanceRequest = (
    companyId: string,
    requestId: string,
    body: { approve: boolean; note?: string }
) =>
    request<AllowanceRequestRow>(
        `/api/company/${companyId}/allowance-requests/${requestId}/decide`,
        { method: "POST", body: JSON.stringify(body) }
    );

export const updateCompanyMember = (
    companyId: string,
    memberId: string,
    body: { role?: CorporateMemberRole; status?: CorporateMemberStatus; policy_override?: boolean }
) =>
    request<CorporateMember>(
        `/api/company/${companyId}/members/${memberId}`,
        { method: "PATCH", body: JSON.stringify(body) }
    );

/* ── Company policy (Plan 6) ── */
export type PaymentSourcePolicy = "allowance_only" | "master_only" | "both";

export interface TimeWindowPolicy {
    day: "mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun";
    start: string;
    end: string;
}

export interface CorporatePolicy {
    id?: string;
    company_id?: string;
    active: boolean;
    max_fare_per_ride?: number | null;
    allowed_geofence?: Record<string, unknown> | null;
    allowed_time_windows?: TimeWindowPolicy[] | null;
    allowed_payment_source: PaymentSourcePolicy;
}

export const getCompanyPolicy = (companyId: string) =>
    request<CorporatePolicy | Record<string, never>>(`/api/company/${companyId}/policy`);

export const putCompanyPolicy = (
    companyId: string,
    body: Omit<CorporatePolicy, "id" | "company_id">
) =>
    request<CorporatePolicy>(`/api/company/${companyId}/policy`, {
        method: "PUT",
        body: JSON.stringify(body),
    });

export const patchCompanyPolicy = (
    companyId: string,
    body: Partial<Omit<CorporatePolicy, "id" | "company_id">>
) =>
    request<CorporatePolicy>(`/api/company/${companyId}/policy`, {
        method: "PATCH",
        body: JSON.stringify(body),
    });

/* ── Company allowed domains (Plan 7) ── */
export interface AllowedDomainRow {
    company_id: string;
    domain: string;
}

export const listAllowedDomains = (companyId: string) =>
    request<AllowedDomainRow[]>(`/api/company/${companyId}/allowed-domains`);

export const addAllowedDomain = (companyId: string, domain: string) =>
    request<AllowedDomainRow>(`/api/company/${companyId}/allowed-domains`, {
        method: "POST",
        body: JSON.stringify({ domain }),
    });

export const removeAllowedDomain = (companyId: string, domain: string) =>
    request<{ status: string }>(
        `/api/company/${companyId}/allowed-domains/${encodeURIComponent(domain)}`,
        { method: "DELETE" }
    );

/* ── Company billing (Plan 6) ── */
export interface BillingMemberBreakdown {
    member_id: string;
    ride_count: number;
    allowance_total: number;
    master_total: number;
    total: number;
}

export interface BillingSummary {
    month: string;
    wallet_balance: number;
    wallet_currency: string;
    ride_count: number;
    allowance_total: number;
    master_total: number;
    total: number;
    avg_fare: number;
    by_member: BillingMemberBreakdown[];
}

export interface BillingLineItem {
    ride_id: string;
    member_id: string;
    source_type: string;
    allowance_debit_amount: number;
    master_fallback_amount: number;
    policy_check_result?: string;
    created_at: string;
}

export interface BillingStatement {
    month: string;
    from: string;
    to: string;
    line_items: BillingLineItem[];
    summary: {
        ride_count: number;
        allowance_total: number;
        master_total: number;
        total: number;
        avg_fare: number;
        by_member: BillingMemberBreakdown[];
    };
}

export interface BillingTransaction {
    id: string;
    type: string;
    amount: number;
    balance_after?: number;
    notes?: string | null;
    ride_id?: string | null;
    member_id?: string | null;
    stripe_payment_intent_id?: string | null;
    created_at: string;
}

export interface BillingTransactionsPage {
    wallet_id: string;
    balance: number;
    currency: string;
    transactions: BillingTransaction[];
}

export const getCompanyBillingSummary = (companyId: string, month?: string) => {
    const qs = month ? `?month=${encodeURIComponent(month)}` : "";
    return request<BillingSummary>(`/api/company/${companyId}/billing/summary${qs}`);
};

export const getCompanyBillingStatement = (companyId: string, month: string) =>
    request<BillingStatement>(
        `/api/company/${companyId}/billing/statements/${encodeURIComponent(month)}`
    );

export const getCompanyBillingTransactions = (
    companyId: string,
    skip = 0,
    limit = 50
) =>
    request<BillingTransactionsPage>(
        `/api/company/${companyId}/billing/transactions?skip=${skip}&limit=${limit}`
    );

/* ── Cloud Messaging (merged with Notifications) ── */
export const getCloudMessages = (status?: string, audience?: string) => {
    const params = new URLSearchParams();
    if (status) params.set('status', status);
    if (audience) params.set('audience', audience);
    return request<any[]>(`/api/admin/cloud-messaging?${params.toString()}`);
};

export const sendCloudMessage = (data: {
    title: string;
    description: string;
    audience: string;
    channels: string[];
    type?: string;
    particular_ids?: string[];
    scheduled_at?: string;
    is_marketing?: boolean;
    service_area_id?: string;
}) =>
    request<any>("/api/admin/cloud-messaging/send", {
        method: "POST",
        body: JSON.stringify(data),
    });

export const getCloudMessageStats = () =>
    request<any>("/api/admin/cloud-messaging/stats");

export const deleteCloudMessage = (id: string) =>
    request<any>(`/api/admin/cloud-messaging/${id}`, { method: "DELETE" });

/* ── Marketing audience preview + suppression list ──────────────────── */

export const getCloudMessageAudiencePreview = (audience: string, serviceAreaId?: string) => {
    const params = new URLSearchParams({ audience });
    if (serviceAreaId) params.set("service_area_id", serviceAreaId);
    return request<{
        audience: string;
        audience_total: number;
        email_opted_in: number | null;
        sms_opted_in: number | null;
        push_opted_in: number | null;
    }>(`/api/admin/cloud-messaging/audience-preview?${params.toString()}`);
};

export const getMarketingSuppressions = (channel?: string) => {
    const params = new URLSearchParams();
    if (channel) params.set("channel", channel);
    return request<any[]>(`/api/admin/marketing/suppressions?${params.toString()}`);
};

export const addMarketingSuppression = (data: { channel: string; target: string; reason?: string }) =>
    request<any>("/api/admin/marketing/suppressions", { method: "POST", body: JSON.stringify(data) });

export const deleteMarketingSuppression = (id: string) =>
    request<any>(`/api/admin/marketing/suppressions/${id}`, { method: "DELETE" });

/* ── Promotions Usage & Stats ──────────────────── */
export const getPromoUsage = (params?: { promo_id?: string; date_from?: string; date_to?: string; limit?: number; offset?: number }) => {
    const sp = new URLSearchParams();
    if (params?.promo_id) sp.set('promo_id', params.promo_id);
    if (params?.date_from) sp.set('date_from', params.date_from);
    if (params?.date_to) sp.set('date_to', params.date_to);
    if (params?.limit) sp.set('limit', params.limit.toString());
    if (params?.offset) sp.set('offset', params.offset.toString());
    return request<any[]>(`/api/admin/promotions/usage?${sp.toString()}`);
};

export const getPromoStats = (range?: string) => {
    const sp = new URLSearchParams();
    if (range) sp.set('range', range);
    return request<any>(`/api/admin/promotions/stats?${sp.toString()}`);
};

/* ── Users (Riders + Drivers + Admins) ───────── */
// NOTE: no `search` param here on purpose — search terms can be phone
// numbers/emails and GET query strings land in browser history and proxy
// logs. Use the POST-based adminSearchUsers for user search.
export const getUsers = (role: "all" | "rider" | "driver" | "admin" = "all") =>
    request<any[]>(`/api/admin/users?role=${role}`);

export const getUsersPaginated = (opts: {
    role?: "all" | "rider" | "driver" | "both" | "admin";
    search?: string;
    limit?: number;
    offset?: number;
} = {}) => {
    const sp = new URLSearchParams();
    sp.set("role", opts.role ?? "all");
    if (opts.search) sp.set("search", opts.search);
    if (opts.limit != null) sp.set("limit", String(opts.limit));
    if (opts.offset != null) sp.set("offset", String(opts.offset));
    return request<any[]>(`/api/admin/users?${sp.toString()}`);
};

export const getUserDetails = (id: string) =>
    request<any>(`/api/admin/users/${id}`);

export const updateUserStatus = (id: string, statusData: any) =>
    request<any>(`/api/admin/users/${id}/status`, {
        method: "PUT",
        body: JSON.stringify(statusData),
    });

export const updateUserFlags = (id: string, flags: { is_rider?: boolean; is_driver?: boolean }) =>
    request<any>(`/api/admin/users/${id}/role`, {
        method: "PATCH",
        body: JSON.stringify(flags),
    });

export const exportUsers = (limit = 1000) =>
    request<{ users: any[]; count: number }>(`/api/admin/export/users?limit=${limit}`);

export const logPiiReveal = (entityType: string, entityId: string) =>
    request<{ ok: boolean }>("/api/admin/audit/pii-reveal", {
        method: "POST",
        body: JSON.stringify({ entity_type: entityType, entity_id: entityId }),
    });

/* ── Wallet (admin) ─────────────────────────── */
export const getUserWallet = (userId: string, limit = 50) =>
    request<{
        user: { id: string; name: string; phone: string; email: string };
        wallet: { id: string; balance: number; currency: string; is_active: boolean };
        transactions: Array<{
            id: string;
            type: string;
            amount: number;
            balance_after: number;
            description: string | null;
            reference_id: string | null;
            metadata: Record<string, any>;
            created_at: string;
        }>;
    }>(`/api/admin/wallet/${userId}?limit=${limit}`);

export const creditUserWallet = (userId: string, amount: number, reason: string) =>
    request<{ balance: number; transaction_id: string; audit_log_id?: string }>(`/api/admin/wallet/credit`, {
        method: "POST",
        body: JSON.stringify({ user_id: userId, amount, reason }),
    });

export const debitUserWallet = (userId: string, amount: number, reason: string) =>
    request<{ balance: number; transaction_id: string; audit_log_id?: string }>(`/api/admin/wallet/debit`, {
        method: "POST",
        body: JSON.stringify({ user_id: userId, amount, reason }),
    });

/* ── Promotions ─────────────────────────────── */
export const getPromotions = (opts: {
    limit?: number;
    offset?: number;
    promo_type?: "public" | "private";
    status?: "active" | "inactive" | "not_expired" | "expired";
    search?: string;
} = {}) => {
    const sp = new URLSearchParams();
    if (opts.limit != null) sp.set("limit", String(opts.limit));
    if (opts.offset != null) sp.set("offset", String(opts.offset));
    if (opts.promo_type) sp.set("promo_type", opts.promo_type);
    if (opts.status) sp.set("status", opts.status);
    if (opts.search) sp.set("search", opts.search);
    const qs = sp.toString();
    return request<any[]>(`/api/admin/promotions${qs ? `?${qs}` : ""}`);
};

export const createPromotion = (data: any) =>
    request<any>("/api/admin/promotions", {
        method: "POST",
        body: JSON.stringify(data),
    });

export const updatePromotion = (id: string, data: any) =>
    request<any>(`/api/admin/promotions/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });

export const deletePromotion = (id: string) =>
    request<any>(`/api/admin/promotions/${id}`, { method: "DELETE" });

/* ── Disputes ───────────────────────────────── */
export const getDisputes = (opts: { limit?: number; offset?: number; status?: string } = {}) => {
    const sp = new URLSearchParams();
    if (opts.limit != null) sp.set("limit", String(opts.limit));
    if (opts.offset != null) sp.set("offset", String(opts.offset));
    if (opts.status && opts.status !== "all") sp.set("status", opts.status);
    const qs = sp.toString();
    return request<any[]>(`/api/admin/disputes${qs ? `?${qs}` : ""}`);
};

export const getDisputeStats = () =>
    request<{ open: number; under_review: number; resolved: number; rejected: number; total_refunded: number }>(
        "/api/admin/disputes/stats"
    );

export const getDisputeDetails = (id: string) =>
    request<any>(`/api/admin/disputes/${id}`);

export const createDispute = (data: any) =>
    request<any>("/api/admin/disputes", {
        method: "POST",
        body: JSON.stringify(data),
    });

export const updateDispute = (id: string, data: any) =>
    request<any>(`/api/admin/disputes/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });

/* ── Safety Queue ───────────────────────────── */
export type SafetyStatus = "open" | "in_progress" | "resolved" | "closed" | "duplicate";
export type SafetySeverity = "sev1" | "sev2" | "sev3";
export type SafetyRole = "rider" | "driver" | "system";

export interface SafetyIncident {
    id: string;
    reported_by_user_id: string | null;
    role: SafetyRole;
    category: string;
    description: string;
    status: SafetyStatus;
    severity: SafetySeverity | null;
    ride_id: string | null;
    latitude: number | null;
    longitude: number | null;
    location_accuracy: number | null;
    assigned_to_admin_id: string | null;
    resolved_at: string | null;
    resolved_by: string | null;
    resolution_notes: string | null;
    reported_at: string;
    created_at: string;
    updated_at: string;
    reporter_name?: string | null;
}

export interface SafetyIncidentListResponse {
    items: SafetyIncident[];
    total: number;
    offset: number;
    limit: number;
    open_count: number | null;
}

export interface SafetyIncidentDetail {
    incident: SafetyIncident;
    reporter: {
        id: string | null;
        name: string | null;
        email: string | null;
        phone: string | null;
        role: string | null;
    } | null;
    ride: {
        id: string | null;
        ride_code: string | null;
        status: string | null;
        rider_id: string | null;
        driver_id: string | null;
        pickup_address: string | null;
        dropoff_address: string | null;
        total_fare: number | null;
        started_at: string | null;
        completed_at: string | null;
    } | null;
}

export const getSafetyIncidents = (params?: {
    status?: SafetyStatus;
    severity?: SafetySeverity;
    role?: SafetyRole;
    category?: string;
    ride_id?: string;
    search?: string;
    limit?: number;
    offset?: number;
}) => {
    const sp = new URLSearchParams();
    if (params?.status) sp.set("status", params.status);
    if (params?.severity) sp.set("severity", params.severity);
    if (params?.role) sp.set("role", params.role);
    if (params?.category) sp.set("category", params.category);
    if (params?.ride_id) sp.set("ride_id", params.ride_id);
    if (params?.search) sp.set("search", params.search);
    if (params?.limit != null) sp.set("limit", String(params.limit));
    if (params?.offset != null) sp.set("offset", String(params.offset));
    const qs = sp.toString();
    return request<SafetyIncidentListResponse>(`/api/admin/safety/incidents${qs ? `?${qs}` : ""}`);
};

export const getSafetyIncident = (id: string) =>
    request<SafetyIncidentDetail>(`/api/admin/safety/incidents/${id}`);

export const updateSafetyIncident = (
    id: string,
    body: Partial<{
        status: SafetyStatus;
        severity: SafetySeverity;
        assigned_to_admin_id: string;
        resolution_notes: string;
    }>,
) =>
    request<{ updated: boolean; incident: SafetyIncident }>(
        `/api/admin/safety/incidents/${id}`,
        { method: "PATCH", body: JSON.stringify(body) },
    );


/* ── Support Tickets ────────────────────────── */
export const getTickets = (opts: {
    limit?: number;
    offset?: number;
    status?: string;
    service_area_id?: string;
} = {}) => {
    const sp = new URLSearchParams();
    if (opts.limit != null) sp.set("limit", String(opts.limit));
    if (opts.offset != null) sp.set("offset", String(opts.offset));
    if (opts.status) sp.set("status", opts.status);
    if (opts.service_area_id) sp.set("service_area_id", opts.service_area_id);
    const qs = sp.toString();
    return request<any[]>(`/api/admin/tickets${qs ? `?${qs}` : ""}`);
};

export const getTicketDetails = (id: string) =>
    request<any>(`/api/admin/tickets/${id}`);

export const createTicket = (data: any) =>
    request<any>("/api/admin/tickets", {
        method: "POST",
        body: JSON.stringify(data),
    });

export const updateTicket = (id: string, data: any) =>
    request<any>(`/api/admin/tickets/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });

export const replyToTicket = (id: string, message: string) =>
    request<any>(`/api/admin/tickets/${id}/reply`, {
        method: "POST",
        body: JSON.stringify({ message }),
    });

export const closeTicket = (id: string) =>
    request<any>(`/api/admin/tickets/${id}/close`, { method: "POST" });

export const deleteTicket = (id: string) =>
    request<any>(`/api/admin/tickets/${id}`, { method: "DELETE" });

/* ── FAQs ───────────────────────────────────── */
export const getFaqs = () =>
    request<any[]>("/api/admin/faqs");

export const createFaq = (data: any) =>
    request<any>("/api/admin/faqs", {
        method: "POST",
        body: JSON.stringify(data),
    });

export const updateFaq = (id: string, data: any) =>
    request<any>(`/api/admin/faqs/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });

export const deleteFaq = (id: string) =>
    request<any>(`/api/admin/faqs/${id}`, { method: "DELETE" });

/* ── Legal Documents (per-audience ToS / Privacy) ─────────────── */
export const getLegalDocuments = () =>
    request<any[]>("/api/admin/legal-documents");

export const upsertLegalDocument = (data: {
    audience: "rider" | "driver";
    type: "tos" | "privacy";
    content: string;
}) =>
    request<any>("/api/admin/legal-documents", {
        method: "PUT",
        body: JSON.stringify(data),
    });

/* ── Notifications (uses sendNotification defined above) ── */

/* ── Area Management (Pricing, Tax, Vehicle Pricing) ─────────────────── */
export const getAreaFees = (areaId: string) =>
    request<any[]>(`/api/admin/areas/${areaId}/fees`);

export const createAreaFee = (areaId: string, data: any) =>
    request<any>(`/api/admin/areas/${areaId}/fees`, {
        method: "POST",
        body: JSON.stringify(data),
    });

export const updateAreaFee = (areaId: string, feeId: string, data: any) =>
    request<any>(`/api/admin/areas/${areaId}/fees/${feeId}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });

export const deleteAreaFee = (areaId: string, feeId: string) =>
    request<any>(`/api/admin/areas/${areaId}/fees/${feeId}`, { method: "DELETE" });

export const getAreaTax = (areaId: string) =>
    request<any>(`/api/admin/areas/${areaId}/tax`);

export const updateAreaTax = (areaId: string, data: any) =>
    request<any>(`/api/admin/areas/${areaId}/tax`, {
        method: "PUT",
        body: JSON.stringify(data),
    });

export const getVehiclePricing = (areaId: string) =>
    request<any>(`/api/admin/areas/${areaId}/vehicle-pricing`);

/* ── Driver Area Assignment ──────────────────── */
export const assignDriverArea = (driverId: string, serviceAreaId: string) =>
    request<any>(`/api/admin/drivers/${driverId}/area?service_area_id=${serviceAreaId}`, {
        method: "PUT",
    });

export const driverAction = (driverId: string, action: string, reason?: string) =>
    request<{ message: string; new_status: string; audit_log_id?: string }>(`/api/admin/drivers/${driverId}/action`, {
        method: "POST",
        body: JSON.stringify({ action, reason }),
    });

export const getDriverVehicleHistory = (driverId: string) =>
    request<{ history: Array<{ id: string; field: string; old_value: string | null; new_value: string | null; changed_by_role: string; created_at: string }> }>(
        `/api/admin/drivers/${driverId}/vehicle-history`,
    );

export const reviewDriverPhoto = (driverId: string, action: "approve" | "reject") =>
    request<{ message: string; profile_image_status: string }>(`/api/admin/drivers/${driverId}/photo-review`, {
        method: "POST",
        body: JSON.stringify({ action }),
    });

/** Upload a driver's profile photo on their behalf (stored approved). */
export const uploadDriverPhoto = (driverId: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<{ message: string; profile_image: string; profile_image_status: string }>(
        `/api/admin/drivers/${driverId}/photo`,
        { method: "POST", body: fd },
    );
};

export const overrideDriverStatus = (driverId: string, status: string, reason?: string) =>
    request<any>(`/api/admin/drivers/${driverId}/status-override`, {
        method: "PUT",
        body: JSON.stringify({ status, reason }),
    });

export const exportDrivers = () =>
    request<{ drivers: any[]; count: number }>("/api/admin/export/drivers");

/* Driver notes moved to lib/api/driver-documents.ts, re-exported above. */

export const getDriverActivity = (driverId: string) =>
    request<any[]>(`/api/admin/drivers/${driverId}/activity`);


/* ── Heat Map Data ─────────────────────────── */
export interface HeatMapData {
    pickup_points: [number, number, number][];
    dropoff_points: [number, number, number][];
    stats: {
        total_rides: number;
        corporate_rides: number;
        regular_rides: number;
    };
}

export const getHeatMapData = (params: {
    filter?: string;
    start_date?: string;
    end_date?: string;
    service_area_id?: string;
    group_by?: string;
}) => {
    const searchParams = new URLSearchParams();
    if (params.filter) searchParams.set('filter', params.filter);
    if (params.start_date) searchParams.set('start_date', params.start_date);
    if (params.end_date) searchParams.set('end_date', params.end_date);
    if (params.service_area_id) searchParams.set('service_area_id', params.service_area_id);
    if (params.group_by) searchParams.set('group_by', params.group_by);

    return request<HeatMapData>(`/api/admin/rides/heatmap-data?${searchParams.toString()}`);
};

/* ── Heat Map Settings ─────────────────────── */
export interface HeatMapSettings {
    heat_map_enabled: boolean;
    heat_map_default_range: string;
    heat_map_intensity: string;
    heat_map_radius: number;
    heat_map_blur: number;
    heat_map_gradient_start: string;
    heat_map_gradient_mid: string;
    heat_map_gradient_end: string;
    heat_map_show_pickups: boolean;
    heat_map_show_dropoffs: boolean;
    corporate_heat_map_enabled: boolean;
    regular_rider_heat_map_enabled: boolean;
}

export const getHeatMapSettings = () =>
    request<HeatMapSettings>("/api/admin/settings/heatmap");

export const updateHeatMapSettings = (data: Partial<HeatMapSettings>) =>
    request<any>("/api/admin/settings/heatmap", {
        method: "PUT",
        body: JSON.stringify(data),
    });

/* ── Staff Management ──────────────────────── */
export const getStaff = () =>
    request<any[]>("/api/admin/staff");

export const createStaff = (data: { email: string; password: string; first_name: string; last_name: string; role: string; modules?: string[] }) =>
    request<any>("/api/admin/staff", { method: "POST", body: JSON.stringify(data) });

export const updateStaff = (id: string, data: any) =>
    request<any>(`/api/admin/staff/${id}`, { method: "PUT", body: JSON.stringify(data) });

export const deleteStaff = (id: string) =>
    request<any>(`/api/admin/staff/${id}`, { method: "DELETE" });

// Lost-phone recovery: super_admin clears a staff member's MFA so they can
// re-enroll. Backend revokes the target's sessions and audit-logs the action.
export const resetStaffMfa = (id: string) =>
    request<{ success: boolean }>(`/api/admin/staff/${id}/mfa-reset`, { method: "POST" });

export const getStaffModules = () =>
    request<{ modules: string[]; role_presets: Record<string, string[]> }>("/api/admin/staff/modules/list");

/* ── Spinr Pass — Subscription Plans ──────── */
export const getSubscriptionPlans = () =>
    request<any[]>("/api/admin/subscription-plans");

export const createSubscriptionPlan = (data: any) =>
    request<any>("/api/admin/subscription-plans", { method: "POST", body: JSON.stringify(data) });

export const updateSubscriptionPlan = (id: string, data: any) =>
    request<any>(`/api/admin/subscription-plans/${id}`, { method: "PUT", body: JSON.stringify(data) });

export const deleteSubscriptionPlan = (id: string) =>
    request<any>(`/api/admin/subscription-plans/${id}`, { method: "DELETE" });

export const getDriverSubscriptions = (status?: string) =>
    request<any[]>(`/api/admin/driver-subscriptions${status ? `?status=${status}` : ''}`);

export const getAdminSubscriptionPayments = (opts: {
    limit?: number;
    offset?: number;
    driver_id?: string;
    plan_id?: string;
    billing_reason?: string;
    start_date?: string;
    end_date?: string;
} = {}) => {
    const sp = new URLSearchParams();
    if (opts.limit) sp.set("limit", String(opts.limit));
    if (opts.offset) sp.set("offset", String(opts.offset));
    if (opts.driver_id) sp.set("driver_id", opts.driver_id);
    if (opts.plan_id) sp.set("plan_id", opts.plan_id);
    if (opts.billing_reason) sp.set("billing_reason", opts.billing_reason);
    if (opts.start_date) sp.set("start_date", opts.start_date);
    if (opts.end_date) sp.set("end_date", opts.end_date);
    return request<any>(`/api/admin/subscription/payments?${sp.toString()}`);
};

/** Download the Spinr Pass invoice PDF for one subscription payment and trigger
 *  a browser save. Returns true on success. The request<T> helper assumes JSON,
 *  so use a raw authed fetch like getRideRouteMapDataUrl. */
export const downloadSubscriptionInvoice = async (paymentId: string): Promise<boolean> => {
    const token = useAuthStore.getState().token;
    try {
        const res = await fetch(`/api/admin/subscription/payments/${paymentId}/invoice.pdf`, {
            headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!res.ok) return false;
        const blob = await res.blob();
        const cd = res.headers.get("content-disposition") || "";
        const filename = cd.match(/filename="?([^"]+)"?/)?.[1] || `SpinrPass_Invoice_${paymentId}.pdf`;
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        return true;
    } catch (e) {
        if (process.env.NODE_ENV === "development") console.log("Invoice download failed:", e);
        return false;
    }
};

/** Email the Spinr Pass invoice to the driver's address on file (admin-triggered). */
export const resendAdminSubscriptionInvoice = (paymentId: string) =>
    request<{ success: boolean }>(`/api/admin/subscription/payments/${paymentId}/resend-invoice`, {
        method: "POST",
    });

export const updateSubscriptionTaxConfig = (
    areaId: string,
    config: { enabled: boolean; province: string; gst_rate: number; pst_rate: number; hst_rate: number },
) =>
    request<any>(`/api/admin/service-areas/${areaId}/subscription-tax`, {
        method: "PUT",
        body: JSON.stringify(config),
    });

/* ── Audit Logs ──────────────────────────── */
export const getAuditLogs = (opts: {
    limit?: number;
    offset?: number;
    action?: string;
    entity_type?: string;
    search?: string;
} = {}) => {
    const sp = new URLSearchParams();
    sp.set("limit", String(opts.limit ?? 50));
    sp.set("offset", String(opts.offset ?? 0));
    if (opts.action) sp.set("action", opts.action);
    if (opts.entity_type) sp.set("entity_type", opts.entity_type);
    if (opts.search) sp.set("search", opts.search);
    return request<any[]>(`/api/admin/audit-logs?${sp.toString()}`);
};

/* ── Quests / Bonus Challenges ──────────── */
export const getQuests = (isActive?: boolean) =>
    request<any[]>(`/api/v1/quests/admin/list${isActive !== undefined ? `?is_active=${isActive}` : ''}`);

export const createQuest = (data: any) =>
    request<any>("/api/v1/quests/admin/create", { method: "POST", body: JSON.stringify(data) });

export const updateQuest = (id: string, data: any) =>
    request<any>(`/api/v1/quests/admin/${id}`, { method: "PATCH", body: JSON.stringify(data) });

export const getQuestParticipants = (questId: string) =>
    request<any[]>(`/api/v1/quests/admin/${questId}/participants`);

/* ── Analytics ──────────────────────────── */
export const getAnalyticsOverview = (dateRange = "30d") =>
    request<any>(`/api/admin/analytics/overview?date_range=${dateRange}`);

/** Main-dashboard stat cards aggregated by time window + optional service area. */
export const getDashboardOverview = (opts: { range?: string; service_area_id?: string | null } = {}) => {
    const sp = new URLSearchParams();
    if (opts.range) sp.set("range", opts.range);
    if (opts.service_area_id) sp.set("service_area_id", opts.service_area_id);
    return request<any>(`/api/admin/analytics/dashboard?${sp.toString()}`);
};

export const getCancellationBreakdown = (dateRange = "30d", serviceAreaId?: string) =>
    request<any>(`/api/admin/analytics/cancellation-reasons?date_range=${dateRange}${serviceAreaId ? `&service_area_id=${serviceAreaId}` : ''}`);

export const getDriverAcceptanceRates = (dateRange = "30d", serviceAreaId?: string) =>
    request<any>(`/api/admin/analytics/driver-acceptance?date_range=${dateRange}${serviceAreaId ? `&service_area_id=${serviceAreaId}` : ''}`);

export const getDriverOfferStats = (dateRange = "30d", serviceAreaId?: string) =>
    request<any>(`/api/admin/analytics/driver-offer-stats?date_range=${dateRange}${serviceAreaId ? `&service_area_id=${serviceAreaId}` : ''}`);

export const getDriverOfferTrends = (dateRange = "30d", opts?: { driverId?: string; serviceAreaId?: string }) => {
    const sp = new URLSearchParams({ date_range: dateRange });
    if (opts?.driverId) sp.set("driver_id", opts.driverId);
    if (opts?.serviceAreaId) sp.set("service_area_id", opts.serviceAreaId);
    return request<{ date_range: string; driver_id: string | null; daily_chart: { date: string; offered: number; accepted: number; declined: number; ignored: number; preempted: number }[] }>(
        `/api/admin/analytics/driver-offer-trends?${sp.toString()}`,
    );
};

export const getDemandForecast = (hoursAhead = 24, areaId?: string) =>
    request<any>(`/api/admin/analytics/demand-forecast?hours_ahead=${hoursAhead}${areaId ? `&area_id=${areaId}` : ''}`);

export const getDemandForecastSummary = (areaId?: string) =>
    request<any>(`/api/admin/analytics/demand-forecast/summary${areaId ? `?area_id=${areaId}` : ''}`);

export const getSurgeHistory = (areaId: string, hours = 24) =>
    request<any>(`/api/admin/analytics/surge-history?area_id=${areaId}&hours=${hours}`);

/* ── Payouts ────────────────────────────── */
export const getPayouts = (status?: string) =>
    request<any[]>(`/api/admin/payouts${status ? `?status=${status}` : ''}`);

export const getPayoutById = (id: string) =>
    request<any>(`/api/admin/payouts/${id}`);

export const getPayoutStats = () =>
    request<any>("/api/admin/payouts/stats");

export interface PayoutsOverview {
    period: {
        key: EarningsPeriod;
        label: string;
        days: number;
        start: string;
        end: string;
        prev_start: string;
        prev_end: string;
    };
    metrics: {
        outstanding_payable: MetricWithDelta;
        total_paid_out: MetricWithDelta;
        pending_in_flight: MetricWithDelta;
        failed_amount: MetricWithDelta;
        success_rate_pct: MetricWithDelta;
        median_time_to_payout_hours: MetricWithDelta;
        avg_payout_amount: MetricWithDelta;
        payouts_count: MetricWithDelta;
    };
    daily_series: Array<{
        date: string;
        paid_out: number;
        pending: number;
        failed: number;
    }>;
    // Pass 2 — operational queues. None carry PoP because they're
    // "right now" lists rather than period totals.
    failure_reasons: Array<{ reason: string; count: number; amount: number }>;
    stuck_over_48h: { count: number; amount: number };
    blocked_drivers: { count: number; outstanding_balance: number };
    top_drivers: Array<{ driver_id: string; name: string; amount: number; payout_count: number }>;
    at_risk_drivers: Array<{ driver_id: string; name: string; failure_count: number; last_reason: string | null }>;
    // Pass 4 — compliance.
    t4a_snapshot: {
        tax_year: number;
        drivers_with_earnings: number;
        buckets: {
            under_500: number;
            from_500_to_10k: number;
            from_10k_to_30k: number;
            over_30k: number;
        };
        ytd_gross_earnings: number;
    };
    period_locks: Array<{
        period: string;
        closed_at: string;
        closed_by: string | null;
        actor_role: string | null;
    }>;
}

export interface ClosePayoutPeriodResponse {
    period: string;
    payout_count: number;
    total_amount: number;
    audit_log_id: string | null;
}
export const closePayoutPeriod = (year: number, month: number) =>
    request<ClosePayoutPeriodResponse>(`/api/admin/payouts/close-period`, {
        method: "POST",
        body: JSON.stringify({ year, month }),
    });

export const getPayoutsOverview = (params: { period: EarningsPeriod; service_area_id?: string }) => {
    const sp = new URLSearchParams({ period: params.period });
    if (params.service_area_id) sp.set("service_area_id", params.service_area_id);
    return request<PayoutsOverview>(`/api/admin/payouts/overview?${sp.toString()}`);
};

/* ── Disputes (resolve) ─────────────────── */
export const resolveDispute = (id: string, data: { resolution: string; refund_amount?: number; admin_note?: string }) =>
    request<any>(`/api/admin/disputes/${id}/resolve`, { method: "PUT", body: JSON.stringify(data) });

/* ── Live Ride Monitoring ───────────────── */
export const getActiveRides = () =>
    request<any>("/api/admin/rides/active");

export const getMonitoringDrivers = () =>
    request<any[]>("/api/admin/monitoring/drivers");

export const getMonitoringRides = () =>
    request<any[]>("/api/admin/monitoring/rides");

export const adminCancelRide = (rideId: string, reason?: string) =>
    request<{ success: boolean; ride_id: string; status: string }>(
        `/api/admin/rides/${rideId}/cancel`,
        {
            method: "POST",
            body: JSON.stringify({ reason: reason ?? "Cancelled by admin" }),
        },
    );

export const adminCompleteRide = (rideId: string) =>
    request<{ success: boolean; ride_id: string; status: string }>(
        `/api/admin/rides/${rideId}/complete`,
        { method: "POST" },
    );

export interface AdminPlaceBias {
    lat: number;
    lng: number;
    /** Soft-bias radius in metres. Backend clamps to [1000, 100000]. Defaults to 20km. */
    radiusMeters?: number;
}

export const adminPlacesAutocomplete = (
    input: string,
    sessionToken?: string,
    bias?: AdminPlaceBias | null,
) => {
    const sp = new URLSearchParams({ input });
    if (sessionToken) sp.set("session_token", sessionToken);
    if (bias && Number.isFinite(bias.lat) && Number.isFinite(bias.lng)) {
        // 4 decimals (~11m) keeps the URL short and avoids over-precise GPS
        // appearing in proxy access logs.
        sp.set("location", `${bias.lat.toFixed(4)},${bias.lng.toFixed(4)}`);
        sp.set("radius", String(Math.min(Math.max(bias.radiusMeters ?? 20000, 1000), 100000)));
    }
    return request<{ predictions: any[] }>(`/api/admin/places/autocomplete?${sp.toString()}`);
};

export const adminPlacesDetails = (placeId: string, sessionToken?: string) => {
    const sp = new URLSearchParams({ place_id: placeId });
    if (sessionToken) sp.set("session_token", sessionToken);
    return request<{ lat: number; lng: number; formatted_address: string }>(`/api/admin/places/details?${sp.toString()}`);
};

export interface AdminFareEstimateResponse {
    base_fare: number;
    distance_fare: number;
    time_fare: number;
    booking_fee: number;
    surge_multiplier: number;
    subtotal: number;
    area_fees: Array<{ name: string; amount: number }>;
    area_fees_total: number;
    tax_amount: number;
    tax_breakdown: Array<{ name: string; rate: number; amount: number }>;
    grand_total: number;
    service_area: string | null;
}

export const adminFareEstimate = (params: {
    pickup_lat: number;
    pickup_lng: number;
    dropoff_lat: number;
    dropoff_lng: number;
    distance_km: number;
    duration_minutes: number;
    vehicle_type_id: string;
}) => {
    const sp = new URLSearchParams();
    sp.set("pickup_lat", String(params.pickup_lat));
    sp.set("pickup_lng", String(params.pickup_lng));
    sp.set("dropoff_lat", String(params.dropoff_lat));
    sp.set("dropoff_lng", String(params.dropoff_lng));
    sp.set("distance_km", String(params.distance_km));
    sp.set("duration_minutes", String(params.duration_minutes));
    sp.set("vehicle_type_id", params.vehicle_type_id);
    return request<AdminFareEstimateResponse>(`/api/admin/rides/fare-estimate?${sp.toString()}`);
};

export const adminPromoPreview = (data: {
    rider_id: string;
    code: string;
    // String preserves Decimal precision over the wire.
    ride_fare: string;
}) =>
    request<{
        valid: boolean;
        code: string;
        discount_type: string;
        discount_amount: number;
        promo_id: string;
        description: string;
    }>("/api/admin/promo/preview", {
        method: "POST",
        body: JSON.stringify(data),
    });

export interface AdminVehicleType {
    id: string;
    name: string;
    icon?: string;
    capacity?: number;
    is_active: boolean;
    marker_variant?: string;
}

export const adminListVehicleTypes = () =>
    request<AdminVehicleType[]>("/api/admin/vehicle-types");

export const adminCreateRide = (data: {
    rider_id: string;
    driver_id?: string;
    pickup_address: string;
    pickup_lat: number;
    pickup_lng: number;
    dropoff_address: string;
    dropoff_lat: number;
    dropoff_lng: number;
    // Pass as string to preserve Decimal precision on the backend
    // (Pydantic Decimal accepts string/number; string avoids float drift).
    total_fare?: string | number;
    vehicle_type_id?: string;
    subtotal_fare?: string | number;
    discount_amount?: string | number;
    promo_code?: string;
    fare_overridden_by_admin?: boolean;
}) =>
    request<{ success: boolean; ride_id: string; status: string }>(
        "/api/admin/rides/create",
        {
            method: "POST",
            body: JSON.stringify(data),
        },
    );

// ── Monitoring: Redis + Infrastructure ────────────────────────────────

export type RedisStats = {
    backend: "redis" | "in_process";
    connected: boolean;
    used_memory_bytes?: number | null;
    used_memory_human?: string;
    maxmemory_bytes?: number | null;
    maxmemory_human?: string;
    maxmemory_policy?: string;
    used_memory_percent?: number | null;
    used_memory_peak_bytes?: number;
    total_keys?: number;
    keyspace_hits_total?: number | null;
    keyspace_misses_total?: number | null;
    hit_rate_percent?: number | null;
    evicted_keys_total?: number;
    expired_keys_total?: number;
    connected_clients?: number;
    uptime_seconds?: number | null;
    total_commands_processed?: number | null;
    error?: string;
};

export type RedisPrefixCount = {
    prefix: string;
    count: number;
    description: string;
    flushable: boolean;
};

export type RedisHealthResponse = {
    stats: RedisStats;
    prefix_counts: RedisPrefixCount[];
    flushable_prefixes: string[];
};

export type InfrastructureStats = {
    replica: {
        hostname: string;
        pid: number;
        uptime_seconds: number;
        python_version: string | null;
    };
    process: {
        rss_bytes: number | null;
        rss_human: string | null;
        cpu_user_seconds: number | null;
        cpu_system_seconds: number | null;
    };
    thread_pool: { max_workers: number | null; note: string };
    db_circuit_breaker: {
        state: "closed" | "open" | "half_open";
        recent_failures: number;
        opened_at_monotonic: number | null;
    };
    redis: {
        connected: boolean;
        used_memory_bytes: number | null;
        used_memory_human: string | null;
        maxmemory_human: string | null;
        used_memory_percent: number | null;
        total_keys: number | null;
        evicted_keys_total: number | null;
    };
    metrics: Record<string, number>;
};

export const getRedisHealth = () =>
    request<RedisHealthResponse>("/api/admin/monitoring/redis");

export type WebsocketHealth = {
    fanout: {
        active: boolean;
        channel: string;
        backend_scheme: string;
        configured: boolean;
        last_error: string | null;
    };
    connections: { total: number; admins: number; drivers: number; riders: number };
    replica_hostname: string;
    worker_pid: number;
    workers_hint: number | null;
    per_worker: boolean;
};

export const getWebsocketHealth = () =>
    request<WebsocketHealth>("/api/admin/monitoring/websockets");

export type RedisConnectivityProbe = {
    label: string;
    configured: boolean;
    status: "ok" | "degraded" | "error" | "unset";
    endpoint?: string;
    scheme?: string;
    host?: string;
    port?: number | null;
    tls?: boolean;
    ping_ms?: number;
    pubsub?: { ok: boolean; error?: string };
    error?: string;
    warning?: string;
    same_as?: string;
};

// Separate from getRedisHealth on purpose: this opens Redis clients and runs a
// pub/sub round-trip per URL, so it's called only on load + manual refresh,
// never on the 10s poll loop.
export const getRedisConnectivity = () =>
    request<{ connectivity: RedisConnectivityProbe[] }>(
        "/api/admin/monitoring/redis/connectivity",
    );

export const getInfrastructureStats = () =>
    request<InfrastructureStats>("/api/admin/monitoring/infrastructure");

export const flushRedisPrefix = (prefix: string) =>
    request<{ prefix: string; deleted_keys: number; admin_id: string }>(
        "/api/admin/monitoring/redis/flush-prefix",
        {
            method: "POST",
            body: JSON.stringify({ prefix, confirm: "FLUSH" }),
        },
    );

/* ── Document Requirements (A-P4-1) ─────── */
export const getDocumentRequirements = () =>
    request<any[]>("/api/admin/documents/requirements");

export const createDocumentRequirement = (data: {
    name: string;
    description?: string;
    document_type?: string;
    is_required?: boolean;
    applicable_to?: string;
}) => request<any>("/api/admin/documents/requirements", { method: "POST", body: JSON.stringify(data) });

export const updateDocumentRequirement = (id: string, data: Partial<{
    name: string;
    description: string;
    document_type: string;
    is_required: boolean;
    applicable_to: string;
}>) => request<any>(`/api/admin/documents/requirements/${id}`, { method: "PUT", body: JSON.stringify(data) });

export const deleteDocumentRequirement = (id: string) =>
    request<any>(`/api/admin/documents/requirements/${id}`, { method: "DELETE" });

/* ── Pending Documents paginated (A-P4-4) ── */
export const getPendingDocuments = (params?: { limit?: number; cursor?: string; status?: string }) => {
    const sp = new URLSearchParams();
    if (params?.limit) sp.set("limit", String(params.limit));
    if (params?.cursor) sp.set("cursor", params.cursor);
    if (params?.status) sp.set("status", params.status);
    const qs = sp.toString();
    return request<{ items: any[]; next_cursor: string | null }>(`/api/admin/documents/pending${qs ? `?${qs}` : ""}`);
};

/* ── Payout retry (A-P4-2) ──────────────── */
export const retryPayout = (id: string) =>
    request<any>(`/api/admin/payouts/${id}/retry`, { method: "POST" });

export interface BulkRetryPayoutsRequest {
    payout_ids?: string[];
    since?: string;
    service_area_id?: string;
    max_to_retry?: number;
}
export interface BulkRetryPayoutsResponse {
    retried: number;
    skipped: number;
    failed_to_initiate: number;
    details: Array<{ payout_id: string; status: string; reason?: string }>;
}
export const bulkRetryPayouts = (body: BulkRetryPayoutsRequest) =>
    request<BulkRetryPayoutsResponse>(`/api/admin/payouts/bulk-retry`, {
        method: "POST",
        body: JSON.stringify(body),
    });

/* ── Zoho Desk support tickets ──────────────────────────────────────────
 * Native admin UI proxied to Zoho Desk via the backend. Secrets are
 * write-only; getZohoConfig returns presence flags only. Gated by the
 * `support_tickets` RBAC module (config endpoints require any admin).
 */
export interface ZohoConfigStatus {
    enabled: boolean;
    auto_sync_enabled?: boolean;
    data_center: string;
    org_id: string;
    default_department_id: string;
    default_from_email: string;
    has_client_id: boolean;
    has_client_secret: boolean;
    has_refresh_token: boolean;
    connected: boolean;
    last_synced_at?: string | null;
    last_sync_count?: number | null;
    updated_at?: string | null;
}
export interface ZohoConfigUpdate {
    enabled?: boolean;
    auto_sync_enabled?: boolean;
    data_center?: string;
    org_id?: string;
    default_department_id?: string;
    default_from_email?: string;
    client_id?: string;
    client_secret?: string;
    refresh_token?: string;
}
export interface ZohoTicketsResponse {
    data: any[];
}
export interface ZohoDashboard {
    total: number | null;
    total_available: boolean;
    open: number;
    by_status: Record<string, number>;
    recent: any[];
    sample_size: number;
    approximate: boolean;
}
export interface ZohoTrends {
    sample_size: number;
    approximate: boolean;
    volume: Array<{ date: string; opened: number; closed: number }>;
    by_status: Record<string, number>;
    by_priority: Record<string, number>;
    by_channel: Record<string, number>;
    by_category: Record<string, number>;
    by_classification: Record<string, number>;
    by_tag: Record<string, number>;
    top_contacts: Array<{ name: string; count: number }>;
    stats: {
        opened: number;
        closed: number;
        open_now: number;
        avg_resolution_hours: number | null;
        median_resolution_hours: number | null;
        resolved_sample: number;
    };
}

export const getZohoConfig = () =>
    request<ZohoConfigStatus>("/api/admin/support-tickets/config");
export const updateZohoConfig = (body: ZohoConfigUpdate) =>
    request<ZohoConfigStatus>("/api/admin/support-tickets/config", {
        method: "PUT",
        body: JSON.stringify(body),
    });
export const testZohoConnection = () =>
    request<{ ok: boolean; departments: any[] }>("/api/admin/support-tickets/config/test", {
        method: "POST",
    });
export const syncDeskTickets = () =>
    request<{ upserted?: number; skipped?: string }>("/api/admin/support-tickets/sync", {
        method: "POST",
    });

export const getDeskDashboard = (departmentId?: string) => {
    const qs = departmentId ? `?department_id=${encodeURIComponent(departmentId)}` : "";
    return request<ZohoDashboard>(`/api/admin/support-tickets/dashboard${qs}`);
};
export const getDeskTrends = (opts?: { days?: number; departmentId?: string; assigneeId?: string }) => {
    const sp = new URLSearchParams();
    if (opts?.days) sp.set("days", String(opts.days));
    if (opts?.departmentId) sp.set("department_id", opts.departmentId);
    if (opts?.assigneeId) sp.set("assignee_id", opts.assigneeId);
    const qs = sp.toString();
    return request<ZohoTrends>(`/api/admin/support-tickets/trends${qs ? `?${qs}` : ""}`);
};

export const getDeskTickets = (opts?: {
    from?: number;
    limit?: number;
    status?: string;
    departmentId?: string;
    assigneeId?: string;
    priority?: string;
    channel?: string;
    sortBy?: string;
}) => {
    const sp = new URLSearchParams();
    if (opts?.from) sp.set("from", String(opts.from));
    if (opts?.limit) sp.set("limit", String(opts.limit));
    if (opts?.status) sp.set("status", opts.status);
    if (opts?.departmentId) sp.set("department_id", opts.departmentId);
    if (opts?.assigneeId) sp.set("assignee_id", opts.assigneeId);
    if (opts?.priority) sp.set("priority", opts.priority);
    if (opts?.channel) sp.set("channel", opts.channel);
    if (opts?.sortBy) sp.set("sort_by", opts.sortBy);
    const qs = sp.toString();
    return request<ZohoTicketsResponse>(`/api/admin/support-tickets/tickets${qs ? `?${qs}` : ""}`);
};
export interface CreateDeskTicket {
    subject: string;
    description?: string;
    email?: string;
    first_name?: string;
    last_name?: string;
    phone?: string;
    priority?: string;
    channel?: string;
    category?: string;
    department_id?: string;
}
export const createDeskTicket = (body: CreateDeskTicket) =>
    request<any>(`/api/admin/support-tickets/tickets`, {
        method: "POST",
        body: JSON.stringify(body),
    });
export const searchDeskTickets = (opts: {
    q: string;
    from?: number;
    limit?: number;
    departmentId?: string;
    status?: string;
    priority?: string;
    assigneeId?: string;
}) => {
    const sp = new URLSearchParams();
    sp.set("q", opts.q);
    if (opts.from) sp.set("from", String(opts.from));
    if (opts.limit) sp.set("limit", String(opts.limit));
    if (opts.departmentId) sp.set("department_id", opts.departmentId);
    if (opts.status) sp.set("status", opts.status);
    if (opts.priority) sp.set("priority", opts.priority);
    if (opts.assigneeId) sp.set("assignee_id", opts.assigneeId);
    return request<ZohoTicketsResponse>(`/api/admin/support-tickets/search?${sp.toString()}`);
};
export const getDeskTicket = (id: string) =>
    request<any>(`/api/admin/support-tickets/tickets/${id}`);
export const getDeskTicketThreads = (id: string) =>
    request<ZohoTicketsResponse>(`/api/admin/support-tickets/tickets/${id}/threads`);
export const getDeskThread = (ticketId: string, threadId: string) =>
    request<any>(`/api/admin/support-tickets/tickets/${ticketId}/threads/${threadId}`);
export const replyDeskTicket = (id: string, body: { content: string; to?: string; channel?: string }) =>
    request<any>(`/api/admin/support-tickets/tickets/${id}/reply`, {
        method: "POST",
        body: JSON.stringify(body),
    });
export const commentDeskTicket = (id: string, body: { content: string; is_public?: boolean }) =>
    request<any>(`/api/admin/support-tickets/tickets/${id}/comment`, {
        method: "POST",
        body: JSON.stringify(body),
    });
export const updateDeskTicket = (
    id: string,
    body: {
        status?: string;
        priority?: string;
        assigneeId?: string;
        departmentId?: string;
        category?: string;
        subCategory?: string;
        classification?: string;
    },
) =>
    request<any>(`/api/admin/support-tickets/tickets/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
    });
export const updateDeskTicketTags = (id: string, body: { add?: string[]; remove?: string[] }) =>
    request<{ ok: boolean }>(`/api/admin/support-tickets/tickets/${id}/tags`, {
        method: "POST",
        body: JSON.stringify(body),
    });
export const getDeskAgents = () =>
    request<ZohoTicketsResponse>("/api/admin/support-tickets/agents");
export const getDeskDepartments = () =>
    request<ZohoTicketsResponse>("/api/admin/support-tickets/departments");

/* ── Service area (Spinr-local; not synced to Zoho) ─────────────────────── */
export interface DeskServiceArea {
    id: string;
    name?: string;
    city?: string;
    province?: string;
}
export interface DeskTicketServiceArea {
    service_area_id?: string | null;
    service_area_name?: string | null;
    service_area_source?: "auto" | "manual" | null;
    service_area_assigned_at?: string | null;
    service_area_assigned_by?: string | null;
    needs_assignment?: boolean;
    suggested?: { service_area_id: string; service_area_name?: string; matched_user_id?: string } | null;
}
export const getDeskServiceAreas = () =>
    request<{ data: DeskServiceArea[] }>("/api/admin/support-tickets/service-areas");
export const getDeskTicketServiceArea = (id: string) =>
    request<DeskTicketServiceArea>(`/api/admin/support-tickets/tickets/${id}/service-area`);
export const setDeskTicketServiceArea = (id: string, service_area_id: string | null) =>
    request<DeskTicketServiceArea>(`/api/admin/support-tickets/tickets/${id}/service-area`, {
        method: "PUT",
        body: JSON.stringify({ service_area_id }),
    });

/* ── AI reply suggestion (draft only; agent reviews + sends) ────────────── */
export const aiSuggestDeskReply = (id: string, instruction?: string) =>
    request<{ reply: string; provider?: string; model?: string }>(
        `/api/admin/support-tickets/tickets/${id}/ai-suggest-reply`,
        {
            method: "POST",
            // Optional agent guidance steering the draft. Omit the body when
            // there's nothing to add so the endpoint behaves exactly as before.
            ...(instruction && instruction.trim()
                ? { body: JSON.stringify({ instruction: instruction.trim() }) }
                : {}),
        },
    );

/* ── Data Transfer module (backend/routes/admin/data_transfer_*.py) ─────── */
export interface DataTransferEntityRow {
    id: string;
    user_id?: string;
    full_name?: string;
    email?: string;
    phone?: string;
    created_at?: string;
    role?: string;
    vehicle_plate?: string;
    status?: string;
}
export interface DataTransferSearchResult {
    rows: DataTransferEntityRow[];
    total_count: number;
    page: number;
    page_size: number;
}
export interface DataTransferSearchParams {
    q?: string;
    entityType?: "driver" | "rider";
    dateFrom?: string;
    dateTo?: string;
    // Exact match on drivers.status/users.status -- free-text, not a shared
    // enum, since the two tables' status vocabularies aren't identical (see
    // backend/routes/admin/data_transfer_search.py's status param comment).
    status?: string;
    // Only meaningful when entityType is "driver" (or omitted/"all", where it
    // still only narrows the driver rows) -- `users` has no service_area_id
    // column, see data_transfer_search.py's driver-only branch.
    serviceAreaId?: string;
    page?: number;
    pageSize?: number;
}
export const searchDataTransferEntities = (params: DataTransferSearchParams) => {
    const qs = new URLSearchParams();
    if (params.q) qs.set("q", params.q);
    if (params.entityType) qs.set("entity_type", params.entityType);
    if (params.dateFrom) qs.set("date_from", params.dateFrom);
    if (params.dateTo) qs.set("date_to", params.dateTo);
    if (params.status) qs.set("status", params.status);
    if (params.serviceAreaId) qs.set("service_area_id", params.serviceAreaId);
    qs.set("page", String(params.page ?? 1));
    qs.set("page_size", String(params.pageSize ?? 50));
    return request<DataTransferSearchResult>(`/api/admin/data-transfer/search?${qs.toString()}`);
};

export type DataTransferExportFormat = "zip" | "csv" | "json" | "excel";
export interface DataTransferExportEntityRef {
    entity_type: "driver" | "rider";
    entity_id: string;
}
// The export route is backgrounded (see backend/routes/admin/data_transfer_export.py) --
// it returns only a job_id immediately; the caller polls getDataTransferJob
// until status leaves "pending", then fetches the download link separately.
export interface DataTransferExportQueuedResult {
    job_id: string;
    status: "pending";
    requested_count: number;
}
/** ACTION_ITEMS.md B10 dual-approval gate — returned instead of
 * DataTransferExportQueuedResult when settings.dual_approval_exports_enabled
 * is on and this export needs a second admin's sign-off before the job
 * even starts. Check `"approval_required" in result` to distinguish. */
export interface ExportApprovalRequiredResult {
    approval_required: true;
    request_id: string;
    status: "pending" | "approved" | "denied" | "expired" | "consumed";
    row_count: number;
}
export interface DataTransferExportScopeOptions {
    docTypes?: string[];
    // PIA recommendation R-B (ACTION_ITEMS.md B11) — default true (current
    // full-fidelity behavior) on both; the backend's ExportRequest defaults
    // match, so omitting these entirely is still safe/backward-compatible.
    includeRideGps?: boolean;
    includeDocumentBytes?: boolean;
}
export const exportDataTransferEntities = (
    entities: DataTransferExportEntityRef[],
    format: DataTransferExportFormat,
    reason: string,
    options?: DataTransferExportScopeOptions,
) =>
    request<DataTransferExportQueuedResult | ExportApprovalRequiredResult>("/api/admin/data-transfer/export", {
        method: "POST",
        body: JSON.stringify({
            entities,
            format,
            doc_types: options?.docTypes ?? null,
            reason,
            include_ride_gps: options?.includeRideGps ?? true,
            include_document_bytes: options?.includeDocumentBytes ?? true,
        }),
    });

export interface DataTransferImportReportItem {
    entity_id: string;
    field: string;
    message: string;
}
export interface DataTransferImportReport {
    can_commit: boolean;
    counts: { entities: number; new: number; existing_match: number; conflict: number };
    warnings: DataTransferImportReportItem[];
    errors: DataTransferImportReportItem[];
}
export interface DataTransferImportCommitResult extends DataTransferImportReport {
    committed: boolean;
    created_users?: number;
    created_drivers?: number;
    updated_users?: number;
    updated_drivers?: number;
    documents_replayed?: number;
    insurance_periods_replayed?: number;
}
export const adminValidateDataTransferImport = (file: File) => {
    const fd = new FormData();
    fd.append("bundle_zip", file);
    return request<DataTransferImportReport>("/api/admin/data-transfer/import/validate", {
        method: "POST",
        body: fd,
    });
};
export const adminCommitDataTransferImport = (file: File, batch?: string, updateExisting?: boolean) => {
    const fd = new FormData();
    fd.append("bundle_zip", file);
    if (batch) fd.append("batch", batch);
    if (updateExisting) fd.append("update_existing", "true");
    return request<DataTransferImportCommitResult>("/api/admin/data-transfer/import/commit", {
        method: "POST",
        body: fd,
    });
};

export type SgiFormType = "driver_details" | "vehicle_details";
// PDF binary response — can't use the generic request<T>() helper (it always
// calls res.json()). Mirrors fetchKybDocumentBlob's manual fetch + auth
// header pattern, adding the CSRF header this call needs since it's a POST.
export async function generateSgiForm(
    formType: SgiFormType,
    driverIds: string[],
    action: "add" | "remove" | "change" = "add",
): Promise<Blob> {
    const store = useAuthStore.getState();
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (store.token) headers["Authorization"] = `Bearer ${store.token}`;
    if (store.csrfToken) headers["X-CSRF-Token"] = store.csrfToken;
    const res = await fetch("/api/admin/data-transfer/sgi-forms/generate", {
        method: "POST",
        headers,
        body: JSON.stringify({ form_type: formType, driver_ids: driverIds, action }),
    });
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Could not generate form (${res.status})`);
    }
    return res.blob();
}

// ─── Compliance & Tax Reporting ────────────────────────────────────────

export type ComplianceReportFormat = "pdf" | "csv" | "xlsx" | "docx";

const COMPLIANCE_FILE_EXTENSIONS: Record<ComplianceReportFormat, string> = {
    pdf: "pdf",
    csv: "csv",
    xlsx: "xlsx",
    docx: "docx",
};

/** Shared GET-and-download for the Compliance & Tax Reporting endpoints —
 *  both return a branded PDF/CSV/Excel/Word file, not JSON, so they use a
 *  raw authed fetch like generateSgiForm rather than request<T>. */
async function downloadComplianceReport(path: string, fallbackFilename: string): Promise<Blob> {
    const store = useAuthStore.getState();
    const headers: Record<string, string> = {};
    if (store.token) headers["Authorization"] = `Bearer ${store.token}`;
    const res = await fetch(path, { headers });
    // ACTION_ITEMS.md B10 dual-approval gate: a 202 is in fetch's `ok`
    // range, so without this check the JSON {"approval_required": true,
    // ...} body would silently be treated as file bytes and downloaded as
    // a corrupt "report". Check the content-type, not just status, since a
    // 202 is otherwise indistinguishable from a real (2xx) file response.
    if (res.status === 202 && (res.headers.get("content-type") || "").includes("application/json")) {
        throw new Error(
            "This report needs a different admin's approval before it can be generated — it's been added to the Export Approvals queue.",
        );
    }
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Could not generate report (${res.status})`);
    }
    return res.blob();
}

export async function downloadGstPstRemittance(
    dateRange: string,
    format: ComplianceReportFormat,
): Promise<{ blob: Blob; filename: string }> {
    const sp = new URLSearchParams({ date_range: dateRange, format });
    const blob = await downloadComplianceReport(
        `/api/admin/compliance/gst-pst-remittance?${sp.toString()}`,
        "gst_pst_remittance",
    );
    return { blob, filename: `gst_pst_remittance.${COMPLIANCE_FILE_EXTENSIONS[format]}` };
}

export async function downloadInsurancePeriodAudit(
    dateRange: string,
    format: ComplianceReportFormat,
    driverId?: string,
): Promise<{ blob: Blob; filename: string }> {
    const sp = new URLSearchParams({ date_range: dateRange, format });
    if (driverId) sp.set("driver_id", driverId);
    const blob = await downloadComplianceReport(
        `/api/admin/compliance/insurance-period-audit?${sp.toString()}`,
        "insurance_period_audit",
    );
    return { blob, filename: `insurance_period_audit.${COMPLIANCE_FILE_EXTENSIONS[format]}` };
}

export async function downloadKnightArcherDriverOnboarding(
    format: ComplianceReportFormat,
    status?: string,
): Promise<{ blob: Blob; filename: string }> {
    const sp = new URLSearchParams({ format });
    if (status) sp.set("status", status);
    const blob = await downloadComplianceReport(
        `/api/admin/compliance/knight-archer-driver-onboarding?${sp.toString()}`,
        "knight_archer_driver_onboarding",
    );
    return { blob, filename: `knight_archer_driver_onboarding.${COMPLIANCE_FILE_EXTENSIONS[format]}` };
}

/** Email a compliance report to a @spinr.ca address instead of downloading
 * it — the backend hard-validates the domain, this just calls the same GET
 * endpoint with `email_to` set, which returns a small JSON confirmation
 * instead of a file. */
async function emailComplianceReport(path: string, emailTo: string): Promise<{ emailed_to: string }> {
    const store = useAuthStore.getState();
    const headers: Record<string, string> = {};
    if (store.token) headers["Authorization"] = `Bearer ${store.token}`;
    const sep = path.includes("?") ? "&" : "?";
    const res = await fetch(`${path}${sep}email_to=${encodeURIComponent(emailTo)}`, { headers });
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Could not email report (${res.status})`);
    }
    const body = await res.json();
    // ACTION_ITEMS.md B10: a 202 approval-required body is also `res.ok`
    // and JSON-shaped like a real {"emailed_to": ...} success response --
    // without this check, an admin would be told the report was emailed
    // when nothing was sent.
    if (body?.approval_required) {
        throw new Error(
            "This report needs a different admin's approval before it can be generated or emailed — it's been added to the Export Approvals queue.",
        );
    }
    return body;
}

export const emailGstPstRemittance = (dateRange: string, format: ComplianceReportFormat, emailTo: string) =>
    emailComplianceReport(
        `/api/admin/compliance/gst-pst-remittance?${new URLSearchParams({ date_range: dateRange, format }).toString()}`,
        emailTo,
    );
export const emailInsurancePeriodAudit = (
    dateRange: string,
    format: ComplianceReportFormat,
    emailTo: string,
    driverId?: string,
) => {
    const sp = new URLSearchParams({ date_range: dateRange, format });
    if (driverId) sp.set("driver_id", driverId);
    return emailComplianceReport(`/api/admin/compliance/insurance-period-audit?${sp.toString()}`, emailTo);
};
export const emailKnightArcherDriverOnboarding = (
    format: ComplianceReportFormat,
    emailTo: string,
    status?: string,
) => {
    const sp = new URLSearchParams({ format });
    if (status) sp.set("status", status);
    return emailComplianceReport(`/api/admin/compliance/knight-archer-driver-onboarding?${sp.toString()}`, emailTo);
};

export interface DataTransferJob {
    id: string;
    requested_by_admin_id?: string;
    entity_type: string;
    entity_ids: string[];
    entity_count: number;
    doc_type_filter?: string[] | null;
    format: string;
    reason?: string | null;
    status: "pending" | "completed" | "failed";
    error_message?: string | null;
    created_at: string;
    completed_at?: string | null;
    expires_at?: string | null;
}
export const listDataTransferJobs = (limit = 50) =>
    request<{ jobs: DataTransferJob[] }>(`/api/admin/data-transfer/jobs?limit=${limit}`);
export const getDataTransferJob = (jobId: string) =>
    request<DataTransferJob>(`/api/admin/data-transfer/jobs/${jobId}`);
export const regenerateDataTransferJobDownload = (jobId: string) =>
    request<{ download_url: string }>(`/api/admin/data-transfer/jobs/${jobId}/download`);
