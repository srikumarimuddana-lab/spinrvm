import { z } from 'zod';

// ACTION_ITEMS.md B39 step 6: migrates app/profile-setup.tsx's inline
// handleSubmit validation -- driver-app's own signup/profile-setup form,
// the item's own "Still open" list names this exact gap after step 5
// closed the rider-app twin. Mirrors handleSubmit's existing checks
// EXACTLY, in the same priority order (first failing check wins) -- this
// is a pure extraction, not a behavior change:
//   if (!isFirstNameValid) -> 'First Name Required' (trim().length > 1)
//   if (!isLastNameValid) -> 'Last Name Required' (trim().length > 1)
//   if (!email.trim()) -> 'Email Required'
//   if (!isEmailValid) -> 'Invalid Email' (non-empty + regex)
//   if (!gender) -> 'Gender Required'
//   if (!isServiceAreaValid) -> 'Service Area Required'
// A validation-rule change on an already-shipped screen is a live-tested-
// surface UX change per CLAUDE.md's pre-merge gates -- do not tighten or
// loosen any bound here without a deliberate, separate decision.
//
// Note: driver-app's `app/driver/(tabs)/profile.tsx` has its own,
// independently hand-rolled `EMAIL_REGEX` + inline check for editing an
// existing driver's email (different variable names, different toast
// copy, same regex pattern text) -- it is NOT migrated here. It is a
// separate screen with its own inline check, not a caller of
// profile-setup.tsx's validation, so extracting profile-setup.tsx's
// checks does not touch it. Left as a known duplicate, same as prior B39
// steps left analogous near-duplicates alone rather than "improving" them
// into a shared function (that would be a validation-rule-adjacent
// change, not a pure extraction).

// Same regex as the screen's original inline check -- not a stricter RFC
// 5322-style pattern, kept byte-for-byte identical.
const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export const driverProfileSetupSchema = z.object({
  firstName: z.string().refine(v => v.trim().length > 1, {
    message: 'Please enter your first name (at least 2 letters).',
  }),
  lastName: z.string().refine(v => v.trim().length > 1, {
    message: 'Please enter your last name (at least 2 letters).',
  }),
  email: z
    .string()
    .refine(v => v.trim().length > 0, { message: 'Please enter your email address.' })
    .refine(v => v.length > 0 && EMAIL_REGEX.test(v), {
      message: 'That email doesn’t look right — e.g. name@example.com.',
    }),
  gender: z.string().refine(v => v.length > 0, {
    message: 'Please select your gender.',
  }),
  serviceAreaId: z.string().refine(v => v.length > 0, {
    message: 'Please select the area where you plan to drive.',
  }),
});

export type DriverProfileSetupInput = z.infer<typeof driverProfileSetupSchema>;

export interface DriverProfileSetupField {
  firstName: string;
  lastName: string;
  email: string;
  gender: string;
  serviceAreaId: string;
}

/**
 * Runs the same checks as the screen's old inline handleSubmit block, in
 * the same order, and returns the same toast title/message pair for the
 * first failing check -- or null if every check passes. Field-name-first-
 * error priority is preserved by evaluating the same conditions in the
 * same order the original inline code did.
 */
export function getDriverProfileSetupError(
  form: DriverProfileSetupField,
): { title: string; message: string } | null {
  const isFirstNameValid = form.firstName.trim().length > 1;
  const isLastNameValid = form.lastName.trim().length > 1;
  const isEmailValid = form.email.length > 0 && EMAIL_REGEX.test(form.email);
  const isServiceAreaValid = form.serviceAreaId.length > 0;

  if (!isFirstNameValid) {
    return { title: 'First Name Required', message: 'Please enter your first name (at least 2 letters).' };
  }
  if (!isLastNameValid) {
    return { title: 'Last Name Required', message: 'Please enter your last name (at least 2 letters).' };
  }
  if (!form.email.trim()) {
    return { title: 'Email Required', message: 'Please enter your email address.' };
  }
  if (!isEmailValid) {
    return {
      title: 'Invalid Email',
      message: 'That email doesn’t look right — e.g. name@example.com.',
    };
  }
  if (!form.gender) {
    return { title: 'Gender Required', message: 'Please select your gender.' };
  }
  if (!isServiceAreaValid) {
    return { title: 'Service Area Required', message: 'Please select the area where you plan to drive.' };
  }
  return null;
}

/**
 * Drop-in replacement for the screen's old inline `isFormValid` boolean --
 * same true/false contract as
 * `isFirstNameValid && isLastNameValid && isEmailValid && gender &&
 * isServiceAreaValid`.
 */
export function isDriverProfileSetupValid(form: DriverProfileSetupField): boolean {
  return driverProfileSetupSchema.safeParse(form).success;
}
