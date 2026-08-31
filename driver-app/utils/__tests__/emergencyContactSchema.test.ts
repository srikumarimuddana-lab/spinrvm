import { isContactNameValid, isContactPhoneValid, getEmergencyContactFormError } from '../emergencyContactSchema';

describe('isContactNameValid', () => {
  it('accepts a non-empty name', () => {
    expect(isContactNameValid('Jane Doe')).toBe(true);
  });

  it('rejects an empty name', () => {
    expect(isContactNameValid('')).toBe(false);
  });

  it('rejects a whitespace-only name', () => {
    expect(isContactNameValid('   ')).toBe(false);
  });
});

describe('isContactPhoneValid', () => {
  it('accepts a 10-digit phone number', () => {
    expect(isContactPhoneValid('3065551234')).toBe(true);
  });

  it('accepts a formatted phone number with 10+ digits', () => {
    expect(isContactPhoneValid('(306) 555-1234')).toBe(true);
  });

  it('accepts an 11-digit phone number (country code)', () => {
    expect(isContactPhoneValid('13065551234')).toBe(true);
  });

  it('rejects a 9-digit phone number', () => {
    expect(isContactPhoneValid('306555123')).toBe(false);
  });

  it('rejects an empty phone number', () => {
    expect(isContactPhoneValid('')).toBe(false);
  });

  it('rejects a non-numeric phone number', () => {
    expect(isContactPhoneValid('abcdefghij')).toBe(false);
  });
});

describe('getEmergencyContactFormError', () => {
  it('accepts a valid name and phone', () => {
    expect(getEmergencyContactFormError('Jane Doe', '3065551234')).toBeNull();
  });

  it('rejects a missing name', () => {
    expect(getEmergencyContactFormError('', '3065551234')).toEqual({
      title: 'Missing Name',
      message: 'Please enter a contact name.',
    });
  });

  it('rejects an invalid phone', () => {
    expect(getEmergencyContactFormError('Jane Doe', '12345')).toEqual({
      title: 'Invalid Phone',
      message: 'Please enter a valid phone number (at least 10 digits).',
    });
  });

  it('checks name before phone (first-error-wins order)', () => {
    expect(getEmergencyContactFormError('', '12345')).toEqual({
      title: 'Missing Name',
      message: 'Please enter a contact name.',
    });
  });
});
