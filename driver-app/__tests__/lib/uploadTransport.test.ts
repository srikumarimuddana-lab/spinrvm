/**
 * Pins the transport used to POST a document upload.
 *
 * Background — the actual cause of the live "Unsupported FormDataPart
 * implementation" reports during driver signup:
 *
 * Expo SDK 54+ replaces global `fetch` with its WinterCG implementation
 * (expo/src/winter/runtime.native.ts installs it unless
 * EXPO_PUBLIC_USE_RN_FETCH=1, which this repo does not set). That
 * implementation accepts only a string, a Blob, or an object exposing
 * bytes() as a FormData part — see expo/src/winter/fetch/convertFormData.ts,
 * whose docstring states "`uri` is not supported for React Native's FormData"
 * and which has its own test asserting a { uri, name, type } part rejects
 * with /Unsupported FormDataPart implementation/.
 *
 * React Native's proprietary { uri, name, type } file descriptor is exactly
 * what an upload has to send, so the request threw on-device and never
 * reached the backend. XMLHttpRequest is untouched by the winter runtime and
 * still handles { uri } natively, so the upload goes over XHR.
 *
 * If someone "modernises" this back to fetch(), uploads break again with an
 * error that looks nothing like a networking bug. Hence this test.
 *
 * Code under test: shared/api/upload.ts::uploadFile / postMultipart
 */

jest.mock('../../../shared/config/spinr.config', () => ({
  __esModule: true,
  default: { backendUrl: 'https://api.example.test' },
}));

jest.mock('../../../shared/api/client', () => ({
  getAuthHeader: jest.fn().mockResolvedValue('token-abc'),
}));

jest.mock('../../../shared/store/authStore', () => ({
  useAuthStore: { getState: () => ({ refreshTokens: jest.fn().mockResolvedValue(false) }) },
}));

// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import { postMultipart, uploadFile } from '../../../shared/api/upload';

type XhrInstance = {
  open: jest.Mock;
  setRequestHeader: jest.Mock;
  send: jest.Mock;
  status: number;
  statusText: string;
  responseText: string;
  onload: (() => void) | null;
  onerror: (() => void) | null;
  onabort: (() => void) | null;
};

let lastXhr: XhrInstance;

function installXhrMock(status = 200, responseText = JSON.stringify({ url: 'https://signed/doc' })) {
  const xhr: XhrInstance = {
    open: jest.fn(),
    setRequestHeader: jest.fn(),
    send: jest.fn(function (this: void) {
      // Resolve asynchronously, the way a real request does.
      setImmediate(() => xhr.onload?.());
    }),
    status,
    statusText: 'OK',
    responseText,
    onload: null,
    onerror: null,
    onabort: null,
  };
  lastXhr = xhr;
  (global as unknown as { XMLHttpRequest: unknown }).XMLHttpRequest = jest.fn(() => xhr);
  return xhr;
}

describe('upload transport', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = jest.fn(() => {
      throw new Error('fetch must not be used for uploads — see this file’s header');
    }) as unknown as typeof fetch;
  });

  it('sends the upload over XMLHttpRequest, never fetch', async () => {
    installXhrMock();

    const url = await uploadFile('file:///tmp/licence.jpg', 'licence.jpg', 'image/jpeg');

    expect(url).toBe('https://signed/doc');
    expect(lastXhr.send).toHaveBeenCalledTimes(1);
    // The regression guard: Expo's fetch would have thrown on the { uri } part.
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('POSTs to the v1 upload route', async () => {
    installXhrMock();
    await uploadFile('file:///tmp/a.jpg', 'a.jpg', 'image/jpeg');
    expect(lastXhr.open).toHaveBeenCalledWith('POST', 'https://api.example.test/api/v1/upload');
  });

  it('sends a FormData body carrying the React Native uri descriptor', async () => {
    installXhrMock();
    // Assert via an append spy rather than reading the parts back: under
    // jest-expo the global FormData is the DOM one, which has no getParts()
    // and stringifies non-Blob values. On device it is React Native's, which
    // keeps the descriptor object for XHR to stream from disk.
    const appendSpy = jest.spyOn(FormData.prototype, 'append');

    await uploadFile('file:///tmp/a.jpg', 'a.jpg', 'image/jpeg');

    expect(lastXhr.send.mock.calls[0][0]).toBeInstanceOf(FormData);
    expect(appendSpy).toHaveBeenCalledWith('file', {
      uri: 'file:///tmp/a.jpg',
      name: 'a.jpg',
      type: 'image/jpeg',
    });
    appendSpy.mockRestore();
  });

  it('attaches the bearer token but never sets Content-Type', async () => {
    installXhrMock();
    await uploadFile('file:///tmp/a.jpg', 'a.jpg', 'image/jpeg');

    expect(lastXhr.setRequestHeader).toHaveBeenCalledWith('Authorization', 'Bearer token-abc');
    // Setting it by hand would omit the multipart boundary and the server
    // would fail to parse the body.
    const headerNames = lastXhr.setRequestHeader.mock.calls.map((c: string[]) => c[0].toLowerCase());
    expect(headerNames).not.toContain('content-type');
  });

  it('surfaces a non-2xx response with the server body', async () => {
    installXhrMock(400, '{"detail":"Unsupported file format"}');
    await expect(uploadFile('file:///tmp/a.bin', 'a.bin', 'image/jpeg')).rejects.toThrow(
      /Upload failed: 400 .*Unsupported file format/,
    );
  });

  it('rejects on a transport error rather than hanging', async () => {
    const xhr = installXhrMock();
    xhr.send = jest.fn(() => setImmediate(() => xhr.onerror?.()));
    await expect(
      postMultipart('https://api.example.test/api/v1/upload', new FormData()),
    ).rejects.toThrow('Network request failed');
  });
});
