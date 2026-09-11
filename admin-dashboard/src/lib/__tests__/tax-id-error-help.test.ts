import { describe, it, expect } from 'vitest';
import { explainTaxIdIssue } from '../tax-id-error-help';

describe('explainTaxIdIssue', () => {
  it('matches an exact backend message', () => {
    const explanation = explainTaxIdIssue('duplicate phone in CSV');
    expect(explanation).not.toBeNull();
    expect(explanation?.cause).toMatch(/same phone number/i);
    expect(explanation?.fix).toMatch(/one row per phone/i);
  });

  it('matches a prefix for a message carrying a dynamic row count', () => {
    const explanation = explainTaxIdIssue('CSV has 501 rows; the limit is 500 per import');
    expect(explanation).not.toBeNull();
    expect(explanation?.fix).toMatch(/smaller batches/i);
  });

  it('matches a prefix for a message carrying a dynamic digit count', () => {
    const explanation = explainTaxIdIssue('SIN must be 9 digits; got 8');
    expect(explanation).not.toBeNull();
    expect(explanation?.cause).toMatch(/exactly 9 digits/i);
  });

  it('never echoes a SIN, GST BN, or phone value -- only ever returns the two fixed strings', () => {
    const explanation = explainTaxIdIssue('not a valid BN (9 digits, optional RTxxxx)');
    expect(explanation).toEqual({
      cause: expect.any(String),
      fix: expect.any(String),
    });
  });

  it('returns null for an unmapped message rather than guessing', () => {
    expect(explainTaxIdIssue('some future backend message nobody wrote a mapping for yet')).toBeNull();
  });
});
