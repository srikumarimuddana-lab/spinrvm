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
      await db.execAsync('PRAGMA busy_timeout = 0');
      const deadline = Date.now() + 10_000;
      while (!acquired) {
        try {
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
