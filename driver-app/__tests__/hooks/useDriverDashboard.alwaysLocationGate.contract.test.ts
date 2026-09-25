/**
 * Source-contract test (same pattern as useDriverDashboard.wsSenders.contract):
 * the hook has no unit harness, and the gate's own behaviour is tested in
 * utils/__tests__/alwaysLocationGate.test.ts. This pins that both entry points
 * stay behind the default-off always_location_required flag (migration 467),
 * so a deploy with the flag off keeps the previous go-online / resume flow.
 */
import fs from 'fs';
import path from 'path';

const source = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'hooks', 'useDriverDashboard.ts'),
  'utf8',
);

describe('useDriverDashboard always-location gate is flagged', () => {
  it('reads the flag from /drivers/config, default off', () => {
    expect(source).toContain('const alwaysLocationGateRef = useRef(false);');
    expect(source).toMatch(/always_location_required\s*===\s*true/);
  });

  it('go-online only runs the gate when the flag is on', () => {
    expect(source).toMatch(
      /next && Platform\.OS === 'android' && alwaysLocationGateRef\.current &&\s*!\(await ensureAlwaysLocationForGoOnline\(\)\)/,
    );
  });

  it('resume only forces offline when the flag is on', () => {
    const forced = source.indexOf('goOfflineWithoutAlwaysLocation({');
    const guard = source.lastIndexOf("Platform.OS === 'android' && alwaysLocationGateRef.current", forced);
    expect(forced).toBeGreaterThan(-1);
    expect(guard).toBeGreaterThan(-1);
    expect(forced - guard).toBeLessThan(800);
  });
});
