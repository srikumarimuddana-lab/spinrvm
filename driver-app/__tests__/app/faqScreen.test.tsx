/**
 * app/driver/faq.tsx — driver Help Center FAQ list. Pins:
 *  - GET /faqs?audience=driver on mount, attaching lat/lng only when
 *    foreground location permission is already granted (never prompts)
 *  - a location lookup failure is swallowed -- FAQs still load globally
 *  - search filters by question or answer text (case-insensitive)
 *  - the empty state renders when a search matches nothing
 *  - categories group in CATEGORY_ORDER first, then alphabetically for
 *    unknown categories, each item's category Title-Cased
 *  - tapping a question toggles its answer open/closed
 *  - the fetch error path is swallowed (logs, doesn't crash) and still
 *    stops the loading spinner
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text } from 'react-native';

import FaqScreen from '../../app/driver/faq';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

const mockPush = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: mockPush }),
}));

jest.mock('../../components/ScreenHeader', () => ({
  ScreenHeader: ({ children, rightAction }: any) => (
    <>
      {rightAction}
      {children}
    </>
  ),
}));

const COLORS = {
  primary: '#EF4444', background: '#FFF', surface: '#FFF', text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockGetForegroundPermissionsAsync = jest.fn();
const mockGetLastKnownPositionAsync = jest.fn();
jest.mock('expo-location', () => ({
  getForegroundPermissionsAsync: (...a: any[]) => mockGetForegroundPermissionsAsync(...a),
  getLastKnownPositionAsync: (...a: any[]) => mockGetLastKnownPositionAsync(...a),
}));

const mockApiGet = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a) },
}));

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

const FAQS = [
  { id: 'f1', question: 'How do I go online?', answer: 'Tap the Go Online button.', category: 'Onboarding', sort_order: 1 },
  { id: 'f2', question: 'When do I get paid?', answer: 'Weekly, every Monday.', category: 'Payments', sort_order: 1 },
  { id: 'f3', question: 'What is a WAV?', answer: 'Wheelchair-accessible vehicle.', category: 'zzz-custom', sort_order: 1 },
];

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<FaqScreen />);
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => JSON.stringify(t.props.children)).join(' | ');
}

beforeEach(() => {
  jest.clearAllMocks();
  mockGetForegroundPermissionsAsync.mockResolvedValue({ granted: false });
  mockApiGet.mockResolvedValue({ data: FAQS });
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
});

describe('FaqScreen (driver-app)', () => {
  it('fetches FAQs for the driver audience without a location when permission is not granted', async () => {
    await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/faqs?audience=driver');
    expect(mockGetLastKnownPositionAsync).not.toHaveBeenCalled();
  });

  it('attaches lat/lng when foreground location permission is already granted', async () => {
    mockGetForegroundPermissionsAsync.mockResolvedValue({ granted: true });
    mockGetLastKnownPositionAsync.mockResolvedValue({ coords: { latitude: 50.45, longitude: -104.6 } });
    await renderScreen();
    const call = mockApiGet.mock.calls.find((c) => c[0].startsWith('/faqs?'));
    expect(call[0]).toContain('lat=50.45');
    expect(call[0]).toContain('lng=-104.6');
  });

  it('swallows a location lookup failure and still loads FAQs globally', async () => {
    mockGetForegroundPermissionsAsync.mockRejectedValue(new Error('permission check failed'));
    const r = await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/faqs?audience=driver');
    expect(allText(r)).toContain('How do I go online?');
  });

  it('groups by known category order, then alphabetically for unknown categories', async () => {
    const r = await renderScreen();
    const text = allText(r);
    const onboardingIdx = text.indexOf('Onboarding');
    const paymentsIdx = text.indexOf('Payments');
    const customIdx = text.indexOf('Zzz-custom');
    expect(onboardingIdx).toBeGreaterThan(-1);
    expect(onboardingIdx).toBeLessThan(paymentsIdx);
    expect(paymentsIdx).toBeLessThan(customIdx);
  });

  it('filters by search text across question and answer, case-insensitively', async () => {
    const r = await renderScreen();
    const input = r.root.findByProps({ placeholder: 'Search questions...' });
    act(() => {
      input.props.onChangeText('WHEELCHAIR');
    });
    const text = allText(r);
    expect(text).toContain('What is a WAV?');
    expect(text).not.toContain('How do I go online?');
  });

  it('shows the empty state when a search matches nothing', async () => {
    const r = await renderScreen();
    const input = r.root.findByProps({ placeholder: 'Search questions...' });
    act(() => {
      input.props.onChangeText('nonexistent topic xyz');
    });
    expect(allText(r)).toContain('No results found');
  });

  it('toggles a question open to reveal its answer', async () => {
    const r = await renderScreen();
    expect(allText(r)).not.toContain('Tap the Go Online button.');
    const questionRow = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('How do I go online?')))!;
    act(() => {
      questionRow.props.onPress();
    });
    expect(allText(r)).toContain('Tap the Go Online button.');
    act(() => {
      questionRow.props.onPress();
    });
    expect(allText(r)).not.toContain('Tap the Go Online button.');
  });

  it('does not crash and stops loading when the fetch fails', async () => {
    mockApiGet.mockRejectedValue(new Error('network down'));
    const r = await renderScreen();
    expect(allText(r)).toContain('No results found');
  });

  it('navigates to /driver/help when the header chat button is pressed', async () => {
    const r = await renderScreen();
    const chatBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => {
      chatBtn.props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith('/driver/help');
  });
});
