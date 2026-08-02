// src/app/dashboard/monitoring/types.ts

export interface MonitoringDriver {
  id: string;
  name: string;
  phone: string;
  photo_url: string | null;
  lat: number | null;
  lng: number | null;
  // Effective online state — intent (DB is_online) AND presence (Redis TTL
  // heartbeat). A driver whose app was force-killed shows as offline here
  // even while the DB row still says is_online=true.
  is_online: boolean;
  // Driver tapped "Go Online". Stale if the app died mid-shift.
  intent_online?: boolean;
  // Driver's WebSocket heartbeat / location ping is still fresh.
  is_present?: boolean;
  // TTL seconds for interpreting is_present in tooltips (default 90 s).
  presence_ttl?: number;
  is_available: boolean;
  vehicle_make: string | null;
  vehicle_model: string | null;
  vehicle_color: string | null;
  license_plate: string | null;
  vehicle_type_id: string | null;
  rating: number | null;
  total_rides: number;
  active_ride_id: string | null;
  service_area_id: string | null;
}

export interface MonitoringRide {
  id: string;
  status: "searching" | "driver_assigned" | "driver_arrived" | "in_progress";
  rider_id: string;
  rider_name: string;
  rider_phone: string | null;
  rider_photo: string | null;
  driver_id: string | null;
  driver_name: string | null;
  driver_phone: string | null;
  pickup_lat: number;
  pickup_lng: number;
  pickup_address: string | null;
  dropoff_lat: number;
  dropoff_lng: number;
  dropoff_address: string | null;
  driver_lat: number | null;
  driver_lng: number | null;
  total_fare: number | null;
  distance_km: number | null;
  created_at: string;
  is_corporate: boolean;
  // Was this ride originally booked as a scheduled (future-dated) ride? A
  // rider who planned around a specific pickup time has less slack to just
  // re-hail than an on-demand rider does if it gets cancelled post-dispatch
  // — surfaced so ops can tell these apart (scheduled-rides gap review,
  // Finding #12).
  is_scheduled: boolean;
}

export type MonitoringWsEvent =
  | { type: "driver_location_update"; driver_id: string; lat: number; lng: number; speed?: number; heading?: number }
  | { type: "ride_status_changed"; ride_id: string; status: string; is_scheduled?: boolean }
  | { type: "driver_status_changed"; driver_id: string; is_online: boolean }
  | { type: "ride_requested"; ride: MonitoringRide }
  | { type: "ride_completed"; ride_id: string; fare?: number }
  | { type: "ride_cancelled"; ride_id: string; is_scheduled?: boolean }
  | { type: "drivers_snapshot"; drivers: MonitoringDriver[] }
  | { type: "rides_snapshot"; rides: MonitoringRide[] };

export interface AlertEvent {
  id: string;
  timestamp: string; // ISO
  icon: "online" | "offline" | "ride_new" | "ride_done" | "ride_cancelled";
  message: string;
  driver_id?: string;
  ride_id?: string;
}

export interface MonitoringCounts {
  online: number;
  onRide: number;
  offline: number;
  activeRides: number;
}

export interface MonitoringFilters {
  showOnline: boolean;
  showOffline: boolean;
  showRides: boolean;
  serviceAreaId: string | null;
  vehicleTypeId: string | null;
}

export type SelectedItem =
  | { type: "driver"; id: string }
  | { type: "ride"; id: string }
  | null;
