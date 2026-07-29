// Live ride monitoring (map view), admin-initiated ride actions (cancel/
// complete/create), places/fare/promo lookups used by the manual-ride
// flow, and infra health (Redis/websocket). Extracted from the monolithic
// lib/api.ts as part of the per-domain split.

import { request } from "./client";

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

