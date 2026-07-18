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

export interface FlushPendingOptions {
  force?: boolean;
}

export interface FlushPendingResult {
  uploaded_points: number;
  acknowledged_points: number;
  skipped: boolean;
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
  'startSession' | 'enqueue' | 'listPendingSessions' | 'peek' | 'acknowledge' | 'pendingCount' | 'closeSession'
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
  return Number.isInteger(value.acked_through) && value.acked_through >= 0;
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

  constructor(options: TripLocationRecorderOptions = {}) {
    this.outbox = options.outbox ?? tripLocationOutbox;
    this.now = options.now ?? Date.now;
  }

  async startRide(rideId: string): Promise<PendingTripLocationSession> {
    if (!rideId) throw new Error('A ride id is required to start trip location recording.');

    const session = await this.outbox.startSession(rideId);
    await AsyncStorage.setItem(ACTIVE_RIDE_KEY, rideId);
    this.activeRideId = rideId;
    this.activeRideStartedAt = this.now();
    return session;
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

  async captureCompletionFix(rideId: string): Promise<CompletionCaptureResult> {
    try {
      const location = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High });
      const point = await this.recordNativeFix(location, 'completion', rideId, true);
      return { point, pendingCount: await this.outbox.pendingCount(rideId) };
    } catch {
      // Completion remains possible after the driver explicitly explains why a
      // fresh fix is unavailable. The caller receives no coordinate from this path.
      return { point: null, pendingCount: await this.outbox.pendingCount(rideId) };
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
    const uploadFailure = this.lastUploadFailureAt !== null
      && now - this.lastUploadFailureAt < ACTIVE_TRIP_WATCHDOG_MS;

    return {
      activeRideId,
      pendingPointCount,
      lastCaptureAt: this.lastCaptureAt === null ? null : new Date(this.lastCaptureAt).toISOString(),
      lastFlushAt: this.lastFlushAt === null ? null : new Date(this.lastFlushAt).toISOString(),
      degraded: noRecentFix || uploadFailure,
      degradationReason: noRecentFix ? 'no_recent_fix' : uploadFailure ? 'upload_failure' : null,
    };
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
      throw error;
    }
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
