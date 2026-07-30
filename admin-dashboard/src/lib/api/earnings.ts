// Earnings, subscription revenue stats. Extracted from the monolithic
// lib/api.ts as part of the per-domain split.

import { request } from "./client";

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

