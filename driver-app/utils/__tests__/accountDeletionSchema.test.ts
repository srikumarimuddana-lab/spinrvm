import { isDeleteConfirmationValid } from '../accountDeletionSchema';

describe('isDeleteConfirmationValid', () => {
  it('accepts the exact uppercase word DELETE', () => {
    expect(isDeleteConfirmationValid('DELETE')).toBe(true);
  });

  it('accepts a lowercase "delete" (case-insensitive)', () => {
    expect(isDeleteConfirmationValid('delete')).toBe(true);
  });

  it('accepts mixed-case "DeLeTe"', () => {
    expect(isDeleteConfirmationValid('DeLeTe')).toBe(true);
  });

  it('accepts DELETE with surrounding whitespace', () => {
    expect(isDeleteConfirmationValid('  DELETE  ')).toBe(true);
  });

  it('rejects an empty string', () => {
    expect(isDeleteConfirmationValid('')).toBe(false);
  });

  it('rejects a near-miss like "DELET"', () => {
    expect(isDeleteConfirmationValid('DELET')).toBe(false);
  });

  it('rejects "DELETE ME" (extra text)', () => {
    expect(isDeleteConfirmationValid('DELETE ME')).toBe(false);
  });
});
