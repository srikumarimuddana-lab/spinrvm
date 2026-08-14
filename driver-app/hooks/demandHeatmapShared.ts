/**
 * One demand-heatmap poller, many views.
 *
 * Android Auto projects from the running phone app, so when a driver plugs in
 * there are two surfaces wanting the same data. Both used to call
 * `useDemandHeatmap()`, and each instance owns a timer — so a driver with the
 * car connected made twice the requests and paid twice the battery and data
 * for identical payloads.
 *
 * Worse than the waste was the divergence. The two surfaces decided "am I
 * online?" from different places: the phone dashboard from `useDriverDashboard`'s
 * own state (which reflects a server-forced offline), the car from
 * `authStore.driver.is_online` directly. A driver taken offline by dispatch —
 * expired insurance, say — stopped seeing demand on the phone and kept seeing
 * it on the head unit, being told to reposition for work they could not accept.
 *
 * So this module makes the phone the single publisher and the car a read-only
 * subscriber. One timer, one online signal, one payload.
 *
 * The publisher-presence tracking matters as much as the data: if the phone's
 * hook unmounts (driver switches tab, screen tears down) the car must not keep
 * rendering the last snapshot forever. A frozen map that looks live is worse
 * than an empty one — the driver cannot tell it stopped updating. With no
 * publisher, `useDemandHeatmapView()` reports idle and no cells.
 */

import { useEffect, useState } from 'react';

import type { HeatmapCell, HeatmapStatus, HeatmapSurge } from './useDemandHeatmap';

export interface DemandHeatmapSnapshot {
  cells: HeatmapCell[];
  status: HeatmapStatus;
  surge: HeatmapSurge | null;
  isV2: boolean;
  /** Cell size the SERVER used, so a view draws the grid it actually sent. */
  cellLatDeg: number | null;
  cellLngDeg: number | null;
}

const EMPTY: DemandHeatmapSnapshot = {
  cells: [],
  status: 'idle',
  surge: null,
  isV2: false,
  cellLatDeg: null,
  cellLngDeg: null,
};

let snapshot: DemandHeatmapSnapshot = EMPTY;
let publisherCount = 0;
const listeners = new Set<(s: DemandHeatmapSnapshot) => void>();

function emit(): void {
  for (const l of listeners) l(snapshot);
}

/** Called by the polling hook on every state change. */
export function publishDemandHeatmap(next: DemandHeatmapSnapshot): void {
  snapshot = next;
  emit();
}

/**
 * Register the polling hook as the active publisher.
 *
 * Refcounted rather than a boolean: React can mount the next instance before
 * unmounting the previous one (strict mode, a remount on navigation), and a
 * boolean would have the outgoing unmount clear a flag the incoming instance
 * had just set — leaving the car blank with a live publisher running.
 */
export function registerDemandHeatmapPublisher(): () => void {
  publisherCount += 1;
  return () => {
    publisherCount = Math.max(0, publisherCount - 1);
    if (publisherCount === 0) {
      // No one is fetching any more. Report idle rather than leaving the last
      // payload visible on a surface that has no way to show it has gone cold.
      snapshot = EMPTY;
      emit();
    }
  };
}

export function getDemandHeatmapSnapshot(): DemandHeatmapSnapshot {
  return publisherCount > 0 ? snapshot : EMPTY;
}

/**
 * Read-only view of whatever the phone is currently polling.
 *
 * Deliberately takes no arguments: a view that could ask for different
 * parameters than the publisher is how the two surfaces diverged in the first
 * place.
 */
export function useDemandHeatmapView(): DemandHeatmapSnapshot {
  const [state, setState] = useState<DemandHeatmapSnapshot>(getDemandHeatmapSnapshot);

  useEffect(() => {
    const listener = (s: DemandHeatmapSnapshot) => setState(publisherCount > 0 ? s : EMPTY);
    listeners.add(listener);
    // Sync on subscribe: the publisher may have emitted before this mounted.
    setState(getDemandHeatmapSnapshot());
    return () => {
      listeners.delete(listener);
    };
  }, []);

  return state;
}

/** Test-only reset so one test's snapshot cannot leak into the next. */
export function __resetDemandHeatmapSharedForTest(): void {
  snapshot = EMPTY;
  publisherCount = 0;
  listeners.clear();
}
