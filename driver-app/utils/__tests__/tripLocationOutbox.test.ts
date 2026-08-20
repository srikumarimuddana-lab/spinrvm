import {
  createTripLocationOutbox,
  type TripLocationFix,
} from '../tripLocationOutbox';

jest.mock('expo-crypto', () => ({
  randomUUID: jest.fn(),
}));

jest.mock('expo-sqlite', () => ({
  openDatabaseAsync: jest.fn(),
}));

type SessionRow = {
  session_id: string;
  ride_id: string;
  opened_at: string;
  closed_at: string | null;
  next_sequence_number: number;
};

type OutboxRow = TripLocationFix & {
  recording_session_id: string;
  sequence_number: number;
  enqueued_at: string;
};

class MemorySqliteDatabase {
  readonly sessions = new Map<string, SessionRow>();
  readonly outbox = new Map<string, OutboxRow>();
  readonly quarantine = new Map<string, OutboxRow & { rejection_reason: string; quarantined_at?: string }>();
  failNextOutboxInsert = false;
  private transactionTail = Promise.resolve();

  async execAsync(): Promise<void> {}

  async withExclusiveTransactionAsync(task: (txn: this) => Promise<void>): Promise<void> {
    const previous = this.transactionTail;
    let release!: () => void;
    this.transactionTail = new Promise<void>((resolve) => { release = resolve; });
    await previous;
    try {
      await task(this);
    } finally {
      release();
    }
  }

  async getFirstAsync<T>(sql: string, values?: unknown[] | unknown): Promise<T | null> {
    const params = Array.isArray(values) ? values : values === undefined ? [] : [values];
    if (sql.includes('FROM trip_location_sessions') && sql.includes('ride_id = ?') && sql.includes('closed_at IS NULL')) {
      const session = [...this.sessions.values()].find((candidate) => candidate.ride_id === params[0] && candidate.closed_at === null);
      return (session ? {
        recording_session_id: session.session_id,
        ride_id: session.ride_id,
        opened_at: session.opened_at,
        closed_at: session.closed_at,
      } : null) as T | null;
    }
    if (sql.includes('SELECT next_sequence_number')) {
      return (this.sessions.get(String(params[0])) ?? null) as T | null;
    }
    if (sql.includes('COUNT(*) AS count')) {
      return { count: [...this.outbox.values()].filter((point) => point.ride_id === params[0]).length } as T;
    }
    if (sql.includes('AS total FROM trip_location_quarantine')) {
      return { total: this.quarantine.size } as T;
    }
    if (sql.includes('AS total FROM trip_location_outbox')) {
      return { total: this.outbox.size } as T;
    }
    return null;
  }

  async getAllAsync<T>(sql: string, values?: unknown[] | unknown): Promise<T[]> {
    const params = Array.isArray(values) ? values : values === undefined ? [] : [values];
    if (sql.includes('FROM trip_location_sessions AS sessions')) {
      return [...this.sessions.values()]
        .filter((session) => [...this.outbox.values()].some((point) => point.recording_session_id === session.session_id))
        .sort((left, right) => left.opened_at.localeCompare(right.opened_at))
        .map((session) => ({
          recording_session_id: session.session_id,
          ride_id: session.ride_id,
          opened_at: session.opened_at,
          closed_at: session.closed_at,
        })) as T[];
    }
    if (sql.includes('FROM trip_location_outbox')) {
      return [...this.outbox.values()]
        .filter((point) => point.recording_session_id === params[0])
        .sort((left, right) => left.sequence_number - right.sequence_number)
        .slice(0, Number(params[1])) as T[];
    }
    return [];
  }

