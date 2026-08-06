import { readFileSync } from 'fs';
import { resolve } from 'path';

// CRLF→LF — see onlineResync.test.ts. Windows checkouts are CRLF, so a needle
// spanning a line break would match in CI and fail only on a dev machine.
const hookSource = readFileSync(resolve(__dirname, '..', 'useDriverDashboard.ts'), 'utf8').replace(
  /\r\n/g,
  '\n',
);

describe('Phase 1 (online-idle) durable location trail', () => {
  const idleBranch = hookSource.slice(
    hookSource.indexOf('// Phase 1 (online, no ride). The live marker fans out'),
    hookSource.indexOf('locationSubRef.current = sub;'),
  );

  it('persists at most one durable idle breadcrumb per minute', () => {
    expect(idleBranch).toContain('const IDLE_DURABLE_INTERVAL_MS = 60_000;');
    expect(idleBranch).toContain(
      'const persistIdle = now - lastIdleDurableMsRef.current >= IDLE_DURABLE_INTERVAL_MS;',
    );
    expect(idleBranch).toContain('if (persistIdle) lastIdleDurableMsRef.current = now;');
  });

  it('marks only the ~60s tick durable, keeping every other idle fix ephemeral', () => {
    // durable is the throttle flag, not a hardcoded false — the backend
    // persists online_idle only for the durable ticks.
    expect(idleBranch).toContain('durable: persistIdle,');
    expect(idleBranch).not.toContain('durable: false,');
  });

  it('declares the idle-throttle ref', () => {
    expect(hookSource).toContain('const lastIdleDurableMsRef = useRef<number>(0);');
  });
});
