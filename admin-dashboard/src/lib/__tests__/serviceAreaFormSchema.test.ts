import { describe, it, expect } from 'vitest';
import {
  isServiceAreaNameValid,
  isAirportZoneValid,
  MIN_AIRPORT_ZONE_POLYGON_POINTS,
} from '../serviceAreaFormSchema';

describe('isServiceAreaNameValid', () => {
  it('accepts a non-empty name', () => {
    expect(isServiceAreaNameValid('Saskatoon Metro')).toBe(true);
  });

  it('rejects an empty string (mirrors the original `!createForm.name` check)', () => {
    expect(isServiceAreaNameValid('')).toBe(false);
  });
});

describe('isAirportZoneValid', () => {
  it('accepts a name with a polygon of at least 3 points', () => {
    expect(isAirportZoneValid('YXE Terminal', 3)).toBe(true);
    expect(isAirportZoneValid('YXE Terminal', 5)).toBe(true);
  });

  it('rejects an empty name even with a valid polygon', () => {
    expect(isAirportZoneValid('', 4)).toBe(false);
  });

  it('rejects a polygon with fewer than 3 points, even with a name', () => {
    expect(isAirportZoneValid('YXE Terminal', 0)).toBe(false);
    expect(isAirportZoneValid('YXE Terminal', 2)).toBe(false);
  });

  it('rejects both an empty name and an under-sized polygon', () => {
    expect(isAirportZoneValid('', 0)).toBe(false);
  });

  it('MIN_AIRPORT_ZONE_POLYGON_POINTS mirrors the original `< 3` boundary', () => {
    expect(MIN_AIRPORT_ZONE_POLYGON_POINTS).toBe(3);
  });
});
