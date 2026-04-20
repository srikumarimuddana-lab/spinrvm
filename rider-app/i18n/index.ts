/**
 * i18n initialiser for the Spinr rider app (R-P1-10/R-P1-34).
 *
 * Stack: expo-localization + i18next + react-i18next
 *
 * Usage:
 *   import { t } from '@/i18n';
 *   // or via hook:
 *   const { t } = useTranslation();
 *
 * Language switching:
 *   import i18n from '@/i18n';
 *   await i18n.changeLanguage('fr-CA');
 *
 * Install (run once):
 *   npx expo install expo-localization
 *   yarn add i18next react-i18next
 */
import * as Localization from 'expo-localization';
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import enCA from './en-CA.json';
import frCA from './fr-CA.json';

const resources = {
  'en-CA': { translation: enCA },
  en: { translation: enCA },
  'fr-CA': { translation: frCA },
  fr: { translation: frCA },
};

// Resolve device locale from expo-localization, fall back to en-CA.
const deviceLocale = Localization.getLocales?.()?.[0]?.languageTag ?? 'en-CA';
const initialLocale = deviceLocale.startsWith('fr') ? 'fr-CA' : 'en-CA';

i18n
  .use(initReactI18next)
  .init({
    resources,
    lng: initialLocale,
    fallbackLng: 'en-CA',
    interpolation: {
      // React already handles escaping — disable for JSX output.
      escapeValue: false,
    },
    compatibilityJSON: 'v4',
  });

export default i18n;
export { useTranslation } from 'react-i18next';
