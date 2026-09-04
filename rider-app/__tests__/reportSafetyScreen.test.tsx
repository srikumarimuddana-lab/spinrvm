/**
 * app/report-safety.tsx — broader coverage beyond
 * reportSafetyBackButton.test.tsx (pins only the back button's
 * accessibilityLabel).
 *
 * Pins:
 *  - handleSubmit rejects an empty/whitespace-only description with a
 *    toast, never calling the API
 *  - a successful submit POSTs the description, toasts success, and
 *    navigates back
 *  - a failed submit logs, toasts the backend's message, and re-enables
 *    the form (submitting flips back to false) instead of leaving it
 *    stuck disabled
 *  - the submit button's loading spinner and disabled state track
 *    `submitting`, and the input is not editable while submitting
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { ActivityIndicator, TextInput, TouchableOpacity } from 'react-native';

import ReportSafetyScreen from '../app/report-safety';

const mockBack = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack }),
}));
jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));

const COLORS = {
  primary: '#EF4444', primaryDark: '#D32F2F', background: '#FFF', surface: '#FFF',
  surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockShowToast = jest.fn();
jest.mock('../store/toastStore', () => ({ showToast: (...a: any[]) => mockShowToast(...a) }));

const mockApiPost = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { post: (...a: any[]) => mockApiPost(...a) },
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<ReportSafetyScreen />);
  });
  return renderer!;
}

// Located by testID (set on the shared Button component report-safety.tsx
// now renders) rather than by matching label text — the shared Button shows
// a spinner in place of the label while loading, so there is no "Submitting"
// text to match during that state any more.
function submitButton(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(TouchableOpacity).find((n) =>
    n.props.testID === 'report-safety-submit-button'
  )!;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockApiPost.mockResolvedValue({ data: {} });
});

afterEach(() => {
  act(() => { renderer?.unmount(); });
  renderer = null;
});

describe('ReportSafetyScreen', () => {
  it('rejects an empty description without calling the API', async () => {
    const r = await renderScreen();
    await act(async () => { await submitButton(r).props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Description Required', 'Please describe the safety issue before submitting.', 'warning');
    expect(mockApiPost).not.toHaveBeenCalled();
  });

  it('rejects a whitespace-only description', async () => {
    const r = await renderScreen();
    const input = r.root.findByType(TextInput);
    act(() => { input.props.onChangeText('   '); });
    await act(async () => { await submitButton(r).props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Description Required', 'Please describe the safety issue before submitting.', 'warning');
    expect(mockApiPost).not.toHaveBeenCalled();
  });

  it('submits, toasts success, and navigates back', async () => {
    const r = await renderScreen();
    const input = r.root.findByType(TextInput);
    act(() => { input.props.onChangeText('A driver ran a red light.'); });
    await act(async () => { await submitButton(r).props.onPress(); await flush(); });
    expect(mockApiPost).toHaveBeenCalledWith('/tickets/safety-report', { description: 'A driver ran a red light.' });
    expect(mockShowToast).toHaveBeenCalledWith(
      'Report Submitted',
      'Your safety report has been submitted. Our trust and safety team will review it immediately.',
      'success',
    );
    expect(mockBack).toHaveBeenCalled();
  });

  it('shows a loading spinner and disables the input while the request is in flight', async () => {
    let resolveFn!: (v: any) => void;
    mockApiPost.mockReturnValue(new Promise((resolve) => { resolveFn = resolve; }));
    const r = await renderScreen();
    const input = r.root.findByType(TextInput);
    act(() => { input.props.onChangeText('Speeding driver.'); });
    act(() => { submitButton(r).props.onPress(); });
    expect(r.root.findByType(TextInput).props.editable).toBe(false);
    expect(submitButton(r).props.disabled).toBe(true);
    // Shared Button swaps the label for a spinner while loading (design-audit
    // follow-up — was a "Submit Report" -> "Submitting..." text swap before).
    expect(submitButton(r).findAllByType(ActivityIndicator).length).toBe(1);
    await act(async () => { resolveFn({ data: {} }); await flush(); });
  });

  it('a failed submit toasts the backend message and re-enables the form', async () => {
    mockApiPost.mockRejectedValue(new Error('rate limited'));
    const r = await renderScreen();
    const input = r.root.findByType(TextInput);
    act(() => { input.props.onChangeText('A driver was reckless.'); });
    await act(async () => { await submitButton(r).props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Submit Failed', 'Failed to submit report. Please try again.', 'danger');
    expect(mockBack).not.toHaveBeenCalled();
    // Re-enabled, not stuck disabled.
    expect(submitButton(r).props.disabled).toBe(false);
    expect(r.root.findByType(TextInput).props.editable).toBe(true);
  });
});
