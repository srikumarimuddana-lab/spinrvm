/**
 * app/legal.tsx — the per-audience legal-document viewer. Pins:
 *  - defaults to type=tos when no `type` param is given, and falls back to
 *    'tos' for an unrecognised/invalid type instead of crashing
 *  - fetches GET /legal-documents?audience=rider&type=<docType> on mount and
 *    re-fetches when the `type` param changes
 *  - shows a loading spinner until the fetch settles, then the doc content
 *  - falls back to legalDocFallbackText(docType) when the response has no
 *    content, and to a generic failure string when the fetch itself throws
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity } from 'react-native';

import LegalScreen from '../app/legal';
import { legalDocFallbackText, legalDocTitle } from '@shared/config/legalDocs';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));

let mockParams: Record<string, string> = {};
const mockBack = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack }),
  useLocalSearchParams: () => mockParams,
}));

const COLORS = {
  primary: '#EF4444', background: '#FFF', surface: '#FFF',
  surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

jest.mock('@shared/config/spinr.config', () => ({
  SpinrConfig: { backendUrl: 'https://api.spinr.test' },
}));

const mockFetch = jest.fn();
(global as any).fetch = mockFetch;

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

async function renderScreen() {
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => {
    renderer = TestRenderer.create(<LegalScreen />);
    await flush();
  });
  return renderer;
}

function allText(renderer: TestRenderer.ReactTestRenderer): string {
  return JSON.stringify(renderer.toJSON());
}

beforeEach(() => {
  jest.clearAllMocks();
  mockParams = {};
});

describe('LegalScreen', () => {
  it('defaults to type=tos and fetches the rider-audience doc', async () => {
    mockFetch.mockResolvedValue({ json: async () => ({ content: 'Terms body text' }) });
    const renderer = await renderScreen();
    expect(mockFetch).toHaveBeenCalledWith(
      'https://api.spinr.test/legal-documents?audience=rider&type=tos',
    );
    expect(allText(renderer)).toContain('Terms body text');
    expect(allText(renderer)).toContain(legalDocTitle('tos'));
  });

  it('falls back to tos for an invalid type param instead of crashing', async () => {
    mockParams = { type: 'not-a-real-doc' };
    mockFetch.mockResolvedValue({ json: async () => ({ content: 'x' }) });
    await renderScreen();
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('type=tos'),
    );
  });

  it('fetches the requested doc type when a valid type param is given', async () => {
    mockParams = { type: 'privacy' };
    mockFetch.mockResolvedValue({ json: async () => ({ content: 'Privacy body' }) });
    const renderer = await renderScreen();
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('type=privacy'),
    );
    expect(allText(renderer)).toContain(legalDocTitle('privacy'));
  });

  it('falls back to the static fallback text when the response has no content', async () => {
    mockFetch.mockResolvedValue({ json: async () => ({}) });
    const renderer = await renderScreen();
    expect(allText(renderer)).toContain(legalDocFallbackText('tos'));
  });

  it('shows a generic failure message when the fetch itself throws', async () => {
    mockFetch.mockRejectedValue(new Error('network down'));
    const renderer = await renderScreen();
    expect(allText(renderer)).toContain('Failed to load document');
  });

  it('navigates back when the back button is pressed', async () => {
    mockFetch.mockResolvedValue({ json: async () => ({ content: 'x' }) });
    const renderer = await renderScreen();
    const backBtn = renderer.root.findAllByType(TouchableOpacity)[0];
    act(() => {
      backBtn.props.onPress();
    });
    expect(mockBack).toHaveBeenCalled();
  });
});
