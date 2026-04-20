import AsyncStorage from '@react-native-async-storage/async-storage';
import { create } from 'zustand';

// Translation files — two parallel sets:
//   en-CA / fr-CA (from main): full snake_case key set for existing screens
//   en / fr / es / zh (P4): camelCase key set for new P4 screens + es/zh languages
import enCA from './en-CA.json';
import frCA from './fr-CA.json';
import en from './en.json';
import fr from './fr.json';
import es from './es.json';
import zh from './zh.json';

export type Language = 'en' | 'fr' | 'es' | 'zh';

export const LANGUAGES: { code: Language; name: string; nativeName: string; flag: string }[] = [
  { code: 'en', name: 'English',              nativeName: 'English',  flag: '🇨🇦' },
  { code: 'fr', name: 'French',               nativeName: 'Français', flag: '🇫🇷' },
  { code: 'es', name: 'Spanish',              nativeName: 'Español',  flag: '🇪🇸' },
  { code: 'zh', name: 'Chinese (Simplified)', nativeName: '简体中文',  flag: '🇨🇳' },
];

const LANGUAGE_KEY = '@spinr_rider_language';

type TranslationValue = string | { [key: string]: TranslationValue };
type Translations = { [key: string]: TranslationValue };

// Lookup order per language:
//   en → en-CA first (snake_case keys for existing screens), then en (camelCase for P4 screens)
//   fr → fr-CA first, then fr
//   es → es first, en-CA as fallback for untranslated keys
//   zh → zh first, en-CA as fallback for untranslated keys
const translationSources: Record<Language, Translations[]> = {
  en: [enCA as Translations, en as Translations],
  fr: [frCA as Translations, fr as Translations],
  es: [es as Translations, enCA as Translations],
  zh: [zh as Translations, enCA as Translations],
};

function getNestedValue(obj: Translations, path: string): string | undefined {
  const keys = path.split('.');
  let current: TranslationValue = obj;
  for (const key of keys) {
    if (current && typeof current === 'object' && key in current) {
      current = (current as Record<string, TranslationValue>)[key];
    } else {
      return undefined;
    }
  }
  return typeof current === 'string' ? current : undefined;
}

function lookupKey(lang: Language, key: string): string {
  for (const source of translationSources[lang]) {
    const value = getNestedValue(source, key);
    if (value !== undefined) return value;
  }
  return key;
}

// Named t() with explicit language argument (for direct use outside components)
export function t(language: Language, key: string): string {
  return lookupKey(language, key);
}

// ── Zustand store ─────────────────────────────────────────────────────────────
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
        set({ language: stored as Language });
      }
    } catch {}
  },
}));

// ── useTranslation shim (compatibility replacement for react-i18next) ─────────
// Allows screens imported from main that call useTranslation() to work without
// requiring i18next/react-i18next packages (not installed in this project).
export function useTranslation() {
  const { language } = useLanguageStore();
  return {
    t: (key: string) => lookupKey(language, key),
    i18n: {
      language,
      changeLanguage: (lang: string) => {
        const map: Record<string, Language> = {
          'en': 'en', 'en-CA': 'en',
          'fr': 'fr', 'fr-CA': 'fr',
          'es': 'es',
          'zh': 'zh', 'zh-CN': 'zh',
        };
        const mapped = map[lang];
        if (mapped) useLanguageStore.getState().setLanguage(mapped);
      },
    },
  };
}

// ── i18n compat object (replaces i18next default export) ─────────────────────
const i18n = {
  changeLanguage: (lang: string) => {
    const map: Record<string, Language> = {
      'en': 'en', 'en-CA': 'en',
      'fr': 'fr', 'fr-CA': 'fr',
      'es': 'es',
      'zh': 'zh', 'zh-CN': 'zh',
    };
    const mapped = map[lang];
    if (mapped) useLanguageStore.getState().setLanguage(mapped);
    return Promise.resolve();
  },
  language: 'en',
};

export default i18n;
