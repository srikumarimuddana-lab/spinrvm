import { z } from 'zod';

// ACTION_ITEMS.md B39: first zod migration on this surface, chosen as the
// pilot form per the item's own risk-ordering (corporate allowance
// requests are money/compliance-adjacent). Mirrors
// app/work-allowance-request.tsx's existing inline validation EXACTLY —
// this is a pure extraction, not a behavior change:
//   const parsedAmount = parseFloat(amount);
//   const isValid = !isNaN(parsedAmount) && parsedAmount > 0 && reason.trim().length >= 5;
// A validation-rule change on an already-shipped screen is a live-tested-
// surface UX change per CLAUDE.md's pre-merge gates — do not tighten or
// loosen any bound here without a deliberate, separate decision.

export const workAllowanceRequestSchema = z.object({
  // Raw string input straight from the TextInput, same as the screen's own
  // `amount` state — parseFloat + isNaN mirrors the original check exactly
  // (an empty string, "abc", or "." all fail the same way parseFloat did).
  amount: z
    .string()
    .refine(v => !isNaN(parseFloat(v)) && parseFloat(v) > 0, {
      message: 'Enter an amount greater than $0.',
    }),
  reason: z
    .string()
    .trim()
    .min(5, { message: 'Reason must be at least 5 characters.' }),
});

export type WorkAllowanceRequestInput = z.infer<typeof workAllowanceRequestSchema>;

/**
 * Drop-in replacement for the screen's old inline `isValid` boolean —
 * same true/false contract, so the call site's disabled-button logic is
 * unchanged.
 */
export function isWorkAllowanceRequestValid(amount: string, reason: string): boolean {
  return workAllowanceRequestSchema.safeParse({ amount, reason }).success;
}
