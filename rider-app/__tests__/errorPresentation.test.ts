/**
 * Error → toast presentation decision layer.
 *
 * Pins that toast titles/severities are derived from the backend's structured
 * error contract (numeric code + details.field), not from regex on the human
 * message. The signup duplicate-email case is the anchor: a
 * RESOURCE_ALREADY_EXISTS with details.field='email' must title
 * "Email Already In Use".
 *
 * Code under test: shared/errors/errorPresentation.ts
 */

import {
  presentError,
  getErrorCode,
  getErrorField,
} from '@shared/errors/errorPresentation';
import { TOAST_TITLE_MAX } from '@shared/utils/toastMessage';

// Shapes mirror what the API client throws.
const spinrApiError = (code: number, details?: Record<string, unknown>) => ({
  name: 'SpinrApiError',
  code,
  details,
  response: { status: 400, data: { error: { code, details } } },
});
const axiosLike = (code: number, details?: Record<string, unknown>) => ({
  response: { status: 400, data: { error: { code, details } } },
});

describe('getErrorCode', () => {
  it('reads SpinrApiError.code, axios body code, and RateLimitError by name', () => {
    expect(getErrorCode(spinrApiError(3002))).toBe(3002);
    expect(getErrorCode(axiosLike(6001))).toBe(6001);
    expect(getErrorCode({ name: 'RateLimitError', status: 429 })).toBe(9003);
  });
  it('returns 0 for unknown / unstructured errors', () => {
    expect(getErrorCode(new Error('boom'))).toBe(0);
    expect(getErrorCode(null)).toBe(0);
    expect(getErrorCode('nope')).toBe(0);
  });
});

describe('getErrorField', () => {
  it('reads details.field from both SpinrApiError and raw body shapes', () => {
    expect(getErrorField(spinrApiError(3002, { field: 'email' }))).toBe('email');
    expect(getErrorField(axiosLike(3002, { field: 'phone' }))).toBe('phone');
  });
  it('is undefined when no field is present', () => {
    expect(getErrorField(spinrApiError(3002))).toBeUndefined();
    expect(getErrorField(new Error('x'))).toBeUndefined();
  });
});

describe('presentError', () => {
  it('titles a duplicate-email error "Email Already In Use" from code+field', () => {
    const p = presentError(spinrApiError(3002, { field: 'email' }), { fallbackTitle: 'Profile Not Saved' });
    expect(p.title).toBe('Email Already In Use');
    expect(p.severity).toBe('error');
    expect(p.code).toBe(3002);
  });

  it('titles a duplicate-phone error "Phone Already In Use"', () => {
    expect(presentError(axiosLike(3002, { field: 'phone' }), { fallbackTitle: 'X' }).title)
      .toBe('Phone Already In Use');
  });

  it('falls back to a generic "already" title when the field is unknown', () => {
    expect(presentError(spinrApiError(9006), { fallbackTitle: 'X' }).title).toBe('Already Registered');
  });

  it('maps a rate-limit code to a warning-severity title', () => {
    const p = presentError({ name: 'RateLimitError', status: 429 }, { fallbackTitle: 'Sign-in Failed' });
    expect(p.title).toBe('Too Many Attempts');
    expect(p.severity).toBe('warning');
  });

  it('maps a payment-declined code to an error-severity title', () => {
    const p = presentError(axiosLike(6001), { fallbackTitle: 'Booking Failed' });
    expect(p.title).toBe('Payment Failed');
    expect(p.severity).toBe('error');
  });

  it('uses the caller fallbackTitle at error severity for unmapped/unknown codes', () => {
    const p = presentError(new Error('network'), { fallbackTitle: 'Booking Failed' });
    expect(p.title).toBe('Booking Failed');
    expect(p.severity).toBe('error');
    expect(p.code).toBe(0);
  });

  it('clamps an over-long fallback title to TOAST_TITLE_MAX', () => {
    const longTitle = 'This fallback title is unreasonably long and should be clamped before it ever reaches the banner layer';
    const p = presentError(new Error('x'), { fallbackTitle: longTitle });
    expect(p.title.length).toBeLessThanOrEqual(TOAST_TITLE_MAX);
    expect(p.title.endsWith('…')).toBe(true);
  });
});
