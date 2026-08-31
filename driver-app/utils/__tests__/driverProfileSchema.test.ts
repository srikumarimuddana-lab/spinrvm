import { isProfileFieldsComplete, isProfileEmailFormatValid, getProfileFormError } from '../driverProfileSchema';

describe('isProfileFieldsComplete', () => {
  it('accepts when all fields are present', () => {
    expect(isProfileFieldsComplete('Jane', 'Doe', 'jane@example.com', 'female')).toBe(true);
  });

  it('rejects when firstName is missing', () => {
    expect(isProfileFieldsComplete('', 'Doe', 'jane@example.com', 'female')).toBe(false);
  });

  it('rejects when gender is missing', () => {
    expect(isProfileFieldsComplete('Jane', 'Doe', 'jane@example.com', '')).toBe(false);
  });

  it('rejects a whitespace-only firstName', () => {
    expect(isProfileFieldsComplete('   ', 'Doe', 'jane@example.com', 'female')).toBe(false);
  });
});

describe('isProfileEmailFormatValid', () => {
  it('accepts a well-formed email', () => {
    expect(isProfileEmailFormatValid('jane@example.com')).toBe(true);
  });

  it('rejects a string with no @', () => {
    expect(isProfileEmailFormatValid('jane.example.com')).toBe(false);
  });

  it('rejects an empty string', () => {
    expect(isProfileEmailFormatValid('')).toBe(false);
  });
});

describe('getProfileFormError', () => {
  it('accepts a fully valid form', () => {
    expect(getProfileFormError('Jane', 'Doe', 'jane@example.com', 'female')).toBeNull();
  });

  it('rejects missing fields', () => {
    expect(getProfileFormError('', 'Doe', 'jane@example.com', 'female')).toEqual({
      title: 'Missing Info',
      message: 'Please fill in all fields',
    });
  });

  it('rejects an invalid email once fields are complete', () => {
    expect(getProfileFormError('Jane', 'Doe', 'not-an-email', 'female')).toEqual({
      title: 'Invalid Email',
      message: 'Please enter a valid email address',
    });
  });

  it('checks field completeness before email format (first-error-wins order)', () => {
    expect(getProfileFormError('', 'Doe', 'not-an-email', 'female')).toEqual({
      title: 'Missing Info',
      message: 'Please fill in all fields',
    });
  });
});
