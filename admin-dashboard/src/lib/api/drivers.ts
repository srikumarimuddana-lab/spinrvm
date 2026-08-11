// Driver listings, live stats, referrals, training, and payout summaries.
// Extracted from the monolithic lib/api.ts as part of the per-domain split
// (this is one contiguous block of the original file's driver-related
// functions; more live in api/driver-documents.ts and elsewhere — the
// original file had no strict single "Drivers" boundary).

import { request } from "./client";

/* ── Drivers ──────────────────────────────── */
export const getDrivers = (opts: {
    limit?: number;
    offset?: number;
    is_verified?: boolean;
    is_online?: boolean;
    is_available?: boolean;
    status?: string;
    service_area_id?: string;
    vehicle_type_id?: string;
    search?: string;
    photo_status?: string;
    /** ACTION_ITEMS.md B14 backfill queue — drivers missing license_number
     * or license_class. Cannot be combined with `search` (backend 400s). */
    missing_license?: boolean;
    sort_by?: string;
    sort_dir?: "asc" | "desc";
} = {}) => {
    const sp = new URLSearchParams();
    if (opts.limit != null) sp.set("limit", String(opts.limit));
    if (opts.offset != null) sp.set("offset", String(opts.offset));
    if (opts.is_verified != null) sp.set("is_verified", String(opts.is_verified));
    if (opts.is_online != null) sp.set("is_online", String(opts.is_online));
    if (opts.is_available != null) sp.set("is_available", String(opts.is_available));
    if (opts.status) sp.set("status", opts.status);
    if (opts.service_area_id) sp.set("service_area_id", opts.service_area_id);
    if (opts.vehicle_type_id) sp.set("vehicle_type_id", opts.vehicle_type_id);
    if (opts.search) sp.set("search", opts.search);
    if (opts.photo_status) sp.set("photo_status", opts.photo_status);
    if (opts.missing_license != null) sp.set("missing_license", String(opts.missing_license));
    if (opts.sort_by) sp.set("sort_by", opts.sort_by);
    if (opts.sort_dir) sp.set("sort_dir", opts.sort_dir);
    const qs = sp.toString();
    return request<any[]>(`/api/admin/drivers${qs ? `?${qs}` : ""}`);
};

/** POST-based typeahead search — keeps search terms (may include phone digits) out of URL/server logs. */
export const adminSearchDrivers = (opts: {
    search: string;
    limit?: number;
    is_online?: boolean;
    is_available?: boolean;
}) =>
    request<any[]>("/api/admin/drivers/search", {
        method: "POST",
        body: JSON.stringify(opts),
    });

/** POST-based typeahead search — keeps search terms out of URL/server logs. */
export const adminSearchUsers = (opts: {
    search: string;
    role?: "all" | "rider" | "driver" | "admin";
    limit?: number;
}) =>
    request<any[]>("/api/admin/users/search", {
        method: "POST",
        body: JSON.stringify(opts),
    });
export const getDriverRides = (id: string) =>
    request<any>(`/api/admin/drivers/${id}/rides`);

export const getDriverDailyActivity = (id: string, date?: string) =>
    request<any>(`/api/admin/drivers/${id}/daily-activity${date ? `?date=${date}` : ""}`);

export interface DriverLiveStats {
    total_rides: number;
    total_earnings: number;
    avg_rating: number | null;
    acceptance_rate: number | null;
    cancelled_by_driver: number;
    total_assigned: number;
    // Driver avatar, loaded lazily here so the bulk drivers list no longer
    // has to ship profile_image (a base64 blob for legacy accounts).
    photo_url: string | null;
    /** Last 4 of the driver's licence number. The full value is Vault-encrypted
     * at rest and never leaves the backend; the bulk list only carries the
     * opaque token, so the detail panel reads the mask from here. */
    license_number_last4: string | null;
    /** True when a licence number exists on the row (even if it could not be
     * decrypted), so the panel can distinguish "none on file" from "unreadable". */
    license_number_on_file: boolean;
    /** Last 4 of the driver's SIN. Stored in the clear precisely so T4A
     * readiness is visible without decrypting anything — unlike the licence,
     * this needs no round-trip through Vault. */
    sin_last4: string | null;
    /** True when Spinr holds an encrypted SIN. This is the T4A gate: without
     * it a slip cannot be filed, and it is NOT the same as Stripe's
     * `id_number_provided` above, which reports a number Stripe never returns. */
    sin_on_file: boolean;
    /** When the driver supplied it. */
    sin_collected_at: string | null;
}
export const getDriverLiveStats = (id: string) =>
    request<DriverLiveStats>(`/api/admin/drivers/${id}/live-stats`);

