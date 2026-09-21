import {
  _resetAppCheckFailureReportsForTests,
  formatAppCheckError,
  reportAppCheckFailure,
} from '../../shared/utils/appCheckDiagnostics';
import { captureMessage, isSentryActive } from '../../shared/services/errorReporting';

jest.mock('../../shared/services/errorReporting', () => ({
  captureMessage: jest.fn(),
  isSentryActive: jest.fn(() => true),
}));

// Mirrors @react-native-firebase/app's NativeFirebaseError: `code`, `message`,
// `nativeErrorCode` and `nativeErrorMessage` are non-enumerable properties, and
// `message` is prefixed with the code. There is no `nativeError` field.
function nativeFirebaseError(overrides: {
  code?: string;
  message?: string;
  nativeErrorCode?: string | number | null;
  nativeErrorMessage?: string | null;
} = {}): Error {
  const code = overrides.code ?? 'app-check/token-error';
  const message = overrides.message ?? 'aa.l: Too many attempts.';
  const err = new Error(`[${code}] ${message}`);
  Object.defineProperty(err, 'code', { value: code, enumerable: false });
  Object.defineProperty(err, 'nativeErrorCode', {
    value: overrides.nativeErrorCode ?? null,
    enumerable: false,
  });
  Object.defineProperty(err, 'nativeErrorMessage', {
    value: overrides.nativeErrorMessage ?? null,
    enumerable: false,
  });
  Object.defineProperty(err, 'userInfo', {
    value: { token: 'must-not-be-logged', message },
    enumerable: false,
  });
  return err;
}

describe('formatAppCheckError', () => {
  it('keeps the native code, message and native reason while excluding unbounded error data', () => {
    const error = nativeFirebaseError({
      nativeErrorCode: 403,
      nativeErrorMessage: 'App attestation failed.',
    });

    const details = JSON.parse(formatAppCheckError(error));

    expect(details).toEqual({
      code: 'app-check/token-error',
      message: '[app-check/token-error] aa.l: Too many attempts.',
      nativeErrorCode: '403',
      nativeErrorMessage: 'App attestation failed.',
    });
    expect(formatAppCheckError(error)).not.toContain('must-not-be-logged');
  });

  it('omits native fields that the SDK left null', () => {
    const details = JSON.parse(formatAppCheckError(nativeFirebaseError()));
    expect(details).toEqual({
      code: 'app-check/token-error',
      message: '[app-check/token-error] aa.l: Too many attempts.',
    });
  });

  it('bounds long native messages', () => {
    const details = JSON.parse(formatAppCheckError({
      code: 'appCheck/error',
      message: 'x'.repeat(600),
    }));

    expect(details.message).toHaveLength(501);
    expect(details.message.endsWith('…')).toBe(true);
  });
});

describe('reportAppCheckFailure', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (isSentryActive as jest.Mock).mockReturnValue(true);
    _resetAppCheckFailureReportsForTests();
  });

  it('sends a fingerprinted error-level Sentry message with the native code as a tag', () => {
    reportAppCheckFailure('token', nativeFirebaseError({ nativeErrorMessage: 'App attestation failed.' }));

    expect(captureMessage).toHaveBeenCalledTimes(1);
    const [message, level, options] = (captureMessage as jest.Mock).mock.calls[0];
    expect(level).toBe('error');
    expect(message).toContain('[AppCheck] token fetch failed');
    expect(message).toContain('App attestation failed.');
    expect(message).not.toContain('must-not-be-logged');
    expect(options.fingerprint).toEqual(['appcheck-failure', 'token', 'app-check/token-error']);
    expect(options.tags).toMatchObject({
      domain: 'auth',
      appcheck_stage: 'token',
      appcheck_code: 'app-check/token-error',
    });
  });

  it('reports the init stage under its own fingerprint', () => {
    reportAppCheckFailure('init', new Error('no provider or no provider options defined'));

    const [message, , options] = (captureMessage as jest.Mock).mock.calls[0];
    expect(message).toContain('[AppCheck] init failed');
    expect(options.fingerprint).toEqual(['appcheck-failure', 'init', 'unknown']);
    expect(options.tags).toMatchObject({ appcheck_stage: 'init', appcheck_code: 'unknown' });
  });

  it('reports each distinct failure once per process so per-request retries do not flood Sentry', () => {
    reportAppCheckFailure('token', nativeFirebaseError());
    reportAppCheckFailure('token', nativeFirebaseError());
    reportAppCheckFailure('token', nativeFirebaseError());
    expect(captureMessage).toHaveBeenCalledTimes(1);

    reportAppCheckFailure('token', nativeFirebaseError({ code: 'app-check/other' }));
    expect(captureMessage).toHaveBeenCalledTimes(2);
  });

  it('dedupes on a bounded message prefix so a per-attempt suffix cannot defeat it', () => {
    for (let i = 0; i < 50; i++) {
      reportAppCheckFailure(
        'token',
        nativeFirebaseError({ message: `${'stable prefix '.repeat(10)} attempt=${i}` }),
      );
    }
    expect(captureMessage).toHaveBeenCalledTimes(1);
  });

  it('caps the number of distinct failures it will ever report in one process', () => {
    for (let i = 0; i < 100; i++) {
      reportAppCheckFailure('token', nativeFirebaseError({ code: `app-check/reason-${i}` }));
    }
    expect((captureMessage as jest.Mock).mock.calls.length).toBeLessThanOrEqual(20);
  });

  it('forwards the bounded diagnostics to the native recorder when one is given', () => {
    const recordNative = jest.fn();
    reportAppCheckFailure('token', nativeFirebaseError(), recordNative);

    expect(recordNative).toHaveBeenCalledTimes(1);
    const recorded: Error = recordNative.mock.calls[0][0];
    expect(recorded).toBeInstanceOf(Error);
    expect(recorded.message).toContain('[AppCheck] token fetch failed');
    expect(recorded.message).not.toContain('must-not-be-logged');
  });

  it('does not mark a failure as sent while Sentry is uninitialised, so it is reported once Sentry comes up', () => {
    (isSentryActive as jest.Mock).mockReturnValue(false);
    const recordNative = jest.fn();

    reportAppCheckFailure('token', nativeFirebaseError(), recordNative);
    reportAppCheckFailure('token', nativeFirebaseError(), recordNative);
    expect(captureMessage).not.toHaveBeenCalled();
    expect(recordNative).toHaveBeenCalledTimes(1);

    (isSentryActive as jest.Mock).mockReturnValue(true);
    reportAppCheckFailure('token', nativeFirebaseError(), recordNative);
    expect(captureMessage).toHaveBeenCalledTimes(1);
    expect(recordNative).toHaveBeenCalledTimes(1);
  });

  it('never throws when reporting itself fails', () => {
    (captureMessage as jest.Mock).mockImplementation(() => { throw new Error('sentry down'); });
    const recordNative = jest.fn(() => { throw new Error('crashlytics down'); });

    expect(() => reportAppCheckFailure('token', nativeFirebaseError(), recordNative)).not.toThrow();
  });
});
