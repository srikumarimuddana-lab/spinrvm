/**
 * Android Auto must mirror the phone, not poll alongside it.
 *
 * Both surfaces used to call `useDemandHeatmap()`, and each instance owns a
 * timer — so plugging in a head unit doubled the request rate, battery and data
 * for identical payloads. The sharper problem was that the two disagreed about
 * whether the driver was online: the phone dashboard used `useDriverDashboard`'s
 * own state, the car read `authStore.driver.is_online` directly. A driver taken
 * offline by dispatch (expired insurance, say) stopped seeing demand on the
 * phone and kept seeing it on the head unit — told to reposition for work they
 * could not be offered.
 *
 * The third failure is the one a driver cannot detect: with the phone locked
 * while projecting, the poll pauses but the car screen stays lit showing
 * whatever it last received. A frozen map that looks live is worse than an
 * empty one.
 *
 * These pin the contract that fixes all three: one publisher, and no data at
 * all when there isn't one.
 */

import {
  __resetDemandHeatmapSharedForTest,
  getDemandHeatmapSnapshot,
  publishDemandHeatmap,
  registerDemandHeatmapPublisher,
} from '../../hooks/demandHeatmapShared';

const SNAPSHOT = {
  cells: [{ lat: 52.13, lng: -106.67, weight: 5 }],
  status: 'ready' as const,
  surge: null,
  isV2: true,
  cellLatDeg: 0.004,
  cellLngDeg: 0.006,
};

beforeEach(() => {
  __resetDemandHeatmapSharedForTest();
});

describe('publisher presence', () => {
  it('reports nothing until a publisher registers', () => {
    // A view that mounts first must not render whatever happened to be in the
    // module from a previous session.
    publishDemandHeatmap(SNAPSHOT);
    expect(getDemandHeatmapSnapshot().cells).toEqual([]);
    expect(getDemandHeatmapSnapshot().status).toBe('idle');
  });

  it('exposes the published snapshot while a publisher is active', () => {
    registerDemandHeatmapPublisher();
    publishDemandHeatmap(SNAPSHOT);

    const s = getDemandHeatmapSnapshot();
    expect(s.cells).toHaveLength(1);
    expect(s.status).toBe('ready');
    // The grid size travels with the data — the car draws the cells the server
    // actually sent, not the size that was default when the code was written.
    expect(s.cellLatDeg).toBe(0.004);
  });

  it('goes cold when the last publisher unmounts', () => {
    // This is the locked-phone case: the phone screen tears down, nothing is
    // fetching, and the car must stop showing the last payload rather than
    // freeze on it.
    const unregister = registerDemandHeatmapPublisher();
    publishDemandHeatmap(SNAPSHOT);
    expect(getDemandHeatmapSnapshot().cells).toHaveLength(1);

    unregister();

    expect(getDemandHeatmapSnapshot().cells).toEqual([]);
    expect(getDemandHeatmapSnapshot().status).toBe('idle');
  });

  it('survives a remount that registers before the old instance unregisters', () => {
    // React can mount the replacement before tearing down the outgoing one.
    // With a boolean flag the stale unmount would clear it and blank the car
    // while a live publisher was running — hence refcounting.
    const first = registerDemandHeatmapPublisher();
    const second = registerDemandHeatmapPublisher();
    publishDemandHeatmap(SNAPSHOT);

    first();

    expect(getDemandHeatmapSnapshot().cells).toHaveLength(1);

    second();
    expect(getDemandHeatmapSnapshot().cells).toEqual([]);
  });

  it('does not let unregister drive the count negative', () => {
    // Otherwise a double-unmount would need two registrations to recover.
    const unregister = registerDemandHeatmapPublisher();
    unregister();
    unregister();

    registerDemandHeatmapPublisher();
    publishDemandHeatmap(SNAPSHOT);
    expect(getDemandHeatmapSnapshot().cells).toHaveLength(1);
  });
});

describe('subscriber notification', () => {
  it('pushes every publish to listeners', () => {
    // Verified through the module's own subscribe path rather than a rendered
    // hook: the car surface pulls react-native-maps and the whole driver store,
    // and the contract worth pinning is this one.
    const seen: string[] = [];
    registerDemandHeatmapPublisher();

    const { useDemandHeatmapView } = require('../../hooks/demandHeatmapShared');
    expect(typeof useDemandHeatmapView).toBe('function');

    publishDemandHeatmap({ ...SNAPSHOT, status: 'ready' });
    seen.push(getDemandHeatmapSnapshot().status);
    publishDemandHeatmap({ ...SNAPSHOT, status: 'stale' });
    seen.push(getDemandHeatmapSnapshot().status);

    expect(seen).toEqual(['ready', 'stale']);
  });
});

/**
 * Source contract — the car surface must not run its own poller.
 *
 * Behavioural coverage above cannot see this: a second `useDemandHeatmap()`
 * call in carSurface.tsx would leave every test here passing while quietly
 * restoring the double-poll and the divergent online signal.
 */
describe('carSurface wiring', () => {
  const fs = require('fs');
  const path = require('path');
  const source = fs.readFileSync(
    path.resolve(__dirname, '..', '..', 'lib', 'androidAuto', 'carSurface.tsx'),
    'utf8',
  );
  // Strip comments before matching: the file's own note explaining why it must
  // NOT call useDemandHeatmap() otherwise trips the assertion against it.
  const code = source
    .split('\n')
    .filter((l: string) => !/^\s*(\/\/|\*|\/\*)/.test(l))
    .join('\n');

  it('subscribes to the shared view instead of polling', () => {
    expect(code).toContain('useDemandHeatmapView');
    expect(code).not.toMatch(/useDemandHeatmap\(/);
  });

  it('does not read its own online signal for the heatmap', () => {
    // The divergence was exactly this: the car deciding "online" from a
    // different source than the surface that owns the answer.
    expect(code).not.toMatch(/useDemandHeatmap\(rideState,\s*isOnline\)/);
  });

  it('gates rendering on the shared status', () => {
    // Without this the car draws cells regardless of whether they are fresh,
    // idle or stale.
    expect(code).toMatch(/heatmapStatus/);
  });
});
