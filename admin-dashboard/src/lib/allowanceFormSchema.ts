import { z } from "zod";

/**
 * dashboard/corporate-accounts/[id]/members/allowance-dialog.tsx --
 * validation extracted from `save()`'s three throw conditions (also
 * duplicated in the Save button's `disabled` expression) per
 * ACTION_ITEMS.md B39. `isAllowanceFormValid` is a byte-for-byte match for
 * the accept/reject boolean both call sites already computed -- see the
 * PR description for the before/after per call site.
 *
 * Money-adjacent: this form sets a corporate member's spend allowance
 * (`corporate_wallet_apply_delta`-backed), so a validation gap here has
 * real financial consequence, not just a UX bug -- the exact case B39
 * calls out by name.
 */

export const allowanceTypeSchema = z.enum(["fixed_recurring", "one_time", "unlimited"]);

export const allowanceFormSchema = z.object({
  type: allowanceTypeSchema,
  amount: z.string(),
  periodStart: z.string(),
  periodEnd: z.string(),
}).superRefine((val, ctx) => {
  // Mirrors save()'s `if (type !== "unlimited") { if (!amount) throw ... }`
  // -- an empty string (falsy) fails, same as the original truthy check.
  if (val.type !== "unlimited" && !val.amount) {
    ctx.addIssue({ code: "custom", message: "Amount is required.", path: ["amount"] });
  }
  // Mirrors save()'s `if (type === "fixed_recurring") { if (!periodStart
  // || !periodEnd) throw ... }`.
  if (val.type === "fixed_recurring" && (!val.periodStart || !val.periodEnd)) {
    ctx.addIssue({ code: "custom", message: "fixed_recurring requires both period dates.", path: ["periodStart"] });
  }
});

export function isAllowanceFormValid(input: {
  type: string;
  amount: string;
  periodStart: string;
  periodEnd: string;
}): boolean {
  return allowanceFormSchema.safeParse(input).success;
}