export interface DriverReferee {
    name: string;
    email: string;
    referred_at: string;
    is_driver: boolean;
    completed_rides: number;
    rides_required: number;
    rides_remaining: number;
    qualified: boolean;
    status: "earned" | "in_progress";
}
export interface DriverReferralSummary {
    referral_code: string;
    total_referrals: number;
    qualified_referrals: number;
    pending_referrals: number;
    referral_earnings: number;
    reward_amount: number;
    rides_required: number;
    referees: DriverReferee[];
    referred_by?: { name: string; code: string } | null;
}
export const getDriverReferrals = (id: string) =>
    request<DriverReferralSummary>(`/api/admin/drivers/${id}/referrals`);

// ─── LMS training integration ───────────────────────────────────────
// The backend matches the driver against the external Spinr LMS by phone
// number and proxies its training record (see routes/admin/drivers.py
// admin_get_driver_training + services/lms_service.py).
export interface DriverTrainingCourse {
    course_title: string | null;
    status: string;
    progress: number;
    enrolled_at: string | null;
    completed_at: string | null;
}
export interface DriverTrainingCertificate {
    certificate_number: string;
    course_title: string;
    final_quiz_score: number | null;
    issued_at: string;
    expires_at: string | null;
    status: "active" | "revoked" | "expired" | string;
}
export interface DriverTrainingQuizAttempt {
    quiz_title: string | null;
    score: number;
    passed: boolean;
    attempted_at: string;
}
export interface DriverTrainingCommunication {
    communication_type: string;
    message_type: string;
    subject: string | null;
    status: string;
    sent_at: string;
}
export interface DriverTraining {
    matched: boolean;
    reason: "no_phone" | "not_found_in_lms" | null;
    phone_last4: string | null;
    lms: {
        driver: {
            id: string;
            full_name: string;
            email: string;
            phone: string | null;
            city: string | null;
            spinr_approved: boolean;
            sgi_approved: boolean;
        };
        training: {
            status: "not_invited" | "invited" | "registered" | "in_progress" | "completed" | string;
            registered: boolean;
            registered_at: string | null;
            completed_at: string | null;
            completion_percentage: number;
            courses: DriverTrainingCourse[];
        };
        certificates: DriverTrainingCertificate[];
        history: {
            quiz_attempts: DriverTrainingQuizAttempt[];
            communications: DriverTrainingCommunication[];
        };
    } | null;
}
export const getDriverTraining = (id: string, refresh = false) =>
    request<DriverTraining>(
        `/api/admin/drivers/${id}/training${refresh ? "?refresh=true" : ""}`,
    );

export interface ReferralLeader {
    driver_id: string;
    driver_code: string;
    name: string;
    total_referrals: number;
    qualified_referrals: number;
    referral_earnings: number;
}
export interface ReferralLeaderboard {
    leaders: ReferralLeader[];
    fleet_total_referrals: number;
    fleet_total_referrers: number;
    reward_amount: number;
    rides_required: number;
}
export const getReferralLeaderboard = (limit = 20) =>
    request<ReferralLeaderboard>(`/api/admin/referrals/leaderboard?limit=${limit}`);

export const getRiderReferralLeaderboard = (limit = 20) =>
    request<ReferralLeaderboard>(`/api/admin/referrals/rider-leaderboard?limit=${limit}`);

