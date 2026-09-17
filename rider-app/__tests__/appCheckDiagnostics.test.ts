import { formatAppCheckError } from '../../shared/utils/appCheckDiagnostics';

describe('formatAppCheckError', () => {
  it('keeps the native code and message while excluding unbounded error data', () => {
    const error = Object.assign(new Error('Play Integrity request failed'), {
      code: 'appCheck/token-fetch-failed',
      nativeError: 'API_NOT_AVAILABLE',
      token: 'must-not-be-logged',
    });

    const details = JSON.parse(formatAppCheckError(error));

    expect(details).toEqual({
      code: 'appCheck/token-fetch-failed',
      message: 'Play Integrity request failed',
      nativeError: 'API_NOT_AVAILABLE',
    });
    expect(formatAppCheckError(error)).not.toContain('must-not-be-logged');
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
