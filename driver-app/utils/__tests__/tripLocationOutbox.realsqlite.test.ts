/**
 * Real-SQLite coverage for the trip location outbox.
 *
 * WHY THIS FILE EXISTS, separately from tripLocationOutbox.test.ts:
 * that suite mocks `expo-sqlite` with an in-memory JS class that pattern-matches
 * SQL strings and never parses them. It is good at asserting behaviour, but it
 * cannot catch a statement that is not valid SQLite — and one shipped:
 * `purgeAll()` attached `ON CONFLICT ... DO NOTHING` to an `INSERT..SELECT`
 * with no `WHERE`, which SQLite rejects with `near "DO": syntax error`. The
 * mocked test passed on every run while sign-out threw on real devices
 * (Sentry, both iOS and Android, driver build 2.0.0+29).
 *
 * So this file injects a REAL SQLite engine (`node:sqlite`, built into Node 22+,
 * no new dependency) through the outbox's own `openDatabase` option. Every
 * statement the module issues is really parsed and really executed. Any future
 * statement that is not valid SQLite fails here.
 */
import { DatabaseSync } from 'node:sqlite';

import { createTripLocationOutbox, type TripLocationFix } from '../tripLocationOutbox';

// These two stubs exist only to stop the module's top-level
// `import * as SQLite from 'expo-sqlite'` / `expo-crypto` from loading Expo's
// native runtime inside Jest (it lazily requires a file outside the test scope
// on first global `fetch` access, which fails the whole suite).
//
// They do NOT weaken this file: both of the real module's uses are injected
// below via `openDatabase` (the real node:sqlite engine) and `randomUUID`, so
// neither stub is ever called. The SQL still runs against a real engine.
jest.mock('expo-sqlite', () => ({ openDatabaseAsync: jest.fn() }));
jest.mock('expo-crypto', () => ({ randomUUID: jest.fn() }));

type Params = readonly unknown[];
/** What node:sqlite will actually accept as a bound parameter. */
type Bound = null | number | bigint | string | Uint8Array;

/**
 * Minimal faithful adapter from expo-sqlite's async surface onto node:sqlite's
 * synchronous one. Only the members the outbox actually uses are implemented:
 * runAsync / getFirstAsync / getAllAsync / execAsync /
 * withExclusiveTransactionAsync.
 */
class RealSqliteDatabase {
  private readonly db = new DatabaseSync(':memory:');

  /** node:sqlite only binds null/number/bigint/string/Uint8Array. */
  private static bind(params: Params): Bound[] {
    return params.map((value): Bound => {
      if (value === undefined || value === null) return null;
      if (typeof value === 'boolean') return value ? 1 : 0;
      if (typeof value === 'number' || typeof value === 'bigint' || typeof value === 'string') return value;
      if (value instanceof Uint8Array) return value;
      // The outbox binds only the scalars above; anything else is a real bug in
      // the statement under test, so fail loudly rather than coercing.
      throw new TypeError(`unbindable SQLite parameter: ${Object.prototype.toString.call(value)}`);
    });
  }

  async execAsync(sql: string): Promise<void> {
    this.db.exec(sql);
  }

  async runAsync(sql: string, params: Params = []) {
    const result = this.db.prepare(sql).run(...RealSqliteDatabase.bind(params));
    return { changes: Number(result.changes), lastInsertRowId: Number(result.lastInsertRowid) };
  }

  async getFirstAsync<T>(sql: string, params: Params = []): Promise<T | null> {
    // expo-sqlite yields null for "no row"; node:sqlite yields undefined.
    return (this.db.prepare(sql).get(...RealSqliteDatabase.bind(params)) as T) ?? null;
  }

  async getAllAsync<T>(sql: string, params: Params = []): Promise<T[]> {
    return this.db.prepare(sql).all(...RealSqliteDatabase.bind(params)) as T[];
  }

  async withExclusiveTransactionAsync(task: (txn: this) => Promise<void>): Promise<void> {
    this.db.exec('BEGIN IMMEDIATE');
    try {
      await task(this);
      this.db.exec('COMMIT');
    } catch (error) {
      this.db.exec('ROLLBACK');
      throw error;
    }
  }
}

