/**
 * app/index.tsx — the driver app's cold-start routing gate. Pins:
 *  - waits for isInitialized + a ready navigationRef before routing at all
 *  - no token/user -> /login
 *  - authenticated but profile incomplete -> logout then /login
 *  - authenticated + profile complete -> checks /consent/status and routes
 *    to /legacy-consent-notice when needs_notice, else /driver/ (fail-open
 *    to /driver/ if the check itself throws)
 *  - sessionRecoverable shows the "Reconnecting…" UI instead of navigating,
 *    and only offers "Sign in instead" after ATTEMPTS_BEFORE_ESCAPE retries
 *  - the retry interval calls initialize() on a 5s cadence while recoverable,
 *    serialised so overlapping intervals never stack concurrent calls
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';

import Index from '../../app/index';

let appStateListener: ((state: string) => void) | null = null;
jest.mock('react-native/Libraries/AppState/AppState', () => ({
  __esModule: true,
  default: {
    addEventListener: (event: string, cb: (state: string) => void) => {
      if (event === 'change') appStateListener = cb;
      return { remove: jest.fn() };
    },
    currentState: 'active',
  },
}));

let mockNavReady = true;
const mockReplace = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ replace: mockReplace }),
  useNavigationContainerRef: () => ({ isReady: () => mockNavReady }),
}));

const mockApiGet = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a) },
}));

jest.mock('@shared/utils/logger', () => ({
  createLogger: () => ({ info: jest.fn(), warn: jest.fn(), error: jest.fn() }),
}));

// Plain mutable-state hand-rolled mock (this app's __mocks__/@shared/store/
// authStore.js convention), not zustand — a `function` declaration (unlike
// `const`) is hoisted with its full body, so referencing it from the
// jest.mock factory below is safe regardless of source order, and the
// mockLogout/mockInitialize spies are only ever read lazily (inside a
// render or a later callback), never at factory-eval time.
const mockLogout = jest.fn().mockResolvedValue(undefined);
const mockInitialize = jest.fn().mockResolvedValue(undefined);
let authState: any;
function resetAuthState() {
  authState = {
    isInitialized: false,
    token: null,
    user: null,
    logout: mockLogout,
    sessionRecoverable: false,
    initialize: mockInitialize,
  };
}
function mockUseAuthStore(selector?: (s: any) => any) {
  return selector ? selector(authState) : authState;
}
mockUseAuthStore.getState = () => authState;
mockUseAuthStore.setState = (partial: any) => {
  authState = { ...authState, ...(typeof partial === 'function' ? partial(authState) : partial) };
};

jest.mock('@shared/store/authStore', () => ({
  __esModule: true,
  useAuthStore: mockUseAuthStore,
}));
const mockedAuthStore = mockUseAuthStore;

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<Index />);
    await flush();
  });
  return renderer!;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockNavReady = true;
  resetAuthState();
  jest.useFakeTimers({ doNotFake: ['nextTick', 'queueMicrotask'] });
});

afterEach(async () => {
  await act(async () => {
    renderer?.unmount();
    await flush();
  });
  renderer = null;
  jest.useRealTimers();
});

describe('Index (driver-app cold start routing)', () => {
  it('does not navigate until initialized and the nav container is ready', async () => {
    mockNavReady = false;
    mockedAuthStore.setState({ isInitialized: true, token: 't', user: { profile_complete: true } });
    await renderScreen();
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it('routes to /login when there is no token/user', async () => {
    mockedAuthStore.setState({ isInitialized: true, token: null, user: null });
    await renderScreen();
    expect(mockReplace).toHaveBeenCalledWith('/login');
  });

  it('logs out and routes to /login when the profile is incomplete', async () => {
    mockedAuthStore.setState({
      isInitialized: true,
      token: 't',
      user: { profile_complete: false, first_name: '', last_name: '', email: '' },
    });
    await renderScreen();
    expect(mockLogout).toHaveBeenCalled();
    expect(mockReplace).toHaveBeenCalledWith('/login');
  });

  it('treats a fully-populated name/email as profile-complete even without the flag', async () => {
    mockApiGet.mockResolvedValue({ data: { needs_notice: false } });
    mockedAuthStore.setState({
      isInitialized: true,
      token: 't',
      user: { profile_complete: false, first_name: 'A', last_name: 'B', email: 'a@b.com' },
    });
    await renderScreen();
    expect(mockLogout).not.toHaveBeenCalled();
    expect(mockReplace).toHaveBeenCalledWith('/driver/');
  });

  it('routes to /legacy-consent-notice when the consent check reports needs_notice', async () => {
    mockApiGet.mockResolvedValue({ data: { needs_notice: true } });
    mockedAuthStore.setState({ isInitialized: true, token: 't', user: { profile_complete: true } });
    await renderScreen();
    expect(mockReplace).toHaveBeenCalledWith('/legacy-consent-notice');
  });

  it('routes to /driver/ when the consent check reports no notice needed', async () => {
    mockApiGet.mockResolvedValue({ data: { needs_notice: false } });
    mockedAuthStore.setState({ isInitialized: true, token: 't', user: { profile_complete: true } });
    await renderScreen();
    expect(mockReplace).toHaveBeenCalledWith('/driver/');
  });

  it('fails open to /driver/ when the consent check itself throws', async () => {
    mockApiGet.mockRejectedValue(new Error('network down'));
    mockedAuthStore.setState({ isInitialized: true, token: 't', user: { profile_complete: true } });
    await renderScreen();
    expect(mockReplace).toHaveBeenCalledWith('/driver/');
  });

  it('shows the reconnecting UI (no navigation) while sessionRecoverable is true', async () => {
    mockedAuthStore.setState({
      isInitialized: true,
      token: 't',
      user: { profile_complete: true },
      sessionRecoverable: true,
    });
    const r = await renderScreen();
    expect(mockReplace).not.toHaveBeenCalled();
    expect(JSON.stringify(r.toJSON())).toContain('Reconnecting');
  });

  it('does not offer "Sign in instead" before ATTEMPTS_BEFORE_ESCAPE retries', async () => {
    mockedAuthStore.setState({
      isInitialized: true,
      token: 't',
      user: { profile_complete: true },
      sessionRecoverable: true,
    });
    const r = await renderScreen();
    expect(() => r.root.findByProps({ accessibilityLabel: 'Sign in instead' })).toThrow();
  });

  it('retries initialize() on the 5s interval while recoverable, and offers escape after 3 attempts', async () => {
    mockedAuthStore.setState({
      isInitialized: true,
      token: 't',
      user: { profile_complete: true },
      sessionRecoverable: true,
    });
    const r = await renderScreen();

    for (let i = 0; i < 3; i++) {
      await act(async () => {
        jest.advanceTimersByTime(5000);
        await flush();
      });
    }
    expect(mockInitialize).toHaveBeenCalledTimes(3);
    const escapeBtn = r.root.findByProps({ accessibilityLabel: 'Sign in instead' });
    expect(escapeBtn).toBeTruthy();
  });

  it('"Sign in instead" logs out and routes to /login', async () => {
    mockedAuthStore.setState({
      isInitialized: true,
      token: 't',
      user: { profile_complete: true },
      sessionRecoverable: true,
    });
    const r = await renderScreen();
    for (let i = 0; i < 3; i++) {
      await act(async () => {
        jest.advanceTimersByTime(5000);
        await flush();
      });
    }
    const escapeBtn = r.root.findByProps({ accessibilityLabel: 'Sign in instead' });
    await act(async () => {
      await escapeBtn.props.onPress();
      await flush();
    });
    expect(mockLogout).toHaveBeenCalled();
    expect(mockReplace).toHaveBeenCalledWith('/login');
  });

  it('retries immediately when the app resumes to active while recoverable', async () => {
    mockedAuthStore.setState({
      isInitialized: true,
      token: 't',
      user: { profile_complete: true },
      sessionRecoverable: true,
    });
    await renderScreen();
    mockInitialize.mockClear();
    expect(appStateListener).toBeTruthy();
    await act(async () => {
      appStateListener?.('active');
      await flush();
    });
    expect(mockInitialize).toHaveBeenCalledTimes(1);
  });

  it('does not retry on resume when the session is no longer recoverable', async () => {
    mockedAuthStore.setState({
      isInitialized: true,
      token: 't',
      user: { profile_complete: true },
      sessionRecoverable: true,
    });
    await renderScreen();
    mockedAuthStore.setState({ sessionRecoverable: false });
    mockInitialize.mockClear();
    await act(async () => {
      appStateListener?.('active');
      await flush();
    });
    expect(mockInitialize).not.toHaveBeenCalled();
  });
});
