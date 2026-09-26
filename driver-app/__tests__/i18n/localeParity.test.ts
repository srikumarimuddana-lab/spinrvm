/**
 * Locale key parity for driver-app (W7.1, decision D3).
 *
 * driver-app loads en.json, fr.json and es.json (driver-app/i18n/index.ts);
 * English is the fallback, so en.json is the source of truth for which keys
 * exist. This suite fails when:
 *   - fr.json is missing any key en.json has, or drops/renames a
 *     placeholder ({{name}}, %{count}, {x}) an English string uses;
 *   - the settings language picker (`languages`) offers a language whose
 *     file is incomplete — that is what keeps Spanish hidden until es.json
 *     catches up;
 *   - a previously stored choice of a hidden language is still honoured.
 */
import en from '../../i18n/en.json';
import fr from '../../i18n/fr.json';
import es from '../../i18n/es.json';
import type { Language } from '../../i18n';

const mockGetItem = jest.fn();
jest.mock('@react-native-async-storage/async-storage', () => ({
    getItem: (...args: unknown[]) => mockGetItem(...args),
    setItem: jest.fn(() => Promise.resolve()),
    removeItem: jest.fn(() => Promise.resolve()),
}));

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { languages, getStoredLanguage } = require('../../i18n') as typeof import('../../i18n');

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

const english = flatten(en as Tree);
const files: Record<Language, Record<string, string>> = {
    en: english,
    fr: flatten(fr as Tree),
    es: flatten(es as Tree),
};

function missingKeys(lang: Language): string[] {
    return Object.keys(english).filter((k) => !(k in files[lang]));
}

describe('driver-app locale parity', () => {
    it('fr.json has every key en.json has', () => {
        expect(missingKeys('fr')).toEqual([]);
    });

    it('fr.json has no keys en.json lacks (no stale or misspelled keys)', () => {
        expect(Object.keys(files.fr).filter((k) => !(k in english))).toEqual([]);
    });

    it('every French value is a non-empty string', () => {
        const blank = Object.entries(files.fr).filter(([, v]) => v.trim() === '').map(([k]) => k);
        expect(blank).toEqual([]);
    });

    it('fr.json keeps every placeholder the English string uses', () => {
        const drift = Object.keys(english).filter(
            (k) => k in files.fr && placeholders(english[k]).join() !== placeholders(files.fr[k]).join(),
        );
        expect(drift).toEqual([]);
    });
});

describe('driver-app language picker', () => {
    it('offers only languages whose locale file is complete', () => {
        const incomplete = languages
            .map((l) => ({ code: l.code, missing: missingKeys(l.code).length }))
            .filter((l) => l.missing > 0);
        expect(incomplete).toEqual([]);
    });

    it('offers English and French, and does not offer Spanish while it is incomplete', () => {
        const codes = languages.map((l) => l.code);
        expect(codes).toEqual(expect.arrayContaining(['en', 'fr']));
        if (missingKeys('es').length > 0) expect(codes).not.toContain('es');
    });
});

describe('driver-app stored language', () => {
    beforeEach(() => mockGetItem.mockReset());

    it('keeps a stored choice the picker offers', async () => {
        mockGetItem.mockResolvedValue('fr');
        await expect(getStoredLanguage()).resolves.toBe('fr');
    });

    it('falls back to English for a stored language the picker no longer offers', async () => {
        mockGetItem.mockResolvedValue('es');
        await expect(getStoredLanguage()).resolves.toBe('en');
    });

    it('defaults to English when nothing is stored', async () => {
        mockGetItem.mockResolvedValue(null);
        await expect(getStoredLanguage()).resolves.toBe('en');
    });
});
