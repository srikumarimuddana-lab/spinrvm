import {
  profileSetupSchema,
  isProfileSetupValid,
  getProfileSetupError,
  type ProfileSetupField,
} from '../profileSetupSchema';

const validForm: ProfileSetupField = {
  firstName: 'Jane',
  lastName: 'Doe',
  email: 'jane@example.com',
  gender: 'Female',
};

describe('profileSetupSchema', () => {
  // --- accept cases ---
  it('accepts a fully valid form', () => {
    expect(profileSetupSchema.safeParse(validForm).success).toBe(true);
    expect(isProfileSetupValid(validForm)).toBe(true);
    expect(getProfileSetupError(validForm)).toBeNull();
  });

  it('accepts an email with a subdomain', () => {
    const form = { ...validForm, email: 'jane@mail.example.com' };
    expect(isProfileSetupValid(form)).toBe(true);
  });

  it('accepts an email with a plus tag', () => {
    const form = { ...validForm, email: 'jane+rides@example.com' };
    expect(isProfileSetupValid(form)).toBe(true);
  });

  it('accepts names with surrounding whitespace (trimmed like the original)', () => {
    const form = { ...validForm, firstName: '  Jane ', lastName: ' Doe ' };
    expect(isProfileSetupValid(form)).toBe(true);
  });

  it('rejects an email with surrounding whitespace with Invalid Email, matching the original untrimmed regex test', () => {
    const form = { ...validForm, email: ' jane@example.com ' };
    expect(getProfileSetupError(form)).toEqual({
      title: 'Invalid Email',
      message: 'That email doesn’t look right — e.g. name@example.com.',
    });
  });

  it('accepts any non-empty gender string, not just the three preset options', () => {
    const form = { ...validForm, gender: 'Non-binary' };
    expect(isProfileSetupValid(form)).toBe(true);
  });

  // --- reject cases, priority order matters ---
  it('rejects an empty firstName with First Name Required, first', () => {
    const form = { firstName: '', lastName: '', email: '', gender: '' };
    expect(isProfileSetupValid(form)).toBe(false);
    expect(getProfileSetupError(form)).toEqual({
      title: 'First Name Required',
      message: 'Please enter your first name.',
    });
  });

  it('rejects a whitespace-only firstName the same as empty', () => {
    const form = { ...validForm, firstName: '   ' };
    expect(getProfileSetupError(form)).toEqual({
      title: 'First Name Required',
      message: 'Please enter your first name.',
    });
  });

  it('rejects an empty lastName with Last Name Required when firstName is present', () => {
    const form = { firstName: 'Jane', lastName: '', email: '', gender: '' };
    expect(getProfileSetupError(form)).toEqual({
      title: 'Last Name Required',
      message: 'Please enter your last name.',
    });
  });

  it('rejects an empty email with Email Required when name fields are present', () => {
    const form = { firstName: 'Jane', lastName: 'Doe', email: '', gender: '' };
    expect(getProfileSetupError(form)).toEqual({
      title: 'Email Required',
      message: 'Please enter your email address.',
    });
  });

  it('rejects a whitespace-only email with Email Required, not Invalid Email', () => {
    const form = { ...validForm, email: '   ' };
    expect(getProfileSetupError(form)).toEqual({
      title: 'Email Required',
      message: 'Please enter your email address.',
    });
  });

  it('rejects a malformed email (no @) with Invalid Email', () => {
    const form = { ...validForm, email: 'jane.example.com' };
    expect(getProfileSetupError(form)).toEqual({
      title: 'Invalid Email',
      message: 'That email doesn’t look right — e.g. name@example.com.',
    });
  });

  it('rejects a malformed email (no domain dot) with Invalid Email', () => {
    const form = { ...validForm, email: 'jane@example' };
    expect(getProfileSetupError(form)).toEqual({
      title: 'Invalid Email',
      message: 'That email doesn’t look right — e.g. name@example.com.',
    });
  });

  it('rejects an email with a space with Invalid Email', () => {
    const form = { ...validForm, email: 'jane doe@example.com' };
    expect(getProfileSetupError(form)).toEqual({
      title: 'Invalid Email',
      message: 'That email doesn’t look right — e.g. name@example.com.',
    });
  });

  it('rejects an empty gender with Gender Required when everything else is valid', () => {
    const form = { ...validForm, gender: '' };
    expect(getProfileSetupError(form)).toEqual({
      title: 'Gender Required',
      message: 'Please select your gender.',
    });
  });

  it('reports the first failing check when multiple fields are invalid (firstName before lastName)', () => {
    const form = { firstName: '', lastName: '', email: 'bad', gender: '' };
    expect(getProfileSetupError(form)?.title).toBe('First Name Required');
  });

  it('reports Email Required before Gender Required when both are missing', () => {
    const form = { firstName: 'Jane', lastName: 'Doe', email: '', gender: '' };
    expect(getProfileSetupError(form)?.title).toBe('Email Required');
  });

  it('reports Invalid Email before Gender Required when both fail', () => {
    const form = { firstName: 'Jane', lastName: 'Doe', email: 'bad-email', gender: '' };
    expect(getProfileSetupError(form)?.title).toBe('Invalid Email');
  });
});
