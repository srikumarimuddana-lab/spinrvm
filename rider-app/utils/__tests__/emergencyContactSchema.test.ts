import {
  isContactNameValid,
  isContactPhoneValid,
  getEmergencyContactFormError,
} from '../emergencyContactSchema';

describe('isContactNameValid', () => {
  it('rejects an empty/whitespace-only name', () => {
    expect(isContactNameValid('')).toBe(false);
    expect(isContactNameValid('   ')).toBe(false);
  });
  it('accepts a non-empty name', () => {
    expect(isContactNameValid('Jane Doe')).toBe(true);
  });
});

describe('isContactPhoneValid', () => {
  it('rejects a phone with fewer than 10 digits', () => {
    expect(isContactPhoneValid('123456789')).toBe(false);
  });
  it('accepts a phone with exactly 10 digits', () => {
    expect(isContactPhoneValid('1234567890')).toBe(true);
  });
  it('strips non-digit characters before counting', () => {
    expect(isContactPhoneValid('(123) 456-7890')).toBe(true);
    expect(isContactPhoneValid('(123) 456-789')).toBe(false);
  });
});

describe('getEmergencyContactFormError', () => {
  it('returns the missing-name error first', () => {
    expect(getEmergencyContactFormError('', '1234567890')).toEqual({
      title: 'Missing Name',
      message: 'Please enter a contact name.',
    });
  });

  it('returns the invalid-phone error when name is valid but phone is too short', () => {
    expect(getEmergencyContactFormError('Jane Doe', '123')).toEqual({
      title: 'Invalid Phone',
      message: 'Please enter a valid phone number (at least 10 digits).',
    });
  });

  it('returns null when both checks pass', () => {
    expect(getEmergencyContactFormError('Jane Doe', '(123) 456-7890')).toBeNull();
  });
});
