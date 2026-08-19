/**
 * Serializes every start/stop/re-configure of the two expo-location tasks
 * (`spinr-background-location` dispatch tracking, `spinr-car-location`
 * Android Auto display).
 *
 * WHY THIS EXISTS — the single-writer invariant:
 *   1. Dispatch task registered ⇒ the car task must NOT be registered.
 *   2. The car task may register only while dispatch is not running.
 *   3. Any actor observing both registered stops the car task and re-asserts
 *      the dispatch task's options.
 *
 * Two verified expo-location (57.x) native behaviours make overlap actively
 * destructive, not just wasteful:
 *   - ONE shared Android LocationTaskService serves ALL location tasks
 *     (single manifest <service>; LocationTaskConsumer.kt builds an explicit
 *     component intent). Stopping either task runs `stopForeground(true);
 *     stopSelf()` on that shared instance — stripping the OTHER task's
 *     foreground promotion, which is precisely what lets Android throttle
 *     background GPS.
 *   - A STATIC `sLastTimestamp` in LocationTaskConsumer dedups fixes across
 *     ALL consumers: with two tasks registered, every fix is delivered to
 *     both and the losing copy is silently discarded before JS. The 2s car
 *     watcher out-races the 4s dispatch task and starves the durable route
 *     stream (how ride SPR-PE7TTB lost 83% of its trip).
 *
 * The invariant is DEFINED over native registration state (persisted by
 * expo-task-manager across process death), SERIALIZED here within a JS
 * context, and REPAIRED by the periodic self-heal in backgroundLocation.ts
 * for cross-context races this mutex cannot see (headless vs foreground JS
 * each hold their own chain).
 *
 * No re-entrancy: composition happens via `*Unlocked` internals — a nested
 * runExclusive would deadlock the FIFO chain.
 */
import { recordNonFatal } from './crashlytics';

const LOCK_WAIT_TIMEOUT_MS = 10_000;

let tail: Promise<unknown> = Promise.resolve();
let timeoutReported = false;

/**
 * Run `fn` after every previously queued exclusive section has finished.
 *
 * Bounded wait: if the queue is still busy after 10s (a hung native call —
 * getBackgroundPermissionsAsync and startLocationUpdatesAsync have both been
 * observed to stall), proceed anyway with a one-shot non-fatal breadcrumb.
 * Same trade as GEOFENCE_STOP_WAIT_TIMEOUT_MS: a wedged lock must never
 * brick go-online/go-offline, and the invariant survives a timed-out lock
 * because the self-heal re-asserts it.
 */
export function runExclusive<T>(label: string, fn: () => Promise<T>): Promise<T> {
  const previous = tail;
  let release!: () => void;
  tail = new Promise<void>((resolve) => {
    release = resolve;
  });

  const run = async (): Promise<T> => {
    const waited = await Promise.race([
      previous.then(() => 'released' as const, () => 'released' as const),
      new Promise<'timeout'>((resolve) => {
        const timer = setTimeout(() => resolve('timeout'), LOCK_WAIT_TIMEOUT_MS);
        // Don't keep a test/process alive just for the lock timer.
        (timer as unknown as { unref?: () => void }).unref?.();
      }),
    ]);
    if (waited === 'timeout' && !timeoutReported) {
      timeoutReported = true;
      try {
        recordNonFatal(new Error('location task lock wait timed out'), {
          domain: 'drivers',
          surface: 'driver-app',
          reason: 'location_task_lock_timeout',
          label,
        });
      } catch {
        // Telemetry must never affect lock behaviour.
      }
    }
    try {
      return await fn();
    } finally {
      release();
    }
  };
  return run();
}

/** @internal Test-only — drop any queued sections and the one-shot report flag. */
export function _resetLocationTaskArbiter(): void {
  tail = Promise.resolve();
  timeoutReported = false;
}
