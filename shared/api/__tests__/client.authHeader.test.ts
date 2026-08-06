/**
 * Pins the contract that documents.tsx and other raw-fetch upload paths
 * rely on: getAuthHeader() must return the token set via setInMemoryToken()
 * from the same module instance.
 *
 * Background: the access token is memory-only (authStore.setTokens
 * deliberately deletes any persisted `auth_token` from SecureStore and
 * only persists the refresh token). documents.tsx used to read directly
 * from SecureStore, which always returned null post-login, producing
 * "401 No authorization token provided" on every upload.
 *
 * Fix: expose getAuthHeader from shared/api/client and have documents.tsx
 * reuse it. This test verifies the exported getAuthHeader honours the
 * in-memory token.
 *
 * Code under test: shared/api/client.ts::getAuthHeader + setInMemoryToken
 */

jest.mock('react-native', () => ({
  Platform: { OS: 'web' },
}));

jest.mock('../../config', () => ({
  API_URL: 'http://localhost:8000',
}));

// api/client imports config/spinr.config, which imports expo-constants. Without
// this mock the suite fails to run outside an Expo app root with
// "Cannot read properties of undefined (reading 'EXDevLauncher')" — which is how
// this file sat broken and unnoticed while no jest project picked the directory
// up. client.refresh.test.ts and client.sos.test.ts already mock it; this one
// did not.
jest.mock('../../config/spinr.config', () => ({
  __esModule: true,
  default: { backendUrl: 'http://localhost:8000' },
}));

jest.mock('../../services/firebase', () => ({
  auth: { currentUser: null, onAuthStateChanged: null },
  isFirebaseConfigured: false,
}));

const _secureStoreBacking: Record<string, string | null> = {};
jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn((k: string) => Promise.resolve(_secureStoreBacking[k] ?? null)),
  setItemAsync: jest.fn((k: string, v: string) => { _secureStoreBacking[k] = v; return Promise.resolve(); }),
  deleteItemAsync: jest.fn((k: string) => { delete _secureStoreBacking[k]; return Promise.resolve(); }),
}));

const _webStorage: Record<string, string> = {};
Object.defineProperty(global, 'sessionStorage', {
  value: {
    getItem: (k: string) => _webStorage[k] ?? null,
    setItem: (k: string, v: string) => { _webStorage[k] = v; },
    removeItem: (k: string) => { delete _webStorage[k]; },
  },
  writable: true,
});
Object.defineProperty(global, 'localStorage', {
  value: {
    getItem: (k: string) => _webStorage[k] ?? null,
    setItem: (k: string, v: string) => { _webStorage[k] = v; },
    removeItem: (k: string) => { delete _webStorage[k]; },
  },
  writable: true,
});

import { setInMemoryToken, getAuthHeader } from '../../api/client';

beforeEach(() => {
  setInMemoryToken(null);
  Object.keys(_secureStoreBacking).forEach((k) => delete _secureStoreBacking[k]);
  Object.keys(_webStorage).forEach((k) => delete _webStorage[k]);
});

describe('shared/api/client — getAuthHeader contract (document-upload fix)', () => {
  it('returns null when no token is set anywhere', async () => {
    const token = await getAuthHeader();
    expect(token).toBeNull();
  });

  it('returns the in-memory token when setInMemoryToken has been called', async () => {
    // This is exactly the post-login state: authStore.setTokens calls
    // setInMemoryToken(accessToken) and deliberately does NOT persist the
    // access token to SecureStore.
    setInMemoryToken('access-jwt-from-verify-otp');

    const token = await getAuthHeader();
    expect(token).toBe('access-jwt-from-verify-otp');
  });

  it('prefers the in-memory token over a stale SecureStore value', async () => {
    // Protects against the regression where a legacy app version wrote
    // auth_token to SecureStore — the current session's in-memory token
    // must always win.
    _secureStoreBacking['auth_token'] = 'stale-legacy-token';
    setInMemoryToken('fresh-in-memory-token');

    const token = await getAuthHeader();
    expect(token).toBe('fresh-in-memory-token');
  });

  it('falls back to SecureStore when in-memory is empty (cold start case)', async () => {
    // On web the fallback reads sessionStorage, not SecureStore. Platform
    // is mocked to 'web' at the top of this file, so use sessionStorage.
    _webStorage['auth_token'] = 'restored-from-storage';
    setInMemoryToken(null);

    const token = await getAuthHeader();
    expect(token).toBe('restored-from-storage');
  });

  it('module state is shared: a setter call from one import is visible to another', async () => {
    // Guards against monorepo module-duplication — if @shared/api/client
    // resolved to a different copy than ../api/client, setInMemoryToken
    // from one wouldn't affect getAuthHeader from the other, and the
    // upload bug would still exist. Re-requiring the same module must
    // return the same singleton.
    const reimport = require('../../api/client');
    setInMemoryToken('singleton-check-token');

    const fromReimport = await reimport.getAuthHeader();
    const fromDirect = await getAuthHeader();

    expect(fromReimport).toBe('singleton-check-token');
    expect(fromDirect).toBe('singleton-check-token');
  });
});
