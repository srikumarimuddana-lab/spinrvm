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

export interface WSNearbyDrivers {
  type: 'nearby_drivers';
  drivers: Array<{
    driver_id: string;
    lat: number;
    lng: number;
    [key: string]: unknown;
  }>;
}

export interface WSChatMessage {
  type: 'chat_message';
  ride_id: string;
  sender_id: string;
  message: string;
  sent_at: string;
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
