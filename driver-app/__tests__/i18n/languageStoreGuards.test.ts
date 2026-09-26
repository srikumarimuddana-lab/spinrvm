/**
 * driver-app language store guards (W7.1 edge-case review).
 *
 * 1. Load-vs-pick race: the app-start restore (loadLanguage(), kicked off by
 *    i18n/index.ts) reads AsyncStorage asynchronously. If that read resolves
 *    after the driver has picked a language, it used to overwrite the fresh
 *    choice with the stale stored value. A pick now always wins.
 * 2. Write-side guard: setLanguage() refuses any code the picker doesn't offer
 *    (the hidden, incomplete 'es'), keeping the current language.
 */
const mockGetItem = jest.fn();
const mockSetItem = jest.fn();
jest.mock('@react-native-async-storage/async-storage', () => ({
    getItem: (...args: unknown[]) => mockGetItem(...args),
    setItem: (...args: unknown[]) => mockSetItem(...args),
    removeItem: jest.fn(() => Promise.resolve()),
}));

type Store = typeof import('../../store/languageStore').useLanguageStore;

const settle = () => new Promise((resolve) => setImmediate(resolve));

function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((r) => {
        resolve = r;
    });
    return { promise, resolve };
}

/** Fresh module registry = a cold start; the i18n import kicks off the restore. */
function coldStartStore(): Store {
    jest.resetModules();
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return (require('../../store/languageStore') as typeof import('../../store/languageStore')).useLanguageStore;
}

beforeEach(() => {
    mockGetItem.mockReset();
    mockSetItem.mockReset();
    mockSetItem.mockResolvedValue(undefined);
});

describe('load-vs-pick race', () => {
    it('keeps a language picked while the app-start restore is still reading storage', async () => {
        const slowRead = deferred<string | null>();
        mockGetItem.mockReturnValue(slowRead.promise);

        const store = coldStartStore();
        await settle(); // restore has started and is waiting on getItem
        expect(mockGetItem).toHaveBeenCalledTimes(1);

        await store.getState().setLanguage('fr'); // driver picks French
        expect(store.getState().language).toBe('fr');

        slowRead.resolve('en'); // stale stored value arrives late
        await settle();

        expect(store.getState().language).toBe('fr');
        expect(store.getState().isLoading).toBe(false);
        expect(mockSetItem).toHaveBeenCalledWith('@spinr_language', 'fr');
    });

    it('keeps the pick even when the read resolves before the pick finishes saving', async () => {
        const slowRead = deferred<string | null>();
        const slowWrite = deferred<void>();
        mockGetItem.mockReturnValue(slowRead.promise);
        mockSetItem.mockReturnValue(slowWrite.promise);

        const store = coldStartStore();
        await settle();

        const picking = store.getState().setLanguage('fr');
        slowRead.resolve('en'); // read lands while the write is still pending
        await settle();
        slowWrite.resolve();
        await picking;

        expect(store.getState().language).toBe('fr');
    });

    it('still applies the restore when nothing was picked', async () => {
        mockGetItem.mockResolvedValue('fr');
        const store = coldStartStore();
        await settle();
        expect(store.getState().language).toBe('fr');
    });
});

describe('setLanguage write-side guard', () => {
    let warn: jest.SpyInstance;
    beforeEach(() => {
        warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    });
    afterEach(() => warn.mockRestore());

    it('refuses a language the picker does not offer and keeps the current one', async () => {
        mockGetItem.mockResolvedValue(null);
        const store = coldStartStore();
        await settle();
        await store.getState().setLanguage('fr');
        mockSetItem.mockClear();

        await store.getState().setLanguage('es');

        expect(store.getState().language).toBe('fr');
        expect(mockSetItem).not.toHaveBeenCalled();
        expect(warn).toHaveBeenCalledWith(expect.stringContaining("setLanguage('es')"));
    });

    it('does not let a refused pick block the app-start restore', async () => {
        const slowRead = deferred<string | null>();
        mockGetItem.mockReturnValue(slowRead.promise);
        const store = coldStartStore();
        await settle();

        await store.getState().setLanguage('es'); // refused
        slowRead.resolve('fr');
        await settle();

        expect(store.getState().language).toBe('fr');
    });
});
