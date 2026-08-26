/**
 * app/legal.tsx — driver-audience legal-document viewer. Pins:
 *  - no type / type=tos / type=privacy all render the combined Privacy+ToS
 *    scroll view, fetching both docs in parallel from the driver-audience
 *    endpoint
 *  - any other valid doc type (e.g. reached from policies.tsx) renders in
 *    single-doc mode, fetching just that one type
 *  - the combined view falls back to the neutral driver-app placeholder
 *    strings when a response has no content; single-doc mode falls back to
 *    legalDocFallbackText(type)
 *  - a fetch failure in either mode falls back to the same placeholder text
 *    rather than crashing or showing a raw error
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, View } from 'react-native';

import LegalScreen from '../../app/legal';
import { legalDocFallbackText, legalDocTitle } from '@shared/config/legalDocs';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

let mockParams: Record<string, string> = {};
const mockBack = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack }),
  useLocalSearchParams: () => mockParams,
}));

const COLORS = {
  primary: '#EF4444', surface: '#FFF', text: '#111', textDim: '#666', border: '#E5E7EB',
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
  jest.useFakeTimers({ doNotFake: ['nextTick', 'queueMicrotask'] });
});

afterEach(() => {
  jest.useRealTimers();
});

describe('LegalScreen (driver-app)', () => {
  it('fetches both privacy and tos docs in parallel when no type param is given', async () => {
    mockFetch.mockImplementation((url: string) =>
      Promise.resolve({
        json: async () => ({ content: url.includes('privacy') ? 'Privacy body' : 'ToS body' }),
      }),
    );
    const renderer = await renderScreen();
    expect(mockFetch).toHaveBeenCalledWith(
      'https://api.spinr.test/legal-documents?audience=driver&type=privacy',
    );
    expect(mockFetch).toHaveBeenCalledWith(
      'https://api.spinr.test/legal-documents?audience=driver&type=tos',
    );
    const text = allText(renderer);
    expect(text).toContain('Privacy body');
    expect(text).toContain('ToS body');
  });

  it('falls back to the neutral placeholders when the combined response has no content', async () => {
    mockFetch.mockResolvedValue({ json: async () => ({}) });
    const renderer = await renderScreen();
    const text = allText(renderer);
    expect(text).toContain('No Privacy Policy has been added yet.');
    expect(text).toContain('No Terms of Service have been added yet.');
  });

  it('falls back to the neutral placeholders when the combined fetch throws', async () => {
    mockFetch.mockRejectedValue(new Error('network down'));
    const renderer = await renderScreen();
    const text = allText(renderer);
    expect(text).toContain('No Privacy Policy has been added yet.');
    expect(text).toContain('No Terms of Service have been added yet.');
  });

  it('renders single-doc mode for a non-tos/privacy type, fetching only that doc', async () => {
    mockParams = { type: 'community-guidelines' };
    mockFetch.mockResolvedValue({ json: async () => ({ content: 'Be nice' }) });
    const renderer = await renderScreen();
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledWith(
      'https://api.spinr.test/legal-documents?audience=driver&type=community-guidelines',
    );
    const text = allText(renderer);
    expect(text).toContain('Be nice');
    expect(text).toContain(legalDocTitle('community-guidelines'));
  });

  it('single-doc mode falls back to legalDocFallbackText when there is no content', async () => {
    mockParams = { type: 'community-guidelines' };
    mockFetch.mockResolvedValue({ json: async () => ({}) });
    const renderer = await renderScreen();
    expect(allText(renderer)).toContain(legalDocFallbackText('community-guidelines'));
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

  it('single-doc mode falls back to legalDocFallbackText when the fetch throws', async () => {
    mockParams = { type: 'community-guidelines' };
    mockFetch.mockRejectedValue(new Error('network down'));
    const renderer = await renderScreen();
    expect(allText(renderer)).toContain(legalDocFallbackText('community-guidelines'));
  });

  it('scrolls to the privacy section after loading when type=privacy', async () => {
    mockFetch.mockResolvedValue({ json: async () => ({ content: 'x' }) });
    mockParams = { type: 'privacy' };
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => {
      renderer = TestRenderer.create(<LegalScreen />);
      await flush();
    });
    // The scroll-to-section effect only fires after `loading` flips false
    // and is debounced 150ms — advancing timers here exercises that branch
    // without asserting on the native scrollTo call (react-test-renderer
    // doesn't expose ScrollView's imperative methods on host refs).
    act(() => {
      jest.advanceTimersByTime(150);
    });
    expect(allText(renderer)).toContain('x');
  });

  it('scrolls to the ToS section after loading when type=tos', async () => {
    mockFetch.mockResolvedValue({ json: async () => ({ content: 'x' }) });
    mockParams = { type: 'tos' };
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => {
      renderer = TestRenderer.create(<LegalScreen />);
      await flush();
    });
    act(() => {
      jest.advanceTimersByTime(150);
    });
    expect(allText(renderer)).toContain('x');
  });

  it('records section layout offsets via onLayout for the combined view', async () => {
    mockFetch.mockResolvedValue({ json: async () => ({ content: 'x' }) });
    const renderer = await renderScreen();
    const layoutViews = renderer.root
      .findAllByType(View)
      .filter((v) => typeof v.props.onLayout === 'function');
    expect(layoutViews.length).toBe(2); // privacy section + tos section
    act(() => {
      layoutViews[0].props.onLayout({ nativeEvent: { layout: { y: 42 } } });
      layoutViews[1].props.onLayout({ nativeEvent: { layout: { y: 420 } } });
    });
    // No crash and content still renders — the refs are internal, not
    // observable state, so this pins the onLayout branch executes cleanly.
    expect(allText(renderer)).toContain('x');
  });
});
