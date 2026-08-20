/**
 * FIFO mutex contract for location-task start/stop serialization.
 * The single-writer invariant (dispatch task vs Android Auto task) depends on
 * these sections never interleaving within a JS context.
 */
const mockRecordNonFatal = jest.fn();
jest.mock('../crashlytics', () => ({
  recordNonFatal: (...a: unknown[]) => mockRecordNonFatal(...a),
}));

import { runExclusive, _resetLocationTaskArbiter } from '../locationTaskArbiter';

const tick = () => new Promise((resolve) => setImmediate(resolve));

describe('locationTaskArbiter.runExclusive', () => {
  beforeEach(() => {
    _resetLocationTaskArbiter();
    mockRecordNonFatal.mockClear();
    jest.useRealTimers();
  });

  it('runs sections strictly in FIFO order with no interleaving', async () => {
    const events: string[] = [];
    let releaseFirst!: () => void;
    const firstGate = new Promise<void>((resolve) => { releaseFirst = resolve; });

    const first = runExclusive('first', async () => {
      events.push('first:start');
      await firstGate;
      events.push('first:end');
      return 1;
    });
    const second = runExclusive('second', async () => {
      events.push('second:start');
      return 2;
    });

    await tick();
    expect(events).toEqual(['first:start']); // second is queued, not started
    releaseFirst();
    expect(await first).toBe(1);
    expect(await second).toBe(2);
    expect(events).toEqual(['first:start', 'first:end', 'second:start']);
  });

  it('a throwing section releases the lock for the next one', async () => {
    await expect(runExclusive('boom', async () => { throw new Error('boom'); })).rejects.toThrow('boom');
    await expect(runExclusive('after', async () => 'ok')).resolves.toBe('ok');
  });

  it('proceeds past a hung predecessor after the bounded wait, with one non-fatal', async () => {
    jest.useFakeTimers();
    // Predecessor never resolves — a hung native call.
    void runExclusive('hung', () => new Promise(() => {}));
    const second = runExclusive('queued-behind-hang', async () => 'ran');

    await Promise.resolve();
    jest.advanceTimersByTime(10_000);
    await expect(second).resolves.toBe('ran');
    expect(mockRecordNonFatal).toHaveBeenCalledTimes(1);

    // One-shot: a second timeout does not re-report.
    const third = runExclusive('also-behind-hang', async () => 'ran too');
    await Promise.resolve();
    jest.advanceTimersByTime(10_000);
    await expect(third).resolves.toBe('ran too');
    expect(mockRecordNonFatal).toHaveBeenCalledTimes(1);
  });
});
