/**
 * rider-app language store guards (W7.1 edge-case review).
 *
 * 1. Load-vs-pick race: hydrate() (called once by RootLayout at app start)
 *    reads AsyncStorage asynchronously. If that read resolved after the rider
 *    had picked a language, it used to overwrite the fresh choice with the
 *    stale stored value. A pick now always wins.
 * 2. Write-side guard: setLanguage() — and the changeLanguage() compat shims
 *    that route through it, which still map 'es'/'zh'/'zh-CN' — refuse any
 *    code the picker doesn't offer, keeping the current language.
 * 3. hydrate() logs a storage failure instead of swallowing it.
 */
const mockGetItem = jest.fn();
const mockSetItem = jest.fn();
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: (...args: unknown[]) => mockGetItem(...args),
  setItem: (...args: unknown[]) => mockSetItem(...args),
  removeItem: jest.fn(() => Promise.resolve()),
}));

import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import i18n, { useLanguageStore, useTranslation } from '../../i18n';

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

beforeEach(() => {
  mockGetItem.mockReset();
  mockSetItem.mockReset();
  mockSetItem.mockResolvedValue(undefined);
  // A cold start: English default, nothing picked yet this session.
  useLanguageStore.setState({ language: 'en', hasUserChosen: false });
});

describe('load-vs-pick race', () => {
  it('keeps a language picked while hydrate() is still reading storage', async () => {
    const slowRead = deferred<string | null>();
    mockGetItem.mockReturnValue(slowRead.promise);

    const restoring = useLanguageStore.getState().hydrate(); // app start
    useLanguageStore.getState().setLanguage('fr'); // rider picks French
    expect(useLanguageStore.getState().language).toBe('fr');

    slowRead.resolve('en'); // stale stored value arrives late
    await restoring;

    expect(useLanguageStore.getState().language).toBe('fr');
    expect(mockSetItem).toHaveBeenCalledWith('@spinr_rider_language', 'fr');
  });

  it('still applies the restore when nothing was picked', async () => {
    mockGetItem.mockResolvedValue('fr');
    await useLanguageStore.getState().hydrate();
    expect(useLanguageStore.getState().language).toBe('fr');
  });
});

describe('write-side guard', () => {
  let warn: jest.SpyInstance;
  beforeEach(() => {
    warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    useLanguageStore.getState().setLanguage('fr');
    mockSetItem.mockClear();
  });
  afterEach(() => warn.mockRestore());

  it.each(['es', 'zh'] as const)('setLanguage(%s) is refused and keeps the current language', (code) => {
    useLanguageStore.getState().setLanguage(code);
    expect(useLanguageStore.getState().language).toBe('fr');
    expect(mockSetItem).not.toHaveBeenCalled();
    expect(warn).toHaveBeenCalledWith(expect.stringContaining(`setLanguage('${code}')`));
  });

  it.each(['es', 'zh', 'zh-CN'])('i18n.changeLanguage(%s) is refused', async (code) => {
    await i18n.changeLanguage(code);
    expect(useLanguageStore.getState().language).toBe('fr');
    expect(mockSetItem).not.toHaveBeenCalled();
  });

  it.each(['es', 'zh-CN'])('useTranslation().i18n.changeLanguage(%s) is refused', (code) => {
    let changeLanguage!: (lang: string) => void;
    function Probe() {
      changeLanguage = useTranslation().i18n.changeLanguage;
      return null;
    }
    let r!: TestRenderer.ReactTestRenderer;
    act(() => {
      r = TestRenderer.create(React.createElement(Probe));
    });
    act(() => changeLanguage(code));
    expect(useLanguageStore.getState().language).toBe('fr');
    expect(mockSetItem).not.toHaveBeenCalled();
    act(() => r.unmount());
  });

  it('still accepts picker languages through the compat shim', async () => {
    await i18n.changeLanguage('en-CA');
    expect(useLanguageStore.getState().language).toBe('en');
    expect(mockSetItem).toHaveBeenCalledWith('@spinr_rider_language', 'en');
  });

  it('a refused pick does not block the app-start restore', async () => {
    useLanguageStore.setState({ language: 'en', hasUserChosen: false });
    const slowRead = deferred<string | null>();
    mockGetItem.mockReturnValue(slowRead.promise);

    const restoring = useLanguageStore.getState().hydrate();
    useLanguageStore.getState().setLanguage('es'); // refused
    slowRead.resolve('fr');
    await restoring;

    expect(useLanguageStore.getState().language).toBe('fr');
  });
});

describe('hydrate() storage failure', () => {
  it('logs the error and keeps the current language', async () => {
    const error = jest.spyOn(console, 'error').mockImplementation(() => {});
    const failure = new Error('AsyncStorage unavailable');
    mockGetItem.mockRejectedValue(failure);

    await useLanguageStore.getState().hydrate();

    expect(useLanguageStore.getState().language).toBe('en');
    expect(error).toHaveBeenCalledWith(expect.stringContaining('restore the saved language'), failure);
    error.mockRestore();
  });
});
