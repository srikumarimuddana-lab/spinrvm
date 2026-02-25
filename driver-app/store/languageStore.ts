import { create } from 'zustand';
import { Language, getStoredLanguage, setStoredLanguage, translate } from '../i18n';

interface LanguageState {
    language: Language;
    isLoading: boolean;
    t: (key: string) => string;
    setLanguage: (language: Language) => Promise<void>;
    loadLanguage: () => Promise<void>;
}

export const useLanguageStore = create<LanguageState>((set, get) => ({
    language: 'en',
    isLoading: true,

    t: (key: string) => {
        const { language } = get();
        return translate(language, key);
    },

    setLanguage: async (language: Language) => {
        await setStoredLanguage(language);
        set({ language });
    },

    loadLanguage: async () => {
        set({ isLoading: true });
        const language = await getStoredLanguage();
        set({ language, isLoading: false });
    },
}));