  async runAsync(sql: string, values?: unknown[] | unknown): Promise<void> {
    const params = Array.isArray(values) ? values : values === undefined ? [] : [values];
    if (sql.includes('INSERT INTO trip_location_sessions')) {
      const [sessionId, rideId, openedAt] = params as [string, string, string];
      this.sessions.set(sessionId, {
        session_id: sessionId,
        ride_id: rideId,
        opened_at: openedAt,
        closed_at: null,
        next_sequence_number: 0,
      });
      return;
    }
    if (sql.includes('UPDATE trip_location_sessions') && sql.includes('next_sequence_number')) {
      const [nextSequence, sessionId] = params as [number, string];
      const session = this.sessions.get(sessionId);
      if (session) session.next_sequence_number = nextSequence;
      return;
    }
    if (sql.includes('INSERT INTO trip_location_outbox')) {
      if (this.failNextOutboxInsert) {
        this.failNextOutboxInsert = false;
        throw new Error('disk full');
      }
      const [
        ride_id, recording_session_id, sequence_number, captured_at, monotonic_ms,
        lat, lng, accuracy, speed, heading, altitude, source, mocked,
        is_completion_fix, enqueued_at,
      ] = params as [string, string, number, string, number, number, number, number | null, number | null, number | null, number | null, string, number, number, string];
      this.outbox.set(`${recording_session_id}:${sequence_number}`, {
        ride_id,
        recording_session_id,
        sequence_number,
        captured_at,
        monotonic_ms,
        lat,
        lng,
        accuracy,
        speed,
        heading,
        altitude,
        source: source as TripLocationFix['source'],
        mocked: Boolean(mocked),
        is_completion_fix: Boolean(is_completion_fix),
        enqueued_at,
      });
      return;
    }
    if (sql.includes('INSERT INTO trip_location_quarantine')) {
      const copy = (key: string, point: OutboxRow, reason: string, quarantinedAt: string) => {
        if (!this.quarantine.has(key)) {
          this.quarantine.set(key, { ...point, rejection_reason: reason, quarantined_at: quarantinedAt });
        }
      };
      // Bulk forms carry their reason inline in the SQL; per-row (acknowledge
      // rejections) passes it as the first param.
      if (sql.includes("'signout_unflushed'")) {
        const [quarantinedAt] = params as [string];
        for (const [key, point] of this.outbox) copy(key, point, 'signout_unflushed', quarantinedAt);
        return;
      }
      if (sql.includes("'expired_unflushed'")) {
        const [quarantinedAt, cutoff] = params as [string, string];
        for (const [key, point] of this.outbox) {
          const session = this.sessions.get(point.recording_session_id);
          if (session?.closed_at && session.closed_at < cutoff) copy(key, point, 'expired_unflushed', quarantinedAt);
        }
        return;
      }
      if (sql.includes("'evicted_capacity'")) {
        const [quarantinedAt, limit] = params as [string, number];
        const closedRows = [...this.outbox.entries()]
          .filter(([, p]) => this.sessions.get(p.recording_session_id)?.closed_at)
          .sort(([, a], [, b]) => a.enqueued_at.localeCompare(b.enqueued_at))
          .slice(0, Number(limit));
        for (const [key, point] of closedRows) copy(key, point, 'evicted_capacity', quarantinedAt);
        return;
      }
      const [rejectionReason, quarantinedAt, sessionId, sequence] = params as [string, string, string, number];
      const point = this.outbox.get(`${sessionId}:${sequence}`);
      if (point) copy(`${sessionId}:${sequence}`, point, rejectionReason, quarantinedAt);
      return;
    }
    // Unqualified purge deletes (sign-out). Checked before the acknowledged-
    // through branch below so a bare DELETE can't be mistaken for a partial one.
    if (sql.trim() === 'DELETE FROM trip_location_outbox') {
      this.outbox.clear();
      return;
    }
    if (sql.trim() === 'DELETE FROM trip_location_quarantine') {
      this.quarantine.clear();
      return;
    }
    if (sql.trim() === 'DELETE FROM trip_location_sessions') {
      this.sessions.clear();
      return;
    }
    // ── Retention prune statements ──
    if (sql.includes('DELETE FROM trip_location_quarantine WHERE quarantined_at <')) {
      const [cutoff] = params as [string];
      for (const [key, row] of this.quarantine) {
        if ((row.quarantined_at ?? '') < cutoff) this.quarantine.delete(key);
      }
      return;
    }
    if (sql.includes('DELETE FROM trip_location_quarantine WHERE rowid IN')) {
      const [limit] = params as [number];
      [...this.quarantine.entries()]
        .sort(([, a], [, b]) => (a.quarantined_at ?? '').localeCompare(b.quarantined_at ?? ''))
        .slice(0, Number(limit))
        .forEach(([key]) => this.quarantine.delete(key));
      return;
    }
    if (sql.includes('DELETE FROM trip_location_outbox WHERE session_id IN')) {
      const [cutoff] = params as [string];
      for (const [key, point] of this.outbox) {
        const session = this.sessions.get(point.recording_session_id);
        if (session?.closed_at && session.closed_at < cutoff) this.outbox.delete(key);
      }
      return;
    }
    if (sql.includes('DELETE FROM trip_location_sessions WHERE closed_at IS NOT NULL AND closed_at <')) {
      const [cutoff] = params as [string];
      for (const [key, session] of this.sessions) {
        if (session.closed_at && session.closed_at < cutoff) this.sessions.delete(key);
      }
      return;
    }
    if (sql.includes('DELETE FROM trip_location_outbox WHERE rowid IN')) {
      const [limit] = params as [number];
      [...this.outbox.entries()]
        .filter(([, p]) => this.sessions.get(p.recording_session_id)?.closed_at)
        .sort(([, a], [, b]) => a.enqueued_at.localeCompare(b.enqueued_at))
        .slice(0, Number(limit))
        .forEach(([key]) => this.outbox.delete(key));
      return;
    }
    if (sql.includes('DELETE FROM trip_location_outbox') && sql.includes('sequence_number <= ?')) {
      const [sessionId, acknowledgedThrough] = params as [string, number];
      for (const [key, point] of this.outbox) {
        if (point.recording_session_id === sessionId && point.sequence_number <= acknowledgedThrough) this.outbox.delete(key);
      }
      return;
    }
    if (sql.includes('UPDATE trip_location_sessions') && sql.includes('closed_at = ?')) {
      const [closedAt, rideId] = params as [string, string];
      for (const session of this.sessions.values()) {
        if (session.ride_id === rideId && session.closed_at === null) session.closed_at = closedAt;
      }
    }
  }
}

