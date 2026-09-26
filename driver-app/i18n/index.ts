import AsyncStorage from '@react-native-async-storage/async-storage';
import en from './en.json';
import fr from './fr.json';
import es from './es.json';

export type Language = 'en' | 'fr' | 'es';

// The languages the settings picker offers. Only complete languages belong
// here: Spanish ('es') is hidden until es.json covers every key en.json has
// (decision D3, W7.1). es.json and the 'es' code stay so re-enabling it is a
// one-line change, gated by __tests__/i18n/localeParity.test.ts.
export const languages: { code: Language; name: string; nativeName: string }[] = [
    { code: 'en', name: 'English', nativeName: 'English' },
    { code: 'fr', name: 'French', nativeName: 'Français' },
];

const LANGUAGE_KEY = '@spinr_language';

type TranslationValue = string | { [key: string]: TranslationValue };
type Translations = { [key: string]: TranslationValue };

const translations: Record<Language, Translations> = {
    en: en as Translations,
    fr: fr as Translations,
    es: es as Translations,
};

export async function getStoredLanguage(): Promise<Language> {
    try {
        const stored = await AsyncStorage.getItem(LANGUAGE_KEY);
        // Only honour a stored choice the picker still offers. A driver who
        // picked Spanish before it was hidden gets English (the complete
        // fallback) instead of a language they can no longer see or re-select.
        // The stored value itself is left untouched.
        if (languages.some((l) => l.code === stored)) {
            return stored as Language;
        }
        return 'en';
    } catch (error) {
        console.error('Failed to read stored language:', error);
        return 'en';
    }
}

export async function setStoredLanguage(language: Language): Promise<void> {
    try {
        await AsyncStorage.setItem(LANGUAGE_KEY, language);
    } catch (error) {
        console.error('Failed to store language:', error);
    }
}

export function getNestedValue(obj: Translations, path: string): string {
    const keys = path.split('.');
    let current: TranslationValue = obj;

    for (const key of keys) {
        if (current && typeof current === 'object' && key in current) {
            current = (current as { [key: string]: TranslationValue })[key];
        } else {
            return path;
        }
    }

    return typeof current === 'string' ? current : path;
}

export function translate(language: Language, key: string): string {
    const value = getNestedValue(translations[language], key);
    // Fall back to English before surfacing the raw key. A missing entry in a
    // non-English locale used to render the literal dotted path to the driver
    // (e.g. "heatmap.airport.zone"), which is worse than showing English.
    if (value === key && language !== 'en') {
        return getNestedValue(translations.en, key);
    }
    return value;
}

// Convenience for non-component callsites (alert helpers, error pipelines)
// that don't have a `useLanguageStore()` hook in scope. Reads the current
// language from the zustand store and returns the translation, or the
// `fallback` (or the key itself) when no entry matches.
//
// Imported lazily to avoid the i18n module depending on the store at
// module-evaluation time (the store imports from this file).
export function tKey(key: string, fallback?: string): string {
    let language: Language = 'en';
    try {
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        const { useLanguageStore } = require('../store/languageStore') as {
            useLanguageStore: { getState: () => { language: Language } };
        };
        language = useLanguageStore.getState().language;
    } catch {
        // store not yet initialised — fall back to English
    }

    const keys = key.split('.');
    let current: TranslationValue = translations[language];
    for (const k of keys) {
        if (current && typeof current === 'object' && k in current) {
            current = (current as { [key: string]: TranslationValue })[k];
        } else {
            return fallback ?? key;
        }
    }
    return typeof current === 'string' ? current : (fallback ?? key);
}

export { translations };

// Restore the driver's saved language at app start. This is the only caller
// of loadLanguage(): Settings used to call it on mount, so a cold start was
// English until the driver opened Settings. Every translated screen and the
// alert helpers import this module, so this runs before the first translated
// string renders; the launch splash still covers that first screen while the
// single AsyncStorage read settles. Deferred to a microtask because
// store/languageStore imports this module: both have finished evaluating by
// then. A saved language the picker no longer offers resolves to English
// (getStoredLanguage()), and a language the driver picks while the read is in
// flight wins over it (hasUserChosen in the store).
Promise.resolve()
    .then(() => {
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        const { useLanguageStore } = require('../store/languageStore') as {
            useLanguageStore: { getState: () => { loadLanguage: () => Promise<void> } };
        };
        return useLanguageStore.getState().loadLanguage();
    })
    .catch((error) => {
        console.error('Failed to restore the saved language at startup:', error);
    });
