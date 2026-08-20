/**
 * One OSRM live-route poller, many views.
 *
 * ─── The bug this exists to fix ──────────────────────────────────────────────
 * The Android Auto map drew `rides.planned_route_polyline` — the line captured
 * once at BOOKING time — and nothing ever replaced it. So a driver who took a
 * different road watched the car screen insist on the original route for the
 * whole trip, while the phone beside them showed the real one. Reported from a
 * head unit as "the polyline mapped at the start of the ride is not updating if
 * I change the route".
 *
 * The phone already solved this: `app/driver/(tabs)/index.tsx` polls
 * `/rides/{id}/live-route` (self-hosted OSRM, so no metered Directions spend)
 * every 20s and draws that road-matched line from the driver's LIVE position to
 * the current destination. But it kept the result in component state, which the
 * car cannot read — and on a car-only cold launch that component never mounts
 * at all.
 *
 * ─── Why a shared channel rather than a second poller ────────────────────────
 * Exactly the reasoning in `demandHeatmapShared.ts`, which this mirrors: two
 * independent pollers means two timers, double the requests, and — worse — two
 * surfaces that can disagree about the same trip. The phone publishes here; the
 * car subscribes.
 *
 * The one deliberate difference from the heatmap module: `getLiveRouteSnapshot`
 * does NOT blank itself when the publisher count hits zero. A demand heatmap
 * going stale is misleading, so it clears. A route does not work that way — the
 * road to the drop-off is still the road to the drop-off a few seconds later,
 * and blanking it would flash the car's route line off every time the driver
 * switched tabs. Staleness is exposed via `receivedAt` instead, so the car can
 * decide for itself (see `useCarLiveRoute`), and `hasLiveRoutePublisher()` lets
 * the car take over polling when the phone is not doing it.
 */
import { useSyncExternalStore } from 'react';

export type LiveRouteLeg = 'pickup' | 'dropoff';

export interface LiveRouteSnapshot {
  /** Road-matched line from the driver's live position to the destination. */
  polyline: { latitude: number; longitude: number }[];
  /** Which leg the line goes to — guards against drawing a stale leg's route. */
  destination: LiveRouteLeg | null;
  /** Ride the line belongs to, so another ride's geometry is never drawn. */
  rideId: string | null;
  etaMinutes: number | null;
  distanceKm: number | null;
  /** `Date.now()` when this landed. Null when nothing has. */
  receivedAt: number | null;
}

const EMPTY: LiveRouteSnapshot = {
  polyline: [],
  destination: null,
  rideId: null,
  etaMinutes: null,
  distanceKm: null,
  receivedAt: null,
};

let snapshot: LiveRouteSnapshot = EMPTY;
let publisherCount = 0;
const listeners = new Set<(s: LiveRouteSnapshot) => void>();

function emit(): void {
  for (const l of listeners) {
    try {
      l(snapshot);
    } catch {
      // A bad subscriber must not stop the others.
    }
  }
}

/** Publish a freshly-fetched route. Called by whichever surface is polling. */
export function publishLiveRoute(next: Omit<LiveRouteSnapshot, 'receivedAt'>): void {
  snapshot = { ...next, receivedAt: Date.now() };
  emit();
}

/**
 * Drop the current line — the leg changed, the ride ended, or OSRM returned
 * nothing routable. Explicit rather than implicit: a route line that outlives
 * its leg would point the driver at the pickup they have already made.
 */
export function clearLiveRoute(): void {
  snapshot = EMPTY;
  emit();
}

/**
 * Register as the active poller. Refcounted, not a boolean: React can mount the
 * next instance before unmounting the previous one, and a boolean would let the
 * outgoing unmount clear a flag the incoming instance had just set.
 */
export function registerLiveRoutePublisher(): () => void {
  publisherCount += 1;
  return () => {
    publisherCount = Math.max(0, publisherCount - 1);
  };
}

/** True while some surface is actively polling — the car defers when it is. */
export function hasLiveRoutePublisher(): boolean {
  return publisherCount > 0;
}

export function getLiveRouteSnapshot(): LiveRouteSnapshot {
  return snapshot;
}

/** Subscribe form required by useSyncExternalStore. */
function subscribe(onChange: () => void): () => void {
  const listener = () => onChange();
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * Read-only view of whatever is currently being polled.
 *
 * useSyncExternalStore rather than the useEffect+useState pairing in
 * `demandHeatmapShared.ts`: this IS an external store, and the hand-rolled
 * version has a real tear window — a publish landing between render and the
 * subscribe effect is missed entirely. `snapshot` is only ever reassigned in
 * publishLiveRoute/clearLiveRoute, so its identity is stable between changes,
 * which is what getSnapshot requires.
 */
export function useLiveRouteView(): LiveRouteSnapshot {
  return useSyncExternalStore(subscribe, getLiveRouteSnapshot, getLiveRouteSnapshot);
}

/**
 * Is this snapshot safe to draw for the given ride and leg?
 *
 * Three ways a stored line goes wrong, all of which have to be caught before it
 * reaches a windscreen: it belongs to a previous ride, it routes to the leg the
 * driver has already completed, or it is simply too old to trust. Pure so the
 * rules are testable without a poller.
 */
export function isLiveRouteUsable(
  s: LiveRouteSnapshot,
  rideId: string | null | undefined,
  leg: LiveRouteLeg,
  maxAgeMs: number,
  now: number,
): boolean {
  if (s.polyline.length < 2) return false;
  if (!rideId || s.rideId !== rideId) return false;
  if (s.destination !== leg) return false;
  if (s.receivedAt === null) return false;
  return now - s.receivedAt <= maxAgeMs;
}

/** Test-only reset so one test's snapshot cannot leak into the next. */
export function __resetLiveRouteSharedForTest(): void {
  snapshot = EMPTY;
  publisherCount = 0;
  listeners.clear();
}
