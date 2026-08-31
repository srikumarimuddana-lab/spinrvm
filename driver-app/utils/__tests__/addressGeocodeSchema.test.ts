import { isAddressNameAndAddressValid, isAddressInputValid, isGeocodeResultValid } from '../addressGeocodeSchema';

describe('isAddressNameAndAddressValid', () => {
  it('accepts when both fields are present', () => {
    expect(isAddressNameAndAddressValid('Home', '123 Main St, Regina, SK')).toBe(true);
  });

  it('rejects when name is missing', () => {
    expect(isAddressNameAndAddressValid('', '123 Main St')).toBe(false);
  });

  it('rejects when address is missing', () => {
    expect(isAddressNameAndAddressValid('Home', '')).toBe(false);
  });

  it('rejects whitespace-only fields', () => {
    expect(isAddressNameAndAddressValid('   ', '   ')).toBe(false);
  });
});

describe('isAddressInputValid', () => {
  it('accepts a non-empty address', () => {
    expect(isAddressInputValid('123 Main St, Regina, SK')).toBe(true);
  });

  it('rejects an empty address', () => {
    expect(isAddressInputValid('')).toBe(false);
  });

  it('rejects a whitespace-only address', () => {
    expect(isAddressInputValid('   ')).toBe(false);
  });
});

describe('isGeocodeResultValid', () => {
  it('accepts a resolved coordinate pair', () => {
    expect(isGeocodeResultValid({ lat: 50.4452, lng: -104.6189 })).toBe(true);
  });

  it('rejects a null result (address not found)', () => {
    expect(isGeocodeResultValid(null)).toBe(false);
  });
});
