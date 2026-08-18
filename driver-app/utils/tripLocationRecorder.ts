import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Location from 'expo-location';
import {
  tripLocationOutbox,
  type PendingTripLocationSession,
  type TripLocationFix,
  type TripLocationPoint,
  type TripLocationRejection,
  type TripLocationSource,
} from './tripLocationOutbox';

const ACTIVE_RIDE_KEY = 'spinr_trip_location_active_ride';
const FLUSH_INTERVAL_MS = 10_000;
const FLUSH_POINT_THRESHOLD = 25;
const ACTIVE_TRIP_WATCHDOG_MS = 30_000;
// Server-side freshness bound for a completion fix (ride_complete.py rejects
// anything older as stale_capture). An outbox fallback point older than this
// would be rejected anyway, so don't send it.
const COMPLETION_FIX_MAX_AGE_MS = 120_000;

export interface TripLocationBatchRequest {
  ride_id: string;
  recording_session_id: string;
  points: TripLocationPoint[];
}

export interface TripLocationBatchAck {
  recording_session_id: string;
  acked_through: number | null;
  rejected?: TripLocationRejection[];
}

export type TripLocationTransport = (request: TripLocationBatchRequest) => Promise<TripLocationBatchAck>;

// Server responses that will never succeed on retry for this batch: the ride
// is gone (404), no longer active (409/410), or the points are permanently
// rejected, e.g. outside the completed-ride retention window (422). Every
// transport must DRAIN these instead of throwing — flushPending aborts its
// whole loop on a transport throw, so one poisoned batch would otherwise
// head-of-line-block every other pending session forever. Lives here (not in
// tripLocationTransport.ts) so the headless background transport can share it
// without pulling the axios/app-shell modules into the background task bundle.
export const TERMINAL_STATUS_CODES = new Set([404, 409, 410, 422]);

/** Synthesize a full ack for a terminally-rejected batch so the outbox clears it. */
export function drainTerminalAck(request: TripLocationBatchRequest): TripLocationBatchAck {
  const lastSequence = request.points[request.points.length - 1]?.sequence_number ?? 0;
  return {
    recording_session_id: request.recording_session_id,
    acked_through: lastSequence,
  };
}

export interface FlushPendingOptions {
  force?: boolean;
  /**
   * Skip the failure backoff window. ONLY the ride-completion flush may pass
   * this — the GPS tail must reach settlement immediately even mid-outage.
   * Every periodic/forced flush respects backoff: without it, the background
   * task retried every ~4s for the whole duration of a backend outage.
   */
  bypassBackoff?: boolean;
}

// Exponential backoff after a failed upload: 5s, 10s, 20s … capped at 5min,
// ±20% jitter so a fleet-wide outage doesn't synchronize retries. Per-JS-
// context state (headless task and foreground app each keep their own clock);
// worst case a fresh context retries once immediately, then backs off.
const FLUSH_BACKOFF_BASE_MS = 5_000;
const FLUSH_BACKOFF_MAX_MS = 300_000;
const FLUSH_BACKOFF_JITTER = 0.2;

export interface FlushPendingResult {
  uploaded_points: number;
  acknowledged_points: number;
  skipped: boolean;
}

export interface BoundedFlushResult extends FlushPendingResult {
  timedOut: boolean;
}

export interface CompletionCaptureResult {
  point: TripLocationPoint | null;
  pendingCount: number;
}

export interface RecorderHealth {
  activeRideId: string | null;
  pendingPointCount: number;
  lastCaptureAt: string | null;
  lastFlushAt: string | null;
  degraded: boolean;
  degradationReason: 'no_recent_fix' | 'upload_failure' | null;
}

type TripLocationOutbox = Pick<
  typeof tripLocationOutbox,
  | 'startSession'
  | 'enqueue'
  | 'listPendingSessions'
  | 'peek'
  | 'acknowledge'
  | 'pendingCount'
  | 'closeSession'
  | 'latestPoint'
  | 'purgeAll'
>;

export interface TripLocationRecorderOptions {
  outbox?: TripLocationOutbox;
  now?: () => number;
}

type NativeLocationWithElapsedTime = Location.LocationObject & {
  elapsedRealtime?: number;
  mocked?: boolean;
};

function isAcknowledgement(value: TripLocationBatchAck): value is TripLocationBatchAck & { acked_through: number } {
  return value.acked_through !== null && Number.isInteger(value.acked_through) && value.acked_through >= 0;
}

function sensorMonotonicMilliseconds(location: NativeLocationWithElapsedTime): number {
  const elapsedRealtime = location.elapsedRealtime;
  if (typeof elapsedRealtime === 'number' && Number.isFinite(elapsedRealtime) && elapsedRealtime >= 0) {
    return Math.round(elapsedRealtime);
  }
  return Math.max(0, Math.round(location.timestamp));
}

