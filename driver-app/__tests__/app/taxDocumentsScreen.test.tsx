/**
 * app/driver/tax-documents.tsx — driver T4A tax-document list. Pins:
 *  - GET /drivers/t4a/years on mount, mapped to one card per earned year
 *    (no synthesized "last 3 years" client-side, per the file's own
 *    stated fix for the $0.00-slip bug)
 *  - Tap-to-email: POST /drivers/t4a/:year/email, showing the backend's
 *    own message when present, else a generic "on its way" fallback
 *  - a load or email-send failure surfaces its own toast
 *  - the empty state renders when the driver has no earned years
 *  - the earnings/trip-count line formats currency and pluralizes "trip"
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text } from 'react-native';

jest.mock('@shared/components/ErrorBoundary', () => ({
  ErrorBoundary: ({ children }: any) => children,
}));

import TaxDocumentsScreen from '../../app/driver/tax-documents';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));
jest.mock('expo-linear-gradient', () => ({
  LinearGradient: ({ children }: any) => children,
}));

const mockBack = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack }),
}));

const COLORS = {
  primary: '#EF4444', background: '#FFF', surface: '#FFF', surfaceLight: '#F5F5F5',
  text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a), post: (...a: any[]) => mockApiPost(...a) },
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));

const mockShowToast = jest.fn();
jest.mock('../../hooks/useToast', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

const YEARS = [{ year: 2025, total_earnings: '12345.67', total_trips: 340 }];

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<TaxDocumentsScreen />);
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => JSON.stringify(t.props.children)).join(' | ');
}

beforeEach(() => {
  jest.clearAllMocks();
  mockApiGet.mockResolvedValue({ data: { years: [] } });
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
});

describe('TaxDocumentsScreen', () => {
  it('loads earned years and renders one card per year with formatted earnings', async () => {
    mockApiGet.mockResolvedValue({ data: { years: YEARS } });
    const r = await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/drivers/t4a/years');
    const text = allText(r);
    expect(text).toContain('T4A Slip');
    expect(text).toContain('["Tax Year ",2025]');
    expect(text).toContain('$12345.67');
    expect(text).toContain('340 trips');
  });

  it('singularizes "trip" for exactly one trip', async () => {
    mockApiGet.mockResolvedValue({ data: { years: [{ year: 2024, total_earnings: '10.00', total_trips: 1 }] } });
    const r = await renderScreen();
    expect(allText(r)).toContain('1 trip');
    expect(allText(r)).not.toContain('1 trips');
  });

  it('shows the empty state when there are no earned years', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('No tax documents yet');
  });

  it('emails the T4A summary with the backend message on success', async () => {
    mockApiGet.mockResolvedValue({ data: { years: YEARS } });
    mockApiPost.mockResolvedValue({ data: { message: 'Sent to jane@example.com' } });
    const r = await renderScreen();
    const emailBtn = r.root.findAllByType(TouchableOpacity)[1]; // [0] is the back button
    await act(async () => {
      await emailBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledWith('/drivers/t4a/2025/email');
    expect(mockShowToast).toHaveBeenCalledWith('success', 'Check Your Email', 'Sent to jane@example.com');
  });

  it('falls back to a generic "on its way" message when the backend sends none', async () => {
    mockApiGet.mockResolvedValue({ data: { years: YEARS } });
    mockApiPost.mockResolvedValue({ data: {} });
    const r = await renderScreen();
    const emailBtn = r.root.findAllByType(TouchableOpacity)[1];
    await act(async () => {
      await emailBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith(
      'success',
      'Check Your Email',
      'Your T4A summary for 2025 is on its way.',
    );
  });

  it('shows a toast when the email send fails', async () => {
    mockApiGet.mockResolvedValue({ data: { years: YEARS } });
    mockApiPost.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    const emailBtn = r.root.findAllByType(TouchableOpacity)[1];
    await act(async () => {
      await emailBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Send Failed', 'Could not send the document. Please try again.');
  });

  it('shows a toast when the initial load fails', async () => {
    mockApiGet.mockRejectedValue(new Error('network down'));
    await renderScreen();
    expect(mockShowToast).toHaveBeenCalledWith(
      'error',
      'Load Failed',
      'Could not load tax documents. Please try again.',
    );
  });

  it('navigates back when the back button is pressed', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => {
      backBtn.props.onPress();
    });
    expect(mockBack).toHaveBeenCalled();
  });
});
