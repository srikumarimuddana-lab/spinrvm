import { create } from 'zustand';
import { Language, getStoredLanguage, setStoredLanguage, translate, languages } from '../i18n';

interface LanguageState {
    language: Language;
    isLoading: boolean;
    /**
     * True once the driver has picked a language this session. A restore of
     * the saved value (loadLanguage) that finishes afterwards must not
     * overwrite that fresh choice with the stale stored one.
     */
    hasUserChosen: boolean;
    t: (key: string) => string;
    setLanguage: (language: Language) => Promise<void>;
    loadLanguage: () => Promise<void>;
}

export const useLanguageStore = create<LanguageState>((set, get) => ({
    language: 'en',
    isLoading: true,
    hasUserChosen: false,

    t: (key: string) => {
        const { language } = get();
        return translate(language, key);
    },

    setLanguage: async (language: Language) => {
        // Only languages the picker offers can be chosen; hidden, incomplete
        // ones (es) are refused and the current language is kept.
        if (!languages.some((l) => l.code === language)) {
            if (__DEV__) console.warn(`[i18n] Ignoring setLanguage('${language}'): not offered in the language picker.`);
            return;
        }
        set({ hasUserChosen: true });
        await setStoredLanguage(language);
        set({ language });
    },

    loadLanguage: async () => {
        set({ isLoading: true });
        const language = await getStoredLanguage();
        if (get().hasUserChosen) {
            // The driver picked a language while the read was in flight (or
            // earlier this session): keep their choice.
            set({ isLoading: false });
            return;
        }
        set({ language, isLoading: false });
    },
}));
