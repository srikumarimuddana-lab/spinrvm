/**
 * C4 (P0 GPS OOM): the WS location batch buffer must be bounded.
 *
 * In useDriverDashboard.ts the WS batch (wsBatchRef) is only cleared when the
 * socket is OPEN. During a sustained outage the per-location push grew it
 * without bound -> OOM on low-memory devices. The fix caps it at 500 (drop
 * oldest), mirroring the REST buffer. This pins that behavior with a standalone
 * mirror of the enqueue logic (the full hook lifecycle is out of scope — same
 * approach as locationBatch.test.ts).
 *
 * Code under test: useDriverDashboard.ts location-batch enqueue (~L634).
 */

const WS_BATCH_CAP = 500;

interface Ref<T> {
  current: T;
}

function enqueueWsPoint(
  batch: Ref<any[]>,
  lastFlush: Ref<number>,
  socketOpen: () => boolean,
  send: (msg: any) => void,
  payload: any,
  now: number,
): void {
  batch.current.push(payload);
  const sinceLastFlush = now - lastFlush.current;
  if (batch.current.length >= 3 || sinceLastFlush > 10_000) {
    if (socketOpen()) {
      send({ type: 'driver_location_batch', points: batch.current });
      batch.current = [];
      lastFlush.current = now;
    }
  }
  // Mirror of the C4 cap in useDriverDashboard.ts.
  if (batch.current.length > WS_BATCH_CAP) {
    batch.current = batch.current.slice(-WS_BATCH_CAP);
  }
}

const pt = (i: number) => ({ latitude: 52 + i / 1e6, longitude: -106, seq: i });

describe('WS location batch buffer cap (C4)', () => {
  it('flushes and clears when the socket is open', () => {
    const batch: Ref<any[]> = { current: [] };
    const lastFlush: Ref<number> = { current: 1000 };
    const send = jest.fn();

    for (let i = 0; i < 3; i++) {
      enqueueWsPoint(batch, lastFlush, () => true, send, pt(i), 1000 + i);
    }

    expect(send).toHaveBeenCalledTimes(1);
    expect(batch.current).toHaveLength(0);
  });

  it('stays bounded at 500 during a sustained socket outage (no OOM)', () => {
    const batch: Ref<any[]> = { current: [] };
    const lastFlush: Ref<number> = { current: 0 };
    const send = jest.fn();

    for (let i = 0; i < 2000; i++) {
      enqueueWsPoint(batch, lastFlush, () => false, send, pt(i), 1_000_000 + i);
    }

    expect(send).not.toHaveBeenCalled();
    expect(batch.current.length).toBeLessThanOrEqual(WS_BATCH_CAP);
    // Oldest dropped, most-recent retained.
    expect(batch.current[batch.current.length - 1].seq).toBe(1999);
    expect(batch.current[0].seq).toBe(2000 - WS_BATCH_CAP);
  });

  it('flushes the capped buffer once the socket recovers', () => {
    const batch: Ref<any[]> = { current: [] };
    const lastFlush: Ref<number> = { current: 0 };
    const send = jest.fn();

    for (let i = 0; i < 600; i++) {
      enqueueWsPoint(batch, lastFlush, () => false, send, pt(i), 1_000_000 + i);
    }
    expect(batch.current.length).toBe(WS_BATCH_CAP);

    // Socket recovers; next enqueue flushes (length >= 3) and clears.
    enqueueWsPoint(batch, lastFlush, () => true, send, pt(600), 1_000_600);
    expect(send).toHaveBeenCalledTimes(1);
    expect(batch.current).toHaveLength(0);
  });
});
