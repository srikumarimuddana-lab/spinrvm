/**
 * Source-contract test for the three WebSocket `driver_location` senders in
 * hooks/useDriverDashboard.ts (same pattern as lib/androidAuto/__tests__/
 * carRoute.test.ts — the hook has no unit harness for its socket path, and
 * the behavioural guarantee lives server-side in
 * backend/tests/test_breadcrumb_persistence.py, ADR 016 invariant I5).
 *
 * Ride SPR-T9NYPB (2026-09-11): the reconnect echo re-sent the cached last
 * fix on every WS auth_success with no `durable` flag (backend default: true)
 * and no capture time, so each of seven relaunches planted a 25–200 s-old
 * position at "now" in the ride's trail. This pins that every durable-capable
 * sender carries the fix's own capture time and that the echo is live-marker
 * only.
 */
import fs from 'fs';
import path from 'path';

const source = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'hooks', 'useDriverDashboard.ts'),
  'utf8',
);

// Every `type: 'driver_location'` send, with the object literal that follows.
function senderBlocks(): string[] {
  const blocks: string[] = [];
  const re = /type:\s*'driver_location',([\s\S]*?)\}\)\);/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(source)) !== null) blocks.push(m[1]);
  return blocks;
}

describe('useDriverDashboard WebSocket driver_location senders (ADR 016 I5)', () => {
  it('has exactly three senders', () => {
    expect(senderBlocks()).toHaveLength(3);
  });

  it('every sender carries the fix\'s own capture time', () => {
    for (const block of senderBlocks()) {
      expect(block).toMatch(/captured_at:\s*(point\.captured_at|new Date\(loc\.timestamp\)\.toISOString\(\))/);
    }
  });

  it('the reconnect echo is live-marker only (durable:false), never a trail point', () => {
    const echo = senderBlocks().find((b) => b.includes("tracking_phase: 'online_idle'"));
    expect(echo).toBeDefined();
    expect(echo).toContain('durable: false');
  });

  it('the durable senders decide durability explicitly, never by backend default', () => {
    for (const block of senderBlocks()) {
      expect(block).toMatch(/durable:\s*(false|persistIdle|!batchUploadHealthyRef\.current)/);
    }
  });
});
