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
