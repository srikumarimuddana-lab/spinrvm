/* eslint-disable import/first */
/**
 * X8 client half: the successor proposal sent with POST /auth/refresh must be
 * persisted before use, reused for the same parent (so a lost response can be
 * recovered), and never sent when it could be weak or unsaved.
 */

const mockStore: Record<string, string> = {};
const mockGetRandomBytes = jest.fn();

jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn(async (key: string) => mockStore[key] ?? null),
  setItemAsync: jest.fn(async (key: string, value: string) => { mockStore[key] = value; }),
  deleteItemAsync: jest.fn(async (key: string) => { delete mockStore[key]; }),
}));

jest.mock('expo-crypto', () => ({ getRandomBytes: (n: number) => mockGetRandomBytes(n) }));

import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';
import { setSessionKeychainOptions } from '../../../shared/auth/sessionLock';
import {
  clearRefreshProposal,
  REFRESH_PROPOSAL_KEY,
  refreshProposalFor,
} from '../../../shared/auth/refreshProposal';

// Expo installs `fetch` as a lazy global; see utils/__tests__/alwaysLocationGate.test.ts.
Object.defineProperty(globalThis, 'fetch', { value: jest.fn(), configurable: true, writable: true });

const SHAPE = /^[A-Za-z0-9_-]{64}$/;
const randomBytes = () => Uint8Array.from({ length: 64 }, (_, i) => (i * 37 + 11) % 256);

beforeEach(() => {
  for (const key of Object.keys(mockStore)) delete mockStore[key];
  jest.clearAllMocks();
  mockGetRandomBytes.mockImplementation(randomBytes);
  (Platform as { OS: string }).OS = 'ios';
  setSessionKeychainOptions({ keychainAccessible: 42 });
});

it('persists a server-shaped proposal bound to its parent before returning it', async () => {
  const proposal = await refreshProposalFor('parent-a');
  expect(proposal).toMatch(SHAPE);
  expect(mockGetRandomBytes).toHaveBeenCalledWith(64);
  expect(SecureStore.setItemAsync).toHaveBeenCalledWith(
    REFRESH_PROPOSAL_KEY,
    JSON.stringify({ parent: 'parent-a', proposal }),
    { keychainAccessible: 42 },
  );
});

it('reuses the pending proposal for the same parent, so a lost response can be replayed', async () => {
  const first = await refreshProposalFor('parent-a');
  mockGetRandomBytes.mockClear();
  expect(await refreshProposalFor('parent-a')).toBe(first);
  expect(mockGetRandomBytes).not.toHaveBeenCalled();
});

it('replaces the pending proposal when the parent changed', async () => {
  const first = await refreshProposalFor('parent-a');
  mockGetRandomBytes.mockImplementation(() => randomBytes().map((b) => 255 - b));
  const second = await refreshProposalFor('parent-b');
  expect(second).toMatch(SHAPE);
  expect(second).not.toBe(first);
  expect(JSON.parse(mockStore[REFRESH_PROPOSAL_KEY])).toEqual({ parent: 'parent-b', proposal: second });
});

it.each([
  ['a degenerate RNG output', () => new Uint8Array(64)],
  ['a short buffer', () => new Uint8Array(10).fill(7)],
  ['a missing native module', () => undefined],
  ['a throwing native module', () => { throw new Error('ExpoCrypto unavailable'); }],
])('sends no proposal for %s', async (_label, impl) => {
  mockGetRandomBytes.mockImplementation(impl);
  expect(await refreshProposalFor('parent-a')).toBeNull();
  expect(SecureStore.setItemAsync).not.toHaveBeenCalled();
});

it('sends no proposal it could not persist', async () => {
  (SecureStore.setItemAsync as jest.Mock).mockRejectedValueOnce(new Error('Keychain unavailable'));
  expect(await refreshProposalFor('parent-a')).toBeNull();
});

it('ignores a corrupt pending entry and starts a fresh proposal', async () => {
  mockStore[REFRESH_PROPOSAL_KEY] = '{not json';
  expect(await refreshProposalFor('parent-a')).toMatch(SHAPE);
});

it('never proposes on web, where the refresh token is a cookie', async () => {
  (Platform as { OS: string }).OS = 'web';
  expect(await refreshProposalFor('parent-a')).toBeNull();
  expect(SecureStore.getItemAsync).not.toHaveBeenCalled();
});

it('clears the pending proposal and swallows storage errors', async () => {
  await refreshProposalFor('parent-a');
  await clearRefreshProposal();
  expect(mockStore[REFRESH_PROPOSAL_KEY]).toBeUndefined();
  (SecureStore.deleteItemAsync as jest.Mock).mockRejectedValueOnce(new Error('Keychain unavailable'));
  await expect(clearRefreshProposal()).resolves.toBeUndefined();
});
