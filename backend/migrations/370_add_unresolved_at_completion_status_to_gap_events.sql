-- Add 'unresolved_at_completion' to the allowed status values for ride_location_gap_events.
-- The route_gap_monitor uses this status when a ride completes while a location gap is still open.

ALTER TABLE ride_location_gap_events
    DROP CONSTRAINT ride_location_gap_events_status_valid;

ALTER TABLE ride_location_gap_events
    ADD CONSTRAINT ride_location_gap_events_status_valid
        CHECK (status IN ('open', 'resolved', 'unresolved_at_completion')) NOT VALID;

ALTER TABLE ride_location_gap_events
    VALIDATE CONSTRAINT ride_location_gap_events_status_valid;
