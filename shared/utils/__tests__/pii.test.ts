/**
 * Tests for shared PII redaction helpers. These mirror
 * `backend/tests/test_pii_redaction.py` so the TS + Python emission paths
 * stay in lockstep. Keep these fast and pure.
 */
import { redactPhone, redactEmail, redactCoords } from '../pii';

describe('redactPhone', () => {
  it.each([
    ['+13065551234', '****1234'],
    ['3065551234', '****1234'],
    ['(306) 555-1234', '****1234'],
    ['306.555.1234', '****1234'],
    ['306 555 1234', '****1234'],
    ['+1 306 555 1234', '****1234'],
  ])('keeps last-4 digits for %s', (raw, expected) => {
    expect(redactPhone(raw)).toBe(expected);
  });

  it.each([['', null, undefined, 'abc', '12', '+', '1']].flat())(
    'returns **** for short or empty input %p',
    (raw) => {
      expect(redactPhone(raw as string | null | undefined)).toBe('****');
    },
  );

  it('never echoes the full number in the mask', () => {
    const raw = '+13065551234';
    const masked = redactPhone(raw);
    expect(masked).not.toContain(raw);
    // Only last-4 digits exposed — exactly four digit characters in the mask.
    const digitsInMask = masked.match(/\d/g) ?? [];
    expect(digitsInMask).toHaveLength(4);
  });
});

describe('redactEmail', () => {
  it.each([
    ['alice@example.com', 'a***@example.com'],
    ['bob@spinr.ca', 'b***@spinr.ca'],
    ['x@y.io', 'x***@y.io'],
    ['@nobody.com', '***@nobody.com'],
  ])('masks the local part of %s', (raw, expected) => {
    expect(redactEmail(raw)).toBe(expected);
  });

  it.each(['', null, undefined, 'no-at-sign', 'plaintext'])(
    'returns **** for malformed input %p',
    (raw) => {
      expect(redactEmail(raw as string | null | undefined)).toBe('****');
    },
  );

  it('never leaks beyond the first character of the local part', () => {
    const raw = 'secretive.username@example.com';
    const masked = redactEmail(raw);
    expect(masked).not.toContain('secretive');
    expect(masked).not.toContain('username');
    expect(masked.startsWith('s***@')).toBe(true);
  });
});

describe('redactCoords', () => {
  it('rounds to one decimal place (~11km precision)', () => {
    expect(redactCoords(52.1332, -106.6700)).toBe('52.1,-106.7');
    expect(redactCoords(52.16, -106.65)).toBe('52.2,-106.7');
  });

  it('preserves sign for negative coordinates', () => {
    expect(redactCoords(-33.8688, 151.2093)).toBe('-33.9,151.2');
  });

  it('emits a single decimal even for round numbers', () => {
    expect(redactCoords(0, 0)).toBe('0.0,0.0');
    expect(redactCoords(45, -90)).toBe('45.0,-90.0');
  });

  it('never leaks a high-precision float in the output', () => {
    const masked = redactCoords(52.13321111, -106.67009999);
    expect(masked).not.toContain('52.13321111');
    expect(masked).not.toContain('106.67009999');
    // Each side of the comma is at most "-NN.N" — never more than one decimal.
    const [lat, lng] = masked.split(',');
    expect(lat.split('.')[1]).toHaveLength(1);
    expect(lng.split('.')[1]).toHaveLength(1);
  });

  it('handles non-finite inputs without leaking NaN/Infinity strings', () => {
    expect(redactCoords(Number.NaN, 0)).toBe('?,0.0');
    expect(redactCoords(0, Number.POSITIVE_INFINITY)).toBe('0.0,?');
  });
});
