/**
 * Regression test for the report-safety submit-error mapping.
 *
 * Finding: rider-app/app/report-safety.tsx caught the submit error, discarded
 * it, and showed a blanket "Failed to submit report" toast — swallowing a 429
 * (the rider keeps hammering submit) and violating CLAUDE.md's "Do not silently
 * swallow errors". submitErrorMessage() now surfaces the rate-limit retry hint
 * and the screen logs the error.
 *
 * Code under test: rider-app/utils/submitErrorMessage.ts
 */

import { submitErrorMessage } from '../submitErrorMessage';

const FALLBACK = 'Failed to submit report. Please try again.';

describe('submitErrorMessage', () => {
  it('surfaces the retry-after hint for a RateLimitError', () => {
    const err = { name: 'RateLimitError', retryAfterSeconds: 30 };
    expect(submitErrorMessage(err, FALLBACK)).toBe(
      'Too many requests — please try again in 30s.',
    );
  });

  it('falls back when a RateLimitError carries no positive retry-after', () => {
    expect(
      submitErrorMessage({ name: 'RateLimitError', retryAfterSeconds: 0 }, FALLBACK),
    ).toBe(FALLBACK);
    expect(submitErrorMessage({ name: 'RateLimitError' }, FALLBACK)).toBe(FALLBACK);
  });

  it('falls back for a generic / non-rate-limit API error', () => {
    expect(submitErrorMessage(new Error('boom'), FALLBACK)).toBe(FALLBACK);
    expect(
      submitErrorMessage({ name: 'SpinrApiError', status: 500 }, FALLBACK),
    ).toBe(FALLBACK);
  });

  it('falls back for null / undefined / non-object input', () => {
    expect(submitErrorMessage(null, FALLBACK)).toBe(FALLBACK);
    expect(submitErrorMessage(undefined, FALLBACK)).toBe(FALLBACK);
    expect(submitErrorMessage('nope', FALLBACK)).toBe(FALLBACK);
  });
});