export interface ReferralAnalytics {
    source: "driver" | "rider";
    funnel: {
        total_referred: number | null; // null when an area filter is active
        qualified: number;
        redeemed: number;
        processing: number;
        failed: number;
        redemption_rate: number | null;
        total_paid: string;
        referrer_paid: string;
        referee_paid: string;
        avg_paid: string;
    };
    trend: { date: string; redeemed: number; paid: string }[];
    reward_amount: number;
    rides_required: number;
}
export const getReferralAnalytics = (params: {
    source?: "driver" | "rider";
    serviceAreaId?: string | null;
    start?: string | null;
    end?: string | null;
} = {}) => {
    const q = new URLSearchParams();
    if (params.source) q.set("source", params.source);
    if (params.serviceAreaId) q.set("service_area_id", params.serviceAreaId);
    if (params.start) q.set("start", params.start);
    if (params.end) q.set("end", params.end);
    const qs = q.toString();
    return request<ReferralAnalytics>(`/api/admin/referrals/analytics${qs ? `?${qs}` : ""}`);
};

export interface FailedReferralClaim {
    id: string;
    referee_user_id: string;
    kind: string;
    referrer_name: string;
    referee_name: string;
    referrer_reward: string;
    referee_reward: string;
    created_at: string;
}
export const getFailedReferralClaims = (params: { source?: "driver" | "rider"; limit?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.source) q.set("source", params.source);
    if (params.limit) q.set("limit", String(params.limit));
    const qs = q.toString();
    return request<{ claims: FailedReferralClaim[]; total: number }>(
        `/api/admin/referrals/failed-claims${qs ? `?${qs}` : ""}`,
    );
};
export const requeueFailedReferral = (refereeUserId: string) =>
    request<{ success: boolean; requeued: string }>(
        `/api/admin/referrals/failed-claims/${encodeURIComponent(refereeUserId)}/requeue`,
        { method: "POST" },
    );

export interface ReferralPair {
    id: string;
    referrer_name: string;
    referee_name: string;
    status: string;
    referrer_reward: string;
    referee_reward: string;
    created_at: string;
}
export const getReferralPairs = (params: { source?: "driver" | "rider"; limit?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.source) q.set("source", params.source);
    if (params.limit) q.set("limit", String(params.limit));
    const qs = q.toString();
    return request<{ pairs: ReferralPair[]; total: number }>(
        `/api/admin/referrals/pairs${qs ? `?${qs}` : ""}`,
    );
};

export interface DriverPayoutSummary {
    summary: {
        lifetime_earnings: number;
        lifetime_ride_earnings?: number;
        lifetime_bonuses?: number;
        lifetime_tips: number;
        ytd_earnings: number;
        total_paid_out: number;
        /** Completed payout_type='stripe_sync' rows — legacy-app money paid
         * via Stripe Transfers, shown separately because it must not deduct
         * from pending_balance (the earnings it cashed out predate this DB). */
        legacy_stripe_transfers?: number;
        pending_in_flight: number;
        pending_balance: number;
        on_hold: number;
        rides_count: number;
        /** Completed rides imported from the previous app, excluded from
         *  lifetime_earnings. -1 means the count was unavailable. */
        imported_rides_excluded?: number;
        active_days_30d: number;
        last_payout: {
            id: string;
            amount: number;
            processed_at: string | null;
            bank_name: string | null;
            account_last4: string | null;
        } | null;
        last_failed_payout: {
            id: string;
            amount: number;
            error_message: string | null;
            created_at: string;
        } | null;
    };
    payment_method: {
        has_bank_account: boolean;
        bank_name: string | null;
        account_last4: string | null;
        account_holder_name: string | null;
        account_type: string | null;
        is_verified: boolean | null;
        stripe_connected: boolean;
        stripe_account_hint: string | null;
    };
    payouts: Array<{
        id: string;
        amount: number;
        status: "pending" | "processing" | "completed" | "failed" | string;
        payout_type?: string | null;
        stripe_transfer_id?: string | null;
        stripe_payout_id: string | null;
        bank_name: string | null;
        account_last4: string | null;
        error_message: string | null;
        created_at: string;
        processed_at: string | null;
    }>;
    bonuses?: Array<{
        id: string;
        amount: number;
        kind: string;
        description: string | null;
        created_at: string;
    }>;
    // Stripe Connect KYC + tax identity mirror (migration 92).
    // SIN itself is never exposed here — only on-file flags and last4.
    // Use /reveal-sin for the one-shot retrieval.
    kyc: {
        details_submitted: boolean;
        charges_enabled: boolean;
        payouts_enabled: boolean;
        verification_status: string | null;
        business_type: string | null;
        id_number_provided: boolean;
        id_number_last4: string | null;
        /** True when Spinr holds a Vault-encrypted SIN (migration 289) — the
         * copy /reveal-sin decrypts and the T4A reads. NOT the same as
         * `id_number_provided`, which reports a number Stripe never returns. */
        sin_on_file: boolean;
        sin_last4: string | null;
        gst_hst_number: string | null;
        requirements_due: string[];
        requirements_past_due: string[];
        disabled_reason: string | null;
        tos_accepted_at: string | null;
        last_synced_at: string | null;
    };
}
export const getDriverPayoutsSummary = (id: string) =>
    request<DriverPayoutSummary>(`/api/admin/drivers/${id}/payouts-summary`);

