/**
 * app/safety-hub.tsx — the calm, out-of-ride safety settings/resources
 * home (distinct from the in-ride SOS panel — no alert-firing control
 * here, per the file's own stated design). Pins:
 *  - resolves SOS location on mount to configure the safety panel
 *  - "Call <emergencyNumber>" opens the dialer directly (never auto-dials
 *    on Spinr's behalf, per the row's own subtitle copy)
 *  - the emergency-contacts row subtitle reflects whether any contacts
 *    are saved, and navigates to /emergency-contacts
 *  - "Report a safety issue" navigates to /report-safety
 *  - the local-authority row only renders when cfg.authority is present,
 *    and calls vs. opens a URL depending on which the authority has
 *  - the Spinr Safety email row only renders when cfg.safetyTeamEmail is
 *    present
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { Text, Linking } from 'react-native';

import SafetyHubScreen from '../app/safety-hub';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));

const mockBack = jest.fn();
const mockPush = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, push: mockPush }),
}));

const COLORS = { primary: '#EF4444', background: '#FFF', surface: '#FFF', text: '#111', textDim: '#666', border: '#E5E7EB' };
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockGetSOSLocation = jest.fn();
jest.mock('@shared/utils/sosLocation', () => ({ getSOSLocation: (...a: any[]) => mockGetSOSLocation(...a) }));

let mockSafetyPanelConfig: any;
jest.mock('@shared/hooks/useSafetyPanelConfig', () => ({
  useSafetyPanelConfig: () => mockSafetyPanelConfig,
}));

let mockContacts: any[];
jest.mock('@shared/hooks/useEmergencyContacts', () => ({
  useEmergencyContacts: () => ({ contacts: mockContacts }),
}));

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<SafetyHubScreen />);
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => JSON.stringify(t.props.children)).join(' | ');
}

function findRowByLabel(r: TestRenderer.ReactTestRenderer, label: string) {
  return r.root.findByProps({ accessibilityLabel: label });
}

beforeEach(() => {
  jest.clearAllMocks();
  mockGetSOSLocation.mockResolvedValue({ lat: 50.45, lng: -104.6 });
  mockSafetyPanelConfig = { emergencyNumber: '911', authority: null, safetyTeamEmail: null };
  mockContacts = [];
  jest.spyOn(Linking, 'openURL').mockResolvedValue(true as any);
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
  jest.restoreAllMocks();
});

describe('SafetyHubScreen', () => {
  it('resolves SOS location on mount', async () => {
    await renderScreen();
    expect(mockGetSOSLocation).toHaveBeenCalled();
  });

  it('opens the dialer for the emergency number', async () => {
    const r = await renderScreen();
    const callRow = findRowByLabel(r, 'Call 911');
    act(() => {
      callRow.props.onPress();
    });
    expect(Linking.openURL).toHaveBeenCalledWith('tel:911');
  });

  it('shows "None saved" when there are no emergency contacts', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('None saved — nobody will be texted if you send an alert');
  });

  it('shows the saved contact count when contacts exist', async () => {
    mockContacts = [{ id: 'c1' }, { id: 'c2' }];
    const r = await renderScreen();
    expect(allText(r)).toContain("2 saved — they're texted when you send an alert");
  });

  it('navigates to /emergency-contacts', async () => {
    const r = await renderScreen();
    const row = findRowByLabel(r, 'Emergency contacts');
    act(() => {
      row.props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith('/emergency-contacts');
  });

  it('navigates to /report-safety', async () => {
    const r = await renderScreen();
    const row = findRowByLabel(r, 'Report a safety issue');
    act(() => {
      row.props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith('/report-safety');
  });

  it('does not render the local-authority row when cfg.authority is absent', async () => {
    const r = await renderScreen();
    expect(allText(r)).not.toContain('Local transport authority');
  });

  it('calls the authority phone number when present', async () => {
    mockSafetyPanelConfig = {
      emergencyNumber: '911',
      authority: { name: 'SK Transport Board', phone: '3065550001', hours: '9-5 M-F' },
      safetyTeamEmail: null,
    };
    const r = await renderScreen();
    const row = findRowByLabel(r, 'Call SK Transport Board');
    act(() => {
      row.props.onPress();
    });
    expect(Linking.openURL).toHaveBeenCalledWith('tel:3065550001');
  });

  it('opens the authority URL when there is no phone', async () => {
    mockSafetyPanelConfig = {
      emergencyNumber: '911',
      authority: { name: 'SK Transport Board', url: 'https://sktb.example.ca' },
      safetyTeamEmail: null,
    };
    const r = await renderScreen();
    const row = findRowByLabel(r, 'SK Transport Board');
    act(() => {
      row.props.onPress();
    });
    expect(Linking.openURL).toHaveBeenCalledWith('https://sktb.example.ca');
  });

  it('does not render the Spinr Safety email row when cfg.safetyTeamEmail is absent', async () => {
    const r = await renderScreen();
    expect(allText(r)).not.toContain('Email Spinr Safety');
  });

  it('opens the mail composer for the Spinr Safety email when present', async () => {
    mockSafetyPanelConfig = { emergencyNumber: '911', authority: null, safetyTeamEmail: 'safety@spinr.ca' };
    const r = await renderScreen();
    const row = findRowByLabel(r, 'Email Spinr Safety');
    act(() => {
      row.props.onPress();
    });
    expect(Linking.openURL).toHaveBeenCalledWith('mailto:safety@spinr.ca');
  });

  it('navigates back when the back button is pressed', async () => {
    const r = await renderScreen();
    const backBtn = findRowByLabel(r, 'Go back');
    act(() => {
      backBtn.props.onPress();
    });
    expect(mockBack).toHaveBeenCalled();
  });
});
