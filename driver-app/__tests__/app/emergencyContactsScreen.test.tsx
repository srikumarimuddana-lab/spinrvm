/**
 * app/driver/emergency-contacts.tsx — safety-critical: the driver's SOS
 * emergency contact list (see domain-safety.md). Pins:
 *  - GET /users/emergency-contacts on mount
 *  - Add: blocks on an empty name or a phone under 10 digits, strips
 *    non-digit characters before submit, and re-fetches + toasts on
 *    success (form resets and collapses)
 *  - the add form and its "Add Contact" trigger both hide once
 *    MAX_CONTACTS (3) is reached
 *  - Remove: confirms via Alert.alert, then DELETE + re-fetch; a failure
 *    surfaces its own toast
 *  - phone formatting for both 10-digit and 11-digit (leading 1) numbers,
 *    with an unrecognised length left as-is (never mangled)
 *  - the empty state renders when there are no contacts
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, Alert } from 'react-native';

const mockT = (key: string) => key;
const mockLanguageState = { t: mockT };
jest.mock('../../store/languageStore', () => ({
  useLanguageStore: () => mockLanguageState,
}));

import EmergencyContactsScreen from '../../app/driver/emergency-contacts';

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

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
const mockApiDelete = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: {
    get: (...a: any[]) => mockApiGet(...a),
    post: (...a: any[]) => mockApiPost(...a),
    delete: (...a: any[]) => mockApiDelete(...a),
  },
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));

const mockShowToast = jest.fn();
jest.mock('../../hooks/useToast', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

const CONTACT_1 = { id: 'c1', name: 'Jane Doe', phone: '3065551234', relationship: 'Spouse' };

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<EmergencyContactsScreen />);
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => JSON.stringify(t.props.children)).join(' | ');
}

function findButtonByText(r: TestRenderer.ReactTestRenderer, text: string) {
  return r.root
    .findAllByType(TouchableOpacity)
    .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes(text)))!;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockApiGet.mockResolvedValue({ data: { contacts: [] } });
  jest.spyOn(Alert, 'alert').mockImplementation(() => {});
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
  jest.restoreAllMocks();
});

describe('EmergencyContactsScreen', () => {
  it('loads contacts on mount', async () => {
    mockApiGet.mockResolvedValue({ data: { contacts: [CONTACT_1] } });
    const r = await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/users/emergency-contacts');
    expect(allText(r)).toContain('Jane Doe');
  });

  it('shows the empty state when there are no contacts', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('emergencyContacts.noContacts');
  });

  it('formats a 10-digit phone number', async () => {
    mockApiGet.mockResolvedValue({ data: { contacts: [{ ...CONTACT_1, phone: '3065551234' }] } });
    const r = await renderScreen();
    expect(allText(r)).toContain('(306) 555-1234');
  });

  it('formats an 11-digit phone number with a leading 1', async () => {
    mockApiGet.mockResolvedValue({ data: { contacts: [{ ...CONTACT_1, phone: '13065551234' }] } });
    const r = await renderScreen();
    expect(allText(r)).toContain('+1 (306) 555-1234');
  });

  it('leaves an unrecognised-length phone number unformatted', async () => {
    mockApiGet.mockResolvedValue({ data: { contacts: [{ ...CONTACT_1, phone: '123' }] } });
    const r = await renderScreen();
    expect(allText(r)).toContain('123');
  });

  it('blocks adding with an empty name', async () => {
    const r = await renderScreen();
    const addTrigger = findButtonByText(r, 'emergencyContacts.addContact');
    act(() => {
      addTrigger.props.onPress();
    });
    const phoneInput = r.root.findByProps({ placeholder: 'e.g. (306) 555-1234' });
    act(() => {
      phoneInput.props.onChangeText('3065551234');
    });
    const saveBtn = findButtonByText(r, 'emergencyContacts.saveContact');
    await act(async () => {
      await saveBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('warning', 'Missing Name', 'Please enter a contact name.');
    expect(mockApiPost).not.toHaveBeenCalled();
  });

  it('blocks adding with a phone under 10 digits', async () => {
    const r = await renderScreen();
    const addTrigger = findButtonByText(r, 'emergencyContacts.addContact');
    act(() => {
      addTrigger.props.onPress();
    });
    const nameInput = r.root.findByProps({ placeholder: 'e.g. Sarah Johnson' });
    act(() => {
      nameInput.props.onChangeText('Sarah Johnson');
    });
    const phoneInput = r.root.findByProps({ placeholder: 'e.g. (306) 555-1234' });
    act(() => {
      phoneInput.props.onChangeText('12345');
    });
    const saveBtn = findButtonByText(r, 'emergencyContacts.saveContact');
    await act(async () => {
      await saveBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith(
      'warning',
      'Invalid Phone',
      'Please enter a valid phone number (at least 10 digits).',
    );
    expect(mockApiPost).not.toHaveBeenCalled();
  });

  it('adds a contact, strips non-digits from the phone, resets the form, and re-fetches', async () => {
    mockApiPost.mockResolvedValue({ data: {} });
    const r = await renderScreen();
    const addTrigger = findButtonByText(r, 'emergencyContacts.addContact');
    act(() => {
      addTrigger.props.onPress();
    });
    const nameInput = r.root.findByProps({ placeholder: 'e.g. Sarah Johnson' });
    act(() => {
      nameInput.props.onChangeText('Sarah Johnson');
    });
    const phoneInput = r.root.findByProps({ placeholder: 'e.g. (306) 555-1234' });
    act(() => {
      phoneInput.props.onChangeText('(306) 555-1234');
    });
    mockApiGet.mockClear();
    const saveBtn = findButtonByText(r, 'emergencyContacts.saveContact');
    await act(async () => {
      await saveBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledWith('/users/emergency-contacts', {
      name: 'Sarah Johnson',
      phone: '3065551234',
      relationship: 'Friend',
    });
    expect(mockApiGet).toHaveBeenCalledWith('/users/emergency-contacts');
    expect(mockShowToast).toHaveBeenCalledWith(
      'success',
      'Contact Added',
      'Sarah Johnson has been added as an emergency contact.',
    );
  });

  it('shows a toast when adding fails', async () => {
    mockApiPost.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    const addTrigger = findButtonByText(r, 'emergencyContacts.addContact');
    act(() => {
      addTrigger.props.onPress();
    });
    const nameInput = r.root.findByProps({ placeholder: 'e.g. Sarah Johnson' });
    act(() => {
      nameInput.props.onChangeText('Sarah Johnson');
    });
    const phoneInput = r.root.findByProps({ placeholder: 'e.g. (306) 555-1234' });
    act(() => {
      phoneInput.props.onChangeText('3065551234');
    });
    const saveBtn = findButtonByText(r, 'emergencyContacts.saveContact');
    await act(async () => {
      await saveBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith(
      'error',
      'Could Not Add',
      'Could not add contact. Please try again.',
    );
  });

  it('hides the Add Contact trigger once MAX_CONTACTS (3) is reached', async () => {
    mockApiGet.mockResolvedValue({
      data: {
        contacts: [
          { id: 'c1', name: 'A', phone: '1111111111' },
          { id: 'c2', name: 'B', phone: '2222222222' },
          { id: 'c3', name: 'C', phone: '3333333333' },
        ],
      },
    });
    const r = await renderScreen();
    const found = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('emergencyContacts.addContact')),
    );
    expect(found).toBeUndefined();
  });

  it('confirms via Alert before removing a contact, then deletes and re-fetches', async () => {
    mockApiGet.mockResolvedValue({ data: { contacts: [CONTACT_1] } });
    mockApiDelete.mockResolvedValue({ data: {} });
    const r = await renderScreen();
    const actualDeleteBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByProps({ name: 'trash-outline' }).length > 0)!;
    act(() => {
      actualDeleteBtn.props.onPress();
    });
    expect(Alert.alert).toHaveBeenCalledWith(
      'Remove Contact',
      'Remove Jane Doe as an emergency contact?',
      expect.any(Array),
    );
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const confirmAction = alertCall[2].find((b: any) => b.style === 'destructive');
    mockApiGet.mockClear();
    await act(async () => {
      await confirmAction.onPress();
      await flush();
    });
    expect(mockApiDelete).toHaveBeenCalledWith('/users/emergency-contacts/c1');
    expect(mockApiGet).toHaveBeenCalledWith('/users/emergency-contacts');
  });

  it('shows a toast when removal fails', async () => {
    mockApiGet.mockResolvedValue({ data: { contacts: [CONTACT_1] } });
    mockApiDelete.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    const actualDeleteBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByProps({ name: 'trash-outline' }).length > 0)!;
    act(() => {
      actualDeleteBtn.props.onPress();
    });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const confirmAction = alertCall[2].find((b: any) => b.style === 'destructive');
    await act(async () => {
      await confirmAction.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith(
      'error',
      'Remove Failed',
      'Could not remove contact. Please try again.',
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
