import {
  isPersonalStepValid,
  hasAnyVehicleInfo,
  isVehicleYearValid,
  isVehicleInfoComplete,
  getVehicleStepError,
  isCrcConsentValid,
} from '../becomeDriverSchema';

describe('isPersonalStepValid', () => {
  it('accepts when all fields are present', () => {
    expect(isPersonalStepValid('Jane', 'Doe', 'jane@example.com', 'female', 'area-1')).toBe(true);
  });

  it('rejects when firstName is missing', () => {
    expect(isPersonalStepValid('', 'Doe', 'jane@example.com', 'female', 'area-1')).toBe(false);
  });

  it('rejects when serviceAreaId is missing', () => {
    expect(isPersonalStepValid('Jane', 'Doe', 'jane@example.com', 'female', '')).toBe(false);
  });
});

describe('hasAnyVehicleInfo', () => {
  it('returns false when all fields are empty', () => {
    expect(hasAnyVehicleInfo('', '', '', '', '', '', '')).toBe(false);
  });

  it('returns true when only one field is set', () => {
    expect(hasAnyVehicleInfo('Toyota', '', '', '', '', '', '')).toBe(true);
  });
});

describe('isVehicleYearValid', () => {
  const currentYear = new Date().getFullYear();

  it('accepts an empty year (not yet entered)', () => {
    expect(isVehicleYearValid('')).toBe(true);
  });

  it('accepts the current year', () => {
    expect(isVehicleYearValid(String(currentYear))).toBe(true);
  });

  it('accepts a year exactly 9 years old', () => {
    expect(isVehicleYearValid(String(currentYear - 9))).toBe(true);
  });

  it('rejects a year 10 years old', () => {
    expect(isVehicleYearValid(String(currentYear - 10))).toBe(false);
  });

  it('rejects a non-numeric year', () => {
    expect(isVehicleYearValid('abcd')).toBe(false);
  });
});

describe('isVehicleInfoComplete', () => {
  it('accepts when all four fields are set', () => {
    expect(isVehicleInfoComplete('Toyota', 'Corolla', 'ABC123', 'sedan')).toBe(true);
  });

  it('rejects when licensePlate is missing', () => {
    expect(isVehicleInfoComplete('Toyota', 'Corolla', '', 'sedan')).toBe(false);
  });
});

describe('getVehicleStepError', () => {
  const currentYear = new Date().getFullYear();
  const base = {
    vehicleMake: '', vehicleModel: '', vehicleColor: '', vehicleYear: '',
    licensePlate: '', vehicleVin: '', vehicleType: '',
  };

  it('allows a fully empty step (skip)', () => {
    expect(getVehicleStepError(base)).toBeNull();
  });

  it('allows a fully complete, valid step', () => {
    expect(
      getVehicleStepError({
        ...base,
        vehicleMake: 'Toyota', vehicleModel: 'Corolla', licensePlate: 'ABC123',
        vehicleType: 'sedan', vehicleYear: String(currentYear),
      }),
    ).toBeNull();
  });

  it('rejects an invalid year when info was started', () => {
    expect(getVehicleStepError({ ...base, vehicleMake: 'Toyota', vehicleYear: String(currentYear - 10) })).toEqual({
      title: 'Invalid Year',
      message: 'Vehicle must be 9 years old or newer.',
    });
  });

  it('rejects incomplete info once started (make only)', () => {
    expect(getVehicleStepError({ ...base, vehicleMake: 'Toyota' })).toEqual({
      title: 'Incomplete Vehicle Info',
      message: 'Please complete all vehicle fields or use "Skip for now".',
    });
  });

  it('checks year validity before completeness (first-error-wins order)', () => {
    expect(
      getVehicleStepError({ ...base, vehicleMake: 'Toyota', vehicleYear: String(currentYear - 10) }),
    ).toEqual({ title: 'Invalid Year', message: 'Vehicle must be 9 years old or newer.' });
  });
});

describe('isCrcConsentValid', () => {
  it('accepts true', () => {
    expect(isCrcConsentValid(true)).toBe(true);
  });

  it('rejects false', () => {
    expect(isCrcConsentValid(false)).toBe(false);
  });
});
