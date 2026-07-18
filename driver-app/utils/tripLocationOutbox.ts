// Type-only imports: the native expo modules are require()d lazily at call
// time so importing this module (via driverStore in Jest / Expo Go contexts
// without the native runtime) never touches native bindings.
import type * as SQLite from 'expo-sqlite';

export type TripLocationSource = 'foreground' | 'background' | 'completion';

export interface TripLocationFix {
  ride_id: string;
  captured_at: string;
  monotonic_ms: number;
  lat: number;
  lng: number;
  accuracy: number | null;
  speed: number | null;
  heading: number | null;
  altitude: number | null;
  source: TripLocationSource;
  mocked: boolean;
  is_completion_fix: boolean;
}

export interface TripLocationPoint extends TripLocationFix {
  recording_session_id: string;
  sequence_number: number;
}

export interface PendingTripLocationSession {
  recording_session_id: string;
  ride_id: string;
  opened_at: string;
  closed_at: string | null;
}

export interface TripLocationRejection {
  sequence_number: number;
  reason: string;
}

/**
 * The inclusive sequence range that was actually submitted in the batch a
 * server acknowledgement refers to. Acknowledgements only license deleting
 * points the server has actually seen — never the whole `<= acked_through`
 * prefix, which would destroy queued-but-never-uploaded points (e.g. the
 * pending tail behind a completion fix).
 */
export interface TripLocationSubmittedRange {
  from: number;
  to: number;
}

type TripLocationOutboxRow = TripLocationPoint & {
  enqueued_at: string;
};

type SqlExecutor = Pick<
  SQLite.SQLiteDatabase,
  'runAsync' | 'getFirstAsync' | 'getAllAsync'
>;

type TripLocationOutboxDatabase = SqlExecutor & Pick<SQLite.SQLiteDatabase, 'execAsync'> & {
  withExclusiveTransactionAsync(task: (txn: SqlExecutor) => Promise<void>): Promise<void>;
};

export interface TripLocationOutboxOptions {
  openDatabase?: () => Promise<TripLocationOutboxDatabase>;
  randomUUID?: () => string;
  now?: () => string;
}

const DATABASE_NAME = 'spinr-trip-location-outbox.db';
const MAX_UPLOAD_BATCH_SIZE = 500;

const SCHEMA = `
  PRAGMA journal_mode = WAL;

  CREATE TABLE IF NOT EXISTS trip_location_sessions (
    session_id TEXT PRIMARY KEY NOT NULL,
    ride_id TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    next_sequence_number INTEGER NOT NULL DEFAULT 0 CHECK (next_sequence_number >= 0)
  );

  CREATE UNIQUE INDEX IF NOT EXISTS trip_location_sessions_one_open_session_per_ride
    ON trip_location_sessions (ride_id)
    WHERE closed_at IS NULL;

  CREATE TABLE IF NOT EXISTS trip_location_outbox (
    session_id TEXT NOT NULL,
    ride_id TEXT NOT NULL,
    sequence_number INTEGER NOT NULL CHECK (sequence_number >= 0),
    captured_at TEXT NOT NULL,
    monotonic_ms INTEGER NOT NULL CHECK (monotonic_ms >= 0),
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    accuracy REAL,
    speed REAL,
    heading REAL,
    altitude REAL,
    source TEXT NOT NULL CHECK (source IN ('foreground', 'background', 'completion')),
    mocked INTEGER NOT NULL CHECK (mocked IN (0, 1)),
    is_completion_fix INTEGER NOT NULL CHECK (is_completion_fix IN (0, 1)),
    enqueued_at TEXT NOT NULL,
    PRIMARY KEY (session_id, sequence_number)
  );

  CREATE INDEX IF NOT EXISTS trip_location_outbox_by_ride
    ON trip_location_outbox (ride_id, session_id, sequence_number);

  CREATE TABLE IF NOT EXISTS trip_location_quarantine (
    session_id TEXT NOT NULL,
    sequence_number INTEGER NOT NULL,
    ride_id TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    monotonic_ms INTEGER NOT NULL,
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    accuracy REAL,
    speed REAL,
    heading REAL,
    altitude REAL,
    source TEXT NOT NULL,
    mocked INTEGER NOT NULL,
    is_completion_fix INTEGER NOT NULL,
    rejection_reason TEXT NOT NULL,
    quarantined_at TEXT NOT NULL,
    PRIMARY KEY (session_id, sequence_number)
  );
`;