export class TripLocationRecorder {
  private readonly outbox: TripLocationOutbox;
  private readonly now: () => number;
  private activeRideId: string | null = null;
  private activeRideStartedAt: number | null = null;
  private lastCaptureAt: number | null = null;
  private lastFlushAt: number | null = null;
  private lastFlushAttemptAt: number | null = null;
  private lastUploadFailureAt: number | null = null;
  private flushPromise: Promise<FlushPendingResult> | null = null;
  private nextFlushNotBefore = 0;
  private consecutiveFlushFailures = 0;

  constructor(options: TripLocationRecorderOptions = {}) {
    this.outbox = options.outbox ?? tripLocationOutbox;
    this.now = options.now ?? Date.now;
  }

  async startRide(rideId: string): Promise<PendingTripLocationSession> {
    if (!rideId) throw new Error('A ride id is required to start trip location recording.');

    // Publish the active ride id BEFORE creating the session. The headless
    // background task calls recordNativeFix() with no rideId and resolves it
    // from ACTIVE_RIDE_KEY; enqueue() lazily creates the session on first
    // write. If startSession() were first and threw (e.g. SQLite/WAL error),
    // the key would stay unset and every background fix would be dropped —
    // the trip would silently record zero points. Setting the key first makes
    // the background path self-heal regardless of the session pre-create.
    this.activeRideId = rideId;
    this.activeRideStartedAt = this.now();
    await AsyncStorage.setItem(ACTIVE_RIDE_KEY, rideId);
    return this.outbox.startSession(rideId);
  }

  async recordNativeFix(
    location: Location.LocationObject,
    source: TripLocationSource,
    rideId?: string,
    isCompletionFix = false,
  ): Promise<TripLocationPoint | null> {
    const resolvedRideId = rideId ?? await this.resolveActiveRideId();
    if (!resolvedRideId) return null;

    const nativeLocation = location as NativeLocationWithElapsedTime;
    const point: TripLocationFix = {
      ride_id: resolvedRideId,
      captured_at: new Date(location.timestamp).toISOString(),
      monotonic_ms: sensorMonotonicMilliseconds(nativeLocation),
      lat: location.coords.latitude,
      lng: location.coords.longitude,
      accuracy: location.coords.accuracy ?? null,
      speed: location.coords.speed ?? null,
      heading: location.coords.heading ?? null,
      altitude: location.coords.altitude ?? null,
      source,
      mocked: nativeLocation.mocked === true,
      is_completion_fix: isCompletionFix,
    };

    const queued = await this.outbox.enqueue(point);
    this.activeRideId = resolvedRideId;
    this.activeRideStartedAt ??= this.now();
    this.lastCaptureAt = this.now();
    return queued;
  }

  async flushPending(
    transport?: TripLocationTransport,
    options: FlushPendingOptions = {},
  ): Promise<FlushPendingResult> {
    if (!transport) {
      return { uploaded_points: 0, acknowledged_points: 0, skipped: true };
    }
    if (this.flushPromise) return this.flushPromise;

    this.flushPromise = this.flushPendingInternal(transport, options).finally(() => {
      this.flushPromise = null;
    });
    return this.flushPromise;
  }

  /**
   * Force-flush the durable outbox with an upper time bound. Used on the
   * ride-completion path so the server sees the full GPS tail before
   * computing route quality/distance, while a dead network can never block
   * the driver from completing the trip. On timeout the underlying flush
   * keeps running and queued points stay durable in SQLite; transport
   * errors propagate so the caller can record them.
   */
  async flushPendingWithTimeout(
    transport: TripLocationTransport | undefined,
    timeoutMs: number,
  ): Promise<BoundedFlushResult> {
    const flush = this.flushPending(transport, { force: true, bypassBackoff: true });
    let timer: ReturnType<typeof setTimeout> | undefined;
    const timeout = new Promise<null>((resolve) => {
      timer = setTimeout(() => resolve(null), timeoutMs);
    });
    try {
      const result = await Promise.race([flush, timeout]);
      if (result === null) {
        // Detach without dropping the rejection on the floor — the shared
        // flushPromise is already tracked for the next caller.
        flush.catch(() => {});
        return { uploaded_points: 0, acknowledged_points: 0, skipped: true, timedOut: true };
      }
      return { ...result, timedOut: false };
    } finally {
      if (timer !== undefined) clearTimeout(timer);
    }
  }