export const refreshDriverStripeKyc = (id: string) =>
    request<{ status: string; synced: boolean; message: string }>(
        `/api/admin/drivers/${id}/refresh-stripe-kyc`,
        { method: "POST" },
    );

export const refreshDriverStripePayouts = (id: string) =>
    request<{
        synced: boolean;
        message: string;
        transfers_inserted: number;
        transfers_skipped: number;
        bank_payouts_synced: number;
        ledger_entries_synced: number;
        payouts: DriverPayoutSummary["payouts"];
        bank_payouts: Array<{
            id: string;
            amount: number;
            currency: string;
            status: string;
            method: string | null;
            arrival_date: string | null;
            bank_last4: string | null;
            failure_code: string | null;
            failure_message: string | null;
            created_at: string;
            synced_at: string;
        }>;
    }>(`/api/admin/drivers/${id}/refresh-stripe-payouts`, { method: "POST" });

/* Fleet-wide Stripe payout sync (super_admin). driver_ids omitted = every
 * mapped driver. Unlike the per-driver call this does not fail the whole run
 * on one unreachable account — check `synced` and `plan_errors`. */
export const refreshAllDriverStripePayouts = (opts?: { driver_ids?: string[] }) =>
    request<{
        synced: boolean;
        message: string;
        drivers_scanned: number;
        transfers_inserted: number;
        transfers_skipped: number;
        bank_payouts_synced: number;
        ledger_entries_synced: number;
        plan_errors: { row_ref: string; field: string; message: string }[];
        ledger_errors: { row_ref: string; field: string; message: string }[];
    }>(`/api/admin/drivers/refresh-stripe-payouts`, {
        method: "POST",
        body: JSON.stringify(opts ?? {}),
    });

/* Recompute stored driver_statements.totals (super_admin). apply=false is a
 * pure preview — nothing is written — which is what the UI shows before
 * asking to confirm. */
export const recomputeStatementTotals = (opts?: {
    driver_ids?: string[];
    since?: string;
    limit?: number;
    apply?: boolean;
}) =>
    request<{
        applied: boolean;
        scanned: number;
        corrected: number;
        unchanged: number;
        has_more: boolean;
        delta_earnings: number;
        delta_payouts: number;
        skipped: string[];
        failed: string[];
        changes: {
            statement_id: string;
            driver_id: string;
            period_type: string;
            period_start: string;
            before: { earnings: string | null; payouts_total: string | null; trips: number | null };
            after: { earnings: string | null; payouts_total: string | null; trips: number | null };
        }[];
    }>(`/api/admin/drivers/statements/recompute-totals`, {
        method: "POST",
        body: JSON.stringify(opts ?? {}),
    });

/* Fleet-wide KYC refresh (super_admin). With retire_unreachable=false (the
 * default) drivers whose Stripe account the current key cannot see are only
 * REPORTED under account_not_on_key — nothing is detached — so it is safe to
 * run first and read. Re-run with retire_unreachable=true to also repair. */
export const refreshAllDriverStripeKyc = (opts?: { driver_ids?: string[]; retire_unreachable?: boolean }) =>
    request<{
        total: number;
        ok?: number;
        no_stripe_account?: number;
        account_not_on_key?: number;
        stripe_error?: number;
        drivers: Record<string, string>;
        note: string;
    }>(`/api/admin/drivers/refresh-stripe-kyc`, {
        method: "POST",
        body: JSON.stringify(opts ?? {}),
    });

