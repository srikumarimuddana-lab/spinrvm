import { z } from 'zod';

// ACTION_ITEMS.md B39 step 5: migrates app/profile-setup.tsx's inline
// handleSubmit validation -- the first login/signup-adjacent form migrated
// per this item's own priority ordering (rider identity fields here feed
// KYC/compliance checks downstream, per this item's own "Why this matters"
// text). Mirrors handleSubmit's existing checks EXACTLY, in the same
// priority order (first failing check wins) -- this is a pure extraction,
// not a behavior change:
//   if (!form.firstName.trim()) -> 'First Name Required'
//   if (!form.lastName.trim()) -> 'Last Name Required'
//   if (!form.email.trim()) -> 'Email Required'
//   if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email)) -> 'Invalid Email'
//   if (!form.gender) -> 'Gender Required'
// A validation-rule change on an already-shipped screen is a live-tested-
// surface UX change per CLAUDE.md's pre-merge gates -- do not tighten or
// loosen any bound here without a deliberate, separate decision.

// Same regex as the screen's original inline check -- not a stricter RFC
// 5322-style pattern, kept byte-for-byte identical.
const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export const profileSetupSchema = z.object({
  firstName: z.string().refine(v => v.trim().length > 0, {
    message: 'Please enter your first name.',
  }),
  lastName: z.string().refine(v => v.trim().length > 0, {
    message: 'Please enter your last name.',
  }),
  email: z
    .string()
    .refine(v => v.trim().length > 0, { message: 'Please enter your email address.' })
    .refine(v => EMAIL_REGEX.test(v), {
      message: 'That email doesn’t look right — e.g. name@example.com.',
    }),
  gender: z.string().refine(v => v.length > 0, {
    message: 'Please select your gender.',
  }),
});

export type ProfileSetupInput = z.infer<typeof profileSetupSchema>;

export interface ProfileSetupField {
  firstName: string;
  lastName: string;
  email: string;
  gender: string;
}

/**
 * Runs the same checks as the screen's old inline handleSubmit block, in the
 * same order, and returns the same toast title/message pair for the first
 * failing check -- or null if every check passes. Field-name-first-error
 * priority is preserved by evaluating the same conditions in the same
 * order the original inline code did.
 */
export function getProfileSetupError(
  form: ProfileSetupField,
): { title: string; message: string } | null {
  if (!form.firstName.trim()) {
    return { title: 'First Name Required', message: 'Please enter your first name.' };
  }
  if (!form.lastName.trim()) {
    return { title: 'Last Name Required', message: 'Please enter your last name.' };
  }
  if (!form.email.trim()) {
    return { title: 'Email Required', message: 'Please enter your email address.' };
  }
  if (!EMAIL_REGEX.test(form.email)) {
    return {
      title: 'Invalid Email',
      message: 'That email doesn’t look right — e.g. name@example.com.',
    };
  }
  if (!form.gender) {
    return { title: 'Gender Required', message: 'Please select your gender.' };
  }
  return null;
}

/**
 * Drop-in replacement for the screen's old inline `isFormValid` name/email/
 * gender portion -- same true/false contract as
 * `form.firstName.trim() && form.lastName.trim() && form.email.trim() &&
 * form.gender` (the TOS/isEditing portion of isFormValid stays in the
 * screen, unrelated to this schema).
 */
export function isProfileSetupValid(form: ProfileSetupField): boolean {
  return profileSetupSchema.safeParse(form).success;
}
