/**
 * driver-app coverage for the ERR_* sentinel mapping.
 *
 * This file exists because driver-app maps every '@shared/*' import into
 * __mocks__, and the client stub used to reimplement getApiErrorMessage as
 * "return detail verbatim". That meant a driver-app test could feed
 * {detail: 'ERR_SESSION_REVOKED'} and assert the raw token as expected copy —
 * passing while production showed the mapped sentence. The stub now delegates
 * to the real, dependency-free helpers; these tests hold it to that.
 */
import { SENTINEL_MESSAGES, messageForSentinel } from '@shared/errors/sentinelMessages';
import { getApiErrorMessage } from '@shared/api/client';
import { TOAST_MESSAGE_MAX } from '@shared/utils/toastMessage';

const FALLBACK = 'Something went wrong. Please try again.';
const body = (detail: string) => ({ response: { status: 400, data: { detail } } });

describe('driver-app resolves ERR_* sentinels to real copy', () => {
  it('renders the mapped sentence for a session revoked mid-shift', () => {
    expect(getApiErrorMessage(body('ERR_SESSION_REVOKED'), FALLBACK)).toBe(
      "You've been signed out. Please sign in again.",
    );
  });

  it('never renders a raw token for any mapped sentinel', () => {
    for (const sentinel of Object.keys(SENTINEL_MESSAGES)) {
      const out = getApiErrorMessage(body(sentinel), FALLBACK);
      expect(out).not.toContain('ERR_');
      expect(out).not.toBe(FALLBACK);
    }
  });

  it('leaves an unmapped sentinel on the caller fallback', () => {
    expect(getApiErrorMessage(body('ERR_NOT_IN_THE_TABLE'), FALLBACK)).toBe(FALLBACK);
  });

  it('does not mistake an Object.prototype member for mapped copy', () => {
    for (const key of ['constructor', 'toString', '__proto__', 'hasOwnProperty']) {
      expect(messageForSentinel(key)).toBeUndefined();
      expect(() => getApiErrorMessage(body(key), FALLBACK)).not.toThrow();
    }
  });

  it('keeps every mapped sentence inside the toast budget', () => {
    for (const copy of Object.values(SENTINEL_MESSAGES)) {
      expect(copy.length).toBeLessThanOrEqual(TOAST_MESSAGE_MAX);
    }
  });
});
