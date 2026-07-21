import { readFileSync } from 'fs';
import { resolve } from 'path';

const hookSource = readFileSync(resolve(__dirname, '..', 'useDriverDashboard.ts'), 'utf8');

describe('online-flag reconcile with authoritative profile', () => {
  const effect = hookSource.slice(
    hookSource.indexOf('// ─── Reconcile local online flag with the authoritative profile ──'),
    hookSource.indexOf('// ─── Fetch earnings when online ─────────────────────────────────'),
  );

  it('adopts the server online flag when it diverges from local state', () => {
    expect(effect).toContain('const serverOnline = driverData?.is_online;');
    expect(effect).toContain('if (!!serverOnline === isOnline) return;');
    expect(effect).toContain('setIsOnline(!!serverOnline);');
    // Keyed on the profile flag so a cold-start/resume that hydrates
    // driverData late still reconciles.
    expect(effect).toContain('}, [driverData?.is_online, isOnline]);');
  });

  it('re-arms background tracking when reconciling to online', () => {
    expect(effect).toContain('if (serverOnline) {');
    expect(effect).toContain('startBackgroundLocation().catch(() => {});');
  });

  it('does not clobber an in-flight optimistic toggle', () => {
    expect(effect).toContain('if (isTogglingRef.current) return;');
  });

  it('toggleOnline sets and clears the toggle guard around its request', () => {
    const toggle = hookSource.slice(
      hookSource.indexOf('const toggleOnline = async () => {'),
      hookSource.indexOf('// ─── Navigate to pickup/dropoff'),
    );
    expect(toggle).toContain('isTogglingRef.current = true;');
    expect(toggle).toContain('} finally {\n      isTogglingRef.current = false;\n    }');
  });
});
