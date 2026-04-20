import AsyncStorage from '@react-native-async-storage/async-storage';
import { create } from 'zustand';
import en from './en.json';
import fr from './fr.json';
import es from './es.json';
import zh from './zh.json';

export type Language = 'en' | 'fr' | 'es' | 'zh';

export const LANGUAGES: { code: Language; name: string; nativeName: string; flag: string }[] = [
  { code: 'en', name: 'English',              nativeName: 'English',   flag: '🇨🇦' },
  { code: 'fr', name: 'French',               nativeName: 'Français',  flag: '🇫🇷' },
  { code: 'es', name: 'Spanish',              nativeName: 'Español',   flag: '🇪🇸' },
  { code: 'zh', name: 'Chinese (Simplified)', nativeName: '简体中文',   flag: '🇨🇳' },
];

const LANGUAGE_KEY = '@spinr_rider_language';

type TranslationValue = string | { [key: string]: TranslationValue };
type Translations = { [key: string]: TranslationValue };

const translations: Record<Language, Translations> = {
  en: en as Translations,
  fr: fr as Translations,
  es: es as Translations,
  zh: zh as Translations,
};

function getNestedValue(obj: Translations, path: string): string {
  const keys = path.split('.');
  let current: TranslationValue = obj;
  for (const key of keys) {
    if (current && typeof current === 'object' && key in current) {
      current = (current as Record<string, TranslationValue>)[key];
    } else {
      return path;
    }
  }
  return typeof current === 'string' ? current : path;
}

export function t(language: Language, key: string): string {
  return getNestedValue(translations[language], key);
}

// ── Zustand store so any component can subscribe to language changes ──────────
interface LanguageState {
  language: Language;
  setLanguage: (lang: Language) => void;
  hydrate: () => Promise<void>;
}

export const useLanguageStore = create<LanguageState>((set) => ({
  language: 'en',

  setLanguage: (lang) => {
    set({ language: lang });
    AsyncStorage.setItem(LANGUAGE_KEY, lang).catch(() => {});
  },

  hydrate: async () => {
    try {
      const stored = await AsyncStorage.getItem(LANGUAGE_KEY);
      if (stored === 'en' || stored === 'fr' || stored === 'es' || stored === 'zh') {
        set({ language: stored });
      }
    } catch {}
  },
}));
