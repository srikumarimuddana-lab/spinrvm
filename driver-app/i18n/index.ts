import AsyncStorage from '@react-native-async-storage/async-storage';
import en from './en.json';
import fr from './fr.json';

export type Language = 'en' | 'fr';

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
};

export async function getStoredLanguage(): Promise<Language> {
    try {
        const stored = await AsyncStorage.getItem(LANGUAGE_KEY);
        if (stored === 'en' || stored === 'fr') {
            return stored;
        }
        return 'en';
    } catch {
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
    return getNestedValue(translations[language], key);
}

export { translations };
