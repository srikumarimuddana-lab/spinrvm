/**
 * app/crc-consent.tsx — purpose-built PIPEDA consent screen for the
 * Criminal Record Check / Vulnerable Sector Check. Pins:
 *  - loads the published consent doc + GET /drivers/crc-consent status in
 *    parallel on mount
 *  - the checkbox gate: "I Consent & Continue" is disabled until checked,
 *    unless the driver already has a current consent on file (no checkbox
 *    shown at all, submit becomes "Continue" and just navigates back)
 *  - checking the box and submitting fires POST /drivers/crc-consent, then
 *    a success toast and router.back()
 *  - a load or submit failure surfaces a toast, never a silent/broken state
 *  - the "already consented" banner shows the recorded date when present
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text } from 'react-native';

import CrcConsentScreen from '../../app/crc-consent';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

const mockBack = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack }),
}));

const COLORS = {
  primary: '#EF4444', surface: '#FFF', surfaceLight: '#F5F5F5',
  text: '#111', textDim: '#666', textSecondary: '#333', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

jest.mock('@shared/config/spinr.config', () => ({
  SpinrConfig: { backendUrl: 'https://api.spinr.test' },
}));

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a), post: (...a: any[]) => mockApiPost(...a) },
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));

const mockShowToast = jest.fn();
jest.mock('../../hooks/useToast', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

const mockFetch = jest.fn();
(global as any).fetch = mockFetch;

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<CrcConsentScreen />);
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return JSON.stringify(r.toJSON());
}

// TouchableOpacity is itself a composite whose rendered host View is a
// separate node in the tree, so `.parent` off a nested <Text> lands on that
// inner View (no `disabled`/`onPress`), not the TouchableOpacity carrying
// them. Find the TouchableOpacity node directly by its own subtree text.
function findButtonByText(r: TestRenderer.ReactTestRenderer, text: string) {
  return r.root
    .findAllByType(TouchableOpacity)
    .find((node) =>
      node
        .findAllByType(Text)
        .some((t) => JSON.stringify(t.props.children).includes(text)),
    )!;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockFetch.mockResolvedValue({ json: async () => ({ content: 'Consent form text' }) });
  mockApiGet.mockResolvedValue({ data: { is_current: false, consented_at: null } });
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
});

describe('CrcConsentScreen', () => {
  it('loads the consent doc and status in parallel, then shows the checkbox gate', async () => {
    const r = await renderScreen();
    expect(mockFetch).toHaveBeenCalledWith(
      'https://api.spinr.test/legal-documents?audience=driver&type=background-check-consent',
    );
    expect(mockApiGet).toHaveBeenCalledWith('/drivers/crc-consent');
    const text = allText(r);
    expect(text).toContain('Consent form text');
    expect(text).toContain('I Consent & Continue');
  });

  it('disables the submit button until the checkbox is checked', async () => {
    const r = await renderScreen();
    const submitBtn = findButtonByText(r, 'I Consent & Continue');
    expect(submitBtn.props.disabled).toBe(true);

    const checkboxRow = r.root.findByProps({ accessibilityRole: 'checkbox' });
    await act(async () => {
      checkboxRow.props.onPress();
      await flush();
    });
    expect(submitBtn.props.disabled).toBe(false);
  });

  it('submits, shows a success toast, and navigates back once checked', async () => {
    mockApiPost.mockResolvedValue({ data: {} });
    const r = await renderScreen();
    const checkboxRow = r.root.findByProps({ accessibilityRole: 'checkbox' });
    await act(async () => {
      checkboxRow.props.onPress();
      await flush();
    });
    const submitBtn = findButtonByText(r, 'I Consent & Continue');
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledWith('/drivers/crc-consent');
    expect(mockShowToast).toHaveBeenCalledWith('success', 'Consent recorded', 'Thank you.');
    expect(mockBack).toHaveBeenCalled();
  });

  it('shows a toast and does not navigate when submit fails', async () => {
    mockApiPost.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    const checkboxRow = r.root.findByProps({ accessibilityRole: 'checkbox' });
    await act(async () => {
      checkboxRow.props.onPress();
      await flush();
    });
    const submitBtn = findButtonByText(r, 'I Consent & Continue');
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Could not submit', 'Please try again.');
    expect(mockBack).not.toHaveBeenCalled();
  });

  it('hides the checkbox and shows "Continue" when consent is already current', async () => {
    mockApiGet.mockResolvedValue({ data: { is_current: true, consented_at: '2026-01-15T00:00:00Z' } });
    const r = await renderScreen();
    expect(() => r.root.findByProps({ accessibilityRole: 'checkbox' })).toThrow();
    const text = allText(r);
    expect(text).toContain('Continue');
    expect(text).toContain('You already gave this consent on');
  });

  it('"Continue" just navigates back without calling POST when already current', async () => {
    mockApiGet.mockResolvedValue({ data: { is_current: true, consented_at: null } });
    const r = await renderScreen();
    const continueBtn = findButtonByText(r, 'Continue');
    await act(async () => {
      await continueBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).not.toHaveBeenCalled();
    expect(mockBack).toHaveBeenCalled();
  });

  it('shows a toast when the initial load fails', async () => {
    mockApiGet.mockRejectedValue(new Error('network down'));
    await renderScreen();
    expect(mockShowToast).toHaveBeenCalledWith(
      'error',
      'Could not load',
      'Please check your connection and try again.',
    );
  });

  it('navigates back when the header back button is pressed', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => {
      backBtn.props.onPress();
    });
    expect(mockBack).toHaveBeenCalled();
  });
});
