import * as Crypto from 'expo-crypto';
import * as SQLite from 'expo-sqlite';

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
  /** Test-only overrides for the retention caps (defaults: 5k / 50k). */
  retention?: { quarantineMaxRows?: number; outboxSoftCapRows?: number };
}

const DATABASE_NAME = 'spinr-trip-location-outbox.db';
const MAX_UPLOAD_BATCH_SIZE = 500;

// ── Retention bounds (the prune pass) ────────────────────────────────────────
// The outbox had NO age or size bound: a long outage, a poisoned session, or a
// driver who never signs out grew the SQLite file without limit, and the
// quarantine table was write-only. Bounds, applied oldest-first:
//   - quarantine rows age out at 14 days and cap at 5k (quarantine is the
//     preservation store for signout/expired/evicted points — never uploaded);
//   - sessions CLOSED >7 days ago are expired (their leftover points move to
//     quarantine first, reason 'expired_unflushed');
//   - the active outbox soft-caps at 50k points by evicting oldest rows of
//     CLOSED sessions only ('evicted_capacity') — an OPEN session's points are
//     never evicted, so the cap is deliberately unenforced if only live trip
//     data remains (losing 3-week-old closed-session points beats losing the
//     current trip's).
const QUARANTINE_TTL_MS = 14 * 24 * 60 * 60 * 1000;
const QUARANTINE_MAX_ROWS = 5_000;
const CLOSED_SESSION_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const OUTBOX_SOFT_CAP_ROWS = 50_000;
const PRUNE_THROTTLE_MS = 60 * 60 * 1000;

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
  // Wall-clock throttle for the retention prune (not the injectable `now`,
  // which tests pin to fixed ISO strings — a throttle wants real elapsed time).
  private lastPruneAt = 0;
  private readonly quarantineMaxRows: number;
  private readonly outboxSoftCapRows: number;

  constructor(options: TripLocationOutboxOptions = {}) {
    this.openDatabase = options.openDatabase ?? (async () => SQLite.openDatabaseAsync(DATABASE_NAME));
    this.randomUUID = options.randomUUID ?? Crypto.randomUUID;
    this.now = options.now ?? (() => new Date().toISOString());
    this.quarantineMaxRows = options.retention?.quarantineMaxRows ?? QUARANTINE_MAX_ROWS;
    this.outboxSoftCapRows = options.retention?.outboxSoftCapRows ?? OUTBOX_SOFT_CAP_ROWS;
  }

  async startSession(rideId: string): Promise<PendingTripLocationSession> {
    if (!rideId) throw new Error('A ride id is required to start trip location recording.');

    const database = await this.getDatabase();
    let session: PendingTripLocationSession | null = null;
    await database.withExclusiveTransactionAsync(async (transaction) => {
      session = await this.openOrCreateSession(transaction, rideId);
    });
    // Retention housekeeping rides on ride-start (~once per trip) and on
    // acknowledge; fire-and-forget so it can never delay a session open.
    void this.maybePrune();
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
    rejected: TripLocationRejection[] = [],
  ): Promise<void> {
    if (!Number.isInteger(acknowledgedThrough) || acknowledgedThrough < 0) {
      throw new Error('Trip location acknowledgements require a non-negative sequence number.');
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
      await transaction.runAsync(
        'DELETE FROM trip_location_outbox WHERE session_id = ? AND sequence_number <= ?',
        [recordingSessionId, acknowledgedThrough],
      );
    });
    void this.maybePrune();
  }

  async latestPoint(rideId: string): Promise<TripLocationPoint | null> {
    const database = await this.getDatabase();
    const row = await database.getFirstAsync<TripLocationOutboxRow>(
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
      WHERE ride_id = ?
      ORDER BY captured_at DESC, sequence_number DESC
      LIMIT 1`,
      [rideId],
    );
    return row ? asPoint(row) : null;
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

  /**
   * Sign-out purge: PRESERVE unacknowledged points into quarantine, then clear
   * the active outbox and sessions.
   *
   * This reverses the 2026-07-29 deliberate-discard decision (which dropped a
   * mid-trip sign-out's GPS tail forever — an SGI/billing evidence loss the
   * owner has ruled out: location evidence must never be silently destroyed).
   * The privacy properties that motivated the old behaviour are kept by
   * construction instead of by deletion:
   *   - quarantine rows are NEVER uploaded — no flush path reads the table, so
   *     the next sign-in on a shared device cannot post another driver's
   *     points under its own session;
   *   - no network is needed here (authStore wipes tokens BEFORE logout
   *     callbacks run, so an upload could not authenticate anyway);
   *   - retention is bounded: the prune pass TTLs quarantine at 14 days and
   *     caps it at 5k rows, so the coordinates age off the device.
   *
   * Runs in one exclusive transaction so a concurrent enqueue cannot interleave
   * and leave orphan points behind a deleted session row.
   */
  async purgeAll(): Promise<void> {
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
          is_completion_fix, 'signout_unflushed', ?
        FROM trip_location_outbox
        ON CONFLICT(session_id, sequence_number) DO NOTHING`,
        [this.now()],
      );
      await transaction.runAsync('DELETE FROM trip_location_outbox');
      await transaction.runAsync('DELETE FROM trip_location_sessions');
    });
  }

  /** Throttled prune — at most once per hour per outbox instance. */
  async maybePrune(): Promise<void> {
    const nowMs = Date.now();
    if (nowMs - this.lastPruneAt < PRUNE_THROTTLE_MS) return;
    this.lastPruneAt = nowMs;
    await this.prune();
  }

  /**
   * Apply the retention bounds (see the constants block). Counts are read
   * before the delete transaction — the caps are soft, so a concurrent
   * enqueue racing the count only defers enforcement to the next prune.
   * Never throws: retention is best-effort housekeeping.
   */
  async prune(): Promise<void> {
    try {
      const database = await this.getDatabase();
      const nowMs = Date.parse(this.now());
      const quarantineCutoff = new Date(nowMs - QUARANTINE_TTL_MS).toISOString();
      const sessionCutoff = new Date(nowMs - CLOSED_SESSION_TTL_MS).toISOString();
      const quarantinedAt = this.now();

      const quarantineTotal =
        (await database.getFirstAsync<{ total: number }>(
          'SELECT COUNT(*) AS total FROM trip_location_quarantine',
        ))?.total ?? 0;
      const quarantineExcess = Math.max(0, quarantineTotal - this.quarantineMaxRows);
      const outboxTotal =
        (await database.getFirstAsync<{ total: number }>(
          'SELECT COUNT(*) AS total FROM trip_location_outbox',
        ))?.total ?? 0;
      const outboxExcess = Math.max(0, outboxTotal - this.outboxSoftCapRows);

      await database.withExclusiveTransactionAsync(async (transaction) => {
        await transaction.runAsync(
          'DELETE FROM trip_location_quarantine WHERE quarantined_at < ?',
          [quarantineCutoff],
        );
        if (quarantineExcess > 0) {
          await transaction.runAsync(
            `DELETE FROM trip_location_quarantine WHERE rowid IN (
              SELECT rowid FROM trip_location_quarantine
              ORDER BY quarantined_at ASC LIMIT ?)`,
            [quarantineExcess],
          );
        }
        // Expired closed sessions: preserve their leftover points first.
        await transaction.runAsync(
          `INSERT INTO trip_location_quarantine (
            session_id, sequence_number, ride_id, captured_at, monotonic_ms,
            lat, lng, accuracy, speed, heading, altitude, source, mocked,
            is_completion_fix, rejection_reason, quarantined_at
          )
          SELECT
            o.session_id, o.sequence_number, o.ride_id, o.captured_at, o.monotonic_ms,
            o.lat, o.lng, o.accuracy, o.speed, o.heading, o.altitude, o.source, o.mocked,
            o.is_completion_fix, 'expired_unflushed', ?
          FROM trip_location_outbox o
          JOIN trip_location_sessions s ON s.session_id = o.session_id
          WHERE s.closed_at IS NOT NULL AND s.closed_at < ?
          ON CONFLICT(session_id, sequence_number) DO NOTHING`,
          [quarantinedAt, sessionCutoff],
        );
        await transaction.runAsync(
          `DELETE FROM trip_location_outbox WHERE session_id IN (
            SELECT session_id FROM trip_location_sessions
            WHERE closed_at IS NOT NULL AND closed_at < ?)`,
          [sessionCutoff],
        );
        await transaction.runAsync(
          'DELETE FROM trip_location_sessions WHERE closed_at IS NOT NULL AND closed_at < ?',
          [sessionCutoff],
        );
        if (outboxExcess > 0) {
          await transaction.runAsync(
            `INSERT INTO trip_location_quarantine (
              session_id, sequence_number, ride_id, captured_at, monotonic_ms,
              lat, lng, accuracy, speed, heading, altitude, source, mocked,
              is_completion_fix, rejection_reason, quarantined_at
            )
            SELECT
              o.session_id, o.sequence_number, o.ride_id, o.captured_at, o.monotonic_ms,
              o.lat, o.lng, o.accuracy, o.speed, o.heading, o.altitude, o.source, o.mocked,
              o.is_completion_fix, 'evicted_capacity', ?
            FROM trip_location_outbox o
            JOIN trip_location_sessions s ON s.session_id = o.session_id
            WHERE s.closed_at IS NOT NULL
            ORDER BY o.enqueued_at ASC LIMIT ?
            ON CONFLICT(session_id, sequence_number) DO NOTHING`,
            [quarantinedAt, outboxExcess],
          );
          await transaction.runAsync(
            `DELETE FROM trip_location_outbox WHERE rowid IN (
              SELECT o.rowid FROM trip_location_outbox o
              JOIN trip_location_sessions s ON s.session_id = o.session_id
              WHERE s.closed_at IS NOT NULL
              ORDER BY o.enqueued_at ASC LIMIT ?)`,
            [outboxExcess],
          );
        }
      });
    } catch {
      // Housekeeping only — never let retention break capture or ack paths.
    }
  }

  private async getDatabase(): Promise<TripLocationOutboxDatabase> {
    if (!this.databasePromise) {
      const attempt = this.openDatabase().then(async (database) => {
        await database.execAsync(SCHEMA);
        return database;
      });
      this.databasePromise = attempt;
      attempt.catch(() => {
        // Clear the cached rejection so the next call retries instead of
        // returning a permanently dead promise for the rest of the session.
        if (this.databasePromise === attempt) {
          this.databasePromise = null;
        }
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
