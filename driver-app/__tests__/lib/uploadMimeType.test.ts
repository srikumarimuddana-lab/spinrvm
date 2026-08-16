/**
 * Pins the MIME type the signup upload paths declare for a picked file.
 *
 * Background: driver signup uploads failed with "unsupported format" because
 * neither picker reports a usable MIME type:
 *   - expo-image-picker's `asset.type` is the media *category*
 *     ('image' | 'video'), so become-driver.tsx sent 'image/jpeg' for every
 *     asset and a gallery PNG/GIF was rejected by the backend's content check.
 *   - Android's document picker often reports 'application/octet-stream', and
 *     the old `asset.mimeType || 'image/jpeg'` fallback turned a PDF into a
 *     declared JPEG.
 *
 * The backend now sniffs the real type from the file's bytes, so this is
 * defence in depth rather than the only guard — but a correct declared type is
 * what the backend falls back to for any format it has no signature for.
 *
 * Imported by relative path, not '@shared/api/upload': this project's jest
 * moduleNameMapper redirects '@shared/*' to __mocks__/@shared/*, and the point
 * of this test is to exercise the real implementation. jest.config's
 * modulePaths entry exists precisely so out-of-rootDir shared modules resolve
 * their babel-runtime helpers here.
 *
 * Code under test: shared/api/upload.ts::resolveUploadMimeType
 */

jest.mock('../../../shared/config/spinr.config', () => ({
  __esModule: true,
  default: { backendUrl: 'http://localhost:8000' },
}));

jest.mock('../../../shared/api/client', () => ({
  getAuthHeader: jest.fn(),
}));

// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import { resolveUploadMimeType } from '../../../shared/api/upload';

describe('resolveUploadMimeType', () => {
  it.each([
    ['photo_1.jpg', 'image/jpeg'],
    ['scan.jpeg', 'image/jpeg'],
    ['IMG_0001.PNG', 'image/png'],
    ['fun.GIF', 'image/gif'],
    ['shot.webp', 'image/webp'],
    ['licence.pdf', 'application/pdf'],
  ])('derives the type from %s', (name, expected) => {
    expect(resolveUploadMimeType(name)).toBe(expected);
  });

  it("ignores expo-image-picker's media category, which is not a MIME type", () => {
    // asset.type === 'image' for every image the picker returns.
    expect(resolveUploadMimeType('IMG_0001.PNG', 'image')).toBe('image/png');
    expect(resolveUploadMimeType('Document', 'image')).toBe('image/jpeg');
  });

  it('prefers the extension over a picker type that disagrees with it', () => {
    // The old code sent 'image/jpeg' here and the upload 400'd.
    expect(resolveUploadMimeType('licence.pdf', 'image/jpeg')).toBe('application/pdf');
  });

  it('ignores application/octet-stream from the Android document picker', () => {
    expect(resolveUploadMimeType('licence.pdf', 'application/octet-stream')).toBe('application/pdf');
  });

  it('falls back to a real picker MIME type when the extension is unknown', () => {
    expect(resolveUploadMimeType('Document', 'application/pdf')).toBe('application/pdf');
  });

  it('declares image/jpeg for an iOS .HEIC filename', () => {
    // expo-image-picker re-encodes to JPEG whenever `quality` is set but keeps
    // the original PHAsset filename, so the bytes really are JPEG here. A
    // genuine HEIF file is caught by the backend byte sniff instead.
    expect(resolveUploadMimeType('IMG_0002.HEIC')).toBe('image/jpeg');
  });

  it('handles a bare URI with a query string', () => {
    expect(resolveUploadMimeType('file:///tmp/cache/img.png?ts=123')).toBe('image/png');
  });

  it('defaults to image/jpeg when nothing is knowable', () => {
    expect(resolveUploadMimeType('', null)).toBe('image/jpeg');
    expect(resolveUploadMimeType('Document', undefined)).toBe('image/jpeg');
  });
});
