export type RouteCoordinate = readonly [lat: number, lng: number];
export type RouteGeometryKind = 'observed' | 'inferred';
export type RouteGapReason = 'missing_start' | 'internal_gap' | 'missing_tail';
export type RouteGeometryProvider =
  | 'osrm_match'
  | 'google_roads'
  | 'observed_fallback'
  | 'osrm_inferred';

export type RouteGeometryStatus =
  | 'pending'
  | 'processing'
  | 'complete'
  | 'incomplete'
  | 'failed';

export interface ActualRouteSegment {
  coordinates: RouteCoordinate[];
  provider?: RouteGeometryProvider;
  geometry_kind?: RouteGeometryKind;
  gap_reason?: RouteGapReason;
  id?: string;
  points?: RouteCoordinate[];
  captured_from?: string;
  captured_to?: string;
  source?: 'observed' | 'osrm' | 'google_roads';
  confidence?: 'high' | 'medium' | 'low';
}

export interface RouteQuality {
  confidence: 'high' | 'medium' | 'low';
  coverage_pct: number;
  covered_seconds: number;
  lifecycle_seconds: number;
  max_gap_seconds: number;
  missing_tail: boolean;
  completion_distance_m: number | null;
  incomplete_reason?: string;
  temporal_coverage_ratio?: number;
  observed_distance_km?: number;
  inferred_distance_km?: number;
  observed_distance_ratio?: number;
  inferred_distance_ratio?: number;
  inferred_gap_count?: number;
  endpoint_start_verified?: boolean;
  endpoint_end_verified?: boolean;
  failed_gaps?: RouteGapReason[];
}
