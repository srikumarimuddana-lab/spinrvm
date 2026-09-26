/**
 * Locale key parity for rider-app (W7.1, decision D3).
 *
 * rider-app/i18n/index.ts resolves each language through a chain of files:
 *   en → [en-CA.json, en.json]   fr → [fr-CA.json, fr.json]
 *   es → [es.json, en-CA.json]   zh → [zh.json, en-CA.json]
 * so the English source of truth is the union of en-CA.json and en.json.
 * This suite fails when:
 *   - any English key does not resolve in French, or fr-CA.json / fr.json
 *     falls behind its English counterpart, or a French string drops a
 *     placeholder ({{name}}, %{count}, {x}) the English string uses;
 *   - the settings language picker (`LANGUAGES`) offers a language whose own
 *     files are incomplete — that is what keeps es/zh hidden until they
 *     catch up;
 *   - a previously stored choice of a hidden language is still honoured.
 */
import enCA from '../../i18n/en-CA.json';
import en from '../../i18n/en.json';
import frCA from '../../i18n/fr-CA.json';
import fr from '../../i18n/fr.json';
import es from '../../i18n/es.json';
import zh from '../../i18n/zh.json';
import type { Language } from '../../i18n';

const mockGetItem = jest.fn();
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: (...args: unknown[]) => mockGetItem(...args),
  setItem: jest.fn(() => Promise.resolve()),
  removeItem: jest.fn(() => Promise.resolve()),
}));

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { LANGUAGES, t, useLanguageStore } = require('../../i18n') as typeof import('../../i18n');

type Tree = { [key: string]: string | Tree };

function flatten(tree: Tree, prefix = '', out: Record<string, string> = {}): Record<string, string> {
  for (const [k, v] of Object.entries(tree)) {
    const path = prefix ? `${prefix}.${k}` : k;
    if (typeof v === 'string') out[path] = v;
    else flatten(v, path, out);
  }
  return out;
}

function placeholders(s: string): string[] {
  return (s.match(/\{\{\s*\w+\s*\}\}|%\{\w+\}|\{\w+\}/g) ?? []).sort();
}

const flat = {
  enCA: flatten(enCA as Tree),
  en: flatten(en as Tree),
  frCA: flatten(frCA as Tree),
  fr: flatten(fr as Tree),
  es: flatten(es as Tree),
  zh: flatten(zh as Tree),
};
const english: Record<string, string> = { ...flat.en, ...flat.enCA };

// Each language's OWN files, without the en-CA fallback es/zh borrow at
// runtime — a key that only resolves via that fallback is untranslated.
const ownFiles: Record<Language, Record<string, string>> = {
  en: english,
  fr: { ...flat.fr, ...flat.frCA },
  es: flat.es,
  zh: flat.zh,
};

function missingKeys(lang: Language): string[] {
  return Object.keys(english).filter((k) => !(k in ownFiles[lang]));
}

function missingFrom(source: Record<string, string>, target: Record<string, string>): string[] {
  return Object.keys(source).filter((k) => !(k in target));
}

function placeholderDrift(source: Record<string, string>, target: Record<string, string>): string[] {
  return Object.keys(source).filter(
    (k) => k in target && placeholders(source[k]).join() !== placeholders(target[k]).join(),
  );
}

describe('rider-app locale parity', () => {
  it('every English key resolves to a French string at runtime', () => {
    const unresolved = Object.keys(english).filter((k) => t('fr', k) === k);
    expect(unresolved).toEqual([]);
  });

  it('fr-CA.json has every key en-CA.json has', () => {
    expect(missingFrom(flat.enCA, flat.frCA)).toEqual([]);
  });

  it('fr.json has every key en.json has', () => {
    expect(missingFrom(flat.en, flat.fr)).toEqual([]);
  });

  it('French files have no keys their English counterparts lack', () => {
    expect(missingFrom(flat.frCA, flat.enCA)).toEqual([]);
    expect(missingFrom(flat.fr, flat.en)).toEqual([]);
  });

  it('every French value is a non-empty string', () => {
    const blank = Object.entries(ownFiles.fr).filter(([, v]) => v.trim() === '').map(([k]) => k);
    expect(blank).toEqual([]);
  });

  it('French strings keep every placeholder the English string uses', () => {
    expect(placeholderDrift(flat.enCA, flat.frCA)).toEqual([]);
    expect(placeholderDrift(flat.en, flat.fr)).toEqual([]);
  });
});

describe('rider-app language picker', () => {
  it('offers only languages whose own locale files are complete', () => {
    const incomplete = LANGUAGES
      .map((l) => ({ code: l.code, missing: missingKeys(l.code).length }))
      .filter((l) => l.missing > 0);
    expect(incomplete).toEqual([]);
  });

  it('offers English and French, and not Spanish or Chinese while they are incomplete', () => {
    const codes = LANGUAGES.map((l) => l.code);
    expect(codes).toEqual(expect.arrayContaining(['en', 'fr']));
    for (const hidden of ['es', 'zh'] as Language[]) {
      if (missingKeys(hidden).length > 0) expect(codes).not.toContain(hidden);
    }
  });
});

describe('rider-app stored language (hydrate)', () => {
  beforeEach(() => {
    mockGetItem.mockReset();
    useLanguageStore.setState({ language: 'en' });
  });

  it('restores a stored choice the picker offers', async () => {
    mockGetItem.mockResolvedValue('fr');
    await useLanguageStore.getState().hydrate();
    expect(useLanguageStore.getState().language).toBe('fr');
  });

  it.each(['es', 'zh'])('stays on English for stored %s, which the picker no longer offers', async (code) => {
    mockGetItem.mockResolvedValue(code);
    await useLanguageStore.getState().hydrate();
    expect(useLanguageStore.getState().language).toBe('en');
  });
});
