/**
 * Minimal pub/sub channel for GPS fixes, so a screen can hand its CarMarker
 * EVERY accepted fix without re-rendering itself.
 *
 * Why it exists: dashboards throttle their location state (a full-screen
 * re-render per fix is wasteful), but the marker's playback buffer needs the
 * un-throttled fix stream — a throttle that stretches inter-fix spacing past
 * the playback delay starves the buffer and the car freezes, then jumps
 * (live-testing report 2026-09-02). This feed bypasses React state entirely:
 * the producer emits from its location callback, the marker ingests in a
 * subscription — zero renders either side.
 *
 * Not an event-emitter dependency: 20 lines, no queuing, no replay. A
 * subscriber that attaches late starts from the next fix (the marker also
 * receives the throttled coordinate prop as its seed/fallback).
 */

export interface MarkerFix {
  latitude: number;
  longitude: number;
  /** Raw reported GPS heading, if any (see selectBearing for trust rules). */
  heading?: number | null;
  /** Real measurement time (e.g. expo-location loc.timestamp). */
  timestampMs: number;
}

export interface FixFeed {
  emit(fix: MarkerFix): void;
  subscribe(cb: (fix: MarkerFix) => void): () => void;
}

export function createFixFeed(): FixFeed {
  const subs = new Set<(fix: MarkerFix) => void>();
  return {
    emit(fix) {
      for (const cb of subs) {
        try {
          cb(fix);
        } catch {
          // A subscriber throwing must never break the producer's
          // location callback or other subscribers.
        }
      }
    },
    subscribe(cb) {
      subs.add(cb);
      return () => {
        subs.delete(cb);
      };
    },
  };
}
