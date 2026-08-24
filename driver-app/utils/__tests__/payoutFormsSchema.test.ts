import { isGstBnValid, isGstBnOnFile, isSinValid } from '../payoutFormsSchema';

describe('isGstBnValid (handleSaveGst accept condition)', () => {
  it('accepts an empty string (clears gst_bn)', () => {
    expect(isGstBnValid('')).toBe(true);
  });

  it('accepts a bare 9-digit Business Number', () => {
    expect(isGstBnValid('123456789')).toBe(true);
  });

  it('accepts a full BN + program-account suffix', () => {
    expect(isGstBnValid('123456789RT0001')).toBe(true);
  });

  it('accepts a non-0001 program-account suffix', () => {
    expect(isGstBnValid('123456789RT9999')).toBe(true);
  });

  it('rejects fewer than 9 digits', () => {
    expect(isGstBnValid('12345678')).toBe(false);
  });

  it('rejects more than 9 digits with no suffix', () => {
    expect(isGstBnValid('1234567890')).toBe(false);
  });

  it('rejects a lowercase suffix (matches existing non-uppercased handleSaveGst check)', () => {
    expect(isGstBnValid('123456789rt0001')).toBe(false);
  });

  it('rejects non-numeric characters', () => {
    expect(isGstBnValid('12345678A')).toBe(false);
  });

  it('rejects a malformed suffix', () => {
    expect(isGstBnValid('123456789RT001')).toBe(false);
  });
});

describe('isGstBnOnFile (setup-checklist display flag)', () => {
  it('rejects an empty value (not on file, unlike isGstBnValid)', () => {
    expect(isGstBnOnFile('')).toBe(false);
  });

  it('rejects null/undefined', () => {
    expect(isGstBnOnFile(null)).toBe(false);
    expect(isGstBnOnFile(undefined)).toBe(false);
  });

  it('accepts a bare 9-digit BN', () => {
    expect(isGstBnOnFile('123456789')).toBe(true);
  });

  it('accepts a full BN + suffix', () => {
    expect(isGstBnOnFile('123456789RT0001')).toBe(true);
  });

  it('accepts a lowercase suffix (normalizes to uppercase, unlike isGstBnValid)', () => {
    expect(isGstBnOnFile('123456789rt0001')).toBe(true);
  });

  it('accepts embedded whitespace (stripped before matching)', () => {
    expect(isGstBnOnFile('123 456 789 RT0001')).toBe(true);
  });

  it('rejects an invalid format', () => {
    expect(isGstBnOnFile('12345')).toBe(false);
  });
});

describe('isSinValid (handleSaveSin length check)', () => {
  it('accepts exactly 9 digits', () => {
    expect(isSinValid('123456789')).toBe(true);
  });

  it('rejects fewer than 9 digits', () => {
    expect(isSinValid('12345678')).toBe(false);
  });

  it('rejects more than 9 digits', () => {
    expect(isSinValid('1234567890')).toBe(false);
  });

  it('rejects an empty string', () => {
    expect(isSinValid('')).toBe(false);
  });
});
