import { z } from 'zod';

export interface EligibilityProfileFields {
  dateOfBirth: string;
  licenseIssueDate: string;
}

function parseIsoDate(value: string): Date | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const parsed = new Date(`${value}T00:00:00.000Z`);
  return Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value ? null : parsed;
}

export function isAdultDateOfBirth(value: string, now = new Date()): boolean {
  const dob = parseIsoDate(value);
  if (!dob || dob > now) return false;
  let age = now.getUTCFullYear() - dob.getUTCFullYear();
  const birthdayPassed = now.getUTCMonth() > dob.getUTCMonth()
    || (now.getUTCMonth() === dob.getUTCMonth() && now.getUTCDate() >= dob.getUTCDate());
  if (!birthdayPassed) age -= 1;
  return age >= 18;
}

export function hasThreeYearsExperience(value: string, now = new Date()): boolean {
  const issued = parseIsoDate(value);
  if (!issued || issued > now) return false;
  const cutoff = new Date(Date.UTC(now.getUTCFullYear() - 3, now.getUTCMonth(), now.getUTCDate()));
  return issued <= cutoff;
}

export const eligibilityProfileSchema = z.object({
  dateOfBirth: z.string().refine(isAdultDateOfBirth, {
    message: 'Enter a valid date of birth showing you are at least 18 (YYYY-MM-DD).',
  }),
  licenseIssueDate: z.string().refine(hasThreeYearsExperience, {
    message: 'Enter a valid licence issue date at least 3 years ago (YYYY-MM-DD).',
  }),
});

export function getEligibilityProfileError(form: EligibilityProfileFields): string | null {
  const result = eligibilityProfileSchema.safeParse(form);
  return result.success ? null : result.error.issues[0]?.message ?? 'Check your eligibility dates.';
}
