import type { RideStatus } from './ride';

// ── Outbound events (server → client) ────────────────────────────────────────

export interface WSAuthSuccess {
  type: 'auth_success';
  client_type: string;
}

export interface WSRideStatusChanged {
  type: 'ride_status_changed';
  ride_id: string;
  status: RideStatus;
  [key: string]: unknown;
}

export interface WSDriverLocationUpdate {
  type: 'driver_location_update';
  driver_id: string;
  /** Coordinate — not money. */
  lat: number;
  /** Coordinate — not money. */
  lng: number;
  updated_at: string;
}

export interface WSLocationBatchAck {
  type: 'location_batch_ack';
  count: number;
}

/**
 * Pre-match driver positions for the rider map.
 *
 * `lat`/`lng` are deliberately COARSE — snapped to a `precision_m` grid cell
 * (default 500m) by the backend, because before a ride is assigned there is no
 * relationship that justifies exact coordinates (PIPEDA; see
 * backend/utils/driver_map_visibility.py). Do not present these as a precise
 * position, and do not use them to compute an ETA — ETA and availability are
 * computed server-side from the true positions.
 *
 * Exact coordinates are only available for an assigned ride, via the ride
 * tracking events.
 *
 * The payload carries no `vehicle_make`/`vehicle_model` by design: together with
 * `heading` they made a specific vehicle re-identifiable on the street. Vehicle
 * details for the *assigned* driver come from the ride/driver detail endpoints.
 */
export interface WSNearbyDrivers {
  type: 'nearby_drivers';
  drivers: Array<{
    /** Driver row id. Previously typed `driver_id`, which the server has never
     *  sent — the index signature below hid the mismatch. */
    id: string;
    lat: number;
    lng: number;
    /** Always 'approximate' for pre-match positions. */
    precision?: 'approximate';
    /** Grid cell size in metres that `lat`/`lng` were snapped to. */
    precision_m?: number;
    heading?: number | null;
    vehicle_type_id?: string | null;
    vehicle_type_name?: string | null;
    marker_variant?: string | null;
    [key: string]: unknown;
  }>;
}

export interface WSChatMessage {
  type: 'chat_message';
  id: string;
  ride_id: string;
  text: string;
  sender: 'rider' | 'driver';
  timestamp: string;
}

export interface WSPing {
  type: 'ping';
  timestamp: string;
}

export interface WSRateLimited {
  type: 'rate_limited';
  retry_after_ms: number;
}

export interface WSDriverStatusChanged {
  type: 'driver_status_changed';
  driver_id: string;
  is_online: boolean;
  is_available: boolean;
}

export interface WSDriverConnectionLost {
  type: 'driver_connection_lost';
  driver_id: string;
}

export interface WSSessionRevoked {
  type: 'session_revoked';
  reason?: string;
}

export interface WSError {
  type: 'error';
  message: string;
}

/** Discriminated union of all events the server may send to clients. */
export type WSServerEvent =
  | WSAuthSuccess
  | WSRideStatusChanged
  | WSDriverLocationUpdate
  | WSLocationBatchAck
  | WSNearbyDrivers
  | WSChatMessage
  | WSPing
  | WSRateLimited
  | WSDriverStatusChanged
  | WSDriverConnectionLost
  | WSSessionRevoked
  | WSError;

// ── Inbound message types (client → server) ───────────────────────────────────

export type WSClientMessageType =
  | 'auth'
  | 'driver_location'
  | 'location_update'
  | 'location_batch'
  | 'ride_status_update'
  | 'get_nearby_drivers'
  | 'chat_message'
  | 'pong';
