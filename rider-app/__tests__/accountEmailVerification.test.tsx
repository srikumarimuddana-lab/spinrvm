/**
 * N14 (ACTION_ITEMS.md): rider-app UI for self-serve email verification.
 *
 * Pins the Account screen's "Personal Info" email row:
 *  - shows a "Verify" pill (linking to /verify-email) when the account has
 *    an email but it isn't verified
 *  - shows a "Verified" pill instead once `email_verified` is true
 *  - shows neither pill when there's no email on file at all
 *  - the focus-triggered /auth/me refetch must not wipe a locally-set
 *    `email_verified` flag (see comment on the second describe block)
 *
 * Purely additive: nothing here changes existing Phone/Email/Gender rows,
 * and no other screen reads `email_verified`.
 *
 * Uses react-test-renderer directly, mirroring privacySettingsToggles.test.tsx
 * (the pinned @testing-library/react-native v12 can't probe RN 0.86 host
 * components). `@shared/store/authStore` is mocked with a *real* zustand
 * store (not a plain object) so account.tsx's `useAuthStore.setState(...)`
 * calls behave exactly like production and drive a real re-render.
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { create } from 'zustand';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));

const mockPush = jest.fn();
const mockBack = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: mockPush, back: mockBack }),
}));

let mockFocusEffectEnabled = false;
jest.mock('expo-router/react-navigation', () => {
  const ReactActual = require('react');
  return {
    // Real behaviour is opt-in per test (`mockFocusEffectEnabled`) — most
    // tests don't want the async /auth/me refetch firing and racing their
    // assertions. Deferred through a real `useEffect` (not called inline
    // during render) so it fires post-commit exactly like the real
    // `@react-navigation` hook — calling `cb()` synchronously during render
    // triggers React's "too many re-renders" guard the moment the callback's
    // first `setState` lands.
    useFocusEffect: (cb: () => void | (() => void)) => {
      ReactActual.useEffect(() => {
        if (mockFocusEffectEnabled) return cb();
      }, []);
    },
  };
});
jest.mock('expo-linear-gradient', () => {
  const { View } = require('react-native');
  return { LinearGradient: ({ children }: any) => <View>{children}</View> };
});
jest.mock('expo-image', () => ({ Image: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));
jest.mock('../store/workProfileStore', () => ({
  useWorkProfileStore: () => ({ profiles: [], workModeEnabled: false }),
}));

const COLORS = {
  primary: '#EF4444', primaryDark: '#D32F2F', background: '#FFF', surface: '#FFF',
  surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockApiGet = jest.fn((_url?: string) => Promise.resolve({ data: {} as any }));
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: {
    get: (url?: string) => mockApiGet(url),
    post: jest.fn(() => Promise.resolve({ data: {} })),
  },
}));

// `email_verified` isn't declared on the shared `User` type yet (see
// app/verify-email.tsx's `VerifiableUser` comment for why) — extend it
// locally for this test file's typing only.
type VerifiableUser = import('@shared/store/authStore').User & { email_verified?: boolean };

const mockDefaultUser: VerifiableUser = {
  id: 'rider-1',
  phone: '+15551234567',
  email: 'rider@example.com',
  role: 'rider',
  created_at: new Date().toISOString(),
  profile_complete: true,
};

jest.mock('@shared/store/authStore', () => {
  const { create: createStore } = require('zustand');
  const useAuthStore = createStore(() => ({ user: mockDefaultUser, logout: jest.fn() }));
  return { useAuthStore };
});

import AccountScreen from '../app/(tabs)/account';
import { useAuthStore } from '@shared/store/authStore';

let mountedRenderer: TestRenderer.ReactTestRenderer | null = null;

function renderScreen() {
  let renderer!: TestRenderer.ReactTestRenderer;
  act(() => {
    renderer = TestRenderer.create(<AccountScreen />);
  });
  mountedRenderer = renderer;
  return renderer;
}

function allText(renderer: TestRenderer.ReactTestRenderer): string {
  return JSON.stringify(renderer.toJSON());
}

afterEach(() => {
  mountedRenderer?.unmount();
  mountedRenderer = null;
});

beforeEach(() => {
  jest.clearAllMocks();
  mockFocusEffectEnabled = false;
  mockApiGet.mockImplementation(() => Promise.resolve({ data: {} }));
  useAuthStore.setState({ user: { ...mockDefaultUser } });
});

describe('AccountScreen — email verification row (N14)', () => {
  it('shows a Verify pill when the account has an unverified email', () => {
    const renderer = renderScreen();
    expect(allText(renderer)).toContain('Verify');
    expect(allText(renderer)).not.toContain('Verified');
  });

  it('navigates to /verify-email when the Verify pill is tapped', () => {
    const renderer = renderScreen();
    const verifyButtons = renderer.root.findAll(
      (node) => node.props?.accessibilityLabel === 'Verify email',
    );
    expect(verifyButtons.length).toBeGreaterThan(0);
    act(() => {
      verifyButtons[0].props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith('/verify-email');
  });

  it('shows a Verified pill (not the Verify action) once email_verified is true', () => {
    useAuthStore.setState({ user: { ...mockDefaultUser, email_verified: true } as VerifiableUser });
    const renderer = renderScreen();
    expect(allText(renderer)).toContain('Verified');
    const verifyButtons = renderer.root.findAll(
      (node) => node.props?.accessibilityLabel === 'Verify email',
    );
    expect(verifyButtons.length).toBe(0);
  });

  it('shows neither pill when there is no email on file', () => {
    useAuthStore.setState({ user: { ...mockDefaultUser, email: undefined } });
    const renderer = renderScreen();
    const verifyButtons = renderer.root.findAll(
      (node) => node.props?.accessibilityLabel === 'Verify email',
    );
    const verifiedBadges = renderer.root.findAll(
      (node) => node.props?.accessibilityLabel === 'Email verified',
    );
    expect(verifyButtons.length).toBe(0);
    expect(verifiedBadges.length).toBe(0);
  });
});

describe('AccountScreen — focus-refresh must not wipe a locally-set email_verified flag', () => {
  // GET /auth/me's response schema doesn't return `email_verified` today
  // (backend/schemas.py's UserProfile — out of scope for this rider-app-only
  // change). Before this fix, the focus-effect's `setState({ user: ... })`
  // fully replaced the user object every time the tab refocused (e.g. right
  // after navigating back from verify-email.tsx), silently reverting the
  // badge to "not verified" even though the account really is verified. The
  // fix merges the fresh response over the existing user instead of
  // replacing it, so a key the response doesn't send is preserved.
  it('keeps the Verified pill after the focus-triggered /auth/me refetch', async () => {
    useAuthStore.setState({ user: { ...mockDefaultUser, email_verified: true } as VerifiableUser });
    // /auth/me responds the way the real backend does today: no
    // `email_verified` key at all (not `email_verified: false` — genuinely
    // absent), mirroring UserProfile's field list.
    mockApiGet.mockImplementation((url?: string) => {
      if (url === '/auth/me') {
        return Promise.resolve({ data: { ...mockDefaultUser, rating: 4.9 } });
      }
      return Promise.resolve({ data: {} });
    });
    mockFocusEffectEnabled = true;

    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => {
      renderer = TestRenderer.create(<AccountScreen />);
      // Flush the focus effect's async IIFE + the setState it triggers.
      await new Promise((resolve) => setTimeout(resolve, 0));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    mountedRenderer = renderer;

    expect(allText(renderer)).toContain('Verified');
    const verifyButtons = renderer.root.findAll(
      (node) => node.props?.accessibilityLabel === 'Verify email',
    );
    expect(verifyButtons.length).toBe(0);
    // Sanity: the merge really did apply the fresh field too, it didn't just
    // no-op and keep the old object untouched.
    expect(useAuthStore.getState().user?.rating).toBe(4.9);
  });
});
