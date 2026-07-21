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
  /** Kilometres — not money. Billed/fare-basis distance (booking-time estimate, kept under fare-lock). */
  distance_km: number;
  /** Kilometres — not money. Booking-time straight-line estimate, frozen at booking. */
  planned_distance_km?: number;
  /** Kilometres — not money. GPS-measured trip distance written at completion (and refreshed by the route finalizer when a late tail batch arrives). */
  actual_distance_km?: number;
  /** Minutes — not money. */
  duration_minutes: number;
  /** Minutes — not money. Measured trip duration, lifted from ride_metrics by the API. */
  actual_duration_minutes?: number;
  /** True when the rider pays the booking-time fare regardless of the measured distance (SK fare-lock). */
  fare_locked?: boolean;
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
  actual_route_segments?: ActualRouteSegment[];
  actual_completion_point?: {
    latitude: number;
    longitude: number;
  };
  route_quality?: RouteQuality;
  route_schema_version?: number;
  route_revision?: number;
  /** Matches route_revision only when the stored snapshot image reflects the current route projection. */
  snapshot_revision?: number;
  route_snapshot_url?: string;
  route_geometry_status?: RouteGeometryStatus;
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
