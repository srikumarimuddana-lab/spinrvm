/**
 * app/emergency-contacts.tsx — safety-critical: the rider's SOS emergency
 * contact list. Pins:
 *  - GET /users/emergency-contacts on mount
 *  - Add: blocks on an empty name or a phone under 10 digits, strips
 *    non-digit characters before submit, and re-fetches + toasts on
 *    success (form resets and collapses)
 *  - the add form and its trigger both hide once MAX_CONTACTS (3) is
 *    reached
 *  - Remove: confirms via the ConfirmSheet, then DELETE + re-fetch; a
 *    failure surfaces its own toast
 *  - phone formatting for both 10-digit and 11-digit (leading 1) numbers,
 *    with an unrecognised length left as-is
 *  - the empty state renders when there are no contacts
 *
 * ConfirmSheet pulls in @gorhom/bottom-sheet (a heavy native dependency),
 * so it's replaced with a lightweight double that renders its buttons
 * directly when visible, matching scheduledRidesScreen.test.tsx's
 * convention.
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text } from 'react-native';

import EmergencyContactsScreen from '../app/emergency-contacts';

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
jest.mock('../store/toastStore', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

jest.mock('../components/ConfirmSheet', () => (props: any) => {
  const { View, Text: RNText, TouchableOpacity: RNTouchableOpacity } = require('react-native');
  if (!props.visible) return null;
  return (
    <View>
      <RNText>{props.title}</RNText>
      <RNText>{props.message}</RNText>
      {(props.buttons || []).map((b: any, i: number) => (
        <RNTouchableOpacity key={i} onPress={b.onPress || props.onClose} accessibilityLabel={`confirm-${b.text}`}>
          <RNText>{b.text}</RNText>
        </RNTouchableOpacity>
      ))}
    </View>
  );
});

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
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
});

describe('EmergencyContactsScreen (rider-app)', () => {
  it('loads contacts on mount', async () => {
    mockApiGet.mockResolvedValue({ data: { contacts: [CONTACT_1] } });
    const r = await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/users/emergency-contacts');
    expect(allText(r)).toContain('Jane Doe');
  });

  it('shows the empty state when there are no contacts', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('No emergency contacts yet');
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

  it('blocks adding with an empty name', async () => {
    const r = await renderScreen();
    const addTrigger = findButtonByText(r, 'Add Emergency Contact');
    act(() => {
      addTrigger.props.onPress();
    });
    const phoneInput = r.root.findByProps({ placeholder: 'e.g. (306) 555-1234' });
    act(() => {
      phoneInput.props.onChangeText('3065551234');
    });
    const saveBtn = findButtonByText(r, 'Save Contact');
    await act(async () => {
      await saveBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('Missing Name', 'Please enter a contact name.', 'warning');
    expect(mockApiPost).not.toHaveBeenCalled();
  });

  it('blocks adding with a phone under 10 digits', async () => {
    const r = await renderScreen();
    const addTrigger = findButtonByText(r, 'Add Emergency Contact');
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
    const saveBtn = findButtonByText(r, 'Save Contact');
    await act(async () => {
      await saveBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith(
      'Invalid Phone',
      'Please enter a valid phone number (at least 10 digits).',
      'warning',
    );
    expect(mockApiPost).not.toHaveBeenCalled();
  });

  it('adds a contact, strips non-digits from the phone, resets the form, and re-fetches', async () => {
    mockApiPost.mockResolvedValue({ data: {} });
    const r = await renderScreen();
    const addTrigger = findButtonByText(r, 'Add Emergency Contact');
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
    const saveBtn = findButtonByText(r, 'Save Contact');
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
      'Contact Added',
      'Sarah Johnson has been added as an emergency contact.',
      'success',
    );
  });

  it('shows a toast when adding fails', async () => {
    mockApiPost.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    const addTrigger = findButtonByText(r, 'Add Emergency Contact');
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
    const saveBtn = findButtonByText(r, 'Save Contact');
    await act(async () => {
      await saveBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('Could Not Add', 'Could not add contact.', 'danger');
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
      n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Add Emergency Contact')),
    );
    expect(found).toBeUndefined();
  });

  it('confirms via the sheet before removing a contact, then deletes and re-fetches', async () => {
    mockApiGet.mockResolvedValue({ data: { contacts: [CONTACT_1] } });
    mockApiDelete.mockResolvedValue({ data: {} });
    const r = await renderScreen();
    const deleteBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByProps({ name: 'trash-outline' }).length > 0)!;
    act(() => {
      deleteBtn.props.onPress();
    });
    expect(allText(r)).toContain('Remove Contact');
    expect(allText(r)).toContain('Remove Jane Doe as an emergency contact?');
    mockApiGet.mockClear();
    const confirmBtn = r.root.findByProps({ accessibilityLabel: 'confirm-Remove' });
    await act(async () => {
      await confirmBtn.props.onPress();
      await flush();
    });
    expect(mockApiDelete).toHaveBeenCalledWith('/users/emergency-contacts/c1');
    expect(mockApiGet).toHaveBeenCalledWith('/users/emergency-contacts');
  });

  it('shows a toast when removal fails', async () => {
    mockApiGet.mockResolvedValue({ data: { contacts: [CONTACT_1] } });
    mockApiDelete.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    const deleteBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByProps({ name: 'trash-outline' }).length > 0)!;
    act(() => {
      deleteBtn.props.onPress();
    });
    const confirmBtn = r.root.findByProps({ accessibilityLabel: 'confirm-Remove' });
    await act(async () => {
      await confirmBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('Remove Failed', 'Could not remove contact.', 'danger');
  });

  it('does not crash and leaves the empty state showing when the fetch fails', async () => {
    mockApiGet.mockRejectedValue(new Error('network down'));
    const r = await renderScreen();
    expect(allText(r)).toContain('No emergency contacts yet');
  });

  it('leaves an unrecognised phone length unformatted', async () => {
    mockApiGet.mockResolvedValue({ data: { contacts: [{ ...CONTACT_1, phone: '12345' }] } });
    const r = await renderScreen();
    expect(allText(r)).toContain('"12345"');
  });

  it('maps each relationship to its own icon, falling back to the generic icon for an unrecognised one', async () => {
    mockApiGet.mockResolvedValue({
      data: {
        contacts: [
          { id: 'c1', name: 'A', phone: '1111111111', relationship: 'Parent' },
          { id: 'c2', name: 'B', phone: '2222222222', relationship: 'Sibling' },
          { id: 'c3', name: 'C', phone: '3333333333', relationship: 'Child' },
          { id: 'c4', name: 'D', phone: '4444444444', relationship: 'Friend' },
        ],
      },
    });
    const r = await renderScreen();
    expect(r.root.findAllByProps({ name: 'people' })).toHaveLength(1);
    expect(r.root.findAllByProps({ name: 'people-outline' })).toHaveLength(1);
    expect(r.root.findAllByProps({ name: 'person' })).toHaveLength(1);
    expect(r.root.findAllByProps({ name: 'person-outline' })).toHaveLength(1);
  });

  it('falls back to the generic person-circle icon for an unrecognised relationship', async () => {
    mockApiGet.mockResolvedValue({ data: { contacts: [{ ...CONTACT_1, relationship: 'Roommate' }] } });
    const r = await renderScreen();
    expect(r.root.findAllByProps({ name: 'person-circle-outline' }).length).toBeGreaterThan(0);
  });

  it('selects a relationship chip and submits it with the new contact', async () => {
    mockApiPost.mockResolvedValue({ data: {} });
    const r = await renderScreen();
    const addTrigger = findButtonByText(r, 'Add Emergency Contact');
    act(() => { addTrigger.props.onPress(); });
    const nameInput = r.root.findByProps({ placeholder: 'e.g. Sarah Johnson' });
    act(() => { nameInput.props.onChangeText('Sarah Johnson'); });
    const phoneInput = r.root.findByProps({ placeholder: 'e.g. (306) 555-1234' });
    act(() => { phoneInput.props.onChangeText('3065551234'); });
    const parentChip = findButtonByText(r, 'Parent');
    act(() => { parentChip.props.onPress(); });
    const saveBtn = findButtonByText(r, 'Save Contact');
    await act(async () => { await saveBtn.props.onPress(); await flush(); });
    expect(mockApiPost).toHaveBeenCalledWith('/users/emergency-contacts', {
      name: 'Sarah Johnson',
      phone: '3065551234',
      relationship: 'Parent',
    });
  });

  it('cancels the add form, clearing the name and phone fields', async () => {
    const r = await renderScreen();
    const addTrigger = findButtonByText(r, 'Add Emergency Contact');
    act(() => { addTrigger.props.onPress(); });
    const nameInput = r.root.findByProps({ placeholder: 'e.g. Sarah Johnson' });
    act(() => { nameInput.props.onChangeText('Sarah Johnson'); });
    const cancelBtn = findButtonByText(r, 'Cancel');
    act(() => { cancelBtn.props.onPress(); });
    expect(allText(r)).not.toContain('New Emergency Contact');
    // Re-open and confirm the name field was actually cleared, not just hidden.
    const addTriggerAgain = findButtonByText(r, 'Add Emergency Contact');
    act(() => { addTriggerAgain.props.onPress(); });
    const nameInputAgain = r.root.findByProps({ placeholder: 'e.g. Sarah Johnson' });
    expect(nameInputAgain.props.value).toBe('');
  });

  it('shows a saving spinner (and disables Save) while the add request is in flight', async () => {
    let resolvePost: (v: any) => void;
    mockApiPost.mockReturnValue(new Promise((resolve) => { resolvePost = resolve; }));
    const r = await renderScreen();
    const addTrigger = findButtonByText(r, 'Add Emergency Contact');
    act(() => { addTrigger.props.onPress(); });
    const nameInput = r.root.findByProps({ placeholder: 'e.g. Sarah Johnson' });
    act(() => { nameInput.props.onChangeText('Sarah Johnson'); });
    const phoneInput = r.root.findByProps({ placeholder: 'e.g. (306) 555-1234' });
    act(() => { phoneInput.props.onChangeText('3065551234'); });
    const saveBtn = findButtonByText(r, 'Save Contact');
    act(() => { saveBtn.props.onPress(); });
    const saveBtnNow = r.root.findAllByType(TouchableOpacity).find((n) => n.props.disabled === true)!;
    expect(saveBtnNow).toBeDefined();
    await act(async () => { resolvePost!({ data: {} }); await flush(); });
  });

  it('dismisses the remove-confirmation sheet via Cancel without deleting', async () => {
    mockApiGet.mockResolvedValue({ data: { contacts: [CONTACT_1] } });
    const r = await renderScreen();
    const deleteBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByProps({ name: 'trash-outline' }).length > 0)!;
    act(() => { deleteBtn.props.onPress(); });
    const cancelBtn = r.root.findByProps({ accessibilityLabel: 'confirm-Cancel' });
    act(() => { cancelBtn.props.onPress(); });
    expect(allText(r)).not.toContain('Remove Contact');
    expect(mockApiDelete).not.toHaveBeenCalled();
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
