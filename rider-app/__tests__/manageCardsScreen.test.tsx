/**
 * app/manage-cards.tsx — rider's saved-card wallet (money-critical, PCI-DSS
 * surface). Pins:
 *  - fetchCards on mount; a fetch failure silently falls back to the empty
 *    state (no raw card data ever cached client-side beyond what's fetched)
 *  - Add Card: blocked (toast) on incomplete CardField details, a missing
 *    cardholder name, or createPaymentMethod not yet ready; on success,
 *    POSTs the resulting payment_method_id, closes the form, and
 *    re-fetches; a createPaymentMethod error or POST failure toasts
 *    without adding
 *  - the "pay for a stuck ride" flow (forPayment=1 + rideId): after adding
 *    a card, immediately routes to /ride-completed with payWithCard set
 *    (carrying tip/rated through) instead of just re-fetching
 *  - Set Default posts to /payments/cards/:id/default and re-fetches; a
 *    failure toasts
 *  - Delete: blocks removing the LAST card with an info sheet instead of
 *    a destructive confirm; deleting the default card (with others left)
 *    warns that another becomes default; a successful delete re-fetches,
 *    a failed one toasts
 *  - back nav
 *
 * ConfirmSheet is replaced with a lightweight double (matches
 * scheduledRidesScreen.test.tsx's convention) to bypass @gorhom/bottom-sheet.
 * @stripe/stripe-react-native's CardField/useStripe are mocked; StripeKeyContext
 * is imported from a mocked ../app/_layout (a plain React.createContext, so a
 * mock avoids pulling in that file's heavy real imports), matching
 * walletScreen.test.tsx's convention.
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, TextInput } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null, FontAwesome: () => null, MaterialCommunityIcons: () => null }));
jest.mock('expo-linear-gradient', () => ({ LinearGradient: ({ children }: any) => children }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));

const mockBack = jest.fn();
const mockReplace = jest.fn();
let mockParams: any;
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, replace: mockReplace }),
  useLocalSearchParams: () => mockParams,
}));

const COLORS = {
  primary: '#EF4444', background: '#FFF', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111', textDim: '#666',
  textSecondary: '#333', border: '#E5E7EB', gold: '#D4AF37', error: '#DC2626', dangerBg: '#FEF2F2', success: '#10B981',
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

const mockCreatePaymentMethod = jest.fn();
jest.mock('@stripe/stripe-react-native', () => ({
  CardField: (props: any) => {
    const { TouchableOpacity: RNTouchableOpacity, Text: RNText } = require('react-native');
    // A minimal double: tapping it simulates the card field reporting
    // "complete" (real native CardField reports this via onCardChange as
    // the user types digits, which react-test-renderer can't simulate).
    return (
      <RNTouchableOpacity accessibilityLabel="mock-card-field" onPress={() => props.onCardChange({ complete: true })}>
        <RNText>CardField</RNText>
      </RNTouchableOpacity>
    );
  },
  useStripe: () => ({ createPaymentMethod: (...a: any[]) => mockCreatePaymentMethod(...a) }),
}));

jest.mock('../app/_layout', () => ({
  StripeKeyContext: require('react').createContext(null),
}));

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

import ManageCardsScreen from '../app/manage-cards';
import { StripeKeyContext } from '../app/_layout';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

const CARD_VISA = { id: 'card-1', brand: 'visa', last4: '4242', exp_month: 12, exp_year: 2030, is_default: true, cardholder_name: 'A Rider' };
const CARD_MC = { id: 'card-2', brand: 'mastercard', last4: '4444', exp_month: 6, exp_year: 2029, is_default: false, cardholder_name: 'A Rider' };

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(
      <StripeKeyContext.Provider value="pk_test_123">
        <ManageCardsScreen />
      </StripeKeyContext.Provider>,
    );
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => { try { return JSON.stringify(t.props.children); } catch { return '<circular>'; } }).join(' | ');
}

function findButtonByText(r: TestRenderer.ReactTestRenderer, text: string) {
  return r.root
    .findAllByType(TouchableOpacity)
    .find((n) => n.findAllByType(Text).some((t) => {
      try { return JSON.stringify(t.props.children).includes(text); } catch { return false; }
    }))!;
}

async function openAddFormAndFillIt(r: TestRenderer.ReactTestRenderer) {
  const addBtn = findButtonByText(r, 'Add a new card');
  act(() => {
    addBtn.props.onPress();
  });
  const cardField = r.root.findByProps({ accessibilityLabel: 'mock-card-field' });
  act(() => {
    cardField.props.onPress();
  });
  const nameInput = r.root.findByProps({ placeholder: 'Name on card' });
  act(() => {
    nameInput.props.onChangeText('A Rider');
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  mockParams = {};
  mockApiGet.mockResolvedValue({ data: [] });
  mockApiPost.mockResolvedValue({ data: {} });
  mockApiDelete.mockResolvedValue({ data: {} });
  mockCreatePaymentMethod.mockResolvedValue({ paymentMethod: { id: 'pm_123' }, error: null });
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
});

describe('ManageCardsScreen', () => {
  it('fetches cards on mount', async () => {
    await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/payments/cards');
  });

  it('falls back to the empty state (no crash) when the fetch fails', async () => {
    mockApiGet.mockRejectedValue(new Error('down'));
    const r = await renderScreen();
    expect(allText(r)).toContain('No cards yet');
  });

  it('renders the default card and set-default action for the non-default card', async () => {
    mockApiGet.mockResolvedValue({ data: [CARD_VISA, CARD_MC] });
    const r = await renderScreen();
    expect(allText(r)).toContain('["•••• ","4242"]');
  });

  it('blocks Add Card when card details are incomplete', async () => {
    const r = await renderScreen();
    const addBtn = findButtonByText(r, 'Add a new card');
    act(() => {
      addBtn.props.onPress();
    });
    const saveBtn = findButtonByText(r, 'Add Card');
    await act(async () => {
      await saveBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('Missing Details', 'Please enter complete card details', 'warning');
    expect(mockCreatePaymentMethod).not.toHaveBeenCalled();
  });

  it('blocks Add Card when the cardholder name is missing', async () => {
    const r = await renderScreen();
    const addBtn = findButtonByText(r, 'Add a new card');
    act(() => {
      addBtn.props.onPress();
    });
    const cardField = r.root.findByProps({ accessibilityLabel: 'mock-card-field' });
    act(() => {
      cardField.props.onPress();
    });
    const saveBtn = findButtonByText(r, 'Add Card');
    await act(async () => {
      await saveBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('Missing Name', 'Please enter the cardholder name', 'warning');
    expect(mockCreatePaymentMethod).not.toHaveBeenCalled();
  });

  it('adds a card, closes the form, and re-fetches on success', async () => {
    const r = await renderScreen();
    await openAddFormAndFillIt(r);
    mockApiGet.mockClear();
    const saveBtn = findButtonByText(r, 'Add Card');
    await act(async () => {
      await saveBtn.props.onPress();
      await flush();
    });
    expect(mockCreatePaymentMethod).toHaveBeenCalledWith({
      paymentMethodType: 'Card',
      paymentMethodData: { billingDetails: { name: 'A Rider' } },
    });
    expect(mockApiPost).toHaveBeenCalledWith('/payments/cards', { payment_method_id: 'pm_123' });
    expect(mockApiGet).toHaveBeenCalledWith('/payments/cards');
    expect(mockShowToast).toHaveBeenCalledWith('Card Added', 'Card added successfully', 'success');
    expect(allText(r)).not.toContain('Add New Card');
  });

  it('toasts and does not add when createPaymentMethod errors', async () => {
    mockCreatePaymentMethod.mockResolvedValue({ paymentMethod: null, error: { message: 'Your card was declined.' } });
    const r = await renderScreen();
    await openAddFormAndFillIt(r);
    const saveBtn = findButtonByText(r, 'Add Card');
    await act(async () => {
      await saveBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('Processing Failed', 'Could not process card. Please try again.', 'danger');
    expect(mockApiPost).not.toHaveBeenCalled();
  });

  it('toasts when the POST to save the card fails', async () => {
    mockApiPost.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    await openAddFormAndFillIt(r);
    const saveBtn = findButtonByText(r, 'Add Card');
    await act(async () => {
      await saveBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('Card Not Added', 'Could not add card. Please try again.', 'danger');
  });

  it('pay-for-ride flow: after adding a card, routes to /ride-completed with payWithCard (carrying tip/rated)', async () => {
    mockParams = { rideId: 'ride-9', forPayment: '1', tip: '5.00', rated: 'true' };
    const r = await renderScreen();
    await openAddFormAndFillIt(r);
    const saveBtn = findButtonByText(r, 'Add Card');
    await act(async () => {
      await saveBtn.props.onPress();
      await flush();
    });
    expect(mockReplace).toHaveBeenCalledWith({
      pathname: '/ride-completed',
      params: { rideId: 'ride-9', payWithCard: 'pm_123', tip: '5.00', rated: 'true' },
    });
  });

  it('sets a card as default and re-fetches', async () => {
    mockApiGet.mockResolvedValue({ data: [CARD_VISA, CARD_MC] });
    const r = await renderScreen();
    const mcCard = findButtonByText(r, '4444');
    act(() => {
      mcCard.props.onPress();
    });
    mockApiGet.mockClear();
    const setDefaultBtn = findButtonByText(r, 'Set Default');
    await act(async () => {
      await setDefaultBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledWith('/payments/cards/card-2/default');
    expect(mockApiGet).toHaveBeenCalledWith('/payments/cards');
  });

  it('toasts when setting default fails', async () => {
    mockApiGet.mockResolvedValue({ data: [CARD_VISA, CARD_MC] });
    mockApiPost.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    const mcCard = findButtonByText(r, '4444');
    act(() => {
      mcCard.props.onPress();
    });
    const setDefaultBtn = findButtonByText(r, 'Set Default');
    await act(async () => {
      await setDefaultBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('Update Failed', 'Could not set default card. Please try again.', 'danger');
  });

  it('blocks removing the last card with an info sheet instead of a destructive confirm', async () => {
    mockApiGet.mockResolvedValue({ data: [CARD_VISA] });
    const r = await renderScreen();
    const deleteBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByProps({ name: 'trash-outline' }).length > 0)!;
    act(() => {
      deleteBtn.props.onPress();
    });
    expect(allText(r)).toContain('Add a Card First');
    expect(mockApiDelete).not.toHaveBeenCalled();
  });

  it('warns that another card becomes default when deleting the default card (with others left)', async () => {
    mockApiGet.mockResolvedValue({ data: [CARD_VISA, CARD_MC] });
    mockApiDelete.mockResolvedValue({ data: {} });
    const r = await renderScreen();
    const deleteBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByProps({ name: 'trash-outline' }).length > 0)!;
    act(() => {
      deleteBtn.props.onPress();
    });
    expect(allText(r)).toContain('This is your default card. Another card will become your default after it is removed.');
    mockApiGet.mockClear();
    const confirmBtn = r.root.findByProps({ accessibilityLabel: 'confirm-Remove' });
    await act(async () => {
      await confirmBtn.props.onPress();
      await flush();
    });
    expect(mockApiDelete).toHaveBeenCalledWith('/payments/cards/card-1');
    expect(mockApiGet).toHaveBeenCalledWith('/payments/cards');
  });

  it('toasts when deleting a card fails', async () => {
    mockApiGet.mockResolvedValue({ data: [CARD_VISA, CARD_MC] });
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
    expect(mockShowToast).toHaveBeenCalledWith('Remove Failed', 'Could not remove card. Please try again.', 'danger');
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
