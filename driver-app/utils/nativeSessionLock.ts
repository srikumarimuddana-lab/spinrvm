import * as SQLite from 'expo-sqlite';
import type { SessionLock } from '../../shared/auth/sessionLock';

let installed = false;
export function installNativeSessionCoordination(): void {
  if (installed || require('react-native').Platform.OS === 'web') return;
  const SecureStore = require('expo-secure-store');
  const { installSessionLock, setSessionKeychainOptions } = require('../../shared/auth/sessionLock');
  installSessionLock(createNativeSessionLock());
  setSessionKeychainOptions({ keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY });
  installed = true;
}

type Connection = { execAsync(sql: string): Promise<void>; closeAsync(): Promise<void> };
export function createNativeSessionLock(
  open: () => Promise<Connection> = () => SQLite.openDatabaseAsync('spinr-session-lock.db', { useNewConnection: true }),
): SessionLock {
  return async work => {
    const db = await open();
    let acquired = false;
    try {
      // Never share a cached connection: a second runtime must contend for a
      // real file lock, not join the first runtime's transaction. No secrets
      // or user data are written to this database.
      //
      // PRAGMA busy_timeout = 0 itself can throw "database is locked"/
      // SQLITE_BUSY on a real device -- not just BEGIN IMMEDIATE -- if a
      // second connection opens while another already holds a write lock.
      // It must run inside the same retry loop, not once before it, or that
      // throw escapes uncaught and this hand-rolled retry never engages
      // (Sentry CRIMSON-SMOKE-7445-120). Re-running the PRAGMA on every
      // attempt is a no-op once it has already taken effect.
      const deadline = Date.now() + 10_000;
      while (!acquired) {
        try {
          await db.execAsync('PRAGMA busy_timeout = 0');
          await db.execAsync('BEGIN IMMEDIATE');
          acquired = true;
        } catch (error) {
          if (!/database is locked|SQLITE_BUSY/i.test(String(error)) || Date.now() >= deadline) throw error;
          await new Promise(resolve => setTimeout(resolve, 50));
        }
      }
      // Hold through request AND SecureStore publication. No lease takeover:
      // a suspended owner may still receive its network response on resume.
      return await work();
    } finally {
      try { if (acquired) await db.execAsync('ROLLBACK'); }
      finally { await db.closeAsync(); }
    }
  };
}
