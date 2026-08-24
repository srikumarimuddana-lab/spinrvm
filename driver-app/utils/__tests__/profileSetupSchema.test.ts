import {
  profileSetupSchema,
  isFirstNameValid,
  isLastNameValid,
  isEmailValid,
  isServiceAreaValid,
  isProfileSetupFormValid,
  getProfileSetupError,
  type ProfileSetupField,
} from '../profileSetupSchema';

const validForm: ProfileSetupField = {
  firstName: 'Jane',
  lastName: 'Doe',
  email: 'jane@example.com',
  gender: 'Female',
  serviceAreaId: 'saskatoon',
};

describe('profileSetupSchema', () => {
  // --- accept cases ---
  it('accepts a fully valid form', () => {
    expect(profileSetupSchema.safeParse(validForm).success).toBe(true);
    expect(isProfileSetupFormValid(validForm)).toBe(true);
    expect(getProfileSetupError(validForm)).toBeNull();
  });

  it('accepts an email with a subdomain', () => {
    const form = { ...validForm, email: 'jane@mail.example.com' };
    expect(isProfileSetupFormValid(form)).toBe(true);
  });

  it('accepts an email with a plus tag', () => {
    const form = { ...validForm, email: 'jane+rides@example.com' };
    expect(isProfileSetupFormValid(form)).toBe(true);
  });

  it('accepts names with surrounding whitespace, trimmed to length > 1 like the original', () => {
    const form = { ...validForm, firstName: '  Jane ', lastName: ' Doe ' };
    expect(isProfileSetupFormValid(form)).toBe(true);
  });

  it('accepts a 2-letter name at the boundary (trim().length > 1)', () => {
    expect(isFirstNameValid('Al')).toBe(true);
    expect(isLastNameValid('Ng')).toBe(true);
  });

  it('accepts any non-empty serviceAreaId', () => {
    expect(isServiceAreaValid('regina')).toBe(true);
  });

  // --- reject cases ---
  it('rejects a 1-letter first name (trim().length > 1 boundary)', () => {
    expect(isFirstNameValid('A')).toBe(false);
    const form = { ...validForm, firstName: 'A' };
    expect(getProfileSetupError(form)).toEqual({
      title: 'First Name Required',
      message: 'Please enter your first name (at least 2 letters).',
    });
  });

  it('rejects a first name that is only whitespace', () => {
    expect(isFirstNameValid('   ')).toBe(false);
  });

  it('rejects a 1-letter last name (trim().length > 1 boundary)', () => {
    expect(isLastNameValid('D')).toBe(false);
    const form = { ...validForm, lastName: 'D' };
    expect(getProfileSetupError(form)).toEqual({
      title: 'Last Name Required',
      message: 'Please enter your last name (at least 2 letters).',
    });
  });

  it('rejects an empty email with Email Required (priority before Invalid Email)', () => {
    const form = { ...validForm, email: '' };
    expect(isEmailValid('')).toBe(false);
    expect(getProfileSetupError(form)).toEqual({
      title: 'Email Required',
      message: 'Please enter your email address.',
    });
  });

  it('rejects a whitespace-only email with Email Required, matching the original untrimmed regex path', () => {
    const form = { ...validForm, email: '   ' };
    expect(getProfileSetupError(form)).toEqual({
      title: 'Email Required',
      message: 'Please enter your email address.',
    });
  });

  it('rejects a malformed non-empty email with Invalid Email', () => {
    const form = { ...validForm, email: 'not-an-email' };
    expect(isEmailValid('not-an-email')).toBe(false);
    expect(getProfileSetupError(form)).toEqual({
      title: 'Invalid Email',
      message: 'That email doesn’t look right — e.g. name@example.com.',
    });
  });

  it('rejects an email missing the domain suffix', () => {
    expect(isEmailValid('jane@example')).toBe(false);
  });

  it('rejects an email with surrounding whitespace, matching the original untrimmed regex test', () => {
    const form = { ...validForm, email: ' jane@example.com ' };
    expect(getProfileSetupError(form)).toEqual({
      title: 'Invalid Email',
      message: 'That email doesn’t look right — e.g. name@example.com.',
    });
  });

  it('rejects a missing gender with Gender Required', () => {
    const form = { ...validForm, gender: '' };
    expect(getProfileSetupError(form)).toEqual({
      title: 'Gender Required',
      message: 'Please select your gender.',
    });
  });

  it('rejects a missing service area with Service Area Required (last in priority order)', () => {
    const form = { ...validForm, serviceAreaId: '' };
    expect(isServiceAreaValid('')).toBe(false);
    expect(getProfileSetupError(form)).toEqual({
      title: 'Service Area Required',
      message: 'Please select the area where you plan to drive.',
    });
  });

  it('reports the first-failing check when multiple fields are invalid (name before email)', () => {
    const form = { ...validForm, firstName: '', email: 'bad' };
    expect(getProfileSetupError(form)?.title).toBe('First Name Required');
  });

  it('reports the first-failing check when multiple fields are invalid (email before gender)', () => {
    const form = { ...validForm, email: 'bad', gender: '' };
    expect(getProfileSetupError(form)?.title).toBe('Invalid Email');
  });

  it('reports the first-failing check when multiple fields are invalid (gender before service area)', () => {
    const form = { ...validForm, gender: '', serviceAreaId: '' };
    expect(getProfileSetupError(form)?.title).toBe('Gender Required');
  });
});
