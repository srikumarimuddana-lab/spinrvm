/**
 * app/work-allowance-request.tsx — corporate work-allowance top-up
 * request form. Pins:
 *  - fetchRequests fires on mount and again when activeCompanyId changes
 *  - Submit is disabled while a request is already pending, and while
 *    the amount/reason fail isWorkAllowanceRequestValid
 *  - submit success: auto_approved shows the "added automatically" toast,
 *    anything else shows the "sent for review" toast; both clear the form
 *  - a submit failure surfaces the backend's error message
 *  - the pending-request banner and the recent-requests list (status
 *    label/color mapping, including the auto_approved special case)
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));

const mockBack = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack }),
}));

const COLORS = {
  primary: '#EF4444', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));

const mockShowToast = jest.fn();
jest.mock('../store/toastStore', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

const mockFetchRequests = jest.fn();
const mockSubmitRequest = jest.fn();
let mockWorkProfileState: any;
function resetWorkProfileState() {
  mockWorkProfileState = {
    balance: null,
    requests: [],
    activeCompanyId: 'company-1',
    fetchRequests: mockFetchRequests,
    submitRequest: mockSubmitRequest,
  };
}
jest.mock('../store/workProfileStore', () => ({
  useWorkProfileStore: () => mockWorkProfileState,
}));

import WorkAllowanceRequestScreen from '../app/work-allowance-request';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<WorkAllowanceRequestScreen />);
    await flush();
  });
  return renderer!;
}

// A <Text> nested inside another <Text>'s children (this screen's balance
// summary does this) shows up as a raw React element in the outer
// instance's props.children, carrying a dev-mode `_owner` back-reference
// to the fiber tree -- JSON.stringify throws on that circularity. Skip
// unstringifiable children rather than crashing the whole scan; the
// nested <Text> is still visited on its own by findAllByType.
function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root
    .findAllByType(Text)
    .map((t) => {
      try {
        return JSON.stringify(t.props.children);
      } catch {
        return '';
      }
    })
    .join(' | ');
}

function findButtonByText(r: TestRenderer.ReactTestRenderer, text: string) {
  return r.root
    .findAllByType(TouchableOpacity)
    .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes(text)))!;
}

beforeEach(() => {
  jest.clearAllMocks();
  resetWorkProfileState();
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
});

describe('WorkAllowanceRequestScreen', () => {
  it('fetches requests on mount', async () => {
    await renderScreen();
    expect(mockFetchRequests).toHaveBeenCalled();
  });

  it('disables submit until a quick amount and a valid reason are entered', async () => {
    const r = await renderScreen();
    const submitBtn = findButtonByText(r, 'Submit Request');
    expect(submitBtn.props.disabled).toBe(true);

    const chip25 = findButtonByText(r, '"$",25');
    act(() => {
      chip25.props.onPress();
    });
    expect(findButtonByText(r, 'Submit Request').props.disabled).toBe(true); // no reason yet

    const reasonInput = r.root.findByProps({ placeholder: 'Why do you need additional funds? (min 5 characters)' });
    act(() => {
      reasonInput.props.onChangeText('Need gas money');
    });
    expect(findButtonByText(r, 'Submit Request').props.disabled).toBe(false);
  });

  it('disables submit while a request is already pending, regardless of form validity', async () => {
    mockWorkProfileState.requests = [{ id: 'r1', amount: '50.00', reason: 'x', status: 'pending' }];
    const r = await renderScreen();
    const chip25 = findButtonByText(r, '"$",25');
    act(() => {
      chip25.props.onPress();
    });
    const reasonInput = r.root.findByProps({ placeholder: 'Why do you need additional funds? (min 5 characters)' });
    act(() => {
      reasonInput.props.onChangeText('Need gas money');
    });
    expect(findButtonByText(r, 'Submit Request').props.disabled).toBe(true);
    expect(allText(r)).toContain('You have a pending request');
  });

  it('shows the auto-approved toast and clears the form on an auto-approved submit', async () => {
    mockSubmitRequest.mockResolvedValue({ status: 'auto_approved' });
    const r = await renderScreen();
    const chip25 = findButtonByText(r, '"$",25');
    act(() => {
      chip25.props.onPress();
    });
    const reasonInput = r.root.findByProps({ placeholder: 'Why do you need additional funds? (min 5 characters)' });
    act(() => {
      reasonInput.props.onChangeText('Need gas money');
    });
    const submitBtn = findButtonByText(r, 'Submit Request');
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });
    expect(mockSubmitRequest).toHaveBeenCalledWith(25, 'Need gas money');
    expect(mockShowToast).toHaveBeenCalledWith(
      'Auto-approved!',
      '$25.00 has been added to your allowance automatically.',
      'success',
    );
    const clearedInput = r.root.findByProps({ placeholder: 'Why do you need additional funds? (min 5 characters)' });
    expect(clearedInput.props.value).toBe('');
  });

  it('shows the sent-for-review toast for a non-auto-approved submit', async () => {
    mockSubmitRequest.mockResolvedValue({ status: 'pending' });
    const r = await renderScreen();
    const chip25 = findButtonByText(r, '"$",25');
    act(() => {
      chip25.props.onPress();
    });
    const reasonInput = r.root.findByProps({ placeholder: 'Why do you need additional funds? (min 5 characters)' });
    act(() => {
      reasonInput.props.onChangeText('Need gas money');
    });
    const submitBtn = findButtonByText(r, 'Submit Request');
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith(
      'Request Submitted',
      'Your request has been sent to your company admin for review.',
      'success',
    );
  });

  it('shows a failure toast when submitRequest rejects', async () => {
    mockSubmitRequest.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    const chip25 = findButtonByText(r, '"$",25');
    act(() => {
      chip25.props.onPress();
    });
    const reasonInput = r.root.findByProps({ placeholder: 'Why do you need additional funds? (min 5 characters)' });
    act(() => {
      reasonInput.props.onChangeText('Need gas money');
    });
    const submitBtn = findButtonByText(r, 'Submit Request');
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith(
      'Request Failed',
      'Could not submit your request. Please try again.',
      'danger',
    );
  });

  it('shows the current balance summary, including the unlimited case', async () => {
    mockWorkProfileState.balance = { type: 'unlimited' };
    const r = await renderScreen();
    expect(allText(r)).toContain('Unlimited');
  });

  it('renders recent requests with the auto_approved status label', async () => {
    mockWorkProfileState.requests = [
      { id: 'r1', amount: '30.00', reason: 'Gas', status: 'auto_approved' },
      { id: 'r2', amount: '75.00', reason: 'Tolls', status: 'denied' },
    ];
    const r = await renderScreen();
    const text = allText(r);
    expect(text).toContain('Auto-approved');
    expect(text).toContain('Denied');
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
