// Driver startup installs a native cross-runtime lock. Other surfaces retain
// their existing storage behavior and do not import a native SQLite dependency.
export type SessionLock = <T>(work: () => Promise<T>) => Promise<T>;
let lock: SessionLock = work => work();
export function installSessionLock(nativeLock: SessionLock): void { lock = nativeLock; }
export function withSessionLock<T>(work: () => Promise<T>): Promise<T> { return lock(work); }

// Driver credentials must remain readable after the screen locks. These options
// are installed alongside the native lock, so rider keychain policy is unchanged.
export let sessionKeychainOptions: { keychainAccessible?: number } | undefined;
export function setSessionKeychainOptions(options: { keychainAccessible?: number }): void {
  sessionKeychainOptions = options;
}

// Non-secret capture fence; changes only on explicit sign-in, never rotation.
export const SESSION_GENERATION_KEY = 'spinr_session_generation';
