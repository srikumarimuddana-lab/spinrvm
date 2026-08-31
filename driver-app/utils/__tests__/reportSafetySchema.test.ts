import { isSafetyCategoryValid, isSafetyIssueValid, getReportSafetyFormError } from '../reportSafetySchema';

describe('isSafetyCategoryValid', () => {
  it('accepts a non-empty category', () => {
    expect(isSafetyCategoryValid('unsafe_driving')).toBe(true);
  });

  it('rejects an empty category', () => {
    expect(isSafetyCategoryValid('')).toBe(false);
  });

  it('rejects a null category (matches the screen\'s unselected state)', () => {
    expect(isSafetyCategoryValid(null)).toBe(false);
  });
});

describe('isSafetyIssueValid', () => {
  it('accepts a non-empty description', () => {
    expect(isSafetyIssueValid('The driver ran a red light.')).toBe(true);
  });

  it('rejects an empty description', () => {
    expect(isSafetyIssueValid('')).toBe(false);
  });

  it('rejects a whitespace-only description', () => {
    expect(isSafetyIssueValid('   ')).toBe(false);
  });
});

describe('getReportSafetyFormError', () => {
  it('accepts a valid category and description', () => {
    expect(getReportSafetyFormError('unsafe_driving', 'Ran a red light')).toBeNull();
  });

  it('rejects a missing category', () => {
    expect(getReportSafetyFormError('', 'Ran a red light')).toEqual({
      title: 'Category Required',
      message: 'Please select a category for your safety report.',
    });
  });

  it('rejects a missing description', () => {
    expect(getReportSafetyFormError('unsafe_driving', '')).toEqual({
      title: 'Description Required',
      message: 'Please describe the safety issue before submitting.',
    });
  });

  it('checks category before description (first-error-wins order)', () => {
    expect(getReportSafetyFormError('', '')).toEqual({
      title: 'Category Required',
      message: 'Please select a category for your safety report.',
    });
  });
});
