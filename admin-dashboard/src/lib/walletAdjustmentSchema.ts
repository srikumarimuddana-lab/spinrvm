import { z } from "zod";

/**
 * dashboard/corporate-accounts/[id]/page.tsx's `handleAdjust` -- validation
 * extracted from three inline checks per ACTION_ITEMS.md B39 (the exact
 * "wallet adjustments" form the item names by name). Each helper below is
 * a byte-for-byte equivalent of the check it replaces, kept separate
 * (rather than one aggregate boolean) because the call site shows a
 * different toast message per failing check -- see the PR description
 * for the before/after.
 *
 * Money-critical: `handleAdjust` posts straight to
 * `/api/admin/corporate-accounts/{id}/wallet/adjust`
 * (`corporate_wallet_apply_delta`-backed per CLAUDE.md), so a validation
 * gap here has real financial consequence, not just a UX bug -- the exact
 * case B39 calls out by name.
 */

// $10,000 CAD safety cap on a single adjustment -- mirrors the page's own
// `MAX_SINGLE_ADJUSTMENT` constant. Exported so the page can keep building
// its "$10,000.00" toast copy from one source instead of a second literal.
export const MAX_SINGLE_ADJUSTMENT = 10000;

export const adjustmentAmountSchema = z
  .number()
  .finite()
  .refine((n) => n !== 0, { message: "Adjustment amount cannot be zero." })
  .refine((n) => Math.abs(n) <= MAX_SINGLE_ADJUSTMENT, {
    message: `Single adjustment cannot exceed $${MAX_SINGLE_ADJUSTMENT.toFixed(2)}.`,
  });

/**
 * Mirrors `handleAdjust`'s `isNaN(adjustmentAmount) || adjustmentAmount === 0`
 * check -- the "Invalid amount" toast's condition. `parseFloat`'s `NaN`
 * result fails `z.number().finite()` the same way the original `isNaN`
 * check did.
 */
export function isAdjustmentAmountValid(amount: number): boolean {
  return !Number.isNaN(amount) && amount !== 0 && Number.isFinite(amount);
}

/**
 * Mirrors `handleAdjust`'s `Math.abs(adjustmentAmount) > MAX_SINGLE_ADJUSTMENT`
 * check -- the separate "Amount exceeds limit" toast's condition. Kept
 * apart from `isAdjustmentAmountValid` because the two failures show
 * different messages at the call site (matches the original's two
 * sequential `if` blocks, not one combined check).
 */
export function isAdjustmentAmountWithinLimit(amount: number): boolean {
  return Math.abs(amount) <= MAX_SINGLE_ADJUSTMENT;
}

/**
 * Combined accept/reject, for anywhere only the overall pass/fail matters
 * (e.g. a future non-toast caller). `handleAdjust` itself still calls the
 * two predicates above separately so its per-failure toast text is
 * unchanged.
 */
export function isAdjustmentAmountFullyValid(amount: number): boolean {
  return adjustmentAmountSchema.safeParse(amount).success;
}

export const adjustmentNoteSchema = z.string().trim().min(1);

/**
 * Mirrors `handleAdjust`'s `!notes.trim()` guard -- required, non-empty
 * after trimming (the same rule as `workAllowanceRequestSchema`'s reason
 * field, minus its length-5 floor: this form's original check only ever
 * tested truthiness of the trimmed string).
 */
export function isAdjustmentNoteValid(notes: string): boolean {
  return adjustmentNoteSchema.safeParse(notes).success;
}
