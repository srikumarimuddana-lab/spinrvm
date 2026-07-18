import type { MoneyString } from './money';
import type { ActualRouteSegment, RouteGeometryStatus, RouteQuality } from './route';

export type RideStatus =
  | 'scheduled'
  | 'searching'
  | 'driver_assigned'
  | 'driver_accepted'
  | 'driver_arrived'
  | 'in_progress'
  | 'completed'
  | 'cancelled';

/** Statuses where a rider may have at most one concurrent ride. */
export const ACTIVE_RIDE_STATUSES: RideStatus[] = [
  'searching',
  'driver_assigned',
  'driver_accepted',
  'driver_arrived',
  'in_progress',
];

export interface Ride {
  id: string;
  rider_id: string;
  driver_id?: string;
  vehicle_type_id: string;
  pickup_address: string;
  /** Coordinate — not money. */
  pickup_lat: number;
  /** Coordinate — not money. */
  pickup_lng: number;
  dropoff_address: string;
  /** Coordinate — not money. */
  dropoff_lat: number;
  /** Coordinate — not money. */
  dropoff_lng: number;
  stops?: unknown;
  is_scheduled: boolean;
  requires_wav: boolean;
  quiet_mode: boolean;
  rider_notes?: string;
  scheduled_time?: string;
  corporate_account_id?: string;
  /** Kilometres — not money. */
  distance_km: number;
  /** Minutes — not money. */
  duration_minutes: number;
  base_fare: MoneyString;
  distance_fare: MoneyString;
  time_fare: MoneyString;
  booking_fee: MoneyString;
  /** Numeric multiplier — not money. */
  surge_multiplier: number;
  total_fare: MoneyString;
  tip_amount: MoneyString;
  payment_method: string;
  payment_method_id?: string;
  payment_intent_id?: string;
  payment_status: string;
  status: RideStatus;
  pickup_otp: string;
  ride_requested_at: string;
  driver_notified_at?: string;
  driver_accepted_at?: string;
  driver_arrived_at?: string;
  ride_started_at?: string;
  ride_completed_at?: string;
  cancelled_at?: string;
  driver_earnings: MoneyString;
  admin_earnings: MoneyString;
  cancellation_fee_driver: MoneyString;
  cancellation_fee_admin: MoneyString;
  rider_rating?: number;
  rider_comment?: string;
  // ── Versioned actual-route projection (ride_routes side-table) ──
  // Attached by backend/repositories/ride_repo.py::_project_route_detail on the
  // authorized ride-detail read. Declared here so rider/driver/admin surfaces
  // consume one shared contract instead of re-declaring these locally.
  actual_route_segments?: ActualRouteSegment[];
  route_quality?: RouteQuality;
  /** Monotonic finalization revision (0 until the first projection lands). */
  route_revision?: number;
  /** Signed, short-lived route-image URL; present only when the snapshot revision matches `route_revision`. */
  route_snapshot_url?: string;
  /** Revision of the currently published snapshot image (0 when none/stale). */
  snapshot_revision?: number;
  route_geometry_status?: RouteGeometryStatus;
  /** Geometry schema version; `>= 2` means segmented (never join across segments). */
  route_schema_version?: number;
  created_at: string;
  updated_at: string;
}

export interface DriverPublicView {
  id: string;
  name: string;
  rating?: number;
  total_rides?: number;
  photo_url?: string;
  vehicle_make?: string;
  vehicle_model?: string;
  vehicle_color?: string;
  license_plate?: string;
  vehicle_year?: number;
  /** Coordinate — not money. */
  lat?: number;
  /** Coordinate — not money. */
  lng?: number;
}
