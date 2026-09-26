/**
 * driver-app restores the saved language at app start (W7.1).
 *
 * Before this, store/languageStore's loadLanguage() was called only from
 * app/driver/settings.tsx, so a driver who had chosen French saw English after
 * every cold start until they opened Settings. driver-app/i18n/index.ts now
 * restores it when the module first loads (every translated screen imports it).
 *
 * Each case simulates a cold start: a fresh module registry, the saved value in
 * AsyncStorage, then the modules a screen would load — without rendering or
 * calling anything from Settings.
 */
const mockGetItem = jest.fn();
jest.mock('@react-native-async-storage/async-storage', () => ({
    getItem: (...args: unknown[]) => mockGetItem(...args),
    setItem: jest.fn(() => Promise.resolve()),
    removeItem: jest.fn(() => Promise.resolve()),
}));

const settle = () => new Promise((resolve) => setImmediate(resolve));

async function coldStart(saved: string | null) {
    jest.resetModules();
    mockGetItem.mockReset();
    mockGetItem.mockResolvedValue(saved);

    // Everything a screen needs, from the same fresh registry.
    /* eslint-disable @typescript-eslint/no-require-imports */
    const React = require('react') as typeof import('react');
    const TestRenderer = require('react-test-renderer') as typeof import('react-test-renderer');
    const { Text } = require('react-native') as typeof import('react-native');
    const { useLanguageStore } = require('../../store/languageStore') as typeof import('../../store/languageStore');
    /* eslint-enable @typescript-eslint/no-require-imports */

    expect(useLanguageStore.getState().language).toBe('en'); // store default before the restore settles
    await TestRenderer.act(async () => {
        await settle();
    });

    function Probe() {
        const { t } = useLanguageStore();
        return React.createElement(Text, null, t('common.cancel'));
    }
    let r!: import('react-test-renderer').ReactTestRenderer;
    TestRenderer.act(() => {
        r = TestRenderer.create(React.createElement(Probe));
    });
    const shown = r.root.findByType(Text).props.children;
    TestRenderer.act(() => r.unmount());
    return { language: useLanguageStore.getState().language, shown, reads: mockGetItem.mock.calls.length };
}

describe('driver-app cold start with a saved language', () => {
    it('shows French when French was saved, without opening Settings', async () => {
        const result = await coldStart('fr');
        expect(result).toEqual({ language: 'fr', shown: 'Annuler', reads: 1 });
    });

    it('falls back to English when the hidden language es was saved', async () => {
        const result = await coldStart('es');
        expect(result).toEqual({ language: 'en', shown: 'Cancel', reads: 1 });
    });

    it('stays English when nothing was saved', async () => {
        const result = await coldStart(null);
        expect(result).toEqual({ language: 'en', shown: 'Cancel', reads: 1 });
    });
});