/* ── Driver earnings statements ──────────────────────────
 * Weekly/monthly statements the backend statement job emails to drivers
 * (backend/utils/driver_statement_job.py). Admin can list what was sent,
 * download the same PDF for any period or date range, and re-send it to
 * the driver. Statements regenerate from live data on every call, so an
 * admin download and the driver's emailed copy can never diverge. */

export type DriverStatementPeriodType = "weekly" | "monthly" | "custom";

export interface DriverStatement {
    id: string;
    period_type: DriverStatementPeriodType;
    period_start: string;
    period_end: string;
    /** claimed | sent | failed | skipped_no_email | skipped_inactive */
    status: string;
    totals: {
        earnings?: Record<string, string>;
        payouts_total?: string;
        /** Era split (statements stored before it existed lack these). */
        payouts_spinr_total?: string | null;
        payouts_previous_app_total?: string | null;
        trips?: number;
    } | null;
    email_sent_at: string | null;
    created_at: string | null;
}

export const getDriverStatements = (driverId: string, limit = 24) =>
    request<{ statements: DriverStatement[] }>(
        `/api/admin/drivers/${driverId}/statements?limit=${limit}`,
    );

/** Either an anchored period (period_type + period_start, what the job
 *  sent) or a custom inclusive date range (start + end, the payout-tab
 *  date filter). The backend rejects a selection that is neither. */
export type StatementSelection =
    | { period_type: "weekly" | "monthly"; period_start: string }
    | { start: string; end: string };

const statementParams = (selection: StatementSelection) =>
    new URLSearchParams(selection as unknown as Record<string, string>).toString();

/** Statement PDF bytes. Uses a raw authed fetch (not request<T>, which
 *  parses JSON) — same pattern as the compliance report downloads. */
export async function downloadDriverStatement(
    driverId: string,
    selection: StatementSelection,
): Promise<{ blob: Blob; filename: string }> {
    const { useAuthStore } = await import("@/store/authStore");
    const token = useAuthStore.getState().token;
    const headers: Record<string, string> = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(
        `/api/admin/drivers/${driverId}/statements/pdf?${statementParams(selection)}`,
        { headers },
    );
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Could not generate statement (${res.status})`);
    }
    const suffix =
        "period_start" in selection
            ? `${selection.period_type}-${selection.period_start}`
            : `${selection.start}_${selection.end}`;
    return { blob: await res.blob(), filename: `spinr-statement-${suffix}.pdf` };
}

/** Email the same statement to the driver's on-file address. */
export const emailDriverStatement = (driverId: string, selection: StatementSelection) =>
    request<{ sent: boolean; period_label: string }>(
        `/api/admin/drivers/${driverId}/statements/email?${statementParams(selection)}`,
        { method: "POST" },
    );

export interface RevealSinResponse {
    sin: string;
    sin_last4: string;
    audit_log_id: string | null;
    warning: string;
}
export const revealDriverSin = (id: string) =>
    request<RevealSinResponse>(`/api/admin/drivers/${id}/reveal-sin`, { method: "POST" });

export const getDriverStats = (params?: {
    service_area_id?: string;
    start_date?: string;
    end_date?: string;
}) => {
    const sp = new URLSearchParams();
    if (params?.service_area_id) sp.set("service_area_id", params.service_area_id);
    if (params?.start_date) sp.set("start_date", params.start_date);
    if (params?.end_date) sp.set("end_date", params.end_date);
    return request<{
        stats: {
            total: number;
            online: number;
            verified: number;
            unverified: number;
            total_rides: number;
            total_earnings: number;
            avg_rating: number;
        };
        area_stats: {
            service_area_id: string;
            service_area_name: string;
            total: number;
            online: number;
            verified: number;
            unverified: number;
            total_rides: number;
            total_earnings: number;
        }[];
        charts: {
            daily_joins: { date: string; date_raw: string; count: number }[];
            daily_rides: { date: string; date_raw: string; count: number }[];
            daily_earnings: { date: string; date_raw: string; amount: number }[];
        };
        drivers: any[];
        service_areas: { id: string; name: string }[];
    }>(`/api/admin/drivers/stats?${sp.toString()}`);
};

export const updateDriver = (id: string, data: Record<string, any>) =>
    request<any>(`/api/admin/drivers/${id}`, { method: "PUT", body: JSON.stringify(data) });

