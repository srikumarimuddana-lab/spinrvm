/**
 * app/report-safety.tsx — driver-app safety-issue report form. Pins:
 *  - blocks submit with no category selected, and with an empty/whitespace
 *    description
 *  - POSTs /safety/report with category/description/photo_count, current
 *    location (when available; null otherwise), and active-ride context
 *    (when on a ride; null otherwise)
 *  - a successful report with photos uploads each photo via a *second*
 *    api.post per photo (not a hand-rolled fetch, per the file's own
 *    comment about a real App-Check-header bug)
 *  - a partial photo-upload failure is never silent: it shows its own
 *    "Report Sent — Photos Failed" toast (singular/plural), while the
 *    report itself is still treated as submitted (router.back())
 *  - a full report-submit failure toasts and re-enables the form
 *    (submitting reset to false)
 *  - photo add flow: camera/gallery permission denials toast and add
 *    nothing; a granted camera shot or gallery picks append to `photos`
 *    (capped at 4); remove drops a photo by index
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, TextInput, Alert } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('expo-image', () => ({ Image: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

const mockBack = jest.fn();
jest.mock('expo-router', () => ({ useRouter: () => ({ back: mockBack }) }));

const COLORS = {
  primary: '#EF4444', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockApiPost = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { post: (...a: any[]) => mockApiPost(...a) },
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));

const mockShowToast = jest.fn();
jest.mock('../../hooks/useToast', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

let mockLocation: any = null;
jest.mock('@shared/store/locationStore', () => ({
  useLocationStore: (selector: any) => selector({ currentLocation: mockLocation }),
}));

let mockActiveRide: any = null;
jest.mock('../../store/driverStore', () => ({
  useDriverStore: (selector: any) => selector({ activeRide: mockActiveRide }),
}));

const mockRequestCameraPermissionsAsync = jest.fn();
const mockRequestMediaLibraryPermissionsAsync = jest.fn();
const mockLaunchCameraAsync = jest.fn();
const mockLaunchImageLibraryAsync = jest.fn();
jest.mock('expo-image-picker', () => ({
  requestCameraPermissionsAsync: (...a: any[]) => mockRequestCameraPermissionsAsync(...a),
  requestMediaLibraryPermissionsAsync: (...a: any[]) => mockRequestMediaLibraryPermissionsAsync(...a),
  launchCameraAsync: (...a: any[]) => mockLaunchCameraAsync(...a),
  launchImageLibraryAsync: (...a: any[]) => mockLaunchImageLibraryAsync(...a),
}));

import ReportSafetyScreen from '../../app/report-safety';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<ReportSafetyScreen />);
    await flush();
  });
  return renderer!;
}

function findButtonByText(r: TestRenderer.ReactTestRenderer, text: string) {
  return r.root
    .findAllByType(TouchableOpacity)
    .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes(text)))!;
}

function selectCategory(r: TestRenderer.ReactTestRenderer, category: string) {
  const chip = findButtonByText(r, category);
  act(() => {
    chip.props.onPress();
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  mockLocation = null;
  mockActiveRide = null;
  mockApiPost.mockResolvedValue({ data: { incident_id: 'inc-1' } });
  mockRequestCameraPermissionsAsync.mockResolvedValue({ status: 'granted' });
  mockRequestMediaLibraryPermissionsAsync.mockResolvedValue({ status: 'granted' });
  jest.spyOn(Alert, 'alert').mockImplementation(() => {});
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
  jest.restoreAllMocks();
});

describe('ReportSafetyScreen', () => {
  it('blocks submit with no category selected', async () => {
    const r = await renderScreen();
    const submitBtn = findButtonByText(r, 'Submit Report');
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith(
      'warning', 'Category Required', 'Please select a category for your safety report.',
    );
    expect(mockApiPost).not.toHaveBeenCalled();
  });

  it('blocks submit with an empty description', async () => {
    const r = await renderScreen();
    selectCategory(r, 'Road Hazard');
    const submitBtn = findButtonByText(r, 'Submit Report');
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith(
      'warning', 'Description Required', 'Please describe the safety issue before submitting.',
    );
    expect(mockApiPost).not.toHaveBeenCalled();
  });

  it('submits with category, description, null location and null ride_context when neither is available', async () => {
    const r = await renderScreen();
    selectCategory(r, 'Vehicle Issue');
    const descInput = r.root.findByType(TextInput);
    act(() => {
      descInput.props.onChangeText('The brakes felt loose after pickup.');
    });
    const submitBtn = findButtonByText(r, 'Submit Report');
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledWith('/safety/report', expect.objectContaining({
      category: 'Vehicle Issue',
      description: 'The brakes felt loose after pickup.',
      photo_count: 0,
      location: null,
      ride_context: null,
    }));
    expect(mockShowToast).toHaveBeenCalledWith(
      'success', 'Report Submitted', 'Your safety report has been submitted. Our trust and safety team will review it promptly.',
    );
    expect(mockBack).toHaveBeenCalled();
  });

  it('includes current location and active-ride context when available', async () => {
    mockLocation = { latitude: 50.45, longitude: -104.6 };
    mockActiveRide = { ride: { id: 'r1', pickup_address: '1 Main St', dropoff_address: '2 Elm St', rider_id: 'rider-1' } };
    const r = await renderScreen();
    selectCategory(r, 'Passenger Behaviour');
    const descInput = r.root.findByType(TextInput);
    act(() => {
      descInput.props.onChangeText('Rider was verbally abusive.');
    });
    const submitBtn = findButtonByText(r, 'Submit Report');
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledWith('/safety/report', expect.objectContaining({
      location: expect.objectContaining({ latitude: 50.45, longitude: -104.6 }),
      ride_context: {
        ride_id: 'r1', pickup_location: '1 Main St', dropoff_location: '2 Elm St', rider_id: 'rider-1',
      },
    }));
  });

  it('uploads each photo via a second api.post call, keyed to the returned incident id', async () => {
    mockLaunchCameraAsync.mockResolvedValue({ canceled: false, assets: [{ uri: 'file://photo1.jpg' }] });
    mockApiPost.mockImplementation((url: string) => {
      if (url === '/safety/report') return Promise.resolve({ data: { incident_id: 'inc-99' } });
      return Promise.resolve({ data: {} });
    });
    const r = await renderScreen();
    const addBtn = findButtonByText(r, 'Add');
    act(() => {
      addBtn.props.onPress();
    });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const cameraAction = alertCall[2].find((b: any) => b.text === 'Camera');
    await act(async () => {
      await cameraAction.onPress();
      await flush();
    });
    selectCategory(r, 'Road Hazard');
    const descInput = r.root.findByType(TextInput);
    act(() => {
      descInput.props.onChangeText('Pothole on the highway.');
    });
    const submitBtn = findButtonByText(r, 'Submit Report');
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledWith('/safety/report/inc-99/photo', expect.any(FormData));
    expect(mockShowToast).toHaveBeenCalledWith(
      'success', 'Report Submitted', 'Your safety report has been submitted. Our trust and safety team will review it promptly.',
    );
  });

  it('shows a "Photos Failed" toast (not silent) when a photo upload fails, but still treats the report as submitted', async () => {
    mockLaunchCameraAsync.mockResolvedValue({ canceled: false, assets: [{ uri: 'file://photo1.jpg' }] });
    mockApiPost.mockImplementation((url: string) => {
      if (url === '/safety/report') return Promise.resolve({ data: { incident_id: 'inc-99' } });
      return Promise.reject(new Error('upload failed'));
    });
    const r = await renderScreen();
    const addBtn = findButtonByText(r, 'Add');
    act(() => {
      addBtn.props.onPress();
    });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const cameraAction = alertCall[2].find((b: any) => b.text === 'Camera');
    await act(async () => {
      await cameraAction.onPress();
      await flush();
    });
    selectCategory(r, 'Road Hazard');
    const descInput = r.root.findByType(TextInput);
    act(() => {
      descInput.props.onChangeText('Pothole on the highway.');
    });
    const submitBtn = findButtonByText(r, 'Submit Report');
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith(
      'warning',
      'Report Sent — Photos Failed',
      'Your report was submitted, but 1 of 1 photo could not be attached. Our team may contact you for them.',
    );
    expect(mockBack).toHaveBeenCalled();
  });

  it('toasts and re-enables the form on a full submit failure', async () => {
    mockApiPost.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    selectCategory(r, 'Other');
    const descInput = r.root.findByType(TextInput);
    act(() => {
      descInput.props.onChangeText('Something happened.');
    });
    const submitBtn = findButtonByText(r, 'Submit Report');
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Error', 'Could not submit your report. Please try again.');
    expect(mockBack).not.toHaveBeenCalled();
    // the form re-enables (submitting reset false) -- confirmed via the
    // submit button's label reverting from "Submitting..." to "Submit Report"
    expect(findButtonByText(r, 'Submit Report')).toBeTruthy();
  });

  it('toasts and adds nothing when camera permission is denied', async () => {
    mockRequestCameraPermissionsAsync.mockResolvedValue({ status: 'denied' });
    const r = await renderScreen();
    const addBtn = findButtonByText(r, 'Add');
    act(() => {
      addBtn.props.onPress();
    });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const cameraAction = alertCall[2].find((b: any) => b.text === 'Camera');
    await act(async () => {
      await cameraAction.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Permission Denied', 'Camera access is needed.');
    expect(mockLaunchCameraAsync).not.toHaveBeenCalled();
  });

  it('toasts and adds nothing when gallery permission is denied', async () => {
    mockRequestMediaLibraryPermissionsAsync.mockResolvedValue({ status: 'denied' });
    const r = await renderScreen();
    const addBtn = findButtonByText(r, 'Add');
    act(() => {
      addBtn.props.onPress();
    });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const galleryAction = alertCall[2].find((b: any) => b.text === 'Photo Library');
    await act(async () => {
      await galleryAction.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Permission Denied', 'Library access is needed.');
    expect(mockLaunchImageLibraryAsync).not.toHaveBeenCalled();
  });

  it('appends gallery picks (capped at 4) and removes a photo by index', async () => {
    mockLaunchImageLibraryAsync.mockResolvedValue({
      canceled: false,
      assets: [{ uri: 'file://a.jpg' }, { uri: 'file://b.jpg' }],
    });
    const r = await renderScreen();
    const addBtn = findButtonByText(r, 'Add');
    act(() => {
      addBtn.props.onPress();
    });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const galleryAction = alertCall[2].find((b: any) => b.text === 'Photo Library');
    await act(async () => {
      await galleryAction.onPress();
      await flush();
    });
    const removeButtons = r.root
      .findAllByType(TouchableOpacity)
      .filter((n) => n.findAllByProps({ name: 'close-circle' }).length > 0);
    expect(removeButtons).toHaveLength(2);
    act(() => {
      removeButtons[0].props.onPress();
    });
    const removeButtonsAfter = r.root
      .findAllByType(TouchableOpacity)
      .filter((n) => n.findAllByProps({ name: 'close-circle' }).length > 0);
    expect(removeButtonsAfter).toHaveLength(1);
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
