import {
  isLicenseNumberValid,
  isLicenseClassValid,
  isLicenseInfoFormValid,
  getLicenseInfoFormError,
} from '../licenseInfoFormSchema';

describe('isLicenseNumberValid', () => {
  it('accepts a non-empty licence number', () => {
    expect(isLicenseNumberValid('S1234-5678-9012')).toBe(true);
  });

  it('rejects an empty string', () => {
    expect(isLicenseNumberValid('')).toBe(false);
  });

  it('rejects a whitespace-only string', () => {
    expect(isLicenseNumberValid('   ')).toBe(false);
  });
});

describe('isLicenseClassValid', () => {
  it('accepts a non-empty licence class', () => {
    expect(isLicenseClassValid('5')).toBe(true);
  });

  it('rejects an empty string', () => {
    expect(isLicenseClassValid('')).toBe(false);
  });
});

describe('isLicenseInfoFormValid', () => {
  it('accepts a fully valid form', () => {
    expect(isLicenseInfoFormValid({ licenseNumber: 'S1234', licenseClass: '5A' })).toBe(true);
  });

  it('rejects a missing licence number', () => {
    expect(isLicenseInfoFormValid({ licenseNumber: '', licenseClass: '5A' })).toBe(false);
  });

  it('rejects a missing licence class', () => {
    expect(isLicenseInfoFormValid({ licenseNumber: 'S1234', licenseClass: '' })).toBe(false);
  });
});

describe('getLicenseInfoFormError', () => {
  it('returns null for a valid form', () => {
    expect(getLicenseInfoFormError({ licenseNumber: 'S1234', licenseClass: '5' })).toBeNull();
  });

  it('returns a message when the licence number is missing', () => {
    expect(getLicenseInfoFormError({ licenseNumber: '', licenseClass: '5' })).toContain('licence number');
  });

  it('returns a message when the licence class is missing', () => {
    expect(getLicenseInfoFormError({ licenseNumber: 'S1234', licenseClass: '' })).toContain('licence class');
  });
});