const makeFix = (overrides: Partial<TripLocationFix> = {}): TripLocationFix => ({
  ride_id: 'ride-1',
  captured_at: '2026-07-17T22:45:00.000Z',
  monotonic_ms: 1234,
  lat: 50.445,
  lng: -104.618,
  accuracy: 7,
  speed: 11,
  heading: 90,
  altitude: null,
  source: 'foreground',
  mocked: false,
  is_completion_fix: false,
  ...overrides,
});

describe('TripLocationOutbox', () => {
  let database: MemorySqliteDatabase;
  let uuidSequence: number;

  beforeEach(() => {
    database = new MemorySqliteDatabase();
    uuidSequence = 0;
  });

  const createOutbox = () => createTripLocationOutbox({
    openDatabase: async () => database as never,
    randomUUID: () => `session-${++uuidSequence}`,
    now: () => '2026-07-17T22:46:00.000Z',
  });

  it('allocates unique, ordered sequence numbers under concurrent writes', async () => {
    const outbox = createOutbox();
    const [first, second] = await Promise.all([outbox.enqueue(makeFix()), outbox.enqueue(makeFix())]);

    expect(first.recording_session_id).toBe(second.recording_session_id);
    expect([first.sequence_number, second.sequence_number].sort((a, b) => a - b)).toEqual([0, 1]);
  });

  it('reuses the one open session when separate JavaScript contexts share the database', async () => {
    const foregroundOutbox = createOutbox();
    const backgroundOutbox = createOutbox();

    const foreground = await foregroundOutbox.startSession('ride-1');
    const background = await backgroundOutbox.startSession('ride-1');

    expect(background.recording_session_id).toBe(foreground.recording_session_id);
    expect(uuidSequence).toBe(1);
  });

  it('preserves the native sensor capture timestamp without replacing it with wall-clock time', async () => {
    const outbox = createOutbox();
    const point = await outbox.enqueue(makeFix({ captured_at: '2026-07-17T21:00:00.111Z' }));

    expect(point.captured_at).toBe('2026-07-17T21:00:00.111Z');
    expect(database.outbox.get(`${point.recording_session_id}:${point.sequence_number}`)?.captured_at)
      .toBe('2026-07-17T21:00:00.111Z');
  });

  it('returns at most 500 pending points in sequence order', async () => {
    const outbox = createOutbox();
    for (let index = 0; index < 502; index += 1) await outbox.enqueue(makeFix({ monotonic_ms: index }));
    const session = await outbox.startSession('ride-1');

    const points = await outbox.peek(session.recording_session_id);

    expect(points).toHaveLength(500);
    expect(points[0]?.sequence_number).toBe(0);
    expect(points[499]?.sequence_number).toBe(499);
  });

  it('recovers pending points after a process restart', async () => {
    const originalOutbox = createOutbox();
    await originalOutbox.enqueue(makeFix());
    const recoveredOutbox = createOutbox();

    const sessions = await recoveredOutbox.listPendingSessions();

    expect(sessions).toEqual([expect.objectContaining({ ride_id: 'ride-1', recording_session_id: 'session-1' })]);
  });

  it('deletes only acknowledged points and quarantines permanent rejections', async () => {
    const outbox = createOutbox();
    const first = await outbox.enqueue(makeFix());
    const rejected = await outbox.enqueue(makeFix());
    const remaining = await outbox.enqueue(makeFix());

    await outbox.acknowledge(first.recording_session_id, rejected.sequence_number, [
      { sequence_number: rejected.sequence_number, reason: 'invalid_coordinate' },
    ]);

    expect(database.outbox.has(`${first.recording_session_id}:${first.sequence_number}`)).toBe(false);
    expect(database.outbox.has(`${rejected.recording_session_id}:${rejected.sequence_number}`)).toBe(false);
    expect(database.outbox.has(`${remaining.recording_session_id}:${remaining.sequence_number}`)).toBe(true);
    expect(database.quarantine.get(`${rejected.recording_session_id}:${rejected.sequence_number}`)?.rejection_reason)
      .toBe('invalid_coordinate');
  });

  it('surfaces local disk failures instead of treating a point as queued', async () => {
    const outbox = createOutbox();
    database.failNextOutboxInsert = true;

    await expect(outbox.enqueue(makeFix())).rejects.toThrow('disk full');
    expect(await outbox.pendingCount('ride-1')).toBe(0);
  });

  it('does not delete queued points merely because an upload must be retried', async () => {
    const outbox = createOutbox();
    const point = await outbox.enqueue(makeFix());

    expect(await outbox.peek(point.recording_session_id)).toHaveLength(1);
    expect(await outbox.peek(point.recording_session_id)).toHaveLength(1);
    expect(await outbox.pendingCount('ride-1')).toBe(1);
  });

  it('retries database initialization after a transient open failure', async () => {
    let openAttempts = 0;
    const outbox = createTripLocationOutbox({
      openDatabase: async () => {
        openAttempts += 1;
        if (openAttempts === 1) throw new Error('WAL checkpoint failed');
        return database as never;
      },
      randomUUID: () => `session-${++uuidSequence}`,
      now: () => '2026-07-17T22:46:00.000Z',
    });

    await expect(outbox.enqueue(makeFix())).rejects.toThrow('WAL checkpoint failed');
    const point = await outbox.enqueue(makeFix());

    expect(point.sequence_number).toBe(0);
    expect(openAttempts).toBe(2);
  });

  it('keeps unacknowledged points when a trip recording session closes', async () => {
    const outbox = createOutbox();
    const point = await outbox.enqueue(makeFix());

    await outbox.closeSession('ride-1');

    expect(await outbox.peek(point.recording_session_id)).toHaveLength(1);
    expect(await outbox.listPendingSessions()).toEqual([
      expect.objectContaining({ recording_session_id: point.recording_session_id, closed_at: '2026-07-17T22:46:00.000Z' }),
    ]);
  });
});

