import { z } from "zod";

/**
 * dashboard/corporate-accounts/[id]/members/page.tsx's `handleInvite` --
 * validation extracted from the two sequential inline checks (an email
 * must be entered; it must match the page's own email-shape regex) per
 * ACTION_ITEMS.md B39 (broader-sweep candidate: admin-dashboard #7,
 * invite-email regex). Pure extraction, byte-for-byte equivalent of the
 * checks it replaces -- no behavior change.
 *
 * Uses the page's own original regex rather than zod's built-in
 * `.email()` -- zod's email validator accepts/rejects a different set of
 * strings than this hand-rolled pattern, so swapping it in would be a
 * validation-rule change, not a pure extraction.
 */

/** Mirrors `handleInvite`'s `!inviteEmail` guard (inverted). This check is silent in the original -- no toast, just a no-op return. */
export function isInviteEmailProvided(email: string): boolean {
  return !!email;
}

const INVITE_EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** Mirrors `handleInvite`'s `!emailRegex.test(inviteEmail.trim())` check (inverted). */
export function isInviteEmailFormatValid(email: string): boolean {
  return INVITE_EMAIL_REGEX.test(email.trim());
}

export const companyMemberInviteSchema = z
  .object({ email: z.string() })
  .superRefine(({ email }, ctx) => {
    if (!isInviteEmailProvided(email)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Email is required", path: ["email"] });
      return;
    }
    if (!isInviteEmailFormatValid(email)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Please enter a valid email", path: ["email"] });
    }
  });
