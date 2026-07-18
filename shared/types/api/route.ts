export type RouteCoordinate = readonly [lat: number, lng: number];

export type RouteGeometryStatus =
  | 'pending'
  | 'processing'
  | 'complete'
  | 'incomplete'
  | 'failed';

export interface ActualRouteSegment {
  id: string;
  points: RouteCoordinate[];
  captured_from: string;
  captured_to: string;
  source: 'observed' | 'osrm' | 'google_roads';
  confidence: 'high' | 'medium' | 'low';
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
}
