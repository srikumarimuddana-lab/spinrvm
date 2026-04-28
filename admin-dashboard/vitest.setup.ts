import { vi } from 'vitest';
import '@testing-library/jest-dom/vitest';

// Mock localStorage
const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => { store[key] = value; },
    removeItem: (key: string) => { delete store[key]; },
    clear: () => { store = {}; },
    get length() { return Object.keys(store).length; },
    key: (index: number) => Object.keys(store)[index] ?? null,
  };
})();

Object.defineProperty(window, 'localStorage', { value: localStorageMock });

// Mock fetch globally — return a resolved Promise by default so callers that
// chain .catch() (e.g. setAuthCookie, clearAuthCookie) don't throw a synchronous
// TypeError when no per-test mockResolvedValueOnce is set.
global.fetch = vi.fn().mockResolvedValue({
  ok: false,
  status: 0,
  json: () => Promise.resolve({}),
});

// Mock URL.createObjectURL
global.URL.createObjectURL = vi.fn(() => 'blob:mock-url');
global.URL.revokeObjectURL = vi.fn();