function toSqlBoolean(value: boolean): number {
  return value ? 1 : 0;
}

function asPendingSession(row: PendingTripLocationSession): PendingTripLocationSession {
  return {
    recording_session_id: row.recording_session_id,
    ride_id: row.ride_id,
    opened_at: row.opened_at,
    closed_at: row.closed_at,
  };
}

function asPoint(row: TripLocationOutboxRow): TripLocationPoint {
  return {
    ride_id: row.ride_id,
    recording_session_id: row.recording_session_id,
    sequence_number: row.sequence_number,
    captured_at: row.captured_at,
    monotonic_ms: row.monotonic_ms,
    lat: row.lat,
    lng: row.lng,
    accuracy: row.accuracy,
    speed: row.speed,
    heading: row.heading,
    altitude: row.altitude,
    source: row.source,
    mocked: Boolean(row.mocked),
    is_completion_fix: Boolean(row.is_completion_fix),
  };
}

function assertFiniteLocationFix(fix: TripLocationFix): void {
  const values = [fix.monotonic_ms, fix.lat, fix.lng];
  if (!fix.ride_id || !fix.captured_at || values.some((value) => !Number.isFinite(value))) {
    throw new Error('A trip location fix requires a ride, sensor capture time, and finite coordinates.');
  }
  if (fix.monotonic_ms < 0 || fix.lat < -90 || fix.lat > 90 || fix.lng < -180 || fix.lng > 180) {
    throw new Error('A trip location fix contains invalid sensor coordinates.');
  }
}

export class TripLocationOutbox {
  private readonly openDatabase: () => Promise<TripLocationOutboxDatabase>;
  private readonly randomUUID: () => string;
  private readonly now: () => string;
  private databasePromise: Promise<TripLocationOutboxDatabase> | null = null;

  constructor(options: TripLocationOutboxOptions = {}) {
    // TODO(ios-backup): expo-sqlite / expo-file-system (SDK 55: 55.0.18 /
    // 55.0.22) expose no API to set NSURLIsExcludedFromBackupKey, so on iOS
    // this raw-GPS database (and its -wal/-shm siblings) is still included in
    // iCloud/iTunes device backups. Android backup is disabled app-wide via
    // android.allowBackup=false in app.config.ts. Residual risk is iOS-only;
    // revisit when Expo ships a backup-exclusion API (new FileSystem
    // File/Directory surface) or add a tiny config plugin/native module that
    // flags the file after first open.
    this.openDatabase = options.openDatabase ?? (async () => {
      // Lazy require keeps native module resolution out of module load.
      const sqlite: typeof SQLite = require('expo-sqlite');
      return sqlite.openDatabaseAsync(DATABASE_NAME);
    });
    this.randomUUID = options.randomUUID ?? (() => {
      const crypto: typeof import('expo-crypto') = require('expo-crypto');
      return crypto.randomUUID();
    });
    this.now = options.now ?? (() => new Date().toISOString());
  }

  async startSession(rideId: string): Promise<PendingTripLocationSession> {
    if (!rideId) throw new Error('A ride id is required to start trip location recording.');

    const database = await this.getDatabase();
    let session: PendingTripLocationSession | null = null;
    await database.withExclusiveTransactionAsync(async (transaction) => {
      session = await this.openOrCreateSession(transaction, rideId);
    });
    return session!;
  }

