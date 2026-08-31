import {
  isPersonalStepValid,
  isVehicleYearValid,
  isVehicleDetailsValid,
  getMissingDriverDocuments,
} from '../becomeDriverSchema';

describe('isPersonalStepValid', () => {
  it('returns false when any field is empty', () => {
    expect(isPersonalStepValid('', 'Doe', 'a@b.com', 'Regina')).toBe(false);
    expect(isPersonalStepValid('John', '', 'a@b.com', 'Regina')).toBe(false);
    expect(isPersonalStepValid('John', 'Doe', '', 'Regina')).toBe(false);
    expect(isPersonalStepValid('John', 'Doe', 'a@b.com', '')).toBe(false);
  });

  it('returns true when every field is filled', () => {
    expect(isPersonalStepValid('John', 'Doe', 'a@b.com', 'Regina')).toBe(true);
  });
});

describe('isVehicleYearValid', () => {
  const currentYear = new Date().getFullYear();

  it('returns false for an empty year', () => {
    expect(isVehicleYearValid('')).toBe(false);
  });

  it('returns false for a non-numeric year', () => {
    expect(isVehicleYearValid('abc')).toBe(false);
  });

  it('returns false for a year older than the SGI 9-year window', () => {
    expect(isVehicleYearValid(String(currentYear - 10))).toBe(false);
  });

  it('returns true for a year within the SGI 9-year window', () => {
    expect(isVehicleYearValid(String(currentYear - 9))).toBe(true);
    expect(isVehicleYearValid(String(currentYear))).toBe(true);
  });
});

describe('isVehicleDetailsValid', () => {
  const validArgs: [string, string, string, string, string, string] = [
    'Toyota', 'Camry', 'Silver', 'ABC 123', '1HGBH41JXMN109186', 'type-1',
  ];

  it('returns false when any field is empty', () => {
    expect(isVehicleDetailsValid('', 'Camry', 'Silver', 'ABC 123', '1HGBH41JXMN109186', 'type-1')).toBe(false);
    expect(isVehicleDetailsValid('Toyota', 'Camry', 'Silver', 'ABC 123', '1HGBH41JXMN109186', '')).toBe(false);
  });

  it('returns true when every field is filled', () => {
    expect(isVehicleDetailsValid(...validArgs)).toBe(true);
  });
});

describe('getMissingDriverDocuments', () => {
  const licenseReq = { id: 'lic', name: 'Driving License', is_mandatory: true, requires_back_side: true };
  const insuranceReq = { id: 'ins', name: 'Vehicle Insurance', is_mandatory: true, requires_back_side: false };
  const optionalReq = { id: 'opt', name: 'Optional Doc', is_mandatory: false, requires_back_side: false };

  it('flags a missing license number', () => {
    expect(getMissingDriverDocuments('', [], {})).toContain('Driver License Number');
  });

  it('does not flag the license number when present', () => {
    expect(getMissingDriverDocuments('D1234567', [], {})).not.toContain('Driver License Number');
  });

  it('flags a missing front image for a mandatory requirement', () => {
    const missing = getMissingDriverDocuments('D1', [licenseReq], {});
    expect(missing).toContain('Driving License (Front)');
  });

  it('flags a missing back image only when requires_back_side is true', () => {
    const missing = getMissingDriverDocuments('D1', [licenseReq], { lic: { front: 'url' } });
    expect(missing).toContain('Driving License (Back)');
  });

  it('does not flag a back image when requires_back_side is false', () => {
    const missing = getMissingDriverDocuments('D1', [insuranceReq], { ins: { front: 'url' } });
    expect(missing).not.toContain('Vehicle Insurance (Back)');
  });

  it('flags a missing expiry date only for Driving License / Vehicle Insurance', () => {
    const missing = getMissingDriverDocuments('D1', [licenseReq, insuranceReq, optionalReq], {
      lic: { front: 'url', back: 'url' },
      ins: { front: 'url' },
    });
    expect(missing).toContain('Driving License Expiry Date');
    expect(missing).toContain('Vehicle Insurance Expiry Date');
    expect(missing).not.toContain('Optional Doc Expiry Date');
  });

  it('ignores a non-mandatory requirement entirely', () => {
    const missing = getMissingDriverDocuments('D1', [optionalReq], {});
    expect(missing).toEqual([]);
  });

  it('returns an empty array when every mandatory requirement is fully satisfied', () => {
    const missing = getMissingDriverDocuments('D1', [licenseReq, insuranceReq], {
      lic: { front: 'url', back: 'url', expiry: '2030-01-01' },
      ins: { front: 'url', expiry: '2030-01-01' },
    });
    expect(missing).toEqual([]);
  });
});
