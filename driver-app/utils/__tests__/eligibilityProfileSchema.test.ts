import {
  getEligibilityProfileError,
  hasThreeYearsExperience,
  isAdultDateOfBirth,
} from '../eligibilityProfileSchema';

describe('eligibility profile dates', () => {
  const today = new Date('2026-09-23T12:00:00Z');

  it('accepts an adult and three-year experience at the boundary', () => {
    expect(isAdultDateOfBirth('2000-09-23', today)).toBe(true);
    expect(hasThreeYearsExperience('2023-09-23', today)).toBe(true);
  });

  it('rejects underage dates and less than three years of experience', () => {
    expect(isAdultDateOfBirth('2010-09-23', today)).toBe(false);
    expect(hasThreeYearsExperience('2023-09-24', today)).toBe(false);
  });

  it('rejects malformed and impossible calendar dates', () => {
    expect(isAdultDateOfBirth('2000-02-30', today)).toBe(false);
    expect(hasThreeYearsExperience('2020/01/01', today)).toBe(false);
  });

  it('provides an actionable message for invalid form data', () => {
    expect(getEligibilityProfileError({ dateOfBirth: '', licenseIssueDate: '' })).toContain('date of birth');
  });
});
