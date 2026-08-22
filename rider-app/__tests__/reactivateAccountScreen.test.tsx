/**
 * app/reactivate-account.tsx — self-serve reactivation before the 7-year
 * deletion retention ceiling (Saskatchewan Transportation Act / PIPEDA
 * lawful-retention carve-out). Pins:
 *  - missing reactivationToken -> toast + redirect to /login, no API call
 *  - POST /auth/reactivate on "Reactivate my account", persisting tokens
 *    via setTokens and merging the returned user into the store
 *  - routes to /(tabs) when the reactivated user is profile-complete, else
 *    /profile-setup
 *  - a 410 response means the account was hard-deleted -> distinct toast +
 *    redirect to /login (not the generic failure toast)
 *  - "Keep it deleted" just redirects to /login, no API call
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';

import ReactivateAccountScreen from '../app/reactivate-account';
import { useAuthStore } from '@shared/store/authStore';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));

let mockParams: Record<string, string | undefined> = {};
const mockReplace = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ replace: mockReplace }),
  useLocalSearchParams: () => mockParams,
}));

const COLORS = {
  primary: '#EF4444', surface: '#FFF', text: '#111', textDim: '#666',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockApiPost = jest.fn();
jest.mock('@shared/api/client', () => {
  const actual = jest.requireActual('@shared/api/client');
  return {
    __esModule: true,
    ...actual,
    default: { post: (...args: any[]) => mockApiPost(...args) },
  };
});

const mockShowToast = jest.fn();
jest.mock('../store/toastStore', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

const mockSetTokens = jest.fn().mockResolvedValue(undefined);
const mockInitialize = jest.fn().mockResolvedValue(undefined);

jest.mock('@shared/store/authStore', () => {
  const { create: createStore } = require('zustand');
  const useAuthStore = createStore(() => ({
    user: null,
    setTokens: (...a: any[]) => mockSetTokens(...a),
    initialize: (...a: any[]) => mockInitialize(...a),
  }));
  return { useAuthStore };
});

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

async function renderScreen() {
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => {
    renderer = TestRenderer.create(<ReactivateAccountScreen />);
    await flush();
  });
  return renderer;
}

async function tapReactivate(renderer: TestRenderer.ReactTestRenderer) {
  const btn = renderer.root.findByProps({ accessibilityLabel: 'Reactivate my account' });
  await act(async () => {
    await btn.props.onPress();
    await flush();
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  mockParams = { reactivationToken: 'tok-123', deletionScheduledAt: '2026-09-01T00:00:00Z' };
});

describe('ReactivateAccountScreen', () => {
  it('redirects to /login without calling the API when the reactivation token is missing', async () => {
    mockParams = {};
    const renderer = await renderScreen();
    await tapReactivate(renderer);
    expect(mockApiPost).not.toHaveBeenCalled();
    expect(mockShowToast).toHaveBeenCalledWith(
      'Reactivation Failed',
      'Missing reactivation token. Please sign in again.',
      'danger',
    );
    expect(mockReplace).toHaveBeenCalledWith('/login');
  });

  it('reactivates, persists tokens, and routes to /(tabs) for a profile-complete user', async () => {
    mockApiPost.mockResolvedValue({
      data: {
        token: 'access-1',
        refresh_token: 'refresh-1',
        expires_in: 900,
        user: { id: 'u1', profile_complete: true },
      },
    });
    const renderer = await renderScreen();
    await tapReactivate(renderer);
    expect(mockApiPost).toHaveBeenCalledWith('/auth/reactivate', { reactivation_token: 'tok-123' });
    expect(mockSetTokens).toHaveBeenCalledWith('access-1', 'refresh-1', 900);
    expect(useAuthStore.getState().user).toEqual({ id: 'u1', profile_complete: true });
    expect(mockShowToast).toHaveBeenCalledWith('Welcome back', 'Your account has been reactivated.', 'success');
    expect(mockReplace).toHaveBeenCalledWith('/(tabs)');
  });

  it('routes to /profile-setup when the reactivated user has an incomplete profile', async () => {
    mockApiPost.mockResolvedValue({
      data: { token: 'access-1', user: { id: 'u1', profile_complete: false, first_name: '', last_name: '', email: '' } },
    });
    const renderer = await renderScreen();
    await tapReactivate(renderer);
    expect(mockReplace).toHaveBeenCalledWith('/profile-setup');
  });

  it('re-initializes the auth store when the response has no user payload', async () => {
    mockApiPost.mockResolvedValue({ data: { token: 'access-1' } });
    const renderer = await renderScreen();
    await tapReactivate(renderer);
    expect(mockInitialize).toHaveBeenCalled();
    expect(mockReplace).toHaveBeenCalledWith('/profile-setup');
  });

  it('shows the permanent-deletion toast and redirects to /login on a 410', async () => {
    mockApiPost.mockRejectedValue({ response: { status: 410 } });
    const renderer = await renderScreen();
    await tapReactivate(renderer);
    expect(mockShowToast).toHaveBeenCalledWith(
      'Account Deleted',
      'This account has been permanently deleted. Please create a new one.',
      'danger',
    );
    expect(mockReplace).toHaveBeenCalledWith('/login');
  });

  it('shows a generic failure toast (not the 410 copy) for any other error', async () => {
    mockApiPost.mockRejectedValue({ name: 'SpinrApiError', message: 'boom' });
    const renderer = await renderScreen();
    await tapReactivate(renderer);
    const call = mockShowToast.mock.calls.find((c) => c[0] === 'Reactivation Failed');
    expect(call).toBeTruthy();
    expect(call?.[1]).not.toContain('Account Deleted');
  });

  it('"Keep it deleted" redirects to /login without calling the API', async () => {
    const renderer = await renderScreen();
    const btn = renderer.root.findByProps({ accessibilityLabel: 'Keep my account scheduled for deletion' });
    act(() => {
      btn.props.onPress();
    });
    expect(mockApiPost).not.toHaveBeenCalled();
    expect(mockReplace).toHaveBeenCalledWith('/login');
  });

  it('renders the formatted deletion date when deletionScheduledAt is a valid date', async () => {
    const renderer = await renderScreen();
    const text = JSON.stringify(renderer.toJSON());
    expect(text).toContain('September 1, 2026');
  });

  it('falls back to "soon" when deletionScheduledAt is missing or invalid', async () => {
    mockParams = { reactivationToken: 'tok-123', deletionScheduledAt: 'not-a-date' };
    const renderer = await renderScreen();
    const text = JSON.stringify(renderer.toJSON());
    expect(text).toContain('scheduled for deletion');
    expect(text).toContain(' soon');
    expect(text).not.toContain('September');
  });
});
