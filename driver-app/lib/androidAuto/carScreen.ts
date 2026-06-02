// Pure mapping from the driver ride-state machine to a car-screen model.
//
// Kept free of the auto-play native package and of React so it can be
// unit-tested in plain Jest and reused for both Android Auto (today) and
// iOS CarPlay (once the Apple entitlement is granted). The register module
// turns this plain model into a native InformationTemplate (a Pane on Android).
import type { ActiveRide, RideState } from '../../store/driverStore';

/** A single informational row on the car screen. */
export interface CarRow {
  text: string;
  detailText?: string;
}

/** Plain, serialisable description of what the head unit should show. */
export interface CarScreenModel {
  title: string;
  /** 1-4 rows. The car host rejects empty or >4-row panes, so we always emit at least one. */
  items: CarRow[];
}

const APP_TITLE = 'Spinr Driver';

function riderName(activeRide: ActiveRide | null): string {
  const first = activeRide?.rider?.first_name?.trim();
  return first && first.length > 0 ? first : 'your rider';
}

function pickup(activeRide: ActiveRide | null): string {
  return activeRide?.ride?.pickup_address?.trim() || 'Pickup location';
}

function dropoff(activeRide: ActiveRide | null): string {
  return activeRide?.ride?.dropoff_address?.trim() || 'Drop-off location';
}

/**
 * Map the driver ride-state machine to the rows shown on the car head unit.
 *
 * The driver's `is_online` flag lives in component-local state (not the global
 * store), so the car screen is intentionally read-only and ride-centric: it
 * reflects whatever ride the driver is currently engaged with. State names
 * mirror `RideState` in store/driverStore.ts exactly.
 */
export function buildCarScreen(rideState: RideState, activeRide: ActiveRide | null): CarScreenModel {
  switch (rideState) {
    case 'ride_offered':
      return {
        title: 'New ride request',
        items: [
          { text: `Pick up ${riderName(activeRide)}`, detailText: pickup(activeRide) },
          { text: 'Drop-off', detailText: dropoff(activeRide) },
        ],
      };

    case 'navigating_to_pickup':
      return {
        title: 'Heading to pickup',
        items: [
          { text: `Pick up ${riderName(activeRide)}`, detailText: pickup(activeRide) },
          { text: 'Then drop-off', detailText: dropoff(activeRide) },
        ],
      };

    case 'arrived_at_pickup':
      return {
        title: 'Arrived at pickup',
        items: [
          { text: `Waiting for ${riderName(activeRide)}`, detailText: pickup(activeRide) },
          { text: 'Drop-off', detailText: dropoff(activeRide) },
        ],
      };

    case 'trip_in_progress':
      return {
        title: 'Trip in progress',
        items: [
          { text: `Dropping off ${riderName(activeRide)}`, detailText: dropoff(activeRide) },
        ],
      };

    case 'trip_completed':
      return {
        title: 'Trip completed',
        items: [{ text: 'Trip completed', detailText: 'Open Spinr on your phone for the next ride' }],
      };

    case 'idle':
    default:
      return {
        title: APP_TITLE,
        items: [
          { text: 'No active ride', detailText: 'Open Spinr on your phone to go online' },
        ],
      };
  }
}
