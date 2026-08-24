import { z } from 'zod';

// ACTION_ITEMS.md B39 step 6: migrates app/profile-setup.tsx's inline
// handleSubmit validation -- driver-app's first signup/profile-setup form
// migrated per this item's "Still open" ordering (driver identity fields
// here feed downstream KYC/compliance checks, same as rider-app's step 5
// equivalent). `rider-app/app/login.tsx` was checked first for this step
// and found to have no extractable validation logic worth migrating (its
// only check is `phoneNumber.length === 10`, with no error message/toast
// and no branching priority order) -- see ACTION_ITEMS.md B39 step 6 note
// and PR description for that finding.
//
// Mirrors handleSubmit's existing checks EXACTLY, in the same priority
// order (first failing check wins) -- this is a pure extraction, not a
// behavior change:
//   if (!isFirstNameValid) -> 'First Name Required'
//   if (!isLastNameValid) -> 'Last Name Required'
//   if (!email.trim()) -> 'Email Required'
//   if (!isEmailValid) -> 'Invalid Email'
//   if (!gender) -> 'Gender Required'
//   if (!isServiceAreaValid) -> 'Service Area Required'
// A validation-rule change on an already-shipped screen is a live-tested-
// surface UX change per CLAUDE.md's pre-merge gates -- do not tighten or
// loosen any bound here without a deliberate, separate decision.

// Same regex as the screen's original inline check -- not a stricter RFC
// 5322-style pattern, kept byte-for-byte identical.
const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export interface ProfileSetupField {
  firstName: string;
  lastName: string;
  email: string;
  gender: string;
  serviceAreaId: string;
}

// Individual predicates are exported (not just an aggregate isFormValid)
// because the screen reads each one separately to drive its per-field
// checkmark icons (`firstName.length > 0 && isFirstNameValid && ...`) --
// collapsing them into one boolean would lose that per-field UI signal.

/** Mirrors `firstName.trim().length > 1` exactly. */
export function isFirstNameValid(firstName: string): boolean {
  return firstName.trim().length > 1;
}

/** Mirrors `lastName.trim().length > 1` exactly. */
export function isLastNameValid(lastName: string): boolean {
  return lastName.trim().length > 1;
}

/** Mirrors `email.length > 0 && EMAIL_REGEX.test(email)` exactly. */
export function isEmailValid(email: string): boolean {
  return email.length > 0 && EMAIL_REGEX.test(email);
}

/** Mirrors `serviceAreaId.length > 0` exactly. */
export function isServiceAreaValid(serviceAreaId: string): boolean {
  return serviceAreaId.length > 0;
}

export const profileSetupSchema = z.object({
  firstName: z.string().refine(isFirstNameValid, {
    message: 'Please enter your first name (at least 2 letters).',
  }),
  lastName: z.string().refine(isLastNameValid, {
    message: 'Please enter your last name (at least 2 letters).',
  }),
  email: z
    .string()
    .refine(v => v.trim().length > 0, { message: 'Please enter your email address.' })
    .refine(isEmailValid, {
      message: 'That email doesn’t look right — e.g. name@example.com.',
    }),
  gender: z.string().refine(v => v.length > 0, {
    message: 'Please select your gender.',
  }),
  serviceAreaId: z.string().refine(isServiceAreaValid, {
    message: 'Please select the area where you plan to drive.',
  }),
});

export type ProfileSetupInput = z.infer<typeof profileSetupSchema>;

/**
 * Drop-in replacement for the screen's old inline `isFormValid`:
 * `isFirstNameValid && isLastNameValid && isEmailValid && gender &&
 * isServiceAreaValid`.
 */
export function isProfileSetupFormValid(form: ProfileSetupField): boolean {
  return profileSetupSchema.safeParse(form).success;
}

/**
 * Runs the same checks as the screen's old inline handleSubmit block, in
 * the same order, and returns the same toast title/message pair for the
 * first failing check -- or null if every check passes.
 */
export function getProfileSetupError(
  form: ProfileSetupField,
): { title: string; message: string } | null {
  if (!isFirstNameValid(form.firstName)) {
    return { title: 'First Name Required', message: 'Please enter your first name (at least 2 letters).' };
  }
  if (!isLastNameValid(form.lastName)) {
    return { title: 'Last Name Required', message: 'Please enter your last name (at least 2 letters).' };
  }
  if (!form.email.trim()) {
    return { title: 'Email Required', message: 'Please enter your email address.' };
  }
  if (!isEmailValid(form.email)) {
    return {
      title: 'Invalid Email',
      message: 'That email doesn’t look right — e.g. name@example.com.',
    };
  }
  if (!form.gender) {
    return { title: 'Gender Required', message: 'Please select your gender.' };
  }
  if (!isServiceAreaValid(form.serviceAreaId)) {
    return { title: 'Service Area Required', message: 'Please select the area where you plan to drive.' };
  }
  return null;
}