  async captureCompletionFix(rideId: string): Promise<CompletionCaptureResult> {
    try {
      const location = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High });
      const point = await this.recordNativeFix(location, 'completion', rideId, true);
      return { point, pendingCount: await this.outbox.pendingCount(rideId) };
    } catch {
      // A fresh fix is unavailable (GPS off, provider timeout). Fall back to
      // the newest durable point inside the server's freshness bound: an
      // already-captured coordinate is strictly better endpoint evidence
      // than none, and referencing its existing session/sequence means the
      // server sees no duplicate row. Older-than-bound or absent → the
      // caller still receives no coordinate and completion proceeds.
      const fallback = await this.latestFreshOutboxPoint(rideId);
      return { point: fallback, pendingCount: await this.outbox.pendingCount(rideId) };
    }
  }

  private async latestFreshOutboxPoint(rideId: string): Promise<TripLocationPoint | null> {
    try {
      const latest = await this.outbox.latestPoint(rideId);
      if (!latest) return null;
      const capturedAtMs = Date.parse(latest.captured_at);
      if (!Number.isFinite(capturedAtMs) || this.now() - capturedAtMs > COMPLETION_FIX_MAX_AGE_MS) {
        return null;
      }
      return latest;
    } catch {
      return null;
    }
  }

  async applyAcknowledgement(acknowledgement: TripLocationBatchAck): Promise<number> {
    if (!isAcknowledgement(acknowledgement)) return 0;

    const pendingPoints = await this.outbox.peek(acknowledgement.recording_session_id);
    if (!pendingPoints.length) return 0;

    const highestPersistedSequence = pendingPoints[pendingPoints.length - 1]?.sequence_number;
    if (
      highestPersistedSequence === undefined
      || acknowledgement.acked_through > highestPersistedSequence
    ) {
      throw new Error('Trip location server acknowledgement exceeded the persisted point range.');
    }

    const acknowledgedPoints = pendingPoints.filter(
      (point) => point.sequence_number <= acknowledgement.acked_through,
    ).length;
    if (!acknowledgedPoints) return 0;

    await this.outbox.acknowledge(
      acknowledgement.recording_session_id,
      acknowledgement.acked_through,
      acknowledgement.rejected ?? [],
    );
    this.lastFlushAt = this.now();
    return acknowledgedPoints;
  }

  async getRecorderHealth(rideId?: string): Promise<RecorderHealth> {
    const activeRideId = rideId ?? this.activeRideId ?? await this.resolveActiveRideId();
    const now = this.now();
    const pendingPointCount = activeRideId ? await this.outbox.pendingCount(activeRideId) : 0;
    const captureAge = this.lastCaptureAt === null
      ? (this.activeRideStartedAt === null ? 0 : now - this.activeRideStartedAt)
      : now - this.lastCaptureAt;
    const noRecentFix = activeRideId !== null && captureAge >= ACTIVE_TRIP_WATCHDOG_MS;
    // A live backoff window counts as upload failure: without the second
    // clause the degradation banner would flicker off during a 5-minute
    // backoff while uploads are provably failing.
    const uploadFailure = (this.lastUploadFailureAt !== null
      && now - this.lastUploadFailureAt < ACTIVE_TRIP_WATCHDOG_MS)
      || now < this.nextFlushNotBefore;

    return {
      activeRideId,
      pendingPointCount,
      lastCaptureAt: this.lastCaptureAt === null ? null : new Date(this.lastCaptureAt).toISOString(),
      lastFlushAt: this.lastFlushAt === null ? null : new Date(this.lastFlushAt).toISOString(),
      degraded: noRecentFix || uploadFailure,
      degradationReason: noRecentFix ? 'no_recent_fix' : uploadFailure ? 'upload_failure' : null,
    };
  }

  /**
   * Discard all recorded location state for sign-out.
   *
   * Clears the durable outbox plus the in-memory and AsyncStorage pointers to
   * the active ride, so a subsequent sign-in on the same device starts clean
   * rather than resolving ACTIVE_RIDE_KEY to the previous driver's ride and
   * attributing fresh fixes to it.
   *
   * Never throws: sign-out must complete even if SQLite is unavailable. A failed
   * purge leaves coordinates on disk, which is logged as an error (PII retention
   * is not a "recoverable anomaly") but must not strand the user in a
   * half-signed-out state.
   */
  async purgeAll(): Promise<void> {
    this.activeRideId = null;
    this.activeRideStartedAt = null;
    this.lastCaptureAt = null;
    this.lastFlushAt = null;
    this.lastFlushAttemptAt = null;
    this.lastUploadFailureAt = null;
    this.nextFlushNotBefore = 0;
    this.consecutiveFlushFailures = 0;
    try {
      await AsyncStorage.removeItem(ACTIVE_RIDE_KEY);
    } catch {
      // Best-effort; the outbox purge below is what removes the coordinates.
    }
    await this.outbox.purgeAll();
  }

  async closeRide(rideId: string): Promise<void> {
    await this.outbox.closeSession(rideId);
    if (this.activeRideId === rideId) {
      this.activeRideId = null;
      this.activeRideStartedAt = null;
      this.lastCaptureAt = null;
      await AsyncStorage.removeItem(ACTIVE_RIDE_KEY);
    }
  }

  private async flushPendingInternal(
    transport: TripLocationTransport,
    options: FlushPendingOptions,
  ): Promise<FlushPendingResult> {
    // Failure backoff — checked before any SQLite work. `force` does NOT
    // bypass this (the background task forces every native callback); only
    // the completion flush passes bypassBackoff.
    if (!options.bypassBackoff && this.now() < this.nextFlushNotBefore) {
      return { uploaded_points: 0, acknowledged_points: 0, skipped: true };
    }
    const sessions = await this.outbox.listPendingSessions();
    if (!sessions.length) return { uploaded_points: 0, acknowledged_points: 0, skipped: true };

    const firstBatch = await this.outbox.peek(sessions[0].recording_session_id);
    if (!firstBatch.length) return { uploaded_points: 0, acknowledged_points: 0, skipped: true };
    const elapsedSinceFlush = this.lastFlushAttemptAt === null
      ? Number.POSITIVE_INFINITY
      : this.now() - this.lastFlushAttemptAt;
    if (!options.force && firstBatch.length < FLUSH_POINT_THRESHOLD && elapsedSinceFlush < FLUSH_INTERVAL_MS) {
      return { uploaded_points: 0, acknowledged_points: 0, skipped: true };
    }

    let uploadedPoints = 0;
    let acknowledgedPoints = 0;
    this.lastFlushAttemptAt = this.now();
    try {
      for (const session of sessions) {
        while (true) {
          const points = await this.outbox.peek(session.recording_session_id);
          if (!points.length) break;

          const acknowledgement = await transport({
            ride_id: session.ride_id,
            recording_session_id: session.recording_session_id,
            points,
          });
          uploadedPoints += points.length;
          // The transport resolved — the network path works. Clear backoff
          // even before validating the ack shape (a malformed ack is a server
          // bug, not an outage to back off from).
          this.consecutiveFlushFailures = 0;
          this.nextFlushNotBefore = 0;
          if (acknowledgement.recording_session_id !== session.recording_session_id) {
            throw new Error('Trip location server acknowledgement referenced a different recording session.');
          }
          if (!isAcknowledgement(acknowledgement)) {
            return { uploaded_points: uploadedPoints, acknowledged_points: acknowledgedPoints, skipped: false };
          }

          const highestSubmittedSequence = points[points.length - 1]?.sequence_number;
          if (highestSubmittedSequence === undefined || acknowledgement.acked_through > highestSubmittedSequence) {
            throw new Error('Trip location server acknowledgement exceeded the submitted batch.');
          }
          await this.outbox.acknowledge(
            session.recording_session_id,
            acknowledgement.acked_through,
            acknowledgement.rejected ?? [],
          );
          acknowledgedPoints += points.filter((point) => point.sequence_number <= acknowledgement.acked_through).length;
          this.lastFlushAt = this.now();

          if (acknowledgement.acked_through < highestSubmittedSequence) break;
        }
      }
      return { uploaded_points: uploadedPoints, acknowledged_points: acknowledgedPoints, skipped: false };
    } catch (error) {
      this.lastUploadFailureAt = this.now();
      this.consecutiveFlushFailures += 1;
      const backoff = Math.min(
        FLUSH_BACKOFF_BASE_MS * 2 ** (this.consecutiveFlushFailures - 1),
        FLUSH_BACKOFF_MAX_MS,
      );
      const jitter = 1 + FLUSH_BACKOFF_JITTER * (2 * Math.random() - 1);
      this.nextFlushNotBefore = this.now() + Math.round(backoff * jitter);
      throw error;
    }
  }

  /** @internal Test-only — clear backoff state on the singleton between cases. */
  _resetUploadBackoff(): void {
    this.nextFlushNotBefore = 0;
    this.consecutiveFlushFailures = 0;
    this.lastUploadFailureAt = null;
  }

  private async resolveActiveRideId(): Promise<string | null> {
    if (this.activeRideId) return this.activeRideId;
    const rideId = await AsyncStorage.getItem(ACTIVE_RIDE_KEY);
    this.activeRideId = rideId;
    this.activeRideStartedAt ??= rideId ? this.now() : null;
    return rideId;
  }
}

export function createTripLocationRecorder(options: TripLocationRecorderOptions = {}): TripLocationRecorder {
  return new TripLocationRecorder(options);
}

export const tripLocationRecorder = createTripLocationRecorder();
