/**
 * Live route for the Android Auto map surface.
 *
 * The car used to draw `rides.planned_route_polyline` and nothing else — the
 * line captured once at BOOKING time. Take a different road and the head unit
 * kept insisting on the original route for the rest of the trip.
 *
 * Same shape as `useCarLocation`: subscribe to the shared channel the phone
 * publishes into, and fall back to fetching for ourselves when nothing else is.
 * The fallback is not optional — Android Auto can start the JS context with no
 * phone UI at all (`carSurface.tsx` documents the same problem for earnings),
 * and in that session there is no publisher to subscribe to, ever.
 *
 * Deferring to the phone when it IS polling is the point: two independent
 * timers against the same endpoint is exactly the waste `demandHeatmapShared`
 * was written to stop.
 */
import { useEffect, useState } from 'react';
import {
  clearLiveRoute,
  hasLiveRoutePublisher,
  isLiveRouteUsable,
  publishLiveRoute,
  registerLiveRoutePublisher,
  useLiveRouteView,
  type LiveRouteLeg,
} from '../../hooks/liveRouteShared';

/** Matches the phone's cadence in app/driver/(tabs)/index.tsx. */
const POLL_MS = 20_000;

/**
 * How long a line stays drawable after it was fetched.
 *
 * Three poll intervals. Long enough to ride out a couple of failed fetches
 * without the route blinking off, short enough that a genuinely dead poller
 * stops drawing a line the driver would otherwise assume is live. A wrong route
 * on a windscreen is worse than no route.
 */
export const LIVE_ROUTE_MAX_AGE_MS = POLL_MS * 3;

export interface CarLiveRoute {
  polyline: { latitude: number; longitude: number }[];
  /** Minutes REMAINING to the current leg's destination, server-derived. */
  etaMinutes: number | null;
  /** Kilometres REMAINING to the current leg's destination, server-derived. */
  distanceKm: number | null;
}

/**
 * The live route for `leg` of `rideId`, or null when there isn't a trustworthy
 * one and the caller should fall back to the stored planned line.
 *
 * `active` gates both the polling and the drawing — passing false during a
 * non-navigation state stops the timer rather than just hiding the result.
 */
export function useCarLiveRoute(
  rideId: string | null | undefined,
  leg: LiveRouteLeg | null,
  active: boolean,
): CarLiveRoute | null {
  const snapshot = useLiveRouteView();
  // Re-evaluated on a timer as well as on publish: usability is time-based, so
  // nothing would otherwise re-render at the moment a line ages out.
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), POLL_MS);
    return () => clearInterval(id);
  }, [active]);

  useEffect(() => {
    if (!active || !rideId) return;
    let cancelled = false;

    // api hard-imports the shared client; lazy-required so this module stays
    // loadable in tests/web where that chain is absent, matching useCarLocation.
    let api: { get: <T>(u: string) => Promise<{ data: T }> } | null = null;
    try {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      api = require('@shared/api/client').default;
    } catch {
      api = null;
    }
    if (!api) return;

    const fetchRoute = async () => {
      // Checked per tick, not once: the driver can open the phone dashboard
      // mid-trip, at which point it takes over and we should go quiet.
      if (hasLiveRoutePublisher()) return;
      const release = registerLiveRoutePublisher();
      try {
        const { data } = await api!.get<{
          polyline: [number, number][];
          eta_seconds: number | null;
          distance_km: number | null;
          destination: LiveRouteLeg | null;
        }>(`/rides/${rideId}/live-route`);
        if (cancelled || !data) return;
        if (Array.isArray(data.polyline) && data.polyline.length > 1) {
          publishLiveRoute({
            polyline: data.polyline.map((p) => ({ latitude: p[0], longitude: p[1] })),
            // Trust the SERVER's leg, not ours: it derives destination from
            // rides.status, so a leg that advanced between our render and this
            // response can't leave us drawing a route to a pickup already made.
            destination: data.destination ?? leg,
            rideId,
            etaMinutes:
              typeof data.eta_seconds === 'number' && data.eta_seconds > 0
                ? Math.max(1, Math.ceil(data.eta_seconds / 60))
                : null,
            distanceKm:
              typeof data.distance_km === 'number' && data.distance_km > 0
                ? data.distance_km
                : null,
          });
        } else {
          // Nothing routable — drop it so the surface falls back to the planned
          // line rather than holding one to a leg that may be finished.
          clearLiveRoute();
        }
      } catch {
        // OSRM or the network is down. Deliberately NOT clearing: the existing
        // line is still the best guess we have, and it ages out on its own via
        // LIVE_ROUTE_MAX_AGE_MS. Clearing here would blink the route off on a
        // single dropped request in a tunnel.
      } finally {
        release();
      }
    };

    fetchRoute();
    const id = setInterval(fetchRoute, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [rideId, leg, active]);

  if (!active || !leg) return null;
  if (!isLiveRouteUsable(snapshot, rideId, leg, LIVE_ROUTE_MAX_AGE_MS, now)) return null;
  // distanceKm rides along with etaMinutes: the status bar leads with what is
  // LEFT of the leg, and dropping it here forced the surface back onto the
  // booking-time trip figures, which read as remaining distance and are not.
  return {
    polyline: snapshot.polyline,
    etaMinutes: snapshot.etaMinutes,
    distanceKm: snapshot.distanceKm,
  };
}