describe('purgeAll (sign-out)', () => {
  it('preserves unacknowledged points into quarantine, then clears outbox + sessions', async () => {
    const database = new MemorySqliteDatabase();
    const outbox = createTripLocationOutbox({
      openDatabase: async () => database as never,
      randomUUID: () => 'session-purge',
      now: () => '2026-07-29T10:00:00.000Z',
    });

    await outbox.startSession('ride-1');
    await outbox.enqueue(makeFix());
    await outbox.enqueue(makeFix({ monotonic_ms: 2345 }));
    await outbox.enqueue(makeFix({ monotonic_ms: 3456 })); // the un-flushed tail
    await outbox.acknowledge('session-purge', 0, [{ sequence_number: 1, reason: 'stale_capture' }]);
    const preQuarantine = database.quarantine.size;

    await outbox.purgeAll();

    // Sign-out must never destroy location evidence (SGI/billing): unacked
    // points move to quarantine — which no flush path ever reads, so the next
    // account on a shared device cannot upload them — and age off via the
    // prune TTL. The active outbox and sessions are cleared.
    expect(database.outbox.size).toBe(0);
    expect(database.sessions.size).toBe(0);
    expect(database.quarantine.size).toBeGreaterThanOrEqual(preQuarantine);
    const preserved = [...database.quarantine.values()].filter(
      (p) => p.rejection_reason === 'signout_unflushed',
    );
    expect(preserved.length).toBeGreaterThan(0);
  });

  it('is a no-op on an untouched outbox', async () => {
    const database = new MemorySqliteDatabase();
    const outbox = createTripLocationOutbox({
      openDatabase: async () => database as never,
      randomUUID: () => 'session-empty',
      now: () => '2026-07-29T10:00:00.000Z',
    });
    await expect(outbox.purgeAll()).resolves.toBeUndefined();
    expect(database.sessions.size).toBe(0);
  });

  it('leaves the outbox usable for a fresh session afterwards', async () => {
    const database = new MemorySqliteDatabase();
    let id = 0;
    const outbox = createTripLocationOutbox({
      openDatabase: async () => database as never,
      randomUUID: () => `session-${++id}`,
      now: () => '2026-07-29T10:00:00.000Z',
    });

    await outbox.startSession('ride-1');
    await outbox.enqueue(makeFix());
    await outbox.purgeAll();

    // The unique "one open session per ride" index would reject a re-open if
    // the purge had left the old session row behind.
    await outbox.startSession('ride-1');
    const queued = await outbox.enqueue(makeFix({ ride_id: 'ride-1' }));
    expect(queued.sequence_number).toBe(0);
    expect(database.outbox.size).toBe(1);
  });
});