  async enqueue(fix: TripLocationFix): Promise<TripLocationPoint> {
    assertFiniteLocationFix(fix);

    const database = await this.getDatabase();
    let point: TripLocationPoint | null = null;
    await database.withExclusiveTransactionAsync(async (transaction) => {
      const session = await this.openOrCreateSession(transaction, fix.ride_id);
      const sequenceState = await transaction.getFirstAsync<{ next_sequence_number: number }>(
        'SELECT next_sequence_number FROM trip_location_sessions WHERE session_id = ?',
        [session.recording_session_id],
      );
      if (!sequenceState) throw new Error('The trip location recording session disappeared during allocation.');

      const sequenceNumber = sequenceState.next_sequence_number;
      await transaction.runAsync(
        'UPDATE trip_location_sessions SET next_sequence_number = ? WHERE session_id = ?',
        [sequenceNumber + 1, session.recording_session_id],
      );

      point = {
        ...fix,
        recording_session_id: session.recording_session_id,
        sequence_number: sequenceNumber,
      };
      await transaction.runAsync(
        `INSERT INTO trip_location_outbox (
          ride_id, session_id, sequence_number, captured_at, monotonic_ms,
          lat, lng, accuracy, speed, heading, altitude, source, mocked,
          is_completion_fix, enqueued_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        [
          point.ride_id,
          point.recording_session_id,
          point.sequence_number,
          point.captured_at,
          point.monotonic_ms,
          point.lat,
          point.lng,
          point.accuracy,
          point.speed,
          point.heading,
          point.altitude,
          point.source,
          toSqlBoolean(point.mocked),
          toSqlBoolean(point.is_completion_fix),
          this.now(),
        ],
      );
    });
    return point!;
  }

  async listPendingSessions(): Promise<PendingTripLocationSession[]> {
    const database = await this.getDatabase();
    const rows = await database.getAllAsync<PendingTripLocationSession>(
      `SELECT
        sessions.session_id AS recording_session_id,
        sessions.ride_id,
        sessions.opened_at,
        sessions.closed_at
      FROM trip_location_sessions AS sessions
      WHERE EXISTS (
        SELECT 1 FROM trip_location_outbox AS outbox
        WHERE outbox.session_id = sessions.session_id
      )
      ORDER BY sessions.opened_at ASC`,
    );
    return rows.map(asPendingSession);
  }

  async peek(recordingSessionId: string, limit = MAX_UPLOAD_BATCH_SIZE): Promise<TripLocationPoint[]> {
    const database = await this.getDatabase();
    const boundedLimit = Math.min(Math.max(Math.floor(limit), 1), MAX_UPLOAD_BATCH_SIZE);
    const rows = await database.getAllAsync<TripLocationOutboxRow>(
      `SELECT
        ride_id,
        session_id AS recording_session_id,
        sequence_number,
        captured_at,
        monotonic_ms,
        lat,
        lng,
        accuracy,
        speed,
        heading,
        altitude,
        source,
        mocked,
        is_completion_fix,
        enqueued_at
      FROM trip_location_outbox
      WHERE session_id = ?
      ORDER BY sequence_number ASC
      LIMIT ?`,
      [recordingSessionId, boundedLimit],
    );
    return rows.map(asPoint);
  }

  async acknowledge(
    recordingSessionId: string,
    acknowledgedThrough: number,
    submitted: TripLocationSubmittedRange,
    rejected: TripLocationRejection[] = [],
  ): Promise<void> {
    if (!Number.isInteger(acknowledgedThrough) || acknowledgedThrough < 0) {
      throw new Error('Trip location acknowledgements require a non-negative sequence number.');
    }
    if (
      !Number.isInteger(submitted.from) || !Number.isInteger(submitted.to)
      || submitted.from < 0 || submitted.to < submitted.from
    ) {
      throw new Error('Trip location acknowledgements require the submitted sequence range.');
    }
    if (rejected.some(({ sequence_number }) => !Number.isInteger(sequence_number) || sequence_number < 0)) {
      throw new Error('Trip location rejection sequence numbers must be non-negative integers.');
    }

    const database = await this.getDatabase();
    await database.withExclusiveTransactionAsync(async (transaction) => {
      for (const rejection of rejected) {
        await transaction.runAsync(
          `INSERT INTO trip_location_quarantine (
            session_id, sequence_number, ride_id, captured_at, monotonic_ms,
            lat, lng, accuracy, speed, heading, altitude, source, mocked,
            is_completion_fix, rejection_reason, quarantined_at
          )
          SELECT
            session_id, sequence_number, ride_id, captured_at, monotonic_ms,
            lat, lng, accuracy, speed, heading, altitude, source, mocked,
            is_completion_fix, ?, ?
          FROM trip_location_outbox
          WHERE session_id = ? AND sequence_number = ?
          ON CONFLICT(session_id, sequence_number) DO NOTHING`,
          [rejection.reason, this.now(), recordingSessionId, rejection.sequence_number],
        );
      }
      // Delete only what was actually submitted in the acknowledged batch and
      // is covered by acked_through — never the whole prefix. A completion-fix
      // ack (single high sequence) must leave the queued tail below it intact
      // for the next flush; a flush ack (contiguous prefix batch) deletes the
      // exact same rows it always did.
      await transaction.runAsync(
        'DELETE FROM trip_location_outbox WHERE session_id = ? AND sequence_number >= ? AND sequence_number <= ?',
        [recordingSessionId, submitted.from, Math.min(acknowledgedThrough, submitted.to)],
      );
    });
  }

  /**
   * Permanently dispose of a session the server has terminally rejected
   * (e.g. 409 ride-not-active, 422 outside the retention window): copy every
   * still-pending point into the quarantine table with the given reason, then
   * remove them from the upload queue so the session can no longer poison the
   * head of the flush loop.
   */
  async quarantineSession(recordingSessionId: string, reason: string): Promise<void> {
    if (!recordingSessionId || !reason) {
      throw new Error('Quarantining a trip location session requires a session id and a reason.');
    }

    const database = await this.getDatabase();
    await database.withExclusiveTransactionAsync(async (transaction) => {
      await transaction.runAsync(
        `INSERT INTO trip_location_quarantine (
          session_id, sequence_number, ride_id, captured_at, monotonic_ms,
          lat, lng, accuracy, speed, heading, altitude, source, mocked,
          is_completion_fix, rejection_reason, quarantined_at
        )
        SELECT
          session_id, sequence_number, ride_id, captured_at, monotonic_ms,
          lat, lng, accuracy, speed, heading, altitude, source, mocked,
          is_completion_fix, ?, ?
        FROM trip_location_outbox
        WHERE session_id = ?
        ON CONFLICT(session_id, sequence_number) DO NOTHING`,
        [reason, this.now(), recordingSessionId],
      );
      await transaction.runAsync(
        'DELETE FROM trip_location_outbox WHERE session_id = ?',
        [recordingSessionId],
      );
    });
  }

  async pendingCount(rideId: string): Promise<number> {
    const database = await this.getDatabase();
    const result = await database.getFirstAsync<{ count: number }>(
      'SELECT COUNT(*) AS count FROM trip_location_outbox WHERE ride_id = ?',
      [rideId],
    );
    return result?.count ?? 0;
  }

  async closeSession(rideId: string): Promise<void> {
    const database = await this.getDatabase();
    await database.runAsync(
      'UPDATE trip_location_sessions SET closed_at = ? WHERE ride_id = ? AND closed_at IS NULL',
      [this.now(), rideId],
    );
  }

  private async getDatabase(): Promise<TripLocationOutboxDatabase> {
    if (!this.databasePromise) {
      this.databasePromise = this.openDatabase().then(async (database) => {
        await database.execAsync(SCHEMA);
        return database;
      });
    }
    return this.databasePromise;
  }

  private async openOrCreateSession(
    transaction: SqlExecutor,
    rideId: string,
  ): Promise<PendingTripLocationSession> {
    const current = await transaction.getFirstAsync<PendingTripLocationSession>(
      `SELECT
        session_id AS recording_session_id,
        ride_id,
        opened_at,
        closed_at
      FROM trip_location_sessions
      WHERE ride_id = ? AND closed_at IS NULL
      LIMIT 1`,
      [rideId],
    );
    if (current) return asPendingSession(current);

    const session: PendingTripLocationSession = {
      recording_session_id: this.randomUUID(),
      ride_id: rideId,
      opened_at: this.now(),
      closed_at: null,
    };
    await transaction.runAsync(
      `INSERT INTO trip_location_sessions (
        session_id, ride_id, opened_at, closed_at, next_sequence_number
      ) VALUES (?, ?, ?, NULL, 0)`,
      [session.recording_session_id, session.ride_id, session.opened_at],
    );
    return session;
  }
}

export function createTripLocationOutbox(options: TripLocationOutboxOptions = {}): TripLocationOutbox {
  return new TripLocationOutbox(options);
}

export const tripLocationOutbox = createTripLocationOutbox();
