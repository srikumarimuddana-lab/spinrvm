/**
 * Minimal in-process pub/sub for the `route_finalized` WS push (Contract A).
 *
 * The driver WebSocket lives in `useDriverDashboard` (mounted on the dashboard),
 * but the ride-detail screen that renders the finalized route owns its own local
 * `ride` state and its own `GET /rides/{id}` fetch. This bus lets the dashboard's
 * WS handler notify whichever ride-detail screen (if any) is currently viewing
 * that ride so it can refetch the durable segments / quality / snapshot once
 * finalization lands. No subscriber => no-op.
 */

export interface RouteFinalizedEvent {
  rideId: string;
  routeRevision: number;
  routeGeometryStatus?: 'complete' | 'incomplete' | 'failed' | string;
}

type Listener = (event: RouteFinalizedEvent) => void;

const listeners = new Set<Listener>();

/** Subscribe to route-finalized pushes. Returns an unsubscribe function. */
export function subscribeRouteFinalized(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Fan a route-finalized push out to every subscriber. */
export function emitRouteFinalized(event: RouteFinalizedEvent): void {
  // Snapshot so a subscriber that unsubscribes during dispatch can't mutate the
  // set mid-iteration; a throwing subscriber must not break fan-out to others.
  for (const listener of Array.from(listeners)) {
    try {
      listener(event);
    } catch {
      /* a bad subscriber must not break the fan-out */
    }
  }
}