describe('prune (retention bounds)', () => {
  const NOW = '2026-08-18T12:00:00.000Z';
  const DAYS = (n: number) => n * 24 * 60 * 60 * 1000;
  const iso = (msAgo: number) => new Date(Date.parse(NOW) - msAgo).toISOString();

  function harness(retention?: { quarantineMaxRows?: number; outboxSoftCapRows?: number }) {
    const database = new MemorySqliteDatabase();
    let id = 0;
    const outbox = createTripLocationOutbox({
      openDatabase: async () => database as never,
      randomUUID: () => `session-${++id}`,
      now: () => NOW,
      retention,
    });
    return { database, outbox };
  }

  it('deletes quarantine rows older than 14 days, keeps fresher ones', async () => {
    const { database, outbox } = harness();
    database.quarantine.set('s:0', { ...makeFix(), recording_session_id: 's', sequence_number: 0, enqueued_at: iso(DAYS(20)), rejection_reason: 'x', quarantined_at: iso(DAYS(15)) } as never);
    database.quarantine.set('s:1', { ...makeFix(), recording_session_id: 's', sequence_number: 1, enqueued_at: iso(DAYS(2)), rejection_reason: 'x', quarantined_at: iso(DAYS(2)) } as never);

    await outbox.prune();

    expect(database.quarantine.has('s:0')).toBe(false);
    expect(database.quarantine.has('s:1')).toBe(true);
  });

  it('expires sessions closed >7 days ago: points preserved to quarantine, rows + session deleted', async () => {
    const { database, outbox } = harness();
    await outbox.startSession('ride-old');
    await outbox.enqueue(makeFix({ ride_id: 'ride-old' }));
    await outbox.closeSession('ride-old');
    // Backdate the close beyond the TTL.
    for (const s of database.sessions.values()) s.closed_at = iso(DAYS(8));
    await outbox.startSession('ride-live');
    await outbox.enqueue(makeFix({ ride_id: 'ride-live' }));

    await outbox.prune();

    const reasons = [...database.quarantine.values()].map((q) => q.rejection_reason);
    expect(reasons).toContain('expired_unflushed');
    expect([...database.outbox.values()].every((p) => p.ride_id === 'ride-live')).toBe(true);
    expect([...database.sessions.values()].every((s) => s.ride_id === 'ride-live')).toBe(true);
  });

  it('caps quarantine oldest-first', async () => {
    const { database, outbox } = harness({ quarantineMaxRows: 2 });
    for (let i = 0; i < 4; i += 1) {
      database.quarantine.set(`s:${i}`, { ...makeFix(), recording_session_id: 's', sequence_number: i, enqueued_at: iso(DAYS(1)), rejection_reason: 'x', quarantined_at: iso(DAYS(0) + (4 - i) * 1000) } as never);
    }

    await outbox.prune();

    expect(database.quarantine.size).toBe(2);
    // The two OLDEST quarantined_at values were evicted (s:0 oldest … s:3 newest).
    expect([...database.quarantine.keys()].sort()).toEqual(['s:2', 's:3']);
  });

  it('soft-caps the outbox by evicting oldest CLOSED-session rows only, preserving them to quarantine', async () => {
    const { database, outbox } = harness({ outboxSoftCapRows: 2 });
    await outbox.startSession('ride-closed');
    await outbox.enqueue(makeFix({ ride_id: 'ride-closed', monotonic_ms: 1 }));
    await outbox.enqueue(makeFix({ ride_id: 'ride-closed', monotonic_ms: 2 }));
    await outbox.closeSession('ride-closed');
    // Recent close — NOT expired, only capped.
    for (const s of database.sessions.values()) if (s.ride_id === 'ride-closed') s.closed_at = iso(DAYS(1));
    await outbox.startSession('ride-open');
    await outbox.enqueue(makeFix({ ride_id: 'ride-open', monotonic_ms: 3 }));
    await outbox.enqueue(makeFix({ ride_id: 'ride-open', monotonic_ms: 4 }));

    await outbox.prune();

    // 4 rows, cap 2 → excess 2 evicted from the CLOSED session only.
    expect([...database.outbox.values()].filter((p) => p.ride_id === 'ride-open')).toHaveLength(2);
    expect([...database.outbox.values()].filter((p) => p.ride_id === 'ride-closed')).toHaveLength(0);
    const evicted = [...database.quarantine.values()].filter((q) => q.rejection_reason === 'evicted_capacity');
    expect(evicted).toHaveLength(2);
  });

  it('never evicts open-session rows even when the cap is exceeded', async () => {
    const { database, outbox } = harness({ outboxSoftCapRows: 1 });
    await outbox.startSession('ride-open');
    await outbox.enqueue(makeFix({ ride_id: 'ride-open', monotonic_ms: 1 }));
    await outbox.enqueue(makeFix({ ride_id: 'ride-open', monotonic_ms: 2 }));

    await outbox.prune();

    // Cap exceeded but every row belongs to an open session: deliberately unenforced.
    expect(database.outbox.size).toBe(2);
    expect(database.quarantine.size).toBe(0);
  });
});
