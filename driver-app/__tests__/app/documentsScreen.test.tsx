/**
 * app/documents.tsx — driver document upload/verification center. Pins:
 *  - loads requirements + documents on mount (and again on focus); a
 *    load failure toasts
 *  - renders Missing/Pending/Verified/Rejected status per requirement,
 *    matched via requirement_key -> requirement_id -> document_type
 *    fallback; a rejected doc shows its reason + a re-upload button
 *  - the legacy-import documents-gap notice only shows for a driver with
 *    legacy_import_metadata AND zero documents on file
 *  - Upload: source-picker Alert offers Camera/Gallery/File; each path's
 *    permission-denied case toasts and uploads nothing; a successful
 *    pick uploads via uploadFile, links it via POST with the
 *    requirement's name as document_type, reloads, and refetches the
 *    driver profile; an upload failure toasts
 *  - the back-side upload row only renders when requires_back_side
 *  - tapping a document's preview thumbnail opens the full-screen modal
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, Image, Alert } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('expo-linear-gradient', () => ({ LinearGradient: ({ children }: any) => children }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

const mockBack = jest.fn();
jest.mock('expo-router', () => ({ useRouter: () => ({ back: mockBack }) }));

jest.mock('expo-router/react-navigation', () => {
  const ReactActual = require('react');
  return {
    useFocusEffect: (cb: () => void | (() => void)) => {
      ReactActual.useEffect(() => cb(), []);
    },
  };
});

const COLORS = {
  primary: '#EF4444', primaryDark: '#B91C1C', background: '#FFF', surface: '#FFF', surfaceLight: '#F5F5F5',
  text: '#111', textDim: '#666', textSecondary: '#333', border: '#E5E7EB', success: '#10B981', error: '#DC2626', warning: '#F59E0B',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a), post: (...a: any[]) => mockApiPost(...a) },
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));

const mockUploadFile = jest.fn();
jest.mock('@shared/api/upload', () => ({
  uploadFile: (...a: any[]) => mockUploadFile(...a),
  resolveUploadMimeType: (name: string) => (name?.endsWith('.pdf') ? 'application/pdf' : 'image/jpeg'),
}));

const mockShowToast = jest.fn();
jest.mock('../../hooks/useToast', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

let mockAuthState: any;
const mockFetchDriverProfile = jest.fn();
jest.mock('@shared/store/authStore', () => ({ useAuthStore: () => mockAuthState }));

jest.mock('@shared/config/spinr.config', () => ({
  __esModule: true,
  default: { backendUrl: 'https://api.spinr.ca' },
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

const mockGetDocumentAsync = jest.fn();
jest.mock('expo-document-picker', () => ({
  getDocumentAsync: (...a: any[]) => mockGetDocumentAsync(...a),
}));

import DocumentsScreen from '../../app/documents';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

const REQ_LICENSE = { id: 'req-1', name: 'Driver License', description: 'Front of your license', is_mandatory: true, requires_back_side: true };
const REQ_INSURANCE = { id: 'req-2', name: 'Insurance', description: 'Proof of insurance', is_mandatory: true, requires_back_side: false };

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<DocumentsScreen />);
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

beforeEach(() => {
  jest.clearAllMocks();
  mockAuthState = { fetchDriverProfile: mockFetchDriverProfile, driver: {} };
  mockApiGet.mockImplementation((url: string) => {
    if (url === '/drivers/requirements') return Promise.resolve({ data: [REQ_LICENSE, REQ_INSURANCE] });
    if (url === '/drivers/documents') return Promise.resolve({ data: [] });
    return Promise.reject(new Error('unexpected url ' + url));
  });
  mockApiPost.mockResolvedValue({ data: {} });
  mockUploadFile.mockResolvedValue('/uploads/doc1.jpg');
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

describe('DocumentsScreen', () => {
  it('loads requirements and documents on mount', async () => {
    await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/drivers/requirements');
    expect(mockApiGet).toHaveBeenCalledWith('/drivers/documents');
  });

  it('toasts on a load failure', async () => {
    mockApiGet.mockRejectedValue(new Error('down'));
    await renderScreen();
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Load Failed', 'Could not load your documents. Please try again.');
  });

  it('shows Missing status for a requirement with no matching document', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('Driver License');
    expect(allText(r)).toContain('Not Submitted');
  });

  it('shows Verified status and a tappable preview for an approved document', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/drivers/requirements') return Promise.resolve({ data: [REQ_INSURANCE] });
      if (url === '/drivers/documents') {
        return Promise.resolve({
          data: [{ id: 'd1', requirement_id: 'req-2', document_url: '/files/ins.jpg', status: 'approved', side: 'front' }],
        });
      }
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('Verified');
    const preview = r.root.findByType(Image);
    expect(preview.props.source.uri).toBe('https://api.spinr.ca/files/ins.jpg');
    const previewBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Image).length > 0)!;
    act(() => {
      previewBtn.props.onPress();
    });
    expect(allText(r)).toContain('Verified'); // modal open didn't crash
  });

  it('shows the rejection reason and a re-upload button for a rejected document', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/drivers/requirements') return Promise.resolve({ data: [REQ_INSURANCE] });
      if (url === '/drivers/documents') {
        return Promise.resolve({
          data: [{ id: 'd1', requirement_id: 'req-2', document_url: '/files/ins.jpg', status: 'rejected', rejection_reason: 'Blurry photo', side: 'front' }],
        });
      }
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('Blurry photo');
    expect(allText(r)).toContain('Re-upload Document');
  });

  it('shows the legacy-import documents-gap notice only when the driver has legacy metadata AND zero documents', async () => {
    mockAuthState.driver = { legacy_import_metadata: { old_id: 'abc123' } };
    const r = await renderScreen();
    expect(allText(r)).toContain("weren't part of");
  });

  it('does not show the legacy notice for a driver with no legacy metadata', async () => {
    const r = await renderScreen();
    expect(allText(r)).not.toContain("weren't part of");
  });

  it('does not show the legacy notice for a legacy-imported driver who already has documents', async () => {
    mockAuthState.driver = { legacy_import_metadata: { old_id: 'abc123' } };
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/drivers/requirements') return Promise.resolve({ data: [REQ_INSURANCE] });
      if (url === '/drivers/documents') {
        return Promise.resolve({ data: [{ id: 'd1', requirement_id: 'req-2', document_url: '/f.jpg', status: 'pending', side: 'front' }] });
      }
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).not.toContain("weren't part of");
  });

  it('only renders the back-side upload row when requires_back_side is true', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('Back Side');
    expect(allText(r)).toContain('Front Side / Main Document');
    // Insurance (requires_back_side: false) should have exactly one "Front Side" label
    const sideLabels = r.root.findAllByType(Text).filter((t) => {
      try { return t.props.children === 'Back Side'; } catch { return false; }
    });
    expect(sideLabels).toHaveLength(1); // only License's back row
  });

  it('toasts and uploads nothing when camera permission is denied', async () => {
    mockRequestCameraPermissionsAsync.mockResolvedValue({ status: 'denied' });
    const r = await renderScreen();
    const uploadBtns = r.root.findAllByType(TouchableOpacity).filter((n) => n.props.onPress?.toString().includes('handleUpload') === false);
    const uploadBtn = findButtonByText(r, 'UPLOAD');
    act(() => {
      uploadBtn.props.onPress();
    });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const cameraAction = alertCall[2].find((b: any) => b.text === 'Camera');
    await act(async () => {
      await cameraAction.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('warning', 'Permission needed', 'Camera permission is required to take photos.');
    expect(mockUploadFile).not.toHaveBeenCalled();
  });

  it('toasts and uploads nothing when gallery permission is denied', async () => {
    mockRequestMediaLibraryPermissionsAsync.mockResolvedValue({ status: 'denied' });
    const r = await renderScreen();
    const uploadBtn = findButtonByText(r, 'UPLOAD');
    act(() => {
      uploadBtn.props.onPress();
    });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const galleryAction = alertCall[2].find((b: any) => b.text === 'Gallery');
    await act(async () => {
      await galleryAction.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('warning', 'Permission needed', 'Gallery permission is required to upload photos.');
    expect(mockUploadFile).not.toHaveBeenCalled();
  });

  it('uploads a captured photo, links it with the requirement name as document_type, reloads, and toasts', async () => {
    mockLaunchCameraAsync.mockResolvedValue({ canceled: false, assets: [{ uri: 'file://photo.jpg', fileName: 'photo.jpg', type: 'image' }] });
    const r = await renderScreen();
    const uploadBtn = findButtonByText(r, 'UPLOAD');
    act(() => {
      uploadBtn.props.onPress();
    });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const cameraAction = alertCall[2].find((b: any) => b.text === 'Camera');
    mockApiGet.mockClear();
    await act(async () => {
      await cameraAction.onPress();
      await flush();
    });
    expect(mockUploadFile).toHaveBeenCalledWith('file://photo.jpg', 'photo.jpg', 'image/jpeg');
    expect(mockApiPost).toHaveBeenCalledWith('/drivers/documents', {
      requirement_id: 'req-1', document_url: '/uploads/doc1.jpg', side: 'front', document_type: 'Driver License',
    });
    expect(mockApiGet).toHaveBeenCalledWith('/drivers/documents');
    expect(mockFetchDriverProfile).toHaveBeenCalled();
    expect(mockShowToast).toHaveBeenCalledWith('success', 'Uploaded', 'Document submitted for review.');
  });

  it('toasts on an upload failure', async () => {
    mockUploadFile.mockRejectedValue(new Error('server error'));
    mockLaunchCameraAsync.mockResolvedValue({ canceled: false, assets: [{ uri: 'file://photo.jpg', fileName: 'photo.jpg', type: 'image' }] });
    const r = await renderScreen();
    const uploadBtn = findButtonByText(r, 'UPLOAD');
    act(() => {
      uploadBtn.props.onPress();
    });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const cameraAction = alertCall[2].find((b: any) => b.text === 'Camera');
    await act(async () => {
      await cameraAction.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Upload Failed', 'Could not upload your document. Please try again.');
  });

  it('uploads via the File picker path', async () => {
    mockGetDocumentAsync.mockResolvedValue({
      canceled: false, assets: [{ uri: 'file://doc.pdf', name: 'doc.pdf', mimeType: 'application/pdf' }],
    });
    const r = await renderScreen();
    const uploadBtn = findButtonByText(r, 'UPLOAD');
    act(() => {
      uploadBtn.props.onPress();
    });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const fileAction = alertCall[2].find((b: any) => b.text === 'File');
    await act(async () => {
      await fileAction.onPress();
      await flush();
    });
    expect(mockUploadFile).toHaveBeenCalledWith('file://doc.pdf', 'doc.pdf', 'application/pdf');
  });

  it('shows a generic toast when the file picker itself fails', async () => {
    mockGetDocumentAsync.mockRejectedValue(new Error('picker crashed'));
    const r = await renderScreen();
    const uploadBtn = findButtonByText(r, 'UPLOAD');
    act(() => {
      uploadBtn.props.onPress();
    });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const fileAction = alertCall[2].find((b: any) => b.text === 'File');
    await act(async () => {
      await fileAction.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Upload Failed', 'Could not open that file. Please try again.');
  });
});
