// Analytics dashboards, driver offer/cancellation stats, demand forecast,
// surge history, payouts, and dispute resolution. Extracted from the
// monolithic lib/api.ts as part of the per-domain split.

import { request } from "./client";
import type { EarningsPeriod, MetricWithDelta } from "./earnings";

/* ── Analytics ──────────────────────────── */
/** `serviceAreaId` omitted means all areas (migration 350 added the scope —
 *  the headline KPI cards previously blended every market together). */
export const getAnalyticsOverview = (dateRange = "30d", serviceAreaId?: string) => {
    const sp = new URLSearchParams({ date_range: dateRange });
    if (serviceAreaId) sp.set("service_area_id", serviceAreaId);
    return request<any>(`/api/admin/analytics/overview?${sp.toString()}`);
};

/** Main-dashboard stat cards aggregated by time window + optional service area. */
export const getDashboardOverview = (opts: { range?: string; service_area_id?: string | null } = {}) => {
    const sp = new URLSearchParams();
    if (opts.range) sp.set("range", opts.range);
    if (opts.service_area_id) sp.set("service_area_id", opts.service_area_id);
    return request<any>(`/api/admin/analytics/dashboard?${sp.toString()}`);
};

export const getCancellationBreakdown = (dateRange = "30d", serviceAreaId?: string) =>
    request<any>(`/api/admin/analytics/cancellation-reasons?date_range=${dateRange}${serviceAreaId ? `&service_area_id=${serviceAreaId}` : ''}`);

export type DriverAcceptanceSort =
    | "acceptance_rate" | "cancellation_rate" | "total_rides"
    | "completed" | "cancelled_by_driver" | "rating" | "name";

export interface DriverAcceptanceOpts {
    serviceAreaId?: string;
    limit?: number;
    offset?: number;
    search?: string;
    sortBy?: DriverAcceptanceSort;
    order?: "asc" | "desc";
    minRides?: number;
    lowPerformersOnly?: boolean;
}

/** Paginated/sortable server-side — the summary counts cover the whole
 *  filtered set, so `low_performer_count` stays reachable via
 *  `lowPerformersOnly` (or an ascending sort) rather than being sliced off
 *  the end of the default descending page. */
export const getDriverAcceptanceRates = (dateRange = "30d", opts: DriverAcceptanceOpts = {}) => {
    const sp = new URLSearchParams({ date_range: dateRange });
    if (opts.serviceAreaId) sp.set("service_area_id", opts.serviceAreaId);
    if (opts.limit != null) sp.set("limit", String(opts.limit));
    if (opts.offset) sp.set("offset", String(opts.offset));
    if (opts.search) sp.set("search", opts.search);
    if (opts.sortBy) sp.set("sort_by", opts.sortBy);
    if (opts.order) sp.set("order", opts.order);
    if (opts.minRides) sp.set("min_rides", String(opts.minRides));
    if (opts.lowPerformersOnly) sp.set("low_performers_only", "true");
    return request<any>(`/api/admin/analytics/driver-acceptance?${sp.toString()}`);
};

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

/* ── Weekly auto-payouts (Spinr-controlled Sunday batch) ── */

/** One weekly run of the Sunday auto-payout batch (`auto_payout_batches`). */
export interface AutoPayoutBatch {
    id: string;
    week_key: string;
    /** running = in flight; partial = some paid, some failed/deferred (resumable). */
    status: "running" | "completed" | "partial" | "failed";
    started_at?: string | null;
    completed_at?: string | null;
    drivers_eligible: number;
    drivers_paid: number;
    drivers_failed: number;
    total_amount: number | string;
    error_summary?: string | null;
    /** counts = everyone skipped; drivers_with_balance = those with money held up. */
    skipped_summary?: {
        counts?: Record<string, number>;
        drivers_with_balance?: Record<string, string[]>;
    } | null;
    /**
     * Per-market slice of the run, keyed by service_area_id ("unassigned"
     * for drivers with no area). The batch itself always runs fleet-wide —
     * this exists so the page can report per area. Null on runs recorded
     * before per-area tracking existed.
     */
    area_summary?: Record<
        string,
        { paid: number; failed: number; skipped: number; amount: string }
    > | null;
    created_at?: string;
}

/** A driver the batch cannot pay right now, with the amount being held. */
export interface BlockedDriver {
    driver_id: string;
    reason: string;
    pending_amount: string;
    service_area_id?: string | null;
}

export const getAutoPayoutBatches = (limit = 20, weekKey?: string) => {
    const sp = new URLSearchParams({ limit: String(limit) });
    // Direct lookup for a run older than the default window.
    if (weekKey) sp.set("week_key", weekKey);
    return request<{ batches: AutoPayoutBatch[]; count: number }>(
        `/api/admin/auto-payouts/batches?${sp.toString()}`,
    );
};

export const getBlockedPayoutDrivers = (limit = 50, serviceAreaId?: string) => {
    const sp = new URLSearchParams({ limit: String(limit) });
    if (serviceAreaId) sp.set("service_area_id", serviceAreaId);
    return request<{ blocked: BlockedDriver[]; count: number; by_reason: Record<string, number> }>(
        `/api/admin/auto-payouts/blocked-drivers?${sp.toString()}`,
    );
};

/* ── Disputes (resolve) ─────────────────── */
export const resolveDispute = (id: string, data: { resolution: string; refund_amount?: number; admin_note?: string }) =>
    request<any>(`/api/admin/disputes/${id}/resolve`, { method: "PUT", body: JSON.stringify(data) });

