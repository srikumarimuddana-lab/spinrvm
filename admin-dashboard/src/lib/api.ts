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
export {
    getCorporateAccounts,
    listCorporateAccounts,
    reviewKyb,
    getCorporateAccount,
    changeCompanyStatus,
    fetchKybDocumentBlob,
    createCorporateAccount,
    updateCorporateAccount,
    deleteCorporateAccount,
    getCorporateWallet,
    updateWalletConfig,
    walletTopupIntent,
    walletAdjust,
    listCompanyMembers,
    inviteCompanyMember,
    removeCompanyMember,
    getMemberAllowance,
    putMemberAllowance,
    listCompanyAllowanceRequests,
    decideAllowanceRequest,
    updateCompanyMember,
    getCompanyPolicy,
    putCompanyPolicy,
    patchCompanyPolicy,
    listAllowedDomains,
    addAllowedDomain,
    removeAllowedDomain,
    getCompanyBillingSummary,
    getCompanyBillingStatement,
    getCompanyBillingTransactions,
} from "./api/corporate";
export type {
    CompanyStatus,
    SizeTier,
    CorporateAccount,
    WalletTxn,
    CorporateWallet,
    WalletConfigPatch,
    CorporateMemberRole,
    CorporateMemberStatus,
    AllowanceTypeValue,
    CorporateMember,
    CorporateAllowance,
    AllowanceRequestRow,
    PaymentSourcePolicy,
    TimeWindowPolicy,
    CorporatePolicy,
    AllowedDomainRow,
    BillingMemberBreakdown,
    BillingSummary,
    BillingLineItem,
    BillingStatement,
    BillingTransaction,
    BillingTransactionsPage,
} from "./api/corporate";
export {
    getEarnings,
    getEarningsRides,
    getEarningsOverview,
    getSubscriptionStats,
} from "./api/earnings";
export type {
    EarningsRide,
    EarningsRidesResponse,
    EarningsPeriod,
    MetricWithDelta,
    EarningsOverview,
} from "./api/earnings";
export {
    loginAdmin,
    loginAdminSession,
    mfaChallenge,
    mfaStatus,
    mfaEnroll,
    mfaConfirm,
    mfaDisable,
    sendOtp,
    logoutAllAdmin,
} from "./api/auth";
export type {
    AuthResponse,
    AdminLoginResponse,
    AdminMfaRequired,
    AdminMfaEnrollmentRequired,
    AdminLoginResult,
    MfaConfirmResponse,
} from "./api/auth";
export { getStats } from "./api/dashboard";
export {
    getEmailDeliverability,
    getSettings,
    updateSettings,
    getAiCatalog,
    adminAiChat,
    getAdminAiConversations,
    getAdminAiMessages,
} from "./api/settings-ai";
export type {
    AiCatalogModel,
    AiCatalogProvider,
    AdminAiChatResponse,
} from "./api/settings-ai";
export {
    getServiceAreas,
    getVenues,
    createVenue,
    updateVenue,
    deleteVenue,
    createServiceArea,
    updateServiceArea,
    deleteServiceArea,
    getIncentives,
    createIncentive,
    updateIncentive,
    toggleIncentive,
    deleteIncentive,
    getSurgeStatus,
    resetSurgeToAuto,
    getVehicleTypes,
    createVehicleType,
    updateVehicleType,
    deleteVehicleType,
    adminUploadVehicleIllustration,
    adminUploadVehicleMarker,
    adminUploadRideOfferSound,
    getFareConfigs,
    createFareConfig,
    updateFareConfig,
    deleteFareConfig,
    updateSurge,
    getPendingExportApprovals,
    approveExportRequest,
    denyExportRequest,
} from "./api/pricing";
export type {
    VenuePickupPoint,
    Venue,
    VenueUpsert,
    ExportApprovalRequest,
} from "./api/pricing";
export {
    adminValidateDriverImport,
    adminCommitDriverImport,
    adminValidateBookingImport,
    adminCommitBookingImport,
    adminValidateStripeImport,
    adminCommitStripeImport,
    adminStripeImportStatus,
    adminUpdateDriverStripeAccount,
    adminValidateRiderImport,
    adminCommitRiderImport,
} from "./api/imports";
export type {
    DriverImportReportItem,
    DriverImportReport,
    DriverImportCommitResult,
    DriverImportOptions,
    BookingImportReportItem,
    BookingImportCounts,
    BookingImportReport,
    BookingImportCommitResult,
    BookingImportFiles,
    BookingImportOptions,
    StripeImportKind,
    StripeImportReportItem,
    StripeImportNeedsUpdateItem,
    StripeImportReport,
    StripeImportCommitResult,
    StripeImportStatus,
    StripeDriverAccountUpdateResult,
    RiderImportReportItem,
    RiderImportDuplicate,
    RiderImportReport,
    RiderImportCommitResult,
} from "./api/imports";
export {
    getCloudMessages,
    sendCloudMessage,
    getCloudMessageStats,
    deleteCloudMessage,
    getCloudMessageAudiencePreview,
    getMarketingSuppressions,
    addMarketingSuppression,
    deleteMarketingSuppression,
    getPromoUsage,
    getPromoStats,
} from "./api/marketing";
export {
    getUsers,
    getUsersPaginated,
    getUserDetails,
    updateUserStatus,
    updateUserFlags,
    exportUsers,
    logPiiReveal,
    getUserWallet,
    creditUserWallet,
    debitUserWallet,
} from "./api/users-wallet";
export {
    getPromotions,
    createPromotion,
    updatePromotion,
    deletePromotion,
    getDisputes,
    getDisputeStats,
    getDisputeDetails,
    createDispute,
    updateDispute,
    getSafetyIncidents,
    getSafetyIncident,
    updateSafetyIncident,
    getTickets,
    getTicketDetails,
    createTicket,
    updateTicket,
    replyToTicket,
    closeTicket,
    deleteTicket,
} from "./api/safety-disputes";
export type {
    SafetyStatus,
    SafetySeverity,
    SafetyRole,
    SafetyIncident,
    SafetyIncidentListResponse,
    SafetyIncidentDetail,
} from "./api/safety-disputes";
export {
    getFaqs,
    createFaq,
    updateFaq,
    deleteFaq,
    getLegalDocuments,
    upsertLegalDocument,
    getAreaFees,
    createAreaFee,
    updateAreaFee,
    deleteAreaFee,
    getAreaTax,
    updateAreaTax,
    getVehiclePricing,
    assignDriverArea,
    driverAction,
    getDriverVehicleHistory,
    reviewDriverPhoto,
    uploadDriverPhoto,
    overrideDriverStatus,
    exportDrivers,
} from "./api/content-area";
export { getHeatMapData, getHeatMapSettings, updateHeatMapSettings } from "./api/heatmap";
export type { HeatMapData, HeatMapSettings } from "./api/heatmap";
export {
    getStaff,
    createStaff,
    updateStaff,
    deleteStaff,
    resetStaffMfa,
    getStaffModules,
    getSubscriptionPlans,
    createSubscriptionPlan,
    updateSubscriptionPlan,
    deleteSubscriptionPlan,
    getDriverSubscriptions,
    getAdminSubscriptionPayments,
    downloadSubscriptionInvoice,
    resendAdminSubscriptionInvoice,
    updateSubscriptionTaxConfig,
    getAuditLogs,
    getQuests,
    createQuest,
    updateQuest,
    getQuestParticipants,
} from "./api/staff-subscriptions";
import { request } from "./api/client";
import { useAuthStore } from "@/store/authStore";
import type { EarningsPeriod, MetricWithDelta } from "./api/earnings";

