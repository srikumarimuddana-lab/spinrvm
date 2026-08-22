/**
 * app/appeal.tsx — driver-facing side of the deactivation/suspension
 * appeals flow. Pins:
 *  - GET /drivers/appeals on mount; a pending appeal replaces the submit
 *    form entirely with a "under review" banner
 *  - Submit is disabled until the message is non-empty (after trimming)
 *  - POST /drivers/appeals on submit, clears the draft, shows a success
 *    toast, and reloads the appeal list
 *  - a 400 response means the driver isn't currently appeal-eligible ->
 *    shown inline as `ineligible` text, not a toast
 *  - any other submit failure surfaces a toast instead
 *  - appeal history renders every entry with its status label and, when
 *    present, the admin's response note
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text } from 'react-native';

import AppealScreen from '../../app/appeal';

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
  text: '#111', textDim: '#666', textSecondary: '#333', border: '#E5E7EB', error: '#DC2626',
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
  await Promise.resolve();
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<AppealScreen />);
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return JSON.stringify(r.toJSON());
}

function findButtonByText(r: TestRenderer.ReactTestRenderer, text: string) {
  return r.root
    .findAllByType(TouchableOpacity)
    .find((node) =>
      node.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes(text)),
    )!;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockApiGet.mockResolvedValue({ data: [] });
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
});

describe('AppealScreen', () => {
  it('loads appeals on mount and shows the submit form when none are pending', async () => {
    const r = await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/drivers/appeals');
    expect(allText(r)).toContain('Submit an appeal');
  });

  it('replaces the form with an "under review" banner when an appeal is pending', async () => {
    mockApiGet.mockResolvedValue({
      data: [{ id: 'a1', appeal_type: 'suspension', driver_message: 'x', status: 'pending', admin_note: null, created_at: '2026-01-01T00:00:00Z', resolved_at: null }],
    });
    const r = await renderScreen();
    const text = allText(r);
    expect(text).toContain('Your appeal is under review');
    expect(text).not.toContain('Submit an appeal');
  });

  it('disables submit until the message is non-empty', async () => {
    const r = await renderScreen();
    const submitBtn = findButtonByText(r, 'Submit Appeal');
    expect(submitBtn.props.disabled).toBe(true);

    const input = r.root.findByProps({ placeholder: 'Explain your situation...' });
    act(() => {
      input.props.onChangeText('   ');
    });
    expect(findButtonByText(r, 'Submit Appeal').props.disabled).toBe(true);

    act(() => {
      input.props.onChangeText('I was wrongly suspended');
    });
    expect(findButtonByText(r, 'Submit Appeal').props.disabled).toBe(false);
  });

  it('submits, clears the draft, shows a success toast, and reloads', async () => {
    mockApiPost.mockResolvedValue({ data: {} });
    const r = await renderScreen();
    const input = r.root.findByProps({ placeholder: 'Explain your situation...' });
    act(() => {
      input.props.onChangeText('I was wrongly suspended');
    });
    mockApiGet.mockClear();
    const submitBtn = findButtonByText(r, 'Submit Appeal');
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledWith('/drivers/appeals', { message: 'I was wrongly suspended' });
    expect(mockShowToast).toHaveBeenCalledWith('success', 'Appeal submitted', "We'll review it and get back to you.");
    expect(mockApiGet).toHaveBeenCalledWith('/drivers/appeals');
    expect(r.root.findByProps({ placeholder: 'Explain your situation...' }).props.value).toBe('');
  });

  it('shows the ineligible message inline (not a toast) on a 400', async () => {
    mockApiPost.mockRejectedValue({ response: { status: 400 }, message: 'Not currently under review' });
    const r = await renderScreen();
    const input = r.root.findByProps({ placeholder: 'Explain your situation...' });
    act(() => {
      input.props.onChangeText('appeal text');
    });
    const submitBtn = findButtonByText(r, 'Submit Appeal');
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });
    expect(allText(r)).toContain("isn't currently under review");
    expect(mockShowToast).not.toHaveBeenCalled();
  });

  it('shows a toast for a non-400 submit failure', async () => {
    mockApiPost.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    const input = r.root.findByProps({ placeholder: 'Explain your situation...' });
    act(() => {
      input.props.onChangeText('appeal text');
    });
    const submitBtn = findButtonByText(r, 'Submit Appeal');
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Could not submit', 'Please try again.');
  });

  it('renders appeal history with the admin note when present', async () => {
    mockApiGet.mockResolvedValue({
      data: [
        {
          id: 'a1', appeal_type: 'suspension', driver_message: 'my appeal text',
          status: 'denied', admin_note: 'Insufficient evidence provided',
          created_at: '2026-01-01T00:00:00Z', resolved_at: '2026-01-05T00:00:00Z',
        },
      ],
    });
    const r = await renderScreen();
    const text = allText(r);
    expect(text).toContain('Your appeal history');
    expect(text).toContain('Denied');
    expect(text).toContain('my appeal text');
    expect(text).toContain('Insufficient evidence provided');
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
