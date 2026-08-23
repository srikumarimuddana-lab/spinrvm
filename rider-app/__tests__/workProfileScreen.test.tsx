/**
 * app/work-profile.tsx — corporate work-profile hub: allowance balance,
 * work/personal mode toggle, company switcher, and recent work rides.
 * Pins:
 *  - the loading state (profiles empty + isLoading) and the true-empty
 *    state (profiles empty, not loading) are distinct
 *  - the balance card's three shapes: no allowance configured, unlimited,
 *    and limited-with-remaining/used
 *  - "Request More Funds" only shows for a limited, active allowance --
 *    never for unlimited or an inactive one
 *  - the work/personal mode toggle calls setWorkMode with the new value
 *  - the company switcher only renders with 2+ profiles, and tapping a
 *    company calls setActiveCompany
 *  - recent work rides render from the per-company rides fetch
 *
 * Note: this screen has two effects that both fetch
 * `/rider/work-profile/:id/rides` on initial mount when activeCompanyId
 * is already set (a documented, unfixed redundant-fetch — see the file's
 * own C20 comments) -- tests here assert on rendered content, not exact
 * api.get call counts, so they don't lock in that known race as either a
 * pass or a regression signal.
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));
jest.mock('expo-linear-gradient', () => ({
  LinearGradient: ({ children }: any) => children,
}));

const mockBack = jest.fn();
const mockPush = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, push: mockPush }),
}));

const COLORS = {
  primary: '#EF4444', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111',
  textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockApiGet = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a) },
}));

const mockFetchProfiles = jest.fn();
const mockFetchBalance = jest.fn();
const mockSetActiveCompany = jest.fn();
const mockSetWorkMode = jest.fn();
let mockWorkProfileState: any;
function resetWorkProfileState() {
  mockWorkProfileState = {
    profiles: [{ company: { id: 'c1', name: 'Acme Co' }, membership: { role: 'employee' } }],
    activeCompanyId: 'c1',
    workModeEnabled: true,
    balance: null,
    balanceLoading: false,
    isLoading: false,
    fetchProfiles: mockFetchProfiles,
    setActiveCompany: mockSetActiveCompany,
    setWorkMode: mockSetWorkMode,
    fetchBalance: mockFetchBalance,
  };
}
jest.mock('../store/workProfileStore', () => ({
  useWorkProfileStore: () => mockWorkProfileState,
}));

import WorkProfileScreen from '../app/work-profile';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<WorkProfileScreen />);
    await flush();
  });
  return renderer!;
}

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
  mockFetchProfiles.mockResolvedValue(undefined);
  mockFetchBalance.mockResolvedValue(undefined);
  mockApiGet.mockResolvedValue({ data: [] });
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
});

describe('WorkProfileScreen', () => {
  it('shows a loading spinner (not the empty state) while profiles are loading', async () => {
    mockWorkProfileState.profiles = [];
    mockWorkProfileState.isLoading = true;
    const r = await renderScreen();
    expect(allText(r)).not.toContain('No work profile yet');
  });

  it('shows the empty state when there are genuinely no profiles', async () => {
    mockWorkProfileState.profiles = [];
    mockWorkProfileState.activeCompanyId = null;
    const r = await renderScreen();
    expect(allText(r)).toContain('No work profile yet');
  });

  it('shows "No allowance configured" when there is no balance', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('No allowance configured');
  });

  it('shows "Unlimited" with no spending cap for an unlimited allowance', async () => {
    mockWorkProfileState.balance = { type: 'unlimited', status: 'active', company_name: 'Acme Co' };
    const r = await renderScreen();
    const text = allText(r);
    expect(text).toContain('Unlimited');
    expect(text).toContain('No spending cap');
  });

  it('shows remaining/limit/used for a limited allowance', async () => {
    mockWorkProfileState.balance = {
      type: 'limited', status: 'active', company_name: 'Acme Co',
      remaining: 42.5, amount: 100, used: 57.5, period_start: null, period_end: null,
    };
    const r = await renderScreen();
    const text = allText(r);
    expect(text).toContain('$42.50');
    expect(text).toContain('["Limit: $","100.00"]');
    expect(text).toContain('["Used: $","57.50"]');
  });

  it('shows "Request More Funds" for a limited, active allowance', async () => {
    mockWorkProfileState.balance = { type: 'limited', status: 'active', remaining: 10, amount: 100 };
    const r = await renderScreen();
    const requestBtn = findButtonByText(r, 'Request More Funds');
    act(() => {
      requestBtn.props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith('/work-allowance-request');
  });

  it('hides "Request More Funds" for an unlimited allowance', async () => {
    mockWorkProfileState.balance = { type: 'unlimited', status: 'active' };
    const r = await renderScreen();
    const found = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Request More Funds')),
    );
    expect(found).toBeUndefined();
  });

  it('hides "Request More Funds" for a non-active (e.g. suspended) allowance', async () => {
    mockWorkProfileState.balance = { type: 'limited', status: 'suspended', remaining: 10, amount: 100 };
    const r = await renderScreen();
    const found = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Request More Funds')),
    );
    expect(found).toBeUndefined();
  });

  it('toggles work mode via setWorkMode', async () => {
    const r = await renderScreen();
    const toggle = r.root.findByProps({ value: true });
    act(() => {
      toggle.props.onValueChange(false);
    });
    expect(mockSetWorkMode).toHaveBeenCalledWith(false);
  });

  it('does not render the company switcher with only one profile', async () => {
    const r = await renderScreen();
    // "Acme Co" alone also appears in the balance card's own company-name
    // line, so check for the switcher's distinguishing content instead.
    expect(allText(r)).not.toContain('"Company"');
    expect(allText(r)).not.toContain('employee');
  });

  it('renders the company switcher and switches company on tap when there are 2+ profiles', async () => {
    mockWorkProfileState.profiles = [
      { company: { id: 'c1', name: 'Acme Co' }, membership: { role: 'employee' } },
      { company: { id: 'c2', name: 'Beta Inc' }, membership: { role: 'admin' } },
    ];
    const r = await renderScreen();
    expect(allText(r)).toContain('Acme Co');
    expect(allText(r)).toContain('Beta Inc');
    const betaOption = findButtonByText(r, 'Beta Inc');
    act(() => {
      betaOption.props.onPress();
    });
    expect(mockSetActiveCompany).toHaveBeenCalledWith('c2');
  });

  it('renders recent work rides fetched from the per-company endpoint', async () => {
    mockApiGet.mockResolvedValue({
      data: [{ id: 'r1', dropoff_address: '123 Main St', total_fare: 15.5, created_at: '2026-01-01T00:00:00Z', status: 'completed', allowance_debit_amount: 15.5, master_fallback_amount: null, source_type: 'allowance' }],
    });
    const r = await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/rider/work-profile/c1/rides');
    expect(allText(r)).toContain('123 Main St');
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
