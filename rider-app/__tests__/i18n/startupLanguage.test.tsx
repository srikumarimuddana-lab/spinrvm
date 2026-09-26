/**
 * rider-app restores the saved language at app start (W7.1).
 *
 * Before this, useLanguageStore.hydrate() had no caller at all, so every cold
 * start was English even for a rider who had chosen French. app/_layout.tsx's
 * RootLayout now calls it once on mount.
 *
 * Two layers, because RootLayout itself (1,000+ lines, ~60 native/SDK
 * imports) has no render harness in this repo:
 *   1. a contract check that RootLayout calls hydrate() in a mount-only
 *      effect — fails if that call is removed;
 *   2. a cold-start simulation: the store at its initial 'en' state, a saved
 *      language in AsyncStorage, then exactly the call RootLayout makes, and a
 *      component rendered through the real useTranslation().
 */
import * as fs from 'fs';
import * as path from 'path';
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { Text } from 'react-native';

const mockGetItem = jest.fn();
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: (...args: unknown[]) => mockGetItem(...args),
  setItem: jest.fn(() => Promise.resolve()),
  removeItem: jest.fn(() => Promise.resolve()),
}));

import { useLanguageStore, useTranslation } from '../../i18n';

const LAYOUT = path.resolve(__dirname, '../../app/_layout.tsx');

describe('RootLayout restores the saved language on mount', () => {
  const source = fs.readFileSync(LAYOUT, 'utf8');
  const start = source.indexOf('function RootLayout()');
  const end = source.indexOf('\nfunction ', start + 1);
  const rootLayoutBody = source.slice(start, end === -1 ? undefined : end);

  it('imports the language store from ../i18n', () => {
    expect(source).toMatch(/import\s*\{[^}]*\buseLanguageStore\b[^}]*\}\s*from\s*'\.\.\/i18n'/);
  });

  it('calls useLanguageStore.getState().hydrate() in a mount-only effect inside RootLayout', () => {
    expect(start).toBeGreaterThan(-1);
    expect(rootLayoutBody).toMatch(
      /useEffect\(\s*\(\)\s*=>\s*\{\s*void\s+useLanguageStore\.getState\(\)\.hydrate\(\);?\s*\},\s*\[\s*\]\s*\)/,
    );
  });
});

describe('rider-app cold start with a saved language', () => {
  // Simulates a cold start: the store at its initial 'en' state, the saved
  // value in AsyncStorage, then the call RootLayout makes.
  async function coldStart(saved: string | null) {
    mockGetItem.mockReset();
    mockGetItem.mockResolvedValue(saved);
    useLanguageStore.setState({ language: 'en' });
    await useLanguageStore.getState().hydrate();

    function Probe() {
      const { t } = useTranslation();
      return <Text>{t('settings.language')}</Text>;
    }
    let r!: TestRenderer.ReactTestRenderer;
    act(() => {
      r = TestRenderer.create(<Probe />);
    });
    const shown = r.root.findByType(Text).props.children;
    act(() => r.unmount());
    return { language: useLanguageStore.getState().language, shown };
  }

  it('shows French when French was saved', async () => {
    await expect(coldStart('fr')).resolves.toEqual({ language: 'fr', shown: 'Langue' });
  });

  it.each(['es', 'zh'])('falls back to English when hidden language %s was saved', async (code) => {
    await expect(coldStart(code)).resolves.toEqual({ language: 'en', shown: 'Language' });
  });

  it('stays English when nothing was saved', async () => {
    await expect(coldStart(null)).resolves.toEqual({ language: 'en', shown: 'Language' });
  });
});