/* ── Auth ── moved to lib/api/auth.ts, re-exported above. */
/* ── Dashboard ── moved to lib/api/dashboard.ts, re-exported above. */
/* ── Rides, flags, complaints, lost-and-found ── moved to lib/api/rides.ts, re-exported above. */
/* ── Drivers (listings, live stats, referrals, training, payouts) ── moved to lib/api/drivers.ts, re-exported above. */
/* ── Earnings ── moved to lib/api/earnings.ts, re-exported above. */
/* ── Settings + AI Assistant ── moved to lib/api/settings-ai.ts, re-exported above. */
/* ── Service areas, venues, incentives, surge status, vehicle types, fare configs, export approvals ── moved to lib/api/pricing.ts, re-exported above. */
/* ── Driver Document Verification ─────────
   Moved to lib/api/driver-documents.ts, re-exported above. */

/* ── Bulk/legacy CSV imports (drivers, bookings, Stripe mapping, riders) ── moved to lib/api/imports.ts, re-exported above. */
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

/* ── Corporate accounts/wallet/members/policy/domains/billing ── moved to lib/api/corporate.ts, re-exported above. */
/* ── Cloud messaging, marketing suppression, promo usage/stats ── moved to lib/api/marketing.ts, re-exported above. */
/* ── Users + admin wallet credit/debit ── moved to lib/api/users-wallet.ts, re-exported above. */
/* ── Promotions, disputes, safety incident queue, support tickets ── moved to lib/api/safety-disputes.ts, re-exported above. */
/* ── FAQs, legal documents, area fees/tax, driver area assignment ── moved to lib/api/content-area.ts, re-exported above. */
/* Driver notes moved to lib/api/driver-documents.ts, re-exported above. */

export const getDriverActivity = (driverId: string) =>
    request<any[]>(`/api/admin/drivers/${driverId}/activity`);


/* ── Heat map data + settings ── moved to lib/api/heatmap.ts, re-exported above. */
/* ── Staff, subscription plans/payments, audit logs, quests ── moved to lib/api/staff-subscriptions.ts, re-exported above. */
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
