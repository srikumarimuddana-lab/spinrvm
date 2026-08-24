import {
  driverProfileSetupSchema,
  isDriverProfileSetupValid,
  getDriverProfileSetupError,
  type DriverProfileSetupField,
} from '../driverProfileSetupSchema';

const validForm: DriverProfileSetupField = {
  firstName: 'Jane',
  lastName: 'Doe',
  email: 'jane@example.com',
  gender: 'Female',
  serviceAreaId: 'saskatoon',
};

describe('driverProfileSetupSchema', () => {
  // --- accept cases ---
  it('accepts a fully valid form', () => {
    expect(driverProfileSetupSchema.safeParse(validForm).success).toBe(true);
    expect(isDriverProfileSetupValid(validForm)).toBe(true);
    expect(getDriverProfileSetupError(validForm)).toBeNull();
  });

  it('accepts an email with a subdomain', () => {
    const form = { ...validForm, email: 'jane@mail.example.com' };
    expect(isDriverProfileSetupValid(form)).toBe(true);
  });

  it('accepts an email with a plus tag', () => {
    const form = { ...validForm, email: 'jane+driver@example.com' };
    expect(isDriverProfileSetupValid(form)).toBe(true);
  });

  it('accepts names with surrounding whitespace that leave 2+ trimmed characters', () => {
    const form = { ...validForm, firstName: '  Jane ', lastName: ' Doe ' };
    expect(isDriverProfileSetupValid(form)).toBe(true);
  });

  it('accepts a two-letter first/last name (boundary: trim().length > 1)', () => {
    const form = { ...validForm, firstName: 'Jo', lastName: 'Xu' };
    expect(isDriverProfileSetupValid(form)).toBe(true);
  });

  it('rejects an email with surrounding whitespace with Invalid Email, matching the original untrimmed regex test', () => {
    const form = { ...validForm, email: ' jane@example.com ' };
    expect(getDriverProfileSetupError(form)).toEqual({
      title: 'Invalid Email',
      message: 'That email doesn’t look right — e.g. name@example.com.',
    });
  });

  it('accepts any non-empty gender string, not just the three preset options', () => {
    const form = { ...validForm, gender: 'Non-binary' };
    expect(isDriverProfileSetupValid(form)).toBe(true);
  });

  it('accepts any non-empty serviceAreaId, not restricted to a known list', () => {
    const form = { ...validForm, serviceAreaId: 'moose-jaw' };
    expect(isDriverProfileSetupValid(form)).toBe(true);
  });

  // --- reject cases, priority order matters ---
  it('rejects an empty firstName with First Name Required, first', () => {
    const form = { firstName: '', lastName: '', email: '', gender: '', serviceAreaId: '' };
    expect(isDriverProfileSetupValid(form)).toBe(false);
    expect(getDriverProfileSetupError(form)).toEqual({
      title: 'First Name Required',
      message: 'Please enter your first name (at least 2 letters).',
    });
  });

  it('rejects a single-letter firstName with First Name Required (boundary: length > 1, not >= 1)', () => {
    const form = { ...validForm, firstName: 'J' };
    expect(getDriverProfileSetupError(form)).toEqual({
      title: 'First Name Required',
      message: 'Please enter your first name (at least 2 letters).',
    });
  });

  it('rejects a whitespace-only firstName with First Name Required (trimmed)', () => {
    const form = { ...validForm, firstName: '   ' };
    expect(getDriverProfileSetupError(form)).toEqual({
      title: 'First Name Required',
      message: 'Please enter your first name (at least 2 letters).',
    });
  });

  it('rejects an empty lastName with Last Name Required, after firstName passes', () => {
    const form = { ...validForm, lastName: '' };
    expect(getDriverProfileSetupError(form)).toEqual({
      title: 'Last Name Required',
      message: 'Please enter your last name (at least 2 letters).',
    });
  });

  it('rejects a single-letter lastName with Last Name Required (boundary)', () => {
    const form = { ...validForm, lastName: 'D' };
    expect(getDriverProfileSetupError(form)).toEqual({
      title: 'Last Name Required',
      message: 'Please enter your last name (at least 2 letters).',
    });
  });

  it('rejects an empty email with Email Required, after name checks pass', () => {
    const form = { ...validForm, email: '' };
    expect(getDriverProfileSetupError(form)).toEqual({
      title: 'Email Required',
      message: 'Please enter your email address.',
    });
  });

  it('rejects a whitespace-only email with Email Required (trimmed check runs first)', () => {
    const form = { ...validForm, email: '   ' };
    expect(getDriverProfileSetupError(form)).toEqual({
      title: 'Email Required',
      message: 'Please enter your email address.',
    });
  });

  it('rejects a non-empty malformed email with Invalid Email, after Email Required passes', () => {
    const form = { ...validForm, email: 'not-an-email' };
    expect(getDriverProfileSetupError(form)).toEqual({
      title: 'Invalid Email',
      message: 'That email doesn’t look right — e.g. name@example.com.',
    });
  });

  it('rejects an email missing a TLD with Invalid Email', () => {
    const form = { ...validForm, email: 'jane@example' };
    expect(getDriverProfileSetupError(form)).toEqual({
      title: 'Invalid Email',
      message: 'That email doesn’t look right — e.g. name@example.com.',
    });
  });

  it('rejects an empty gender with Gender Required, after email checks pass', () => {
    const form = { ...validForm, gender: '' };
    expect(getDriverProfileSetupError(form)).toEqual({
      title: 'Gender Required',
      message: 'Please select your gender.',
    });
  });

  it('rejects an empty serviceAreaId with Service Area Required, last in priority order', () => {
    const form = { ...validForm, serviceAreaId: '' };
    expect(getDriverProfileSetupError(form)).toEqual({
      title: 'Service Area Required',
      message: 'Please select the area where you plan to drive.',
    });
  });

  it('reports the first failing check only, even when multiple fields are invalid', () => {
    const form = { firstName: '', lastName: '', email: 'bad', gender: '', serviceAreaId: '' };
    expect(getDriverProfileSetupError(form)).toEqual({
      title: 'First Name Required',
      message: 'Please enter your first name (at least 2 letters).',
    });
  });

  it('reports Service Area Required when every earlier field is valid', () => {
    const form = { ...validForm, serviceAreaId: '' };
    expect(isDriverProfileSetupValid(form)).toBe(false);
  });
});
