import { describe, it, expect } from 'vitest';
import { isStaffRequiredFieldsValid, isStaffPasswordValid } from '../staffFormSchema';

describe('isStaffRequiredFieldsValid', () => {
  it('accepts when email, first name, and last name are all present', () => {
    expect(isStaffRequiredFieldsValid('ops@spinr.ca', 'Jane', 'Doe')).toBe(true);
  });

  it('rejects an empty email', () => {
    expect(isStaffRequiredFieldsValid('', 'Jane', 'Doe')).toBe(false);
  });

  it('rejects an empty first name', () => {
    expect(isStaffRequiredFieldsValid('ops@spinr.ca', '', 'Doe')).toBe(false);
  });

  it('rejects an empty last name', () => {
    expect(isStaffRequiredFieldsValid('ops@spinr.ca', 'Jane', '')).toBe(false);
  });

  it('rejects when all three are empty (mirrors the original `!email || !first_name || !last_name` guard)', () => {
    expect(isStaffRequiredFieldsValid('', '', '')).toBe(false);
  });
});

describe('isStaffPasswordValid', () => {
  it('accepts a non-empty password', () => {
    expect(isStaffPasswordValid('correct horse battery staple')).toBe(true);
  });

  it('rejects an empty password (mirrors the original `!form.password` guard, create path only)', () => {
    expect(isStaffPasswordValid('')).toBe(false);
  });
});
