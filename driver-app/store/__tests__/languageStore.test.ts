/**
 * Unit tests for languageStore — covers t, setLanguage, loadLanguage.
 *
 * Coverage target: P3-4 step 3 (raise driver-app coverage).
 */

jest.mock('react-native', () => ({
    Platform: { OS: 'ios' },
    Alert: { alert: jest.fn() },
    NativeModules: {},
}));

jest.mock('@react-native-async-storage/async-storage', () => ({
    getItem: jest.fn(() => Promise.resolve(null)),
    setItem: jest.fn(() => Promise.resolve()),
    removeItem: jest.fn(() => Promise.resolve()),
}));

jest.mock('../../i18n', () => ({
    translate: jest.fn((lang: string, key: string) => `${lang}:${key}`),
    getStoredLanguage: jest.fn(() => Promise.resolve('fr')),
    setStoredLanguage: jest.fn(() => Promise.resolve()),
}));

import { useLanguageStore } from '../languageStore';
import { translate, getStoredLanguage, setStoredLanguage } from '../../i18n';

const mockTranslate = translate as jest.Mock;
const mockGetStored = getStoredLanguage as jest.Mock;
const mockSetStored = setStoredLanguage as jest.Mock;

beforeEach(() => {
    jest.clearAllMocks();
    useLanguageStore.setState({ language: 'en', isLoading: true });
});

describe('languageStore — t', () => {
    it('delegates to translate with current language', () => {
        useLanguageStore.setState({ language: 'en' });
        mockTranslate.mockReturnValue('Hello');

        const result = useLanguageStore.getState().t('greeting');

        expect(mockTranslate).toHaveBeenCalledWith('en', 'greeting');
        expect(result).toBe('Hello');
    });

    it('uses the current language from state', () => {
        useLanguageStore.setState({ language: 'fr' });
        mockTranslate.mockReturnValue('Bonjour');

        const result = useLanguageStore.getState().t('greeting');

        expect(mockTranslate).toHaveBeenCalledWith('fr', 'greeting');
        expect(result).toBe('Bonjour');
    });
});

describe('languageStore — setLanguage', () => {
    it('calls setStoredLanguage and updates state', async () => {
        await useLanguageStore.getState().setLanguage('fr');

        expect(mockSetStored).toHaveBeenCalledWith('fr');
        expect(useLanguageStore.getState().language).toBe('fr');
    });

    it('can switch back to en', async () => {
        useLanguageStore.setState({ language: 'fr' });

        await useLanguageStore.getState().setLanguage('en');

        expect(useLanguageStore.getState().language).toBe('en');
    });
});

describe('languageStore — loadLanguage', () => {
    it('sets isLoading true during load then updates language from storage', async () => {
        mockGetStored.mockResolvedValue('fr');
        let resolveGet!: (lang: string) => void;
        mockGetStored.mockReturnValue(new Promise((res) => { resolveGet = res; }));

        const loadPromise = useLanguageStore.getState().loadLanguage();
        expect(useLanguageStore.getState().isLoading).toBe(true);

        resolveGet('fr');
        await loadPromise;

        expect(useLanguageStore.getState().language).toBe('fr');
        expect(useLanguageStore.getState().isLoading).toBe(false);
    });

    it('defaults to stored value from AsyncStorage', async () => {
        mockGetStored.mockResolvedValue('es');

        await useLanguageStore.getState().loadLanguage();

        expect(useLanguageStore.getState().language).toBe('es');
        expect(useLanguageStore.getState().isLoading).toBe(false);
    });
});
