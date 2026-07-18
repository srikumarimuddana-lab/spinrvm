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
  readonly quarantine = new Map<string, OutboxRow & { rejection_reason: string }>();
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
      const rejectionReason = params[0] as string;
      if (sql.includes('sequence_number = ?')) {
        // Per-point rejection: params [reason, quarantined_at, session_id, sequence].
        const sessionId = params[2] as string;
        const sequence = params[3] as number;
        const point = this.outbox.get(`${sessionId}:${sequence}`);
        if (point && !this.quarantine.has(`${sessionId}:${sequence}`)) {
          this.quarantine.set(`${sessionId}:${sequence}`, { ...point, rejection_reason: rejectionReason });
        }
      } else {
        // Whole-session quarantine: params [reason, quarantined_at, session_id].
        const sessionId = params[2] as string;
        for (const [key, point] of this.outbox) {
          if (point.recording_session_id === sessionId && !this.quarantine.has(key)) {
            this.quarantine.set(key, { ...point, rejection_reason: rejectionReason });
          }
        }
      }
      return;
    }
    if (sql.includes('DELETE FROM trip_location_outbox')) {
      const sessionId = params[0] as string;
      if (sql.includes('sequence_number >= ?')) {
        // Acknowledgement range delete: params [session_id, from, to].
        const from = params[1] as number;
        const to = params[2] as number;
        for (const [key, point] of this.outbox) {
          if (point.recording_session_id === sessionId && point.sequence_number >= from && point.sequence_number <= to) {
            this.outbox.delete(key);
          }
        }
      } else if (sql.includes('sequence_number <= ?')) {
        const acknowledgedThrough = params[1] as number;
        for (const [key, point] of this.outbox) {
          if (point.recording_session_id === sessionId && point.sequence_number <= acknowledgedThrough) this.outbox.delete(key);
        }
      } else {
        // Whole-session delete: params [session_id].
        for (const [key, point] of this.outbox) {
          if (point.recording_session_id === sessionId) this.outbox.delete(key);
        }
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

    await outbox.acknowledge(
      first.recording_session_id,
      rejected.sequence_number,
      { from: first.sequence_number, to: remaining.sequence_number },
      [{ sequence_number: rejected.sequence_number, reason: 'invalid_coordinate' }],
    );

    expect(database.outbox.has(`${first.recording_session_id}:${first.sequence_number}`)).toBe(false);
    expect(database.outbox.has(`${rejected.recording_session_id}:${rejected.sequence_number}`)).toBe(false);
    expect(database.outbox.has(`${remaining.recording_session_id}:${remaining.sequence_number}`)).toBe(true);
    expect(database.quarantine.get(`${rejected.recording_session_id}:${rejected.sequence_number}`)?.rejection_reason)
      .toBe('invalid_coordinate');
  });

  it('only deletes what a batch actually submitted, preserving the pending tail', async () => {
    const outbox = createOutbox();
    const tail = await outbox.enqueue(makeFix());
    const completion = await outbox.enqueue(makeFix({ is_completion_fix: true }));

    // A completion acknowledgement covers only the completion fix's own
    // sequence — the in-ride tail below it must remain queued for the next flush.
    await outbox.acknowledge(
      completion.recording_session_id,
      completion.sequence_number,
      { from: completion.sequence_number, to: completion.sequence_number },
    );

    expect(database.outbox.has(`${completion.recording_session_id}:${completion.sequence_number}`)).toBe(false);
    expect(database.outbox.has(`${tail.recording_session_id}:${tail.sequence_number}`)).toBe(true);
  });

  it('quarantines an entire poisoned session and clears it from the queue', async () => {
    const outbox = createOutbox();
    const a = await outbox.enqueue(makeFix());
    const b = await outbox.enqueue(makeFix());

    await outbox.quarantineSession(a.recording_session_id, 'http_409');

    expect(database.outbox.has(`${a.recording_session_id}:${a.sequence_number}`)).toBe(false);
    expect(database.outbox.has(`${b.recording_session_id}:${b.sequence_number}`)).toBe(false);
    expect(database.quarantine.get(`${a.recording_session_id}:${a.sequence_number}`)?.rejection_reason).toBe('http_409');
    expect(await outbox.pendingCount('ride-1')).toBe(0);
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
