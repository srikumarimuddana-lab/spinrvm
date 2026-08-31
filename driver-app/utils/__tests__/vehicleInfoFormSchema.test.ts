import {
  isVehicleTypeSelected,
  isVehicleMakeValid,
  isVehicleModelValid,
  isVehicleYearValid,
  isLicensePlateValid,
  isVehicleInfoFormValid,
  getVehicleYearValue,
} from '../vehicleInfoFormSchema';

describe('isVehicleTypeSelected', () => {
  it('returns false for an empty id', () => {
    expect(isVehicleTypeSelected('')).toBe(false);
  });
  it('returns true for a non-empty id', () => {
    expect(isVehicleTypeSelected('type-123')).toBe(true);
  });
});

describe('isVehicleMakeValid / isVehicleModelValid / isLicensePlateValid', () => {
  it('reject empty/whitespace-only values', () => {
    expect(isVehicleMakeValid('')).toBe(false);
    expect(isVehicleMakeValid('   ')).toBe(false);
    expect(isVehicleModelValid('')).toBe(false);
    expect(isLicensePlateValid('')).toBe(false);
  });
  it('accept a non-empty value', () => {
    expect(isVehicleMakeValid('Toyota')).toBe(true);
    expect(isVehicleModelValid('Camry')).toBe(true);
    expect(isLicensePlateValid('ABC 123')).toBe(true);
  });
});

describe('isVehicleYearValid', () => {
  it('returns false for an empty string (mirrors the original `.trim()` check)', () => {
    expect(isVehicleYearValid('')).toBe(false);
    expect(isVehicleYearValid('   ')).toBe(false);
  });

  it('returns true for a valid numeric year', () => {
    expect(isVehicleYearValid('2020')).toBe(true);
  });

  it('BUG FIX: returns false for a non-numeric year instead of silently accepting it (the original only checked non-empty)', () => {
    expect(isVehicleYearValid('abc')).toBe(false);
  });
});

describe('isVehicleInfoFormValid', () => {
  const validForm = {
    vehicleTypeId: 'type-1',
    vehicleMake: 'Toyota',
    vehicleModel: 'Camry',
    vehicleYear: '2020',
    licensePlate: 'ABC 123',
  };

  it('returns true when every field is valid', () => {
    expect(isVehicleInfoFormValid(validForm)).toBe(true);
  });

  it('returns false when vehicle type is missing', () => {
    expect(isVehicleInfoFormValid({ ...validForm, vehicleTypeId: '' })).toBe(false);
  });

  it('BUG FIX: returns false for a non-numeric vehicle year, where the original isFormValid would have returned true', () => {
    expect(isVehicleInfoFormValid({ ...validForm, vehicleYear: 'abc' })).toBe(false);
  });
});

describe('getVehicleYearValue', () => {
  it('returns the parsed integer for a valid numeric year', () => {
    expect(getVehicleYearValue('2020')).toBe(2020);
  });

  it('BUG FIX: falls back to 0 for a non-numeric year (defensive only -- isVehicleInfoFormValid already blocks this case from reaching submit)', () => {
    expect(getVehicleYearValue('abc')).toBe(0);
  });
});
