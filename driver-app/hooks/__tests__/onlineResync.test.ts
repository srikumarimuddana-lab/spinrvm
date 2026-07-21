import { readFileSync } from 'fs';
import { resolve } from 'path';

const hookSource = readFileSync(resolve(__dirname, '..', 'useDriverDashboard.ts'), 'utf8');

describe('one-time online-flag hydration from the authoritative profile', () => {
  const effect = hookSource.slice(
    hookSource.indexOf('// ─── Hydrate the online flag from the authoritative profile (once) ──'),
    hookSource.indexOf('// ─── Fetch earnings when online ─────────────────────────────────'),
  );

  it('adopts the loaded profile online flag exactly once', () => {
    expect(effect).toContain('const onlineHydratedRef = useRef(false);');
    expect(effect).toContain('if (onlineHydratedRef.current) return;');
    expect(effect).toContain('onlineHydratedRef.current = true;');
    expect(effect).toContain('const serverOnline = driverData?.is_online;');
    expect(effect).toContain('setIsOnline(!!serverOnline);');
  });

  it('waits for the profile to load before hydrating', () => {
    expect(effect).toContain('if (serverOnline === undefined || serverOnline === null) return;');
  });

  it('re-arms background tracking only when hydrating to online', () => {
    expect(effect).toContain('if (serverOnline) {');
    expect(effect).toContain('startBackgroundLocation().catch(() => {});');
  });

  it('never fights an in-flight toggle (leaves marking un-hydrated so a later run syncs)', () => {
    // The toggling guard must come BEFORE we mark hydrated, so a toggle in
    // flight at first-profile-load doesn't burn the one-time hydration.
    const guardIdx = effect.indexOf('if (isTogglingRef.current) return;');
    const markIdx = effect.indexOf('onlineHydratedRef.current = true;');
    expect(guardIdx).toBeGreaterThan(-1);
    expect(markIdx).toBeGreaterThan(guardIdx);
  });

  it('is one-time hydration, not continuous reactivity that could fight auto_offline', () => {
    // Regression guard: auto_offline sets isOnline=false locally but leaves
    // driverData.is_online=true; a reactive reconcile would flip it back on.
    // The early return on onlineHydratedRef makes the effect inert after the
    // first loaded profile.
    expect(effect).toContain('if (onlineHydratedRef.current) return;');
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