const fix = (overrides: Partial<TripLocationFix> = {}): TripLocationFix => ({
  ride_id: 'ride-1',
  captured_at: '2026-09-13T01:48:30.000Z',
  monotonic_ms: 1_000,
  lat: 52.1332,
  lng: -106.6700,
  accuracy: 5,
  speed: 12.5,
  heading: 90,
  altitude: 480,
  source: 'foreground',
  mocked: false,
  is_completion_fix: false,
  ...overrides,
});

const makeOutbox = () => {
  const database = new RealSqliteDatabase();
  let uuid = 0;
  const outbox = createTripLocationOutbox({
    openDatabase: async () => database as never,
    randomUUID: () => `session-${++uuid}`,
    now: () => '2026-09-13T02:00:00.000Z',
  });
  return { outbox, database };
};

describe('tripLocationOutbox against real SQLite', () => {
  it('applies its schema to a real engine', async () => {
    const { outbox } = makeOutbox();
    // startSession is the first call that runs SCHEMA through execAsync.
    await expect(outbox.startSession('ride-1')).resolves.toEqual(
      expect.objectContaining({ ride_id: 'ride-1' }),
    );
  });

  it('enqueues and reads back a point', async () => {
    const { outbox } = makeOutbox();
    await outbox.startSession('ride-1');
    await outbox.enqueue(fix());

    expect(await outbox.pendingCount('ride-1')).toBe(1);
    expect(await outbox.latestPoint('ride-1')).toEqual(
      expect.objectContaining({ ride_id: 'ride-1', lat: 52.1332 }),
    );
  });

  /**
   * THE REGRESSION TEST. Before the fix this threw
   * `near "DO": syntax error` on a real engine while the mocked suite passed.
   */
  it('purgeAll() parses and runs on a real engine, preserving points to quarantine', async () => {
    const { outbox, database } = makeOutbox();
    await outbox.startSession('ride-1');
    await outbox.enqueue(fix());
    expect(await outbox.pendingCount('ride-1')).toBe(1);

    await expect(outbox.purgeAll()).resolves.toBeUndefined();

    // Outbox and sessions are cleared...
    expect(await outbox.pendingCount('ride-1')).toBe(0);
    expect(await outbox.listPendingSessions()).toEqual([]);
    // ...and the unflushed point was preserved, not silently dropped.
    const quarantined = await database.getAllAsync<{ rejection_reason: string }>(
      'SELECT rejection_reason FROM trip_location_quarantine',
    );
    expect(quarantined).toEqual([expect.objectContaining({ rejection_reason: 'signout_unflushed' })]);
  });

  it('purgeAll() is idempotent on an empty outbox', async () => {
    const { outbox } = makeOutbox();
    await outbox.startSession('ride-1');
    await expect(outbox.purgeAll()).resolves.toBeUndefined();
    await expect(outbox.purgeAll()).resolves.toBeUndefined();
  });

  /**
   * Exercises the other three `ON CONFLICT` quarantine statements
   * (flushRejected / expired_unflushed / evicted_capacity) so none of them can
   * regress into the same unparseable shape.
   */
  it('acknowledge() with rejections parses and quarantines on a real engine', async () => {
    const { outbox, database } = makeOutbox();
    const session = await outbox.startSession('ride-1');
    await outbox.enqueue(fix());
    const [point] = await outbox.peek(session.recording_session_id);

    await outbox.acknowledge(session.recording_session_id, point.sequence_number, [
      { sequence_number: point.sequence_number, reason: 'invalid_fix' },
    ]);

    const quarantined = await database.getAllAsync<{ rejection_reason: string }>(
      'SELECT rejection_reason FROM trip_location_quarantine',
    );
    expect(quarantined).toEqual([expect.objectContaining({ rejection_reason: 'invalid_fix' })]);
  });

  it('prune() parses and runs on a real engine', async () => {
    const { outbox } = makeOutbox();
    await outbox.startSession('ride-1');
    await expect(outbox.prune()).resolves.toBeUndefined();
  });
});
