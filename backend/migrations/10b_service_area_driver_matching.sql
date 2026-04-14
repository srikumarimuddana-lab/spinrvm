-- Migration: Add driver matching settings to service_areas

ALTER TABLE service_areas
  ADD COLUMN IF NOT EXISTS driver_matching_algorithm TEXT NOT NULL DEFAULT 'nearest';

ALTER TABLE service_areas
  ADD COLUMN IF NOT EXISTS search_radius_km FLOAT NOT NULL DEFAULT 10.0;

ALTER TABLE service_areas
  ADD COLUMN IF NOT EXISTS min_driver_rating FLOAT NOT NULL DEFAULT 4.0;
